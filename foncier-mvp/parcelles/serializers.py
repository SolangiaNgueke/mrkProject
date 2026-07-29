from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Document, Parcelle


class ParcellePublicSerializer(GeoFeatureModelSerializer):
    """Sortie GeoJSON PUBLIQUE : uniquement les champs visibles par tous.

    Le nom du propriétaire n'apparaît QUE s'il y a explicitement consenti
    (name_owner_public). Les documents et la description privée ne sortent jamais.
    """

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    reliability_display = serializers.CharField(source="get_reliability_display", read_only=True)
    surface_ha = serializers.SerializerMethodField()
    perimetre_m = serializers.SerializerMethodField()
    nb_bornes = serializers.SerializerMethodField()
    region = serializers.SerializerMethodField()
    proprietaire = serializers.SerializerMethodField()
    date_declaration = serializers.DateTimeField(source="created_at", read_only=True)
    date_maj = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = Parcelle
        geo_field = "geometry"
        fields = [
            "id", "reference", "status", "status_display",
            "reliability", "reliability_display",
            "surface_m2", "surface_ha", "perimetre_m", "nb_bornes",
            "region", "proprietaire", "date_declaration", "date_maj",
        ]

    def get_surface_ha(self, obj):
        return round(obj.surface_m2 / 10000, 4) if obj.surface_m2 else None

    def get_perimetre_m(self, obj):
        """Longueur du contour, en mètres (projection à aires égales)."""
        if not obj.geometry:
            return None
        try:
            return round(obj.geometry.transform(6933, clone=True).length, 1)
        except Exception:  # noqa: BLE001
            return None

    def get_nb_bornes(self, obj):
        """Nombre de sommets du terrain (le 1er point est répété à la fin)."""
        if not obj.geometry:
            return None
        return max(len(obj.geometry.coords[0]) - 1, 0)

    def get_region(self, obj):
        """Pays et région, déduits de la référence (ex. TG-1-26-000042)."""
        if not obj.reference:
            return None
        parts = obj.reference.split("-")
        return f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else None

    def get_proprietaire(self, obj):
        # Uniquement si le propriétaire a donné son accord.
        if obj.name_owner_public and obj.owner:
            return obj.owner.get_full_name() or obj.owner.username
        return None


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
    doc_type_display = serializers.CharField(source="get_doc_type_display", read_only=True)

    class Meta:
        model = Document
        fields = ["id", "doc_type", "doc_type_display", "file", "sha256", "created_at", "download_url"]
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


class ParcelleFileAttenteSerializer(serializers.ModelSerializer):
    """File de travail du géomètre : parcelles à tracer ou déjà tracées par lui."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    a_un_plan = serializers.SerializerMethodField()
    nb_documents = serializers.SerializerMethodField()
    localisation = serializers.SerializerMethodField()
    deja_trace = serializers.SerializerMethodField()

    class Meta:
        model = Parcelle
        fields = [
            "id", "reference", "status", "status_display",
            "a_un_plan", "nb_documents", "localisation", "deja_trace",
            "surface_m2", "created_at",
        ]

    def get_a_un_plan(self, obj):
        """Un plan de bornage a-t-il été déposé ? (seul type visible du géomètre)"""
        return obj.documents.filter(doc_type=Document.DocType.PLAN).exists()

    def get_nb_documents(self, obj):
        return obj.documents.count()

    def get_localisation(self, obj):
        pt = obj.declared_location
        if not pt:
            return None
        return {"lon": round(pt.x, 6), "lat": round(pt.y, 6)}

    def get_deja_trace(self, obj):
        return hasattr(obj, "delimitation")


class ParcelleFileNotaireSerializer(serializers.ModelSerializer):
    """File de travail du notaire : parcelles tracées à valider juridiquement."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    nb_documents = serializers.SerializerMethodField()
    surface_ha = serializers.SerializerMethodField()
    decision = serializers.SerializerMethodField()

    class Meta:
        model = Parcelle
        fields = [
            "id", "reference", "status", "status_display",
            "surface_m2", "surface_ha", "nb_documents", "decision", "updated_at",
        ]

    def get_nb_documents(self, obj):
        return obj.documents.count()

    def get_surface_ha(self, obj):
        return round(obj.surface_m2 / 10000, 4) if obj.surface_m2 else None

    def get_decision(self, obj):
        verif = getattr(obj, "verification", None)
        return verif.decision if verif else "pending"