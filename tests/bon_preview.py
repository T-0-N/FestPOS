"""Bon-Vorschau als Bild: interpretiert die ESC/POS-Bytes (Fett, Grösse, Unterstrich, Ausrichtung,
Schnitt) und zeichnet sie wie auf 80-mm-Thermopapier. Nur für Entwicklung/Doku.

    python -m tests.bon_preview out.png
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from app.escpos import CODEPAGE_PY

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
CW, CH = 12, 24            # Zeichenzelle Font A (12x24 Punkte)


def render(data: bytes, width_chars: int = 48) -> Image.Image:
    lines: list[list[tuple[str, bool, int, int, bool]]] = [[]]
    aligns = ["left"]
    bold = ul = False
    w = h = 1
    align = "left"
    cuts: list[int] = []
    gaps: dict[int, int] = {}
    images: dict = {}
    i = 0
    while i < len(data):
        c = data[i:i + 1]
        if c == b"\x1b":
            cmd = data[i + 1:i + 2]
            arg = data[i + 2] if i + 2 < len(data) else 0
            if cmd == b"@":
                bold = ul = False; w = h = 1; align = "left"; i += 2; continue
            if cmd == b"(":
                i += 5 + data[i + 3] + 256 * data[i + 4]; continue
            if cmd == b"E": bold = bool(arg)
            elif cmd == b"-": ul = bool(arg)
            elif cmd == b"a":
                align = ["left", "center", "right"][arg]
                if not lines[-1]:
                    aligns[-1] = align
            elif cmd == b"J":
                gaps[len(lines) - 1] = gaps.get(len(lines) - 1, 0) + arg
            elif cmd == b"d":
                for _ in range(arg):
                    lines.append([]); aligns.append(align)
            i += 3 if cmd != b"@" else 2
        elif c == b"\x1d":
            cmd = data[i + 1:i + 2]
            if cmd == b"(":
                n = data[i + 3] + 256 * data[i + 4]
                if data[i + 6] == 80:
                    lines[-1].append(("[QR " + data[i + 8:i + 5 + n].decode() + "]", False, 1, 1, False))
                i += 5 + n; continue
            if cmd == b"!":
                v = data[i + 2]; w, h = (v >> 4) + 1, (v & 15) + 1; i += 3; continue
            if cmd == b"v" and data[i + 2:i + 3] == b"0":     # Rasterbild (z.B. durchgestrichene Storno-Positionen)
                wb, hh = data[i + 4] + 256 * data[i + 5], data[i + 6] + 256 * data[i + 7]
                pic = Image.frombytes("1", (wb * 8, hh), bytes(data[i + 8:i + 8 + wb * hh]))
                if lines[-1]:
                    lines.append([]); aligns.append(align)
                images[len(lines) - 1] = pic
                lines.append([]); aligns.append(align)
                i += 8 + wb * hh; continue
            if cmd == b"V":
                cuts.append(len(lines) - (0 if lines[-1] else 1)); i += 4; continue
            i += 3
        elif c == b"\n":
            lines.append([]); aligns.append(align); i += 1
        else:
            lines[-1].append((c.decode(CODEPAGE_PY), bold, w, h, ul)); i += 1

    pad = 24
    rows = []
    for ln in lines:
        idx = len(rows)
        rows.append(images[idx].height if idx in images else max([s[3] for s in ln] or [1]) * CH + 6 + gaps.get(idx, 0))
    img = Image.new("RGB", (width_chars * CW + 2 * pad, sum(rows) + 2 * pad + 20 * len(cuts)), "white")
    d = ImageDraw.Draw(img)
    fonts = {}

    def font(b, hh):
        k = (b, hh)
        if k not in fonts:
            fonts[k] = ImageFont.truetype(FONT_B if b else FONT, int(20 * hh))
        return fonts[k]

    y = pad
    for idx, (ln, al) in enumerate(zip(lines, aligns)):
        lh = rows[idx]
        if idx in images:                                     # Bit 1 = schwarz drucken
            pic = images[idx].convert("L").point(lambda v: 0 if v else 255)
            img.paste(pic, (pad, y))
            y += lh
            continue
        total = sum(s[2] for s in ln) * CW
        x = pad + {"left": 0, "center": (width_chars * CW - total) // 2, "right": width_chars * CW - total}[al]
        base = y + lh - 6
        for ch, b, ww, hh, u in ln:
            glyph = Image.new("L", (CW, CH), 0)
            ImageDraw.Draw(glyph).text((0, 0), ch, font=font(b, 1), fill=255)
            glyph = glyph.resize((CW * ww, CH * hh))
            img.paste((0, 0, 0), (x, base - CH * hh + 4), glyph)
            if u:
                d.line((x, base + 1, x + CW * ww, base + 1), fill="black", width=2)
            x += CW * ww
        y += lh
        if idx in cuts:
            d.line((0, y + 8, img.width, y + 8), fill=(150, 150, 150), width=2)
            d.text((6, y), "✂", fill=(120, 120, 120), font=font(False, 1))
            y += 20
    return img.crop((0, 0, img.width, y + pad))
