"""Module OCR — extraction du tableau de points de bornage depuis un plan.

Deux moteurs possibles, choisis par la variable d'environnement OCR_ENGINE :

  - "tesseract" (défaut) : moteur local gratuit. Correct sur un tableau net et
    bien cadré, médiocre sur un scan de travers ou de faible qualité.
  - "vision" : Google Cloud Vision (bien meilleur sur les tableaux). Nécessite
    la variable GOOGLE_VISION_API_KEY et un compte de facturation Google.

Dans tous les cas, l'OCR ne fait que PROPOSER un tableau : le géomètre le
vérifie et le corrige avant de valider (l'humain décide).
"""

import base64
import io
import json
import os
import re
import urllib.request

try:
    import pytesseract
    from PIL import Image, ImageFilter, ImageOps

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import fitz  # PyMuPDF : conversion des pages PDF en images

    _PDF_AVAILABLE = True
except ImportError:
    _PDF_AVAILABLE = False

# Nombre maximal de pages analysées dans un PDF (les plans tiennent sur 1-2 pages).
MAX_PAGES_PDF = 5


def est_pdf(donnees):
    return donnees[:5] == b"%PDF-"


def pdf_en_images(donnees, dpi=300):
    """Convertit les premières pages d'un PDF en images PNG (haute résolution).

    300 dpi est un bon compromis : assez net pour l'OCR sans être trop lourd.
    """
    if not _PDF_AVAILABLE:
        raise RuntimeError(
            "Lecture PDF indisponible : la librairie PyMuPDF n'est pas installée."
        )
    images = []
    with fitz.open(stream=donnees, filetype="pdf") as doc:
        for page in list(doc)[:MAX_PAGES_PDF]:
            pix = page.get_pixmap(dpi=dpi)
            images.append(pix.tobytes("png"))
    return images


# --------------------------------------------------------------------- #
#  Prétraitement de l'image (améliore nettement Tesseract)               #
# --------------------------------------------------------------------- #

def _deskew(img):
    """Redresse légèrement l'image si le texte est incliné."""
    try:
        osd = pytesseract.image_to_osd(img)
        angle = int(re.search(r"Rotate: (\d+)", osd).group(1))
        if angle:
            img = img.rotate(-angle, expand=True, fillcolor=255)
    except Exception:  # OSD indisponible : on garde l'image telle quelle
        pass
    return img


def _preprocess(image_bytes):
    """Prétraitement VOLONTAIREMENT LÉGER — identique à la version qui donnait
    les meilleurs résultats. Les traitements plus agressifs dégradaient la
    lecture (binarisation qui efface les chiffres fins, redressement qui pivote
    de travers) : ils sont désormais OPTIONNELS, activables sans toucher au code
    via le fichier .env :

        OCR_AUTOCONTRAST=1  -> étire le contraste (peu risqué)
        OCR_SHARPEN=1       -> renforce les contours
        OCR_DESKEW=1        -> redresse l'image (risqué)
        OCR_BINARIZE=1      -> noir & blanc pur (risqué)
        OCR_BINARIZE_THRESHOLD=160
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("L")

    # Agrandissement modéré : aide l'OCR sans déformer les caractères.
    w, h = img.size
    if max(w, h) < 2000:
        scale = 2000 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))

    if os.environ.get("OCR_DESKEW", "0") == "1":
        img = _deskew(img)
    if os.environ.get("OCR_AUTOCONTRAST", "0") == "1":
        img = ImageOps.autocontrast(img)
    if os.environ.get("OCR_SHARPEN", "0") == "1":
        img = img.filter(ImageFilter.SHARPEN)
    if os.environ.get("OCR_BINARIZE", "0") == "1":
        seuil = int(os.environ.get("OCR_BINARIZE_THRESHOLD", "160"))
        img = img.point(lambda p: 255 if p > seuil else 0)
    return img


# --------------------------------------------------------------------- #
#  Analyse du texte -> points de bornage                                 #
# --------------------------------------------------------------------- #

def _rows_from_vision(data):
    """Reconstitue les LIGNES réelles du tableau à partir de la position des mots.

    Indispensable pour les plans où le tableau est lu colonne par colonne
    (tous les B1..B30, puis tous les X, puis tous les Y) : dans ce cas aucune
    ligne de texte ne contient X et Y ensemble, et une analyse ligne à ligne
    échoue. On regroupe donc les mots par leur position verticale sur l'image.
    """
    mots = []
    for resp in data.get("responses") or []:
        for page in (resp.get("fullTextAnnotation") or {}).get("pages", []):
            for block in page.get("blocks", []):
                for para in block.get("paragraphs", []):
                    for word in para.get("words", []):
                        texte = "".join(s.get("text", "") for s in word.get("symbols", []))
                        if not texte:
                            continue
                        sommets = (word.get("boundingBox") or {}).get("vertices", [])
                        ys = [v.get("y", 0) for v in sommets]
                        xs = [v.get("x", 0) for v in sommets]
                        if not ys or not xs:
                            continue
                        mots.append({
                            "t": texte,
                            "cy": sum(ys) / len(ys),          # centre vertical
                            "cx": sum(xs) / len(xs),          # centre horizontal
                            "h": max(ys) - min(ys) or 10,     # hauteur du mot
                        })
    if not mots:
        return []

    # Regroupe les mots dont le centre vertical est proche = même ligne.
    mots.sort(key=lambda m: m["cy"])
    hauteur_moyenne = sum(m["h"] for m in mots) / len(mots)
    tolerance = max(hauteur_moyenne * 0.6, 5)

    lignes, courante, ref_y = [], [], None
    for m in mots:
        if ref_y is None or abs(m["cy"] - ref_y) <= tolerance:
            courante.append(m)
            ref_y = m["cy"] if ref_y is None else (ref_y + m["cy"]) / 2
        else:
            lignes.append(courante)
            courante, ref_y = [m], m["cy"]
    if courante:
        lignes.append(courante)

    # Dans chaque ligne, remet les mots dans l'ordre gauche -> droite.
    return [" ".join(w["t"] for w in sorted(ln, key=lambda w: w["cx"])) for ln in lignes]


def _pair_columns(text):
    """Dernier recours : le tableau a été lu colonne par colonne.

    On récupère les repères (B1, B2…) et les nombres dans l'ordre, puis on
    apparie la 1re moitié des nombres (X) avec la 2de (Y).
    """
    noms = re.findall(r"\b([Bb]\d{1,3})\b", text)
    nombres = [float(n) for n in re.findall(r"\d{5,}\.\d+", text)]
    if len(nombres) < 6 or len(nombres) % 2:
        return []
    moitie = len(nombres) // 2
    xs, ys = nombres[:moitie], nombres[moitie:]
    points = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        points.append({"name": noms[i] if i < len(noms) else f"B{i+1}", "x": x, "y": y})
    return points


def _coherent(points):
    """Écarte les faux points : sur un même terrain, toutes les coordonnées sont
    du même ordre de grandeur (les distances inscrites sur le plan, elles, sont
    très différentes)."""
    if len(points) < 3:
        return points
    xs = sorted(p["x"] for p in points)
    ys = sorted(p["y"] for p in points)
    mx, my = xs[len(xs) // 2], ys[len(ys) // 2]   # valeurs médianes
    # On garde les points proches de la médiane (tolérance large : 50 km).
    return [p for p in points if abs(p["x"] - mx) < 50000 and abs(p["y"] - my) < 50000]


def _parse_points(text):
    """Repère les lignes contenant un nom de point + deux coordonnées (X, Y).

    Deux passes : les GRANDS nombres (5 chiffres et plus), typiques des
    coordonnées UTM, puis une passe plus souple (3 chiffres). On garde la passe
    qui détecte LE PLUS de points — s'arrêter à la première suffisante faisait
    manquer des lignes.
    """
    meilleur = []
    for min_chiffres in (5, 3):
        motif = re.compile(r"\d{%d,}\.\d+" % min_chiffres)
        points = []
        for line in text.splitlines():
            # Virgule décimale -> point.
            norm = line.replace(",", ".")
            nums = motif.findall(norm)
            if len(nums) >= 2:
                m = re.match(r"\s*([A-Za-z]{0,3}\d{1,3})", line)
                points.append(
                    {"name": m.group(1) if m else None, "x": float(nums[0]), "y": float(nums[1])}
                )
        points = _coherent(points)
        if len(points) > len(meilleur):
            meilleur = points
    return meilleur


# --------------------------------------------------------------------- #
#  Moteurs                                                               #
# --------------------------------------------------------------------- #

def _ocr_tesseract(image_bytes):
    if not _PIL_AVAILABLE:
        raise RuntimeError("Dépendances OCR (pytesseract/Pillow) manquantes.")

    # PDF : on analyse chaque page et on garde la meilleure lecture.
    if est_pdf(image_bytes):
        meilleur = []
        for page in pdf_en_images(image_bytes):
            points = _ocr_tesseract(page)
            if len(points) > len(meilleur):
                meilleur = points
        return meilleur

    img = _preprocess(image_bytes)
    # --psm 6 : bloc de texte uniforme (le réglage le plus fiable ici).
    # Pas de liste de caractères imposée : elle empêchait certaines lectures.
    points = _parse_points(pytesseract.image_to_string(img, config="--psm 6"))
    if not points:  # 2e tentative : segmentation en colonnes
        points = _parse_points(pytesseract.image_to_string(img, config="--psm 4"))
    return points


def _ocr_google_vision(image_bytes):
    """Google Cloud Vision — bien plus fiable sur les plans scannés."""
    key = os.environ.get("GOOGLE_VISION_API_KEY", "")
    if not key:
        raise RuntimeError("GOOGLE_VISION_API_KEY absente : configure-la dans .env")
    contenu = base64.b64encode(image_bytes).decode()

    # Cloud Vision lit nativement les PDF via un point d'entrée dédié
    # (files:annotate), ce qui évite de rasteriser côté serveur.
    if est_pdf(image_bytes):
        url = f"https://vision.googleapis.com/v1/files:annotate?key={key}"
        payload = {
            "requests": [{
                "inputConfig": {"content": contenu, "mimeType": "application/pdf"},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "pages": list(range(1, MAX_PAGES_PDF + 1)),
            }]
        }
    else:
        url = f"https://vision.googleapis.com/v1/images:annotate?key={key}"
        payload = {
            "requests": [{
                "image": {"content": contenu},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
            }]
        }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    r = (data.get("responses") or [{}])[0]
    if r.get("error"):
        raise RuntimeError(r["error"].get("message", "Erreur Cloud Vision"))

    # Pour un PDF, la réponse contient une sous-réponse par page.
    pages = r.get("responses")
    if pages:
        textes = [(p.get("fullTextAnnotation") or {}).get("text", "") for p in pages]
        texte_brut = "\n".join(t for t in textes if t)
        data = {"responses": pages}   # pour la reconstruction des lignes
    else:
        texte_brut = (r.get("fullTextAnnotation") or {}).get("text", "")

    # On essaie TOUTES les stratégies et on garde celle qui trouve le plus de
    # points (s'arrêter à la première donnait des tableaux incomplets).
    candidats = [
        _parse_points("\n".join(_rows_from_vision(data))),  # lignes reconstruites
        _parse_points(texte_brut),                          # texte brut
        _coherent(_pair_columns(texte_brut)),               # tableau lu en colonnes
    ]
    return max(candidats, key=len)


def extract_boundary_points(image_bytes):
    """Retourne une liste de points {name, x, y} lus sur le plan.

    Peut renvoyer une liste vide ou imparfaite : le géomètre corrige toujours.
    """
    engine = os.environ.get("OCR_ENGINE", "tesseract").lower()
    if engine == "vision":
        return _ocr_google_vision(image_bytes)
    return _ocr_tesseract(image_bytes)