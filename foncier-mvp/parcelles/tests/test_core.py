"""Tests automatisés des règles CRITIQUES de la plateforme foncière.

Lancer :  docker compose exec web python manage.py test parcelles

Ces tests vérifient les garanties qui font la valeur anti-fraude du produit :
visibilité publique, isolement des rôles (moindre privilège), workflow de
validation, anti-doublon et intégrité du journal d'audit. Si un futur changement
casse l'une de ces règles, le test échoue AVANT le déploiement.
"""

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import GEOSGeometry, Point, Polygon
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from parcelles.models import Delimitation, Document, Parcelle, VerificationDossier

User = get_user_model()


def _auth(client, user):
    """Authentifie le client de test avec le jeton de l'utilisateur."""
    token, _ = Token.objects.get_or_create(user=user)
    client.credentials(HTTP_AUTHORIZATION="Token " + token.key)


def _point_feature(lon, lat):
    """Corps GeoJSON attendu par l'API de déclaration (point)."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {},
    }


class BaseData(APITestCase):
    """Comptes de test partagés par les scénarios."""

    def setUp(self):
        self.citoyen = User.objects.create_user(
            username="citoyen", password="pw", role=User.Role.CITIZEN, email="c@x.tg"
        )
        self.autre_citoyen = User.objects.create_user(
            username="autre", password="pw", role=User.Role.CITIZEN
        )
        self.geometre = User.objects.create_user(
            username="geometre", password="pw", role=User.Role.SURVEYOR
        )
        self.notaire = User.objects.create_user(
            username="notaire", password="pw", role=User.Role.NOTARY
        )


class VisibilitePubliqueTests(BaseData):
    """La carte publique ne montre QUE les parcelles validées / en vérif / litige."""

    def test_parcelle_soumise_invisible_du_public(self):
        Parcelle.objects.create(owner=self.citoyen, declared_location=Point(1.2, 6.1),
                                status=Parcelle.Status.SUBMITTED)
        res = self.client.get("/api/parcelles/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["features"]), 0)  # rien de public

    def test_parcelle_validee_visible_du_public(self):
        Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.VALIDATED,
            geometry=Polygon(((1.2, 6.1), (1.2, 6.11), (1.21, 6.11), (1.2, 6.1))),
        )
        res = self.client.get("/api/parcelles/")
        self.assertEqual(len(res.data["features"]), 1)

    def test_reference_ne_fuit_pas_le_proprietaire(self):
        """La fiche publique ne révèle jamais le nom du propriétaire non consenti."""
        Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.VALIDATED, name_owner_public=False,
            geometry=Polygon(((1.2, 6.1), (1.2, 6.11), (1.21, 6.11), (1.2, 6.1))),
        )
        res = self.client.get("/api/parcelles/")
        props = res.data["features"][0]["properties"]
        self.assertIsNone(props.get("proprietaire"))


class DeclarationTests(BaseData):
    """Déclaration d'une parcelle : référence auto + anti-doublon."""

    def test_declaration_cree_parcelle_soumise_avec_reference(self):
        _auth(self.client, self.citoyen)
        res = self.client.post("/api/parcelles/", _point_feature(1.2255, 6.1319), format="json")
        self.assertEqual(res.status_code, 201)
        p = Parcelle.objects.get(pk=res.data["id"])
        self.assertEqual(p.status, Parcelle.Status.SUBMITTED)
        self.assertTrue(p.reference.startswith("TG-"))
        self.assertEqual(p.owner, self.citoyen)

    def test_anti_doublon_meme_point(self):
        """Deux déclarations identiques rapprochées ne créent qu'UNE parcelle."""
        _auth(self.client, self.citoyen)
        f = _point_feature(1.2255, 6.1319)
        r1 = self.client.post("/api/parcelles/", f, format="json")
        r2 = self.client.post("/api/parcelles/", f, format="json")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(Parcelle.objects.filter(owner=self.citoyen).count(), 1)
        self.assertTrue(r2.data.get("doublon_evite"))

    def test_declaration_exige_authentification(self):
        res = self.client.post("/api/parcelles/", _point_feature(1.2, 6.1), format="json")
        self.assertIn(res.status_code, (401, 403))


class RolesTests(BaseData):
    """Isolement des espaces réservés (moindre privilège)."""

    def test_espace_geometre_interdit_au_citoyen(self):
        _auth(self.client, self.citoyen)
        self.assertEqual(self.client.get("/api/parcelles/a_tracer/").status_code, 403)

    def test_espace_geometre_autorise_au_geometre(self):
        _auth(self.client, self.geometre)
        self.assertEqual(self.client.get("/api/parcelles/a_tracer/").status_code, 200)

    def test_espace_notaire_interdit_au_citoyen(self):
        _auth(self.client, self.citoyen)
        self.assertEqual(self.client.get("/api/parcelles/a_valider/").status_code, 403)

    def test_espace_notaire_autorise_au_notaire(self):
        _auth(self.client, self.notaire)
        self.assertEqual(self.client.get("/api/parcelles/a_valider/").status_code, 200)

    def test_mes_parcelles_isolees_par_utilisateur(self):
        Parcelle.objects.create(owner=self.citoyen, declared_location=Point(1.2, 6.1))
        Parcelle.objects.create(owner=self.autre_citoyen, declared_location=Point(1.3, 6.2))
        _auth(self.client, self.citoyen)
        res = self.client.get("/api/parcelles/mine/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)  # seulement les siennes


class DocumentsPrivilegeTests(BaseData):
    """Le géomètre n'accède QU'aux plans, jamais aux titres ni aux pièces d'identité."""

    def setUp(self):
        super().setUp()
        self.parcelle = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.VERIFYING,
        )
        from django.core.files.base import ContentFile

        self.plan = Document.objects.create(
            parcelle=self.parcelle, doc_type=Document.DocType.PLAN,
            file=ContentFile(b"plan", name="plan.pdf"),
        )
        self.titre = Document.objects.create(
            parcelle=self.parcelle, doc_type=Document.DocType.TITLE,
            file=ContentFile(b"titre", name="titre.pdf"),
        )

    def test_geometre_peut_lire_le_plan(self):
        _auth(self.client, self.geometre)
        url = f"/api/parcelles/{self.parcelle.id}/documents/{self.plan.id}/download/"
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_geometre_ne_peut_pas_lire_le_titre(self):
        _auth(self.client, self.geometre)
        url = f"/api/parcelles/{self.parcelle.id}/documents/{self.titre.id}/download/"
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_notaire_peut_lire_le_titre(self):
        _auth(self.client, self.notaire)
        url = f"/api/parcelles/{self.parcelle.id}/documents/{self.titre.id}/download/"
        self.assertEqual(self.client.get(url).status_code, 200)


class WorkflowValidationTests(BaseData):
    """La décision du notaire fait passer la parcelle au bon statut (via signaux)."""

    def _parcelle_tracee(self):
        poly = Polygon(((1.2, 6.1), (1.2, 6.11), (1.21, 6.11), (1.21, 6.1), (1.2, 6.1)))
        p = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.VERIFYING, geometry=poly,
        )
        Delimitation.objects.create(parcelle=p, surveyor=self.geometre,
                                    validated_geometry=poly, source_epsg=32631)
        return p

    def test_validation_notaire_passe_en_validee(self):
        p = self._parcelle_tracee()
        _auth(self.client, self.notaire)
        res = self.client.post(f"/api/parcelles/{p.id}/decision/",
                               {"decision": "approved"}, format="json")
        self.assertEqual(res.status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.status, Parcelle.Status.VALIDATED)

    def test_rejet_exige_un_motif(self):
        p = self._parcelle_tracee()
        _auth(self.client, self.notaire)
        res = self.client.post(f"/api/parcelles/{p.id}/decision/",
                               {"decision": "rejected"}, format="json")
        self.assertEqual(res.status_code, 400)  # motif manquant

    def test_rejet_avec_motif_passe_en_rejetee(self):
        p = self._parcelle_tracee()
        _auth(self.client, self.notaire)
        res = self.client.post(f"/api/parcelles/{p.id}/decision/",
                               {"decision": "rejected", "comments": "Documents incohérents"},
                               format="json")
        self.assertEqual(res.status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.status, Parcelle.Status.REJECTED)


class AuditTests(BaseData):
    """Le journal d'audit s'écrit et reste vérifiable (chaîne intègre)."""

    def test_journal_integre_apres_actions(self):
        from parcelles.audit import journaliser, verifier_integrite

        p = Parcelle.objects.create(owner=self.citoyen, declared_location=Point(1.2, 6.1))
        journaliser("parcelle_declaree", actor=self.citoyen, parcelle=p)
        journaliser("statut_change", parcelle=p, ancien="submitted", nouveau="verifying")
        ok, n, rupture = verifier_integrite()
        self.assertTrue(ok)
        self.assertGreaterEqual(n, 2)
        self.assertIsNone(rupture)


class OcrDossierTests(BaseData):
    """Le géomètre lit les documents du dossier (sans pouvoir les télécharger)."""

    def setUp(self):
        super().setUp()
        from django.core.files.base import ContentFile
        self.parcelle = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.VERIFYING,
        )
        self.titre = Document.objects.create(
            parcelle=self.parcelle, doc_type=Document.DocType.TITLE,
            file=ContentFile(b"titre", name="titre.pdf"),
        )

    def test_liste_documents_ocr_reservee_au_geometre(self):
        _auth(self.client, self.citoyen)
        r = self.client.get(f"/api/parcelles/{self.parcelle.id}/documents_ocr/")
        self.assertEqual(r.status_code, 403)

    def test_geometre_voit_tous_les_documents_du_dossier(self):
        _auth(self.client, self.geometre)
        r = self.client.get(f"/api/parcelles/{self.parcelle.id}/documents_ocr/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["documents"]), 1)  # le titre est listable pour OCR

    def test_ocr_document_reserve_au_geometre(self):
        _auth(self.client, self.citoyen)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/ocr_document/",
                             {"document_id": self.titre.id}, format="json")
        self.assertEqual(r.status_code, 403)


class TraceMainLeveeTests(BaseData):
    """Tracé à main levée : autonome, réservé au géomètre, passe en 'non vérifié'."""

    def setUp(self):
        super().setUp()
        self.parcelle = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.SUBMITTED,
        )
        self.ring = [[1.20, 6.10], [1.20, 6.11], [1.21, 6.11], [1.21, 6.10]]

    def test_main_levee_reservee_au_geometre(self):
        _auth(self.client, self.citoyen)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/delimitation_freehand/",
                             {"ring": self.ring}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_main_levee_cree_le_trace(self):
        _auth(self.client, self.geometre)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/delimitation_freehand/",
                             {"ring": self.ring}, format="json")
        self.assertEqual(r.status_code, 201)
        self.parcelle.refresh_from_db()
        self.assertIsNotNone(self.parcelle.geometry)
        self.assertEqual(self.parcelle.status, Parcelle.Status.VERIFYING)  # tracé = non vérifié

    def test_main_levee_exige_trois_sommets(self):
        _auth(self.client, self.geometre)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/delimitation_freehand/",
                             {"ring": [[1.2, 6.1], [1.2, 6.11]]}, format="json")
        self.assertEqual(r.status_code, 400)


class ImportTxtTests(BaseData):
    """Import d'un fichier .txt de coordonnées (réservé au géomètre)."""

    def setUp(self):
        super().setUp()
        self.parcelle = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.SUBMITTED,
        )

    def _fichier(self, contenu):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("coords.txt", contenu.encode(), content_type="text/plain")

    def test_import_txt_reserve_au_geometre(self):
        _auth(self.client, self.citoyen)
        f = self._fichier("X=308822.70 Y=756828.50 Z=0")
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/import_txt/",
                             {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 403)

    def test_import_txt_format_xyz(self):
        _auth(self.client, self.geometre)
        contenu = ("X=308822.7053  Y=756828.5067  Z=0.0000 "
                   "X=310133.8235  Y=747714.9881  Z=0.0000")
        f = self._fichier(contenu)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/import_txt/",
                             {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 2)
        self.assertAlmostEqual(r.data["points"][0]["x"], 308822.7053, places=3)

    def test_import_txt_format_colonnes(self):
        _auth(self.client, self.geometre)
        contenu = "B1,296167.00,697106.00\nB2,296173.30,697101.27\nB3,296166.00,697088.00"
        f = self._fichier(contenu)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/import_txt/",
                             {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 3)


class ZoneEtatTests(BaseData):
    """Zones de l'État : couche publique + alerte à la déclaration."""

    def setUp(self):
        super().setUp()
        from parcelles.models import ZoneEtat
        # Un carré autour de (1.20, 6.10) en WGS84.
        poly = "MULTIPOLYGON(((1.19 6.09, 1.19 6.12, 1.22 6.12, 1.22 6.09, 1.19 6.09)))"
        ZoneEtat.objects.create(name="Forêt test", type_zone="foret",
                                geometry=GEOSGeometry(poly, srid=4326))

    def test_zones_etat_publiques(self):
        r = self.client.get("/api/parcelles/zones_etat/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["features"]), 1)

    def test_alerte_declaration_dans_zone_etat(self):
        _auth(self.client, self.citoyen)
        # Point à l'intérieur de la forêt test.
        r = self.client.post("/api/parcelles/", _point_feature(1.205, 6.105), format="json")
        self.assertEqual(r.status_code, 201)
        self.assertIn("zone_etat", r.data)
        self.assertEqual(r.data["zone_etat"]["nom"], "Forêt test")

    def test_pas_d_alerte_hors_zone(self):
        _auth(self.client, self.citoyen)
        r = self.client.post("/api/parcelles/", _point_feature(1.50, 6.50), format="json")
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("zone_etat", r.data)


class NouveauTraceTests(BaseData):
    """Tracé autonome : le géomètre crée une parcelle vierge à tracer."""

    def test_nouveau_trace_reserve_au_geometre(self):
        _auth(self.client, self.citoyen)
        r = self.client.post("/api/parcelles/nouveau_trace/", {}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_nouveau_trace_cree_parcelle_vierge(self):
        _auth(self.client, self.geometre)
        r = self.client.post("/api/parcelles/nouveau_trace/",
                             {"lon": 1.2, "lat": 6.13}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["reference"].startswith("TG-"))
        p = Parcelle.objects.get(pk=r.data["id"])
        self.assertEqual(p.owner, self.geometre)


class OcrUnifieTests(BaseData):
    """L'endpoint OCR accepte aussi les fichiers .txt de coordonnées."""

    def setUp(self):
        super().setUp()
        self.parcelle = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.SUBMITTED,
        )

    def test_ocr_plan_lit_un_txt(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        contenu = ("X=308822.7053  Y=756828.5067  Z=0 "
                   "X=310133.8235  Y=747714.9881  Z=0").encode()
        f = SimpleUploadedFile("coords.txt", contenu, content_type="text/plain")
        _auth(self.client, self.geometre)
        r = self.client.post(f"/api/parcelles/{self.parcelle.id}/ocr_plan/",
                             {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 2)
        self.assertEqual(r.data["source"], "txt")


class CertificatTests(BaseData):
    """Certificat PDF : réservé aux parcelles validées, propriétaire ou admin."""

    def _parcelle_validee(self):
        from parcelles.models import Delimitation, VerificationDossier
        poly = Polygon(((1.2, 6.1), (1.2, 6.11), (1.21, 6.11), (1.21, 6.1), (1.2, 6.1)))
        p = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            geometry=poly, surface_m2=12000,
        )
        Delimitation.objects.create(parcelle=p, surveyor=self.geometre,
                                    validated_geometry=poly, source_epsg=32631)
        # Décision notaire APPROUVÉE -> le signal fait passer la parcelle en validée.
        VerificationDossier.objects.create(
            parcelle=p, decision=VerificationDossier.Decision.APPROVED,
        )
        p.refresh_from_db()
        return p

    def test_certificat_pour_parcelle_validee(self):
        p = self._parcelle_validee()
        _auth(self.client, self.citoyen)
        r = self.client.get(f"/api/parcelles/{p.id}/certificat/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn(p.reference, r["Content-Disposition"])
        self.assertTrue(r.content[:4] == b"%PDF")

    def test_certificat_refuse_si_non_validee(self):
        p = Parcelle.objects.create(
            owner=self.citoyen, declared_location=Point(1.2, 6.1),
            status=Parcelle.Status.SUBMITTED,
        )
        _auth(self.client, self.citoyen)
        r = self.client.get(f"/api/parcelles/{p.id}/certificat/")
        self.assertEqual(r.status_code, 400)

    def test_certificat_refuse_a_un_tiers(self):
        p = self._parcelle_validee()
        _auth(self.client, self.autre_citoyen)
        r = self.client.get(f"/api/parcelles/{p.id}/certificat/")
        self.assertEqual(r.status_code, 403)


class EmailRequisDeclarationTests(BaseData):
    """L'email est requis pour déclarer (mais pas à l'inscription)."""

    def setUp(self):
        super().setUp()
        # Un citoyen SANS email.
        self.sans_email = User.objects.create_user(
            username="sansmail", password="pw", role=User.Role.CITIZEN
        )

    def test_declaration_refusee_sans_email(self):
        _auth(self.client, self.sans_email)
        r = self.client.post("/api/parcelles/", _point_feature(1.2, 6.1), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.data.get("email_requis"))
        self.assertEqual(Parcelle.objects.filter(owner=self.sans_email).count(), 0)

    def test_declaration_avec_email_enregistre_l_email(self):
        _auth(self.client, self.sans_email)
        corps = _point_feature(1.2, 6.1)
        corps["email"] = "nouveau@exemple.tg"
        r = self.client.post("/api/parcelles/", corps, format="json")
        self.assertEqual(r.status_code, 201)
        self.sans_email.refresh_from_db()
        self.assertEqual(self.sans_email.email, "nouveau@exemple.tg")

    def test_declaration_ok_si_compte_a_deja_un_email(self):
        _auth(self.client, self.citoyen)  # a déjà c@x.tg
        r = self.client.post("/api/parcelles/", _point_feature(1.2, 6.1), format="json")
        self.assertEqual(r.status_code, 201)