from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Parcelle


class ParcellePublicSerializer(GeoFeatureModelSerializer):
    """Sortie GeoJSON PUBLIQUE : uniquement les champs visibles par tous.

    (Pas de documents, pas de description, pas de propriétaire.)
    """

    class Meta:
        model = Parcelle
        geo_field = "geometry"
        fields = ["id", "name", "status", "reliability", "surface_m2"]


class ParcelleCreateSerializer(GeoFeatureModelSerializer):
    """Entrée GeoJSON pour créer une parcelle (depuis la carte)."""

    class Meta:
        model = Parcelle
        geo_field = "geometry"
        fields = ["id", "name", "description", "status", "reliability", "surface_m2"]
        read_only_fields = ["status", "reliability", "surface_m2"]


class OverlapSerializer(serializers.Serializer):
    """Petit format pour signaler une parcelle en conflit."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()
