"""Génération du Certificat de Vérification (PDF) d'une parcelle.

IMPORTANT (cadre juridique) : ce certificat est un document PRIVÉ de
vérification de la plateforme. Il n'est PAS un titre foncier, ni un acte
authentique, ni une certification de l'État. Le PDF le mentionne explicitement.

Le QR code renvoie vers la carte publique, centrée sur la parcelle, avec sa
fiche publique affichée (aucune donnée privée n'est exposée).
"""

import hashlib
import io
import os

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


def _url_verification(parcelle):
    """URL publique ouverte par le QR : la carte, centrée sur la parcelle."""
    base = os.environ.get("PUBLIC_SITE_URL", "http://localhost:5500").rstrip("/")
    centre = parcelle.geometry.centroid if parcelle.geometry else parcelle.declared_location
    if centre:
        return f"{base}/index.html?parcelle={parcelle.id}&lat={centre.y:.6f}&lon={centre.x:.6f}"
    return f"{base}/index.html?parcelle={parcelle.id}"


def _numero_certificat(parcelle):
    """Numéro de certificat lisible, basé sur la référence de la parcelle."""
    return f"CERT-{parcelle.reference}"


def generer_certificat(parcelle):
    """Retourne (nom_fichier, contenu_pdf_bytes) pour une parcelle validée."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    largeur, hauteur = A4

    vert = colors.HexColor("#15803d")
    gris = colors.HexColor("#6b7280")
    fonce = colors.HexColor("#1f2d3a")

    # --- En-tête ---
    c.setFillColor(fonce)
    c.rect(0, hauteur - 45 * mm, largeur, 45 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(20 * mm, hauteur - 25 * mm, "Certificat de Vérification")
    c.setFont("Helvetica", 11)
    c.drawString(20 * mm, hauteur - 33 * mm, "Plateforme foncière — vérification communautaire")
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.HexColor("#22c55e"))
    c.drawRightString(largeur - 20 * mm, hauteur - 25 * mm, _numero_certificat(parcelle))

    # --- Bandeau d'avertissement juridique ---
    y = hauteur - 58 * mm
    c.setFillColor(colors.HexColor("#fff7ed"))
    c.rect(20 * mm, y, largeur - 40 * mm, 14 * mm, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#f59e0b"))
    c.rect(20 * mm, y, largeur - 40 * mm, 14 * mm, fill=0, stroke=1)
    c.setFillColor(colors.HexColor("#92400e"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(24 * mm, y + 8.5 * mm, "Document privé de vérification — CE N'EST PAS un titre foncier")
    c.setFont("Helvetica", 8)
    c.drawString(24 * mm, y + 3.5 * mm,
                 "ni un acte authentique, ni une certification de l'État. Il atteste d'un contrôle interne à la plateforme.")

    # --- Informations de la parcelle (publiques) ---
    centre = parcelle.geometry.centroid if parcelle.geometry else parcelle.declared_location
    surface_ha = round(parcelle.surface_m2 / 10000, 4) if parcelle.surface_m2 else None
    region = "—"
    if parcelle.reference and "-" in parcelle.reference:
        parts = parcelle.reference.split("-")
        region = f"{parts[0]}-{parts[1]}" if len(parts) >= 2 else "—"

    lignes = [
        ["Identifiant de parcelle", parcelle.reference or f"#{parcelle.id}"],
        ["Statut", "Validée (double vérification)"],
        ["Superficie", f"{round(parcelle.surface_m2)} m² ({surface_ha} ha)" if parcelle.surface_m2 else "—"],
        ["Région", region],
        ["Localisation (centre)", f"{centre.y:.6f}, {centre.x:.6f}" if centre else "—"],
        ["Date de déclaration", parcelle.created_at.strftime("%d/%m/%Y") if parcelle.created_at else "—"],
        ["Date du certificat", parcelle.updated_at.strftime("%d/%m/%Y") if parcelle.updated_at else "—"],
    ]
    table = Table(lignes, colWidths=[55 * mm, 95 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), gris),
        ("TEXTCOLOR", (1, 0), (1, -1), fonce),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    table.wrapOn(c, largeur, hauteur)
    table.drawOn(c, 20 * mm, hauteur - 130 * mm)

    # --- QR code + explication ---
    url = _url_verification(parcelle)
    qr = qrcode.make(url)
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    from reportlab.lib.utils import ImageReader

    qr_size = 38 * mm
    qr_x = largeur - 20 * mm - qr_size
    qr_y = hauteur - 178 * mm
    c.drawImage(ImageReader(qr_buffer), qr_x, qr_y, qr_size, qr_size)
    c.setFont("Helvetica", 8)
    c.setFillColor(gris)
    c.drawCentredString(qr_x + qr_size / 2, qr_y - 4 * mm, "Scannez pour localiser le terrain")

    c.setFont("Helvetica", 10)
    c.setFillColor(fonce)
    c.drawString(20 * mm, hauteur - 150 * mm, "Vérification en ligne")
    c.setFont("Helvetica", 8.5)
    c.setFillColor(gris)
    txt = c.beginText(20 * mm, hauteur - 157 * mm)
    txt.textLines(
        "Scannez le QR code ci-contre pour ouvrir la fiche\n"
        "publique de la parcelle et sa localisation sur la\n"
        "carte."
    )
    c.drawText(txt)

    # --- Empreinte d'intégrité (hash) ---
    empreinte = hashlib.sha256(
        f"{parcelle.reference}|{url}|{parcelle.surface_m2}".encode()
    ).hexdigest()[:32]
    c.setFont("Courier", 7)
    c.setFillColor(gris)
    c.drawString(20 * mm, 22 * mm, f"Empreinte d'intégrité : {empreinte}")

    # --- Pied de page ---
    c.setStrokeColor(colors.HexColor("#e5e7eb"))
    c.line(20 * mm, 18 * mm, largeur - 20 * mm, 18 * mm)
    c.setFont("Helvetica", 7.5)
    c.setFillColor(gris)
    c.drawString(20 * mm, 13 * mm,
                 "Ce certificat atteste d'une vérification interne à la plateforme et n'a pas de valeur officielle de titre de propriété.")
    c.drawString(20 * mm, 9 * mm, f"Numéro : {_numero_certificat(parcelle)}")

    c.setFillColor(vert)
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(largeur - 20 * mm, 9 * mm, "✓ Parcelle vérifiée")

    c.showPage()
    c.save()
    buffer.seek(0)
    nom = f"certificat_{parcelle.reference or parcelle.id}.pdf"
    return nom, buffer.getvalue()