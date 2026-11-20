"""Texte in alle Sprachdateien (app/i18n/*.json) eintragen oder entfernen.

  python3 tools/i18n_add.py texte.json
  python3 tools/i18n_add.py --entfernen app.pay.alt app.pay.alt2

texte.json:
  {"app.pay.next": {"de": "Weiter", "en": "Next", "fr": "Suivant", "it": "Avanti", "rm": "Inavant"},
   "app.pay.left": {"de": {"one": "{count} offen", "other": "{count} offen"}, "en": {...}, ...}}

"de" ist Pflicht. Fehlt eine andere Sprache, bleibt deren bisheriger Text; ist keiner da, gilt Deutsch
(fehlende Übersetzungen sind erlaubt und werden von i18n_check gezählt). Die Schlüssel werden sortiert.
Danach: python3 tools/i18n_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

I18N = Path(__file__).resolve().parent.parent / "app" / "i18n"
FILES = {"de": "de.json", "en": "en.json", "fr": "fr.json", "it": "it.json", "rm": "rm-vallader.json"}


def _load(lang: str) -> dict:
    return json.loads((I18N / FILES[lang]).read_text(encoding="utf-8"))


def _save(lang: str, data: dict, order: list[str]) -> None:
    meta = data.pop("_meta")
    rest = [k for k in data if k not in order]               # unbekannte Schlüssel: nicht still löschen
    out = {"_meta": meta, **{k: data[k] for k in order if k in data}, **{k: data[k] for k in sorted(rest)}}
    (I18N / FILES[lang]).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    files = {lang: _load(lang) for lang in FILES}
    if argv[0] == "--entfernen":
        for lang in FILES:
            for k in argv[1:]:
                files[lang].pop(k, None)
        print(f"{len(argv) - 1} Schlüssel entfernt")
    else:
        new = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        bad = [k for k, v in new.items() if not isinstance(v, dict) or "de" not in v or set(v) - set(FILES)]
        if bad:
            print("Jeder Eintrag braucht {\"de\": …} und nur die Sprachen " + ", ".join(FILES) + ":", *bad, sep="\n  ")
            return 1
        for k, v in new.items():
            for lang, text in v.items():
                files[lang][k] = text
        print(f"{len(new)} Schlüssel eingetragen")
    order = sorted(k for k in files["de"] if k != "_meta")
    for lang, data in files.items():
        _save(lang, data, order)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
