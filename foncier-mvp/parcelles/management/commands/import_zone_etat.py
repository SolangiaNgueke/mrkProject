"""Importe une ZONE DE L'ÉTAT (forêt classée, réserve…) depuis un fichier .txt
de coordonnées, ou depuis un GeoJSON.

Le fichier .txt contient le contour de la zone (un point par ligne, format
« X=… Y=… Z=… » ou colonnes). Les coordonnées sont reprojetées depuis leur
système source (UTM par défaut) vers WGS84.

Exemples :
    python manage.py import_zone_etat foret_alibi.txt \
        --name "Forêt classée d'Alibi" --type foret --epsg 32631

    python manage.py import_zone_etat reserve.geojson \
        --name "Réserve de Fazao" --type reserve --geojson
"""

import json

from django.contrib.gis.gdal import CoordTransform, SpatialReference
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.management.base import BaseCommand, CommandError

from parcelles.models import ZoneEtat
from parcelles.ocr import parse_coordonnees_texte


class Command(BaseCommand):
    help = "Importe une zone de l'État (forêt classée, réserve…) depuis un .txt ou GeoJSON."

    def add_arguments(self, parser):
        parser.add_argument("fichier", help="Chemin d'un fichier .txt/.geojson, OU d'un dossier (import groupé)")
        parser.add_argument("--name", help="Nom de la zone (un seul fichier). En mode dossier, le nom du fichier est utilisé.")
        parser.add_argument("--type", default="foret",
                            choices=[c[0] for c in ZoneEtat.Type.choices],
                            help="Type de zone (foret, reserve, domaine, plan_ville, autre)")
        parser.add_argument("--epsg", type=int, default=32631,
                            help="Code EPSG source des coordonnées (défaut 32631 = UTM 31N)")
        parser.add_argument("--geojson", action="store_true",
                            help="Le(s) fichier(s) sont des GeoJSON (au lieu de .txt)")
        parser.add_argument("--replace", action="store_true",
                            help="Remplace une zone existante portant le même nom")

    def handle(self, *args, **o):
        import os

        chemin = o["fichier"]

        # Mode DOSSIER : importe tous les .txt (ou .geojson) qu'il contient.
        if os.path.isdir(chemin):
            ext = ".geojson" if o["geojson"] else ".txt"
            fichiers = sorted(
                f for f in os.listdir(chemin) if f.lower().endswith(ext)
            )
            if not fichiers:
                raise CommandError(f"Aucun fichier {ext} dans le dossier {chemin}.")
            self.stdout.write(f"{len(fichiers)} fichier(s) {ext} à importer…\n")
            ok = 0
            for nom_fichier in fichiers:
                # Nom de la zone = nom du fichier sans extension, tirets/underscores en espaces.
                nom_zone = os.path.splitext(nom_fichier)[0].replace("_", " ").replace("-", " ").strip()
                try:
                    self._importer_un(os.path.join(chemin, nom_fichier), nom_zone, o)
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    self.stdout.write(self.style.ERROR(f"  ✗ {nom_fichier} : {exc}"))
            self.stdout.write(self.style.SUCCESS(f"\n{ok}/{len(fichiers)} zone(s) importée(s)."))
            return

        # Mode FICHIER UNIQUE.
        if not o["name"]:
            raise CommandError("Pour un seul fichier, --name est requis (ou passez un dossier).")
        self._importer_un(chemin, o["name"], o)

    def _importer_un(self, chemin, nom, o):
        with open(chemin, encoding="utf-8", errors="ignore") as f:
            contenu = f.read()

        if o["replace"]:
            n, _ = ZoneEtat.objects.filter(name=nom).delete()
            if n:
                self.stdout.write(f"  ({n} zone(s) « {nom} » remplacée(s))")

        geom = self._depuis_geojson(contenu) if o["geojson"] else self._depuis_txt(contenu, o["epsg"])
        zone = ZoneEtat.objects.create(
            name=nom, type_zone=o["type"], geometry=geom,
            source=chemin.split("/")[-1],
        )
        aire_ha = round(geom.transform(6933, clone=True).area / 10000, 2)
        self.stdout.write(self.style.SUCCESS(f"  ✓ {zone}  (~{aire_ha} ha)"))

    def _depuis_txt(self, contenu, epsg):
        """Construit le polygone à partir du contour, reprojeté en WGS84."""
        points = parse_coordonnees_texte(contenu)
        if len(points) < 3:
            raise CommandError(
                f"Seulement {len(points)} point(s) lus : impossible de fermer une zone (min. 3)."
            )
        coords = [(p["x"], p["y"]) for p in points]
        if coords[0] != coords[-1]:
            coords.append(coords[0])  # ferme le contour

        # Reprojection EPSG source -> WGS84 (4326).
        src = SpatialReference(epsg)
        dst = SpatialReference(4326)
        ct = CoordTransform(src, dst)
        poly = Polygon(coords, srid=epsg)
        poly.transform(ct)
        return MultiPolygon(poly, srid=4326)

    def _depuis_geojson(self, contenu):
        data = json.loads(contenu)
        # Accepte une FeatureCollection, une Feature, ou une géométrie brute.
        if data.get("type") == "FeatureCollection":
            geoms = [GEOSGeometry(json.dumps(f["geometry"]), srid=4326) for f in data["features"]]
        elif data.get("type") == "Feature":
            geoms = [GEOSGeometry(json.dumps(data["geometry"]), srid=4326)]
        else:
            geoms = [GEOSGeometry(json.dumps(data), srid=4326)]

        polys = []
        for g in geoms:
            if g.geom_type == "Polygon":
                polys.append(g)
            elif g.geom_type == "MultiPolygon":
                polys.extend(list(g))
        if not polys:
            raise CommandError("Aucun polygone trouvé dans le GeoJSON.")
        return MultiPolygon(polys, srid=4326)