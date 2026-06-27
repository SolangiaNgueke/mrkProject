from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Profil renvoyé au client (jamais le mot de passe)."""

    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "role_display", "phone", "kyc_verified"]
        # Le rôle et le KYC ne sont PAS modifiables par l'utilisateur lui-même :
        # ils sont attribués par un administrateur dans le back-office.
        read_only_fields = ["role", "kyc_verified"]


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "phone"]

    def create(self, validated_data):
        # Toute inscription crée un CITOYEN. Les rôles géomètre / notaire / admin
        # sont attribués ensuite par un administrateur (sécurité).
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
            phone=validated_data.get("phone", ""),
        )