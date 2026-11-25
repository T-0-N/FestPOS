"""Release-Text aus CHANGELOG.md für einen Tag (für den Release-Entwurf, .github/workflows/release.yml).

  python3 tools/release_notes.py v1.1.0         -> Abschnitt «## [1.1.0]»
  python3 tools/release_notes.py v1.1.0-rc.2    -> Abschnitt «## [1.1.0]», sonst «## [Unveröffentlicht]», mit Hinweis

Dazu immer der Abschnitt «Bekannte Einschränkungen» aus README.md und wie die Dateien geprüft werden.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section(text: str, title: str, level: str = "##") -> str | None:
    m = re.search(rf"^{level} {re.escape(title)}[^\n]*\n(.*?)(?=^{level} |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def notes(tag: str) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    base, _, rc = version.partition("-rc.")
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    body = section(log, f"[{base}]") or section(log, "[Unveröffentlicht]") or "Siehe CHANGELOG.md."
    out = []
    if rc:
        out.append(f"> **Testversion {version}** – nur für den Probelauf. «Update aus dem Internet» und der Raspberry Pi "
                   "Imager nehmen sie nicht automatisch.\n")
    out.append(body)
    limits = section((ROOT / "README.md").read_text(encoding="utf-8"), "Bekannte Einschränkungen")
    if limits and "Bekannte Einschränkungen" not in body:
        out.append("\n### Bekannte Einschränkungen\n\n" + limits)
    out.append("""
### Dateien

| Datei | Wofür |
|---|---|
| `festpos-<VERSION>.img.xz` | Neue SD-Karte mit dem Raspberry Pi Imager (Liste für den Imager: `festpos-os-list.json`) |
| `festpos.zip` + `festpos.zip.sig` | Update für laufende Pi (Verwaltung › Wartung › Update, USB-Stick oder «Aus dem Internet») |
| `festpos-tap.apk` | Begleit-App für Karte/TWINT mit NFC über Payrexx (nur mit Payrexx, ungetestet) |
| `FestPOS-Kurzanleitung.pdf` | Kurzanleitung für Bedienung und Festleitung |
| `SHA256SUMS` | Prüfsummen aller Dateien |
| `festpos.zip.cdx.json`, `festpos-<VERSION>.img.xz.cdx.json` | Paketliste (SBOM, CycloneDX): Python-Pakete, rqlite, beim Image alle Debian-Pakete |

Die Pi nehmen nur ein `festpos.zip` an, das mit dem Update-Schlüssel des Projekts signiert ist (`festpos.zip.sig`).

### Herkunft prüfen

ZIP, Image, App und Paketlisten hat der Release-Workflow dieses Repos gebaut; GitHub bestätigt das mit einem
Herkunftsnachweis. Prüfen mit der GitHub-Kommandozeile (`gh`):

```
gh attestation verify festpos.zip -R T-0-N/FestPOS
gh attestation verify festpos-<VERSION>.img.xz -R T-0-N/FestPOS
```""")
    return "\n".join(out).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    sys.stdout.write(notes(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
