from rest_framework import permissions


class IsOwnerOrStaffOrReadOnly(permissions.BasePermission):
    """Lecture ouverte à tous.

    Écriture (modifier / supprimer) réservée :
      - au propriétaire de la parcelle, OU
      - aux rôles notaire/cadastre et admin (qui interviennent dans la vérification),
        ainsi qu'au staff Django.
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_staff or user.role in (user.Role.NOTARY, user.Role.ADMIN):
            return True
        return obj.owner_id == user.id