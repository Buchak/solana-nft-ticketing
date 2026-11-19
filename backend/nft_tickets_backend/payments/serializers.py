from rest_framework import serializers
from solders.pubkey import Pubkey

from accounts.models import WalletProfile


class CryptoPurchaseSerializer(serializers.Serializer):
    buyer_wallet = serializers.CharField(max_length=128)
    asset_address = serializers.CharField(max_length=128)
    metadata_uri = serializers.URLField(max_length=500, required=False)

    def validate_buyer_wallet(self, value):
        return self._validate_public_key(value, "Buyer wallet")

    def validate_asset_address(self, value):
        return self._validate_public_key(value, "Ticket asset")

    def validate_metadata_uri(self, value):
        if len(value.encode("utf-8")) > 200:
            raise serializers.ValidationError(
                "Metadata URI must be at most 200 UTF-8 bytes to match the on-chain limit."
            )
        return value

    def validate(self, attrs):
        if attrs["buyer_wallet"] == attrs["asset_address"]:
            raise serializers.ValidationError(
                {"asset_address": "Generate a new keypair for the ticket asset."}
            )
        user = self.context["request"].user
        wallet_exists = WalletProfile.objects.filter(
            user=user,
            wallet_address=attrs["buyer_wallet"],
        ).exists()
        if not wallet_exists:
            raise serializers.ValidationError({"buyer_wallet": "This wallet is not linked to your account."})
        return attrs

    @staticmethod
    def _validate_public_key(value, label):
        try:
            return str(Pubkey.from_string(value))
        except ValueError as exc:
            raise serializers.ValidationError(
                f"{label} must be a valid Solana public key."
            ) from exc
