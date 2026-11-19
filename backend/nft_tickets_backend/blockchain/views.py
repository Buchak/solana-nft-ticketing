from django.shortcuts import get_object_or_404
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction as db_transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
import httpx
from rest_framework import permissions, status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from solana.rpc.core import RPCException
from solders.errors import BincodeError
from solders.transaction import TransactionError

from accounts.models import Platform, WalletProfile
from events.models import Event
from payments.models import Purchase
from tickets.models import Ticket
from .models import ChainTransaction
from .serializers import (
    InitializePlatformSerializer,
    ResolveFrozenEventSerializer,
    SignedTransactionSerializer,
)
from .services import (
    confirm_submitted_transaction,
    prepare_cancel_event_transaction,
    prepare_initialize_platform_transaction,
    prepare_refund_ticket_transaction,
    prepare_withdraw_funds_transaction,
    submit_resolve_frozen_event_transaction,
    submit_signed_transaction,
)


class SolanaRPCUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Solana RPC is unavailable. Retry the request."
    default_code = "solana_rpc_unavailable"


class SolanaRPCRequestFailed(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "solana_rpc_request_failed"

    def __init__(self, error):
        super().__init__(detail=f"Solana RPC rejected the request: {error}")


class BackendAuthorityUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "backend_authority_unavailable"

    def __init__(self, error):
        super().__init__(detail=str(error))


class ChainTransactionAccessMixin:
    permission_classes = (permissions.IsAuthenticated,)

    def get_transaction(self):
        record = get_object_or_404(ChainTransaction, pk=self.kwargs["pk"])
        if record.instruction == ChainTransaction.Instruction.INITIALIZE_PLATFORM:
            if not self.request.user.is_staff:
                raise PermissionDenied("Only platform staff may manage initialization transactions.")
            platform_pk = record.accounts.get("platform_pk")
            if not platform_pk or not Platform.objects.filter(pk=platform_pk).exists():
                raise PermissionDenied("This transaction is not associated with a platform configuration.")
            return record

        if record.instruction in (
            ChainTransaction.Instruction.FINALIZE_EVENT,
            ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT,
        ):
            if not self.request.user.is_staff:
                raise PermissionDenied(
                    "Only platform staff may manage event finalization transactions."
                )
            event_pk = record.accounts.get("event_pk")
            if not event_pk or not Event.objects.filter(pk=event_pk).exists():
                raise PermissionDenied("This transaction is not associated with an event.")
            return record

        if record.instruction == ChainTransaction.Instruction.BUY_TICKET_CRYPTO:
            buyer_wallet = record.accounts.get("buyer_wallet")
            purchase_exists = Purchase.objects.filter(
                pk=record.accounts.get("purchase_pk"),
                buyer_wallet=buyer_wallet,
            ).exists()
            wallet_exists = WalletProfile.objects.filter(
                user=self.request.user,
                wallet_address=buyer_wallet,
            ).exists()
            if not purchase_exists or not wallet_exists:
                raise PermissionDenied("You are not the buyer of this transaction.")
            return record

        if record.instruction == ChainTransaction.Instruction.REFUND_TICKET:
            owner_wallet = record.accounts.get("owner_wallet")
            owns_ticket = Ticket.objects.filter(
                pk=record.accounts.get("ticket_pk"),
                event_id=record.accounts.get("event_pk"),
                owner_wallet=owner_wallet,
            ).exists()
            wallet_exists = WalletProfile.objects.filter(
                user=self.request.user,
                wallet_address=owner_wallet,
            ).exists()
            if not owns_ticket or not wallet_exists:
                raise PermissionDenied("You are not the owner of the refunded ticket.")
            return record

        event_pk = record.accounts.get("event_pk")
        event_query = Event.objects.filter(
            pk=event_pk,
            organizer__user=self.request.user,
        )
        if record.instruction == ChainTransaction.Instruction.WITHDRAW_FUNDS:
            event_query = event_query.filter(
                organizer__wallet__user=self.request.user,
                organizer__wallet__wallet_address=record.accounts.get("organizer_wallet"),
            )
        if record.instruction == ChainTransaction.Instruction.CANCEL_EVENT:
            event_query = event_query.filter(
                organizer__wallet__user=self.request.user,
                organizer__wallet__wallet_address=record.accounts.get("organizer_wallet"),
            )
        if record.instruction == ChainTransaction.Instruction.CONSUME_TICKET:
            event_query = event_query.filter(
                organizer__wallet__user=self.request.user,
            )
        if not event_pk or not event_query.exists():
            raise PermissionDenied("You are not the organizer of this transaction's event.")
        if record.instruction not in (
            ChainTransaction.Instruction.CREATE_EVENT,
            ChainTransaction.Instruction.ADD_TICKET_TYPE,
            ChainTransaction.Instruction.CONSUME_TICKET,
            ChainTransaction.Instruction.WITHDRAW_FUNDS,
            ChainTransaction.Instruction.CANCEL_EVENT,
        ):
            raise PermissionDenied("This transaction type cannot be submitted through this endpoint.")
        return record


class InitializePlatformView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request):
        serializer = InitializePlatformSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with db_transaction.atomic():
            platform, _ = Platform.objects.get_or_create(
                pk=1,
                defaults={
                    **serializer.validated_data,
                    "program_id": settings.SOLANA_PROGRAM_ID,
                },
            )
            platform = Platform.objects.select_for_update().get(pk=platform.pk)
            if platform.is_initialized:
                return Response(
                    {"detail": "The platform has already been initialized on Solana."},
                    status=status.HTTP_409_CONFLICT,
                )

            active_initializations = ChainTransaction.objects.filter(
                instruction=ChainTransaction.Instruction.INITIALIZE_PLATFORM,
                status=ChainTransaction.Status.SUBMITTED,
            )
            if any(
                item.accounts.get("platform_pk") == platform.pk
                for item in active_initializations
            ):
                return Response(
                    {"detail": "A platform initialization transaction is already submitted."},
                    status=status.HTTP_409_CONFLICT,
                )

            ChainTransaction.objects.filter(
                instruction=ChainTransaction.Instruction.INITIALIZE_PLATFORM,
                status=ChainTransaction.Status.CREATED,
            ).update(
                status=ChainTransaction.Status.FAILED,
                error_message="Superseded by a newer initialization transaction.",
                updated_at=timezone.now(),
            )
            platform.admin_wallet = serializer.validated_data["admin_wallet"]
            platform.backend_authority_wallet = serializer.validated_data[
                "backend_authority_wallet"
            ]
            platform.program_id = settings.SOLANA_PROGRAM_ID
            platform.save(
                update_fields=(
                    "admin_wallet",
                    "backend_authority_wallet",
                    "program_id",
                    "updated_at",
                )
            )

        try:
            chain_transaction = prepare_initialize_platform_transaction(
                platform=platform,
                admin_wallet=platform.admin_wallet,
                backend_authority_wallet=platform.backend_authority_wallet,
            )
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise SolanaRPCUnavailable() from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        return Response(
            {
                "platform": {
                    "admin_wallet": platform.admin_wallet,
                    "backend_authority_wallet": platform.backend_authority_wallet,
                    "program_id": platform.program_id,
                    "is_initialized": platform.is_initialized,
                },
                "blockchain_transaction": chain_transaction,
            },
            status=status.HTTP_201_CREATED,
        )


class ResolveFrozenEventView(APIView):
    permission_classes = (permissions.IsAdminUser,)

    def post(self, request, event_id):
        event = get_object_or_404(Event, pk=event_id)
        serializer = ResolveFrozenEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            record = submit_resolve_frozen_event_transaction(
                event,
                serializer.validated_data["action"],
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except ImproperlyConfigured as exc:
            raise BackendAuthorityUnavailable(exc) from exc
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except httpx.HTTPError as exc:
            raise SolanaRPCUnavailable() from exc

        return Response(
            {
                "event_id": event.pk,
                "action": "complete" if record.accounts["action_type"] else "cancel",
                "transaction_id": record.pk,
                "signature": record.signature,
                "status": record.status,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class WithdrawEventFundsView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, event_id):
        event = get_object_or_404(
            Event.objects.select_related("organizer__wallet").filter(
                organizer__user=request.user,
            ),
            pk=event_id,
        )
        if (
            event.organizer.wallet_id is None
            or event.organizer.wallet.user_id != request.user.pk
            or not WalletProfile.objects.filter(
                user=request.user,
                wallet_address=event.organizer.wallet.wallet_address,
            ).exists()
        ):
            raise PermissionDenied("Connect the organizer wallet associated with this event.")

        try:
            prepared = prepare_withdraw_funds_transaction(
                event,
                event.organizer.wallet.wallet_address,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except httpx.HTTPError as exc:
            raise SolanaRPCUnavailable() from exc

        return Response(
            {
                "chain_transaction_id": prepared["id"],
                "unsigned_transaction": prepared["unsigned_transaction"],
                "last_valid_block_height": prepared["last_valid_block_height"],
                "event_pda": prepared["event_pda"],
            },
            status=status.HTTP_201_CREATED,
        )


class CancelEventView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, event_id):
        event = get_object_or_404(
            Event.objects.select_related("organizer__wallet").filter(
                organizer__user=request.user,
            ),
            pk=event_id,
        )
        if (
            event.organizer.wallet_id is None
            or event.organizer.wallet.user_id != request.user.pk
            or not WalletProfile.objects.filter(
                user=request.user,
                wallet_address=event.organizer.wallet.wallet_address,
            ).exists()
        ):
            raise PermissionDenied("Connect the organizer wallet associated with this event.")

        try:
            prepared = prepare_cancel_event_transaction(
                event,
                event.organizer.wallet.wallet_address,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except httpx.HTTPError as exc:
            raise SolanaRPCUnavailable() from exc

        return Response(
            {
                "chain_transaction_id": prepared["id"],
                "unsigned_transaction": prepared["unsigned_transaction"],
                "last_valid_block_height": prepared["last_valid_block_height"],
                "event_pda": prepared["event_pda"],
            },
            status=status.HTTP_201_CREATED,
        )


class SubmitChainTransactionView(ChainTransactionAccessMixin, APIView):
    def post(self, request, pk):
        record = self.get_transaction()
        if record.instruction == ChainTransaction.Instruction.CONSUME_TICKET:
            raise ValidationError(
                "Ticket consumption is signed and submitted by the backend."
            )
        if record.status == ChainTransaction.Status.SUBMITTED:
            return Response(
                {"signature": record.signature, "status": record.status},
                status=status.HTTP_200_OK,
            )
        if record.status != ChainTransaction.Status.CREATED:
            raise ValidationError("Only a prepared transaction can be submitted.")

        serializer = SignedTransactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = submit_signed_transaction(
                record,
                serializer.validated_data["signed_transaction"],
            )
        except (BincodeError, ValueError, TransactionError) as exc:
            raise ValidationError(str(exc)) from exc
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except httpx.HTTPError as exc:
            raise SolanaRPCUnavailable() from exc

        return Response(
            {"signature": record.signature, "status": record.status},
            status=status.HTTP_202_ACCEPTED,
        )


class ConfirmChainTransactionView(ChainTransactionAccessMixin, APIView):
    def post(self, request, pk):
        record = self.get_transaction()
        if record.status == ChainTransaction.Status.CONFIRMED:
            return Response({"status": record.status}, status=status.HTTP_200_OK)
        if record.status == ChainTransaction.Status.FAILED:
            return Response(
                {"status": record.status, "error": record.error_message},
                status=status.HTTP_409_CONFLICT,
            )
        if record.status != ChainTransaction.Status.SUBMITTED:
            raise ValidationError("Submit the signed transaction before checking confirmation.")

        try:
            confirmed = confirm_submitted_transaction(record)
        except RPCException as exc:
            raise SolanaRPCRequestFailed(exc) from exc
        except httpx.HTTPError as exc:
            raise SolanaRPCUnavailable() from exc

        if record.status == ChainTransaction.Status.FAILED:
            return Response(
                {"status": record.status, "error": record.error_message},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "status": (
                    ChainTransaction.Status.CONFIRMED
                    if confirmed
                    else ChainTransaction.Status.SUBMITTED
                ),
                "signature": record.signature,
            },
            status=status.HTTP_200_OK if confirmed else status.HTTP_202_ACCEPTED,
        )
