from rest_framework.routers import DefaultRouter

from .views import ParcelleViewSet

router = DefaultRouter()
router.register(r"parcelles", ParcelleViewSet, basename="parcelle")

urlpatterns = router.urls
