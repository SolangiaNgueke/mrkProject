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


# Brique 2 — Authentification + contrôle d'accès par rôle

Ferme la porte laissée ouverte par `AllowAny`. Désormais : lecture publique,
mais il faut être **connecté** pour créer/modifier une parcelle, chaque parcelle
est rattachée à son **propriétaire**, et seul le propriétaire (ou un notaire/admin)
peut la modifier.

## Ce qui a changé
- `config/settings.py` : ajout de `rest_framework.authtoken`, authentification par
  **token**, permission par défaut `IsAuthenticatedOrReadOnly`.
- `accounts/serializers.py`, `accounts/views.py`, `accounts/urls.py` : inscription,
  connexion, endpoint « me ».
- `config/urls.py` : routes `api/auth/...`.
- `parcelles/permissions.py` : règle d'accès par rôle.
- `parcelles/views.py` : applique la permission + rattache la parcelle à l'utilisateur connecté.
- `frontend/index.html` : panneau connexion/inscription + envoi du token.

## Nouveaux endpoints
- `POST /api/auth/register/`  `{username, password, email?, phone?}` → `{token, user}`
- `POST /api/auth/login/`     `{username, password}`                 → `{token, user}`
- `GET  /api/auth/me/`        (en-tête `Authorization: Token <clé>`)  → profil

## Étapes pour l'appliquer

1) Remplace les fichiers modifiés par ceux de cette version.

2) Crée la table des tokens (nouvelle app `authtoken`) :
```bash
docker compose exec web python manage.py migrate
```
> Aucune nouvelle dépendance à installer : `authtoken` est inclus dans DRF.

3) Redémarre le serveur web :
```bash
docker compose restart web
docker compose logs web --tail=10   # doit afficher "Starting development server..." sans erreur
```

4) (Recommandé) Donne le rôle "admin" à ton compte SELOM :
   admin → Utilisateurs → SELOM → champ **Role** = Administrateur → Enregistrer.
   (Ton compte est déjà superuser, donc il peut de toute façon tout modifier.)

## Tester

Recharge la page carte avec **Ctrl+Maj+R**.

- **Sans être connecté** : la carte et les parcelles s'affichent (lecture publique),
  mais « Enregistrer » répond « Tu dois être connecté ».
- **Crée un compte** (bouton « Créer un compte ») ou **connecte-toi** (SELOM + mot de passe).
- Trace une parcelle et enregistre → elle est maintenant rattachée à ton compte.
- Vérifie dans l'admin → Parcelles → la parcelle a bien un **propriétaire** (owner).

### Test rapide en ligne de commande (optionnel)
```bash
# inscription
curl -s -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username":"test1","password":"motdepasse123"}'
```
Tu reçois un `token`. Réutilise-le :
```bash
curl -s http://localhost:8000/api/auth/me/ -H "Authorization: Token COLLE_LE_TOKEN_ICI"
```

## Règles d'accès en place
- **GET** (voir les parcelles) : tout le monde, même non connecté.
- **POST/PUT/DELETE** : utilisateur connecté.
- Modifier/supprimer une parcelle précise : son **propriétaire**, ou un **notaire/cadastre/admin**.
- L'inscription publique crée toujours un **citoyen** ; les rôles géomètre/notaire
  sont attribués par un admin dans le back-office (sécurité).

## Étapes suivantes (briques à venir)
- Logique « litige automatique + rouge » sur les chevauchements.
- Upload des documents confidentiels (chiffrés) + URLs signées.
- Endpoints d'action pour le **géomètre** (délimitation) et le **notaire** (vérification),
  avec permissions réservées à ces rôles.

# Brique 3 — Upload des documents confidentiels

Permet au propriétaire de joindre ses pièces (titre, acte, plan…) à une parcelle.
Conforme au blueprint (§16, §17) : documents **confidentiels**, **hash SHA-256**
pour l'intégrité, et **aucun lien public** — le téléchargement passe par une route
protégée qui vérifie les droits à chaque requête.

## Ce qui a changé
- `parcelles/serializers.py` : ajout de `DocumentSerializer` (fichier en écriture seule,
  URL de téléchargement protégée).
- `parcelles/views.py` : deux routes sur la parcelle —
  - `GET/POST /api/parcelles/{id}/documents/` (lister / ajouter),
  - `GET /api/parcelles/{id}/documents/{doc_id}/download/` (télécharger, protégé).
- `frontend/index.html` : section « Documents » qui apparaît après l'enregistrement
  d'une parcelle (ajout + liste + téléchargement).

> Aucune migration ni dépendance nouvelle : le modèle `Document` existait déjà
> (créé dans la migration initiale).

## Étapes pour l'appliquer
1) Remplace les 3 fichiers modifiés par cette version.
2) Le serveur se recharge tout seul. Au besoin :
```bash
docker compose restart web
docker compose logs web --tail=10   # "Starting development server..." sans erreur
```

## Tester
Recharge la page carte avec **Ctrl+Maj+R**, connecte-toi, puis :
1. Trace et **enregistre** une parcelle → la section « Documents » apparaît.
2. Choisis un type (titre, acte…), sélectionne un fichier, clique **Ajouter le document**.
   → message « Document ajouté (intégrité garantie par hash SHA-256) ».
3. Le document s'affiche dans la liste avec le début de son hash + un bouton **Télécharger**.

## Vérifier la confidentialité (le point important)
- **Le propriétaire** voit et télécharge ses documents. ✅
- **Un autre citoyen** (autre compte) qui appelle l'URL des documents → **403 Accès refusé**.
  C'est exactement le but : les pièces ne sont pas publiques.
- **Un notaire/cadastre/admin** peut les consulter (vérificateurs).
- Le fichier n'est **jamais** accessible par un lien direct : il faut passer par la route
  protégée, avec un token valide.

Test rapide (terminal WSL) — récupère le token de `test1` puis liste les docs d'une parcelle
qui ne lui appartient pas : tu dois obtenir un 403.

## Règles d'accès en place
| Action | Qui |
|---|---|
| Ajouter un document | Propriétaire de la parcelle (ou admin) |
| Lister / télécharger | Propriétaire + notaire/cadastre + admin |
| Lien public direct | Personne (route protégée uniquement) |

## Note production (rappel du blueprint §16-17)
En prototype, les fichiers sont stockés en local et servis par une vue Django protégée.
En production : **stockage objet S3 chiffré au repos** + **URLs signées temporaires**.
La logique de permission posée ici reste valable ; seul le stockage change.

## Étape suivante
Le **workflow de vérification à deux niveaux** (§8) : endpoints réservés au géomètre
(valider la délimitation) puis au notaire/cadastre (valider les documents), avec mise à
jour automatique du statut et du niveau de fiabilité.