from django.contrib import admin

from .models import Event, EventDispute, Organizer, TicketType


class TicketTypeInline(admin.TabularInline):
    model = TicketType
    extra = 0


@admin.register(Organizer)
class OrganizerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "wallet", "is_verified", "created_at")
    list_filter = ("is_verified",)
    search_fields = ("name", "email", "wallet__wallet_address")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "organizer",
        "event_date",
        "is_active",
        "is_canceled",
        "is_completed",
        "is_frozen",
        "on_chain_event_id",
    )
    list_filter = ("is_active", "is_canceled", "is_completed", "is_frozen", "event_date")
    search_fields = ("title", "slug", "organizer__name", "collection_address")
    readonly_fields = ("created_at", "updated_at")
    inlines = (TicketTypeInline,)


@admin.register(TicketType)
class TicketTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "price_in_lamports", "minted", "max_supply", "is_active")
    list_filter = ("is_active", "event")
    search_fields = ("name", "event__title")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EventDispute)
class EventDisputeAdmin(admin.ModelAdmin):
    list_display = ("event", "ticket", "reported_by_wallet", "status", "created_at", "resolved_at")
    list_filter = ("status", "created_at")
    search_fields = ("event__title", "reported_by_wallet", "reason")
    readonly_fields = ("created_at",)
