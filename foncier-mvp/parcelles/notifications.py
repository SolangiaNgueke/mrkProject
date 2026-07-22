"""Notifications email au propriétaire d'une parcelle.

Le mode d'envoi (console en dev, SMTP en prod) est défini dans settings.py :
ce module n'a pas à changer quand on passe aux vrais emails.
Les envois sont silencieux en cas d'erreur (fail_silently) pour ne jamais
bloquer le workflow.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import Q

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


def notify_admins_new_report(signalement):
    """Alerte les administrateurs qu'un signalement a été déposé."""
    User = get_user_model()
    admins = (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(role=User.Role.ADMIN))
        .exclude(email="")
    )
    emails = [u.email for u in admins if u.email]
    if not emails:
        return
    p = signalement.parcelle
    nature = signalement.get_type_demande_display()
    subject = f"[Foncier] {nature} — {p.reference}"
    body = (
        f"Une {nature.lower()} a été déposée et attend votre traitement.\n\n"
        f"Parcelle : {p.reference}\n"
        f"Motif : {signalement.get_motif_display()}\n"
        f"Message : {signalement.comment or '—'}\n"
        f"Répondre à : {signalement.contact_email or '—'}"
        f"{' / ' + signalement.contact_phone if signalement.contact_phone else ''}\n\n"
        f"À traiter dans l'administration.\n\n"
        f"— Plateforme foncière"
    )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, emails, fail_silently=True)