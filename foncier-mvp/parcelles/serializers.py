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
        fields = ["id", "reference", "status", "reliability", "surface_m2"]


class ParcelleSubmitSerializer(GeoFeatureModelSerializer):
    """Entrée citoyen : une LOCALISATION (point). Aucun nom : la référence
    officielle est attribuée automatiquement. Le tracé sera fait par le géomètre."""

    class Meta:
        model = Parcelle
        geo_field = "declared_location"
        fields = ["id", "reference", "status", "reliability"]
        read_only_fields = ["reference", "status", "reliability"]


class OverlapSerializer(serializers.Serializer):
    """Petit format pour signaler une parcelle en conflit."""

    id = serializers.IntegerField()
    reference = serializers.CharField()
    status = serializers.CharField()


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


class ParcelleMineSerializer(serializers.ModelSerializer):
    """Liste des parcelles du propriétaire connecté (page « Mes parcelles »)."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    reliability_display = serializers.CharField(source="get_reliability_display", read_only=True)

    class Meta:
        model = Parcelle
        fields = [
            "id", "reference", "status", "status_display",
            "reliability", "reliability_display", "surface_m2", "created_at",
        ]