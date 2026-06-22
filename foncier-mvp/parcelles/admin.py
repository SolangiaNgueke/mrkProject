from django.contrib.gis import admin

from .models import Delimitation, Document, Parcelle, VerificationDossier


@admin.register(Parcelle)
class ParcelleAdmin(admin.GISModelAdmin):
    list_display = ("name", "status", "reliability", "surface_m2", "owner", "created_at")
    list_filter = ("status", "reliability")
    search_fields = ("name", "description")


admin.site.register(Delimitation)
admin.site.register(Document)
admin.site.register(VerificationDossier)
