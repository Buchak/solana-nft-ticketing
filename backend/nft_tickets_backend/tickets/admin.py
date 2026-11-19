from django.contrib import admin

from .models import ScanLog, Ticket


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "ticket_type",
        "owner_wallet",
        "asset_address",
        "status",
        "minted_at",
    )
    list_filter = ("status", "event", "ticket_type")
    search_fields = ("owner_wallet", "asset_address", "ticket_record_pda", "event__title")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ScanLog)
class ScanLogAdmin(admin.ModelAdmin):
    list_display = ("ticket", "result", "scanned_by_wallet", "scanned_at")
    list_filter = ("result", "scanned_at")
    search_fields = ("ticket__owner_wallet", "ticket__asset_address", "scanned_by_wallet")
    readonly_fields = ("scanned_at",)
