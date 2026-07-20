"""Journal d'audit inaltérable (§17 du blueprint).

Chaque entrée est chaînée à la précédente par une empreinte SHA-256 :

    empreinte(n) = SHA256( empreinte(n-1) + contenu(n) )

Si quelqu'un modifiait une ligne directement en base, son empreinte ne
correspondrait plus et toute la chaîne suivante serait invalidée : l'altération
est donc détectable (commande `verifier_audit`).
"""

import hashlib
import json

from .models import AuditLog


def _empreinte(prev_hash, action, actor_label, parcelle_ref, details, created_at):
    contenu = json.dumps(
        {
            "prev": prev_hash,
            "action": action,
            "actor": actor_label,
            "parcelle": parcelle_ref,
            "details": details,
            "at": created_at.isoformat() if created_at else "",
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


def journaliser(action, actor=None, parcelle=None, **details):
    """Enregistre une action dans le journal. Ne lève jamais d'erreur : un
    problème d'audit ne doit pas bloquer le fonctionnement de la plateforme."""
    try:
        dernier = AuditLog.objects.order_by("-id").first()
        prev_hash = dernier.entry_hash if dernier else ""

        entry = AuditLog(
            action=action,
            actor=actor if (actor and getattr(actor, "is_authenticated", False)) else None,
            actor_label=(getattr(actor, "username", "") or "système"),
            parcelle=parcelle,
            parcelle_ref=(getattr(parcelle, "reference", "") or ""),
            details=details or {},
            prev_hash=prev_hash,
        )
        entry.save()  # created_at est renseigné ici

        entry.entry_hash = _empreinte(
            prev_hash, entry.action, entry.actor_label,
            entry.parcelle_ref, entry.details, entry.created_at,
        )
        # .update() : contourne le save() verrouillé, sans modifier le contenu.
        AuditLog.objects.filter(pk=entry.pk).update(entry_hash=entry.entry_hash)
        return entry
    except Exception:  # noqa: BLE001
        return None


def verifier_integrite():
    """Recalcule toute la chaîne. Retourne (ok, nombre_verifie, premiere_rupture)."""
    prev_hash = ""
    n = 0
    for e in AuditLog.objects.order_by("id"):
        attendu = _empreinte(
            prev_hash, e.action, e.actor_label, e.parcelle_ref, e.details, e.created_at
        )
        if e.prev_hash != prev_hash or e.entry_hash != attendu:
            return (False, n, e)
        prev_hash = e.entry_hash
        n += 1
    return (True, n, None)