from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from rest_framework import status
from rest_framework.test import APITestCase
from solders.signature import Signature
from solders.transaction_status import TransactionConfirmationStatus

from accounts.models import Platform, WalletProfile
from blockchain.models import ChainTransaction
from events.models import Event, Organizer, TicketType
from payments.models import Purchase
from .models import ScanLog, Ticket


class TicketModelTests(TestCase):
    def setUp(self):
        wallet = WalletProfile.objects.create(wallet_address="organizer-wallet")
        organizer = Organizer.objects.create(
            wallet=wallet,
            name="Organizer",
            email="organizer@example.com",
        )
        self.event = Event.objects.create(
            organizer=organizer,
            title="Concert",
            event_date=timezone.now() + timedelta(days=7),
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event,
            name="General",
            price_in_lamports=1_000_000,
            max_supply=10,
        )

    def create_ticket(self, **kwargs):
        values = {
            "event": self.event,
            "ticket_type": self.ticket_type,
            "owner_wallet": "attendee-wallet",
            "name": "General #1",
            "metadata_uri": "https://example.com/ticket.json",
            "price_paid_lamports": 1_000_000,
            "on_chain_ticket_id": 1,
            "asset_address": "ticket-asset",
        }
        values.update(kwargs)
        return Ticket.objects.create(**values)

    def test_ticket_defaults_to_pending_and_has_relations(self):
        ticket = self.create_ticket()

        self.assertEqual(ticket.status, Ticket.Status.PENDING)
        self.assertEqual(ticket.event, self.event)
        self.assertEqual(ticket.ticket_type, self.ticket_type)
        self.assertIn("Concert", str(ticket))

    def test_on_chain_ticket_id_is_unique_per_event(self):
        self.create_ticket()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_ticket(asset_address="second-asset")

    def test_scan_log_records_result(self):
        ticket = self.create_ticket()
        scan = ScanLog.objects.create(
            ticket=ticket,
            scanned_by_wallet="gate-wallet",
            result=ScanLog.Result.ACCEPTED,
        )

        self.assertEqual(ticket.scan_logs.get(), scan)
        self.assertIn("accepted", str(scan))

    def test_models_are_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(Ticket))
        self.assertTrue(admin.site.is_registered(ScanLog))


class TicketScanAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organizer_user = user_model.objects.create_user(username="event-organizer")
        self.owner_user = user_model.objects.create_user(username="ticket-owner")
        self.other_user = user_model.objects.create_user(username="other-user")
        self.organizer_wallet = WalletProfile.objects.create(
            user=self.organizer_user,
            wallet_address="verified-organizer-wallet",
        )
        WalletProfile.objects.create(
            user=self.owner_user,
            wallet_address="verified-owner-wallet",
        )
        self.organizer = Organizer.objects.create(
            user=self.organizer_user,
            wallet=self.organizer_wallet,
            name="Organizer",
            email="organizer@example.com",
        )
        self.event = Event.objects.create(
            organizer=self.organizer,
            title="Concert",
            event_date=timezone.now() + timedelta(days=7),
            is_active=True,
            total_tickets_sold=1,
            active_tickets=1,
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event,
            name="General",
            price_in_lamports=1_000_000,
            max_supply=1,
            minted=1,
        )
        self.ticket = Ticket.objects.create(
            event=self.event,
            ticket_type=self.ticket_type,
            owner_wallet="verified-owner-wallet",
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=1_000_000,
            on_chain_ticket_id=1,
            asset_address="ticket-asset",
            status=Ticket.Status.ACTIVE,
        )
        self.qr_url = f"/api/tickets/{self.ticket.pk}/qr/"
        self.mine_url = "/api/tickets/mine/"
        self.scan_url = "/api/tickets/scan/"

    def test_owner_can_list_owned_tickets(self):
        self.client.force_authenticate(self.owner_user)

        response = self.client.get(self.mine_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.ticket.pk)
        self.assertEqual(response.data[0]["event_title"], self.event.title)
        self.assertEqual(response.data[0]["ticket_type_name"], self.ticket_type.name)

    def test_ticket_list_only_includes_the_authenticated_wallets_tickets(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.get(self.mine_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data, [])

    def test_ticket_list_requires_authentication(self):
        response = self.client.get(self.mine_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def issue_qr_token(self):
        self.client.force_authenticate(self.owner_user)
        response = self.client.get(self.qr_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data["qr_token"]

    def test_ticket_owner_can_generate_short_lived_qr_token(self):
        token = self.issue_qr_token()

        self.assertTrue(token)
        self.assertEqual(self.client.get(self.qr_url).data["expires_in_seconds"], 60)

    def test_only_verified_ticket_owner_can_generate_qr_token(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.get(self.qr_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_event_organizer_scan_marks_ticket_used_once(self):
        token = self.issue_qr_token()
        self.client.force_authenticate(self.organizer_user)

        with (
            patch(
                "tickets.views.submit_consume_ticket_transaction",
                side_effect=self.create_submitted_consume_transaction,
            ),
            patch("blockchain.services._rpc_client") as rpc_client,
        ):
            rpc_client.return_value.get_signature_statuses.return_value.value = [
                SimpleNamespace(
                    err=None,
                    confirmation_status=TransactionConfirmationStatus.Confirmed,
                    slot=901,
                )
            ]
            first_response = self.client.post(
                self.scan_url,
                {"qr_token": token},
                format="json",
            )
        second_response = self.client.post(self.scan_url, {"qr_token": token}, format="json")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK, first_response.data)
        self.assertEqual(first_response.data["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(second_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second_response.data["result"], ScanLog.Result.ALREADY_USED)
        self.ticket.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.Status.USED)
        self.assertEqual(self.event.total_ticket_scanned, 1)
        self.assertEqual(
            list(self.ticket.scan_logs.order_by("result").values_list("result", flat=True)),
            [ScanLog.Result.ACCEPTED, ScanLog.Result.ALREADY_USED],
        )

    def create_submitted_consume_transaction(self, ticket, scanned_by_wallet):
        return ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CONSUME_TICKET,
            status=ChainTransaction.Status.SUBMITTED,
            signature=str(Signature.default()),
            event_id_on_chain=ticket.event.on_chain_event_id,
            asset_address=ticket.asset_address,
            accounts={
                "event_pk": ticket.event_id,
                "ticket_pk": ticket.pk,
                "scanned_by_wallet": scanned_by_wallet,
            },
        )

    def test_only_event_organizer_can_scan(self):
        token = self.issue_qr_token()
        self.client.force_authenticate(self.other_user)

        response = self.client.post(self.scan_url, {"qr_token": token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.ticket.scan_logs.count(), 0)

    @override_settings(TICKET_SCANNER_API_TOKEN="demo-scanner-secret")
    def test_backend_authority_scanner_can_scan_any_organizers_event(self):
        other_organizer = Organizer.objects.create(name="Another Organizer")
        other_event = Event.objects.create(
            organizer=other_organizer,
            title="Another Event",
            event_date=timezone.now() + timedelta(days=7),
            is_active=True,
            on_chain_event_id=101,
            event_pda="other-event-pda",
        )
        other_ticket_type = TicketType.objects.create(
            event=other_event,
            name="Other General",
            price_in_lamports=1_000_000,
            max_supply=1,
            minted=1,
        )
        other_ticket = Ticket.objects.create(
            event=other_event,
            ticket_type=other_ticket_type,
            owner_wallet="verified-owner-wallet",
            name="Other event ticket",
            metadata_uri="https://example.com/other-ticket.json",
            price_paid_lamports=1_000_000,
            status=Ticket.Status.ACTIVE,
            asset_address="other-event-ticket-asset",
            ticket_record_pda="other-event-ticket-pda",
        )
        Platform.objects.create(
            admin_wallet="admin-wallet",
            backend_authority_wallet="backend-authority-wallet",
            is_initialized=True,
        )
        self.client.force_authenticate(self.owner_user)
        qr_response = self.client.get(f"/api/tickets/{other_ticket.pk}/qr/")
        self.assertEqual(qr_response.status_code, status.HTTP_200_OK, qr_response.data)
        self.client.force_authenticate(user=None)

        with (
            patch(
                "tickets.views.submit_consume_ticket_transaction",
                side_effect=self.create_submitted_consume_transaction,
            ) as submit_transaction,
            patch("tickets.views.confirm_submitted_transaction", return_value=True),
        ):
            response = self.client.post(
                self.scan_url,
                {"qr_token": qr_response.data["qr_token"]},
                format="json",
                HTTP_X_SCANNER_TOKEN="demo-scanner-secret",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["result"], ScanLog.Result.ACCEPTED)
        self.assertEqual(response.data["event_id"], other_event.pk)
        self.assertEqual(
            submit_transaction.call_args.kwargs["scanned_by_wallet"],
            "backend-authority-wallet",
        )

    @override_settings(TICKET_SCANNER_API_TOKEN="demo-scanner-secret")
    def test_scanner_can_poll_pending_scan_until_confirmed_without_rescanning(self):
        Platform.objects.create(
            admin_wallet="admin-wallet",
            backend_authority_wallet="backend-authority-wallet",
            is_initialized=True,
        )
        event = self.event
        event.on_chain_event_id = 102
        event.save(update_fields=("on_chain_event_id",))
        self.ticket.asset_address = "scan-status-ticket-asset"
        self.ticket.ticket_record_pda = "scan-status-ticket-pda"
        self.ticket.save(update_fields=("asset_address", "ticket_record_pda"))
        self.client.force_authenticate(self.owner_user)
        qr_response = self.client.get(self.qr_url)
        self.assertEqual(qr_response.status_code, status.HTTP_200_OK, qr_response.data)
        self.client.force_authenticate(user=None)

        with (
            patch(
                "tickets.views.submit_consume_ticket_transaction",
                side_effect=self.create_submitted_consume_transaction,
            ) as submit_transaction,
            patch("tickets.views.confirm_submitted_transaction", return_value=False),
        ):
            scan_response = self.client.post(
                self.scan_url,
                {"qr_token": qr_response.data["qr_token"]},
                format="json",
                HTTP_X_SCANNER_TOKEN="demo-scanner-secret",
            )

        self.assertEqual(
            scan_response.status_code,
            status.HTTP_202_ACCEPTED,
            scan_response.data,
        )
        transaction_id = scan_response.data["chain_transaction_id"]

        def confirm_record(record):
            record.status = ChainTransaction.Status.CONFIRMED
            record.save(update_fields=("status", "updated_at"))
            return True

        with patch(
            "tickets.views.confirm_submitted_transaction",
            side_effect=confirm_record,
        ):
            status_response = self.client.get(
                f"/api/tickets/scan/{transaction_id}/",
                HTTP_X_SCANNER_TOKEN="demo-scanner-secret",
            )

        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data["result"], ScanLog.Result.ACCEPTED)
        submit_transaction.assert_called_once()

    @override_settings(TICKET_SCANNER_API_TOKEN="demo-scanner-secret")
    def test_invalid_scanner_token_does_not_bypass_organizer_authentication(self):
        qr_token = self.issue_qr_token()
        self.client.force_authenticate(user=None)

        response = self.client.post(
            self.scan_url,
            {"qr_token": qr_token},
            format="json",
            HTTP_X_SCANNER_TOKEN="invalid-secret",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.ticket.scan_logs.count(), 0)

    def test_invalid_qr_token_is_rejected(self):
        self.client.force_authenticate(self.organizer_user)

        response = self.client.post(self.scan_url, {"qr_token": "invalid-token"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch(
        "tickets.views.signing.loads",
        side_effect=signing.SignatureExpired("expired"),
    )
    def test_expired_qr_token_is_rejected(self, _loads):
        token = self.issue_qr_token()
        self.client.force_authenticate(self.organizer_user)

        response = self.client.post(self.scan_url, {"qr_token": token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_410_GONE)


class TicketRefundAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner_user = user_model.objects.create_user(username="refund-owner")
        self.other_user = user_model.objects.create_user(username="not-refund-owner")
        self.owner_wallet = "refund-owner-wallet"
        WalletProfile.objects.create(user=self.owner_user, wallet_address=self.owner_wallet)
        organizer = Organizer.objects.create(name="Organizer")
        self.event = Event.objects.create(
            organizer=organizer,
            title="Refund Event",
            event_date=timezone.now() + timedelta(days=5),
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event,
            name="General",
            price_in_lamports=250_000,
            max_supply=5,
            minted=1,
        )
        self.ticket = Ticket.objects.create(
            event=self.event,
            ticket_type=self.ticket_type,
            owner_wallet=self.owner_wallet,
            name="General #1",
            metadata_uri="https://example.com/ticket.json",
            price_paid_lamports=250_000,
            status=Ticket.Status.ACTIVE,
        )
        Purchase.objects.create(
            event=self.event,
            ticket_type=self.ticket_type,
            ticket=self.ticket,
            buyer_wallet=self.owner_wallet,
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=250_000,
            status=Purchase.Status.COMPLETED,
        )
        self.refund_url = f"/api/tickets/{self.ticket.pk}/refund/"

    def test_only_ticket_owner_can_prepare_refund(self):
        self.client.force_authenticate(self.other_user)

        response = self.client.post(self.refund_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_prepare_refund(self):
        self.client.force_authenticate(self.owner_user)
        prepared = {
            "id": 41,
            "unsigned_transaction": "dGVzdA==",
            "last_valid_block_height": 456,
        }

        with patch(
            "tickets.views.prepare_refund_ticket_transaction",
            return_value=prepared,
        ) as prepare_transaction:
            response = self.client.post(self.refund_url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["chain_transaction_id"], prepared["id"])
        prepare_transaction.assert_called_once_with(self.ticket, self.owner_wallet)

    def test_owned_ticket_list_can_be_filtered_by_event_and_includes_refund_state(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
            status=ChainTransaction.Status.SUBMITTED,
            accounts={"ticket_pk": self.ticket.pk},
        )
        self.client.force_authenticate(self.owner_user)

        response = self.client.get(f"/api/tickets/mine/?event_slug={self.event.slug}")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["event_slug"], self.event.slug)
        self.assertEqual(response.data[0]["owner_wallet"], self.owner_wallet)
        self.assertEqual(response.data[0]["refund_status"], ChainTransaction.Status.SUBMITTED)
        self.assertEqual(response.data[0]["refund_transaction_id"], record.pk)

    def test_owned_ticket_list_exposes_canceled_and_frozen_event_state(self):
        self.event.is_canceled = True
        self.event.is_frozen = False
        self.event.is_active = False
        self.event.save(update_fields=("is_canceled", "is_frozen", "is_active"))
        self.client.force_authenticate(self.owner_user)

        response = self.client.get("/api/tickets/mine/")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertTrue(response.data[0]["event_is_canceled"])
        self.assertFalse(response.data[0]["event_is_frozen"])

    def test_refund_confirmation_is_only_available_to_ticket_owner(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
            event_id_on_chain=1,
            accounts={
                "event_pk": self.event.pk,
                "ticket_pk": self.ticket.pk,
                "owner_wallet": self.owner_wallet,
            },
        )
        self.client.force_authenticate(self.other_user)

        response = self.client.post(
            f"/api/blockchain/transactions/{record.pk}/confirm/",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ticket_owner_can_submit_refund_transaction(self):
        record = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.REFUND_TICKET,
            event_id_on_chain=1,
            accounts={
                "event_pk": self.event.pk,
                "ticket_pk": self.ticket.pk,
                "owner_wallet": self.owner_wallet,
                "expected_message": "dGVzdA==",
            },
        )
        self.client.force_authenticate(self.owner_user)

        with patch(
            "blockchain.views.submit_signed_transaction",
            return_value=record,
        ) as submit_transaction:
            response = self.client.post(
                f"/api/blockchain/transactions/{record.pk}/submit/",
                {"signed_transaction": "dGVzdA=="},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        submit_transaction.assert_called_once()
