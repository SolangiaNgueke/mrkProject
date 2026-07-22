from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.gis.admin import GISModelAdmin
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AdminBoundary,
    AuditLog,
    Conflit,
    Delimitation,
    Document,
    Parcelle,
    Signalement,
    VerificationDossier,
)

Role = get_user_model().Role


def _role(user):
    """Rôle de l'utilisateur, ou None pour un visiteur non connecté.
    Évite une erreur 500 quand l'admin est consulté sans être authentifié."""
    if not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "role", None)


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
        r = _role(u)
        if u.is_superuser or r in (Role.NOTARY, Role.ADMIN):
            return qs
        if r == Role.SURVEYOR:
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

        from .audit import journaliser

        journaliser(
            "verification", actor=request.user, parcelle=obj.parcelle,
            decision=obj.decision,
        )


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

    list_display = ("id", "type_demande", "parcelle", "motif", "contact_email", "en_attente", "created_at")
    list_filter = ("type_demande", "motif", "resolved_at")
    search_fields = ("contact_email", "parcelle__reference")
    readonly_fields = (
        "type_demande", "parcelle", "reporter", "motif", "comment",
        "contact_email", "contact_phone", "created_at",
    )
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


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Journal d'audit : consultation seule, aucune modification possible."""

    list_display = ("created_at", "action", "actor_label", "parcelle_ref", "resume")
    list_filter = ("action", "created_at")
    search_fields = ("actor_label", "parcelle_ref")
    readonly_fields = (
        "action", "actor", "actor_label", "parcelle", "parcelle_ref",
        "details", "created_at", "prev_hash", "entry_hash",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # inaltérable, même pour un superuser

    def has_module_permission(self, request):
        u = request.user
        return bool(u.is_superuser or _role(u) == Role.ADMIN)

    @admin.display(description="Détails")
    def resume(self, obj):
        if not obj.details:
            return "—"
        return ", ".join(f"{k} : {v}" for k, v in list(obj.details.items())[:3]) 