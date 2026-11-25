"""Geheimnisse im verschlüsselten Datenbereich (T-0-N/FestPOS#3) – gemeinsam für festpos-krypt, festpos-node,
festpos-wiederherstellen und das Kartenpaket.

Ist der Datenbereich verschlüsselt, stehen die geheimen Werte nicht mehr auf der Boot-Partition: in festpos.conf steht
statt des Werts MARKER, die Werte selbst im Container («geheim»/festpos.conf, die Schlüsseldateien in «geheim»/tls).
Trägt jemand am PC einen neuen Wert in festpos.conf ein, übernimmt festpos-krypt ihn beim nächsten Start (er gilt).
Für den laufenden Pi legt festpos-krypt beim Start eine zusammengesetzte festpos.conf und die Schlüssel in RUN_DIR
(Arbeitsspeicher, nur root): festpos-node liest dort, root-Helfer nehmen merged() und key_file().
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from . import backup

MARKER = "im-container"
# Werte aus festpos.conf, die verschlüsselt liegen (das Notfall-Passwort im Klartext wird vorher zum Prüfwert)
CONF_KEYS = ("CLUSTER_SECRET", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "ADMIN_PASSWORD_LAENGE", "TUNNEL_TOKEN")
KEY_FILES = ("server.key", "device-ca.key")
RUN_DIR = Path(os.environ.get("FESTPOS_GEHEIM_RUN", "/run/festpos-geheim/quelle"))    # beim Start entpackt


# Wiederherstellungs-Schlüssel: 8 Gruppen à 4 Zeichen (160 Bit), ohne verwechselbare Zeichen (0/O, 1/I)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def recovery_new() -> str:
    c = "".join(secrets.choice(ALPHABET) for _ in range(32))
    return "-".join(c[i:i + 4] for i in range(0, 32, 4))


def recovery_norm(s: str) -> str | None:
    """Eingabe (Leerzeichen, Kleinbuchstaben, mit oder ohne Bindestriche) -> «ABCD-…» (so steht er im Container) oder
    None. Der Schlüssel gilt nur genau so geschrieben, mit Bindestrichen und Grossbuchstaben."""
    c = "".join(ch for ch in str(s).upper() if ch.isalnum())
    if len(c) != 32 or any(ch not in ALPHABET for ch in c):
        return None
    return "-".join(c[i:i + 4] for i in range(0, 32, 4))


def secret_values(text: str) -> dict[str, str]:
    """Die geheimen Werte aus einer festpos.conf (ohne MARKER)."""
    out = {}
    for k in CONF_KEYS:
        v = backup.conf_value(text, k)
        if v is not None and v != MARKER:
            out[k] = v
    return out


def merge(boot_text: str, stored: dict[str, str]) -> str:
    """festpos.conf der Boot-Partition mit den gespeicherten Werten anstelle von MARKER."""
    for k in CONF_KEYS:
        v = backup.conf_value(boot_text, k)
        if v == MARKER:
            boot_text = backup.conf_set(boot_text, k, stored[k]) if k in stored else backup.conf_remove(boot_text, k)
    return boot_text


def mask(boot_text: str) -> str:
    """Geheime Werte in festpos.conf durch MARKER ersetzen (Kommentare und alles andere bleibt)."""
    for k in CONF_KEYS:
        v = backup.conf_value(boot_text, k)
        if v is not None and v != MARKER:
            boot_text = backup.conf_set(boot_text, k, MARKER)
    return boot_text


def read_stored(path: Path) -> dict[str, str]:
    try:
        return secret_values(path.read_text(encoding="utf-8", errors="surrogateescape"))
    except OSError:
        return {}


def stored_text(values: dict[str, str]) -> str:
    return "# FestPOS – geheime Werte aus festpos.conf (verschlüsselt, festpos-krypt)\n" + \
        "".join(backup.conf_line(k, values[k]) + "\n" for k in CONF_KEYS if k in values)


def merged(boot_text: str, run_dir: Path | None = None) -> str:
    """festpos.conf, wie sie gilt: MARKER durch die Werte ersetzt, die festpos-krypt beim Start entpackt hat."""
    if MARKER not in boot_text:
        return boot_text
    return merge(boot_text, read_stored((run_dir or RUN_DIR) / "festpos.conf"))


def key_file(boot: Path, name: str, run_dir: Path | None = None) -> Path | None:
    """Schlüsseldatei (server.key, device-ca.key): auf der Boot-Partition oder – verschlüsselt – beim Start entpackt."""
    for p in (boot / "festpos-tls" / name, (run_dir or RUN_DIR) / name):
        if p.is_file():
            return p
    return None
