from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Document, Parcelle


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


class DocumentSerializer(serializers.ModelSerializer):
    """Document justificatif CONFIDENTIEL.

    Le fichier (`file`) est en écriture seule : on ne renvoie jamais le chemin
    brut du média. Le téléchargement se fait via une route protégée (download_url).
    """

    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ["id", "doc_type", "file", "sha256", "created_at", "download_url"]
        read_only_fields = ["sha256", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

    def get_download_url(self, obj):
        request = self.context.get("request")
        path = f"/api/parcelles/{obj.parcelle_id}/documents/{obj.id}/download/"
        return request.build_absolute_uri(path) if request else path