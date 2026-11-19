from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from solders.pubkey import Pubkey

from .models import Event, TicketType


class EventListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ("title", "slug", "event_date", "image_uri", "is_active")


class OrganizerEventListSerializer(serializers.ModelSerializer):
    cancellation_status = serializers.CharField(read_only=True, allow_null=True)
    cancellation_transaction_id = serializers.IntegerField(read_only=True, allow_null=True)
    withdrawal_status = serializers.CharField(read_only=True, allow_null=True)
    withdrawal_transaction_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Event
        fields = (
            "id",
            "title",
            "slug",
            "event_date",
            "image_uri",
            "is_active",
            "is_canceled",
            "is_completed",
            "is_frozen",
            "cancellation_status",
            "cancellation_transaction_id",
            "total_tickets_sold",
            "total_ticket_scanned",
            "active_tickets",
            "withdrawal_status",
            "withdrawal_transaction_id",
        )


class EventDetailSerializer(serializers.ModelSerializer):
    organizer_wallet = serializers.SerializerMethodField()
    finalization_scheduled_for = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = (
            "title",
            "slug",
            "organizer_wallet",
            "description",
            "event_date",
            "finalization_scheduled_for",
            "metadata_uri",
            "image_uri",
            "is_active",
            "is_canceled",
            "is_completed",
            "is_frozen",
            "total_tickets_sold",
            "total_ticket_scanned",
            "active_tickets",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_organizer_wallet(self, event):
        if event.organizer.wallet_id is None:
            return None
        return event.organizer.wallet.wallet_address

    def get_finalization_scheduled_for(self, event):
        if not event.is_active or event.is_canceled or event.is_completed or event.is_frozen:
            return None
        return event.event_date + timedelta(hours=24)


class EventCreateSerializer(serializers.ModelSerializer):
    collection_address = serializers.CharField(write_only=True, max_length=128)
    image = serializers.ImageField(write_only=True, required=True)

    class Meta:
        model = Event
        fields = (
            "id",
            "title",
            "slug",
            "description",
            "event_date",
            "image",
            "collection_address",
        )
        read_only_fields = ("id", "slug")

    def validate_collection_address(self, value):
        try:
            return str(Pubkey.from_string(value))
        except ValueError as exc:
            raise serializers.ValidationError("A valid Solana collection address is required.") from exc

    def validate_image(self, value):
        max_bytes = self.context.get("image_max_bytes", 5 * 1024 * 1024)
        if value.size > max_bytes:
            raise serializers.ValidationError(
                f"Event image must not exceed {max_bytes // (1024 * 1024)} MB."
            )
        if value.image.format not in {"JPEG", "PNG", "WEBP"}:
            raise serializers.ValidationError("Event image must be JPEG, PNG, or WebP.")
        return value

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Event title cannot be empty.")
        if len(value.encode("utf-8")) > 32:
            raise serializers.ValidationError(
                "Event title must be at most 32 UTF-8 bytes to match the on-chain limit."
            )
        return value

    def validate_event_date(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Event date must be in the future.")
        return value

    def create(self, validated_data):
        validated_data["organizer"] = self.context["organizer"]
        return super().create(validated_data)


class EventUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ("description",)


class TicketTypeSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(write_only=True, required=True)

    class Meta:
        model = TicketType
        fields = (
            "id",
            "event",
            "name",
            "description",
            "image",
            "image_uri",
            "metadata_uri",
            "on_chain_ticket_type_id",
            "price_in_lamports",
            "max_supply",
            "minted",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "event",
            "image_uri",
            "metadata_uri",
            "on_chain_ticket_type_id",
            "minted",
            "is_active",
            "created_at",
            "updated_at",
        )

    def validate_price_in_lamports(self, value):
        if value <= 0:
            raise serializers.ValidationError("Price must be greater than zero.")
        return value

    def validate_image(self, value):
        max_bytes = self.context.get("image_max_bytes", 5 * 1024 * 1024)
        if value.size > max_bytes:
            raise serializers.ValidationError(
                f"Ticket image must not exceed {max_bytes // (1024 * 1024)} MB."
            )
        if value.image.format not in {"JPEG", "PNG", "WEBP"}:
            raise serializers.ValidationError("Ticket image must be JPEG, PNG, or WebP.")
        return value

    def validate_max_supply(self, value):
        if value <= 0:
            raise serializers.ValidationError("Maximum supply must be greater than zero.")
        return value

    def create(self, validated_data):
        validated_data["event"] = self.context["event"]
        validated_data["is_active"] = False
        return super().create(validated_data)
