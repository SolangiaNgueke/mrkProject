"""Outils géodésiques : détection automatique du système de coordonnées selon
la localité, et reconstruction d'un polygone à partir d'un tableau de points
de bornage, reprojeté en WGS84 (le système de la carte).
"""

from django.contrib.gis.geos import Polygon


def suggest_utm_epsg(lon, lat):
    """Propose le code EPSG UTM adapté à une position (longitude, latitude).

    S'adapte automatiquement à n'importe quelle localité dans le monde :
    - zone UTM = ⌊(lon + 180) / 6⌋ + 1
    - 326xx au nord de l'équateur, 327xx au sud (datum WGS84).
    Ex. Lomé / Agbélouvé (lon≈1.2, lat≈6.6) -> zone 31 -> EPSG 32631.
    """
    zone = int((lon + 180) / 6) + 1
    base = 32600 if lat >= 0 else 32700
    return base + zone


def utm_zone_label(lon, lat):
    zone = int((lon + 180) / 6) + 1
    return f"{zone}{'N' if lat >= 0 else 'S'}"


def polygon_from_points(points, source_epsg):
    """Construit un polygone WGS84 à partir d'un tableau de points de bornage.

    `points` : liste de dicts contenant au moins `x` et `y` (coordonnées dans
    le système `source_epsg`, en mètres pour une projection UTM).
    Le polygone est fermé automatiquement puis reprojeté vers le WGS84 (4326).
    """
    coords = []
    for p in points:
        coords.append((float(p["x"]), float(p["y"])))

    if len(coords) < 3:
        raise ValueError("Au moins 3 points sont nécessaires pour un terrain.")

    if coords[0] != coords[-1]:
        coords.append(coords[0])  # ferme l'anneau

    poly = Polygon(coords, srid=int(source_epsg))
    poly.transform(4326)  # reprojection vers le système de la carte
    return poly