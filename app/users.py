"""Verwaltung der Benutzer und Rollen (nur Admin) und das eigene Konto (jede Rolle). Regeln und Passwörter: auth.py.

Schutz vor dem Aussperren: die Rolle Admin lässt sich weder ändern noch löschen, der letzte aktive
Admin-Benutzer weder sperren, herabstufen noch löschen, niemand ändert seine eigene Rolle; der Notfall-Login ist immer
Admin."""
from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, i18n
from . import ledger_base as lb
from .core import admin, current_user, db, rt

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

ROLE_RE = "^(" + "|".join(auth.ROLES) + r"|r[0-9]{1,6})$"


EMERGENCY_GEN = "emergency_gen"            # Einstellung: Stand des Notfall-Logins («Überall abmelden»)


def load_users() -> list[dict]:
    """Für auth.lookup (Sitzungen): ohne Passwort-Hash. Nummer 0 = Notfall-Login (nur sein Stand)."""
    rows = rt.store.query("SELECT id, name, role, gen, active FROM users")
    try:
        gen = int(rt.store.setting(EMERGENCY_GEN, "0") or 0)
    except ValueError:
        gen = 0
    return [*rows, {"id": 0, "name": auth.EMERGENCY_NAME, "role": "admin", "gen": gen, "active": 1}]


class UserIn(BaseModel):
    name: str = Field(pattern=auth.NAME_RE)
    role: str = Field(pattern=ROLE_RE)
    password: str = Field(min_length=auth.PW_MIN, max_length=auth.PW_MAX)


class UserEdit(BaseModel):
    role: str | None = Field(default=None, pattern=ROLE_RE)
    active: bool | None = None
    password: str | None = Field(default=None, min_length=auth.PW_MIN, max_length=auth.PW_MAX)


class OwnPasswordIn(BaseModel):
    old: str = Field(max_length=auth.PW_MAX)
    new: str = Field(min_length=auth.PW_MIN, max_length=auth.PW_MAX)


def _who(request: Request) -> str:
    return (current_user(request) or {}).get("name") or "?"


def _public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "name", "role", "active", "created_at", "last_login")} | {"active": bool(u.get("active"))}


def me_info(u: dict) -> dict:
    """Wer angemeldet ist: Name, Rolle, Name der eigenen Rolle (feste übersetzt der Browser) und die Rechte."""
    role = u.get("role")
    return {"name": u.get("name"), "role": role, "emergency": bool(u.get("emergency")),
            "role_name": None if role in auth.BUILTIN else (auth.roles().get(role) or {}).get("name"),
            "perms": sorted(auth.perms_of(role))}


@admin_router.get("/me")
async def me(request: Request):
    from . import devices
    from .core import admin_cert_ok
    info = devices.client_cert(request)
    return {**await db(me_info, current_user(request) or {}),
            "cert": {"name": info["name"], "ok": await db(admin_cert_ok, request)} if info else None}


@admin_router.post("/me/password")
async def own_password(p: OwnPasswordIn, request: Request):
    u = current_user(request) or {}
    if u.get("emergency"):
        raise HTTPException(422, i18n.t("err.users.emergency_pw"))

    def run():
        row = rt.store.query_one("SELECT pw_hash FROM users WHERE id=?", (u["id"],))
        if not row or not auth.verify_password(p.old, row["pw_hash"]):
            raise HTTPException(422, i18n.t("err.users.old_wrong"))
        rt.store.batch([("UPDATE users SET pw_hash=?, gen=gen+1 WHERE id=?", (auth.hash_password(p.new), u["id"])),
                        lb.audit_stmt(u.get("name"), "own_password")])
    await db(run)
    auth.forget()
    return {"ok": True}


@admin_router.post("/me/logout-all")
async def logout_all(request: Request):
    """Alle Sitzungen des eigenen Kontos beenden (auf allen Geräten), z.B. nach einem vergessenen Browser."""
    u = current_user(request) or {}

    def run():
        if u.get("emergency"):
            rt.store.batch([("INSERT INTO settings(key,value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET "
                             "value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)", (EMERGENCY_GEN,)),
                            lb.audit_stmt(u.get("name"), "logout_all")])
        else:
            rt.store.batch([("UPDATE users SET gen=gen+1 WHERE id=?", (u["id"],)),
                            lb.audit_stmt(u.get("name"), "logout_all")])
    await db(run)
    auth.forget()
    return {"ok": True}


# Bedingung für UPDATE/DELETE: nicht der letzte aktive Admin (in derselben Anweisung geprüft – atomar, auch im Cluster)
NOT_LAST_ADMIN = ("NOT (role='admin' AND active=1 AND "
                  "(SELECT COUNT(*) FROM users WHERE role='admin' AND active=1) <= 1)")


def _last_admin(u: dict) -> bool:
    """Ist das der letzte aktive Benutzer mit der Rolle Admin? Dann bleibt er (Aussperrschutz)."""
    if u.get("role") != "admin" or not u.get("active"):
        return False
    return (rt.store.scalar("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1") or 0) <= 1


@admin_router.get("/users")
async def list_users():
    rows = await db(rt.store.query, "SELECT id, name, role, active, created_at, last_login FROM users ORDER BY name")
    return {"users": [_public(r) for r in rows], "roles": await db(roles_view), "perms": list(auth.PERMS)}


@admin_router.post("/users")
async def create_user(u: UserIn, request: Request):
    name = " ".join(u.name.split())
    if name.lower() == auth.EMERGENCY_NAME:
        raise HTTPException(422, i18n.t("err.users.reserved", name=auth.EMERGENCY_NAME))

    def run():
        if not auth.known_role(u.role):
            raise HTTPException(422, i18n.t("err.users.role_not_found"))
        if rt.store.scalar("SELECT COUNT(*) FROM users WHERE name=? COLLATE NOCASE", (name,)):
            raise HTTPException(409, i18n.t("err.users.exists", name=name))
        res = rt.store.execute("INSERT INTO users(name, role, pw_hash, created_at) VALUES (?,?,?,?)",
                               (name, u.role, auth.hash_password(u.password), time.strftime("%Y-%m-%dT%H:%M:%S")))
        lb.audit(rt.store, _who(request), "user_create", name=name, role=u.role)
        return rt.store.query_one("SELECT * FROM users WHERE id=?", (res.last_id,))
    row = await db(run)
    auth.forget()
    return _public(row)


@admin_router.put("/users/{uid}")
async def edit_user(uid: int, e: UserEdit, request: Request):
    me_ = current_user(request) or {}
    if uid == me_.get("id") and (e.active is False or (e.role and e.role != me_.get("role"))):
        raise HTTPException(422, i18n.t("err.users.not_self"))       # sich selbst nicht aussperren / herabstufen

    def run():
        cur = rt.store.query_one("SELECT id, role, active FROM users WHERE id=?", (uid,))
        if not cur:
            raise HTTPException(404, i18n.t("err.users.not_found"))
        if e.role and not auth.known_role(e.role):
            raise HTTPException(422, i18n.t("err.users.role_not_found"))
        if (e.active is False or (e.role and e.role != "admin")) and _last_admin(cur):
            raise HTTPException(422, i18n.t("err.users.last_admin"))
        sets, args = [], []
        if e.role:
            sets.append("role=?")
            args.append(e.role)
        if e.active is not None:
            sets += ["active=?", "gen=gen+1"]             # gesperrt: offene Sitzungen enden sofort
            args.append(1 if e.active else 0)
        if e.password:
            sets += ["pw_hash=?", "gen=gen+1"]
            args.append(auth.hash_password(e.password))
        if sets:
            old = rt.store.query_one("SELECT name, role, active FROM users WHERE id=?", (uid,))
            removes = e.active is False or bool(e.role and e.role != "admin")
            guard = f" AND {NOT_LAST_ADMIN}" if removes else ""
            # Änderung und Protokoll in einem Batch: ohne Änderung (letzter Admin) auch kein Eintrag
            res = rt.store.batch([
                (f"UPDATE users SET {', '.join(dict.fromkeys(sets))} WHERE id=?{guard}", (*args, uid)),
                lb.audit_if_changed_stmt(_who(request), "user_update", name=old["name"], role=e.role, active=e.active,
                                         password=bool(e.password))], transaction=True)
            if not res[0].rows_affected:
                if not rt.store.query_one("SELECT id FROM users WHERE id=?", (uid,)):
                    raise HTTPException(404, i18n.t("err.users.not_found"))
                raise HTTPException(422, i18n.t("err.users.last_admin"))
        return rt.store.query_one("SELECT * FROM users WHERE id=?", (uid,))
    row = await db(run)
    auth.forget()
    return _public(row)


@admin_router.delete("/users/{uid}")
async def delete_user(uid: int, request: Request):
    if uid == (current_user(request) or {}).get("id"):
        raise HTTPException(422, i18n.t("err.users.not_self"))
    cur = await db(rt.store.query_one, "SELECT id, name, role, active FROM users WHERE id=?", (uid,))
    if cur and await db(_last_admin, cur):
        raise HTTPException(422, i18n.t("err.users.last_admin"))
    name = cur["name"] if cur else None
    res = await db(rt.store.batch, [(f"DELETE FROM users WHERE id=? AND {NOT_LAST_ADMIN}", (uid,)),
                                    lb.audit_if_changed_stmt(_who(request), "user_delete", name=name)], True)
    if not res[0].rows_affected:
        if await db(rt.store.query_one, "SELECT id FROM users WHERE id=?", (uid,)):
            raise HTTPException(422, i18n.t("err.users.last_admin"))
        raise HTTPException(404, i18n.t("err.users.not_found"))
    auth.forget()
    return {"ok": True}


# -- Eigene Rollen: Name + Rechte, gespeichert in der Einstellung «user_roles» ------------------------
ROLES_KEY = "user_roles"


def load_roles(raw_text: str | None = None) -> list[dict]:
    """Für auth.roles: [{"id": "r1", "name": "Kasse", "perms": [...]}] – fehlerhafte Einträge fallen weg."""
    try:
        raw = json.loads((raw_text if raw_text is not None else rt.store.setting(ROLES_KEY, "[]")) or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        if isinstance(r, dict) and re.match(auth.ROLE_ID_RE, str(r.get("id", ""))):
            out.append({"id": r["id"], "name": str(r.get("name") or r["id"])[:40],
                        "perms": [p for p in r.get("perms") or [] if p in auth.PERMS]})
    return out


def _roles_now() -> tuple[str | None, list[dict]]:
    """(gespeicherter Text, Rollen) – der Text dient beim Speichern als Vergleich (wer zuerst kommt, gewinnt)."""
    raw = rt.store.setting(ROLES_KEY, None)
    return raw, load_roles(raw or "[]")


def _save_roles(before: str | None, roles: list[dict], who: str, action: str, **info) -> None:
    """Nur speichern, wenn seit dem Lesen niemand anderes gespeichert hat (zwei Admins zugleich, auch auf zwei Pi):
    sonst 409 – die Verwaltung lädt neu, man speichert nochmals."""
    new = json.dumps(roles, ensure_ascii=False, separators=(",", ":"))
    if before is None:
        stmt = ("INSERT INTO settings(key, value) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM settings WHERE key=?)",
                (ROLES_KEY, new, ROLES_KEY))
    else:
        stmt = ("UPDATE settings SET value=? WHERE key=? AND value=?", (new, ROLES_KEY, before))
    res = rt.store.batch([stmt, lb.audit_if_changed_stmt(who, action, **info)], transaction=True)   # zusammen oder gar nicht
    if not res[0].rows_affected:
        raise HTTPException(409, i18n.t("err.users.roles_busy"))
    auth.forget()


def roles_view() -> list[dict]:
    """Alle Rollen für die Verwaltung: feste zuerst (nicht änderbar), dann die eigenen."""
    fixed = [{"id": r, "name": None, "perms": sorted(auth.BUILTIN[r] - {auth.ADMIN_PERM}, key=auth.PERMS.index),
              "admin": r == "admin", "builtin": True} for r in auth.ROLES]
    return fixed + [{**r, "admin": False, "builtin": False} for r in load_roles()]


class RoleIn(BaseModel):
    name: str = Field(pattern=auth.NAME_RE)
    perms: list[str] = Field(default_factory=list, max_length=len(auth.PERMS))


def _check_role(r: RoleIn, others: list[dict]) -> tuple[str, list[str]]:
    name = " ".join(r.name.split())
    bad = [p for p in r.perms if p not in auth.PERMS]
    if bad:                                           # «admin» gibt es nur in der festen Rolle Admin
        raise HTTPException(422, i18n.t("err.users.role_perm", perm=bad[0]))
    fixed = {i18n.t(f"admin.users.role_{x}").lower() for x in auth.ROLES} | set(auth.ROLES)
    if name.lower() in fixed or any(o["name"].lower() == name.lower() for o in others):
        raise HTTPException(409, i18n.t("err.users.role_exists", name=name))
    want = set(r.perms) | ({"orders"} if "orders_edit" in r.perms else set())   # ändern ohne ansehen nützt nichts
    return name, [p for p in auth.PERMS if p in want]


@admin_router.post("/roles")
async def create_role(r: RoleIn, request: Request):
    def run():
        before, roles = _roles_now()
        name, perms = _check_role(r, roles)
        n = max([int(x["id"][1:]) for x in roles] + [0]) + 1
        roles.append({"id": f"r{n}", "name": name, "perms": perms})
        _save_roles(before, roles, _who(request), "role_create", name=name, perms=perms)
        return roles[-1]
    return await db(run)


@admin_router.put("/roles/{rid}")
async def edit_role(rid: str, r: RoleIn, request: Request):
    if rid in auth.BUILTIN:
        raise HTTPException(422, i18n.t("err.users.role_fixed"))      # Admin (und die anderen festen) bleiben, wie sie sind

    def run():
        before, roles = _roles_now()
        cur = next((x for x in roles if x["id"] == rid), None)
        if not cur:
            raise HTTPException(404, i18n.t("err.users.role_not_found"))
        name, perms = _check_role(r, [x for x in roles if x["id"] != rid])
        cur.update(name=name, perms=perms)
        _save_roles(before, roles, _who(request), "role_update", name=name, perms=perms)
        return cur
    return await db(run)


@admin_router.delete("/roles/{rid}")
async def delete_role(rid: str, request: Request):
    if rid in auth.BUILTIN:
        raise HTTPException(422, i18n.t("err.users.role_fixed"))

    def run():
        before, roles = _roles_now()
        cur = next((x for x in roles if x["id"] == rid), None)
        if not cur:
            raise HTTPException(404, i18n.t("err.users.role_not_found"))
        n = rt.store.scalar("SELECT COUNT(*) FROM users WHERE role=?", (rid,))
        if n:
            raise HTTPException(409, i18n.t("err.users.role_in_use", name=cur["name"], count=n))
        _save_roles(before, [x for x in roles if x["id"] != rid], _who(request), "role_delete", name=cur["name"])
    await db(run)
    return {"ok": True}
