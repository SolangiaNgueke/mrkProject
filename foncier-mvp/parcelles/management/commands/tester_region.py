"""Vérifie quel pays et quelle région sont détectés pour une position GPS.

    python manage.py tester_region 1.2255 6.1319          # Lomé
    python manage.py tester_region 1.19 9.55              # Kara
    python manage.py tester_region --parcelles            # toutes les parcelles

Indique aussi le MODE utilisé :
  - « exact »        : frontières officielles importées en base
  - « approximatif » : géocodage inverse (lieu peuplé le plus proche)
"""

from django.core.management.base import BaseCommand

from parcelles.geoloc import _resolve_approx, _resolve_exact, resolve_location
from parcelles.models import AdminBoundary, Parcelle


class Command(BaseCommand):
    help = "Diagnostique la détection pays/région utilisée pour les références."

    def add_arguments(self, parser):
        parser.add_argument("lon", nargs="?", type=float, help="Longitude")
        parser.add_argument("lat", nargs="?", type=float, help="Latitude")
        parser.add_argument(
            "--parcelles", action="store_true",
            help="Analyse toutes les parcelles existantes",
        )

    def handle(self, *args, **o):
        nb = AdminBoundary.objects.count()
        if nb:
            self.stdout.write(self.style.SUCCESS(
                f"Mode EXACT disponible : {nb} limite(s) administrative(s) en base."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                "Mode APPROXIMATIF : aucune frontière importée.\n"
                "  Pour une détection exacte : manage.py import_boundaries <fichier.geojson> "
                "--country TG --level 1 --replace"
            ))

        if o["parcelles"]:
            self._analyser_parcelles()
            return

        if o["lon"] is None or o["lat"] is None:
            self.stdout.write("\nIndiquez une position : manage.py tester_region <lon> <lat>")
            return

        self._analyser_point(o["lon"], o["lat"])

    def _analyser_point(self, lon, lat):
        self.stdout.write(f"\nPosition : longitude {lon}, latitude {lat}")
        exact = _resolve_exact(lon, lat)
        approx = _resolve_approx(lon, lat)
        retenu = resolve_location(lon, lat)

        self.stdout.write(f"  exact         : {exact if exact else '— (aucune frontière)'}")
        self.stdout.write(f"  approximatif  : {approx}")
        self.stdout.write(self.style.SUCCESS(
            f"  RETENU        : {retenu[0]}-{retenu[1]}  "
            f"→ référence de la forme {retenu[0]}-{retenu[1]}-AA-NNNNNN"
        ))

    def _analyser_parcelles(self):
        qs = Parcelle.objects.exclude(declared_location=None).order_by("id")
        if not qs.exists():
            self.stdout.write("\nAucune parcelle avec une localisation.")
            return
        self.stdout.write(f"\n{qs.count()} parcelle(s) :\n")
        compte = {}
        for p in qs:
            cc, region = resolve_location(p.declared_location.x, p.declared_location.y)
            attendu = f"{cc}-{region}"
            actuel = "-".join((p.reference or "").split("-")[:2]) or "—"
            marque = " " if attendu == actuel else "  ← diffère"
            compte[attendu] = compte.get(attendu, 0) + 1
            self.stdout.write(
                f"  {p.reference or '(sans réf.)':22} "
                f"lon={p.declared_location.x:9.4f} lat={p.declared_location.y:8.4f} "
                f"→ {attendu}{marque}"
            )
        self.stdout.write("\nRépartition détectée :")
        for k, v in sorted(compte.items()):
            self.stdout.write(f"  {k} : {v} parcelle(s)")