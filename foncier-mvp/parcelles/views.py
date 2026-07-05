import hashlib
import json

from django.contrib.gis.geos import GEOSGeometry
from django.http import FileResponse, Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .geo import polygon_from_points, suggest_utm_epsg, utm_zone_label
from .models import Delimitation, Document, Parcelle
from .permissions import IsOwnerOrStaffOrReadOnly
from .serializers import (
    DocumentSerializer,
    OverlapSerializer,
    ParcelleCreateSerializer,
    ParcellePublicSerializer,
)


class ParcelleViewSet(viewsets.ModelViewSet):
    """API des parcelles.

    GET  /api/parcelles/                -> FeatureCollection GeoJSON (champs publics)
    POST /api/parcelles/                -> crée une parcelle + renvoie les chevauchements
    POST /api/parcelles/check_overlap/  -> teste un polygone SANS l'enregistrer
    """

    queryset = Parcelle.objects.all().order_by("-created_at")
    permission_classes = [IsOwnerOrStaffOrReadOnly]

    def get_serializer_class(self):
        if self.action == "create":
            return ParcelleCreateSerializer
        return ParcellePublicSerializer

    def create(self, request, *args, **kwargs):
        serializer = ParcelleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # La parcelle est automatiquement rattachée à l'utilisateur connecté.
        parcelle = serializer.save(owner=request.user)  # surface_m2 calculée dans Model.save()

        overlaps = parcelle.overlapping()
        data = ParcellePublicSerializer(parcelle).data
        data["overlaps"] = OverlapSerializer(overlaps, many=True).data
        data["overlap_count"] = overlaps.count()
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def check_overlap(self, request):
        """Reçoit une géométrie GeoJSON et renvoie les parcelles en conflit,
        sans rien enregistrer. Utile pour avertir l'utilisateur en temps réel."""
        geom_data = request.data.get("geometry")
        if not geom_data:
            return Response(
                {"detail": "Champ 'geometry' (GeoJSON) requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            import json

            geom = GEOSGeometry(json.dumps(geom_data), srid=4326)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Géométrie invalide : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        overlaps = (
            Parcelle.objects.filter(geometry__intersects=geom)
            .exclude(status=Parcelle.Status.REJECTED)
        )
        return Response(
            {
                "overlap_count": overlaps.count(),
                "overlaps": OverlapSerializer(overlaps, many=True).data,
            }
        )

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

    @action(detail=True, methods=["get"], url_path="suggest_crs")
    def suggest_crs(self, request, pk=None):
        """Propose automatiquement le système de coordonnées adapté à la
        localité de la parcelle (s'adapte à n'importe quelle région)."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()
        c = parcelle.geometry.centroid
        return Response(
            {
                "suggested_epsg": suggest_utm_epsg(c.x, c.y),
                "utm_zone": utm_zone_label(c.x, c.y),
                "lon": round(c.x, 6),
                "lat": round(c.y, 6),
            }
        )

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

        # Crée ou met à jour la délimitation (déclenche le signal de statut).
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
        return Response(
            {
                "delimitation_id": delim.id,
                "geometry": json.loads(poly.geojson),
                "surface_m2": surface,
            },
            status=status.HTTP_201_CREATED,
        )