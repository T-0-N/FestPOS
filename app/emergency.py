"""Notfall-Passwort (Benutzer «admin») nur als Prüfwert (T-0-N/FestPOS#15).

In festpos.conf steht nicht mehr das Passwort, sondern ADMIN_PASSWORD_HASH (scrypt wie bei den Benutzern) und
ADMIN_PASSWORD_LAENGE (für die Mindestlänge). Das Salz ist aus CLUSTER_SECRET abgeleitet: alle Pi einer Installation
rechnen denselben Prüfwert – darauf beruht der Schlüssel der Sitzungen (gilt auf allen Pi). Ein Passwort im Klartext
(ältere Version, von Hand eingetragen) wandelt festpos-node beim nächsten Start um und entfernt es aus der Datei:

    python3 -m app.emergency --conf /boot/firmware/festpos.conf [--tls-key /etc/festpos/tls/server.key]

Mindestens MIN_LEN (16) Zeichen. Kürzer: Gibt es einen eigenen, aktiven Admin-Benutzer, ist der Notfall-Login
gesperrt (sonst wäre nach dem Update niemand mehr drin – dann nur Warnung). Der Notfall-Login geht nur aus dem
FestPOS-Netz und am Pi selbst (127.0.0.1, z.B. SSH-Tunnel), nie über den Tunnel der QR-Bestellung.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import sys
from pathlib import Path

# 16 Zeichen; nur die Tests setzen weniger (FESTPOS_ADMIN_PASSWORD_MIN – festpos-node schreibt das nie)
MIN_LEN = max(4, int(os.environ.get("FESTPOS_ADMIN_PASSWORD_MIN", "16") or 16))
PLACEHOLDERS = frozenset({"", "admin", "bitte-aendern"})
_N, _R, _P = 2 ** 14, 8, 1                      # wie auth.hash_password


def salt(secret: str) -> bytes:
    """Salz pro Installation (aus CLUSTER_SECRET): gleich auf allen Pi, anders in jeder Installation."""
    return hmac.new(secret.encode(), b"festpos-notfall-passwort", hashlib.sha256).digest()[:16]


def make_hash(password: str, secret: str) -> str:
    s = salt(secret)
    h = hashlib.scrypt(password.encode(), salt=s, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${s.hex()}${h.hex()}"


def check(password: str, stored: str) -> bool:
    from . import auth
    return auth.verify_password(password, stored)


def local_ok(ip: str | None) -> bool:
    """Notfall-Login von hier erlaubt? FestPOS-Netz oder der Pi selbst."""
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return True                                  # keine Adresse (lokaler Socket, Testclient): am Pi selbst
    if a.is_loopback:
        return True
    from . import printer_net
    net = printer_net.main_net()                     # NODE_IP mit NET_CIDR; unbekannt (Entwicklung): private Adressen
    return a in net if net is not None else a.is_private


# -- festpos.conf umwandeln (festpos-node, als root) --------------------------------------------------------------
def effective_secret(conf_text: str, tls_key: Path | None) -> str:
    """CLUSTER_SECRET wie die App es nimmt: aus festpos.conf, sonst (Platzhalter) aus dem Server-Schlüssel abgeleitet."""
    from . import backup, timesync
    s = backup.conf_value(conf_text, "CLUSTER_SECRET") or ""
    if timesync.secret_usable(s):
        return s
    try:
        return backup.derived_secret(tls_key.read_bytes()) if tls_key else s
    except OSError:
        return s


def convert_text(text: str, secret: str) -> str | None:
    """festpos.conf mit Klartext-Passwort -> mit Prüfwert (None: nichts zu tun – kein Klartext oder Platzhalter)."""
    from . import backup
    pw = backup.conf_value(text, "ADMIN_PASSWORD")
    if pw is None or pw.strip() in PLACEHOLDERS or pw == "im-container":    # im-container: verschlüsselt (#3)
        return None
    text = backup.conf_set(text, "ADMIN_PASSWORD_HASH", make_hash(pw, secret))
    text = backup.conf_set(text, "ADMIN_PASSWORD_LAENGE", str(len(pw)))
    return backup.conf_remove(text, "ADMIN_PASSWORD")


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 4) or argv[0] != "--conf" or (len(argv) == 4 and argv[2] != "--tls-key"):
        print(__doc__.split("\n\n")[2], file=sys.stderr)
        return 2
    conf = Path(argv[1])
    text = conf.read_text(encoding="utf-8", errors="surrogateescape")
    new = convert_text(text, effective_secret(text, Path(argv[3]) if len(argv) == 4 else None))
    if new is None:
        return 0
    tmp = conf.with_name(conf.name + ".neu")
    tmp.write_text(new, encoding="utf-8", errors="surrogateescape")
    tmp.replace(conf)
    print("Notfall-Passwort in festpos.conf durch einen Prüfwert ersetzt (ADMIN_PASSWORD_HASH)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
