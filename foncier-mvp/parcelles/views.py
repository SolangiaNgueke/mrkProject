import hashlib
import json

from django.contrib.gis.geos import GEOSGeometry
from django.contrib.gis.measure import D
from django.db.models import Count
from django.http import FileResponse, Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .geo import polygon_from_points, suggest_utm_epsg, utm_zone_label

# Anti-doublon à la déclaration : une nouvelle soumission du même utilisateur,
# au même endroit et dans ce court laps de temps, est considérée comme un
# double envoi (double-clic ou requête relancée) et non comme une 2e parcelle.
DELAI_ANTI_DOUBLON = 60          # secondes
DISTANCE_ANTI_DOUBLON_M = 25     # mètres
from .models import Conflit, Delimitation, Document, Parcelle, Signalement, VerificationDossier, ZoneEtat
from .permissions import IsOwnerOrStaffOrReadOnly
from .serializers import (
    DocumentSerializer,
    ParcelleFileAttenteSerializer,
    ParcelleFileNotaireSerializer,
    OverlapSerializer,
    ParcelleMineSerializer,
    ParcellePublicSerializer,
    ParcelleSubmitSerializer,
)


class ParcelleViewSet(viewsets.ModelViewSet):
    """API des parcelles.

    VISIBILITÉ (règle centrale) :
      - Publiques : uniquement les parcelles VALIDÉES (double validation) et celles
        EN LITIGE confirmé par un vérificateur.
      - Privées : brouillons, soumises, en vérification, rejetées -> visibles seulement
        par leur propriétaire et les vérificateurs (notaire/cadastre/géomètre/admin).
    La détection de chevauchement, elle, compare contre TOUTES les parcelles
    (anti-fraude), mais anonymise les conflits avec des parcelles non publiques.
    """

    # Statuts affichés au grand public.
    # Publiques dès qu'un géomètre les a tracées : en vérification (bleu),
    # validées (vert) ou en litige (rouge). Les simples soumissions (point,
    # sans tracé) restent privées.
    PUBLIC_STATUSES = (
        Parcelle.Status.VERIFYING,
        Parcelle.Status.VALIDATED,
        Parcelle.Status.DISPUTED,
    )

    permission_classes = [IsOwnerOrStaffOrReadOnly]

    @staticmethod
    def _is_verifier(user):
        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or user.role in (user.Role.NOTARY, user.Role.SURVEYOR, user.Role.ADMIN)
            )
        )

    def get_queryset(self):
        """Ne renvoie que ce que l'utilisateur a le droit de voir."""
        qs = Parcelle.objects.all().order_by("-created_at")
        user = self.request.user

        # Carte publique (liste) : uniquement les parcelles publiques AVEC un tracé
        # officiel (donc validées). Les soumissions sans polygone n'y figurent pas.
        if self.action == "list":
            return qs.filter(status__in=self.PUBLIC_STATUSES, geometry__isnull=False)

        # Les vérificateurs voient tout (ils doivent instruire les dossiers).
        if self._is_verifier(user):
            return qs

        public = qs.filter(status__in=self.PUBLIC_STATUSES)
        if user and user.is_authenticated:
            # Le propriétaire voit en plus SES propres parcelles (ses soumissions).
            return (public | qs.filter(owner=user)).distinct()
        return public

    def get_serializer_class(self):
        if self.action == "create":
            return ParcelleSubmitSerializer  # citoyen : une localisation (point)
        return ParcellePublicSerializer

    def _overlap_payload(self, overlaps, user):
        """Détaille les conflits publics ou appartenant à l'utilisateur ;
        anonymise les autres (ne jamais révéler l'existence détaillée d'une
        parcelle privée d'autrui)."""
        visible, anonymous = [], 0
        for p in overlaps:
            is_public = p.status in self.PUBLIC_STATUSES
            is_mine = bool(user and user.is_authenticated and p.owner_id == user.id)
            if is_public or is_mine or self._is_verifier(user):
                visible.append(p)
            else:
                anonymous += 1
        return {
            "overlap_count": len(visible) + anonymous,
            "overlaps": OverlapSerializer(visible, many=True).data,
            "anonymous_overlaps": anonymous,
        }

    def create(self, request, *args, **kwargs):
        """Le citoyen soumet une LOCALISATION (point) + un nom. Pas de tracé :
        le polygone sera réalisé par le géomètre. La parcelle est privée
        (statut « soumise ») jusqu'à validation."""
        serializer = ParcelleSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Anti-doublon : un envoi répété (double-clic, requête relancée après une
        # coupure réseau) ne doit pas créer deux fois la même parcelle. On renvoie
        # celle qui vient d'être déclarée au même endroit par le même utilisateur.
        point = serializer.validated_data.get("declared_location")
        if point:
            from datetime import timedelta

            from django.utils import timezone

            recente = (
                Parcelle.objects.filter(
                    owner=request.user,
                    status=Parcelle.Status.SUBMITTED,
                    created_at__gte=timezone.now() - timedelta(seconds=DELAI_ANTI_DOUBLON),
                    declared_location__distance_lte=(point, D(m=DISTANCE_ANTI_DOUBLON_M)),
                )
                .order_by("-created_at")
                .first()
            )
            if recente:
                data = ParcelleSubmitSerializer(recente).data
                data["already_registered_zone"] = False
                data["doublon_evite"] = True
                return Response(data, status=status.HTTP_200_OK)

        parcelle = serializer.save(
            owner=request.user, status=Parcelle.Status.SUBMITTED
        )

        # Alerte si le point tombe dans une zone de l'État (forêt classée…).
        zone = ZoneEtat.objects.filter(geometry__contains=point).first() if point else None

        # Accusé de réception au propriétaire + journal d'audit.
        from .audit import journaliser
        from .notifications import notify_submission

        journaliser("parcelle_declaree", actor=request.user, parcelle=parcelle)
        if zone:
            journaliser("parcelle_declaree", actor=request.user, parcelle=parcelle,
                        alerte_zone_etat=zone.name)
        notify_submission(parcelle)

        data = ParcelleSubmitSerializer(parcelle).data
        # Avertissement ANONYME : la localisation tombe-t-elle dans une parcelle
        # déjà validée ? (on ne révèle aucun détail).
        already = False
        if parcelle.declared_location:
            already = Parcelle.objects.filter(
                status=Parcelle.Status.VALIDATED,
                geometry__contains=parcelle.declared_location,
            ).exists()
        data["already_registered_zone"] = already
        # Alerte forte : déclaration dans un domaine de l'État.
        if zone:
            data["zone_etat"] = {"nom": zone.name, "type": zone.get_type_zone_display()}
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def check_overlap(self, request):
        """Teste un polygone SANS l'enregistrer.

        Compare contre TOUTES les parcelles (y compris privées) pour ne rien
        laisser passer, mais n'expose pas les détails des parcelles privées d'autrui.
        """
        geom_data = request.data.get("geometry")
        if not geom_data:
            return Response(
                {"detail": "Champ 'geometry' (GeoJSON) requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            geom = GEOSGeometry(json.dumps(geom_data), srid=4326)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Géométrie invalide : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        overlaps = Parcelle.objects.filter(geometry__intersects=geom).exclude(
            status=Parcelle.Status.REJECTED
        )
        return Response(self._overlap_payload(overlaps, request.user))

    # ------------------------------------------------------------------ #
    #  Documents confidentiels                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _has_full_doc_access(user, parcelle):
        """Accès COMPLET aux documents : propriétaire, notaire/cadastre, admin."""
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or user.role in (user.Role.NOTARY, user.Role.ADMIN):
            return True
        return parcelle.owner_id == user.id

    @classmethod
    def _can_read_doc(cls, user, parcelle, doc):
        """Peut lire CE document précis.

        Le géomètre n'accède QU'aux documents techniques (plan / bornage),
        jamais aux titres ni aux pièces d'identité (moindre privilège)."""
        if cls._has_full_doc_access(user, parcelle):
            return True
        if (
            user
            and user.is_authenticated
            and user.role == user.Role.SURVEYOR
            and doc.doc_type == Document.DocType.PLAN
        ):
            return True
        return False

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="documents",
        parser_classes=[MultiPartParser, FormParser],
    )
    def documents(self, request, pk=None):
        """GET  -> liste les documents accessibles selon le rôle
        POST -> ajoute un document (propriétaire ou admin), calcule le hash SHA-256."""
        parcelle = self.get_object()  # vérifie déjà les permissions d'objet
        user = request.user

        if request.method == "GET":
            if self._has_full_doc_access(user, parcelle):
                docs = parcelle.documents.all().order_by("-created_at")
            elif user.is_authenticated and user.role == user.Role.SURVEYOR:
                # Géomètre : uniquement les plans / bornages.
                docs = parcelle.documents.filter(
                    doc_type=Document.DocType.PLAN
                ).order_by("-created_at")
            else:
                return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
            return Response(
                DocumentSerializer(docs, many=True, context={"request": request}).data
            )

        # POST : seul le propriétaire (ou un admin) peut ajouter une pièce.
        is_owner = parcelle.owner_id == user.id
        if not (is_owner or user.is_superuser or user.role == user.Role.ADMIN):
            return Response(
                {"detail": "Seul le propriétaire peut ajouter des documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DocumentSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        doc = serializer.save(parcelle=parcelle)

        # Calcul du hash SHA-256 pour garantir l'intégrité du fichier.
        hasher = hashlib.sha256()
        for chunk in doc.file.chunks():
            hasher.update(chunk)
        doc.sha256 = hasher.hexdigest()
        doc.save(update_fields=["sha256"])

        from .audit import journaliser

        journaliser(
            "document_ajoute", actor=request.user, parcelle=parcelle,
            type_doc=doc.doc_type, sha256=doc.sha256,
        )

        return Response(
            DocumentSerializer(doc, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path=r"documents/(?P<doc_id>[0-9]+)/download",
        url_name="download-document",
    )
    def download_document(self, request, pk=None, doc_id=None):
        """Téléchargement protégé : aucun lien public direct.
        L'accès dépend du rôle ET du type de document (géomètre = plans seulement)."""
        parcelle = self.get_object()
        try:
            doc = parcelle.documents.get(pk=doc_id)
        except Document.DoesNotExist:
            raise Http404
        if not self._can_read_doc(request.user, parcelle, doc):
            return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
        filename = doc.file.name.split("/")[-1]
        return FileResponse(doc.file.open("rb"), as_attachment=True, filename=filename)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"documents/(?P<doc_id>[0-9]+)",
        permission_classes=[IsAuthenticated],
    )
    def delete_document(self, request, pk=None, doc_id=None):
        """Suppression ENCADRÉE : le propriétaire (ou un admin) peut supprimer un
        document uniquement tant que la parcelle est « soumise ». Dès que la
        vérification a commencé, les documents sont figés (intégrité anti-fraude)."""
        try:
            parcelle = Parcelle.objects.get(pk=pk)
        except Parcelle.DoesNotExist:
            raise Http404

        user = request.user
        is_owner = parcelle.owner_id == user.id
        is_admin = user.is_superuser or user.role == user.Role.ADMIN
        if not (is_owner or is_admin):
            return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)

        # Encadrement : une fois la vérification lancée, on fige (sauf admin).
        if not is_admin and parcelle.status != Parcelle.Status.SUBMITTED:
            return Response(
                {"detail": "Documents figés : la vérification a déjà commencé."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            doc = parcelle.documents.get(pk=doc_id)
        except Document.DoesNotExist:
            raise Http404

        from .audit import journaliser

        journaliser(
            "document_supprime", actor=request.user, parcelle=parcelle,
            type_doc=doc.doc_type, sha256=doc.sha256,
        )
        doc.file.delete(save=False)  # supprime le fichier du disque
        doc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------ #
    #  Délimitation par coordonnées (géomètre)                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_surveyor(user):
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.role in (user.Role.SURVEYOR, user.Role.ADMIN))
        )

    @staticmethod
    def _is_notary(user):
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.role in (user.Role.NOTARY, user.Role.ADMIN))
        )

    @action(detail=True, methods=["get"], url_path="suggest_crs")
    def suggest_crs(self, request, pk=None):
        """Propose automatiquement le système de coordonnées adapté à la
        localité de la parcelle (s'adapte à n'importe quelle région)."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()
        # Référence : le polygone s'il existe, sinon la localisation déclarée.
        ref = parcelle.geometry.centroid if parcelle.geometry else parcelle.declared_location
        if ref is None:
            return Response(
                {"detail": "Aucune localisation pour cette parcelle."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "suggested_epsg": suggest_utm_epsg(ref.x, ref.y),
                "utm_zone": utm_zone_label(ref.x, ref.y),
                "lon": round(ref.x, 6),
                "lat": round(ref.y, 6),
            }
        )

    @action(detail=True, methods=["post"], url_path="preview_delimitation")
    def preview_delimitation(self, request, pk=None):
        """Calcule et renvoie le polygone SANS rien enregistrer (prévisualisation)."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        self.get_object()

        source_epsg = request.data.get("source_epsg")
        points = request.data.get("points")
        if not source_epsg or not points:
            return Response(
                {"detail": "Champs 'source_epsg' et 'points' requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            poly = polygon_from_points(points, source_epsg)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Coordonnées invalides : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        surface = round(poly.transform(6933, clone=True).area, 2)
        return Response({"geometry": json.loads(poly.geojson), "surface_m2": surface})

    @action(detail=True, methods=["post"], url_path="delimitation_from_points")
    def delimitation_from_points(self, request, pk=None):
        """Construit la délimitation du géomètre à partir d'un tableau de points
        (X/Y dans le système `source_epsg`), reprojetée en WGS84."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()

        source_epsg = request.data.get("source_epsg")
        points = request.data.get("points")
        if not source_epsg or not points:
            return Response(
                {"detail": "Champs 'source_epsg' et 'points' requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            poly = polygon_from_points(points, source_epsg)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Coordonnées invalides : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._enregistrer_trace(request, parcelle, poly, points, int(source_epsg))

    @action(detail=True, methods=["get"], url_path="certificat", permission_classes=[IsAuthenticated])
    def certificat(self, request, pk=None):
        """Génère et renvoie le Certificat de Vérification (PDF) d'une parcelle.

        Uniquement pour les parcelles VALIDÉES. Accessible au propriétaire et à
        l'administrateur. Le PDF est généré à la volée (rien n'est stocké).
        """
        parcelle = self.get_object()
        user = request.user
        is_owner = parcelle.owner_id == user.id
        is_admin = user.is_superuser or user.role == user.Role.ADMIN
        if not (is_owner or is_admin):
            return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
        if parcelle.status != Parcelle.Status.VALIDATED:
            return Response(
                {"detail": "Le certificat n'est disponible que pour une parcelle validée."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .certificat import generer_certificat

        nom, contenu = generer_certificat(parcelle)
        from django.http import HttpResponse

        resp = HttpResponse(contenu, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="{nom}"'
        return resp

    @action(detail=False, methods=["post"], url_path="nouveau_trace", permission_classes=[IsAuthenticated])
    def nouveau_trace(self, request):
        """Crée une parcelle VIERGE que le géomètre va tracer directement.

        Pour les relevés autonomes : le géomètre n'attend pas qu'un citoyen ait
        déclaré la parcelle. Elle reçoit une référence et une localisation
        provisoire (le centre de la carte), puis passe par le tracé habituel.
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)

        from django.contrib.gis.geos import Point as GEOSPoint

        # Localisation provisoire fournie par le client (centre de la carte),
        # sinon un point par défaut au Togo. Le tracé la remplacera.
        lon = request.data.get("lon")
        lat = request.data.get("lat")
        try:
            point = GEOSPoint(float(lon), float(lat), srid=4326) if lon and lat else GEOSPoint(1.2, 6.13, srid=4326)
        except (TypeError, ValueError):
            point = GEOSPoint(1.2, 6.13, srid=4326)

        parcelle = Parcelle.objects.create(
            owner=request.user,
            declared_location=point,
            status=Parcelle.Status.SUBMITTED,
            name_owner_public=False,
        )
        from .audit import journaliser

        journaliser("parcelle_declaree", actor=request.user, parcelle=parcelle,
                    trace_geometre_autonome=True)
        return Response(
            {"id": parcelle.id, "reference": parcelle.reference},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="delimitation_freehand")
    def delimitation_freehand(self, request, pk=None):
        """Tracé à MAIN LEVÉE : le géomètre dessine le polygone sur la carte.

        Reçoit directement l'anneau de sommets en lon/lat (WGS84). Autonome :
        ne dépend d'aucun document. Suit ensuite le même circuit que le tracé
        par coordonnées (détection de litige, audit, statut « non vérifié »).
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()

        ring = request.data.get("ring")  # [[lon,lat], [lon,lat], ...]
        if not ring or len(ring) < 3:
            return Response(
                {"detail": "Un tracé exige au moins 3 sommets."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from django.contrib.gis.geos import Polygon as GEOSPolygon

        try:
            coords = [(float(x), float(y)) for x, y in ring]
            if coords[0] != coords[-1]:
                coords.append(coords[0])          # ferme l'anneau
            poly = GEOSPolygon(coords, srid=4326)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Tracé invalide : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # boundary_points : on mémorise les sommets lon/lat (source = WGS84).
        pts = [{"name": f"P{i+1}", "x": x, "y": y} for i, (x, y) in enumerate(coords[:-1])]
        return self._enregistrer_trace(request, parcelle, poly, pts, 4326, main_levee=True)

    def _enregistrer_trace(self, request, parcelle, poly, points, source_epsg, main_levee=False):
        """Logique COMMUNE aux deux modes de tracé (coordonnées et main levée) :
        enregistre la géométrie, crée la délimitation, détecte les litiges,
        journalise, et renvoie la réponse (surface + conflits)."""
        # Le tracé du géomètre devient la géométrie de la parcelle : elle
        # apparaît immédiatement sur la carte (gris) et sert à détecter les conflits.
        parcelle.geometry = poly
        parcelle.save(update_fields=["geometry", "surface_m2", "updated_at"])

        delim, _ = Delimitation.objects.update_or_create(
            parcelle=parcelle,
            defaults={
                "surveyor": request.user,
                "validated_geometry": poly,
                "boundary_points": points,
                "source_epsg": int(source_epsg),
            },
        )
        surface = round(poly.transform(6933, clone=True).area, 2)

        from .audit import journaliser
        from .signals import recompute_conflicts

        journaliser(
            "trace_valide", actor=request.user, parcelle=parcelle,
            surface_m2=surface, epsg=int(source_epsg), nb_points=len(points),
            main_levee=main_levee,
        )
        recompute_conflicts(parcelle)

        ids = set(
            Parcelle.objects.filter(geometry__intersects=poly)
            .exclude(pk=parcelle.pk)
            .exclude(status=Parcelle.Status.REJECTED)
            .values_list("pk", flat=True)
        )
        overlap = self._overlap_payload(Parcelle.objects.filter(pk__in=ids), request.user)

        data = {
            "delimitation_id": delim.id,
            "geometry": json.loads(poly.geojson),
            "surface_m2": surface,
        }
        data.update(overlap)
        return Response(data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="import_txt",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_txt(self, request, pk=None):
        """Lit un fichier .txt de coordonnées et renvoie les points détectés.

        Comme l'OCR, ne sauvegarde rien : le géomètre vérifie/corrige, choisit
        l'EPSG, puis valide. Réservé au géomètre.
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        self.get_object()

        f = request.FILES.get("file")
        if not f:
            return Response({"detail": "Aucun fichier fourni."}, status=status.HTTP_400_BAD_REQUEST)

        from .ocr import parse_coordonnees_texte

        try:
            points = parse_coordonnees_texte(f.read())
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Lecture impossible : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"count": len(points), "points": points})

    @action(
        detail=True,
        methods=["post"],
        url_path="ocr_plan",
        parser_classes=[MultiPartParser, FormParser],
    )
    def ocr_plan(self, request, pk=None):
        """Lit un fichier de plan et renvoie les points de bornage détectés.

        Accepte une IMAGE, un PDF ou un fichier .txt de coordonnées : le type est
        détecté automatiquement (le .txt passe par le lecteur de coordonnées, le
        reste par l'OCR). NE sauvegarde rien : le géomètre vérifie/corrige, puis
        valide. Ne fait que pré-remplir le tableau.
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        self.get_object()  # vérifie l'existence de la parcelle

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "Aucun fichier fourni."}, status=status.HTTP_400_BAD_REQUEST)

        contenu = upload.read()
        nom = (upload.name or "").lower()
        est_texte = nom.endswith(".txt") or nom.endswith(".csv") or nom.endswith(".dat")

        from .ocr import extract_boundary_points, parse_coordonnees_texte

        try:
            if est_texte:
                points = parse_coordonnees_texte(contenu)   # fichier de coordonnées
            else:
                points = extract_boundary_points(contenu)    # image ou PDF -> OCR
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Lecture impossible : {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"count": len(points), "points": points, "source": "txt" if est_texte else "ocr"})

    @action(detail=True, methods=["get"], url_path="documents_ocr")
    def documents_ocr(self, request, pk=None):
        """Liste les documents du dossier que le géomètre peut lire par OCR.

        Réservé au géomètre. Renvoie tous les types de pièces (plan, titre,
        acte…) pour qu'il choisisse LE document déjà fourni par le citoyen,
        plutôt que d'en re-téléverser un depuis son ordinateur (source d'erreur).
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()
        docs = [
            {
                "id": d.id,
                "type": d.doc_type,
                "type_display": d.get_doc_type_display(),
                "filename": d.file.name.split("/")[-1],
            }
            for d in parcelle.documents.all().order_by("doc_type")
        ]
        return Response({"documents": docs})

    @action(detail=True, methods=["post"], url_path="ocr_document")
    def ocr_document(self, request, pk=None):
        """Lance l'OCR sur un document DÉJÀ stocké dans le dossier.

        Le géomètre indique l'identifiant du document ; le serveur le lit et ne
        renvoie QUE les points de bornage détectés — jamais le fichier lui-même.
        Il peut ainsi tracer à partir d'un titre foncier sans pouvoir le
        télécharger (le moindre privilège sur les documents reste préservé).
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()

        doc_id = request.data.get("document_id")
        try:
            doc = parcelle.documents.get(pk=doc_id)
        except Document.DoesNotExist:
            raise Http404

        from .ocr import extract_boundary_points

        try:
            with doc.file.open("rb") as f:
                points = extract_boundary_points(f.read())
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"OCR indisponible : {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"count": len(points), "points": points})

    @action(
        detail=False,
        methods=["get"],
        url_path="a_valider",
        permission_classes=[IsAuthenticated],
    )
    def a_valider(self, request):
        """File de travail du NOTAIRE : parcelles tracées en attente de validation
        juridique (statut « en vérification »), plus celles qu'il a déjà traitées."""
        if not self._is_notary(request.user):
            return Response({"detail": "Réservé au notaire."}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            Parcelle.objects.filter(
                status__in=[Parcelle.Status.VERIFYING, Parcelle.Status.VALIDATED, Parcelle.Status.REJECTED]
            )
            .select_related("verification")
            .prefetch_related("documents")
            .order_by("status", "-updated_at")
        )
        data = ParcelleFileNotaireSerializer(qs, many=True).data
        return Response({
            "a_valider": sum(1 for p in data if p["status"] == "verifying"),
            "traitees": sum(1 for p in data if p["status"] in ("validated", "rejected")),
            "parcelles": data,
        })

    @action(
        detail=True,
        methods=["get"],
        url_path="fiche_notaire",
        permission_classes=[IsAuthenticated],
    )
    def fiche_notaire(self, request, pk=None):
        """Fiche détaillée d'une parcelle pour la décision du notaire :
        géométrie (tracé), documents justificatifs, et infos de référence."""
        if not self._is_notary(request.user):
            return Response({"detail": "Réservé au notaire."}, status=status.HTTP_403_FORBIDDEN)
        try:
            parcelle = Parcelle.objects.get(pk=pk)
        except Parcelle.DoesNotExist:
            raise Http404

        geometry = None
        if parcelle.geometry:
            import json

            geometry = json.loads(parcelle.geometry.geojson)

        verif = getattr(parcelle, "verification", None)
        return Response({
            "id": parcelle.id,
            "reference": parcelle.reference,
            "status": parcelle.status,
            "status_display": parcelle.get_status_display(),
            "surface_m2": parcelle.surface_m2,
            "geometry": geometry,
            "documents": DocumentSerializer(
                parcelle.documents.all(), many=True, context={"request": request}
            ).data,
            "decision": verif.decision if verif else "pending",
            "comments": verif.comments if verif else "",
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="decision",
        permission_classes=[IsAuthenticated],
    )
    def decision(self, request, pk=None):
        """Décision du notaire : approuver ou rejeter (motif obligatoire au rejet)."""
        if not self._is_notary(request.user):
            return Response({"detail": "Réservé au notaire."}, status=status.HTTP_403_FORBIDDEN)
        try:
            parcelle = Parcelle.objects.get(pk=pk)
        except Parcelle.DoesNotExist:
            raise Http404

        if not hasattr(parcelle, "delimitation"):
            return Response(
                {"detail": "Cette parcelle n'a pas encore été tracée par un géomètre."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        choix = request.data.get("decision")
        motif = (request.data.get("comments") or "").strip()
        if choix not in ("approved", "rejected"):
            return Response({"detail": "Décision invalide."}, status=status.HTTP_400_BAD_REQUEST)
        if choix == "rejected" and not motif:
            return Response(
                {"detail": "Un motif est obligatoire pour rejeter un dossier."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.utils import timezone

        verif, _ = VerificationDossier.objects.get_or_create(parcelle=parcelle)
        verif.decision = choix
        verif.comments = motif
        verif.notary = request.user
        verif.decided_at = timezone.now()
        verif.save()   # déclenche le recalcul du statut (validée / rejetée)

        from .audit import journaliser

        journaliser("verification", actor=request.user, parcelle=parcelle, decision=choix)

        parcelle.refresh_from_db()
        return Response({
            "detail": "Dossier validé." if choix == "approved" else "Dossier rejeté.",
            "status": parcelle.status,
            "status_display": parcelle.get_status_display(),
        })

    @action(
        detail=False,
        methods=["get"],
        url_path="a_tracer",
        permission_classes=[IsAuthenticated],
    )
    def a_tracer(self, request):
        """File de travail du GÉOMÈTRE.

        Renvoie les parcelles déclarées en attente de tracé, ainsi que celles
        qu'il a déjà tracées (pour pouvoir les corriger tant que le notaire
        n'a pas validé). Évite de saisir un numéro de parcelle à la main.
        """
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            Parcelle.objects.filter(
                status__in=[Parcelle.Status.SUBMITTED, Parcelle.Status.VERIFYING]
            )
            .exclude(declared_location=None)
            .select_related("delimitation")
            .prefetch_related("documents")
            .order_by("status", "created_at")   # « soumises » d'abord, plus anciennes en tête
        )
        data = ParcelleFileAttenteSerializer(qs, many=True).data
        return Response({
            "a_tracer": sum(1 for p in data if not p["deja_trace"]),
            "en_verification": sum(1 for p in data if p["deja_trace"]),
            "parcelles": data,
        })

    @action(
        detail=False,
        methods=["get"],
        url_path="mine",
        permission_classes=[IsAuthenticated],
    )
    def mine(self, request):
        """Liste les parcelles du propriétaire connecté (même privées/non tracées)."""
        qs = Parcelle.objects.filter(owner=request.user).order_by("-created_at")
        return Response(ParcelleMineSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"], url_path="zones_etat", permission_classes=[AllowAny])
    def zones_etat(self, request):
        """Zones de l'État (forêts classées, réserves…) au format GeoJSON.

        Couche de référence publique, affichée en permanence sur la carte.
        """
        features = []
        for z in ZoneEtat.objects.all():
            features.append({
                "type": "Feature",
                "geometry": json.loads(z.geometry.geojson),
                "properties": {
                    "id": z.id, "name": z.name,
                    "type": z.type_zone, "type_display": z.get_type_zone_display(),
                },
            })
        return Response({"type": "FeatureCollection", "features": features})

    @action(detail=False, methods=["get"], url_path="recherche", permission_classes=[AllowAny])
    def recherche(self, request):
        """Recherche publique : par référence de terrain OU par nom de région.

        Renvoie les parcelles publiques dont la référence correspond, et, si le
        texte évoque une région, le centre de cette région (pour y centrer la
        carte). La recherche par ville se fait côté client (géocodage MapTiler).
        """
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"parcelles": [], "region": None})

        # --- Parcelles par référence (publiques uniquement) ---
        parcelles = []
        qs = Parcelle.objects.filter(
            status__in=self.PUBLIC_STATUSES, reference__icontains=q
        ).order_by("-created_at")[:10]
        for p in qs:
            centre = p.geometry.centroid if p.geometry else p.declared_location
            if not centre:
                continue
            parcelles.append({
                "id": p.id, "reference": p.reference, "status": p.status,
                "lon": round(centre.x, 6), "lat": round(centre.y, 6),
            })

        # --- Région (frontières officielles si importées) ---
        region = None
        try:
            from .geoloc import normaliser
            from .models import AdminBoundary

            cible = normaliser(q)
            if cible:
                for b in AdminBoundary.objects.filter(level=1):
                    nom_norm = normaliser(b.name)
                    if nom_norm == cible or cible in nom_norm:
                        c = b.geometry.centroid
                        region = {"nom": b.name, "lon": round(c.x, 6), "lat": round(c.y, 6)}
                        break
        except Exception:  # noqa: BLE001
            region = None

        return Response({"parcelles": parcelles, "region": region})

    # ------------------------------------------------------------------ #
    #  Signalement communautaire                                          #
    # ------------------------------------------------------------------ #

    @action(
        detail=True,
        methods=["post"],
        url_path="report",
        permission_classes=[AllowAny],
    )
    def report(self, request, pk=None):
        """Formulaire de contact au sujet d'une parcelle.

        Deux natures : demande d'information ou signalement. Ouvert à tous ;
        un moyen de recontact (email) est exigé des visiteurs non connectés.
        Crée une alerte pour l'administrateur, sans modifier le statut.
        """
        try:
            parcelle = Parcelle.objects.get(pk=pk)
        except Parcelle.DoesNotExist:
            raise Http404

        type_demande = request.data.get("type_demande") or Signalement.Type.SIGNALEMENT
        if type_demande not in Signalement.Type.values:
            type_demande = Signalement.Type.SIGNALEMENT

        motif = request.data.get("motif") or Signalement.Motif.AUTRE
        comment = (request.data.get("comment") or "").strip()
        email = (request.data.get("contact_email") or "").strip()
        phone = (request.data.get("contact_phone") or "").strip()

        user = request.user if request.user.is_authenticated else None
        if not user:
            if not email:
                return Response(
                    {"detail": "Indiquez une adresse email pour que nous puissions vous répondre."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif not email:
            email = user.email or ""

        if not comment:
            return Response(
                {"detail": "Précisez le motif de votre demande dans le message."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        signalement = Signalement.objects.create(
            parcelle=parcelle,
            reporter=user,
            type_demande=type_demande,
            motif=motif,
            comment=comment,
            contact_email=email,
            contact_phone=phone,
        )

        from .audit import journaliser
        from .notifications import notify_admins_new_report

        journaliser(
            "signalement", actor=user, parcelle=parcelle,
            type_demande=type_demande, motif=motif,
        )
        notify_admins_new_report(signalement)

        message = (
            "Signalement enregistré. Un administrateur va l'examiner."
            if type_demande == Signalement.Type.SIGNALEMENT
            else "Demande envoyée. Nous vous répondrons à l'adresse indiquée."
        )
        return Response({"detail": message}, status=status.HTTP_201_CREATED)


class DashboardStats(APIView):
    """Statistiques de pilotage — RÉSERVÉ aux administrateurs."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        is_admin = user.is_superuser or getattr(user, "role", None) == user.Role.ADMIN
        if not is_admin:
            return Response({"detail": "Réservé aux administrateurs."}, status=status.HTTP_403_FORBIDDEN)

        # Comptage des parcelles par statut.
        raw = {r["status"]: r["c"] for r in Parcelle.objects.values("status").annotate(c=Count("id"))}
        par_statut = {s.value: raw.get(s.value, 0) for s in Parcelle.Status}

        data = {
            "parcelles_total": Parcelle.objects.count(),
            "par_statut": par_statut,
            "litiges_actifs": Conflit.objects.filter(resolved_at__isnull=True).count(),
            "signalements_a_examiner": Signalement.objects.filter(resolved_at__isnull=True).count(),
            # Dossiers en attente d'action humaine :
            "attente_geometre": par_statut.get(Parcelle.Status.SUBMITTED.value, 0),   # à tracer
            "attente_notaire": par_statut.get(Parcelle.Status.VERIFYING.value, 0),    # à valider juridiquement
        }
        return Response(data)