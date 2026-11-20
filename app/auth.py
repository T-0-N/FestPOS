"""Benutzer, Rollen und Rechte der Verwaltung (ohne Abhängigkeit von core – core prüft damit jede Anfrage).

Rechte pro Bereich (PERMS): Bestellungen ansehen, Druckaufträge, Bestellungen ändern (Storno, Zahlart,
Quittung), offene Tische, Tagesabschluss (auch Export der Bestellungen), Protokoll, Produkte, Ausverkauft/Restmenge,
Tische, Drucker, Geräte & Fully, Pi & Uhrzeit, Testmodus. Dazu «admin»: alles Übrige (Einstellungen, Benutzer und
Rollen, Sicherung, Update, Geräte entsperren/löschen) – das hat nur die Rolle Admin.

Feste Rollen (BUILTIN, nicht änderbar):
  view  – Anzeige:     Übersicht, Statusanzeige, Druckaufträge (nachdrucken / erledigt), Bestellungen ansehen
  lead  – Festleitung: alle Rechte ausser «admin»
  admin – Admin:       alles. Lässt sich weder löschen noch ändern; der letzte aktive Admin-Benutzer bleibt (users.py).
Eigene Rollen (r1, r2 …) stellt der Admin aus den Rechten zusammen (Einstellung «user_roles», users.py).
Notfall-Login: Benutzer «admin» mit dem Passwort aus festpos.conf ist immer Admin (für den Fall, dass niemand
mehr sein Passwort weiss – das System läuft nur einmal im Jahr).

Welche Rechte ein Pfad braucht, steht unten in _RULES – alles, was dort fehlt, braucht «admin» (sicherer Standard).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import Callable

ROLES = ("view", "lead", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
EMERGENCY_NAME = "admin"
NAME_RE = r"^[\w][\w .\-]{0,39}$"
PW_MIN, PW_MAX = 8, 128

# -- Passwörter (scrypt: absichtlich langsam, ~0,1 s auf dem Pi; nur beim Anmelden) -------------------
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str | None) -> bool:
    try:
        kind, n, r, p, salt, h = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
    except (ValueError, TypeError):
        _dummy()
        return False
    return hmac.compare_digest(got.hex(), h)


_DUMMY: list[str] = []


def _dummy() -> None:
    """Gleich lange rechnen, wenn es den Benutzer nicht gibt (sonst verriete die Antwortzeit gültige Namen)."""
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_hex(8)))
    verify_password("x", _DUMMY[0])


# -- Rechte pro Pfad (Rollen und Rechte) ----------------------------------------------------------
# Jede Zeile: Methode, Pfad, Rechte (eines davon genügt). «base» = jede angemeldete Rolle. Nicht aufgeführt =
# «admin» (sicherer Standard): Einstellungen, Benutzer und Rollen, Sicherung, Update, Cluster – nur die Rolle Admin.
_RULES = """
GET /api/admin/me base
POST /api/admin/me/password base
POST /api/admin/me/logout-all base
GET /api/admin/stats base
GET /api/admin/lamp base
GET /api/admin/cluster base
GET /api/admin/printers base
GET /api/admin/settings base
GET /api/admin/system base
GET /api/admin/fully base
GET /api/admin/uplink base
GET /api/status base
GET /status-einfach base
GET /api/admin/orders orders
GET /api/admin/orders/{id} orders
GET /api/admin/export/orders.csv closing
GET /api/admin/jobs jobs
POST /api/admin/jobs/{id}/reprint jobs
POST /api/admin/jobs/{id}/resolve jobs
POST /api/admin/orders/{id}/storno orders_edit
POST /api/admin/orders/{id}/receipt orders_edit
PUT /api/admin/orders/{id}/payment orders_edit
POST /api/admin/payrexx/payments/{id}/cancel orders_edit
POST /api/admin/payrexx/payments/{id}/late/{action} orders_edit
GET /api/admin/tabs tabs
GET /api/admin/tabs/{id} tabs
POST /api/admin/tabs/{id}/settle tabs
POST /api/admin/tabs/{id}/transfer tabs
POST /api/admin/tabs/{id}/unpaid tabs
GET /api/admin/closings/open closing
GET /api/admin/closings closing
POST /api/admin/closings closing
GET /api/admin/closings/{id}/pdf closing
GET /api/admin/closings/{id}/csv closing
GET /api/admin/closings/{id}/verify closing
GET /api/admin/audit audit
GET /api/admin/categories catalog stock
POST /api/admin/categories catalog
PUT /api/admin/categories/{id} catalog
DELETE /api/admin/categories/{id} catalog
GET /api/admin/products catalog stock
POST /api/admin/products catalog
PUT /api/admin/products/{id} catalog
DELETE /api/admin/products/{id} catalog
PUT /api/admin/products/{id}/color catalog
PUT /api/admin/products/{id}/delivery catalog
PUT /api/admin/products/{id}/options catalog
POST /api/admin/products/{id}/image catalog
DELETE /api/admin/products/{id}/image catalog
POST /api/admin/catalog/sort-az catalog
POST /api/admin/import/products catalog
POST /api/admin/import/images catalog
GET /api/admin/import/template.csv catalog
GET /api/admin/export/products.csv catalog
GET /api/admin/vat catalog
PUT /api/admin/products/{id}/vat catalog
PUT /api/admin/products/{id}/stock stock
GET /api/admin/tables tables
POST /api/admin/tables tables
POST /api/admin/tables/print-qr tables
GET /api/admin/tables/qr.pdf tables
DELETE /api/admin/tables/{id} tables
GET /api/admin/plan tables
POST /api/admin/plan/areas tables
PUT /api/admin/plan/areas/{id} tables
DELETE /api/admin/plan/areas/{id} tables
POST /api/admin/plan/areas/{id}/image tables
DELETE /api/admin/plan/areas/{id}/image tables
PUT /api/admin/plan/settings tables
POST /api/admin/printers printers
PUT /api/admin/printers/{id} printers
DELETE /api/admin/printers/{id} printers
GET /api/admin/printers/{id}/status printers
POST /api/admin/printers/{id}/test printers
POST /api/admin/printers/{id}/reroute printers
GET /api/admin/time system
POST /api/admin/time system
POST /api/admin/time/confirm system
GET /api/admin/time/usb system
POST /api/admin/system/power system
POST /api/admin/system/power/cancel system
POST /api/admin/display/{id}/reboot system
PUT /api/admin/settings testmode
POST /api/admin/test-orders/delete testmode
POST /api/admin/devices/test-exit testmode devices
POST /api/admin/fully/check devices
POST /api/admin/fully/whitelist devices
POST /api/admin/fully/command devices
GET /api/admin/devices devices
POST /api/admin/devices/cert devices
POST /api/admin/devices/{id}/revoke devices
PUT /api/admin/devices/role devices
PUT /api/admin/devices/phone devices
POST /api/admin/devices/mac devices
GET /api/admin/devices/wifi-qr devices
GET /api/admin/fully/provision devices
GET /api/admin/fully/apk devices
GET /api/admin/fully/template devices
POST /api/admin/fully/cert devices
POST /api/admin/fully/ca devices
POST /api/admin/fully/device-cert devices
POST /api/admin/fully/settings devices
POST /api/admin/fully/update-app devices
POST /api/admin/fully/bootstrap devices
POST /api/admin/fully/rollout devices
POST /api/admin/fully/prepare devices
GET /api/admin/fully/rollout/{token} devices
POST /api/admin/fully/internet devices
POST /api/admin/fully/license devices
POST /api/admin/fully/adb devices
POST /api/admin/uplink/check devices
"""
# Festleitung darf über PUT /settings nur den Testmodus ändern (siehe settings_admin.put_settings) – ohne Recht «admin»
LEAD_SETTINGS = frozenset({"test_enabled", "test_pin", "test_print", "test_pin_required"})

# Rechte, die eine Rolle haben kann (Reihenfolge = Anzeige in der Verwaltung). «admin» = alles Übrige, nur die Rolle Admin.
PERMS = ("orders", "jobs", "orders_edit", "tabs", "closing", "audit", "catalog", "stock", "tables", "printers",
         "devices", "system", "testmode")
ADMIN_PERM = "admin"
BUILTIN = {"view": frozenset({"orders", "jobs"}), "lead": frozenset(PERMS), "admin": frozenset((*PERMS, ADMIN_PERM))}
ROLE_ID_RE = r"^r[0-9]{1,6}$"                  # eigene Rollen: r1, r2 … (Name frei, nur der Schlüssel steht beim Benutzer)


def _compile(text: str) -> list[tuple[str, re.Pattern, tuple[str, ...]]]:
    out = []
    for line in text.strip().splitlines():
        method, path, *perms = line.split()
        rx = re.compile("^" + re.sub(r"\\\{[a-z]+\\\}", "[^/]+", re.escape(path)) + "$")
        out.append((method, rx, tuple(perms)))
    return out


RULES = _compile(_RULES)


def required(method: str, path: str) -> tuple[str, ...]:
    """Rechte, von denen eines für diese Anfrage genügt. Nicht aufgeführt = («admin»,)."""
    method = "GET" if method == "HEAD" else method
    for m, rx, perms in RULES:
        if m == method and rx.match(path):
            return perms
    return (ADMIN_PERM,)


def required_role(method: str, path: str) -> str:
    """Kleinste der festen Rollen (view < lead < admin), die diese Anfrage darf – für Übersichten und Tests."""
    need = required(method, path)
    for role in ROLES:
        if "base" in need or BUILTIN[role] & set(need):
            return role
    return "admin"


def perms_of(role: str | None) -> frozenset:
    """Rechte einer Rolle: fest (view, lead, admin) oder eigene Rolle aus der Einstellung «user_roles»."""
    if role in BUILTIN:
        return BUILTIN[role]
    r = roles().get(role or "")
    return frozenset(p for p in (r or {}).get("perms", ()) if p in PERMS) if r else frozenset()


def known_role(role: str | None) -> bool:
    return role in BUILTIN or role in roles()


def allowed(role: str | None, need: str | tuple[str, ...]) -> bool:
    """Darf diese Rolle das? need: ein Recht oder mehrere (eines genügt), «base» = jede bekannte Rolle."""
    if not known_role(role):
        return False
    have = perms_of(role)
    return any(p == "base" or p in have for p in ((need,) if isinstance(need, str) else need))


def has(user: dict | None, perm: str) -> bool:
    return bool(user) and perm in perms_of(user.get("role"))


# -- Benutzer (aus der Datenbank, kurz zwischengespeichert) ------------------------------------------------
EMERGENCY = {"id": 0, "name": EMERGENCY_NAME, "role": "admin", "gen": 0, "active": True, "emergency": True}
CACHE_S = 5.0
_loader: Callable[[], list[dict]] | None = None
_cache: dict = {"until": 0.0, "users": {}}
_lock = threading.Lock()


_roles_loader: Callable[[], list[dict]] | None = None
_roles_cache: dict = {"until": 0.0, "roles": {}}


def set_roles_loader(fn: Callable[[], list[dict]] | None) -> None:
    """Woher die eigenen Rollen kommen (Einstellung user_roles) – setzt main beim Start."""
    global _roles_loader
    _roles_loader = fn
    forget()


def roles() -> dict:
    """Eigene Rollen nach Schlüssel ({"r1": {"id", "name", "perms"}}), höchstens CACHE_S alt."""
    now = time.monotonic()
    with _lock:
        fresh, cur = now < _roles_cache["until"], _roles_cache["roles"]
    if not fresh and _roles_loader is not None:
        try:
            cur = {r["id"]: r for r in _roles_loader() if r.get("id") not in BUILTIN}
        except Exception:  # noqa: BLE001 – Datenbank gerade nicht da: letzter Stand gilt kurz weiter
            now -= CACHE_S - 1
        with _lock:
            _roles_cache.update(until=now + CACHE_S, roles=cur)
    return cur


def set_loader(fn: Callable[[], list[dict]] | None) -> None:
    """Woher die Benutzer kommen (Tabelle users) – setzt main beim Start."""
    global _loader
    _loader = fn
    forget()


def forget() -> None:
    with _lock:
        _cache["until"] = 0.0
        _roles_cache["until"] = 0.0


def _users() -> dict:
    """Benutzer nach Nummer (höchstens CACHE_S alt – im Cluster kommen Änderungen so schnell überall an).
    Nummer 0 = Notfall-Login (nur sein Stand «gen», für «Überall abmelden»)."""
    now = time.monotonic()
    with _lock:
        fresh = now < _cache["until"]
        users = _cache["users"]
    if not fresh and _loader is not None:
        try:
            users = {u["id"]: u for u in _loader()}
        except Exception:  # noqa: BLE001 – Datenbank gerade nicht da: letzter Stand gilt kurz weiter
            users = _cache["users"]
            now -= CACHE_S - 1
        with _lock:
            _cache.update(until=now + CACHE_S, users=users)
    return users


def emergency() -> dict:
    """Der Notfall-Login mit seinem aktuellen Stand (gen)."""
    u = _users().get(0) or {}
    return dict(EMERGENCY, gen=int(u.get("gen") or 0))


def lookup(uid: int) -> dict | None:
    """Benutzer zur Sitzung, None = gesperrt oder gelöscht."""
    if uid == 0:
        return emergency()
    u = _users().get(uid)
    return dict(u, emergency=False) if u and u.get("active") else None
