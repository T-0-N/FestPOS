"""Vorschau eines Bons als Bild (Verwaltung › Einstellungen › Bon-Design): liest dieselben ESC/POS-Bytes, die an den
Drucker gehen (app/escpos.py), und zeichnet sie wie ein Epson TM-T20III – Schrift A (12 × 24 Punkte) und B (9 × 17),
Vergrösserung, fett, unterstrichen, weiss auf schwarz, Ausrichtung, Zeilenabstand, Rasterbilder (Logo, Durchgestrichenes) und QR-Codes.
Nur die Befehle, die FestPOS selbst schreibt; Unbekanntes wird übersprungen."""
from __future__ import annotations

import io

from .escpos import CODEPAGE_PY, DEFAULT_SPACING_DOTS, motion_dots

CW, CH = 12, 24                      # Schrift A
CELL = {False: (CW, CH), True: (9, 17)}          # Schrift A, Schrift B (klein)
MARGIN = 16


def _font(size: int, bold: bool):
    from PIL import ImageFont

    from .print_service import _vera
    try:
        return ImageFont.truetype(_vera(bold), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def render_png(data: bytes, width_dots: int = 576, *, offsets=None, ys: dict | None = None) -> bytes:
    """offsets: Stellen in den Druckerdaten (Bon-Baukasten: Anfang/Ende der Bausteine) – ys bekommt dazu die Höhe im
    Bild (Bildpunkte von oben)."""
    from PIL import Image, ImageDraw

    from . import qr as qr_mod
    canvas = Image.new("L", (width_dots + 2 * MARGIN, 6000), 255)
    draw = ImageDraw.Draw(canvas)
    st = {"align": 0, "bold": False, "w": 1, "h": 1, "ul": False, "inv": False, "spacing": DEFAULT_SPACING_DOTS,
          "small": False}
    marks = sorted(set(offsets or ()))
    mi = 0
    y = MARGIN
    line: list[tuple[str, dict]] = []
    qr_data, qr_mod_size = b"", 6
    fonts: dict = {}

    def flush(force: bool = True) -> None:
        nonlocal y, line
        if not line and not force:
            return
        width = sum(CELL[s["small"]][0] * s["w"] for _, s in line)
        height = max([CELL[s["small"]][1] * s["h"] for _, s in line] or [CELL[st["small"]][1]])
        align = line[0][1]["align"] if line else st["align"]
        x = MARGIN + (0 if align == 0 else (width_dots - width) // 2 if align == 1 else width_dots - width)
        for ch, s in line:
            cw, chh = CELL[s["small"]][0] * s["w"], CELL[s["small"]][1] * s["h"]
            top = y + height - chh
            if s["inv"]:
                draw.rectangle((x, top, x + cw - 1, top + chh - 1), fill=0)
            if ch.strip():                               # Zeichen in der Grundgrösse zeichnen, dann strecken (wie der Drucker)
                key = (ch, s["bold"])
                if key not in fonts:
                    f = _font(int(CH * 2 * 0.8), s["bold"])
                    tw = int(draw.textlength(ch, font=f)) + 2
                    gw = max(CW * 2, tw)                     # breite Zeichen (m, W): in die Zelle zusammendrücken
                    g = Image.new("L", (gw, CH * 2), 255)
                    ImageDraw.Draw(g).text(((gw - tw) / 2 + 1, CH * 2 * 0.06), ch, font=f, fill=0)
                    fonts[key] = g
                glyph = fonts[key].resize((cw, chh))
                if s["inv"]:
                    from PIL import ImageOps
                    glyph = ImageOps.invert(glyph)
                    mask = glyph.point(lambda v: 255 if v > 128 else 0)
                    canvas.paste(255, (x, top), mask)
                else:
                    mask = glyph.point(lambda v: 255 if v < 170 else 0)
                    canvas.paste(0, (x, top), mask)
            if s["ul"]:
                draw.line((x, top + chh - 2, x + cw - 1, top + chh - 2), fill=0, width=2)
            x += cw
        y += max(height, round(st["spacing"]))
        line = []

    i, n = 0, len(data)
    while i < n and y < canvas.height - 400:
        while ys is not None and mi < len(marks) and marks[mi] <= i:
            ys[marks[mi]] = y
            mi += 1
        c = data[i]
        if c == 0x1B and i + 1 < n:                          # ESC
            cmd = data[i + 1]
            if cmd == 0x40:                                  # @ Initialisieren
                st.update(align=0, bold=False, w=1, h=1, ul=False, inv=False, spacing=DEFAULT_SPACING_DOTS, small=False)
                i += 2
            elif cmd in (0x74, 0x61, 0x45, 0x2D, 0x33, 0x4A, 0x64, 0x4D) and i + 2 < n:
                v = data[i + 2]
                if cmd == 0x4D:                              # M: Schrift A oder B
                    st["small"] = bool(v & 1)
                elif cmd == 0x61:
                    st["align"] = v
                elif cmd == 0x45:
                    st["bold"] = bool(v)
                elif cmd == 0x2D:
                    st["ul"] = bool(v)
                elif cmd == 0x33:                            # Zeilenabstand in 1/360 Zoll (wie der Drucker)
                    st["spacing"] = motion_dots(v)
                elif cmd == 0x4A:
                    flush(False)
                    y += round(motion_dots(v))
                elif cmd == 0x64:
                    flush(False)
                    y += round(v * st["spacing"])
                i += 3
            elif cmd == 0x32:                                # 2: Standard-Zeilenabstand (1/6 Zoll)
                st["spacing"] = DEFAULT_SPACING_DOTS
                i += 2
            elif cmd == 0x28 and i + 4 < n:                  # ( A …: Summer
                ln = data[i + 3] | (data[i + 4] << 8)
                i += 5 + ln
            else:
                i += 2
        elif c == 0x1D and i + 1 < n:                        # GS
            cmd = data[i + 1]
            if cmd == 0x21 and i + 2 < n:                    # ! Grösse
                v = data[i + 2]
                st["w"], st["h"] = (v >> 4) + 1, (v & 0x0F) + 1
                i += 3
            elif cmd == 0x42 and i + 2 < n:                  # B weiss auf schwarz
                st["inv"] = bool(data[i + 2])
                i += 3
            elif cmd == 0x56:                                # V Schneiden: Ende des Bons
                flush(False)
                y += 24
                break
            elif cmd == 0x76 and i + 7 < n:                  # v 0 Rasterbild
                flush(False)
                wb = data[i + 4] | (data[i + 5] << 8)
                h = data[i + 6] | (data[i + 7] << 8)
                raw = data[i + 8:i + 8 + wb * h]
                img = Image.frombytes("1", (wb * 8, h), raw)
                from PIL import ImageOps
                img = ImageOps.invert(img.convert("L"))      # Bit 1 = drucken (schwarz)
                x = MARGIN + (0 if st["align"] == 0 else (width_dots - wb * 8) // 2 if st["align"] == 1 else width_dots - wb * 8)
                canvas.paste(img, (x, y))
                y += h
                i += 8 + wb * h
            elif cmd == 0x28 and i + 6 < n and data[i + 2] == 0x6B:     # ( k QR-Code
                ln = data[i + 3] | (data[i + 4] << 8)
                fn = data[i + 6]
                params = data[i + 7:i + 3 + 2 + ln]
                if fn == 67 and params:
                    qr_mod_size = params[0]
                elif fn == 80:
                    qr_data = params[1:]
                elif fn == 81 and qr_data:
                    flush(False)
                    m = qr_mod.qr_matrix(qr_data.decode("utf-8", "replace"))
                    size = len(m) * qr_mod_size
                    x = MARGIN + (0 if st["align"] == 0 else (width_dots - size) // 2 if st["align"] == 1 else width_dots - size)
                    for r, row in enumerate(m):
                        for col, dark in enumerate(row):
                            if dark:
                                draw.rectangle((x + col * qr_mod_size, y + r * qr_mod_size,
                                                x + (col + 1) * qr_mod_size - 1, y + (r + 1) * qr_mod_size - 1), fill=0)
                    y += size + 8
                i += 5 + ln
            else:
                i += 2
        elif c == 0x0A:
            flush()
            i += 1
        else:
            ch = bytes([c]).decode(CODEPAGE_PY, "replace")
            line.append((ch, dict(st)))
            i += 1
    flush(False)
    while ys is not None and mi < len(marks):
        ys[marks[mi]] = y
        mi += 1
    out = io.BytesIO()
    canvas.crop((0, 0, canvas.width, min(canvas.height, y + MARGIN))).save(out, "PNG", optimize=True)
    return out.getvalue()
