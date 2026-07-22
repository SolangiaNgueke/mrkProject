# Mise en ligne de la plateforme

Le code est prêt pour la production. Ce guide décrit ce qu'il reste à faire :
créer les comptes chez un hébergeur (étape que seul toi peux réaliser) et
renseigner les variables de configuration.

## Ce qui a été préparé côté code

- **Gunicorn** : serveur robuste pour la production (`runserver` est réservé au développement).
- **WhiteNoise** : sert les fichiers statiques de l'admin sans serveur web séparé.
- **DATABASE_URL** : la base est configurée automatiquement à partir de l'unique
  variable fournie par les hébergeurs.
- **Garde-fous** : avec `DEBUG=0`, le serveur refuse de démarrer si `SECRET_KEY`
  ou `ALLOWED_HOSTS` manquent. HTTPS, cookies sécurisés et protections d'en-têtes
  s'activent automatiquement.
- **`start-prod.sh`** : applique les migrations, rassemble les statiques, lance Gunicorn.

## Contrainte importante : PostGIS

La plateforme a besoin de **PostgreSQL avec l'extension PostGIS** (données
géographiques). Toutes les offres ne la proposent pas. Vérifie ce point avant de
choisir :

| Hébergeur | PostGIS | Remarque |
|---|---|---|
| Render | oui | Simple, offre gratuite pour tester |
| Railway | oui | Déploiement rapide depuis GitHub |
| Supabase (base seule) | oui | À coupler avec un hébergeur d'application |
| VPS (Hetzner, OVH, Contabo…) | oui | Le plus souple et le moins cher, mais tout est à administrer |

Pour un premier déploiement, **Render** ou **Railway** sont les plus simples :
ils lisent directement ton dépôt GitHub et construisent l'image Docker.

## Étapes

### 1. Pousser le code sur GitHub
```bash
git status          # vérifier que .env n'apparaît PAS
git add -A
git commit -m "Preparation deploiement"
git push
```

### 2. Créer la base de données PostGIS
Chez l'hébergeur, crée une base PostgreSQL et **active l'extension PostGIS**
(souvent une case à cocher, ou la commande `CREATE EXTENSION postgis;`).
Récupère l'URL de connexion (`postgres://utilisateur:motdepasse@hote:5432/base`).

### 3. Créer le service web
Choisis un déploiement **par Docker** (le `Dockerfile` du projet contient déjà
GDAL, GEOS, PROJ et Tesseract, indispensables et souvent absents des
environnements standards).

Commande de démarrage : `/app/start-prod.sh`

### 4. Renseigner les variables d'environnement
À définir dans l'interface de l'hébergeur (jamais dans le code) :

```
DEBUG=0
SECRET_KEY=<généré, voir ci-dessous>
ALLOWED_HOSTS=mon-domaine.com,www.mon-domaine.com
CORS_ORIGINS=https://mon-domaine.com
DATABASE_URL=postgres://...
PUBLIC_API_URL=https://api.mon-domaine.com

EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=Plateforme Foncière <...>

OCR_ENGINE=vision
GOOGLE_VISION_API_KEY=...
```

Générer une clé secrète solide :
```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### 5. Créer le compte administrateur
Une fois le service en ligne, depuis la console de l'hébergeur :
```bash
python manage.py createsuperuser
```

### 6. Mettre les pages en ligne
Le dossier `frontend/` (pages HTML) est un site statique : publie-le sur
**Netlify**, **Vercel** ou **GitHub Pages** (gratuit).

Avant, remplace l'adresse de l'API dans chaque page :
```js
const API_ROOT = "http://localhost:8000/api/";   // ← à remplacer
const API_ROOT = "https://api.mon-domaine.com/api/";
```
Fichiers concernés : `index.html`, `documents.html`, `delimitation.html`,
`dashboard.html`, `mes-parcelles.html`.

Puis déclare l'adresse du site dans `CORS_ORIGINS` (étape 4), sinon le navigateur
bloquera les appels à l'API.

## Point de vigilance : les documents téléversés

`MEDIA_ROOT` écrit les fichiers sur le disque du conteneur. Sur la plupart des
hébergeurs, **ce disque est effacé à chaque redéploiement**. Pour une mise en
production réelle, il faut soit un disque persistant, soit un stockage objet
(S3, Cloudflare R2) — d'autant que ces documents sont confidentiels et doivent
être chiffrés au repos.

## Vérifications après mise en ligne

```bash
python manage.py check --deploy      # contrôle de sécurité Django
python manage.py verifier_audit      # intégrité du journal d'audit
```
Teste ensuite : inscription, email de confirmation, déclaration d'une parcelle,
tracé géomètre, validation notaire, affichage sur la carte publique.