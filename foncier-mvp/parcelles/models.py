from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models


class Parcelle(models.Model):
    """Une parcelle = un polygone géolocalisé + un statut + un niveau de fiabilité."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SUBMITTED = "submitted", "Soumise"
        DELIMITING = "delimiting", "En délimitation (géomètre)"
        VERIFYING = "verifying", "En vérification (notaire/cadastre)"
        VALIDATED = "validated", "Validée"
        REJECTED = "rejected", "Rejetée"
        DISPUTED = "disputed", "En litige"

    class Reliability(models.TextChoices):
        LOW = "low", "Faible"        # déclarée, non vérifiée
        MEDIUM = "medium", "Moyenne"  # délimitation faite, vérif. en cours
        HIGH = "high", "Élevée"       # délimitation + vérification validées

    # --- données plutôt publiques ---
    # Référence unique attribuée automatiquement (ex. PARC-2026-000042).
    # C'est l'identifiant officiel de la parcelle : ni choisi, ni falsifiable.
    reference = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    # Ancien champ nom conservé (optionnel, non utilisé côté public).
    name = models.CharField(max_length=255, blank=True, default="")
    # Localisation déclarée par le citoyen (un simple point). C'est le point de
    # départ : le polygone officiel est réalisé plus tard par le géomètre.
    declared_location = gis_models.PointField(srid=4326, null=True, blank=True)
    # Tracé officiel (polygone). Reste vide tant que le géomètre n'a pas délimité
    # et que la vérification n'est pas validée.
    geometry = gis_models.PolygonField(srid=4326, null=True, blank=True)  # WGS84 (lng/lat)
    surface_m2 = models.FloatField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED
    )
    reliability = models.CharField(
        max_length=10, choices=Reliability.choices, default=Reliability.LOW
    )
    name_owner_public = models.BooleanField(
        default=False, help_text="Le propriétaire consent à afficher son nom publiquement"
    )

    # --- données confidentielles ---
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="parcelles",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # GiST sur la géométrie -> requêtes spatiales rapides
            gis_models.Index(fields=["geometry"]),
        ]

    def __str__(self):
        return f"{self.reference or '(sans réf.)'} [{self.get_status_display()}]"

    def save(self, *args, **kwargs):
        # Surface en m² via une projection à aires égales (EPSG:6933).
        if self.geometry:
            self.surface_m2 = round(self.geometry.transform(6933, clone=True).area, 2)
        super().save(*args, **kwargs)
        # Attribue la référence officielle une fois le pk connu :
        # format PAYS-RÉGION-ANNÉE-NUMÉRO (ex. TG-1-26-000042).
        if not self.reference:
            from django.utils import timezone

            from .geoloc import resolve_location

            year = (self.created_at or timezone.now()).year % 100
            cc, region = ("XX", "0")
            if self.declared_location:
                cc, region = resolve_location(
                    self.declared_location.x, self.declared_location.y
                )
            ref = f"{cc}-{region}-{year:02d}-{self.pk:06d}"
            Parcelle.objects.filter(pk=self.pk).update(reference=ref)
            self.reference = ref

    def overlapping(self):
        """Parcelles existantes qui empiètent sur celle-ci (cœur anti-fraude).

        Exclut elle-même et les parcelles rejetées.
        """
        qs = Parcelle.objects.filter(geometry__intersects=self.geometry)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        return qs.exclude(status=self.Status.REJECTED)


class Delimitation(models.Model):
    """Tracé/validation géométrique réalisé par un GÉOMÈTRE."""

    parcelle = models.OneToOneField(
        Parcelle, on_delete=models.CASCADE, related_name="delimitation"
    )
    surveyor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="delimitations"
    )
    # Tracé corrigé/validé par le géomètre. Devient le tracé officiel de la
    # parcelle une fois la vérification approuvée (cf. signals.py).
    validated_geometry = gis_models.PolygonField(srid=4326, null=True, blank=True)
    # Points de bornage saisis (tableau B1, B2… avec X/Y dans le système source),
    # conservés pour la traçabilité.
    boundary_points = models.JSONField(null=True, blank=True)
    # Code EPSG du système de coordonnées d'origine du plan (ex. 32631 = UTM 31N/WGS84).
    source_epsg = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Document(models.Model):
    """Pièce justificative CONFIDENTIELLE (jamais exposée publiquement)."""

    class DocType(models.TextChoices):
        TITLE = "title", "Titre foncier"
        DEED = "deed", "Acte de vente"
        PLAN = "plan", "Plan / bornage"
        ID = "id", "Pièce d'identité"
        OTHER = "other", "Autre"

    parcelle = models.ForeignKey(
        Parcelle, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    file = models.FileField(upload_to="documents/")  # PROTOTYPE -> S3 chiffré en prod
    sha256 = models.CharField(max_length=64, blank=True)  # intégrité
    created_at = models.DateTimeField(auto_now_add=True)


class VerificationDossier(models.Model):
    """Vérification juridique par un NOTAIRE / agent de CADASTRE."""

    class Decision(models.TextChoices):
        PENDING = "pending", "En attente"
        APPROVED = "approved", "Approuvée"
        REJECTED = "rejected", "Rejetée"
        MORE_INFO = "more_info", "Complément demandé"

    parcelle = models.OneToOneField(
        Parcelle, on_delete=models.CASCADE, related_name="verification"
    )
    notary = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="verifications",
        null=True,
        blank=True,
    )
    decision = models.CharField(
        max_length=20, choices=Decision.choices, default=Decision.PENDING
    )
    comments = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)