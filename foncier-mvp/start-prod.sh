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

echo "→ Démarrage Gunicorn…"
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-3}" \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -