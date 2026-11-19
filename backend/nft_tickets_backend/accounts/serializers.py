from rest_framework import serializers
from solders.pubkey import Pubkey


class WalletChallengeSerializer(serializers.Serializer):
    wallet_address = serializers.CharField(max_length=128)

    def validate_wallet_address(self, value):
        try:
            return str(Pubkey.from_string(value))
        except ValueError as exc:
            raise serializers.ValidationError(
                "A valid Solana wallet address is required."
            ) from exc


class WalletLoginSerializer(WalletChallengeSerializer):
    nonce = serializers.CharField(max_length=64)
    signature = serializers.CharField(max_length=128)
