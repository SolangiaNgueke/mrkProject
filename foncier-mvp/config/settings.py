import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "1") == "1"

# En production (DEBUG=0), la clé secrète DOIT venir de l'environnement (.env) :
# le serveur refuse de démarrer avec la clé de développement.
SECRET_KEY = os.environ.get("SECRET_KEY", "" if not DEBUG else "dev-secret-change-me")
if not DEBUG and (not SECRET_KEY or SECRET_KEY == "dev-secret-change-me"):
    raise RuntimeError(
        "SECRET_KEY manquante ou non modifiée. Définis-la dans .env avant de passer en production. "
        "Génère-la avec : python -c \"import secrets; print(secrets.token_urlsafe(50))\""
    )

# Hôtes autorisés : tout en dev, liste explicite en production (ALLOWED_HOSTS
# dans .env, séparés par des virgules — ex. foncier.tg,www.foncier.tg).
_hosts = os.environ.get("ALLOWED_HOSTS", "").strip()
ALLOWED_HOSTS = ["*"] if DEBUG else [h.strip() for h in _hosts.split(",") if h.strip()]
if not DEBUG and not ALLOWED_HOSTS:
    raise RuntimeError("ALLOWED_HOSTS doit être défini dans .env en production.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Géospatial
    "django.contrib.gis",
    # 3rd party
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_gis",
    "corsheaders",
    # apps du projet
    "accounts",
    "parcelles",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# En production, les hébergeurs fournissent une seule variable DATABASE_URL
# (ex. postgres://user:mdp@hote:5432/base). On la décompose si elle existe.
_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url:
    from urllib.parse import unquote, urlparse

    _u = urlparse(_db_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis",
            "NAME": _u.path.lstrip("/"),
            "USER": unquote(_u.username or ""),
            "PASSWORD": unquote(_u.password or ""),
            "HOST": _u.hostname or "",
            "PORT": str(_u.port or 5432),
            "CONN_MAX_AGE": 600,          # connexions réutilisées (performances)
            "OPTIONS": {"sslmode": os.environ.get("DB_SSLMODE", "require")},
        }
    }
else:
    DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": os.environ.get("POSTGRES_DB", "foncier"),
        "USER": os.environ.get("POSTGRES_USER", "foncier"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "foncier"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"   # rempli par `collectstatic` en production
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # WhiteNoise : sert les fichiers statiques (admin) sans serveur web séparé.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"  # PROTOTYPE: en production -> stockage objet S3 chiffré

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Lecture (GET) ouverte à tous ; écriture réservée aux utilisateurs connectés.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
}

# CORS : en dev, on autorise la page carte servie en local.
# En production, seule la liste CORS_ORIGINS du .env est acceptée.
_cors = os.environ.get("CORS_ORIGINS", "").strip()
CORS_ALLOWED_ORIGINS = (
    ["http://localhost:5500", "http://127.0.0.1:5500", "http://localhost:8000"]
    if DEBUG
    else [o.strip() for o in _cors.split(",") if o.strip()]
)
CORS_ALLOW_ALL_ORIGINS = DEBUG  # jamais en production
CORS_ALLOW_CREDENTIALS = True

# ------------------------------------------------------------------ #
#  Durcissement HTTPS / cookies (actif uniquement en production)      #
# ------------------------------------------------------------------ #
if not DEBUG:
    SECURE_SSL_REDIRECT = True                # force le HTTPS
    SESSION_COOKIE_SECURE = True              # cookies envoyés en HTTPS seulement
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000            # 1 an
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"                  # anti-clickjacking
    # Derrière un proxy (nginx, Render, Railway…) qui termine le TLS :
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_TRUSTED_ORIGINS = [o for o in CORS_ALLOWED_ORIGINS if o.startswith("https://")]

# ------------------------------------------------------------------ #
#  Email (notifications aux propriétaires)                            #
# ------------------------------------------------------------------ #
# Bascule automatique :
#   - si EMAIL_HOST_USER est renseigné (dans .env) -> vrais envois via SMTP
#   - sinon -> mode console (les emails s'affichent dans les logs Docker)
# Les identifiants ne sont JAMAIS écrits ici : ils viennent du fichier .env.
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")

if EMAIL_HOST_USER:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
    DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    DEFAULT_FROM_EMAIL = "Plateforme Foncière <no-reply@foncier.local>"