FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Librairies système nécessaires à GeoDjango (GEOS, GDAL, PROJ) + OCR (Tesseract)
RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        tesseract-ocr \
        tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/start-prod.sh

# Développement : serveur Django (rechargement auto).
# Production   : surcharger la commande par  /app/start-prod.sh  (Gunicorn).
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]