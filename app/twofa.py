"""Verwaltung: kritische Aktionen bestätigen und zweiter Faktor (TOTP) – T-0-N/FestPOS#9.

Kritische Aktionen (auth.CRITICAL: Update verteilen und zurück, Benutzer und Rollen, Netz, Sicherung einspielen,
Zurücksetzen, Karten und Kartenpaket, Sicherheits-Einstellungen) verlangen Passwort und – mit zweitem Faktor – den
Code nochmals. Die Bestätigung gilt CONFIRM_S (5 Minuten) und steht im Token (core.issue_session, «bestätigt bis»):
so gilt sie auf allen Pi. Ohne Bestätigung antwortet der Server 428 {"code": "confirm_needed"}, die Verwaltung zeigt
dann das Fenster und wiederholt die Anfrage. Basic Auth schickt das Passwort ohnehin mit: gilt als bestätigt, ausser
der Benutzer hat einen zweiten Faktor (dann geht Basic Auth nur zum Lesen).

Zweiter Faktor: Pflicht für die Rollen in der Einstellung «totp_required_roles» (Standard: Admin), dreimal «Später»,
danach geht nur noch das Einrichten. Freiwillig für alle anderen (Benutzermenü). Der Notfall-Login hat keinen
zweiten Faktor – er geht nur aus dem FestPOS-Netz (app/emergency.py). Daten pro Benutzer in users.totp (JSON):
{"on", "secret" (verschlüsselt, secretbox), "pending" (beim Einrichten), "later" (übrige «Später»),
"last" (zuletzt benutzter Schritt), "salt", "codes" (HMAC der Wiederherstellungs-Codes)}.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, i18n, ledger_base as lb, secretbox, totp
from .core import admin, current_user, db, rt

CONFIRM_S = 5 * 60
LATER = 3
REQUIRED_KEY = "totp_required_roles"
PENDING_S = 300                                          # Zeit für den zweiten Schritt der Anmeldung
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

_cache: dict = {"until": 0.0, "roles": ["admin"]}
_lock = threading.Lock()


# -- Daten ------------------------------------------------------------------------------------------------------
def required_roles() -> list[str]:
    """Rollen, für die der zweite Faktor Pflicht ist (höchstens 5 s alt)."""
    now = time.monotonic()
    with _lock:
        if now < _cache["until"]:
            return _cache["roles"]
    try:
        roles = json.loads(rt.store.setting(REQUIRED_KEY, '["admin"]') or "[]")
        roles = [r for r in roles if isinstance(r, str)] if isinstance(roles, list) else ["admin"]
    except (ValueError, Exception):  # noqa: BLE001 – Datenbank kurz weg: letzter Stand
        roles = _cache["roles"]
    with _lock:
        _cache.update(until=now + 5, roles=roles)
    return roles


def forget() -> None:
    with _lock:
        _cache["until"] = 0.0


def data_of(uid: int) -> dict:
    return _parse(rt.store.scalar("SELECT totp FROM users WHERE id=?", (uid,)) if uid else None)


def _parse(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _save(uid: int, d: dict, extra: list | None = None) -> None:
    rt.store.batch([("UPDATE users SET totp=? WHERE id=?", (json.dumps(d), uid)), *(extra or [])])
    auth.forget()


def _swap(uid: int, raw: str | None, d: dict, extra: list | None = None) -> bool:
    """Nur ändern, wenn der gespeicherte Stand noch der gelesene ist (ein Code gilt genau einmal, auch wenn zwei
    Anfragen gleichzeitig kommen). -> geändert?"""
    res = rt.store.batch([("UPDATE users SET totp=? WHERE id=? AND totp IS ?", (json.dumps(d), uid, raw)),
                          *(extra or [])])
    auth.forget()
    return bool(res and res[0].rows_affected)


def _changed(uid: int) -> HTTPException:
    """Fehler, wenn der Stand beim Schreiben nicht mehr der gelesene war."""
    return HTTPException(409, i18n.t("err.totp.already_on" if data_of(uid).get("on") else "err.totp.no_pending"))


def _raw(uid: int) -> str | None:
    return rt.store.scalar("SELECT totp FROM users WHERE id=?", (uid,)) if uid else None


def summary(user: dict) -> dict:
    """Für /me: zweiter Faktor an? Pflicht? Wie oft noch «Später»?"""
    if not user or user.get("emergency"):
        return {"on": False, "required": False, "later": 0, "blocked": False, "codes": 0, "role_required": False}
    on, later = bool(user.get("totp_on")), user.get("totp_later")
    required = user.get("role") in required_roles()
    later = LATER if later is None else int(later)
    # das dritte «Später» gilt noch für die laufende Anmeldung (bis zu SESSION_HOURS), danach nur noch Einrichten
    waiting = float(user.get("totp_later_until") or 0) > time.time()
    return {"on": on, "required": required and not on, "later": later,
            "blocked": required and not on and later <= 0 and not waiting,
            "codes": int(user.get("totp_codes") or 0), "role_required": required}


def setup_blocked(user: dict) -> bool:
    return summary(user)["blocked"]


SETUP_PATHS = ("/api/admin/me", "/api/admin/me/")             # bei Pflicht ohne «Später»: nur das Einrichten


def setup_allowed(path: str) -> bool:
    return path == "/api/admin/me" or path.startswith("/api/admin/me/")


def has_totp(user: dict) -> bool:
    return bool(user and user.get("totp_on"))


def verify_code(uid: int, code: str, account: str) -> str:
    """Code aus der App oder Wiederherstellungs-Code prüfen (und verbrauchen). -> ok | wrong | clock | used."""
    raw = _raw(uid)
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        d = {}
    if not isinstance(d, dict) or not d.get("on"):
        return "wrong"
    secret = secretbox.unseal(f"totp.{uid}", d.get("secret") or "")
    if not secret:
        return "wrong"
    state, step = totp.check(secret, code, last_step=int(d.get("last") or -1))
    if state == "ok":
        return "ok" if _swap(uid, raw, {**d, "last": step}) else "used"
    if state == "wrong" and len("".join(ch for ch in str(code) if ch.isalnum())) == 8:   # Wiederherstellungs-Code
        rest = totp.use_code(code, d.get("salt") or "", list(d.get("codes") or []))
        if rest is not None:
            ok = _swap(uid, raw, {**d, "codes": rest})
            if ok:
                rt.store.execute(*lb.audit_stmt(account, "totp_recovery_used", left=len(rest)))
            return "ok" if ok else "used"
    return state


def code_error(state: str, status: int = 422) -> HTTPException:
    """Code falsch/benutzt/Uhr daneben. 422 in einer Sitzung (401 hiesse «abgemeldet»), 401 bei der Anmeldung."""
    key = {"clock": "err.totp.clock", "used": "err.totp.used"}.get(state, "err.totp.wrong")
    return HTTPException(status, i18n.t(key))


# -- Anmeldung in zwei Schritten ------------------------------------------------------------------------------------
def _pending_key() -> bytes:
    from .core import _session_key
    return hashlib.sha256(b"festpos-totp-pending\0" + _session_key()).digest()


def pending_token(user: dict) -> str:
    msg = f"{int(time.time()) + PENDING_S}.{int(user['id'])}.{int(user.get('gen') or 0)}.{secrets.token_urlsafe(8)}"
    return msg + "." + hmac.new(_pending_key(), msg.encode(), hashlib.sha256).hexdigest()


def pending_user(token: str) -> dict | None:
    try:
        exp_s, uid_s, gen_s, nonce, sig = token.split(".")
        exp, uid, gen = int(exp_s), int(uid_s), int(gen_s)
    except ValueError:
        return None
    good = hmac.new(_pending_key(), f"{exp_s}.{uid_s}.{gen_s}.{nonce}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good) or exp < time.time():
        return None
    user = auth.lookup(uid)
    return user if user and int(user.get("gen") or 0) == gen else None


# -- Routen: eigener zweiter Faktor ----------------------------------------------------------------------------------
class CodeIn(BaseModel):
    code: str = Field(max_length=20)


class PwCodeIn(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(default="", max_length=20)


def _me(request: Request) -> dict:
    u = current_user(request) or {}
    if u.get("emergency") or not u.get("id"):
        raise HTTPException(422, i18n.t("err.totp.emergency"))
    return u


def _check_password(u: dict, password: str) -> bool:
    if u.get("emergency"):
        from .core import _password_ok
        return _password_ok(password)
    row = rt.store.query_one("SELECT pw_hash FROM users WHERE id=?", (u["id"],))
    return bool(row) and auth.verify_password(password, row["pw_hash"])


@admin_router.get("/me/totp")
async def my_totp(request: Request):
    return summary(current_user(request) or {})


@admin_router.post("/me/totp/start")
async def start(request: Request):
    """Einrichten beginnen: neues Geheimnis (noch nicht aktiv) -> QR-Code für die App und der Schlüssel zum Abtippen.
    Ist der zweite Faktor schon an, geht das nicht: zuerst ausschalten (Passwort und Code) oder vom Admin zurücksetzen
    lassen – sonst ersetzte eine übernommene Sitzung allein den Faktor."""
    u = _me(request)
    if has_totp(u):
        raise HTTPException(409, i18n.t("err.totp.already_on"))
    from . import qr
    secret = totp.new_secret()

    def run():
        raw = _raw(u["id"])
        d = _parse(raw)
        if d.get("on"):
            raise HTTPException(409, i18n.t("err.totp.already_on"))
        if not _swap(u["id"], raw, {**d, "pending": secretbox.seal(f"totp.{u['id']}", secret)}):
            raise _changed(u["id"])                  # gleichzeitig geändert (z.B. in einem anderen Fenster)
    await db(run)
    link = totp.uri(secret, u["name"])
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": link, "qr": qr.qr_svg(link)}


@admin_router.post("/me/totp/confirm")
async def confirm_setup(c: CodeIn, request: Request):
    """Ersten Code prüfen -> zweiter Faktor an, 10 Wiederherstellungs-Codes (nur jetzt sichtbar)."""
    u = _me(request)

    def run():
        raw = _raw(u["id"])
        d = _parse(raw)
        if d.get("on"):
            raise HTTPException(409, i18n.t("err.totp.already_on"))
        secret = secretbox.unseal(f"totp.{u['id']}", d.get("pending") or "")
        if not secret:
            raise HTTPException(409, i18n.t("err.totp.no_pending"))
        state, step = totp.check(secret, c.code)
        if state != "ok":
            raise code_error(state)
        codes, salt = totp.new_codes(), secrets.token_hex(16)
        if not _swap(u["id"], raw, {"on": True, "secret": d["pending"], "last": step, "salt": salt,
                                    "codes": [totp.code_hash(x, salt) for x in codes], "later": d.get("later", LATER)},
                     [lb.audit_if_changed_stmt(u["name"], "totp_on")]):
            raise _changed(u["id"])                  # inzwischen neu begonnen oder schon eingerichtet
        return codes
    return {"ok": True, "codes": await db(run)}


@admin_router.post("/me/totp/later")
async def later(request: Request):
    """«Später»: noch nicht einrichten (Pflicht: höchstens dreimal)."""
    u = _me(request)

    def run():
        d = data_of(u["id"])
        left = int(d.get("later", LATER))
        if left <= 0:
            raise HTTPException(409, i18n.t("err.totp.required"))
        from .core import SESSION_HOURS
        _save(u["id"], {**d, "later": left - 1, "later_until": int(time.time()) + SESSION_HOURS * 3600},
              [lb.audit_stmt(u["name"], "totp_later", left=left - 1)])
        return left - 1
    return {"later": await db(run)}


@admin_router.post("/me/totp/off")
async def off(p: PwCodeIn, request: Request):
    """Eigenen zweiten Faktor ausschalten (Passwort und Code). Ist er für die Rolle Pflicht: nicht möglich."""
    u = _me(request)
    if u.get("role") in required_roles():
        raise HTTPException(409, i18n.t("err.totp.required"))
    if not await db(_check_password, u, p.password):
        raise HTTPException(422, i18n.t("err.totp.password"))
    state = await db(verify_code, u["id"], p.code, u["name"])
    if state != "ok":
        raise code_error(state)
    await db(lambda: _save(u["id"], {"later": LATER}, [lb.audit_stmt(u["name"], "totp_off")]))
    return {"ok": True}


@admin_router.post("/me/totp/codes")
async def new_codes(p: PwCodeIn, request: Request):
    """Neue Wiederherstellungs-Codes (die alten gelten nicht mehr)."""
    u = _me(request)
    if not await db(_check_password, u, p.password):
        raise HTTPException(422, i18n.t("err.totp.password"))
    state = await db(verify_code, u["id"], p.code, u["name"])
    if state != "ok":
        raise code_error(state)

    def run():
        d = data_of(u["id"])
        codes, salt = totp.new_codes(), secrets.token_hex(16)
        _save(u["id"], {**d, "salt": salt, "codes": [totp.code_hash(x, salt) for x in codes]},
              [lb.audit_stmt(u["name"], "totp_codes_new")])
        return codes
    return {"codes": await db(run)}


# -- Kritische Aktionen bestätigen ------------------------------------------------------------------------------------
@admin_router.post("/me/confirm")
async def confirm(p: PwCodeIn, request: Request):
    """Passwort (und Code) nochmals -> neues Token, «bestätigt» für CONFIRM_S."""
    from .core import confirm_session, login_throttle
    u = current_user(request) or {}
    ip = request.client.host if request.client else "?"
    login_throttle.check(ip)
    if not await db(_check_password, u, p.password):
        login_throttle.fail(ip, u.get("name") or "")
        raise HTTPException(422, i18n.t("err.totp.password"))
    if has_totp(u):
        state = await db(verify_code, u["id"], p.code, u["name"])
        if state != "ok":
            login_throttle.fail(ip, u.get("name") or "")
            raise code_error(state)
    scheme, _, tok = request.headers.get("authorization", "").partition(" ")
    s = await db(confirm_session, tok.strip(), u, CONFIRM_S) if scheme.lower() == "bearer" else None
    if not s:
        raise HTTPException(409, i18n.t("err.login.no_session"))
    await db(lb.audit, rt.store, u.get("name"), "confirm")
    return s
