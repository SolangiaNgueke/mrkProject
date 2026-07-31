"""Tests automatisés des règles CRITIQUES de la plateforme foncière.

Lancer :  docker compose exec web python manage.py test parcelles

Ces tests vérifient les garanties qui font la valeur anti-fraude du produit :
visibilité publique, isolement des rôles (moindre privilège), workflow de
validation, anti-doublon et intégrité du journal d'audit. Si un futur changement
casse l'une de ces règles, le test échoue AVANT le déploiement.
"""

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point, Polygon
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
        self.assertIs