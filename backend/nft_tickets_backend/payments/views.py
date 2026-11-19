import httpx
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from solana.rpc.core import RPCException

from blockchain.services import prepare_buy_ticket_crypto_transaction
from events.models import Event, TicketType
from .serializers import CryptoPurchaseSerializer


class SolanaRPCUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Solana RPC is unavailable. The purchase was not prepared."
    default_code = "solana_rpc_unavailable"


class CryptoPurchaseCreateView(generics.GenericAPIView):
    serializer_class = CryptoPurchaseSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, event_slug, ticket_type_id):
        event = get_object_or_404(Event, slug=event_slug, is_active=True)
        ticket_type = get_object_or_404(
            TicketType,
            pk=ticket_type_id,
            event=event,
            is_active=True,
        )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = prepare_buy_ticket_crypto_transaction(
                event_slug=event.slug,
                ticket_type_pk=ticket_type.pk,
                buyer_wallet=serializer.validated_data["buyer_wallet"],
                asset_address=serializer.validated_data["asset_address"],
                metadata_uri=(
                    ticket_type.metadata_uri
                    or serializer.validated_data.get("metadata_uri", event.metadata_uri)
                ),
            )
        except (Event.DoesNotExist, TicketType.DoesNotExist) as exc:
            raise ValidationError("The event or ticket type is no longer available.") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        except (RPCException, httpx.HTTPError, OSError) as exc:
            raise SolanaRPCUnavailable() from exc

        return Response(result, status=status.HTTP_201_CREATED)
