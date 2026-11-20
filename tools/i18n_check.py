#!/usr/bin/env python3
"""Sprachdateien prüfen (läuft in tools/check.sh und in der CI).

  python3 tools/i18n_check.py            prüfen, Rückgabe 1 bei Fehlern
  python3 tools/i18n_check.py --fehlend  nur die fehlenden Schlüssel auflisten (beim Entwickeln)
  python3 tools/i18n_check.py --offen fr noch nicht übersetzte Texte einer Sprache (mit dem deutschen Text)

Geprüft wird:
  - jede Zeichenkette im Code, die wie ein Schlüssel aussieht ("app.cart.title"), steht in de.json
  - de.json enthält keine Schlüssel, die nirgends mehr vorkommen
  - andere Sprachen: keine unbekannten Schlüssel, gleiche {Platzhalter}, gleiche HTML-Tags (<b>, <code>, <a …>),
    Mehrzahl mit "other"
  - Bon-Texte (bon.*) lassen sich auf dem Bondrucker drucken (Zeichensatz PC858, z.B. kein «œ»)
  - fehlende Übersetzungen in anderen Sprachen sind erlaubt (dann gilt Deutsch) und werden gezählt
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "app" / "i18n"
SOURCE = "de"
AREAS = ("app", "admin", "status", "bon", "err", "srv", "common", "ledger")
KEY_RE = re.compile(r"""(["'`])((?:%s)\.[a-z0-9_]+(?:\.[a-z0-9_]+)+)\1""" % "|".join(AREAS))
PH_RE = re.compile(r"\{(\w+)\}")
CODE_FILES = [*sorted((ROOT / "app").glob("*.py")),
              *sorted(p for p in (ROOT / "static").rglob("*.js") if "vendor" not in p.parts),   # auch static/admin/
              *sorted((ROOT / "static").glob("*.html"))]


def used_keys() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in CODE_FILES:
        if f.name in ("i18n.js", "i18n.py"):
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in KEY_RE.finditer(line):
                out.setdefault(m.group(2), []).append(f"{f.relative_to(ROOT)}:{n}")
            for m in re.finditer(r'data-i18n(?:-attr)?="([^"]+)"', line):
                for part in m.group(1).split(";"):
                    key = part.split(":")[-1].strip()
                    if key:
                        out.setdefault(key, []).append(f"{f.relative_to(ROOT)}:{n}")
    return out


def placeholders(v) -> set[str]:
    if isinstance(v, dict):
        return set().union(*(placeholders(x) for x in v.values())) if v else set()
    return set(PH_RE.findall(str(v)))


TAG_RE = re.compile(r"<(/?[a-zA-Z]+)")


def unprintable(v) -> str:
    """Zeichen, die der Bondrucker (PC858) nicht kennt – nach denselben Ersetzungen wie beim Drucken."""
    sys.path.insert(0, str(ROOT))
    from app.escpos import CODEPAGE_PY, Bon
    vals = v.values() if isinstance(v, dict) else [v]
    return "".join(sorted({c for x in vals for c in str(x).translate(Bon._SUBST)
                           if not c.encode(CODEPAGE_PY, errors="ignore")}))


def tags(v) -> list[str]:
    """HTML-Tags in Reihenfolge des Vorkommens (bei Mehrzahl: die der Form «other»)."""
    if isinstance(v, dict):
        v = v.get("other", "")
    return sorted(TAG_RE.findall(str(v)))


def main(argv: list[str]) -> int:
    errors: list[str] = []
    try:
        src = json.loads((I18N / f"{SOURCE}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"FEHLER: {SOURCE}.json nicht lesbar: {e}")
        return 1
    keys = {k for k in src if k != "_meta"}
    if "--offen" in argv:
        lang = argv[argv.index("--offen") + 1]
        cat = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8")) if (I18N / f"{lang}.json").exists() else {}
        for k in sorted(keys):
            if cat.get(k) in (None, ""):
                print(json.dumps({k: src[k]}, ensure_ascii=False)[1:-1])
        return 0
    used = used_keys()
    missing = sorted(set(used) - keys)
    if "--fehlend" in argv:
        for k in missing:
            print(k, used[k][0])
        return 0
    for k in missing:
        errors.append(f"Text fehlt in {SOURCE}.json: {k}  ({', '.join(used[k][:3])})")
    for k in sorted(keys - set(used)):
        errors.append(f"Schlüssel wird nirgends verwendet: {k}")
    for k, v in src.items():
        if k == "_meta":
            continue
        if isinstance(v, dict) and "other" not in v:
            errors.append(f"{SOURCE}.json {k}: Mehrzahl ohne \"other\"")
        if not isinstance(v, (str, dict)) or v == "":
            errors.append(f"{SOURCE}.json {k}: leerer oder ungültiger Text")
        elif k.startswith("bon.") and (bad := unprintable(v)):
            errors.append(f"{SOURCE}.json {k}: Zeichen {bad!r} kann der Bondrucker nicht drucken")
    for f in sorted(I18N.glob("*.json")):
        if f.stem == SOURCE:
            continue
        try:
            cat = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            errors.append(f"{f.name} nicht lesbar: {e}")
            continue
        if not isinstance(cat.get("_meta"), dict) or not cat["_meta"].get("name"):
            errors.append(f"{f.name}: \"_meta\": {{\"name\": …, \"locale\": …}} fehlt")
        todo = 0
        for k, v in cat.items():
            if k == "_meta":
                continue
            if k not in src:
                errors.append(f"{f.name}: unbekannter Schlüssel {k}")
            elif v in (None, ""):
                todo += 1
            elif placeholders(v) != placeholders(src[k]):
                errors.append(f"{f.name} {k}: Platzhalter {sorted(placeholders(v))} statt {sorted(placeholders(src[k]))}")
            elif tags(v) != tags(src[k]):
                errors.append(f"{f.name} {k}: HTML-Tags {tags(v)} statt {tags(src[k])}")
            elif isinstance(v, dict) and "other" not in v:
                errors.append(f"{f.name} {k}: Mehrzahl ohne \"other\"")
            elif k.startswith("bon.") and (bad := unprintable(v)):
                errors.append(f"{f.name} {k}: Zeichen {bad!r} kann der Bondrucker nicht drucken")
        todo += len(keys - set(cat))
        if todo:
            print(f"HINWEIS: {f.name}: {todo} Texte noch nicht übersetzt (es gilt Deutsch)")
    for e in errors:
        print("FEHLER:", e)
    print(f"Sprachdateien: {len(keys)} Texte, {len(used)} im Code verwendet" + (f", {len(errors)} Fehler" if errors else ", ok"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
