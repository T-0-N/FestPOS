"""FestPOS-Sicherung (.festpos) am PC entschlüsseln und entpacken (T8).

  python3 tools/sicherung_lesen.py festpos-a-20261028-153000.festpos ziel/

Fragt nach dem Passwort (Sicherungs-Passwort vom Ausdruck oder das beim Herunterladen gewählte) und legt den Inhalt in
den Zielordner: festpos.sqlite (z.B. mit «DB Browser for SQLite» ansehen), tls/ (Zertifikate und Schlüssel),
conf.json (CLUSTER_SECRET, Notfall-Passwort), update-keys/. Der Ordner enthält alle Schlüssel – danach löschen.
Braucht Python mit dem Paket cryptography (pip install cryptography).
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import backup  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    data = Path(argv[1]).read_bytes()
    head = backup.read_header(data)
    if head is None:
        print("Das ist keine verschlüsselte FestPOS-Sicherung (.festpos).", file=sys.stderr)
        return 2
    print(f"Sicherung von Pi {str(head.get('node') or '?').upper()}, Version {head.get('app') or '?'}")
    try:
        content = backup.unpack(data, getpass.getpass("Passwort: "))
    except backup.BadPassword:
        print("Passwort falsch – oder die Datei ist beschädigt.", file=sys.stderr)
        return 1
    target = Path(argv[2])
    for name, value in content.items():
        path = (target / name).resolve()
        if target.resolve() not in path.parents:              # nichts ausserhalb des Zielordners
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    print(f"{len(content)} Dateien nach {target} entpackt (enthält alle Schlüssel – danach löschen).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
