import base64
import hashlib
import json
import struct
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase
from solana.rpc.commitment import Confirmed
from solana.rpc.core import RPCException
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction
from solders.transaction_status import TransactionConfirmationStatus

from accounts.models import Platform, WalletProfile
from events.models import Event, EventDispute, Organizer, TicketType
from payments.models import Purchase
from tickets.models import ScanLog, Ticket
from .models import ChainTransaction
from .services import (
    METAPLEX_CORE_PROGRAM_ID,
    confirm_submitted_transaction,
    prepare_initialize_platform_transaction,
    prepare_create_event_transaction,
    prepare_buy_ticket_crypto_transaction,
    prepare_cancel_event_transaction,
    prepare_withdraw_funds_transaction,
    prepare_refund_ticket_transaction,
    get_event_account_balance,
    submit_finalize_event_transaction,
    submit_resolve_frozen_event_transaction,
    rebroadcast_consume_ticket_transaction,
    submit_consume_ticket_transaction,
    submit_signed_transaction,
    _event_pda,
    _program_id,
    _ticket_pda,
    _ticket_type_pda,
)
from .scheduler import schedule_event_finalization


class ChainTransactionModelTests(TestCase):
    def test_transaction_defaults_to_created(self):
        transaction_record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
        )

        self.assertEqual(transaction_record.status, ChainTransaction.Status.CREATED)
        self.assertIn("create_event", str(transaction_record))

    def test_signature_must_be_unique(self):
        ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
            signature="unique-signature",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ChainTransaction.objects.create(
                instruction=ChainTransaction.Instruction.CONSUME_TICKET,
                signature="unique-signature",
            )

    def test_models_are_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(ChainTransaction))


class EventFinalizationSchedulerTests(TestCase):
    @patch("blockchain.scheduler._scheduler")
    def test_active_event_is_scheduled_exactly_24_hours_after_start(self, scheduler):
        scheduler.running = True
        scheduler.get_job.return_value = None
        event_date = timezone.now() + timedelta(days=3)
        event = Event.objects.create(
            organizer=Organizer.objects.create(name="Scheduler Organizer"),
            title="Scheduled Event",
            event_date=event_date,
            on_chain_event_id=101,
            event_pda=str(_event_pda(101)),
            is_active=True,
        )

        schedule_event_finalization(event.pk)

        self.assertEqual(
            scheduler.add_job.call_args.kwargs["run_date"],
            event_date + timedelta(hours=24),
        )
        self.assertEqual(
            scheduler.add_job.call_args.kwargs["id"],
            f"finalize-event-{event.pk}",
        )

    @patch("blockchain.scheduler._scheduler")
    def test_canceled_event_is_not_scheduled(self, scheduler):
        scheduler.running = True
        scheduler.get_job.return_value = None
        event = Event.objects.create(
            organizer=Organizer.objects.create(name="Canceled Scheduler Organizer"),
            title="Canceled Event",
            event_date=timezone.now() + timedelta(days=3),
            on_chain_event_id=102,
            event_pda=str(_event_pda(102)),
            is_active=False,
            is_canceled=True,
        )

        schedule_event_finalization(event.pk)

        scheduler.add_job.assert_not_called()


class SolanaTransactionPreparationTests(TestCase):
    @patch("blockchain.services._rpc_client")
    def test_event_account_balance_includes_total_and_withdrawable_lamports(self, rpc_client):
        event_id = 60
        account_data = bytearray(105)
        account_data[:8] = hashlib.sha256(b"account:EventState").digest()[:8]
        struct.pack_into("<Q", account_data, 8, event_id)
        rpc_client.return_value.get_account_info.return_value.value = SimpleNamespace(
            owner=_program_id(),
            lamports=2_500_000_000,
            data=bytes(account_data),
        )
        rpc_client.return_value.get_minimum_balance_for_rent_exemption.return_value.value = (
            890_880
        )
        event = Event.objects.create(
            organizer=Organizer.objects.create(name="Balance Organizer"),
            title="Balance Event",
            event_date=timezone.now() + timedelta(days=3),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
        )

        balance = get_event_account_balance(event)

        self.assertEqual(balance["balance_lamports"], "2500000000")
        self.assertEqual(balance["rent_exempt_lamports"], "890880")
        self.assertEqual(balance["withdrawable_lamports"], "2499109120")
        rpc_client.return_value.get_minimum_balance_for_rent_exemption.assert_called_once_with(
            len(account_data),
            commitment=Confirmed,
        )

    @patch("blockchain.services._read_on_chain_ticket_type_state")
    @patch("blockchain.services._read_on_chain_ticket_state")
    @patch("blockchain.services._read_on_chain_event_state")
    @patch("blockchain.services._rpc_client")
    def test_prepare_ticket_refund_builds_anchor_instruction(
        self,
        rpc_client,
        read_event_state,
        read_ticket_state,
        read_ticket_type_state,
    ):
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=456,
        )
        user = get_user_model().objects.create_user(username="ticket-refund-owner")
        owner_wallet = str(Pubkey.new_unique())
        WalletProfile.objects.create(user=user, wallet_address=owner_wallet)
        organizer = Organizer.objects.create(name="Organizer")
        event_id = 61
        collection = str(Pubkey.new_unique())
        event = Event.objects.create(
            organizer=organizer,
            title="Refund Event",
            event_date=timezone.now() + timedelta(days=4),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            collection_address=collection,
            is_active=True,
            active_tickets=1,
        )
        ticket_type_id = 4
        ticket_type = TicketType.objects.create(
            event=event,
            name="General",
            on_chain_ticket_type_id=ticket_type_id,
            price_in_lamports=250_000,
            max_supply=10,
            minted=1,
        )
        asset_address = str(Pubkey.new_unique())
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=owner_wallet,
            on_chain_ticket_id=1,
            ticket_record_pda=str(_ticket_pda(asset_address)),
            asset_address=asset_address,
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=250_000,
            status=Ticket.Status.ACTIVE,
        )
        purchase = Purchase.objects.create(
            event=event,
            ticket_type=ticket_type,
            ticket=ticket,
            buyer_wallet=owner_wallet,
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=250_000,
            status=Purchase.Status.COMPLETED,
        )
        read_event_state.return_value = {
            "is_canceled": False,
            "is_frozen": False,
            "event_date": int((timezone.now() + timedelta(days=4)).timestamp()),
        }
        read_ticket_state.return_value = {
            "event_id": event_id,
            "ticket_type_id": ticket_type_id,
            "is_used": False,
            "price_paid": 250_000,
            "owner": owner_wallet,
        }
        read_ticket_type_state.return_value = {"price_in_lamports": 250_000, "minted": 1}

        prepared = prepare_refund_ticket_transaction(ticket, owner_wallet)
        transaction = Transaction.from_bytes(
            base64.b64decode(prepared["unsigned_transaction"])
        )
        instruction = transaction.message.instructions[0]
        accounts = transaction.message.account_keys

        self.assertEqual(prepared["last_valid_block_height"], 456)
        self.assertEqual(str(accounts[0]), owner_wallet)
        self.assertEqual(
            instruction.data,
            hashlib.sha256(b"global:refund_ticket").digest()[:8]
            + event_id.to_bytes(8, "little"),
        )
        self.assertEqual(
            [str(accounts[index]) for index in instruction.accounts],
            [
                owner_wallet,
                event.event_pda,
                ticket.ticket_record_pda,
                str(_ticket_type_pda(event_id, ticket_type_id)),
                "11111111111111111111111111111111",
                asset_address,
                collection,
                str(METAPLEX_CORE_PROGRAM_ID),
            ],
        )
        record = ChainTransaction.objects.get(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
        )
        self.assertEqual(record.accounts["ticket_pk"], ticket.pk)
        self.assertEqual(record.accounts["purchase_pk"], purchase.pk)

    @patch("blockchain.services._read_on_chain_event_state")
    def test_ticket_refund_requires_more_than_48_hours_unless_event_canceled(
        self,
        read_event_state,
    ):
        wallet = str(Pubkey.new_unique())
        WalletProfile.objects.create(wallet_address=wallet)
        organizer = Organizer.objects.create(name="Organizer")
        event_id = 62
        event = Event.objects.create(
            organizer=organizer,
            title="Upcoming Event",
            event_date=timezone.now() + timedelta(hours=47),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            collection_address=str(Pubkey.new_unique()),
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="General",
            on_chain_ticket_type_id=2,
            price_in_lamports=200_000,
            max_supply=2,
            minted=1,
        )
        asset_address = str(Pubkey.new_unique())
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=wallet,
            ticket_record_pda=str(_ticket_pda(asset_address)),
            asset_address=asset_address,
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=200_000,
            status=Ticket.Status.ACTIVE,
        )
        Purchase.objects.create(
            event=event,
            ticket_type=ticket_type,
            ticket=ticket,
            buyer_wallet=wallet,
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=200_000,
            status=Purchase.Status.COMPLETED,
        )
        read_event_state.return_value = {
            "is_canceled": False,
            "is_frozen": False,
            "event_date": int((timezone.now() + timedelta(hours=47)).timestamp()),
        }

        with self.assertRaisesMessage(ValueError, "48 hours"):
            prepare_refund_ticket_transaction(ticket, wallet)

        read_event_state.return_value = {
            "is_canceled": True,
            "is_frozen": False,
            "event_date": int((timezone.now() + timedelta(hours=47)).timestamp()),
        }
        with patch("blockchain.services._read_on_chain_ticket_state") as read_ticket:
            read_ticket.return_value = {
                "event_id": event_id,
                "ticket_type_id": 2,
                "is_used": False,
                "price_paid": 200_000,
                "owner": wallet,
            }
            with patch("blockchain.services._read_on_chain_ticket_type_state") as read_type:
                read_type.return_value = {"price_in_lamports": 200_000, "minted": 1}
                with patch("blockchain.services._rpc_client") as rpc_client:
                    rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
                        blockhash=Hash.default(),
                        last_valid_block_height=456,
                    )
                    prepared = prepare_refund_ticket_transaction(ticket, wallet)
        self.assertTrue(prepared["unsigned_transaction"])

    @patch("blockchain.services._read_on_chain_event_state")
    @patch("blockchain.services._rpc_client")
    def test_prepare_cancel_event_builds_anchor_instruction(
        self,
        rpc_client,
        read_event_state,
    ):
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=789,
        )
        read_event_state.return_value = {
            "is_canceled": False,
            "is_completed": False,
            "is_active": True,
            "is_frozen": False,
        }
        user = get_user_model().objects.create_user(username="cancel-organizer")
        organizer_wallet = str(Pubkey.new_unique())
        wallet = WalletProfile.objects.create(user=user, wallet_address=organizer_wallet)
        organizer = Organizer.objects.create(
            user=user,
            wallet=wallet,
            name="Organizer",
        )
        event_id = 70
        event = Event.objects.create(
            organizer=organizer,
            title="Active Event",
            event_date=timezone.now() + timedelta(days=2),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            is_active=True,
        )

        prepared = prepare_cancel_event_transaction(event, organizer_wallet)
        transaction = Transaction.from_bytes(
            base64.b64decode(prepared["unsigned_transaction"])
        )
        instruction = transaction.message.instructions[0]
        accounts = transaction.message.account_keys

        self.assertEqual(prepared["id"], ChainTransaction.objects.get().pk)
        self.assertEqual(str(accounts[0]), organizer_wallet)
        self.assertEqual(
            instruction.data,
            hashlib.sha256(b"global:cencel_event").digest()[:8]
            + event_id.to_bytes(8, "little"),
        )
        self.assertEqual(str(accounts[instruction.accounts[0]]), organizer_wallet)
        self.assertEqual(str(accounts[instruction.accounts[1]]), event.event_pda)
        self.assertEqual(str(accounts[instruction.accounts[2]]), "11111111111111111111111111111111")
        self.assertEqual(
            ChainTransaction.objects.get().instruction,
            ChainTransaction.Instruction.CANCEL_EVENT,
        )

    @patch("blockchain.services._read_on_chain_event_state")
    def test_prepare_cancel_event_rejects_closed_or_frozen_events(self, read_event_state):
        wallet = WalletProfile.objects.create(wallet_address=str(Pubkey.new_unique()))
        organizer = Organizer.objects.create(wallet=wallet, name="Organizer")
        event_id = 71
        event = Event.objects.create(
            organizer=organizer,
            title="Frozen Event",
            event_date=timezone.now() + timedelta(days=2),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
        )
        read_event_state.return_value = {
            "is_canceled": False,
            "is_completed": False,
            "is_active": True,
            "is_frozen": True,
        }

        with self.assertRaisesMessage(ValueError, "frozen"):
            prepare_cancel_event_transaction(event, wallet.wallet_address)

    @patch("blockchain.services._read_on_chain_event_state")
    @patch("blockchain.services._rpc_client")
    def test_prepare_withdraw_funds_builds_organizer_signed_instruction(
        self,
        rpc_client,
        read_event_state,
    ):
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=789,
        )
        read_event_state.return_value = {
            "is_canceled": False,
            "is_completed": True,
            "is_active": False,
            "is_frozen": False,
        }
        user = get_user_model().objects.create_user(username="withdraw-organizer")
        organizer_wallet = str(Pubkey.new_unique())
        wallet = WalletProfile.objects.create(user=user, wallet_address=organizer_wallet)
        organizer = Organizer.objects.create(
            user=user,
            wallet=wallet,
            name="Organizer",
        )
        event_id = 77
        event = Event.objects.create(
            organizer=organizer,
            title="Completed Event",
            event_date=timezone.now() - timedelta(hours=2),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            is_completed=True,
        )

        prepared = prepare_withdraw_funds_transaction(event, organizer_wallet)
        transaction = Transaction.from_bytes(
            base64.b64decode(prepared["unsigned_transaction"])
        )
        instruction = transaction.message.instructions[0]
        accounts = transaction.message.account_keys

        self.assertEqual(prepared["id"], ChainTransaction.objects.get().pk)
        self.assertEqual(str(accounts[0]), organizer_wallet)
        self.assertEqual(
            instruction.data,
            hashlib.sha256(b"global:withdraw_funds").digest()[:8]
            + event_id.to_bytes(8, "little"),
        )
        self.assertEqual(str(accounts[instruction.accounts[0]]), organizer_wallet)
        self.assertEqual(str(accounts[instruction.accounts[1]]), event.event_pda)
        self.assertEqual(
            ChainTransaction.objects.get().instruction,
            ChainTransaction.Instruction.WITHDRAW_FUNDS,
        )
        self.assertEqual(
            ChainTransaction.objects.get().accounts["organizer_wallet"],
            organizer_wallet,
        )

    @patch("blockchain.services._read_on_chain_event_state")
    def test_prepare_withdraw_funds_rejects_unresolved_or_frozen_event(
        self,
        read_event_state,
    ):
        wallet = WalletProfile.objects.create(wallet_address=str(Pubkey.new_unique()))
        organizer = Organizer.objects.create(wallet=wallet, name="Organizer")
        event_id = 78
        event = Event.objects.create(
            organizer=organizer,
            title="Frozen Event",
            event_date=timezone.now() - timedelta(hours=2),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
        )
        read_event_state.return_value = {
            "is_canceled": False,
            "is_completed": False,
            "is_active": False,
            "is_frozen": True,
        }

        with self.assertRaisesMessage(ValueError, "frozen"):
            prepare_withdraw_funds_transaction(event, wallet.wallet_address)

    @patch("blockchain.services._rpc_client")
    def test_prepare_ticket_purchase_builds_anchor_instruction(self, rpc_client):
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=789,
        )
        buyer_wallet = str(Pubkey.new_unique())
        WalletProfile.objects.create(wallet_address=buyer_wallet)
        organizer = Organizer.objects.create(
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Sale Event",
            event_date=timezone.now() + timedelta(days=10),
            on_chain_event_id=77,
            event_pda=str(Pubkey.new_unique()),
            collection_address=str(Pubkey.new_unique()),
            is_active=True,
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="VIP",
            on_chain_ticket_type_id=5,
            price_in_lamports=250_000,
            max_supply=10,
            is_active=True,
        )
        asset_address = str(Pubkey.new_unique())

        result = prepare_buy_ticket_crypto_transaction(
            event_slug=event.slug,
            ticket_type_pk=ticket_type.pk,
            buyer_wallet=buyer_wallet,
            asset_address=asset_address,
            metadata_uri="https://example.com/vip-ticket.json",
        )

        unsigned = Transaction.from_bytes(
            base64.b64decode(result["unsigned_transaction"], validate=True)
        )
        instruction = unsigned.message.instructions[0]
        self.assertEqual(
            instruction.data[:8],
            hashlib.sha256(b"global:buy_ticket_crypto").digest()[:8],
        )
        self.assertEqual(result["price_in_lamports"], ticket_type.price_in_lamports)
        self.assertEqual(result["last_valid_block_height"], 789)
        self.assertEqual(
            Purchase.objects.get(pk=result["purchase_id"]).status,
            Purchase.Status.PENDING,
        )
        self.assertEqual(
            Ticket.objects.get(pk=result["ticket_id"]).status,
            Ticket.Status.PENDING,
        )
        record = ChainTransaction.objects.get(pk=result["chain_transaction_id"])
        self.assertEqual(record.instruction, ChainTransaction.Instruction.BUY_TICKET_CRYPTO)
        self.assertEqual(record.accounts["buyer_wallet"], buyer_wallet)

    @patch("blockchain.services._rpc_client")
    def test_purchase_rejects_inactive_event(self, rpc_client):
        buyer_wallet = str(Pubkey.new_unique())
        WalletProfile.objects.create(wallet_address=buyer_wallet)
        organizer = Organizer.objects.create(
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Inactive Event",
            event_date=timezone.now() + timedelta(days=10),
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="Regular",
            price_in_lamports=100,
            max_supply=10,
            is_active=True,
        )

        with self.assertRaises(ValueError):
            prepare_buy_ticket_crypto_transaction(
                event_slug=event.slug,
                ticket_type_pk=ticket_type.pk,
                buyer_wallet=buyer_wallet,
                asset_address=str(Pubkey.new_unique()),
                metadata_uri="https://example.com/ticket.json",
            )

        self.assertFalse(Purchase.objects.exists())

    @patch("blockchain.services._rpc_client")
    def test_prepare_platform_initialization_builds_anchor_instruction(self, rpc_client):
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=200,
        )
        admin_wallet = str(Pubkey.new_unique())
        backend_wallet = str(Pubkey.new_unique())
        platform = Platform.objects.create(
            admin_wallet=admin_wallet,
            backend_authority_wallet=backend_wallet,
        )

        result = prepare_initialize_platform_transaction(
            platform,
            admin_wallet,
            backend_wallet,
        )

        transaction = Transaction.from_bytes(
            base64.b64decode(result["unsigned_transaction"], validate=True)
        )
        instruction = transaction.message.instructions[0]
        self.assertEqual(
            instruction.data[:8],
            hashlib.sha256(b"global:initialize_platform").digest()[:8],
        )
        self.assertEqual(instruction.data[8:], bytes(Pubkey.from_string(backend_wallet)))
        self.assertEqual(result["last_valid_block_height"], 200)
        record = ChainTransaction.objects.get(pk=result["id"])
        self.assertEqual(
            record.instruction,
            ChainTransaction.Instruction.INITIALIZE_PLATFORM,
        )
        self.assertEqual(record.accounts["platform_pk"], platform.pk)

    @patch("blockchain.services._backend_authority_keypair")
    @patch("blockchain.services._rpc_client")
    def test_submit_finalize_event_uses_backend_authority_signature(
        self,
        rpc_client,
        load_authority,
    ):
        authority = Keypair()
        load_authority.return_value = authority
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=456,
        )
        platform = Platform.objects.create(
            admin_wallet=str(Pubkey.new_unique()),
            backend_authority_wallet=str(authority.pubkey()),
            is_initialized=True,
        )
        organizer = Organizer.objects.create(name="Organizer")
        event = Event.objects.create(
            organizer=organizer,
            title="Completed Event",
            event_date=timezone.now() - timedelta(days=2),
            on_chain_event_id=41,
            event_pda=str(_event_pda(41)),
            is_active=True,
        )

        record = submit_finalize_event_transaction(event, valid_disputes=2)

        signed = Transaction.from_bytes(
            base64.b64decode(record.accounts["signed_transaction"], validate=True)
        )
        instruction = signed.message.instructions[0]
        self.assertEqual(
            instruction.data[:8],
            hashlib.sha256(b"global:finalize_event").digest()[:8],
        )
        self.assertEqual(instruction.data[8:], struct.pack("<QI", 41, 2))
        self.assertEqual(signed.message.account_keys[0], authority.pubkey())
        self.assertEqual(record.instruction, ChainTransaction.Instruction.FINALIZE_EVENT)
        self.assertEqual(record.event_id_on_chain, 41)
        self.assertEqual(record.accounts["backend_authority_wallet"], str(authority.pubkey()))
        self.assertEqual(record.accounts["valid_disputes"], 2)
        self.assertEqual(record.accounts["last_valid_block_height"], 456)
        rpc_client.return_value.send_raw_transaction.assert_called_once()
        self.assertTrue(platform.is_initialized)

    @patch("blockchain.services._rpc_client")
    def test_confirmed_finalize_event_syncs_on_chain_result(self, rpc_client):
        organizer = Organizer.objects.create(name="Organizer")
        event_id = 42
        event_pda = _event_pda(event_id)
        event = Event.objects.create(
            organizer=organizer,
            title="Finalized Event",
            event_date=timezone.now() - timedelta(days=2),
            on_chain_event_id=event_id,
            event_pda=str(event_pda),
            is_active=True,
        )
        chain_transaction = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event_id,
            accounts={"event_pk": event.pk, "event_pda": str(event_pda)},
        )
        account_data = (
            hashlib.sha256(b"account:EventState").digest()[:8]
            + struct.pack("<Q", event_id)
            + bytes(Pubkey.new_unique())
            + bytes(Pubkey.new_unique())
            + struct.pack("<q", int(event.event_date.timestamp()))
            + struct.pack("<????III", False, True, False, False, 10, 8, 6)
            + b"\x00"
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=902,
            )
        ]
        rpc_client.return_value.get_account_info.return_value.value = SimpleNamespace(
            owner=_program_id(),
            data=account_data,
        )

        self.assertTrue(confirm_submitted_transaction(chain_transaction))

        event.refresh_from_db()
        chain_transaction.refresh_from_db()
        self.assertFalse(event.is_active)
        self.assertTrue(event.is_completed)
        self.assertFalse(event.is_canceled)
        self.assertFalse(event.is_frozen)
        self.assertEqual(chain_transaction.status, ChainTransaction.Status.CONFIRMED)

    @patch("blockchain.services._backend_authority_keypair")
    @patch("blockchain.services._rpc_client")
    def test_submit_resolve_frozen_event_is_signed_by_backend_authority(
        self,
        rpc_client,
        load_authority,
    ):
        authority = Keypair()
        load_authority.return_value = authority
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=654,
        )
        platform = Platform.objects.create(
            admin_wallet=str(Pubkey.new_unique()),
            backend_authority_wallet=str(authority.pubkey()),
            is_initialized=True,
        )
        organizer = Organizer.objects.create(name="Organizer")
        event_id = 43
        event_pda = _event_pda(event_id)
        event = Event.objects.create(
            organizer=organizer,
            title="Frozen Event",
            event_date=timezone.now() - timedelta(days=2),
            on_chain_event_id=event_id,
            event_pda=str(event_pda),
            is_active=False,
            is_frozen=True,
        )
        account_data = (
            hashlib.sha256(b"account:EventState").digest()[:8]
            + struct.pack("<Q", event_id)
            + bytes(Pubkey.new_unique())
            + bytes(Pubkey.new_unique())
            + struct.pack("<q", int(event.event_date.timestamp()))
            + struct.pack("<????III", False, False, False, True, 10, 4, 8)
            + b"\x00"
        )
        rpc_client.return_value.get_account_info.return_value.value = SimpleNamespace(
            owner=_program_id(),
            data=account_data,
        )

        record = submit_resolve_frozen_event_transaction(event, action_type=True)

        signed = Transaction.from_bytes(
            base64.b64decode(record.accounts["signed_transaction"], validate=True)
        )
        instruction = signed.message.instructions[0]
        self.assertEqual(
            instruction.data[:8],
            hashlib.sha256(b"global:resolve_frozen_event").digest()[:8],
        )
        self.assertEqual(instruction.data[8:], struct.pack("<Q?", event_id, True))
        self.assertEqual(signed.message.account_keys[0], authority.pubkey())
        self.assertEqual(
            record.instruction,
            ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT,
        )
        self.assertTrue(record.accounts["action_type"])
        self.assertEqual(record.accounts["backend_authority_wallet"], str(authority.pubkey()))
        rpc_client.return_value.send_raw_transaction.assert_called_once()
        self.assertTrue(platform.is_initialized)

    @patch("blockchain.services._rpc_client")
    def test_confirmed_resolve_frozen_event_updates_database_state(self, rpc_client):
        organizer = Organizer.objects.create(name="Organizer")
        event_id = 44
        event_pda = _event_pda(event_id)
        event = Event.objects.create(
            organizer=organizer,
            title="Frozen Event",
            event_date=timezone.now() - timedelta(days=2),
            on_chain_event_id=event_id,
            event_pda=str(event_pda),
            is_active=False,
            is_frozen=True,
        )
        chain_transaction = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event_id,
            accounts={
                "event_pk": event.pk,
                "event_pda": str(event_pda),
                "action_type": False,
            },
        )
        account_data = (
            hashlib.sha256(b"account:EventState").digest()[:8]
            + struct.pack("<Q", event_id)
            + bytes(Pubkey.new_unique())
            + bytes(Pubkey.new_unique())
            + struct.pack("<q", int(event.event_date.timestamp()))
            + struct.pack("<????III", True, False, False, False, 10, 4, 8)
            + b"\x00"
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=903,
            )
        ]
        rpc_client.return_value.get_account_info.return_value.value = SimpleNamespace(
            owner=_program_id(),
            data=account_data,
        )

        self.assertTrue(confirm_submitted_transaction(chain_transaction))

        event.refresh_from_db()
        chain_transaction.refresh_from_db()
        self.assertTrue(event.is_canceled)
        self.assertFalse(event.is_completed)
        self.assertFalse(event.is_frozen)
        self.assertEqual(chain_transaction.status, ChainTransaction.Status.CONFIRMED)

    @patch("blockchain.services._rpc_client")
    def test_confirmed_initialization_updates_platform_configuration(self, rpc_client):
        admin_wallet = str(Pubkey.new_unique())
        backend_wallet = str(Pubkey.new_unique())
        platform = Platform.objects.create(
            admin_wallet=admin_wallet,
            backend_authority_wallet=backend_wallet,
        )
        chain_transaction = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.INITIALIZE_PLATFORM,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            accounts={
                "platform_pk": platform.pk,
                "admin_wallet": admin_wallet,
                "backend_authority_wallet": backend_wallet,
                "platform_pda": str(Pubkey.new_unique()),
            },
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=789,
            )
        ]

        confirmed = confirm_submitted_transaction(chain_transaction)

        self.assertTrue(confirmed)
        platform.refresh_from_db()
        chain_transaction.refresh_from_db()
        self.assertTrue(platform.is_initialized)
        self.assertIsNotNone(platform.initialized_at)
        self.assertEqual(
            platform.program_id,
            "Gmv9vZgZLwRjqGySw8u65MGKjnpJNiFm2Y2GeALMuRgw",
        )
        self.assertEqual(chain_transaction.status, ChainTransaction.Status.CONFIRMED)

    @patch("blockchain.services._rpc_client")
    def test_confirmed_purchase_activates_ticket_and_updates_counters(self, rpc_client):
        organizer = Organizer.objects.create(
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Purchase Event",
            event_date=timezone.now() + timedelta(days=5),
            on_chain_event_id=12,
            event_pda=str(Pubkey.new_unique()),
            collection_address=str(Pubkey.new_unique()),
            is_active=True,
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="General",
            on_chain_ticket_type_id=4,
            price_in_lamports=200_000,
            max_supply=10,
            is_active=True,
        )
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=str(Pubkey.new_unique()),
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=ticket_type.price_in_lamports,
        )
        purchase = Purchase.objects.create(
            event=event,
            ticket_type=ticket_type,
            ticket=ticket,
            buyer_wallet=ticket.owner_wallet,
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=ticket_type.price_in_lamports,
        )
        chain_transaction = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.BUY_TICKET_CRYPTO,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event.on_chain_event_id,
            accounts={
                "event_pk": event.pk,
                "ticket_type_pk": ticket_type.pk,
                "ticket_pk": ticket.pk,
                "purchase_pk": purchase.pk,
                "ticket_pda": str(Pubkey.new_unique()),
            },
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=900,
            )
        ]

        confirmed = confirm_submitted_transaction(chain_transaction)

        self.assertTrue(confirmed)
        event.refresh_from_db()
        ticket_type.refresh_from_db()
        ticket.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(event.total_tickets_sold, 1)
        self.assertEqual(event.active_tickets, 1)
        self.assertEqual(ticket_type.minted, 1)
        self.assertEqual(ticket.status, Ticket.Status.ACTIVE)
        self.assertEqual(ticket.ticket_record_pda, chain_transaction.accounts["ticket_pda"])
        self.assertEqual(purchase.status, Purchase.Status.COMPLETED)
        self.assertEqual(purchase.transaction_signature, chain_transaction.signature)

    @patch("blockchain.scheduler.remove_event_finalization")
    @patch("blockchain.services._read_on_chain_event_state")
    @patch("blockchain.services._rpc_client")
    def test_confirmed_event_cancellation_syncs_event_state(
        self,
        rpc_client,
        read_event_state,
        remove_finalization,
    ):
        organizer = Organizer.objects.create(name="Organizer")
        event_id = 72
        event = Event.objects.create(
            organizer=organizer,
            title="Canceled Event",
            event_date=timezone.now() + timedelta(days=5),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            is_active=True,
        )
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event_id,
            accounts={
                "event_pk": event.pk,
                "event_pda": event.event_pda,
            },
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=901,
            )
        ]
        read_event_state.return_value = {
            "is_canceled": True,
            "is_completed": False,
            "is_active": False,
            "is_frozen": False,
        }

        with self.captureOnCommitCallbacks(execute=True):
            confirmed = confirm_submitted_transaction(record)

        self.assertTrue(confirmed)
        event.refresh_from_db()
        record.refresh_from_db()
        self.assertTrue(event.is_canceled)
        self.assertFalse(event.is_active)
        self.assertEqual(record.status, ChainTransaction.Status.CONFIRMED)
        remove_finalization.assert_called_once_with(event.pk)

    @patch("blockchain.services._rpc_client")
    def test_confirmed_ticket_refund_updates_ticket_purchase_and_counts(self, rpc_client):
        wallet = str(Pubkey.new_unique())
        organizer = Organizer.objects.create(name="Organizer")
        event = Event.objects.create(
            organizer=organizer,
            title="Refunded Event",
            event_date=timezone.now() + timedelta(days=4),
            on_chain_event_id=74,
            event_pda=str(_event_pda(74)),
            active_tickets=1,
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="General",
            on_chain_ticket_type_id=5,
            price_in_lamports=300_000,
            max_supply=5,
            minted=1,
        )
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=wallet,
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=300_000,
            status=Ticket.Status.ACTIVE,
        )
        purchase = Purchase.objects.create(
            event=event,
            ticket_type=ticket_type,
            ticket=ticket,
            buyer_wallet=wallet,
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=300_000,
            status=Purchase.Status.COMPLETED,
        )
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event.on_chain_event_id,
            accounts={
                "event_pk": event.pk,
                "ticket_pk": ticket.pk,
                "ticket_type_pk": ticket_type.pk,
                "purchase_pk": purchase.pk,
                "owner_wallet": wallet,
            },
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=903,
            )
        ]

        confirmed = confirm_submitted_transaction(record)

        self.assertTrue(confirmed)
        event.refresh_from_db()
        ticket_type.refresh_from_db()
        ticket.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.REFUNDED)
        self.assertEqual(purchase.status, Purchase.Status.REFUNDED)
        self.assertEqual(ticket_type.minted, 0)
        self.assertEqual(event.active_tickets, 0)

    @patch("blockchain.services._rpc_client")
    def test_submit_sends_only_transaction_with_prepared_message(self, rpc_client):
        payer = Keypair()
        program = Pubkey.new_unique()
        instruction = Instruction(
            program,
            b"test-instruction",
            [AccountMeta(payer.pubkey(), is_signer=True, is_writable=True)],
        )
        message = Message.new_with_blockhash(
            [instruction],
            payer.pubkey(),
            Hash.default(),
        )
        transaction = Transaction.new_unsigned(message)
        transaction.sign([payer], Hash.default())
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
            accounts={
                "expected_message": base64.b64encode(bytes(message)).decode("ascii"),
                "event_pk": 1,
            },
        )
        rpc_client.return_value.send_raw_transaction.return_value.value = str(
            transaction.signatures[0]
        )

        submitted = submit_signed_transaction(
            record,
            base64.b64encode(bytes(transaction)).decode("ascii"),
        )

        self.assertEqual(submitted.status, ChainTransaction.Status.SUBMITTED)
        self.assertEqual(submitted.signature, str(transaction.signatures[0]))
        rpc_client.return_value.send_raw_transaction.assert_called_once()

    @patch("blockchain.services._rpc_client")
    def test_submit_accepts_compute_budget_instructions_added_by_wallet(self, rpc_client):
        payer = Keypair()
        program = Pubkey.new_unique()
        instruction = Instruction(
            program,
            b"test-instruction",
            [AccountMeta(payer.pubkey(), is_signer=True, is_writable=True)],
        )
        expected_message = Message.new_with_blockhash(
            [instruction],
            payer.pubkey(),
            Hash.default(),
        )
        signed_message = Message.new_with_blockhash(
            [
                set_compute_unit_limit(300_000),
                set_compute_unit_price(1_000),
                instruction,
            ],
            payer.pubkey(),
            Hash.default(),
        )
        transaction = Transaction.new_unsigned(signed_message)
        transaction.sign([payer], Hash.default())
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
            accounts={
                "expected_message": base64.b64encode(bytes(expected_message)).decode("ascii"),
                "event_pk": 1,
            },
        )
        rpc_client.return_value.send_raw_transaction.return_value.value = str(
            transaction.signatures[0]
        )

        submitted = submit_signed_transaction(
            record,
            base64.b64encode(bytes(transaction)).decode("ascii"),
        )

        self.assertEqual(submitted.status, ChainTransaction.Status.SUBMITTED)
        rpc_client.return_value.send_raw_transaction.assert_called_once()

    @patch("blockchain.services._rpc_client")
    def test_submit_rejects_unexpected_instruction_with_compute_budget(self, rpc_client):
        payer = Keypair()
        program = Pubkey.new_unique()
        instruction = Instruction(
            program,
            b"test-instruction",
            [AccountMeta(payer.pubkey(), is_signer=True, is_writable=True)],
        )
        expected_message = Message.new_with_blockhash(
            [instruction],
            payer.pubkey(),
            Hash.default(),
        )
        signed_message = Message.new_with_blockhash(
            [
                set_compute_unit_limit(300_000),
                Instruction(
                    Pubkey.new_unique(),
                    b"unexpected",
                    [],
                ),
                instruction,
            ],
            payer.pubkey(),
            Hash.default(),
        )
        transaction = Transaction.new_unsigned(signed_message)
        transaction.sign([payer], Hash.default())
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
            accounts={
                "expected_message": base64.b64encode(bytes(expected_message)).decode("ascii"),
                "event_pk": 1,
            },
        )

        with self.assertRaises(ValueError):
            submit_signed_transaction(
                record,
                base64.b64encode(bytes(transaction)).decode("ascii"),
            )

        rpc_client.return_value.send_raw_transaction.assert_not_called()

    @patch("blockchain.services._rpc_client")
    def test_submit_rejects_transaction_with_changed_message(self, rpc_client):
        payer = Keypair()
        instruction = Instruction(
            Pubkey.new_unique(),
            b"tampered",
            [AccountMeta(payer.pubkey(), is_signer=True, is_writable=True)],
        )
        message = Message.new_with_blockhash([instruction], payer.pubkey(), Hash.default())
        transaction = Transaction.new_unsigned(message)
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
            accounts={"expected_message": base64.b64encode(b"expected").decode("ascii")},
        )

        with self.assertRaises(ValueError):
            submit_signed_transaction(
                record,
                base64.b64encode(bytes(transaction)).decode("ascii"),
            )

        rpc_client.return_value.send_raw_transaction.assert_not_called()

    @patch("blockchain.services._rpc_client")
    def test_create_event_transaction_has_expected_anchor_instruction(self, rpc_client):
        rpc_client.return_value.get_latest_blockhash.return_value.value = SimpleNamespace(
            blockhash=Hash.default(),
            last_valid_block_height=123,
        )
        user_wallet = WalletProfile.objects.create(wallet_address=str(Pubkey.new_unique()))
        organizer = Organizer.objects.create(
            wallet=user_wallet,
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Anchor Event",
            event_date=timezone.now() + timedelta(days=3),
            metadata_uri="https://example.com/event.json",
        )
        collection = str(Pubkey.new_unique())

        result = prepare_create_event_transaction(
            event=event,
            organizer_wallet=user_wallet.wallet_address,
            collection_address=collection,
        )

        unsigned = Transaction.from_bytes(
            base64.b64decode(result["unsigned_transaction"], validate=True)
        )
        instruction = unsigned.message.instructions[0]
        expected_discriminator = hashlib.sha256(b"global:create_event").digest()[:8]
        self.assertEqual(instruction.data[:8], expected_discriminator)
        self.assertEqual(result["last_valid_block_height"], 123)
        self.assertEqual(result["collection_address"], collection)
        self.assertIn(METAPLEX_CORE_PROGRAM_ID, unsigned.message.account_keys)
        chain_transaction = ChainTransaction.objects.get(pk=result["id"])
        self.assertEqual(chain_transaction.status, ChainTransaction.Status.CREATED)
        self.assertEqual(chain_transaction.accounts["event_pk"], event.pk)

    @patch("blockchain.scheduler.schedule_event_finalization")
    @patch("blockchain.services._rpc_client")
    def test_confirmed_create_event_activates_event(self, rpc_client, schedule_finalization):
        wallet = WalletProfile.objects.create(wallet_address=str(Pubkey.new_unique()))
        organizer = Organizer.objects.create(
            wallet=wallet,
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Confirmed Event",
            event_date=timezone.now() + timedelta(days=3),
            metadata_uri="https://example.com/event.json",
        )
        chain_transaction = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CREATE_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event.pk,
            accounts={
                "event_pk": event.pk,
                "event_pda": str(Pubkey.new_unique()),
                "collection_address": str(Pubkey.new_unique()),
            },
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=456,
            )
        ]

        with self.captureOnCommitCallbacks(execute=True):
            confirmed = confirm_submitted_transaction(chain_transaction)

        self.assertTrue(confirmed)
        event.refresh_from_db()
        chain_transaction.refresh_from_db()
        self.assertTrue(event.is_active)
        self.assertEqual(event.on_chain_event_id, event.pk)
        self.assertEqual(chain_transaction.status, ChainTransaction.Status.CONFIRMED)
        self.assertEqual(chain_transaction.confirmed_slot, 456)
        schedule_finalization.assert_called_once_with(event.pk)

    @patch("blockchain.services._rpc_client")
    def test_consume_ticket_signs_and_submits_backend_authority_transaction(
        self,
        rpc_client,
    ):
        authority = Keypair()
        with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as key_file:
            json.dump(list(authority.to_bytes_array()), key_file)
            key_file.flush()
            with self.settings(SOLANA_BACKEND_KEYPAIR_PATH=key_file.name):
                rpc_client.return_value.get_latest_blockhash.return_value.value = (
                    SimpleNamespace(
                        blockhash=Hash.default(),
                        last_valid_block_height=4567,
                    )
                )
                rpc_client.return_value.send_raw_transaction.return_value.value = (
                    str(Signature.default())
                )
                Platform.objects.create(
                    admin_wallet=str(Pubkey.new_unique()),
                    backend_authority_wallet=str(authority.pubkey()),
                    program_id="test-program",
                    is_initialized=True,
                )
                owner_wallet = str(Pubkey.new_unique())
                organizer = Organizer.objects.create(
                    name="Organizer",
                    email="organizer@example.com",
                )
                event_id = 75
                event = Event.objects.create(
                    organizer=organizer,
                    title="Scan Event",
                    event_date=timezone.now() + timedelta(days=3),
                    on_chain_event_id=event_id,
                    event_pda=str(_event_pda(event_id)),
                    is_active=True,
                )
                ticket_type = TicketType.objects.create(
                    event=event,
                    name="General",
                    price_in_lamports=100_000,
                    max_supply=5,
                )
                asset_address = str(Pubkey.new_unique())
                ticket = Ticket.objects.create(
                    event=event,
                    ticket_type=ticket_type,
                    owner_wallet=owner_wallet,
                    name="General #1",
                    metadata_uri="https://example.com/ticket.json",
                    price_paid_lamports=100_000,
                    on_chain_ticket_id=1,
                    asset_address=asset_address,
                    ticket_record_pda=str(_ticket_pda(asset_address)),
                    status=Ticket.Status.ACTIVE,
                )

                record = submit_consume_ticket_transaction(
                    ticket=ticket,
                    scanned_by_wallet=str(Pubkey.new_unique()),
                )

        self.assertEqual(record.instruction, ChainTransaction.Instruction.CONSUME_TICKET)
        self.assertEqual(record.status, ChainTransaction.Status.SUBMITTED)
        sent_bytes = rpc_client.return_value.send_raw_transaction.call_args.args[0]
        sent_transaction = Transaction.from_bytes(sent_bytes)
        sent_transaction.verify()
        instruction = sent_transaction.message.instructions[0]
        self.assertEqual(
            instruction.data,
            hashlib.sha256(b"global:consume_ticket").digest()[:8]
            + event_id.to_bytes(8, "little"),
        )
        self.assertEqual(str(sent_transaction.signatures[0]), record.signature)
        self.assertEqual(record.accounts["ticket_pk"], ticket.pk)

    @patch("blockchain.services._rpc_client")
    def test_confirmed_consume_ticket_synchronizes_local_state(self, rpc_client):
        wallet = WalletProfile.objects.create(wallet_address=str(Pubkey.new_unique()))
        organizer = Organizer.objects.create(
            wallet=wallet,
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Consumed Event",
            event_date=timezone.now() + timedelta(days=3),
            on_chain_event_id=92,
            is_active=True,
            total_tickets_sold=1,
            active_tickets=1,
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="General",
            price_in_lamports=100_000,
            max_supply=5,
            minted=1,
        )
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=str(Pubkey.new_unique()),
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=100_000,
            on_chain_ticket_id=1,
            asset_address=str(Pubkey.new_unique()),
            status=Ticket.Status.ACTIVE,
        )
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CONSUME_TICKET,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=event.on_chain_event_id,
            accounts={
                "event_pk": event.pk,
                "ticket_pk": ticket.pk,
                "scanned_by_wallet": wallet.wallet_address,
                "signed_transaction": "temporary-signed-bytes",
            },
        )
        rpc_client.return_value.get_signature_statuses.return_value.value = [
            SimpleNamespace(
                err=None,
                confirmation_status=TransactionConfirmationStatus.Confirmed,
                slot=902,
            )
        ]

        confirmed = confirm_submitted_transaction(record)

        self.assertTrue(confirmed)
        event.refresh_from_db()
        ticket.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.USED)
        self.assertEqual(event.total_ticket_scanned, 1)
        self.assertEqual(
            ticket.scan_logs.get().result,
            ScanLog.Result.ACCEPTED,
        )
        self.assertEqual(record.status, ChainTransaction.Status.CONFIRMED)
        self.assertNotIn("signed_transaction", record.accounts)

    @patch("blockchain.services._rpc_client")
    def test_expired_consume_transaction_is_not_rebroadcast(self, rpc_client):
        wallet = WalletProfile.objects.create(wallet_address=str(Pubkey.new_unique()))
        organizer = Organizer.objects.create(
            wallet=wallet,
            name="Organizer",
            email="organizer@example.com",
        )
        event = Event.objects.create(
            organizer=organizer,
            title="Expired Scan Event",
            event_date=timezone.now() + timedelta(days=3),
        )
        ticket_type = TicketType.objects.create(
            event=event,
            name="General",
            price_in_lamports=100_000,
            max_supply=5,
        )
        ticket = Ticket.objects.create(
            event=event,
            ticket_type=ticket_type,
            owner_wallet=str(Pubkey.new_unique()),
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=100_000,
            asset_address=str(Pubkey.new_unique()),
            status=Ticket.Status.ACTIVE,
        )
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CONSUME_TICKET,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            accounts={
                "ticket_pk": ticket.pk,
                "scanned_by_wallet": wallet.wallet_address,
                "last_valid_block_height": 100,
                "signed_transaction": "dGVzdA==",
            },
        )
        rpc_client.return_value.get_block_height.return_value.value = 101

        result = rebroadcast_consume_ticket_transaction(record)

        self.assertIsNone(result)
        record.refresh_from_db()
        self.assertEqual(record.status, ChainTransaction.Status.FAILED)
        self.assertEqual(ticket.scan_logs.get().result, ScanLog.Result.REJECTED)
        rpc_client.return_value.send_raw_transaction.assert_not_called()


class FinalizeDueEventsCommandTests(TestCase):
    def setUp(self):
        self.organizer = Organizer.objects.create(name="Organizer")
        self.event = Event.objects.create(
            organizer=self.organizer,
            title="Due Event",
            event_date=timezone.now() - timedelta(hours=25),
            on_chain_event_id=93,
            event_pda=str(_event_pda(93)),
            is_active=True,
        )

    @patch("blockchain.management.commands.finalize_due_events.submit_finalize_event_transaction")
    def test_command_waits_for_pending_disputes(self, submit_finalize):
        dispute = EventDispute.objects.create(
            event=self.event,
            reported_by_wallet=str(Pubkey.new_unique()),
            reason="Awaiting review",
            status=EventDispute.Status.PENDING,
        )

        call_command("finalize_due_events")

        submit_finalize.assert_not_called()
        dispute.refresh_from_db()
        self.assertEqual(dispute.status, EventDispute.Status.PENDING)

    @patch("blockchain.management.commands.finalize_due_events.rebroadcast_backend_transaction")
    @patch("blockchain.management.commands.finalize_due_events.confirm_submitted_transaction")
    @patch("blockchain.management.commands.finalize_due_events.submit_finalize_event_transaction")
    def test_command_submits_with_valid_dispute_count_after_24_hours(
        self,
        submit_finalize,
        confirm_transaction,
        rebroadcast_transaction,
    ):
        EventDispute.objects.create(
            event=self.event,
            reported_by_wallet=str(Pubkey.new_unique()),
            reason="Verified issue",
            status=EventDispute.Status.VALID,
        )
        record = ChainTransaction(
            instruction=ChainTransaction.Instruction.FINALIZE_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
        )
        submit_finalize.return_value = record
        confirm_transaction.return_value = False
        rebroadcast_transaction.return_value = True

        call_command("finalize_due_events")

        submit_finalize.assert_called_once()
        self.assertEqual(submit_finalize.call_args.args[0].pk, self.event.pk)
        self.assertEqual(submit_finalize.call_args.args[1], 1)
        rebroadcast_transaction.assert_called_once_with(record)


class ResolveFrozenEventAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="platform-moderator",
            is_staff=True,
        )
        self.regular_user = user_model.objects.create_user(username="organizer")
        organizer = Organizer.objects.create(
            user=self.regular_user,
            name="Organizer",
        )
        self.event = Event.objects.create(
            organizer=organizer,
            title="Frozen Event",
            event_date=timezone.now() - timedelta(days=2),
            on_chain_event_id=51,
            event_pda=str(_event_pda(51)),
            is_active=False,
            is_frozen=True,
        )
        self.url = f"/api/blockchain/events/{self.event.pk}/resolve-frozen/"

    def test_only_staff_can_resolve_frozen_events(self):
        self.client.force_authenticate(self.regular_user)

        response = self.client.post(self.url, {"action": "complete"}, format="json")

        self.assertEqual(response.status_code, 403)

    def test_staff_can_request_backend_signed_frozen_event_resolution(self):
        self.client.force_authenticate(self.staff_user)
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.RESOLVE_FROZEN_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=self.event.on_chain_event_id,
            accounts={"action_type": True},
        )

        with patch(
            "blockchain.views.submit_resolve_frozen_event_transaction",
            return_value=record,
        ) as submit_transaction:
            response = self.client.post(
                self.url,
                {"action": "complete"},
                format="json",
            )

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["action"], "complete")
        self.assertEqual(response.data["transaction_id"], record.pk)
        submit_transaction.assert_called_once_with(self.event, True)

    def test_staff_must_choose_a_supported_action(self):
        self.client.force_authenticate(self.staff_user)

        response = self.client.post(self.url, {"action": "withdraw"}, format="json")

        self.assertEqual(response.status_code, 400)


class WithdrawEventFundsAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organizer_user = user_model.objects.create_user(username="funds-organizer")
        self.other_user = user_model.objects.create_user(username="other-organizer")
        self.organizer_wallet = str(Pubkey.new_unique())
        wallet = WalletProfile.objects.create(
            user=self.organizer_user,
            wallet_address=self.organizer_wallet,
        )
        organizer = Organizer.objects.create(
            user=self.organizer_user,
            wallet=wallet,
            name="Organizer",
        )
        event_id = 79
        self.event = Event.objects.create(
            organizer=organizer,
            title="Completed Event",
            event_date=timezone.now() - timedelta(days=1),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            is_completed=True,
        )
        self.prepare_url = f"/api/blockchain/events/{self.event.pk}/withdraw/"

    def test_only_event_organizer_can_prepare_withdrawal(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.post(self.prepare_url)

        self.assertEqual(response.status_code, 404)

    def test_organizer_can_prepare_withdrawal(self):
        self.client.force_authenticate(self.organizer_user)
        prepared = {
            "id": 29,
            "unsigned_transaction": "dGVzdA==",
            "last_valid_block_height": 789,
            "event_pda": self.event.event_pda,
        }

        with patch(
            "blockchain.views.prepare_withdraw_funds_transaction",
            return_value=prepared,
        ) as prepare_transaction:
            response = self.client.post(self.prepare_url)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["chain_transaction_id"], prepared["id"])
        prepare_transaction.assert_called_once_with(self.event, self.organizer_wallet)

    def test_withdrawal_transaction_submit_requires_the_event_organizer(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.WITHDRAW_FUNDS,
            event_id_on_chain=self.event.on_chain_event_id,
            accounts={
                "event_pk": self.event.pk,
                "organizer_wallet": self.organizer_wallet,
                "expected_message": "dGVzdA==",
            },
        )
        submit_url = f"/api/blockchain/transactions/{record.pk}/submit/"
        self.client.force_authenticate(self.other_user)

        response = self.client.post(
            submit_url,
            {"signed_transaction": "dGVzdA=="},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_event_organizer_can_submit_withdrawal_transaction(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.WITHDRAW_FUNDS,
            event_id_on_chain=self.event.on_chain_event_id,
            accounts={
                "event_pk": self.event.pk,
                "organizer_wallet": self.organizer_wallet,
                "expected_message": "dGVzdA==",
            },
        )
        submit_url = f"/api/blockchain/transactions/{record.pk}/submit/"
        self.client.force_authenticate(self.organizer_user)

        with patch(
            "blockchain.views.submit_signed_transaction",
            return_value=record,
        ) as submit_transaction:
            response = self.client.post(
                submit_url,
                {"signed_transaction": "dGVzdA=="},
                format="json",
            )

        self.assertEqual(response.status_code, 202, response.data)
        submit_transaction.assert_called_once()


class CancelEventAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organizer_user = user_model.objects.create_user(username="cancel-organizer")
        self.other_user = user_model.objects.create_user(username="other-cancel-organizer")
        self.organizer_wallet = str(Pubkey.new_unique())
        wallet = WalletProfile.objects.create(
            user=self.organizer_user,
            wallet_address=self.organizer_wallet,
        )
        organizer = Organizer.objects.create(
            user=self.organizer_user,
            wallet=wallet,
            name="Organizer",
        )
        event_id = 73
        self.event = Event.objects.create(
            organizer=organizer,
            title="Active Event",
            event_date=timezone.now() + timedelta(days=1),
            on_chain_event_id=event_id,
            event_pda=str(_event_pda(event_id)),
            is_active=True,
        )
        self.prepare_url = f"/api/blockchain/events/{self.event.pk}/cancel/"

    def test_only_event_organizer_can_prepare_cancellation(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.post(self.prepare_url)

        self.assertEqual(response.status_code, 404)

    def test_organizer_can_prepare_cancellation(self):
        self.client.force_authenticate(self.organizer_user)
        prepared = {
            "id": 31,
            "unsigned_transaction": "dGVzdA==",
            "last_valid_block_height": 789,
            "event_pda": self.event.event_pda,
        }

        with patch(
            "blockchain.views.prepare_cancel_event_transaction",
            return_value=prepared,
        ) as prepare_transaction:
            response = self.client.post(self.prepare_url)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["chain_transaction_id"], prepared["id"])
        prepare_transaction.assert_called_once_with(self.event, self.organizer_wallet)

    def test_cancellation_transaction_submit_requires_the_event_organizer(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
            event_id_on_chain=self.event.on_chain_event_id,
            accounts={
                "event_pk": self.event.pk,
                "organizer_wallet": self.organizer_wallet,
                "expected_message": "dGVzdA==",
            },
        )
        submit_url = f"/api/blockchain/transactions/{record.pk}/submit/"
        self.client.force_authenticate(self.other_user)

        response = self.client.post(
            submit_url,
            {"signed_transaction": "dGVzdA=="},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_event_organizer_can_submit_cancellation_transaction(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
            event_id_on_chain=self.event.on_chain_event_id,
            accounts={
                "event_pk": self.event.pk,
                "organizer_wallet": self.organizer_wallet,
                "expected_message": "dGVzdA==",
            },
        )
        submit_url = f"/api/blockchain/transactions/{record.pk}/submit/"
        self.client.force_authenticate(self.organizer_user)

        with patch(
            "blockchain.views.submit_signed_transaction",
            return_value=record,
        ) as submit_transaction:
            response = self.client.post(
                submit_url,
                {"signed_transaction": "dGVzdA=="},
                format="json",
            )

        self.assertEqual(response.status_code, 202, response.data)
        submit_transaction.assert_called_once()


class InitializePlatformAPITests(APITestCase):
    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="platform-admin",
            is_staff=True,
        )
        self.regular_user = get_user_model().objects.create_user(username="member")
        self.payload = {
            "admin_wallet": str(Pubkey.new_unique()),
            "backend_authority_wallet": str(Pubkey.new_unique()),
        }
        self.url = "/api/blockchain/platform/initialize/"

    def test_non_staff_user_cannot_initialize_platform(self):
        self.client.force_authenticate(self.regular_user)

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Platform.objects.exists())

    def test_staff_user_prepares_platform_initialization(self):
        self.client.force_authenticate(self.staff_user)
        with patch(
            "blockchain.views.prepare_initialize_platform_transaction",
            return_value={
                "id": 17,
                "unsigned_transaction": "base64-unsigned-transaction",
                "last_valid_block_height": 555,
                "platform_pda": str(Pubkey.new_unique()),
            },
        ) as prepare_transaction:
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        platform = Platform.objects.get(pk=1)
        self.assertFalse(platform.is_initialized)
        self.assertEqual(platform.admin_wallet, self.payload["admin_wallet"])
        self.assertEqual(platform.backend_authority_wallet, self.payload["backend_authority_wallet"])
        self.assertEqual(response.data["blockchain_transaction"]["id"], 17)
        prepare_transaction.assert_called_once()

    def test_invalid_wallet_address_is_rejected(self):
        self.client.force_authenticate(self.staff_user)
        payload = {**self.payload, "backend_authority_wallet": "not-a-public-key"}

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Platform.objects.exists())

    def test_rpc_transaction_rejection_is_returned_as_diagnostic(self):
        self.client.force_authenticate(self.staff_user)
        platform = Platform.objects.create(
            admin_wallet=self.payload["admin_wallet"],
            backend_authority_wallet=self.payload["backend_authority_wallet"],
        )
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.INITIALIZE_PLATFORM,
            accounts={"platform_pk": platform.pk},
        )
        with patch(
            "blockchain.views.submit_signed_transaction",
            side_effect=RPCException("simulation failed: blockhash not found"),
        ):
            response = self.client.post(
                f"/api/blockchain/transactions/{record.pk}/submit/",
                {"signed_transaction": "dGVzdA=="},
                format="json",
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("blockhash not found", response.data["detail"])
