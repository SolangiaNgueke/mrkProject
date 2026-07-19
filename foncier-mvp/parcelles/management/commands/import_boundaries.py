"""Importe des limites administratives (pays / régions) depuis un fichier GeoJSON.

Rend la détection pays/région EXACTE (au lieu d'approximative).

Exemple :
    python manage.py import_boundaries limites_togo.geojson \
        --country TG --level 1 --name-field shapeName

Les codes de région sont déduits du nom (voir REGION_CODES dans geoloc.py),
ou peuvent être fournis par un champ du fichier via --region-field.
"""

import json

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand, CommandError

from parcelles.geoloc import REGION_CODES
from parcelles.models import AdminBoundary


class Command(BaseCommand):
    help = "Importe des limites administratives depuis un fichier GeoJSON."

    def add_arguments(self, parser):
        parser.add_argument("geojson", help="Chemin du fichier GeoJSON")
        parser.add_argument("--country", required=True, help="Code pays ISO2 (ex. TG)")
        parser.add_argument("--level", type=int, default=1, help="0 = pays, 1 = région")
        parser.add_argument("--name-field", default="shapeName", help="Champ du nom")
        parser.add_argument("--region-field", default="", help="Champ du code région (optionnel)")
        parser.add_argument("--replace", action="store_true", help="Remplace les limites existantes de ce pays/niveau")

    def handle(self, *args, **o):
        try:
            with open(o["geojson"], encoding="utf-8") as f:
                data = json.load(f)
        except OSError as exc:
            raise CommandError(f"Fichier illisible : {exc}")

        features = data.get("features") or []
        if not features:
            raise CommandError("Aucune entité (feature) dans ce GeoJSON.")

        cc = o["country"].upper()
        if o["replace"]:
            n, _ = AdminBoundary.objects.filter(country_code=cc, level=o["level"]).delete()
            self.stdout.write(f"{n} limite(s) existante(s) supprimée(s).")

        created = 0
        for feat in features:
            props = feat.get("properties") or {}
            name = str(props.get(o["name_field"], "")).strip() or "Sans nom"

            if o["region_field"]:
                region = str(props.get(o["region_field"], "")).strip()
            else:
                region = REGION_CODES.get(cc, {}).get(name, "")

            geom = GEOSGeometry(json.dumps(feat["geometry"]), srid=4326)
            if geom.geom_type == "Polygon":
                geom = MultiPolygon(geom, srid=4326)

            AdminBoundary.objects.create(
                name=name,
                country_code=cc,
                region_code=region,
                level=o["level"],
                geometry=geom,
            )
            created += 1
            flag = "" if region else "  (code région non reconnu -> à compléter)"
            self.stdout.write(f"  {name} -> {cc}-{region or '?'}{flag}")

        self.stdout.write(self.style.SUCCESS(f"{created} limite(s) importée(s). Détection désormais EXACTE."))