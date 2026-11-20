"""Pseudo-Sprache für Tests (bewährte Praxis bei Übersetzungen): jeder Text aus der Sprachdatei wird
⟦mít Ákzéntéñ⟧ eingerahmt. Steht danach auf einer Seite noch Text ohne Rahmen, ist er fest im Code
geschrieben (nicht übersetzbar). Platzhalter {name} und HTML-Tags bleiben unverändert."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCENT = str.maketrans("aeiouAEIOUcnyz", "áéíóúÁÉÍÓÚçñýž")
_KEEP = re.compile(r"(\{\w+\}|<[^>]+>|&\w+;|%[a-zA-Z])")       # Platzhalter, Tags, Entities, strftime


def pseudo_text(s: str, open_: str = "⟦", close: str = "⟧") -> str:
    parts = _KEEP.split(s)
    return open_ + "".join(p if _KEEP.fullmatch(p) else p.translate(ACCENT) for p in parts) + close


def pseudo_catalog(src: dict, name: str = "Pseudo", open_: str = "⟦", close: str = "⟧") -> dict:
    out = {}
    for k, v in src.items():
        if k == "_meta":
            continue
        out[k] = ({c: pseudo_text(x, open_, close) for c, x in v.items()} if isinstance(v, dict)
                  else pseudo_text(v, open_, close))
    out["_meta"] = {"name": name, "locale": "de-CH"}
    return out


def write_pseudo(folder: Path, code: str = "qps", **kw) -> Path:
    src = json.loads((ROOT / "app" / "i18n" / "de.json").read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{code}.json"
    path.write_text(json.dumps(pseudo_catalog(src, **kw), ensure_ascii=False), encoding="utf-8")
    return path


def strip_translated(text: str, open_: str = "⟦", close: str = "⟧") -> str:
    """Alle (auch verschachtelten) übersetzten Abschnitte entfernen – übrig bleibt fester Text und Daten."""
    pat = re.compile(re.escape(open_) + r"[^" + re.escape(open_ + close) + r"]*" + re.escape(close))
    prev = None
    while prev != text:
        prev, text = text, pat.sub(" ", text)
    return text


def leftover_words(text: str, allowed: set[str], open_: str = "⟦", close: str = "⟧") -> list[str]:
    """Wörter (ab 3 Buchstaben) ausserhalb der Rahmen, die keine erlaubten Daten sind."""
    rest = strip_translated(text, open_, close)
    words = re.findall(r"[A-Za-zÄÖÜäöüÉéèàç]{3,}", rest)
    return sorted({w for w in words if w not in allowed})
