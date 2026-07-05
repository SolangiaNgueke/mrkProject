from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Gestion des comptes — réservée aux admins (les autres rôles n'ont
    aucune permission sur le modèle User, donc ne le voient pas)."""

    list_display = ("username", "email", "role", "is_staff", "is_superuser")
    list_filter = ("role", "is_staff", "is_superuser")

    # On ajoute les champs métier au formulaire (sinon ils n'apparaissent pas).
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Profil foncier", {"fields": ("role", "phone", "kyc_verified")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Profil foncier", {"fields": ("role",)}),
    )