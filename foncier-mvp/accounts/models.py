from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Utilisateur avec rôle. Sert de base au contrôle d'accès (RBAC)."""

    class Role(models.TextChoices):
        CITIZEN = "citizen", "Citoyen / propriétaire"
        SURVEYOR = "surveyor", "Géomètre"
        NOTARY = "notary", "Notaire / agent de cadastre"
        ADMIN = "admin", "Administrateur"

    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.CITIZEN
    )
    phone = models.CharField(max_length=30, blank=True)
    kyc_verified = models.BooleanField(default=False)
    # L'adresse email a-t-elle été confirmée via le lien envoyé ?
    email_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"