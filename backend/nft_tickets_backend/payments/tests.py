from datetime import timedelta

from django.contrib import admin
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from solders.pubkey import Pubkey
from unittest.mock import patch

from accounts.models import WalletProfile
from events.models import Event, Organizer, TicketType
from tickets.models import Ticket
from .models import Purchase, RefundRequest


class PurchaseModelTests(TestCase):
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

    def test_crypto_purchase_defaults_to_pending(self):
        purchase = Purchase.objects.create(
            event=self.event,
            ticket_type=self.ticket_type,
            buyer_wallet="buyer-wallet",
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=1_000_000,
        )

        self.assertEqual(purchase.status, Purchase.Status.PENDING)
        self.assertEqual(purchase.currency, "SOL")

    def test_refund_request_can_reference_purchase(self):
        purchase = Purchase.objects.create(
            event=self.event,
            ticket_type=self.ticket_type,
            buyer_wallet="buyer-wallet",
            payment_method=Purchase.Method.CRYPTO,
            amount_lamports=1_000_000,
        )
        refund = RefundRequest.objects.create(
            purchase=purchase,
            requester_wallet="buyer-wallet",
            amount_lamports=1_000_000,
        )

        self.assertEqual(refund.status, RefundRequest.Status.PENDING)
        self.assertEqual(purchase.refunds.get(), refund)

    def test_models_are_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(Purchase))
        self.assertTrue(admin.site.is_registered(RefundRequest))


class CryptoPurchaseAPITests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="buyer")
        self.buyer_wallet = str(Pubkey.new_unique())
        WalletProfile.objects.create(
            user=self.user,
            wallet_address=self.buyer_wallet,
        )
        organizer = Organizer.objects.create(
            name="Organizer",
            email="organizer@example.com",
        )
        self.event = Event.objects.create(
            organizer=organizer,
            title="Live Event",
            event_date=timezone.now() + timedelta(days=5),
            on_chain_event_id=42,
            event_pda=str(Pubkey.new_unique()),
            collection_address=str(Pubkey.new_unique()),
            is_active=True,
        )
        self.ticket_type = TicketType.objects.create(
            event=self.event,
            name="VIP",
            on_chain_ticket_type_id=3,
            price_in_lamports=500_000,
            max_supply=20,
            is_active=True,
            metadata_uri="https://gateway.example/vip.json",
        )
        self.url = (
            f"/api/payments/events/{self.event.slug}/"
            f"ticket-types/{self.ticket_type.pk}/purchase/"
        )
        self.payload = {
            "buyer_wallet": self.buyer_wallet,
            "asset_address": str(Pubkey.new_unique()),
            "metadata_uri": "https://example.com/client-selected.json",
        }

    def test_anonymous_user_cannot_purchase(self):
        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 401)

    def test_wallet_linked_to_authenticated_user_does_not_need_separate_verification(self):
        self.client.force_authenticate(self.user)

        with patch("payments.views.prepare_buy_ticket_crypto_transaction") as prepare_transaction:
            prepare_transaction.return_value = {
                "price_in_lamports": self.ticket_type.price_in_lamports,
            }
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)

    @patch("payments.views.prepare_buy_ticket_crypto_transaction")
    def test_verified_buyer_gets_unsigned_purchase_transaction(self, prepare_transaction):
        self.client.force_authenticate(self.user)
        prepare_transaction.return_value = {
            "purchase_id": 8,
            "ticket_id": 9,
            "chain_transaction_id": 10,
            "unsigned_transaction": "unsigned-base64",
            "last_valid_block_height": 100,
            "ticket_asset_address": self.payload["asset_address"],
            "ticket_record_pda": str(Pubkey.new_unique()),
            "price_in_lamports": self.ticket_type.price_in_lamports,
            "ticket_name": "VIP #1",
            "metadata_uri": self.ticket_type.metadata_uri,
        }

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["price_in_lamports"], 500_000)
        prepare_transaction.assert_called_once()
        self.assertEqual(
            prepare_transaction.call_args.kwargs["metadata_uri"],
            self.ticket_type.metadata_uri,
        )

    def test_purchase_uses_ticket_type_metadata_uri(self):
        self.client.force_authenticate(self.user)
        self.ticket_type.metadata_uri = "https://gateway.example/official-ticket.json"
        self.ticket_type.save(update_fields=("metadata_uri",))

        with patch("payments.views.prepare_buy_ticket_crypto_transaction") as prepare_transaction:
            prepare_transaction.return_value = {"price_in_lamports": self.ticket_type.price_in_lamports}
            response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            prepare_transaction.call_args.kwargs["metadata_uri"],
            "https://gateway.example/official-ticket.json",
        )

    def test_client_cannot_choose_price(self):
        self.client.force_authenticate(self.user)
        payload = {**self.payload, "amount_lamports": 1}

        with patch("payments.views.prepare_buy_ticket_crypto_transaction") as prepare_transaction:
            prepare_transaction.return_value = {
                "price_in_lamports": self.ticket_type.price_in_lamports,
            }
            response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            prepare_transaction.call_args.kwargs["event_slug"],
            self.event.slug,
        )
        self.assertNotIn("amount_lamports", prepare_transaction.call_args.kwargs)
