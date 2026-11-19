from rest_framework import serializers
from solders.pubkey import Pubkey

from accounts.models import Platform


class InitializePlatformSerializer(serializers.ModelSerializer):
    class Meta:
        model = Platform
        fields = ("admin_wallet", "backend_authority_wallet")

    def validate_admin_wallet(self, value):
        return self._validate_pubkey(value, "Admin wallet")

    def validate_backend_authority_wallet(self, value):
        return self._validate_pubkey(value, "Backend authority wallet")

    @staticmethod
    def _validate_pubkey(value, label):
        try:
            return str(Pubkey.from_string(value))
        except ValueError as exc:
            raise serializers.ValidationError(f"{label} must be a valid Solana public key.") from exc


class SignedTransactionSerializer(serializers.Serializer):
    signed_transaction = serializers.CharField()

    def validate_signed_transaction(self, value):
        if len(value) > 20_000:
            raise serializers.ValidationError("Signed transaction is too large.")
        return value


class ResolveFrozenEventSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=("complete", "cancel"))

    def validate_action(self, value):
        return value == "complete"
