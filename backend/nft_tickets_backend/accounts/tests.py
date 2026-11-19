from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APITestCase
from rest_framework.authtoken.models import Token
from solders.keypair import Keypair

from .models import Platform, WalletLoginChallenge, WalletProfile


class PlatformModelTests(TestCase):
    def test_platform_configuration_can_be_created(self):
        platform = Platform.objects.create(
            admin_wallet="admin-wallet",
            backend_authority_wallet="backend-wallet",
            is_initialized=True,
        )

        self.assertEqual(platform.pk, 1)
        self.assertTrue(platform.is_initialized)
        self.assertIn("Platform configuration", str(platform))


class WalletProfileModelTests(TestCase):
    def test_wallet_can_be_linked_to_user(self):
        user = get_user_model().objects.create_user(username="alice")
        wallet = WalletProfile.objects.create(
            user=user,
            wallet_address="wallet-alice",
        )

        self.assertEqual(user.wallet_profiles.get(), wallet)
        self.assertEqual(str(wallet), "wallet-alice")

    def test_wallet_address_must_be_unique(self):
        WalletProfile.objects.create(wallet_address="duplicate-wallet")

        with self.assertRaises(IntegrityError), transaction.atomic():
            WalletProfile.objects.create(wallet_address="duplicate-wallet")

    def test_models_are_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(Platform))
        self.assertTrue(admin.site.is_registered(WalletProfile))


class PhantomWalletAuthenticationTests(APITestCase):
    nonce_url = "/api/auth/nonce/"
    verify_url = "/api/auth/verify/"

    def setUp(self):
        self.wallet = Keypair()
        self.wallet_address = str(self.wallet.pubkey())

    def create_challenge(self):
        response = self.client.post(
            self.nonce_url,
            {"wallet_address": self.wallet_address},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def sign_challenge(self, challenge):
        signature = self.wallet.sign_message(challenge["message"].encode("utf-8"))
        return {
            "wallet_address": self.wallet_address,
            "nonce": challenge["nonce"],
            "signature": str(signature),
        }

    def test_wallet_signature_creates_account_and_returns_token(self):
        challenge = self.create_challenge()

        response = self.client.post(
            self.verify_url,
            self.sign_challenge(challenge),
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["token_type"], "Token")
        user = get_user_model().objects.get(pk=response.data["user_id"])
        self.assertEqual(user.wallet_profiles.get().wallet_address, self.wallet_address)
        self.assertEqual(Token.objects.get(user=user).key, response.data["token"])
        self.assertIsNotNone(
            WalletLoginChallenge.objects.get(nonce=challenge["nonce"]).used_at
        )

    def test_login_token_authenticates_user_and_logout_revokes_token(self):
        challenge = self.create_challenge()
        login = self.client.post(
            self.verify_url,
            self.sign_challenge(challenge),
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")

        profile_response = self.client.get("/api/auth/me/")
        logout_response = self.client.post("/api/auth/logout/")
        protected_response = self.client.get("/api/auth/me/")

        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(profile_response.data["wallet_addresses"], [self.wallet_address])
        self.assertEqual(logout_response.status_code, 204)
        self.assertEqual(protected_response.status_code, 401)

    def test_login_challenge_cannot_be_reused(self):
        challenge = self.create_challenge()
        payload = self.sign_challenge(challenge)
        first_response = self.client.post(self.verify_url, payload, format="json")
        second_response = self.client.post(self.verify_url, payload, format="json")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 403)

    def test_signature_from_different_wallet_is_rejected(self):
        challenge = self.create_challenge()
        wrong_wallet = Keypair()
        payload = self.sign_challenge(challenge)
        payload["signature"] = str(
            wrong_wallet.sign_message(challenge["message"].encode("utf-8"))
        )

        response = self.client.post(self.verify_url, payload, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            WalletProfile.objects.filter(wallet_address=self.wallet_address).exists()
        )

    def test_expired_challenge_is_rejected(self):
        challenge = self.create_challenge()
        WalletLoginChallenge.objects.filter(nonce=challenge["nonce"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.client.post(
            self.verify_url,
            self.sign_challenge(challenge),
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            WalletProfile.objects.filter(wallet_address=self.wallet_address).exists()
        )
