from django.contrib import admin

from .models import Purchase, RefundRequest


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "ticket_type",
        "buyer_wallet",
        "payment_method",
        "status",
        "transaction_signature",
        "created_at",
    )
    list_filter = ("payment_method", "status", "created_at")
    search_fields = (
        "buyer_wallet",
        "transaction_signature",
        "event__title",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(RefundRequest)
class RefundRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "purchase", "requester_wallet", "status", "created_at", "processed_at")
    list_filter = ("status", "created_at")
    search_fields = ("requester_wallet", "transaction_signature", "purchase__buyer_wallet")
    readonly_fields = ("created_at",)
