from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AdminBoundary,
    Conflit,
    Delimitation,
    Document,
    Parcelle,
    Signalement,
    VerificationDossier,
)


@admin.register(Parcelle)
class ParcelleAdmin(GISModelAdmin):
    """Parcelles : consultables par les rôles métier, mais modifiables
    UNIQUEMENT par un superuser. Le statut et la fiabilité sont calculés
    automatiquement (signaux), donc personne ne les édite à la main."""

    list_display = ("id", "reference", "status", "reliability", "surface_m2", "owner", "created_at")
    list_filter = ("status", "reliability")
    search_fields = ("reference",)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Delimitation)
class DelimitationAdmin(GISModelAdmin):
    """Réservé au géomètre : il choisit une parcelle et CORRIGE son tracé sur une carte.
    Le géomètre est forcément l'utilisateur connecté (responsabilité, traçabilité).
    Le tracé validé devient le tracé officiel une fois la vérification approuvée."""

    list_display = ("id", "parcelle", "surveyor", "created_at")
    exclude = ("surveyor",)            # non choisi à la main
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        if not obj.surveyor_id:
            obj.surveyor = request.user   # toujours le géomètre connecté
        # Si le géomètre n'a pas encore dessiné, on part du tracé déclaré par le
        # citoyen : il pourra ensuite l'ajuster sur la carte.
        if not obj.validated_geometry and obj.parcelle_id:
            obj.validated_geometry = obj.parcelle.geometry
        super().save_model(request, obj, form, change)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """Documents confidentiels : LECTURE SEULE dans l'admin.
    Ils sont déposés par le propriétaire via l'application. Le notaire les
    consulte ici ; le fichier reste servi par la route protégée (pas de lien public).

    Moindre privilège : le GÉOMÈTRE ne voit QUE les documents techniques
    (plan / bornage), jamais les titres ou pièces d'identité."""

    list_display = ("id", "parcelle", "doc_type", "short_hash", "telecharger")
    readonly_fields = ("parcelle", "doc_type", "sha256", "created_at", "telecharger")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        u = request.user
        if u.is_superuser or u.role in (u.Role.NOTARY, u.Role.ADMIN):
            return qs
        if u.role == u.Role.SURVEYOR:
            return qs.filter(doc_type=Document.DocType.PLAN)
        return qs.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # lecture seule

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Hash (SHA-256)")
    def short_hash(self, obj):
        return (obj.sha256[:16] + "…") if obj.sha256 else "—"

    @admin.display(description="Fichier")
    def telecharger(self, obj):
        # Route protégée : accessible seulement si l'utilisateur connecté a le droit.
        url = f"/api/parcelles/{obj.parcelle_id}/documents/{obj.id}/download/"
        return format_html('<a href="{}" target="_blank">Consulter / télécharger</a>', url)


@admin.register(VerificationDossier)
class VerificationDossierAdmin(admin.ModelAdmin):
    """Réservé au notaire/cadastre : il choisit une parcelle et rend sa décision.
    Le notaire est forcément l'utilisateur connecté ; la date de décision est posée
    automatiquement."""

    list_display = ("id", "parcelle", "decision", "notary", "decided_at")
    list_filter = ("decision",)
    exclude = ("notary", "decided_at")

    def save_model(self, request, obj, form, change):
        if not obj.notary_id:
            obj.notary = request.user
        if obj.decision != VerificationDossier.Decision.PENDING and not obj.decided_at:
            obj.decided_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(Conflit)
class ConflitAdmin(admin.ModelAdmin):
    """Alerte administrateur : chevauchements détectés automatiquement.
    Un chevauchement minime peut venir d'une erreur de saisie de coordonnées :
    l'administrateur enquête. Le conflit se résout tout seul quand il disparaît."""

    list_display = ("id", "parcelle_a", "parcelle_b", "overlap_area_m2", "etat", "created_at")
    list_filter = ("resolved_at",)
    readonly_fields = ("parcelle_a", "parcelle_b", "overlap_area_m2", "created_at", "resolved_at")

    def has_add_permission(self, request):
        return False  # créé automatiquement par le système

    @admin.display(description="État", boolean=True)
    def etat(self, obj):
        # True = actif (non résolu)
        return obj.resolved_at is None


@admin.register(Signalement)
class SignalementAdmin(admin.ModelAdmin):
    """Alerte administrateur : signalements communautaires à examiner.
    L'admin décide de la suite (il ne met pas la parcelle en litige automatiquement)."""

    list_display = ("id", "parcelle", "motif", "reporter", "en_attente", "created_at")
    list_filter = ("motif", "resolved_at")
    readonly_fields = ("parcelle", "reporter", "motif", "comment", "created_at")
    actions = ("marquer_traite",)

    def has_add_permission(self, request):
        return False  # créé par les utilisateurs via l'application

    @admin.display(description="À examiner", boolean=True)
    def en_attente(self, obj):
        return obj.resolved_at is None

    @admin.action(description="Marquer comme traité")
    def marquer_traite(self, request, queryset):
        from django.utils import timezone

        queryset.filter(resolved_at__isnull=True).update(resolved_at=timezone.now())


@admin.register(AdminBoundary)
class AdminBoundaryAdmin(admin.ModelAdmin):
    """Limites administratives officielles (détection exacte pays/région)."""

    list_display = ("name", "country_code", "region_code", "level")
    list_filter = ("country_code", "level")
    search_fields = ("name",)

    def has_module_permission(self, request):
        return request.user.is_superuser