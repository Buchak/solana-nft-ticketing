from django.db import models

from events.models import Event, TicketType


class Ticket(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        USED = "used", "Used"
        REFUNDED = "refunded", "Refunded"
        BURNED = "burned", "Burned"
        FAILED = "failed", "Failed"

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="tickets")
    ticket_type = models.ForeignKey(
        TicketType,
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    
    owner_wallet = models.CharField(max_length=128)
    on_chain_ticket_id = models.PositiveBigIntegerField(null=True, blank=True)
    ticket_record_pda = models.CharField(max_length=128, blank=True, null=True, unique=True)
    asset_address = models.CharField(max_length=128, blank=True, null=True, unique=True)

    name = models.CharField(max_length=32)
    metadata_uri = models.URLField(max_length=500)
    price_paid_lamports = models.PositiveBigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    minted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("event", "on_chain_ticket_id"),
                name="unique_chain_ticket_id_per_event",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(price_paid_lamports__isnull=True)
                    | models.Q(price_paid_lamports__gt=0)
                ),
                name="ticket_paid_price_nonnegative",
            ),
        ]

    def __str__(self):
        return f"Ticket {self.on_chain_ticket_id or self.pk} — {self.event}"


class ScanLog(models.Model):
    class Result(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        ALREADY_USED = "already_used", "Already used"
        INVALID = "invalid", "Invalid"
        REJECTED = "rejected", "Rejected"

    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="scan_logs")
    scanned_by_wallet = models.CharField(max_length=128)
    result = models.CharField(max_length=12, choices=Result.choices)
    transaction_signature = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-scanned_at",)

    def __str__(self):
        return f"{self.result}: {self.ticket}"
