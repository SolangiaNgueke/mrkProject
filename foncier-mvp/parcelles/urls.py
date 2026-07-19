from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import DashboardStats, ParcelleViewSet

router = DefaultRouter()
router.register(r"parcelles", ParcelleViewSet, basename="parcelle")

urlpatterns = router.urls + [
    path("stats/", DashboardStats.as_view(), name="dashboard-stats"),
]