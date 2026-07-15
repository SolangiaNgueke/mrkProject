"""Module OCR — extraction du tableau de points de bornage depuis un plan.

MODULE BRANCHABLE : aujourd'hui, moteur local Tesseract (gratuit). Pour passer
à un moteur cloud plus précis (Google Vision, AWS Textract, modèle de vision…),
il suffira de remplacer le corps de `extract_boundary_points` — le reste du code
(endpoint, page géomètre) ne change pas.

Dans tous les cas, l'OCR ne fait que PROPOSER un tableau : le géomètre le
vérifie et le corrige avant de valider (principe : l'humain décide).
"""

import io
import re

try:
    import pytesseract
    from PIL import Image

    _AVAILABLE = True
except ImportError:  # dépendances non installées
    _AVAILABLE = False


def _preprocess(image_bytes):
    """Prépare l'image pour améliorer la lecture (niveaux de gris + agrandissement)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    w, h = img.size
    if max(w, h) < 2000:  # agrandir les petites images aide l'OCR
        scale = 2000 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
    return img


def _parse_points(text):
    """Repère les lignes contenant un nom de point + deux grands nombres (X, Y)."""
    points = []
    for line in text.splitlines():
        norm = line.replace(",", ".")
        # coordonnées = nombres à au moins 3 chiffres avec décimales (ex. 293977.58)
        nums = re.findall(r"\d{3,}\.\d+", norm)
        if len(nums) >= 2:
            m = re.match(r"\s*([A-Za-z]{0,3}\d{1,3})", line)
            points.append(
                {
                    "name": m.group(1) if m else None,
                    "x": float(nums[0]),
                    "y": float(nums[1]),
                }
            )
    return points


def extract_boundary_points(image_bytes):
    """Retourne une liste de points {name, x, y} lus sur le plan.

    Peut renvoyer une liste vide ou imparfaite : c'est normal, le géomètre corrige.
    """
    if not _AVAILABLE:
        raise RuntimeError("OCR indisponible : dépendances (pytesseract/Pillow) manquantes.")

    img = _preprocess(image_bytes)
    # --psm 6 : suppose un bloc de texte uniforme (adapté à un tableau)
    text = pytesseract.image_to_string(img, config="--psm 6")
    return _parse_points(text)