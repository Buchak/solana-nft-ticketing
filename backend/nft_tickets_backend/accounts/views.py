import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from solders.pubkey import Pubkey
from solders.signature import Signature

from .models import WalletLoginChallenge, WalletProfile
from .serializers import WalletChallengeSerializer, WalletLoginSerializer


CHALLENGE_VALIDITY_SECONDS = 300


class WalletNonceView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        serializer = WalletChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet_address = serializer.validated_data["wallet_address"]
        nonce = secrets.token_urlsafe(32)
        issued_at = timezone.now()
        expires_at = issued_at + timedelta(seconds=CHALLENGE_VALIDITY_SECONDS)
        WalletLoginChallenge.objects.filter(
            wallet_address=wallet_address,
            expires_at__lte=issued_at,
        ).delete()
        message = "\n".join(
            (
                "Sign in to NFT Tickets",
                f"Wallet: {wallet_address}",
                f"Nonce: {nonce}",
                f"Issued At: {issued_at.isoformat()}",
                f"Expiration Time: {expires_at.isoformat()}",
            )
        )
        WalletLoginChallenge.objects.create(
            wallet_address=wallet_address,
            nonce=nonce,
            message=message,
            expires_at=expires_at,
        )
        return Response(
            {
                "wallet_address": wallet_address,
                "nonce": nonce,
                "message": message,
                "expires_at": expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class WalletLoginView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()

    def post(self, request):
        serializer = WalletLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet_address = serializer.validated_data["wallet_address"]

        try:
            signature = Signature.from_string(serializer.validated_data["signature"])
        except ValueError as exc:
            raise ValidationError({"signature": "Signature must be valid base58."}) from exc

        try:
            with transaction.atomic():
                challenge = (
                    WalletLoginChallenge.objects.select_for_update()
                    .get(
                        wallet_address=wallet_address,
                        nonce=serializer.validated_data["nonce"],
                    )
                )
                if challenge.used_at is not None:
                    raise AuthenticationFailed("This login challenge has already been used.")
                if challenge.expires_at <= timezone.now():
                    raise AuthenticationFailed("This login challenge has expired.")

                try:
                    valid_signature = signature.verify(
                        Pubkey.from_string(wallet_address),
                        challenge.message.encode("utf-8"),
                    )
                except ValueError as exc:
                    raise ValidationError({"signature": "Signature is invalid."}) from exc
                if not valid_signature:
                    raise AuthenticationFailed("Wallet signature is invalid.")

                challenge.used_at = timezone.now()
                challenge.save(update_fields=("used_at",))

                profile = (
                    WalletProfile.objects.select_for_update()
                    .filter(wallet_address=wallet_address)
                    .first()
                )
                user_model = get_user_model()
                if profile is not None and profile.user_id:
                    user = profile.user
                else:
                    username = f"solana_{wallet_address}_{secrets.token_hex(4)}"
                    user = user_model.objects.create_user(
                        username=username,
                        password=None,
                    )
                    if profile is None:
                        profile = WalletProfile(wallet_address=wallet_address, user=user)
                    profile.user = user
                    profile.save()

                Token.objects.filter(user=user).delete()
                token = Token.objects.create(user=user)
        except WalletLoginChallenge.DoesNotExist as exc:
            raise AuthenticationFailed("Login challenge not found for this wallet.") from exc

        return Response(
            {
                "token": token.key,
                "token_type": "Token",
                "wallet_address": wallet_address,
                "user_id": user.pk,
            }
        )


class WalletLogoutView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        if isinstance(request.auth, Token):
            request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WalletMeView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        wallets = WalletProfile.objects.filter(user=request.user).values_list(
            "wallet_address",
            flat=True,
        )
        return Response(
            {
                "user_id": request.user.pk,
                "wallet_addresses": list(wallets),
            }
        )
