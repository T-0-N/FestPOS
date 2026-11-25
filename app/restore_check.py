"""Prüfliste nach dem Einspielen einer Sicherung (T8): was automatisch geprüft werden kann (Zertifikate, Schlüssel,
Drucker, Handys, Zeit, Zugangsdaten, Fully-APK, feste IPs, Sicherung auf dem Stick) und was von Hand bleibt.

Was beim Einspielen festgestellt wurde, steht in der Einstellung restore_check (maintenance._prepare_backup). Die
Punkte werden bei jedem Aufruf neu gerechnet – keine Schreibzugriffe im Takt (SD-Karten, T9). Zustände: ok | warn |
bad | manual | wait. Rot (bad) nur, was den Betrieb verhindert: andere Geräte-CA, Drucker nicht erreichbar.
Sichtbar bis «Alles erledigt» (Übersicht höchstens 7 Tage), unter Wartung › Sicherung bis zum nächsten Einspielen."""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import backup as backup_mod, cards, ledger
from . import ledger_base as lb
from .core import Config, admin, db, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])
SHOW_DAYS = 7                       # so lange zeigt die Übersicht die Karte, wenn niemand «Alles erledigt» drückt


def record() -> dict | None:
    from .maintenance import RESTORE_CHECK
    try:
        rc = json.loads(rt.store.setting(RESTORE_CHECK) or "null")
    except ValueError:
        return None
    return rc if isinstance(rc, dict) else None


def _item(id_: str, state: str, **data) -> dict:
    return {"id": id_, "state": state, **data}


def _ids_items(rc: dict) -> list[dict]:
    from .maintenance import system_ids
    now, old = system_ids(), rc.get("ids_backup") or {}
    keys = rc.get("keys")
    out = []
    if not old.get("device_ca"):
        out.append(_item("device_ca", "manual"))                     # ältere Sicherung: mit einem Handy bestellen
    elif old["device_ca"] == now.get("device_ca"):
        out.append(_item("device_ca", "ok"))
    else:
        out.append(_item("device_ca", "wait" if keys == "restoring" else "bad", keys=keys))
    if old.get("server_ca"):
        same = old["server_ca"] == now.get("server_ca")
        out.append(_item("server_ca", "ok" if same else "wait" if keys == "restoring" else "warn", keys=keys))
    has_key = bool(Config.device_ca_key and Path(Config.device_ca_key).exists())
    out.append(_item("device_ca_key", "ok" if has_key else "warn"))
    if old.get("ledger_key"):
        same = old["ledger_key"] == now.get("ledger_key")
        out.append(_item("ledger_key", "ok" if same else "wait" if keys == "restoring" else "warn"))
    if keys in ("differ", "differ_cluster", "other_net"):
        out.append(_item("keys", "warn", cluster=keys == "differ_cluster", single=len(Config.peers) <= 1,
                         other_net=keys == "other_net"))
    return out


def _status_items() -> list[dict]:
    from . import status as status_mod
    data = status_mod._status_data(False, None)
    out = []
    for p in data["printers"]:
        if p["online"] is None and not p["down"]:
            state, why = "wait", None
        elif p["online"] is False or p["down"]:
            state, why = "bad", p.get("reason_code") or "unreachable"
        elif p.get("paper_out") or p.get("cover_open") or p.get("error") or p.get("no_status"):
            state, why = "bad", ("paper_out" if p.get("paper_out") else "cover_open" if p.get("cover_open")
                                 else "no_status" if p.get("no_status") else "printer_error")
        else:
            state, why = "ok", None
        out.append(_item("printer", state, name=p["name"], reason=why))
    phones = [d for d in data["devices"] if d.get("configured")]
    if phones:
        missing = [d["name"] for d in phones if not d.get("online")]
        out.append(_item("phones", "ok" if not missing else "warn", total=len(phones),
                         online=len(phones) - len(missing), missing=missing[:12]))
    return out


def _time_item() -> dict:
    """Uhr vor der letzten Bestellung oder nicht aus einer sicheren Quelle (RTC-Batterie leer, T2): falsch. Den
    Vergleich mit dem Browser macht die Verwaltung (backup.js)."""
    from .time_admin import local_state
    from .timesync import time_floor
    floor = time_floor(rt.store)
    bad = time.time() < floor - 60 or not local_state()["sure"]
    return _item("time", "bad" if bad else "ok", server_time=time.time())


def _secrets_item() -> dict:
    """Zugangsdaten, die gebraucht werden, aber fehlen (ältere Sicherung oder neu einzutragen)."""
    from . import fully, uplink_admin
    s = rt.store.setting
    missing = []
    if any(t.get("fully_pin") for t in fully.config()["targets"]) and not s("fully_password"):
        missing.append("fully")
    if s("fully_prov_ssid") and not s("fully_prov_wifi_password"):
        missing.append("wifi")
    if uplink_admin._raw().get("kind") and not s("uplink_password"):
        missing.append("router")
    try:
        repo = (json.loads(s("update_net") or "{}") or {}).get("repo")
    except (ValueError, AttributeError):
        repo = None
    if repo and not s("update_net_token"):
        missing.append("update")
    return _item("secrets", "manual" if missing else "ok", missing=missing)


def _apk_item() -> dict | None:
    """Fully-APK auf diesem Pi: die massgebliche (T6) – fehlt sie, holt er sie von einem anderen Pi («wait») oder sie
    kommt vom Stick bzw. wird neu hochgeladen («warn»)."""
    from . import fully, fully_apk
    m = fully_apk.meta()
    if fully.provision_config().get("apk_local") != "1" and not m:
        return None
    if fully_apk.current_here() or (not m and fully.apk_info()):
        return _item("apk", "ok")
    src = fully_apk.holders(m["checksum"]) if m else []
    if src:
        return _item("apk", "wait", pi=src[0]["id"])
    return _item("apk", "warn", helper=cards.available())


def _dhcp_item() -> dict | None:
    from . import dhcp
    if not dhcp.enabled():
        return None
    want, have = dhcp.reservations_expected(), dhcp.reservations_written()
    return _item("dhcp", "ok" if have is not None and have >= want else "warn", count=want, written=have)


def _backup_item() -> dict | None:
    if not backup_mod.installed():
        return None
    st = backup_mod.read_status() or {}
    ok = st.get("state") == "ok" and time.time() - (st.get("at") or 0) < backup_mod.OLD_S
    single = len(Config.peers) <= 1
    return _item("usb", "ok" if ok else ("warn" if single else "manual"), state=st.get("state"),
                 last_ok=st.get("last_ok"), code=st.get("code"))


def _receipt_item(rc: dict) -> dict | None:
    last, nxt = rc.get("receipt_last_backup") or 0, rc.get("receipt_next") or 0
    if not last and nxt <= 1:
        return None
    from .maintenance import RECEIPT_FLOOR
    floor = int(rt.store.setting(RECEIPT_FLOOR) or 0)
    current = int(rt.store.scalar("SELECT COALESCE(MAX(no), 0) FROM receipts") or 0)
    return _item("receipts", "warn" if rc.get("case") == "new" else "ok", last_backup=last,
                 last_now=rc.get("receipt_last_now") or 0, next=max(floor, current) + 1, case=rc.get("case"))


def checklist() -> dict:
    rc = record()
    if not rc:
        return {"active": False}
    items = _ids_items(rc) + _status_items() + [_time_item(), _secrets_item()]
    items += [i for i in (_apk_item(), _dhcp_item(), _backup_item(), _receipt_item(rc)) if i]
    test = rt.store.setting("test_enabled", "0") == "1"
    manual = [_item("m_test", "manual", on=test), _item("m_orders", "manual", backup_at=rc.get("backup_at")),
              _item("m_try", "manual")]
    if rc.get("case") == "new":
        manual.append(_item("m_new", "manual"))
    auto_open = sum(1 for i in items if i["state"] not in ("ok",))
    return {"active": True, **{k: rc.get(k) for k in ("at", "by", "case", "file", "backup_node", "backup_at",
                                                      "encrypted", "keys", "certs_added", "revocations_added", "done")},
            "items": items, "manual": manual, "open": auto_open, "total": len(items),
            "show_overview": not rc.get("done") and time.time() - (rc.get("at") or 0) < SHOW_DAYS * 86400}


def overview_state() -> dict | None:
    """Für die Übersicht (leicht: nur der Zähler, ohne Statusanzeige zu rechnen) -> {open, bad} oder None."""
    rc = record()
    if not rc or rc.get("done") or time.time() - (rc.get("at") or 0) > SHOW_DAYS * 86400:
        return None
    items = _ids_items(rc) + [_time_item(), _secrets_item()] + [i for i in (_apk_item(),) if i]
    return {"open": sum(1 for i in items if i["state"] != "ok"), "bad": any(i["state"] == "bad" for i in items)}


@admin_router.get("/restore-check")
async def get_checklist():
    return await db(checklist)


@admin_router.post("/restore-check/done")
async def done(request: Request):
    from .maintenance import RESTORE_CHECK, UPSERT

    def run():
        rc = record()
        if not rc:
            raise HTTPException(404, t("err.restore.no_check"))
        rc["done"] = int(time.time())
        rt.store.execute(UPSERT, (RESTORE_CHECK, json.dumps(rc, sort_keys=True)))
    await db(run)
    await db(lb.audit, rt.store, ledger.actor_of(request), "restore_check_done")
    return await db(checklist)


class ReceiptNextIn(BaseModel):
    next: int = Field(ge=1, le=10 ** 9)


@admin_router.post("/restore-check/receipt-next")
async def receipt_next(r: ReceiptNextIn, request: Request):
    """Nächste Quittungsnummer erhöhen (nie senken – sonst gäbe es eine Nummer doppelt)."""
    from .maintenance import RECEIPT_FLOOR, UPSERT

    def run() -> int:
        floor = int(rt.store.setting(RECEIPT_FLOOR) or 0)
        current = int(rt.store.scalar("SELECT COALESCE(MAX(no), 0) FROM receipts") or 0)
        if r.next <= max(floor, current):
            raise HTTPException(422, t("err.restore.receipt_lower", next=max(floor, current) + 1))
        rt.store.execute(UPSERT, (RECEIPT_FLOOR, str(r.next - 1)))
        return max(floor, current) + 1
    before = await db(run)
    await db(lb.audit, rt.store, ledger.actor_of(request), "receipt_gap", first=before, last=r.next - 1, case="manual")
    return await db(checklist)


@admin_router.post("/restore-check/apk")
async def apk(request: Request):
    """Fully-APK vom USB-Stick holen (liegt dort, seit festpos-sicherung sie einmal daneben gelegt hat)."""
    await db(cards.apk_from_stick)
    from . import fully_apk
    await db(fully_apk.adopt_local, True)                  # die vom Stick gilt jetzt für alle Pi (T6)
    await db(lb.audit, rt.store, ledger.actor_of(request), "apk_from_stick")
    return await db(checklist)
