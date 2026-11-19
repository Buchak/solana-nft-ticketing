from datetime import timedelta

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from solana.rpc.core import RPCException

from events.models import Event, EventDispute
from blockchain.models import ChainTransaction
from blockchain.services import (
    confirm_submitted_transaction,
    rebroadcast_backend_transaction,
    submit_finalize_event_transaction,
)


class Command(BaseCommand):
    help = (
        "Finalize eligible Solana events at least 24 hours after their scheduled start."
    )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=24)
        events = Event.objects.filter(
            is_active=True,
            is_canceled=False,
            is_completed=False,
            is_frozen=False,
            event_date__lte=cutoff,
            on_chain_event_id__isnull=False,
            event_pda__isnull=False,
        ).exclude(event_pda="").order_by("event_date")
        failures = []

        for event_id in events.values_list("pk", flat=True).iterator():
            try:
                self._finalize_event(event_id)
            except (ImproperlyConfigured, ValueError, RPCException, httpx.HTTPError, OSError) as exc:
                message = f"Event {event_id}: {exc}"
                failures.append(message)
                self.stderr.write(self.style.ERROR(message))

        if failures:
            raise CommandError(
                f"{len(failures)} event(s) could not be finalized; see errors above."
            )

    def _finalize_event(self, event_pk):
        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=event_pk)
            if (
                not event.is_active
                or event.is_canceled
                or event.is_completed
                or event.is_frozen
                or event.event_date > timezone.now() - timedelta(hours=24)
            ):
                return

            existing = ChainTransaction.objects.filter(
                instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
                event_id_on_chain=event.on_chain_event_id,
            ).first()
            if existing is not None:
                if existing.status == ChainTransaction.Status.CONFIRMED:
                    return
                if existing.status == ChainTransaction.Status.FAILED:
                    self.stderr.write(
                        self.style.WARNING(
                            f"Event {event.pk}: finalization transaction failed; "
                            "manual review is required."
                        )
                    )
                    return
                if existing.status != ChainTransaction.Status.SUBMITTED:
                    self.stderr.write(
                        self.style.WARNING(
                            f"Event {event.pk}: finalization transaction is in "
                            f"unexpected status {existing.status}."
                        )
                    )
                    return
                record = existing
            else:
                if EventDispute.objects.filter(
                    event=event,
                    status=EventDispute.Status.PENDING,
                ).exists():
                    self.stdout.write(
                        self.style.WARNING(
                            f"Event {event.pk}: waiting for pending disputes to be resolved."
                        )
                    )
                    return
                valid_disputes = EventDispute.objects.filter(
                    event=event,
                    status=EventDispute.Status.VALID,
                ).count()
                record = submit_finalize_event_transaction(event, valid_disputes)
                self.stdout.write(
                    f"Event {event.pk}: submitted finalize_event transaction "
                    f"{record.signature} with {valid_disputes} valid dispute(s)."
                )

        if confirm_submitted_transaction(record):
            self.stdout.write(self.style.SUCCESS(f"Event {event_pk}: finalization confirmed."))
            return

        if record.status == ChainTransaction.Status.FAILED:
            self.stderr.write(
                self.style.ERROR(
                    f"Event {event_pk}: Solana rejected finalization: {record.error_message}"
                )
            )
            return

        if rebroadcast_backend_transaction(record):
            self.stdout.write(
                self.style.WARNING(
                    f"Event {event_pk}: finalization is still pending; transaction rebroadcast."
                )
            )
