"""Recalcule automatiquement le statut et le niveau de fiabilité d'une parcelle
à partir de l'avancement de la délimitation (géomètre) et de la vérification
(notaire/cadastre). C'est la mise en œuvre de la double validation du blueprint (§8-9).

Aucun rôle ne modifie la parcelle directement : tout passe par ces signaux.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Delimitation, Parcelle, VerificationDossier


def recompute_parcelle(parcelle):
    # Si la parcelle a été supprimée (cascade), ne rien faire.
    if not Parcelle.objects.filter(pk=parcelle.pk).exists():
        return

    has_delim = Delimitation.objects.filter(parcelle=parcelle).exists()
    verif = VerificationDossier.objects.filter(parcelle=parcelle).first()
    decision = verif.decision if verif else None

    if decision == VerificationDossier.Decision.REJECTED:
        parcelle.status = Parcelle.Status.REJECTED
        parcelle.reliability = Parcelle.Reliability.LOW
    elif decision == VerificationDossier.Decision.APPROVED and has_delim:
        # Double validation OK : délimitation + vérification juridique.
        parcelle.status = Parcelle.Status.VALIDATED
        parcelle.reliability = Parcelle.Reliability.HIGH
    elif has_delim:
        # Délimitation faite, vérification juridique en cours.
        parcelle.status = Parcelle.Status.VERIFYING
        parcelle.reliability = Parcelle.Reliability.MEDIUM
    else:
        # Simplement déclarée par le propriétaire.
        parcelle.status = Parcelle.Status.SUBMITTED
        parcelle.reliability = Parcelle.Reliability.LOW

    parcelle.save(update_fields=["status", "reliability", "updated_at"])


@receiver([post_save, post_delete], sender=Delimitation)
def on_delimitation_change(sender, instance, **kwargs):
    recompute_parcelle(instance.parcelle)


@receiver([post_save, post_delete], sender=VerificationDossier)
def on_verification_change(sender, instance, **kwargs):
    recompute_parcelle(instance.parcelle)