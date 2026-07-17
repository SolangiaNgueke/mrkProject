"""Notifications email au propriétaire d'une parcelle.

Le mode d'envoi (console en dev, SMTP en prod) est défini dans settings.py :
ce module n'a pas à changer quand on passe aux vrais emails.
Les envois sont silencieux en cas d'erreur (fail_silently) pour ne jamais
bloquer le workflow.
"""

from django.conf import settings
from django.core.mail import send_mail

STATUS_MESSAGES = {
    "submitted": "Votre déclaration a bien été enregistrée. Un géomètre réalisera le tracé du terrain.",
    "verifying": "Votre parcelle a été tracée par un géomètre. Elle est en cours de vérification.",
    "validated": "Votre parcelle a été validée (géomètre puis notaire). Elle est désormais officielle.",
    "rejected": "La vérification de votre parcelle a été rejetée. Rapprochez-vous des services compétents.",
    "disputed": "Un chevauchement a été détecté sur votre parcelle. Un administrateur examine la situation.",
}


def _send(owner, subject, body):
    if not owner or not getattr(owner, "email", ""):
        return  # pas d'email connu : on n'envoie rien
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [owner.email], fail_silently=True)


def notify_submission(parcelle):
    """Accusé de réception à la déclaration."""
    subject = f"[Foncier] Déclaration reçue — {parcelle.reference}"
    body = (
        f"Bonjour,\n\n"
        f"Votre déclaration a bien été enregistrée sous la référence {parcelle.reference}.\n"
        f"Un géomètre réalisera le tracé, puis un notaire validera le dossier.\n\n"
        f"— Plateforme foncière"
    )
    _send(parcelle.owner, subject, body)


def notify_status_change(parcelle):
    """Informe le propriétaire d'un changement de statut."""
    label = parcelle.get_status_display()
    detail = STATUS_MESSAGES.get(parcelle.status, "")
    subject = f"[Foncier] {parcelle.reference} — {label}"
    body = (
        f"Bonjour,\n\n{detail}\n\n"
        f"Référence : {parcelle.reference}\n"
        f"Nouveau statut : {label}\n\n"
        f"— Plateforme foncière"
    )
    _send(parcelle.owner, subject, body)