import json
from io import BytesIO
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase
from unittest.mock import Mock, patch

from accounts.models import WalletProfile
from blockchain.models import ChainTransaction
from tickets.models import Ticket
from .models import Event, EventDispute, Organizer, TicketType
from .storage import pin_event_media


def create_test_image():
    image_buffer = BytesIO()
    Image.new("RGB", (4, 4), color="purple").save(image_buffer, format="PNG")
    return SimpleUploadedFile(
        "event.png",
        image_buffer.getvalue(),
        content_type="image/png",
    )


class EventModelTests(TestCase):
    def setUp(self):
        self.wallet = WalletProfile.objects.create(wallet_address="organizer-wallet")
        self.organizer = Organizer.objects.create(
            wallet=self.wallet,
            name="Organizer",
            email="organizer@example.com",
        )

    def create_event(self, **kwargs):
        values = {
            "organizer": self.organizer,
            "title": "Summer Festival",
            "event_date": timezone.now() + timedelta(days=30),
        }
        values.update(kwargs)
        return Event.objects.create(**values)

    def test_event_generates_unique_slug(self):
        event = self.create_event()
        second_event = self.create_event()

        self.assertEqual(event.slug, "summer-festival")
        self.assertEqual(second_event.slug, "summer-festival-2")
        self.assertEqual(str(event), "Summer Festival")

    def test_event_rejects_more_scans_than_tickets_sold(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_event(total_ticket_scanned=1, total_tickets_sold=0)

    def test_ticket_type_enforces_price_and_supply(self):
        event = self.create_event()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TicketType.objects.create(
                event=event,
                name="Free",
                price_in_lamports=0,
                max_supply=10,
            )

    def test_ticket_type_minted_cannot_exceed_supply(self):
        event = self.create_event()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TicketType.objects.create(
                event=event,
                name="VIP",
                price_in_lamports=100,
                max_supply=1,
                minted=2,
            )

    def test_dispute_defaults_to_valid_for_demonstration(self):
        event = self.create_event()
        dispute = EventDispute.objects.create(
            event=event,
            reported_by_wallet="attendee-wallet",
            reason="Ticket was not accepted at the door.",
        )

        self.assertEqual(dispute.status, EventDispute.Status.VALID)

    def test_models_are_registered_in_admin(self):
        for model in (Organizer, Event, TicketType, EventDispute):
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))


class PinataEventStorageTests(TestCase):
    @override_settings(
        PINATA_JWT="test-pinata-token",
        PINATA_GATEWAY_URL="https://gateway.example",
    )
    @patch("events.storage.httpx.post")
    def test_pins_image_and_metadata_and_returns_gateway_uris(self, post):
        image_response = Mock()
        image_response.json.return_value = {"IpfsHash": "poster-cid"}
        metadata_response = Mock()
        metadata_response.json.return_value = {"IpfsHash": "metadata-cid"}
        post.side_effect = (image_response, metadata_response)
        image = create_test_image()

        image_uri, metadata_uri = pin_event_media(
            image,
            "Summer Festival",
            "Live music",
        )

        self.assertEqual(image_uri, "https://gateway.example/ipfs/poster-cid")
        self.assertEqual(metadata_uri, "https://gateway.example/ipfs/metadata-cid")
        self.assertEqual(post.call_count, 2)
        metadata = post.call_args.kwargs["content"]
        self.assertEqual(json.loads(metadata)["image"], image_uri)
        self.assertEqual(json.loads(metadata)["name"], "Summer Festival")


class EventAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organizer_user = user_model.objects.create_user(username="organizer")
        self.other_user = user_model.objects.create_user(username="other")
        self.attendee_user = user_model.objects.create_user(username="attendee")
        wallet = WalletProfile.objects.create(
            user=self.organizer_user,
            wallet_address="organizer-api-wallet",
        )
        self.organizer = Organizer.objects.create(
            user=self.organizer_user,
            wallet=wallet,
            name="API Organizer",
            email="api-organizer@example.com",
        )
        self.event = Event.objects.create(
            organizer=self.organizer,
            title="API Festival",
            event_date=timezone.now() + timedelta(days=30),
            metadata_uri="https://example.com/event.json",
        )
        self.event_url = f"/api/events/{self.event.slug}/"
        self.ticket_types_url = f"{self.event_url}ticket-types/"
        self.attendee_wallet = WalletProfile.objects.create(
            user=self.attendee_user,
            wallet_address="attendee-api-wallet",
        )

    def create_disputable_ticket(self, **overrides):
        ticket_type = TicketType.objects.create(
            event=self.event,
            name="General Admission",
            price_in_lamports=1_000_000,
            max_supply=5,
            minted=1,
        )
        values = {
            "event": self.event,
            "ticket_type": ticket_type,
            "owner_wallet": self.attendee_wallet.wallet_address,
            "name": "General #1",
            "metadata_uri": "https://example.com/ticket.json",
            "price_paid_lamports": 1_000_000,
            "status": Ticket.Status.ACTIVE,
        }
        values.update(overrides)
        return Ticket.objects.create(**values)

    def test_ticket_holder_can_submit_valid_dispute_after_event(self):
        self.event.event_date = timezone.now() - timedelta(hours=2)
        self.event.is_active = True
        self.event.save(update_fields=("event_date", "is_active"))
        ticket = self.create_disputable_ticket()
        self.client.force_authenticate(self.attendee_user)

        response = self.client.post(
            f"/api/events/{self.event.slug}/disputes/",
            {"ticket_id": ticket.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], EventDispute.Status.VALID)
        dispute = EventDispute.objects.get(ticket=ticket)
        self.assertEqual(dispute.event, self.event)
        self.assertEqual(dispute.reported_by_wallet, ticket.owner_wallet)
        self.assertEqual(
            EventDispute.objects.filter(
                event=self.event,
                status=EventDispute.Status.VALID,
            ).count(),
            1,
        )

    def test_organizer_cannot_submit_event_dispute(self):
        self.event.event_date = timezone.now() - timedelta(hours=2)
        self.event.is_active = True
        self.event.save(update_fields=("event_date", "is_active"))
        ticket = self.create_disputable_ticket(
            owner_wallet=self.organizer.wallet.wallet_address,
        )
        self.client.force_authenticate(self.organizer_user)

        response = self.client.post(
            f"/api/events/{self.event.slug}/disputes/",
            {"ticket_id": ticket.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(EventDispute.objects.exists())

    def test_only_ticket_owner_can_submit_dispute(self):
        self.event.event_date = timezone.now() - timedelta(hours=2)
        self.event.is_active = True
        self.event.save(update_fields=("event_date", "is_active"))
        ticket = self.create_disputable_ticket()
        self.client.force_authenticate(self.other_user)

        response = self.client.post(
            f"/api/events/{self.event.slug}/disputes/",
            {"ticket_id": ticket.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(EventDispute.objects.exists())

    def test_used_ticket_cannot_submit_dispute(self):
        self.event.event_date = timezone.now() - timedelta(hours=2)
        self.event.is_active = True
        self.event.save(update_fields=("event_date", "is_active"))
        ticket = self.create_disputable_ticket(status=Ticket.Status.USED)
        self.client.force_authenticate(self.attendee_user)

        response = self.client.post(
            f"/api/events/{self.event.slug}/disputes/",
            {"ticket_id": ticket.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(EventDispute.objects.exists())

    def test_ticket_with_pending_scan_cannot_submit_dispute(self):
        self.event.event_date = timezone.now() - timedelta(hours=2)
        self.event.is_active = True
        self.event.save(update_fields=("event_date", "is_active"))
        ticket = self.create_disputable_ticket()
        ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CONSUME_TICKET,
            status=ChainTransaction.Status.SUBMITTED,
            signature="pending-ticket-scan",
            event_id_on_chain=1,
            accounts={"ticket_pk": ticket.pk},
        )
        self.client.force_authenticate(self.attendee_user)

        response = self.client.post(
            f"/api/events/{self.event.slug}/disputes/",
            {"ticket_id": ticket.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(EventDispute.objects.exists())

    def test_ticket_can_only_submit_one_dispute(self):
        self.event.event_date = timezone.now() - timedelta(hours=2)
        self.event.is_active = True
        self.event.save(update_fields=("event_date", "is_active"))
        ticket = self.create_disputable_ticket()
        self.client.force_authenticate(self.attendee_user)
        url = f"/api/events/{self.event.slug}/disputes/"

        first_response = self.client.post(url, {"ticket_id": ticket.pk}, format="json")
        second_response = self.client.post(url, {"ticket_id": ticket.pk}, format="json")

        self.assertEqual(first_response.status_code, 201, first_response.data)
        self.assertEqual(second_response.status_code, 400)
        self.assertEqual(EventDispute.objects.filter(ticket=ticket).count(), 1)

    def test_dispute_is_not_available_before_event_starts(self):
        ticket = self.create_disputable_ticket()
        self.client.force_authenticate(self.attendee_user)

        response = self.client.post(
            f"/api/events/{self.event.slug}/disputes/",
            {"ticket_id": ticket.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(EventDispute.objects.exists())

    def test_organizer_can_list_all_owned_events_including_inactive(self):
        inactive_event = Event.objects.create(
            organizer=self.organizer,
            title="Pending Festival",
            event_date=timezone.now() + timedelta(days=60),
        )
        other_organizer = Organizer.objects.create(
            user=self.other_user,
            name="Other Organizer",
            email="other@example.com",
        )
        Event.objects.create(
            organizer=other_organizer,
            title="Other Festival",
            event_date=timezone.now() + timedelta(days=90),
        )
        self.client.force_authenticate(self.organizer_user)

        response = self.client.get("/api/events/mine/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({event["slug"] for event in response.data}, {self.event.slug, inactive_event.slug})
        pending = next(event for event in response.data if event["slug"] == inactive_event.slug)
        self.assertFalse(pending["is_active"])

    def test_organizer_event_list_requires_authentication(self):
        response = self.client.get("/api/events/mine/")

        self.assertEqual(response.status_code, 401)

    def test_only_event_organizer_can_read_event_account_balance(self):
        response = self.client.get(f"/api/events/{self.event.slug}/balance/")
        self.assertEqual(response.status_code, 401)

        self.client.force_authenticate(self.other_user)
        response = self.client.get(f"/api/events/{self.event.slug}/balance/")
        self.assertEqual(response.status_code, 404)

    def test_event_organizer_can_read_event_account_balance(self):
        balance = {
            "event_pda": "event-account-pda",
            "balance_lamports": "2000000000",
            "rent_exempt_lamports": "890880",
            "withdrawable_lamports": "1999109120",
        }
        self.client.force_authenticate(self.organizer_user)

        with patch("events.views.get_event_account_balance", return_value=balance):
            response = self.client.get(f"/api/events/{self.event.slug}/balance/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, balance)

    def test_event_detail_exposes_scheduled_finalization_time(self):
        self.event.is_active = True
        self.event.save(update_fields=("is_active",))
        self.client.force_authenticate(self.organizer_user)

        response = self.client.get(self.event_url)

        self.assertEqual(response.status_code, 200, response.data)
        expected = self.event.event_date + timedelta(hours=24)
        self.assertEqual(
            response.data["finalization_scheduled_for"],
            expected,
        )

    def test_ticket_owner_can_open_canceled_event_and_ticket_types(self):
        self.event.is_active = False
        self.event.is_canceled = True
        self.event.save(update_fields=("is_active", "is_canceled"))
        ticket = self.create_disputable_ticket()
        self.client.force_authenticate(self.attendee_user)

        event_response = self.client.get(self.event_url)
        ticket_types_response = self.client.get(self.ticket_types_url)
        events_response = self.client.get("/api/events/")

        self.assertEqual(event_response.status_code, 200, event_response.data)
        self.assertTrue(event_response.data["is_canceled"])
        self.assertEqual(ticket_types_response.status_code, 200, ticket_types_response.data)
        self.assertEqual(len(ticket_types_response.data), 1)
        self.assertIn(self.event.slug, {item["slug"] for item in events_response.data})
        self.assertEqual(ticket.event, self.event)

    def test_non_ticket_holder_cannot_open_canceled_event(self):
        self.event.is_active = False
        self.event.is_canceled = True
        self.event.save(update_fields=("is_active", "is_canceled"))
        self.create_disputable_ticket()
        self.client.force_authenticate(self.other_user)

        response = self.client.get(self.event_url)

        self.assertEqual(response.status_code, 404)

    def test_organizer_event_list_includes_latest_withdrawal_status(self):
        withdrawal = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.WITHDRAW_FUNDS,
            status=ChainTransaction.Status.SUBMITTED,
            event_id_on_chain=77,
            accounts={"event_pk": self.event.pk},
        )
        self.client.force_authenticate(self.organizer_user)

        response = self.client.get("/api/events/mine/")

        self.assertEqual(response.status_code, 200, response.data)
        event_data = next(event for event in response.data if event["id"] == self.event.pk)
        self.assertEqual(event_data["withdrawal_status"], ChainTransaction.Status.SUBMITTED)
        self.assertEqual(event_data["withdrawal_transaction_id"], withdrawal.pk)

    def test_organizer_event_list_includes_latest_cancellation_status(self):
        cancellation = ChainTransaction.objects.create(
            instruction=ChainTransaction.Instruction.CANCEL_EVENT,
            status=ChainTransaction.Status.SUBMITTED,
            event_id_on_chain=77,
            accounts={"event_pk": self.event.pk},
        )
        self.client.force_authenticate(self.organizer_user)

        response = self.client.get("/api/events/mine/")

        self.assertEqual(response.status_code, 200, response.data)
        event_data = next(event for event in response.data if event["id"] == self.event.pk)
        self.assertEqual(event_data["cancellation_status"], ChainTransaction.Status.SUBMITTED)
        self.assertEqual(event_data["cancellation_transaction_id"], cancellation.pk)

    def test_anonymous_user_cannot_create_event(self):
        response = self.client.post(
            "/api/events/",
            {
                "title": "New Festival",
                "event_date": (timezone.now() + timedelta(days=30)).isoformat(),
                "metadata_uri": "https://example.com/new-event.json",
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_user_without_organizer_profile_cannot_create_event(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.post(
            "/api/events/",
            {
                "title": "New Festival",
                "event_date": (timezone.now() + timedelta(days=30)).isoformat(),
                "metadata_uri": "https://example.com/new-event.json",
            },
        )

        self.assertEqual(response.status_code, 403)

    @patch(
        "events.views.pin_event_media",
        return_value=(
            "https://gateway.pinata.cloud/ipfs/imagecid",
            "https://gateway.pinata.cloud/ipfs/metadatacid",
        ),
    )
    @patch("events.views.prepare_create_event_transaction")
    def test_authenticated_wallet_can_create_organizer_profile_and_event(
        self,
        prepare_transaction,
        pin_media,
    ):
        wallet = WalletProfile.objects.create(
            user=self.other_user,
            wallet_address="new-phantom-wallet",
        )
        prepare_transaction.return_value = {
            "id": 52,
            "unsigned_transaction": "unsigned-data",
            "last_valid_block_height": 100,
            "event_pda": "event-pda",
            "collection_address": "11111111111111111111111111111111",
        }
        self.client.force_authenticate(self.other_user)

        response = self.client.post(
            "/api/events/",
            {
                "title": "Wallet Created Event",
                "event_date": (timezone.now() + timedelta(days=30)).isoformat(),
                "image": create_test_image(),
                "collection_address": "11111111111111111111111111111111",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.data)
        event = Event.objects.get(slug=response.data["event"]["slug"])
        self.assertEqual(event.organizer.user, self.other_user)
        self.assertEqual(event.organizer.wallet, wallet)
        self.assertEqual(event.image_uri, "https://gateway.pinata.cloud/ipfs/imagecid")
        self.assertEqual(event.metadata_uri, "https://gateway.pinata.cloud/ipfs/metadatacid")
        pin_media.assert_called_once()

    @patch(
        "events.views.pin_event_media",
        return_value=(
            "https://gateway.pinata.cloud/ipfs/imagecid",
            "https://gateway.pinata.cloud/ipfs/metadatacid",
        ),
    )
    @patch("events.views.prepare_create_event_transaction")
    def test_organizer_can_prepare_event_creation(self, prepare_transaction, pin_media):
        prepare_transaction.return_value = {
            "id": 42,
            "unsigned_transaction": "unsigned-data",
            "last_valid_block_height": 100,
            "event_pda": "event-pda",
            "collection_address": "11111111111111111111111111111111",
        }
        self.client.force_authenticate(self.organizer_user)
        response = self.client.post(
            "/api/events/",
            {
                "title": "New Festival",
                "description": "A new event",
                "event_date": (timezone.now() + timedelta(days=30)).isoformat(),
                "image": create_test_image(),
                "collection_address": "11111111111111111111111111111111",
                "is_active": True,
                "organizer": self.organizer.pk + 100,
                "on_chain_event_id": 99,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.data)
        event = Event.objects.get(slug=response.data["event"]["slug"])
        self.assertEqual(event.organizer, self.organizer)
        self.assertFalse(event.is_active)
        self.assertIsNone(event.on_chain_event_id)
        self.assertEqual(response.data["blockchain_transaction"]["id"], 42)
        prepare_transaction.assert_called_once()
        pin_media.assert_called_once()

    def test_event_creation_requires_an_image(self):
        self.client.force_authenticate(self.organizer_user)
        response = self.client.post(
            "/api/events/",
            {
                "title": "Festival Without Poster",
                "event_date": (timezone.now() + timedelta(days=30)).isoformat(),
                "collection_address": "11111111111111111111111111111111",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("image", response.data)

    def test_organizer_can_patch_description_only(self):
        self.client.force_authenticate(self.organizer_user)
        response = self.client.patch(
            self.event_url,
            {"description": "Updated", "title": "Changed", "is_active": True},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, "Updated")
        self.assertEqual(self.event.title, "API Festival")
        self.assertFalse(self.event.is_active)

    def test_other_user_cannot_patch_event(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.patch(self.event_url, {"description": "Unauthorized"})

        self.assertEqual(response.status_code, 404)
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, "")

    def test_event_cannot_be_deleted_or_replaced(self):
        self.client.force_authenticate(self.organizer_user)

        delete_response = self.client.delete(self.event_url)
        put_response = self.client.put(self.event_url, {"description": "Replace"})

        self.assertEqual(delete_response.status_code, 405)
        self.assertEqual(put_response.status_code, 405)

    @patch(
        "events.views.pin_ticket_type_media",
        return_value=(
            "https://gateway.pinata.cloud/ipfs/ticket-image",
            "https://gateway.pinata.cloud/ipfs/ticket-metadata",
        ),
    )
    @patch("events.views.prepare_add_ticket_type_transaction")
    def test_organizer_can_prepare_ticket_type_creation(self, prepare_transaction, pin_media):
        prepare_transaction.return_value = {
            "id": 50,
            "unsigned_transaction": "unsigned-data",
            "last_valid_block_height": 100,
            "ticket_type_pda": "ticket-type-pda",
        }
        self.event.is_active = True
        self.event.event_pda = "11111111111111111111111111111111"
        self.event.on_chain_event_id = self.event.pk
        self.event.save(update_fields=("is_active", "event_pda", "on_chain_event_id"))
        self.client.force_authenticate(self.organizer_user)
        response = self.client.post(
            self.ticket_types_url,
            {
                "name": "VIP",
                "description": "Front row",
                "price_in_lamports": 1_000_000,
                "max_supply": 50,
                "is_active": True,
                "minted": 50,
                "image": create_test_image(),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.data)
        ticket_type = TicketType.objects.get(pk=response.data["ticket_type"]["id"])
        self.assertEqual(ticket_type.event, self.event)
        self.assertFalse(ticket_type.is_active)
        self.assertEqual(ticket_type.minted, 0)
        self.assertEqual(ticket_type.image_uri, "https://gateway.pinata.cloud/ipfs/ticket-image")
        self.assertEqual(ticket_type.metadata_uri, "https://gateway.pinata.cloud/ipfs/ticket-metadata")
        self.assertEqual(response.data["blockchain_transaction"]["id"], 50)
        prepare_transaction.assert_called_once()
        pin_media.assert_called_once()

    def test_ticket_type_requires_nft_image(self):
        self.event.is_active = True
        self.event.event_pda = "11111111111111111111111111111111"
        self.event.on_chain_event_id = self.event.pk
        self.event.save(update_fields=("is_active", "event_pda", "on_chain_event_id"))
        self.client.force_authenticate(self.organizer_user)

        response = self.client.post(
            self.ticket_types_url,
            {
                "name": "VIP",
                "price_in_lamports": 1_000_000,
                "max_supply": 50,
            },
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("image", response.data)

    def test_other_user_cannot_create_ticket_type(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.post(
            self.ticket_types_url,
            {
                "name": "VIP",
                "price_in_lamports": 1_000_000,
                "max_supply": 50,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.event.ticket_types.count(), 0)

    def test_ticket_type_requires_positive_price_and_supply(self):
        self.event.is_active = True
        self.event.event_pda = "11111111111111111111111111111111"
        self.event.on_chain_event_id = self.event.pk
        self.event.save(update_fields=("is_active", "event_pda", "on_chain_event_id"))
        self.client.force_authenticate(self.organizer_user)
        response = self.client.post(
            self.ticket_types_url,
            {
                "name": "VIP",
                "price_in_lamports": 0,
                "max_supply": 0,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.event.ticket_types.count(), 0)
