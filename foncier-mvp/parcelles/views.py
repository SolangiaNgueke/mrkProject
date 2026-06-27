from django.contrib.gis.geos import GEOSGeometry
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Parcelle
from .permissions import IsOwnerOrStaffOrReadOnly
from .serializers import (
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