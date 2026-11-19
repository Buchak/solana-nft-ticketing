from rest_framework import serializers

from .models import Ticket


class OwnedTicketSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source="event.title", read_only=True)
    event_slug = serializers.CharField(source="event.slug", read_only=True)
    event_date = serializers.DateTimeField(source="event.event_date", read_only=True)
    event_is_canceled = serializers.BooleanField(source="event.is_canceled", read_only=True)
    event_is_frozen = serializers.BooleanField(source="event.is_frozen", read_only=True)
    event_image_uri = serializers.CharField(
        source="event.image_uri",
        read_only=True,
        allow_blank=True,
        allow_null=True,
    )
    ticket_type_name = serializers.CharField(source="ticket_type.name", read_only=True)
    refund_status = serializers.CharField(read_only=True, allow_null=True)
    refund_transaction_id = serializers.IntegerField(read_only=True, allow_null=True)
    has_event_dispute = serializers.BooleanField(read_only=True)

    class Meta:
        model = Ticket
        fields = (
            "id",
            "name",
            "owner_wallet",
            "status",
            "price_paid_lamports",
            "event_title",
            "event_slug",
            "event_date",
            "event_is_canceled",
            "event_is_frozen",
            "event_image_uri",
            "ticket_type_name",
            "refund_status",
            "refund_transaction_id",
            "has_event_dispute",
        )


class TicketScanSerializer(serializers.Serializer):
    qr_token = serializers.CharField(max_length=2048)
