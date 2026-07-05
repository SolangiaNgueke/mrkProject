"""Quand on attribue un rôle à un utilisateur, on lui donne automatiquement
le minimum d'accès nécessaire (moindre privilège) :

- Géomètre  -> compte staff + groupe "Géomètres"
- Notaire   -> compte staff + groupe "Notaires"
- Admin / superuser -> staff
- Citoyen   -> aucun accès à l'admin

Les permissions des groupes sont définies par la commande `setup_roles`.
"""

from django.contrib.auth.models import Group
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User

ROLE_GROUP = {
    User.Role.SURVEYOR: "Géomètres",
    User.Role.NOTARY: "Notaires",
}


@receiver(post_save, sender=User)
def sync_role_access(sender, instance, **kwargs):
    group_name = ROLE_GROUP.get(instance.role)

    # Doit-il pouvoir accéder à l'admin ?
    desired_staff = bool(group_name) or instance.is_superuser or instance.role == User.Role.ADMIN
    if instance.is_staff != desired_staff:
        # .update() évite de redéclencher ce signal (pas de récursion).
        User.objects.filter(pk=instance.pk).update(is_staff=desired_staff)

    # On retire d'abord des groupes métier, puis on ajoute le bon (un seul).
    metier = Group.objects.filter(name__in=list(ROLE_GROUP.values()))
    instance.groups.remove(*metier)
    if group_name:
        grp, _ = Group.objects.get_or_create(name=group_name)
        instance.groups.add(grp)