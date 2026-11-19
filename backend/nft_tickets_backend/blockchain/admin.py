from django.contrib import admin

from .models import ChainTransaction


@admin.register(ChainTransaction)
class ChainTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "instruction",
        "status",
        "signature",
        "event_id_on_chain",
        "confirmed_slot",
        "created_at",
    )
    list_filter = ("instruction", "status", "created_at")
    search_fields = ("signature", "idempotency_key", "asset_address", "error_message")
    readonly_fields = ("created_at", "updated_at", "confirmed_at")
