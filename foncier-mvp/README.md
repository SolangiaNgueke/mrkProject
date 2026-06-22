# Foncier — MVP partie 1 (socle géospatial)

Première brique du projet : **base PostGIS + API Django/GeoDjango + carte satellite**
permettant de tracer une parcelle et de **détecter automatiquement les chevauchements**
(le garde-fou contre les doubles/triples ventes).

## Ce que fait déjà ce code
- Base **PostgreSQL + PostGIS** prête (via Docker).
- Modèle **Parcelle** (polygone, statut, fiabilité) + **Délimitation** (géomètre),
  **Document** (confidentiel), **VerificationDossier** (notaire/cadastre).
- Utilisateur avec **4 rôles** (citoyen, géomètre, notaire/cadastre, admin).
- **API REST** : créer/lister des parcelles en **GeoJSON**, avec **détection de chevauchement**.
- **Séparation public/privé** : l'API publique n'expose que nom, statut, fiabilité, surface.
- **Back-office** via l'admin Django (carte incluse) pour les vérificateurs.
- **Page carte** (satellite) pour tracer une parcelle et voir l'alerte de conflit.

## Pré-requis
- Docker + Docker Compose installés.

## Étapes

### 1. Lancer la base + l'application
```bash
cd foncier-mvp
docker compose up --build
```
Laisse tourner. L'API sera sur http://localhost:8000

### 2. Créer les tables (dans un 2e terminal)
```bash
docker compose exec web python manage.py makemigrations accounts parcelles
docker compose exec web python manage.py migrate
```

### 3. Créer un compte administrateur (pour le back-office)
```bash
docker compose exec web python manage.py createsuperuser
```
Puis va sur http://localhost:8000/admin — tu peux y voir/éditer les parcelles sur une carte.

### 4. Ouvrir la carte
Ouvre `frontend/index.html` dans ton navigateur.
> Le plus simple : avec l'extension « Live Server » de VS Code (sert sur le port 5500,
> déjà autorisé par le CORS). Sinon, ouverture directe du fichier possible en mode debug.

### 5. Tester l'anti-fraude
1. Clique **Tracer**, pose 3–4 coins sur un terrain, clique **Terminer le tracé**.
2. Donne un nom, clique **Enregistrer** → la parcelle apparaît en orange.
3. Trace une **2e parcelle qui chevauche la première** → message **⚠️ CHEVAUCHEMENT détecté**.

## Comment fonctionne la détection de chevauchement
Dans `parcelles/models.py`, la méthode `Parcelle.overlapping()` utilise le lookup
spatial `geometry__intersects` qui se traduit en `ST_Intersects` côté PostGIS.
C'est la base : une nouvelle parcelle qui intersecte une parcelle existante
non rejetée = conflit potentiel à arbitrer par un vérificateur.

## Étapes suivantes (hors de cette partie 1)
- **Authentification + RBAC** (aujourd'hui l'API est ouverte pour le prototype).
- Outil de tracé avancé pour le **géomètre** (édition de sommets).
- **Upload des documents** chiffrés + hash + stockage objet S3.
- **Workflow de validation** géomètre → notaire/cadastre, mise à jour de la fiabilité.
- **URLs signées** pour les documents confidentiels.

## Note production (important)
Ce code est un **prototype local** : `DEBUG=1`, API ouverte, `SECRET_KEY` factice,
documents stockés en local. Avant toute mise en ligne : authentification, HTTPS,
secrets hors du code, stockage objet chiffré, et restriction de `ALLOWED_HOSTS` / CORS.
