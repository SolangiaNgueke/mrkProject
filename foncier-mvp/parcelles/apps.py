from django.apps import AppConfig


class ParcellesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "parcelles"

    def ready(self):
        from . import signals  # noqa: F401
