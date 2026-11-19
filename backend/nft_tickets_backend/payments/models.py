from django.db import models

from events.models import Event, TicketType
from tickets.models import Ticket


class Purchase(models.Model):
    class Method(models.TextChoices):
        CRYPTO = "crypto", "Cryptocurrency"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="purchases")
    ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    ticket = models.OneToOneField(
        Ticket,
        on_delete=models.PROTECT,
        related_name="purchase",
        null=True,
        blank=True,
    )
    buyer_wallet = models.CharField(max_length=128)
    payment_method = models.CharField(max_length=8, choices=Method.choices)
    amount_lamports = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=12, default="SOL")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    transaction_signature = models.CharField(max_length=128, blank=True, unique=True, null=True)
    idempotency_key = models.CharField(max_length=128, blank=True, unique=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_lamports__gt=0),
                name="purchase_amount_positive",
            ),
        ]

    def __str__(self):
        return f"{self.payment_method} purchase ({self.status}) — {self.event}"


class RefundRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="refunds")
    requester_wallet = models.CharField(max_length=128)
    reason = models.TextField(blank=True)
    amount_lamports = models.PositiveBigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    transaction_signature = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_lamports__gt=0),
                name="refund_amount_positive",
            ),
        ]

    def __str__(self):
        return f"Refund {self.status} — purchase {self.purchase_id}"
