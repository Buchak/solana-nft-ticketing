from django.shortcuts import get_object_or_404
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import CharField, OuterRef, Q, Subquery
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from rest_framework import generics, permissions
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
import httpx
from solana.rpc.core import RPCException

from accounts.models import WalletProfile
from blockchain.models import ChainTransaction
from blockchain.services import (
    get_event_account_balance,
    prepare_add_ticket_type_transaction,
    prepare_create_event_transaction,
)
from .models import Event, EventDispute, Organizer
from tickets.models import Ticket
from .storage import EventMediaStorageError, pin_event_media, pin_ticket_type_media
from .serializers import (
    EventCreateSerializer,
    EventDetailSerializer,
    EventListSerializer,
    OrganizerEventListSerializer,
    EventUpdateSerializer,
    TicketTypeSerializer,
)


class EventListCreateView(generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)

    def get_queryset(self):
        queryset = Event.objects.select_related("organizer")
        if self.request.user.is_authenticated:
            wallets = WalletProfile.objects.filter(user=self.request.user).values_list(
                "wallet_address",
                flat=True,
            )
            return queryset.filter(
                Q(is_active=True)
                | Q(organizer__user=self.request.user)
                | Q(tickets__owner_wallet__in=wallets)
            ).distinct()
        return queryset.filter(is_active=True)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return EventCreateSerializer
        return EventListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method == "POST":
            wallets = WalletProfile.objects.filter(user=self.request.user)
            if wallets.count() != 1:
                raise PermissionDenied(
                    "Your account must have exactly one authenticated Solana wallet."
                )
            wallet = wallets.get()
            organizer, _ = Organizer.objects.get_or_create(
                user=self.request.user,
                defaults={
                    "wallet": wallet,
                    "name": f"Organizer {wallet.wallet_address[:8]}",
                    "email": "",
                },
            )
            if organizer.wallet_id != wallet.pk:
                organizer.wallet = wallet
                organizer.save(update_fields=("wallet", "updated_at"))
            context["organizer"] = organizer
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            data=request.data,
            context={**self.get_serializer_context(), "image_max_bytes": settings.EVENT_IMAGE_MAX_BYTES},
        )
        serializer.is_valid(raise_exception=True)
        collection_address = serializer.validated_data.pop("collection_address")
        image = serializer.validated_data.pop("image")
        try:
            image_uri, metadata_uri = pin_event_media(
                image=image,
                title=serializer.validated_data["title"],
                description=serializer.validated_data.get("description", ""),
            )
        except (EventMediaStorageError, ImproperlyConfigured) as exc:
            raise EventMediaUnavailable(str(exc)) from exc

        event = serializer.save(image_uri=image_uri, metadata_uri=metadata_uri)
        try:
            chain_transaction = prepare_create_event_transaction(
                event=event,
                organizer_wallet=event.organizer.wallet.wallet_address,
                collection_address=collection_address,
            )
        except (RPCException, httpx.HTTPError, OSError, ValueError) as exc:
            raise BlockchainUnavailable() from exc

        return Response(
            {
                "event": EventDetailSerializer(event).data,
                "blockchain_transaction": chain_transaction,
            },
            status=status.HTTP_201_CREATED,
        )


class OrganizerEventListView(generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = OrganizerEventListSerializer

    def get_queryset(self):
        cancellations = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
        ).annotate(
            event_pk_text=KeyTextTransform("event_pk", "accounts"),
        ).filter(
            event_pk_text=Cast(OuterRef("pk"), output_field=CharField()),
        ).order_by("-created_at")
        withdrawals = ChainTransaction.objects.filter(
            instruction=ChainTransaction.Instruction.WITHDRAW_FUNDS,
        ).annotate(
            event_pk_text=KeyTextTransform("event_pk", "accounts"),
        ).filter(
            event_pk_text=Cast(OuterRef("pk"), output_field=CharField()),
        ).order_by("-created_at")
        return (
            Event.objects.filter(organizer__user=self.request.user)
            .annotate(
                cancellation_status=Subquery(cancellations.values("status")[:1]),
                cancellation_transaction_id=Subquery(cancellations.values("pk")[:1]),
                withdrawal_status=Subquery(withdrawals.values("status")[:1]),
                withdrawal_transaction_id=Subquery(withdrawals.values("pk")[:1]),
            )
            .order_by("-created_at")
        )


class EventDetailUpdateView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)
    lookup_field = "slug"
    http_method_names = ("get", "patch", "head", "options")

    def get_queryset(self):
        queryset = Event.objects.select_related("organizer")
        if self.request.method in permissions.SAFE_METHODS:
            if self.request.user.is_authenticated:
                wallets = WalletProfile.objects.filter(user=self.request.user).values_list(
                    "wallet_address",
                    flat=True,
                )
                return queryset.filter(
                    Q(is_active=True)
                    | Q(organizer__user=self.request.user)
                    | Q(tickets__owner_wallet__in=wallets)
                ).distinct()
            return queryset.filter(is_active=True)
        return queryset.filter(organizer__user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return EventUpdateSerializer
        return EventDetailSerializer


class EventAccountBalanceView(generics.GenericAPIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, slug):
        event = get_object_or_404(
            Event.objects.select_related("organizer"),
            slug=slug,
            organizer__user=request.user,
        )
        try:
            balance = get_event_account_balance(event)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except RPCException as exc:
            raise BlockchainUnavailable() from exc
        except (httpx.HTTPError, OSError) as exc:
            raise BlockchainUnavailable() from exc
        return Response(balance)


class EventDisputeCreateView(generics.GenericAPIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, slug):
        ticket_id = request.data.get("ticket_id")
        if not isinstance(ticket_id, int) or isinstance(ticket_id, bool):
            raise ValidationError({"ticket_id": "A valid ticket ID is required."})

        with transaction.atomic():
            event = get_object_or_404(
                Event.objects.select_for_update(),
                slug=slug,
            )
            if event.organizer.user_id == request.user.pk:
                raise PermissionDenied("Organizers cannot submit event disputes.")
            if (
                not event.is_active
                or event.is_canceled
                or event.is_completed
                or event.is_frozen
                or event.event_date > timezone.now()
            ):
                raise ValidationError("This event is not accepting disputes.")

            wallets = WalletProfile.objects.filter(user=request.user).values_list(
                "wallet_address",
                flat=True,
            )
            try:
                ticket = Ticket.objects.select_for_update().get(
                    pk=ticket_id,
                    event=event,
                    owner_wallet__in=wallets,
                )
            except Ticket.DoesNotExist as exc:
                raise NotFound("An eligible ticket for this event was not found.") from exc

            if ticket.status != Ticket.Status.ACTIVE:
                raise ValidationError("Only active, unscanned tickets can report an event.")
            if ChainTransaction.objects.filter(
                instruction=ChainTransaction.Instruction.CONSUME_TICKET,
                status=ChainTransaction.Status.SUBMITTED,
                accounts__ticket_pk=ticket.pk,
            ).exists():
                raise ValidationError("This ticket has a pending scan and cannot be disputed yet.")
            if EventDispute.objects.filter(ticket=ticket).exists():
                raise ValidationError("A dispute has already been submitted for this ticket.")

            dispute = EventDispute.objects.create(
                event=event,
                ticket=ticket,
                reported_by_wallet=ticket.owner_wallet,
                reason="Ticket holder reports that the event did not take place.",
                status=EventDispute.Status.VALID,
                resolved_at=timezone.now(),
            )

        return Response(
            {
                "id": dispute.pk,
                "event_id": event.pk,
                "ticket_id": ticket.pk,
                "status": dispute.status,
                "created_at": dispute.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


class EventTicketTypeListCreateView(generics.ListCreateAPIView):
    serializer_class = TicketTypeSerializer
    permission_classes = (permissions.IsAuthenticatedOrReadOnly,)

    def get_event(self):
        queryset = Event.objects.select_related("organizer")
        if self.request.method == "POST":
            queryset = queryset.filter(organizer__user=self.request.user)
        elif not self.request.user.is_authenticated:
            queryset = queryset.filter(is_active=True)
        else:
            wallets = WalletProfile.objects.filter(user=self.request.user).values_list(
                "wallet_address",
                flat=True,
            )
            queryset = queryset.filter(
                Q(is_active=True)
                | Q(organizer__user=self.request.user)
                | Q(tickets__owner_wallet__in=wallets)
            ).distinct()
        return get_object_or_404(queryset, slug=self.kwargs["event_slug"])

    def get_queryset(self):
        return self.get_event().ticket_types.filter(is_active=True)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["event"] = self.get_event()
        return context

    def perform_create(self, serializer):
        event = self.get_event()
        if not event.is_active or event.is_canceled or event.is_completed or event.is_frozen:
            raise PermissionDenied("Ticket types can only be added to an active on-chain event.")
        serializer.save()

    def create(self, request, *args, **kwargs):
        event = self.get_event()
        if not event.is_active or event.is_canceled or event.is_completed or event.is_frozen:
            raise PermissionDenied("Ticket types can only be added to an active on-chain event.")
        serializer = self.get_serializer(
            data=request.data,
            context={**self.get_serializer_context(), "image_max_bytes": settings.EVENT_IMAGE_MAX_BYTES},
        )
        serializer.is_valid(raise_exception=True)
        image = serializer.validated_data.pop("image")
        try:
            image_uri, metadata_uri = pin_ticket_type_media(
                image=image,
                title=serializer.validated_data["name"],
                description=serializer.validated_data.get("description", ""),
            )
        except (EventMediaStorageError, ImproperlyConfigured) as exc:
            raise EventMediaUnavailable(str(exc)) from exc

        ticket_type = serializer.save(image_uri=image_uri, metadata_uri=metadata_uri)
        try:
            chain_transaction = prepare_add_ticket_type_transaction(
                event=event,
                ticket_type=ticket_type,
                organizer_wallet=event.organizer.wallet.wallet_address,
            )
        except (RPCException, httpx.HTTPError, OSError, ValueError) as exc:
            raise BlockchainUnavailable() from exc

        return Response(
            {
                "ticket_type": TicketTypeSerializer(ticket_type).data,
                "blockchain_transaction": chain_transaction,
            },
            status=status.HTTP_201_CREATED,
        )


class BlockchainUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Could not prepare the Solana transaction. The draft remains saved."
    default_code = "solana_rpc_unavailable"


class EventMediaUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Could not upload event or ticket image and metadata."
    default_code = "event_media_unavailable"
