"""Vérification de l'adresse email à l'inscription.

Le lien de confirmation contient un jeton SIGNÉ et HORODATÉ (django.core.signing) :
aucun stockage supplémentaire n'est nécessaire, et le lien expire de lui-même.
Un jeton falsifié est rejeté car la signature ne correspondrait pas.
"""

import os

from django.conf import settings
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

SIGNER = TimestampSigner(salt="verification-email")

# Durée de validité du lien (48 h par défaut).
DUREE_VALIDITE = 60 * 60 * 48


def creer_jeton(user):
    return SIGNER.sign(str(user.pk))


def lire_jeton(token):
    """Retourne l'identifiant utilisateur, ou lève BadSignature/SignatureExpired."""
    return int(SIGNER.unsign(token, max_age=DUREE_VALIDITE))


def _base_api():
    """Adresse publique de l'API (configurable pour la production)."""
    return os.environ.get("PUBLIC_API_URL", "http://localhost:8000").rstrip("/")


def envoyer_verification(user):
    """Envoie le lien de confirmation. Sans effet si l'email est absent ou déjà vérifié."""
    if not user.email or user.email_verified:
        return
    lien = f"{_base_api()}/api/auth/verify-email/?token={creer_jeton(user)}"
    sujet = "[Foncier] Confirmez votre adresse email"
    corps = (
        f"Bonjour {user.username},\n\n"
        f"Confirmez votre adresse email en ouvrant ce lien :\n{lien}\n\n"
        f"Ce lien est valable 48 heures.\n"
        f"Si vous n'êtes pas à l'origine de cette inscription, ignorez ce message.\n\n"
        f"— Plateforme foncière"
    )
    send_mail(sujet, corps, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)