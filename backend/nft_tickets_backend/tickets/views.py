import secrets
import uuid
from datetime import timedelta

import httpx
from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import CharField, Exists, OuterRef, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from solana.rpc.core import RPCException

from accounts.models import Platform, WalletProfile
from blockchain.models import ChainTransaction
from events.models import EventDispute
from blockchain.services import (
    confirm_submitted_transaction,
    prepare_refund_ticket_transaction,
    rebroadcast_consume_ticket_transaction,
    submit_consume_ticket_transaction,
)
from .models import ScanLog, Ticket
from .serializers import OwnedTicketSerializer, TicketScanSerializer


QR_TOKEN_SALT = "tickets.ticket-entry-qr"


class SolanaRPCUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Solana RPC is unavailable. The scan remains pending; retry shortly."
    default_code = "solana_rpc_unavailable"


class SolanaRPCRequestFailed(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "solana_rpc_request_failed"

    def __init__(self, error):
        super().__init__(detail=f"Solana RPC rejected the refund request: {error}")


class TicketQRExpired(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = "This ticket QR code has expired. Generate a new one."
    default_code = "ticket_qr_expired"


class TicketScannerAuthorityUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The backend scanner authority is not initialized."
    default_code = "scanner_authority_unavailable"


class IsAuthenticatedOrganizerOrScanner(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True
        if view.is_scanner_request(request):
            return True
        raise AuthenticationFailed("Valid organizer or scanner credentials are required.")


class TicketQRView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, ticket_id):
        ticket = get_object_or_404(
            Ticket.objects.select_related("event"),
            pk=ticket_id,
            status=Ticket.Status.ACTIVE,
        )
        wallet_is_owner = WalletProfile.objects.filter(
            user=request.user,
            wallet_address=ticket.owner_wallet,
        ).exists()
        if not wallet_is_owner:
            raise PermissionDenied("Only the ticket owner can generate its QR code.")
        if (
            not ticket.event.is_active
            or ticket.event.is_canceled
            or ticket.event.is_completed
            or ticket.event.is_frozen
        ):
            raise ValidationError("This event is not accepting ticket scans.")

        validity_seconds = settings.TICKET_QR_VALIDITY_SECONDS
        if validity_seconds <= 0:
            raise ImproperlyConfigured(
                "TICKET_QR_VALIDITY_SECONDS must be greater than zero."
            )

        issued_at = timezone.now()
        qr_token = signing.dumps(
            {
                "ticket_id": ticket.pk,
                "event_id": ticket.event_id,
                "owner_wallet": ticket.owner_wallet,
                "nonce": uuid.uuid4().hex,
            },
            salt=QR_TOKEN_SALT,
        )
        return Response(
            {
                "qr_token": qr_token,
                "expires_at": (issued_at + timedelta(seconds=validity_seconds)).isoformat(),
                "expires_in_seconds": validity_seconds,
                "ticket_id": ticket.pk,
            }
        )


class OwnedTicketListView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        wallet_addresses = WalletProfile.objects.filter(user=request.user).values_list(
            "wallet_address",
            flat=True,
        )
        tickets_query = Ticket.objects.filter(owner_wallet__in=wallet_addresses).annotate(
            has_event_dispute=Exists(
                EventDispute.objects.filter(ticket_id=OuterRef("pk"))
            )
        )
        event_slug = request.query_params.get("event_slug")
        if event_slug:
            tickets_query = tickets_query.filter(event__slug=event_slug)
        refunds = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
        ).annotate(
            ticket_pk_text=KeyTextTransform("ticket_pk", "accounts"),
        ).filter(
            ticket_pk_text=Cast(OuterRef("pk"), output_field=CharField()),
        ).order_by("-created_at")
        tickets = (
            tickets_query.annotate(
                refund_status=Subquery(refunds.values("status")[:1]),
                refund_transaction_id=Subquery(refunds.values("pk")[:1]),
            )
            .select_related("event", "ticket_type")
            .order_by("-created_at")
        )
        return Response(OwnedTicketSerializer(tickets, many=True).data)


class TicketRefundView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, ticket_id):
        wallet_addresses = WalletProfile.objects.filter(user=request.user).values_list(
            "wallet_address",
            flat=True,
        )
        ticket = get_object_or_404(
            Ticket.objects.select_related("event", "ticket_type", "purchase"),
            pk=ticket_id,
            owner_wallet__in=wallet_addresses,
        )

        try:
            prepared = prepare_refund_ticket_transaction(ticket, ticket.owner_wallet)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise SolanaRPCUnavailable() from exc

        return Response(
            {
                "chain_transaction_id": prepared["id"],
                "unsigned_transaction": prepared["unsigned_transaction"],
                "last_valid_block_height": prepared["last_valid_block_height"],
            },
            status=status.HTTP_201_CREATED,
        )


class TicketScanView(APIView):
    permission_classes = (IsAuthenticatedOrganizerOrScanner,)

    @staticmethod
    def is_scanner_request(request):
        configured_token = settings.TICKET_SCANNER_API_TOKEN
        provided_token = request.headers.get("X-Scanner-Token", "")
        return bool(
            configured_token
            and provided_token
            and secrets.compare_digest(
                provided_token.encode("utf-8"),
                configured_token.encode("utf-8"),
            )
        )

    def post(self, request):
        is_scanner = self.is_scanner_request(request)
        if not is_scanner and not request.user.is_authenticated:
            raise AuthenticationFailed("Authentication credentials were not provided.")

        serializer = TicketScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = signing.loads(
                serializer.validated_data["qr_token"],
                salt=QR_TOKEN_SALT,
                max_age=settings.TICKET_QR_VALIDITY_SECONDS,
            )
        except signing.SignatureExpired as exc:
            raise TicketQRExpired() from exc
        except signing.BadSignature as exc:
            raise ValidationError({"qr_token": "The QR code is invalid."}) from exc

        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("ticket_id"), int)
            or isinstance(payload.get("ticket_id"), bool)
            or not isinstance(payload.get("event_id"), int)
            or isinstance(payload.get("event_id"), bool)
            or not isinstance(payload.get("owner_wallet"), str)
            or not isinstance(payload.get("nonce"), str)
            or not payload.get("nonce")
        ):
            raise ValidationError({"qr_token": "The QR code payload is invalid."})

        with transaction.atomic():
            try:
                ticket = (
                    Ticket.objects.select_for_update(of=("self",))
                    .select_related("event__organizer__wallet")
                    .get(pk=payload["ticket_id"])
                )
            except Ticket.DoesNotExist as exc:
                raise NotFound("Ticket not found.") from exc

            if is_scanner:
                platform = Platform.objects.filter(pk=1, is_initialized=True).first()
                if platform is None:
                    raise TicketScannerAuthorityUnavailable()
                scanned_by_wallet = platform.backend_authority_wallet
            else:
                organizer = ticket.event.organizer
                scanner_wallet = organizer.wallet
                if (
                    organizer.user_id != request.user.pk
                    or scanner_wallet is None
                    or scanner_wallet.user_id != request.user.pk
                ):
                    raise PermissionDenied("Only this event's organizer can scan its tickets.")
                scanned_by_wallet = scanner_wallet.wallet_address

            if (
                payload["event_id"] != ticket.event_id
                or payload["owner_wallet"] != ticket.owner_wallet
            ):
                raise ValidationError({"qr_token": "The QR code does not match this ticket."})

            if ticket.status == Ticket.Status.USED:
                ScanLog.objects.create(
                    ticket=ticket,
                    scanned_by_wallet=scanned_by_wallet,
                    result=ScanLog.Result.ALREADY_USED,
                )
                return Response(
                    {"detail": "This ticket has already been used.", "result": "already_used"},
                    status=status.HTTP_409_CONFLICT,
                )

            if (
                ticket.status != Ticket.Status.ACTIVE
                or not ticket.event.is_active
                or ticket.event.is_canceled
                or ticket.event.is_completed
                or ticket.event.is_frozen
            ):
                ScanLog.objects.create(
                    ticket=ticket,
                    scanned_by_wallet=scanned_by_wallet,
                    result=ScanLog.Result.REJECTED,
                    notes="Ticket or event is not eligible for entry.",
                )
                return Response(
                    {"detail": "This ticket or event is not eligible for entry.", "result": "rejected"},
                    status=status.HTTP_409_CONFLICT,
                )

            pending_record = ChainTransaction.objects.filter(
                instruction=ChainTransaction.Instruction.CONSUME_TICKET,
                status=ChainTransaction.Status.SUBMITTED,
                accounts__ticket_pk=ticket.pk,
            ).first()

            try:
                if pending_record is not None:
                    if confirm_submitted_transaction(pending_record):
                        pending_record.refresh_from_db()
                        return self._accepted_response(pending_record, ticket)
                    pending_record.refresh_from_db()
                    if pending_record.status == ChainTransaction.Status.FAILED:
                        return Response(
                            {
                                "detail": "On-chain ticket consumption failed.",
                                "result": "rejected",
                                "error": pending_record.error_message,
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    rebroadcast_consume_ticket_transaction(pending_record)
                    pending_record.refresh_from_db()
                    if pending_record.status == ChainTransaction.Status.FAILED:
                        return Response(
                            {
                                "detail": "On-chain ticket consumption failed.",
                                "result": "rejected",
                                "error": pending_record.error_message,
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    if confirm_submitted_transaction(pending_record):
                        pending_record.refresh_from_db()
                        return self._accepted_response(pending_record, ticket)
                    pending_record.refresh_from_db()
                    return self._pending_response(pending_record)

                pending_record = submit_consume_ticket_transaction(
                    ticket=ticket,
                    scanned_by_wallet=scanned_by_wallet,
                )
                if confirm_submitted_transaction(pending_record):
                    pending_record.refresh_from_db()
                    return self._accepted_response(pending_record, ticket)
                pending_record.refresh_from_db()
                if pending_record.status == ChainTransaction.Status.FAILED:
                    return Response(
                        {
                            "detail": "On-chain ticket consumption failed.",
                            "result": "rejected",
                            "error": pending_record.error_message,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                return self._pending_response(pending_record)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            except (RPCException, httpx.HTTPError, OSError) as exc:
                raise SolanaRPCUnavailable() from exc

    @staticmethod
    def _accepted_response(record, ticket):
        return Response(
            {
                "detail": "Ticket accepted and consumed on Solana.",
                "result": ScanLog.Result.ACCEPTED,
                "ticket_id": ticket.pk,
                "event_id": ticket.event_id,
                "signature": record.signature,
            }
        )

    @staticmethod
    def _pending_response(record):
        return Response(
            {
                "detail": "On-chain ticket consumption is pending confirmation.",
                "result": "pending",
                "chain_transaction_id": record.pk,
                "signature": record.signature,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class TicketScanStatusView(APIView):
    permission_classes = (IsAuthenticatedOrganizerOrScanner,)

    def get(self, request, transaction_id):
        record = get_object_or_404(
            ChainTransaction,
            pk=transaction_id,
            instruction=ChainTransaction.Instruction.CONSUME_TICKET,
        )
        ticket = get_object_or_404(
            Ticket.objects.select_related("event__organizer__wallet"),
            pk=record.accounts.get("ticket_pk"),
            event_id=record.accounts.get("event_pk"),
        )

        if not self.is_scanner_request(request):
            organizer = ticket.event.organizer
            scanner_wallet = organizer.wallet
            if (
                organizer.user_id != request.user.pk
                or scanner_wallet is None
                or scanner_wallet.user_id != request.user.pk
            ):
                raise PermissionDenied("Only this event's organizer can check its scan.")

        try:
            if record.status == ChainTransaction.Status.SUBMITTED:
                confirm_submitted_transaction(record)
            record.refresh_from_db()
        except (RPCException, httpx.HTTPError, OSError) as exc:
            raise SolanaRPCUnavailable() from exc

        if record.status == ChainTransaction.Status.CONFIRMED:
            return TicketScanView._accepted_response(record, ticket)
        if record.status == ChainTransaction.Status.FAILED:
            return Response(
                {
                    "detail": "On-chain ticket consumption failed.",
                    "result": "rejected",
                    "error": record.error_message,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return TicketScanView._pending_response(record)

    @staticmethod
    def is_scanner_request(request):
        return TicketScanView.is_scanner_request(request)
