from django.db import models


class ChainTransaction(models.Model):
    class Instruction(models.TextChoices):
        INITIALIZE_PLATFORM = "initialize_platform", "Initialize platform"
        CREATE_EVENT = "create_event", "Create event"
        ADD_TICKET_TYPE = "add_ticket_type", "Add ticket type"
        BUY_TICKET_CRYPTO = "buy_ticket_crypto", "Buy ticket with crypto"
        CONSUME_TICKET = "consume_ticket", "Consume ticket"
        CANCEL_EVENT = "cancel_event", "Cancel event"
        REFUND_TICKET = "refund_ticket", "Refund ticket"
        FINALIZE_EVENT = "finalize_event", "Finalize event"
        RESOLVE_FROZEN_EVENT = "resolve_frozen_event", "Resolve frozen event"
        WITHDRAW_FUNDS = "withdraw_funds", "Withdraw funds"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        SUBMITTED = "submitted", "Submitted"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"

    instruction = models.CharField(max_length=32, choices=Instruction.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED)
    signature = models.CharField(max_length=128, unique=True, null=True, blank=True)
    idempotency_key = models.CharField(max_length=128, unique=True, null=True, blank=True)
    event_id_on_chain = models.PositiveBigIntegerField(null=True, blank=True)
    asset_address = models.CharField(max_length=128, blank=True)
    accounts = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    confirmed_slot = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.instruction}: {self.status}"
