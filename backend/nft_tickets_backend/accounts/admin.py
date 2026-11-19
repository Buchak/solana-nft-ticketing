from django.contrib import admin

from .models import Platform, WalletProfile


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ("id", "is_initialized", "admin_wallet", "backend_authority_wallet", "updated_at")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WalletProfile)
class WalletProfileAdmin(admin.ModelAdmin):
    list_display = ("wallet_address", "user", "created_at")
    search_fields = ("wallet_address", "user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
