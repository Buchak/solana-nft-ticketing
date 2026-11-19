import hashlib
import struct

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from solders.pubkey import Pubkey

from blockchain.models import ChainTransaction
from blockchain.services import _program_id, _rpc_client, _ticket_pda
from payments.models import Purchase
from tickets.models import Ticket


class Command(BaseCommand):
    help = "Safely release a stale pending ticket reservation after verifying Solana state."

    def add_arguments(self, parser):
        parser.add_argument("--ticket-id", type=int, required=True)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the reconciliation. Without this option, only run checks.",
        )

    def handle(self, *args, **options):
        ticket_id = options["ticket_id"]
        apply_changes = options["apply"]

        with transaction.atomic():
            try:
                ticket = Ticket.objects.select_for_update().select_related("event").get(
                    pk=ticket_id
                )
                purchase = Purchase.objects.select_for_update().get(ticket=ticket)
            except (Ticket.DoesNotExist, Purchase.DoesNotExist) as exc:
                raise CommandError(
                    "The ticket or its associated purchase does not exist."
                ) from exc

            if (
                ticket.status != Ticket.Status.PENDING
                or purchase.status != Purchase.Status.PENDING
            ):
                raise CommandError("Ticket and purchase must both still be pending.")
            if ticket.on_chain_ticket_id is None or not ticket.asset_address:
                raise CommandError("Ticket is missing its on-chain ID or asset address.")
            if ticket.event.on_chain_event_id is None or not ticket.event.event_pda:
                raise CommandError("Event is missing its on-chain ID or event account.")

            linked_transactions = ChainTransaction.objects.filter(
                instruction=ChainTransaction.Instruction.BUY_TICKET_CRYPTO,
                event_id_on_chain=ticket.event.on_chain_event_id,
                accounts__ticket_pk=ticket.pk,
            ).exclude(status=ChainTransaction.Status.FAILED)
            if linked_transactions.exists():
                raise CommandError(
                    "A non-failed blockchain transaction exists for this ticket."
                )

            client = _rpc_client()
            event_account = client.get_account_info(
                Pubkey.from_string(ticket.event.event_pda)
            ).value
            if event_account is None:
                raise CommandError("The on-chain event account was not found.")
            if event_account.owner != _program_id():
                raise CommandError("The event account is not owned by this program.")

            data = bytes(event_account.data)
            discriminator = hashlib.sha256(b"account:EventState").digest()[:8]
            if len(data) < 96 or data[:8] != discriminator:
                raise CommandError("The event account data is invalid or unexpected.")

            on_chain_sold_count = struct.unpack_from("<I", data, 92)[0]
            if on_chain_sold_count != ticket.event.total_tickets_sold:
                raise CommandError(
                    "The on-chain ticket count differs from Django. Reconcile the event "
                    "state before releasing this reservation."
                )
            if ticket.on_chain_ticket_id != on_chain_sold_count + 1:
                raise CommandError(
                    "This ticket ID is not the next ID expected by the on-chain event."
                )

            asset_account = client.get_account_info(
                Pubkey.from_string(ticket.asset_address)
            ).value
            ticket_record_account = client.get_account_info(
                _ticket_pda(ticket.asset_address)
            ).value
            if asset_account is not None or ticket_record_account is not None:
                raise CommandError(
                    "A Solana ticket account already exists; no database changes were made."
                )

            if not apply_changes:
                self.stdout.write(
                    self.style.WARNING(
                        f"Checks passed for ticket {ticket.pk}. No changes made. "
                        "Re-run with --apply to mark it failed and release its ID."
                    )
                )
                return

            ticket.status = Ticket.Status.FAILED
            ticket.on_chain_ticket_id = None
            ticket.save(update_fields=("status", "on_chain_ticket_id", "updated_at"))
            purchase.status = Purchase.Status.FAILED
            purchase.save(update_fields=("status", "updated_at"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Ticket {ticket.pk} and purchase {purchase.pk} marked failed; "
                "on-chain ticket ID released."
            )
        )
