"""Crée les groupes 'Géomètres' et 'Notaires' avec le STRICT minimum de
permissions (principe du moindre privilège). Commande idempotente.

Usage : python manage.py setup_roles
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Géomètre : voit les parcelles (lecture) + gère les délimitations + consulte
# les documents (filtrés aux plans/bornages dans l'admin). RIEN d'autre.
GEOMETRE_PERMS = [
    "view_parcelle",
    "view_delimitation", "add_delimitation", "change_delimitation",
    "view_document",
]

# Notaire/cadastre : voit parcelles + documents + gère les vérifications. PAS les délimitations, PAS les comptes.
NOTAIRE_PERMS = [
    "view_parcelle",
    "view_document",
    "view_verificationdossier", "add_verificationdossier", "change_verificationdossier",
]


class Command(BaseCommand):
    help = "Configure les groupes métier avec le minimum de permissions (moindre privilège)."

    def handle(self, *args, **options):
        self._make_group("Géomètres", GEOMETRE_PERMS)
        self._make_group("Notaires", NOTAIRE_PERMS)
        self.stdout.write(self.style.SUCCESS("Groupes configurés (moindre privilège)."))

    def _make_group(self, name, codenames):
        group, _ = Group.objects.get_or_create(name=name)
        perms = Permission.objects.filter(
            codename__in=codenames, content_type__app_label="parcelles"
        )
        group.permissions.set(perms)
        self.stdout.write(f"  {name} : {perms.count()} permission(s)")