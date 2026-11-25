"""Saison-Archiv von FestPOS am PC öffnen und prüfen (T-0-N/FestPOS#12).

  python3 tools/archiv.py festpos-saison-….festpos            Passwort wird abgefragt, Inhalt nach ./<name>/
  python3 tools/archiv.py festpos-saison-….festpos --ziel D   Inhalt nach D

Entschlüsselt das Archiv (AES-256-GCM, scrypt – wie eine Sicherung; braucht das Python-Paket «cryptography»), prüft
die SHA-256 jeder Datei gegen archiv.json und legt alles ab: abschluesse.json, quittungen.json, kopien.json,
pdf/abschluss-*.pdf. Die Prüfsummen der Buchungen (HMAC) lassen sich nur auf einem Pi derselben Installation
nachrechnen: Verwaltung › Tagesabschluss › Saison abschliessen › Archiv hochladen.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import backup, saison_inhalt  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Saison-Archiv von FestPOS öffnen und prüfen")
    ap.add_argument("datei")
    ap.add_argument("--ziel")
    ap.add_argument("--passwort-datei", help="Passwort aus dieser Datei (statt Abfrage)")
    a = ap.parse_args(argv)
    data = Path(a.datei).read_bytes()
    pw = Path(a.passwort_datei).read_text().strip() if a.passwort_datei else getpass.getpass("Passwort des Archivs: ")
    try:
        members = backup.unpack(data, pw)
    except backup.BadPassword:
        print("Passwort falsch oder Datei verändert.", file=sys.stderr)
        return 2
    except ValueError:
        print("Das ist kein Archiv von FestPOS.", file=sys.stderr)
        return 2
    got = saison_inhalt.records(members)                 # dieselben Regeln wie die Prüfung auf dem Pi
    if not got or not saison_inhalt.complete(members, *got):
        print("FEHLER: Archiv unvollständig – Inhaltsangabe archiv.json, Abschlüsse, Quittungen, Kopien oder PDF "
              "fehlen oder passen nicht zusammen.", file=sys.stderr)
        return 1
    info = got[0]
    bad = saison_inhalt.bad_digests(members, info)
    ziel = Path(a.ziel or Path(a.datei).name.removesuffix(".festpos"))
    root = ziel.resolve()
    for name, content in members.items():
        out = (root / name).resolve()
        if out == root or not out.is_relative_to(root):
            continue                                       # kein Pfad ausserhalb des Ziels (auch nicht «ziel-x/…»)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    c, r = info.get("closings", {}), info.get("receipts", {})
    print(f"Archiv: {info.get('event') or 'FestPOS'}, erstellt {info.get('created_at')} auf Pi {str(info.get('node')).upper()}")
    print(f"Tagesabschlüsse {c.get('from')}–{c.get('to')} ({c.get('count')}), Quittungen {r.get('count')} "
          f"(Nr. {r.get('first_no')}–{r.get('last_no')}), Kopien {r.get('copies')}")
    print(f"Inhalt in {ziel}/")
    if bad:
        print(f"FEHLER: Prüfsumme stimmt nicht: {', '.join(bad)}", file=sys.stderr)
        return 1
    print("Alle Dateien vollständig (SHA-256 stimmt).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
