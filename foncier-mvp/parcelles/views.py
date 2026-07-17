import hashlib
import json

from django.contrib.gis.geos import GEOSGeometry
from django.http import FileResponse, Http404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .geo import polygon_from_points, suggest_utm_epsg, utm_zone_label
from .models import Delimitation, Document, Parcelle
from .permissions import IsOwnerOrStaffOrReadOnly
from .serializers import (
    DocumentSerializer,
    OverlapSerializer,
    ParcellePublicSerializer,
    ParcelleSubmitSerializer,
)


class ParcelleViewSet(viewsets.ModelViewSet):
    """API des parcelles.

    VISIBILITÉ (règle centrale) :
      - Publiques : uniquement les parcelles VALIDÉES (double validation) et celles
        EN LITIGE confirmé par un vérificateur.
      - Privées : brouillons, soumises, en vérification, rejetées -> visibles seulement
        par leur propriétaire et les vérificateurs (notaire/cadastre/géomètre/admin).
    La détection de chevauchement, elle, compare contre TOUTES les parcelles
    (anti-fraude), mais anonymise les conflits avec des parcelles non publiques.
    """

    # Statuts affichés au grand public.
    # Publiques dès qu'un géomètre les a tracées : en vérification (bleu),
    # validées (vert) ou en litige (rouge). Les simples soumissions (point,
    # sans tracé) restent privées.
    PUBLIC_STATUSES = (
        Parcelle.Status.VERIFYING,
        Parcelle.Status.VALIDATED,
        Parcelle.Status.DISPUTED,
    )

    permission_classes = [IsOwnerOrStaffOrReadOnly]

    @staticmethod
    def _is_verifier(user):
        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or user.role in (user.Role.NOTARY, user.Role.SURVEYOR, user.Role.ADMIN)
            )
        )

    def get_queryset(self):
        """Ne renvoie que ce que l'utilisateur a le droit de voir."""
        qs = Parcelle.objects.all().order_by("-created_at")
        user = self.request.user

        # Carte publique (liste) : uniquement les parcelles publiques AVEC un tracé
        # officiel (donc validées). Les soumissions sans polygone n'y figurent pas.
        if self.action == "list":
            return qs.filter(status__in=self.PUBLIC_STATUSES, geometry__isnull=False)

        # Les vérificateurs voient tout (ils doivent instruire les dossiers).
        if self._is_verifier(user):
            return qs

        public = qs.filter(status__in=self.PUBLIC_STATUSES)
        if user and user.is_authenticated:
            # Le propriétaire voit en plus SES propres parcelles (ses soumissions).
            return (public | qs.filter(owner=user)).distinct()
        return public

    def get_serializer_class(self):
        if self.action == "create":
            return ParcelleSubmitSerializer  # citoyen : une localisation (point)
        return ParcellePublicSerializer

    def _overlap_payload(self, overlaps, user):
        """Détaille les conflits publics ou appartenant à l'utilisateur ;
        anonymise les autres (ne jamais révéler l'existence détaillée d'une
        parcelle privée d'autrui)."""
        visible, anonymous = [], 0
        for p in overlaps:
            is_public = p.status in self.PUBLIC_STATUSES
            is_mine = bool(user and user.is_authenticated and p.owner_id == user.id)
            if is_public or is_mine or self._is_verifier(user):
                visible.append(p)
            else:
                anonymous += 1
        return {
            "overlap_count": len(visible) + anonymous,
            "overlaps": OverlapSerializer(visible, many=True).data,
            "anonymous_overlaps": anonymous,
        }

    def create(self, request, *args, **kwargs):
        """Le citoyen soumet une LOCALISATION (point) + un nom. Pas de tracé :
        le polygone sera réalisé par le géomètre. La parcelle est privée
        (statut « soumise ») jusqu'à validation."""
        serializer = ParcelleSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parcelle = serializer.save(
            owner=request.user, status=Parcelle.Status.SUBMITTED
        )

        data = ParcelleSubmitSerializer(parcelle).data
        # Avertissement ANONYME : la localisation tombe-t-elle dans une parcelle
        # déjà validée ? (on ne révèle aucun détail).
        already = False
        if parcelle.declared_location:
            already = Parcelle.objects.filter(
                status=Parcelle.Status.VALIDATED,
                geometry__contains=parcelle.declared_location,
            ).exists()
        data["already_registered_zone"] = already
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def check_overlap(self, request):
        """Teste un polygone SANS l'enregistrer.

        Compare contre TOUTES les parcelles (y compris privées) pour ne rien
        laisser passer, mais n'expose pas les détails des parcelles privées d'autrui.
        """
        geom_data = request.data.get("geometry")
        if not geom_data:
            return Response(
                {"detail": "Champ 'geometry' (GeoJSON) requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            geom = GEOSGeometry(json.dumps(geom_data), srid=4326)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Géométrie invalide : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        overlaps = Parcelle.objects.filter(geometry__intersects=geom).exclude(
            status=Parcelle.Status.REJECTED
        )
        return Response(self._overlap_payload(overlaps, request.user))

    # ------------------------------------------------------------------ #
    #  Documents confidentiels                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _has_full_doc_access(user, parcelle):
        """Accès COMPLET aux documents : propriétaire, notaire/cadastre, admin."""
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or user.role in (user.Role.NOTARY, user.Role.ADMIN):
            return True
        return parcelle.owner_id == user.id

    @classmethod
    def _can_read_doc(cls, user, parcelle, doc):
        """Peut lire CE document précis.

        Le géomètre n'accède QU'aux documents techniques (plan / bornage),
        jamais aux titres ni aux pièces d'identité (moindre privilège)."""
        if cls._has_full_doc_access(user, parcelle):
            return True
        if (
            user
            and user.is_authenticated
            and user.role == user.Role.SURVEYOR
            and doc.doc_type == Document.DocType.PLAN
        ):
            return True
        return False

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="documents",
        parser_classes=[MultiPartParser, FormParser],
    )
    def documents(self, request, pk=None):
        """GET  -> liste les documents accessibles selon le rôle
        POST -> ajoute un document (propriétaire ou admin), calcule le hash SHA-256."""
        parcelle = self.get_object()  # vérifie déjà les permissions d'objet
        user = request.user

        if request.method == "GET":
            if self._has_full_doc_access(user, parcelle):
                docs = parcelle.documents.all().order_by("-created_at")
            elif user.is_authenticated and user.role == user.Role.SURVEYOR:
                # Géomètre : uniquement les plans / bornages.
                docs = parcelle.documents.filter(
                    doc_type=Document.DocType.PLAN
                ).order_by("-created_at")
            else:
                return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
            return Response(
                DocumentSerializer(docs, many=True, context={"request": request}).data
            )

        # POST : seul le propriétaire (ou un admin) peut ajouter une pièce.
        is_owner = parcelle.owner_id == user.id
        if not (is_owner or user.is_superuser or user.role == user.Role.ADMIN):
            return Response(
                {"detail": "Seul le propriétaire peut ajouter des documents."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = DocumentSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        doc = serializer.save(parcelle=parcelle)

        # Calcul du hash SHA-256 pour garantir l'intégrité du fichier.
        hasher = hashlib.sha256()
        for chunk in doc.file.chunks():
            hasher.update(chunk)
        doc.sha256 = hasher.hexdigest()
        doc.save(update_fields=["sha256"])

        return Response(
            DocumentSerializer(doc, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path=r"documents/(?P<doc_id>[0-9]+)/download",
        url_name="download-document",
    )
    def download_document(self, request, pk=None, doc_id=None):
        """Téléchargement protégé : aucun lien public direct.
        L'accès dépend du rôle ET du type de document (géomètre = plans seulement)."""
        parcelle = self.get_object()
        try:
            doc = parcelle.documents.get(pk=doc_id)
        except Document.DoesNotExist:
            raise Http404
        if not self._can_read_doc(request.user, parcelle, doc):
            return Response({"detail": "Accès refusé."}, status=status.HTTP_403_FORBIDDEN)
        filename = doc.file.name.split("/")[-1]
        return FileResponse(doc.file.open("rb"), as_attachment=True, filename=filename)

    # ------------------------------------------------------------------ #
    #  Délimitation par coordonnées (géomètre)                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_surveyor(user):
        return bool(
            user
            and user.is_authenticated
            and (user.is_superuser or user.role in (user.Role.SURVEYOR, user.Role.ADMIN))
        )

    @action(detail=True, methods=["get"], url_path="suggest_crs")
    def suggest_crs(self, request, pk=None):
        """Propose automatiquement le système de coordonnées adapté à la
        localité de la parcelle (s'adapte à n'importe quelle région)."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()
        # Référence : le polygone s'il existe, sinon la localisation déclarée.
        ref = parcelle.geometry.centroid if parcelle.geometry else parcelle.declared_location
        if ref is None:
            return Response(
                {"detail": "Aucune localisation pour cette parcelle."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "suggested_epsg": suggest_utm_epsg(ref.x, ref.y),
                "utm_zone": utm_zone_label(ref.x, ref.y),
                "lon": round(ref.x, 6),
                "lat": round(ref.y, 6),
            }
        )

    @action(detail=True, methods=["post"], url_path="preview_delimitation")
    def preview_delimitation(self, request, pk=None):
        """Calcule et renvoie le polygone SANS rien enregistrer (prévisualisation)."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        self.get_object()

        source_epsg = request.data.get("source_epsg")
        points = request.data.get("points")
        if not source_epsg or not points:
            return Response(
                {"detail": "Champs 'source_epsg' et 'points' requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            poly = polygon_from_points(points, source_epsg)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Coordonnées invalides : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        surface = round(poly.transform(6933, clone=True).area, 2)
        return Response({"geometry": json.loads(poly.geojson), "surface_m2": surface})

    @action(detail=True, methods=["post"], url_path="delimitation_from_points")
    def delimitation_from_points(self, request, pk=None):
        """Construit la délimitation du géomètre à partir d'un tableau de points
        (X/Y dans le système `source_epsg`), reprojetée en WGS84."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        parcelle = self.get_object()

        source_epsg = request.data.get("source_epsg")
        points = request.data.get("points")
        if not source_epsg or not points:
            return Response(
                {"detail": "Champs 'source_epsg' et 'points' requis."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            poly = polygon_from_points(points, source_epsg)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"Coordonnées invalides : {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Le tracé du géomètre devient la géométrie de la parcelle : elle
        # apparaît immédiatement sur la carte (bleu) et sert à détecter les conflits.
        parcelle.geometry = poly
        parcelle.save(update_fields=["geometry", "surface_m2", "updated_at"])

        # Crée ou met à jour la délimitation (déclenche le signal de statut).
        delim, _ = Delimitation.objects.update_or_create(
            parcelle=parcelle,
            defaults={
                "surveyor": request.user,
                "validated_geometry": poly,
                "boundary_points": points,
                "source_epsg": int(source_epsg),
            },
        )
        surface = round(poly.transform(6933, clone=True).area, 2)

        # Détection AUTOMATIQUE des litiges : crée les alertes (Conflit) pour
        # l'administrateur, passe les parcelles concernées en rouge, résout ce
        # qui ne se chevauche plus. (Réponse au géomètre : conflit anonymisé.)
        from .signals import recompute_conflicts

        recompute_conflicts(parcelle)

        ids = set(
            Parcelle.objects.filter(geometry__intersects=poly)
            .exclude(pk=parcelle.pk)
            .exclude(status=Parcelle.Status.REJECTED)
            .values_list("pk", flat=True)
        )
        overlap = self._overlap_payload(Parcelle.objects.filter(pk__in=ids), request.user)

        data = {
            "delimitation_id": delim.id,
            "geometry": json.loads(poly.geojson),
            "surface_m2": surface,
        }
        data.update(overlap)
        return Response(data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="ocr_plan",
        parser_classes=[MultiPartParser, FormParser],
    )
    def ocr_plan(self, request, pk=None):
        """Lit un plan (image) et renvoie les points de bornage détectés.
        NE sauvegarde rien : le géomètre vérifie/corrige, puis appelle
        delimitation_from_points. L'OCR ne fait que pré-remplir."""
        if not self._is_surveyor(request.user):
            return Response({"detail": "Réservé au géomètre."}, status=status.HTTP_403_FORBIDDEN)
        self.get_object()  # vérifie l'existence de la parcelle

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "Aucun fichier fourni."}, status=status.HTTP_400_BAD_REQUEST)

        from .ocr import extract_boundary_points

        try:
            points = extract_boundary_points(upload.read())
        except Exception as exc:  # noqa: BLE001
            return Response(
                {"detail": f"OCR indisponible : {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"count": len(points), "points": points})