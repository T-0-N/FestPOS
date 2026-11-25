"""Geheimnisse in der Datenbank verschlüsselt ablegen (T-0-N/FestPOS#8).

Alle Geheimnisse der Einstellungen (backup.SECRET_KEYS: Sicherungs-Passwort, Fully-Passwort, WLAN-Passwort,
Router-Passwort, Token für ein privates Repo …) und die beiden, die schon vorher verschlüsselt waren (Payrexx-Schlüssel,
Tunnel-Token), liegen mit AES-GCM verschlüsselt in der Datenbank: «enc1:» + Base64(Nonce + Geheimtext). Der Schlüssel
ist pro Zweck ein eigener – abgeleitet (HKDF-SHA256) aus dem Hauptschlüssel und dem Namen der Einstellung, der auch als
zusätzliche Prüfung (AAD) dient: ein Wert lässt sich nicht unter einem anderen Namen entschlüsseln.

Hauptschlüssel: CLUSTER_SECRET (auf allen Pi gleich, liegt nicht in der Datenbank – eine Sicherung der Datenbank oder
ein Abzug davon verrät die Geheimnisse nicht). Mit «Daten verschlüsseln mit Schlüssel im Chip» (T-0-N/FestPOS#3) kommt
er später aus dem Chip statt von der Karte: dann nur _master_from() anpassen.

Lesen: Store.setting() entschlüsselt die Geheimnisse selbst; ältere Werte im Klartext gehen weiter. Schreiben: über
put_stmt()/seal(). Bestehende Klartext-Werte schreibt migrate() um – erst, wenn alle Pi diese Version haben (sonst
läse ein noch älterer Pi während des Updates den verschlüsselten Text als Passwort).
"""
from __future__ import annotations

import base64
import secrets
from typing import Callable

from .backup import SECRET_KEYS as _PLAIN_KEYS

PREFIX = "enc1:"
LEGACY_KEYS = ("gast_key_enc", "gast_tunnel_enc")          # bis jetzt mit eigenem Verfahren (payrexx_api, alt)
SECRETS = frozenset((*_PLAIN_KEYS, *LEGACY_KEYS))
AUTO = frozenset(_PLAIN_KEYS)       # entschlüsselt Store.setting selbst; LEGACY_KEYS über payrexx_api.unseal/tunnel
UPSERT = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"

_master: Callable[[], str] = lambda: ""                     # setzt core beim Start (CLUSTER_SECRET)
# Neue Werte erst verschlüsselt schreiben, wenn alle Pi diese Version haben (setzt main: all_pis_current) – sonst
# läse ein älterer Pi während eines Updates (oder nach «Zurück») den verschlüsselten Text als Passwort.
_may_seal: Callable[[], bool] = lambda: True
MIN_SEALED = 12 + 16                                        # Nonce und Prüfwert von AES-GCM


def use(fn: Callable[[], str]) -> None:
    global _master
    _master = fn


def gate(fn: Callable[[], bool]) -> None:
    global _may_seal
    _may_seal = fn


def may_seal() -> bool:
    """Neue Werte im neuen Verfahren schreiben? (alle Pi haben diese Version)"""
    try:
        return bool(_may_seal())
    except Exception:  # noqa: BLE001 – im Zweifel wie bisher
        return False


def _key(purpose: str, secret: str | None = None) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    master = (_master() if secret is None else secret) or ""
    return HKDF(hashes.SHA256(), 32, b"festpos-secretbox-v1", purpose.encode()).derive(master.encode())


def seal(purpose: str, value: str, secret: str | None = None) -> str:
    """Klartext -> «enc1:…» (leer bleibt leer: «nichts gesetzt»)."""
    if value == "":
        return ""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key(purpose, secret)).encrypt(nonce, value.encode(), purpose.encode())
    return PREFIX + base64.b64encode(nonce + ct).decode()


def _raw(value: str) -> bytes | None:
    """Inhalt nach «enc1:», wenn es wirklich ein verschlüsselter Wert ist (Base64, mindestens Nonce und Prüfwert)."""
    try:
        raw = base64.b64decode(value[len(PREFIX):], validate=True)
    except ValueError:
        return None
    return raw if len(raw) >= MIN_SEALED else None


def sealed(value: str | None) -> bool:
    """Verschlüsselter Wert? Ein älteres Passwort im Klartext, das zufällig mit «enc1:» beginnt, gilt nicht als solcher."""
    return bool(value) and str(value).startswith(PREFIX) and _raw(str(value)) is not None


def unseal(purpose: str, value: str | None, secret: str | None = None) -> str | None:
    """«enc1:…» -> Klartext; Klartext (älterer Stand) unverändert; None: nicht lesbar (anderer Schlüssel, kaputt)."""
    if value is None or not sealed(value):
        return value
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = _raw(value)
    try:
        return AESGCM(_key(purpose, secret)).decrypt(raw[:12], raw[12:], purpose.encode()).decode()
    except (ValueError, InvalidTag):
        return None


def stored(key: str, value: str) -> str:
    """So kommt ein Wert in die Datenbank: ein Geheimnis verschlüsselt – die bisher unverschlüsselten (AUTO) erst, wenn
    alle Pi diese Version haben (bis dahin Klartext wie bisher; migrate() holt es nach)."""
    if key in AUTO and may_seal():
        return seal(key, value)
    if key in LEGACY_KEYS:                                   # Payrexx/Tunnel: altes Verfahren, bis alle Pi neu sind
        from . import payrexx_api
        return payrexx_api.seal(value, key)
    return value


def put_stmt(key: str, value: str) -> tuple[str, tuple]:
    """Anweisung für store.batch: Einstellung setzen – ein Geheimnis verschlüsselt."""
    return UPSERT, (key, stored(key, value))


def _legacy_open(key: str, value: str) -> str | None:
    """Payrexx-Schlüssel und Tunnel-Token im bisherigen Verfahren (vor «enc1:»)."""
    from . import payrexx_api
    return payrexx_api.unseal_legacy(value)


def migrate(store) -> int:
    """Klartext (und das alte Verfahren) umschreiben -> Anzahl. Mehrfach aufrufen schadet nicht."""
    stmts = []
    rows = store.query(f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(SECRETS))})", tuple(SECRETS))
    for r in rows:
        k, v = r["key"], r["value"]
        if not v or sealed(v):
            continue
        plain = _legacy_open(k, v) if k in LEGACY_KEYS else v
        if plain is None:
            continue                                 # alter Wert nicht lesbar (z.B. CLUSTER_SECRET geändert): lassen
        stmts.append(("UPDATE settings SET value=? WHERE key=? AND value=?", (seal(k, plain), k, v)))
    if stmts:
        store.batch(stmts)
    return len(stmts)
