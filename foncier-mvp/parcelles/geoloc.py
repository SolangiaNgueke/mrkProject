"""Détermine le pays et la région administrative d'un point GPS.

Deux niveaux de précision, du plus exact au plus approximatif :

1. EXACT — polygones officiels de frontières chargés en base (modèle
   `AdminBoundary`). On teste dans quel polygone tombe réellement le point.
   Fiable y compris près des frontières. Nécessite d'importer un fichier
   GeoJSON de limites administratives (commande `import_boundaries`).

2. APPROXIMATIF — géocodage inverse hors-ligne (lieu peuplé le plus proche).
   Utilisé automatiquement tant qu'aucune frontière n'a été importée.
"""

try:
    import reverse_geocoder as _rg

    _RG_AVAILABLE = True
except ImportError:
    _RG_AVAILABLE = False


# Correspondance région (nom admin1) -> numéro, par pays (mode approximatif).
REGION_CODES = {
    "TG": {
        "Maritime": "1", "Maritime Region": "1",
        "Plateaux": "2", "Plateaux Region": "2",
        "Centrale": "3", "Centrale Region": "3", "Central": "3",
        "Kara": "4", "Kara Region": "4",
        "Savanes": "5", "Savanes Region": "5", "Savannes": "5",
    },
}


def _resolve_exact(lon, lat):
    """Détection par polygones officiels. Retourne (cc, region) ou None."""
    from django.contrib.gis.geos import Point

    from .models import AdminBoundary

    try:
        pt = Point(lon, lat, srid=4326)
        b = AdminBoundary.objects.filter(geometry__contains=pt).order_by("-level").first()
        if b:
            return (b.country_code.upper(), b.region_code or "0")
    except Exception:  # table absente / non migrée
        return None
    return None


def _resolve_approx(lon, lat):
    """Détection approximative (lieu peuplé le plus proche)."""
    if not _RG_AVAILABLE:
        return ("XX", "0")
    try:
        res = _rg.get((lat, lon))
    except Exception:  # noqa: BLE001
        return ("XX", "0")
    cc = (res.get("cc") or "XX").upper()
    admin1 = res.get("admin1") or ""
    return (cc, REGION_CODES.get(cc, {}).get(admin1, "0"))


def resolve_location(lon, lat):
    """Retourne (code_pays_ISO2, code_region) pour un point (lon, lat).

    Utilise les frontières officielles si elles ont été importées,
    sinon retombe sur la détection approximative.
    """
    exact = _resolve_exact(lon, lat)
    if exact:
        return exact
    return _resolve_approx(lon, lat)