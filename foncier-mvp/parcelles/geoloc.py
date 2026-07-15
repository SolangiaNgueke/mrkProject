"""Détermine le pays et la région administrative d'un point GPS.

MODULE BRANCHABLE : aujourd'hui, géocodage inverse hors-ligne (approximatif,
basé sur le lieu peuplé le plus proche). Pour une précision maximale, on
remplacera plus tard le corps de `resolve_location` par une détection par
polygones de frontières officielles dans PostGIS — sans changer le reste.
"""

try:
    import reverse_geocoder as _rg

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


# Correspondance région (nom admin1) -> numéro, par pays.
# À compléter pour d'autres pays au fur et à mesure.
REGION_CODES = {
    "TG": {  # Togo : régions numérotées du sud au nord
        "Maritime": "1", "Maritime Region": "1",
        "Plateaux": "2", "Plateaux Region": "2",
        "Centrale": "3", "Centrale Region": "3", "Central": "3",
        "Kara": "4", "Kara Region": "4",
        "Savanes": "5", "Savanes Region": "5", "Savannes": "5",
    },
}


def resolve_location(lon, lat):
    """Retourne (code_pays_ISO2, code_region) pour un point (lon, lat).

    Renvoie ('XX', '0') si la détection est indisponible ou inconnue.
    """
    if not _AVAILABLE:
        return ("XX", "0")
    try:
        res = _rg.get((lat, lon))  # requête unique (sans multiprocessing)
    except Exception:  # noqa: BLE001
        return ("XX", "0")

    cc = (res.get("cc") or "XX").upper()
    admin1 = res.get("admin1") or ""
    region = REGION_CODES.get(cc, {}).get(admin1, "0")
    return (cc, region)