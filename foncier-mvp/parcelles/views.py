import hashlib
 
from django.contrib.gis.geos import GEOSGeometry
from django.http import FileResponse, Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
 
from .models import Document, Parcelle
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
    def _can_read_docs(user, parcelle):
        """Lecture des documents : propriétaire OU vérificateur (notaire/cadastre/admin)."""
        if not (user and user.is_authenticated):
            return False
        if user.is_staff or user.role in (user.Role.NOTARY, user.Role.ADMIN):
            return True
        return parcelle.owner_id == user.id
 
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="documents",
        parser_classes=[MultiPartParser, FormParser],
    )
    def documents(self, request, pk=None):
        """GET  -> liste les documents (propriétaire + vérificateurs uniquement)
        POST -> ajoute un document (propriétaire ou admin), calcule le hash SHA-256."""
        parcelle = self.get_object()  # vérifie déjà les permissions d'objet
 
        if request.method == "GET":
            if not self._can_read_docs(request.user, parcelle):
                return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
            docs = parcelle.documents.all().order_by("-created_at")
            return Response(
                DocumentSerializer(docs, many=True, context={"request": request}).data
            )
 
        # POST : seul le propriétaire (ou un admin/staff) peut ajouter une pièce
        user = request.user
        is_owner = parcelle.owner_id == user.id
        if not (is_owner or user.is_staff or user.role == user.Role.ADMIN):
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
        L'accès est vérifié à chaque requête (propriétaire + vérificateurs)."""
        parcelle = self.get_object()
        if not self._can_read_docs(request.user, parcelle):
            return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
        try:
            doc = parcelle.documents.get(pk=doc_id)
        except Document.DoesNotExist:
            raise Http404
        filename = doc.file.name.split("/")[-1]
        return FileResponse(doc.file.open("rb"), as_attachment=True, filename=filename)
 
