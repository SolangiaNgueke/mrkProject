"""Recalcule automatiquement le statut et le niveau de fiabilité d'une parcelle
à partir de l'avancement de la délimitation (géomètre) et de la vérification
(notaire/cadastre). C'est la mise en œuvre de la double validation du blueprint (§8-9).

Aucun rôle ne modifie la parcelle directement : tout passe par ces signaux.
"""

from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Conflit, Delimitation, Parcelle, VerificationDossier


def _has_active_conflict(parcelle):
    return Conflit.objects.filter(resolved_at__isnull=True).filter(
        Q(parcelle_a=parcelle) | Q(parcelle_b=parcelle)
    ).exists()


def recompute_parcelle(parcelle):
    # Si la parcelle a été supprimée (cascade), ne rien faire.
    if not Parcelle.objects.filter(pk=parcelle.pk).exists():
        return

    old_status = (
        Parcelle.objects.filter(pk=parcelle.pk).values_list("status", flat=True).first()
    )
    fields = ["status", "reliability", "updated_at"]

    # Un litige actif est PRIORITAIRE : la parcelle reste en rouge tant que le
    # chevauchement n'est pas résolu.
    if _has_active_conflict(parcelle):
        parcelle.status = Parcelle.Status.DISPUTED
        parcelle.reliability = Parcelle.Reliability.LOW
    else:
        has_delim = Delimitation.objects.filter(parcelle=parcelle).exists()
        verif = VerificationDossier.objects.filter(parcelle=parcelle).first()
        decision = verif.decision if verif else None

        if decision == VerificationDossier.Decision.REJECTED:
            parcelle.status = Parcelle.Status.REJECTED
            parcelle.reliability = Parcelle.Reliability.LOW
        elif decision == VerificationDossier.Decision.APPROVED and has_delim:
            parcelle.status = Parcelle.Status.VALIDATED
            parcelle.reliability = Parcelle.Reliability.HIGH
            delim = Delimitation.objects.filter(parcelle=parcelle).first()
            if delim and delim.validated_geometry:
                parcelle.geometry = delim.validated_geometry
                fields += ["geometry", "surface_m2"]
        elif has_delim:
            parcelle.status = Parcelle.Status.VERIFYING
            parcelle.reliability = Parcelle.Reliability.MEDIUM
        else:
            parcelle.status = Parcelle.Status.SUBMITTED
            parcelle.reliability = Parcelle.Reliability.LOW

    parcelle.save(update_fields=fields)

    # Notifier le propriétaire uniquement si le statut a réellement changé.
    if parcelle.status != old_status:
        from .audit import journaliser
        from .notifications import notify_status_change

        journaliser(
            "statut_change", parcelle=parcelle,
            ancien=old_status, nouveau=parcelle.status,
        )
        notify_status_change(parcelle)


def _overlap_area_m2(geom_a, geom_b):
    """Surface (m²) du chevauchement entre deux géométries WGS84."""
    inter = geom_a.intersection(geom_b)
    if inter.empty:
        return 0.0
    return round(inter.transform(6933, clone=True).area, 2)


def recompute_conflicts(parcelle):
    """Détecte les chevauchements de `parcelle` avec les autres, crée/résout les
    alertes (Conflit) et recalcule le statut de toutes les parcelles concernées.
    """
    if not Parcelle.objects.filter(pk=parcelle.pk).exists():
        return

    impliquees = {parcelle.pk: parcelle}

    # 1) Résoudre les conflits devenus obsolètes (plus de chevauchement).
    for c in Conflit.objects.filter(resolved_at__isnull=True).filter(
        Q(parcelle_a=parcelle) | Q(parcelle_b=parcelle)
    ):
        autre = c.parcelle_b if c.parcelle_a_id == parcelle.pk else c.parcelle_a
        impliquees[autre.pk] = autre
        still = (
            parcelle.geometry is not None
            and autre.geometry is not None
            and parcelle.geometry.intersects(autre.geometry)
        )
        if not still:
            c.resolved_at = timezone.now()
            c.save(update_fields=["resolved_at"])
            from .audit import journaliser

            journaliser("litige_resolu", parcelle=parcelle, avec=autre.reference or autre.pk)

    # 2) Créer / mettre à jour les conflits pour les chevauchements actuels.
    if parcelle.geometry is not None:
        autres = Parcelle.objects.filter(
            geometry__intersects=parcelle.geometry
        ).exclude(pk=parcelle.pk)
        for autre in autres:
            impliquees[autre.pk] = autre
            a, b = (parcelle, autre) if parcelle.pk < autre.pk else (autre, parcelle)
            area = _overlap_area_m2(parcelle.geometry, autre.geometry)
            existing = Conflit.objects.filter(
                parcelle_a=a, parcelle_b=b, resolved_at__isnull=True
            ).first()
            if existing:
                existing.overlap_area_m2 = area
                existing.save(update_fields=["overlap_area_m2"])
            else:
                Conflit.objects.create(parcelle_a=a, parcelle_b=b, overlap_area_m2=area)
                from .audit import journaliser

                journaliser(
                    "litige_ouvert", parcelle=parcelle,
                    avec=autre.reference or autre.pk, surface_m2=area,
                )

    # 3) Recalculer le statut de toutes les parcelles impliquées.
    for p in impliquees.values():
        recompute_parcelle(p)


@receiver([post_save, post_delete], sender=Delimitation)
def on_delimitation_change(sender, instance, **kwargs):
    recompute_parcelle(instance.parcelle)


@receiver([post_save, post_delete], sender=VerificationDossier)
def on_verification_change(sender, instance, **kwargs):
    recompute_parcelle(instance.parcelle)