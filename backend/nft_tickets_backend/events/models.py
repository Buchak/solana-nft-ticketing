from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.text import slugify

from accounts.models import WalletProfile


class Organizer(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="organized_events_profiles",
        null=True,
        blank=True,
    )
    wallet = models.ForeignKey(
        WalletProfile,
        on_delete=models.PROTECT,
        related_name="organizer_profiles",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=128)
    email = models.EmailField(max_length=254, blank=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class Event(models.Model):
    organizer = models.ForeignKey(
        Organizer,
        on_delete=models.PROTECT,
        related_name="events",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    event_date = models.DateTimeField()
    metadata_uri = models.URLField(max_length=500, blank=True)
    image_uri = models.URLField(max_length=500, blank=True)

    on_chain_event_id = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    event_pda = models.CharField(max_length=128, blank=True, unique=True, null=True)
    collection_address = models.CharField(max_length=128, blank=True, unique=True, null=True)

    is_active = models.BooleanField(default=False)
    is_canceled = models.BooleanField(default=False)
    is_completed = models.BooleanField(default=False)
    is_frozen = models.BooleanField(default=False)
    total_tickets_sold = models.PositiveIntegerField(default=0)
    total_ticket_scanned = models.PositiveIntegerField(default=0)
    active_tickets = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("event_date",)
        constraints = [
            models.CheckConstraint(
                condition=Q(total_ticket_scanned__lte=F("total_tickets_sold")),
                name="event_scans_lte_tickets_sold",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or "event"
            self.slug = base_slug
            suffix = 2
            while Event.objects.exclude(pk=self.pk).filter(slug=self.slug).exists():
                self.slug = f"{base_slug}-{suffix}"
                suffix += 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class TicketType(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ticket_types")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    image_uri = models.URLField(max_length=500, blank=True)
    metadata_uri = models.URLField(max_length=500, blank=True)
    on_chain_ticket_type_id = models.PositiveBigIntegerField(null=True, blank=True)
    price_in_lamports = models.PositiveBigIntegerField()
    max_supply = models.PositiveIntegerField()
    minted = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("event", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "on_chain_ticket_type_id"),
                name="unique_chain_ticket_type_per_event",
            ),
            models.CheckConstraint(
                condition=Q(price_in_lamports__gt=0),
                name="ticket_type_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(max_supply__gt=0),
                name="ticket_type_supply_positive",
            ),
            models.CheckConstraint(
                condition=Q(minted__lte=F("max_supply")),
                name="ticket_type_minted_lte_supply",
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.event.title}"


class EventDispute(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VALID = "valid", "Valid"
        INVALID = "invalid", "Invalid"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="disputes")
    ticket = models.OneToOneField(
        "tickets.Ticket",
        on_delete=models.PROTECT,
        related_name="event_dispute",
        null=True,
        blank=True,
    )
    reported_by_wallet = models.CharField(max_length=128)
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.VALID)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Dispute for {self.event}"
