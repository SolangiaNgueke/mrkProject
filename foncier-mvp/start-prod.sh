#!/usr/bin/env bash
# Démarrage en PRODUCTION.
#   - applique les migrations
#   - rassemble les fichiers statiques (admin Django)
#   - lance Gunicorn (serveur robuste, contrairement à runserver réservé au dev)
set -e

echo "→ Migrations…"
python manage.py migrate --noinput

echo "→ Fichiers statiques…"
python manage.py collectstatic --noinput

# Groupes métier (géomètres / notaires) : commande idempotente.
python manage.py setup_roles || true

# Compte administrateur : créé au premier démarrage si les variables
# DJANGO_SUPERUSER_* sont définies (utile quand l'hébergeur ne donne pas
# accès à un terminal). Sans effet si le compte existe déjà.
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    echo "→ Compte administrateur…"
    python manage.py createsuperuser --noinput 2>/dev/null \
        && echo "  compte créé." \
        || echo "  déjà existant (rien à faire)."
fi

echo "→ Démarrage Gunicorn…"
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-3}" \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -