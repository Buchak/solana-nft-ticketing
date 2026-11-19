from django.conf import settings
from django.db import models


class Platform(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    admin_wallet = models.CharField(max_length=128)
    backend_authority_wallet = models.CharField(max_length=128)
    program_id = models.CharField(max_length=128, blank=True)
    is_initialized = models.BooleanField(default=False)
    initialized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "platform configuration"
        verbose_name_plural = "platform configuration"

    def __str__(self):
        return f"Platform configuration ({self.pk})"


class WalletProfile(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet_profiles",
        null=True,
        blank=True,
    )
    wallet_address = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("wallet_address",)

    def __str__(self):
        return self.wallet_address


class WalletLoginChallenge(models.Model):
    wallet_address = models.CharField(max_length=128)
    nonce = models.CharField(max_length=64, unique=True)
    message = models.TextField()
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("wallet_address", "expires_at")),
        ]

    def __str__(self):
        return f"Login challenge for {self.wallet_address}"
