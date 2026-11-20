"""QR-Codes: SVG (Gerätekopplung in der Verwaltung) und PDF-Tischkarten zum Ausdrucken."""
from __future__ import annotations

import io

from reportlab.graphics.barcode import qrencoder
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from . import i18n

BRAND = HexColor("#019724")      # Hauptfarbe (Grün, wie --hi in app.css)


def qr_matrix(data: str, level: str = "M") -> list[list[bool]]:
    lvl = getattr(qrencoder.QRErrorCorrectLevel, level)
    qr = qrencoder.QRCode(None, lvl)
    qr.addData(data)
    qr.make()
    n = qr.getModuleCount()
    return [[bool(qr.isDark(r, c)) for c in range(n)] for r in range(n)]


def qr_svg(data: str, quiet: int = 4) -> str:
    """Kompaktes SVG (ein Pfad), skaliert über die viewBox."""
    m = qr_matrix(data)
    n = len(m) + 2 * quiet
    path = "".join(f"M{c + quiet} {r + quiet}h1v1h-1z" for r, row in enumerate(m) for c, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def _draw_qr(c: canvas.Canvas, data: str, x: float, y: float, size: float) -> None:
    m = qr_matrix(data, "M")
    cell = size / len(m)
    c.setFillColorRGB(0, 0, 0)
    for r, row in enumerate(m):
        run = None
        for col, dark in enumerate(row + [False]):           # Läufe zusammenfassen = kleinere PDF
            if dark and run is None:
                run = col
            elif not dark and run is not None:
                c.rect(x + run * cell, y + size - (r + 1) * cell, (col - run) * cell, cell, stroke=0, fill=1)
                run = None


def _fit(text: str, font: str, size: float, width: float) -> float:
    while size > 8 and stringWidth(text, font, size) > width:
        size -= 1
    return size


def _wrap(text: str, font: str, width: float, height: float) -> tuple[list[str], float]:
    """Grösste Schrift (max. 54 pt), bei der der Text in max. 3 Zeilen in die Box passt."""
    words = text.split()
    for fs in range(54, 9, -2):
        lines, cur = [], ""
        for w in words:
            t = f"{cur} {w}".strip()
            if stringWidth(t, font, fs) <= width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= 3 and all(stringWidth(l, font, fs) <= width for l in lines) and len(lines) * fs * 1.05 <= height:
            return lines, fs
    return [text], 10


def tables_pdf(labels: list[str], event_name: str = "", table_word: str = "Tisch", data: dict[str, str] | None = None,
               hint_key: str = "admin.tables.pdf_hint") -> bytes:
    """A4 hoch, 2 × 3 Karten pro Seite mit Schnittlinien. QR-Inhalt: table:<Bezeichnung> oder data[Bezeichnung]."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(i18n.t("admin.tables.pdf_title"))
    c.setAuthor("FestPOS")
    pw, ph = A4
    cols, rows = 2, 3
    margin = 10 * mm
    cw, ch = (pw - 2 * margin) / cols, (ph - 2 * margin) / rows
    per_page = cols * rows

    for i, label in enumerate(labels):
        if i and i % per_page == 0:
            c.showPage()
        k = i % per_page
        x0 = margin + (k % cols) * cw
        y0 = ph - margin - (k // cols + 1) * ch

        # Schnittlinie
        c.setStrokeColorRGB(.7, .7, .7)
        c.setDash(2, 3)
        c.setLineWidth(.5)
        c.rect(x0, y0, cw, ch, stroke=1, fill=0)
        c.setDash()

        pad = 6 * mm
        # Kopfband in Vereinsgrün
        band = 11 * mm
        c.setFillColor(BRAND)
        c.rect(x0 + pad, y0 + ch - pad - band, cw - 2 * pad, band, stroke=0, fill=1)
        if event_name:
            fs = _fit(event_name, "Helvetica-Bold", 13, cw - 2 * pad - 8 * mm)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", fs)
            c.drawCentredString(x0 + cw / 2, y0 + ch - pad - band / 2 - fs * .35, event_name)

        # Körper: Textspalte links, QR rechts (vertikal zentriert)
        foot = 8 * mm
        body_top = y0 + ch - pad - band - 4 * mm
        body_bot = y0 + pad + foot
        qsize = max(34 * mm, min(52 * mm, body_top - body_bot))
        qx, qy = x0 + cw - pad - qsize, body_bot + (body_top - body_bot - qsize) / 2
        _draw_qr(c, (data or {}).get(label) or f"table:{label}", qx, qy, qsize)

        text_w = qx - (x0 + pad) - 4 * mm
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 12)
        c.drawString(x0 + pad, body_top - 10, i18n.t("common.word.seat" if table_word == "Platz" else "common.word.table"))
        lines, fs = _wrap(label, "Helvetica-Bold", text_w, body_top - body_bot - 18)
        c.setFont("Helvetica-Bold", fs)
        y = body_top - 14 - fs
        for ln in lines:
            c.drawString(x0 + pad, y, ln)
            y -= fs * 1.05

        c.setFont("Helvetica", 8.5)
        c.setFillColorRGB(.35, .35, .35)
        c.drawString(x0 + pad, y0 + pad, i18n.t(hint_key))
    if not labels:
        c.setFont("Helvetica", 14)
        c.drawString(margin, ph - margin - 20, "Keine Tische angelegt.")
    c.save()
    return buf.getvalue()


def gast_pdf(items: list[tuple[str, str]], event_name: str = "", table_word: str = "Tisch") -> bytes:
    """QR-Bestellung am Tisch (T22): dieselben Karten, QR mit der Adresse der Gästeseite (Tisch signiert)."""
    return tables_pdf([lb for lb, _ in items], event_name, table_word, data=dict(items), hint_key="app.gast.pdf_hint")
