"""Statusanzeige (Bildschirm am Pi und Verwaltung): alles in einem Aufruf, plus Touch-Einrichtung."""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import backup as backup_mod, cluster as cluster_mod, device_ids, devices, i18n, monitor as monitor_mod, print_service as ps, \
    system as system_mod
from . import devices as devices_mod, mac_change, stock as stock_mod, test_mode, update as update_mod, uplink_admin
from . import auth, ledger_base as lb
from .core import APP_VERSION, LOCAL_HOSTS, Config, admin, cluster_info, current_user, db, logo_info, rt, \
    security_warnings
from .display import _display_cfg, _display_update

router = APIRouter()


def _monitor_targets() -> list[dict]:
    """Alle eingetragenen Geräte (mit «active»); geprüft und angezeigt werden nur die im Einsatz. Handys gesperrter
    Geräte fehlen, solange sie gesperrt sind (T3)."""
    targets = monitor_mod.parse_targets(rt.store.setting("monitor_devices"), rt.store.setting("monitor_hosts", monitor_mod.DEFAULT_HOSTS))
    if not any(t.get("cert") for t in targets):
        return targets
    off = device_ids.blocked(device_ids.lineages(rt.store))
    return [t for t in targets if t.get("cert") not in off]


def backup_state(info: dict | None = None) -> tuple[list[str], dict | None]:
    """Automatische Sicherung aller Pi: (Hinweis-Codes, neuste Sicherung {node, at, file})."""
    pis = system_mod.nodes(info)
    single = len(Config.peers) <= 1 and backup_mod.installed()      # nur ein Pi: ohne Stick fehlt jede Sicherung
    return backup_mod.warnings({p["id"]: (p.get("info") or {}).get("backup") for p in pis}, single=single)


def warnings(info: dict | None = None) -> list[str]:
    """Hinweise für Übersicht und Statusanzeige (Codes): Notfall-Passwort, Sicherung …"""
    return security_warnings() + backup_state(info)[0]


# Ampel für die Verwaltung (Seitenleiste, alle 5 s): dieselben Störungen und Hinweise wie die Statusanzeige zu Druckern
# und Pi, nur kurz – als Schlüssel aus der Sprachdatei (status.*), der Text entsteht im Browser in seiner Sprache.
_DOWN_KEY = {"paper_out": "status.prn.p_paper", "cover_open": "status.prn.p_cover", "no_status": "status.prn.p_no_status",
             "printer_error": "status.prn.p_error"}


# Mobilfunk-Router: ohne Verbindung ist er nur dann eine Störung (rot), wenn FestPOS ihn braucht –
# Kartenzahlung (Payrexx) oder QR-Bestellung eingeschaltet, oder der Pi geht dauerhaft über das FestPOS-Netz (Router)
# ins Internet. Sonst nur ein Hinweis (gelb).
def uplink_needed() -> bool:
    from . import gast, payrexx, update_net
    try:
        via = update_net._raw().get("via") or "lan"
        return bool(payrexx.config()["enabled"] or gast.config()["open"] or (update_net.keep_on() and via == "lan"))
    except Exception:  # noqa: BLE001 – im Zweifel nicht rot
        return False


def internet_needed() -> bool:
    """Internet ist nötig: Kartenzahlung (Payrexx) oder QR-Bestellung eingeschaltet."""
    from . import gast, payrexx
    try:
        return bool(payrexx.config()["enabled"] or gast.config()["open"])
    except Exception:  # noqa: BLE001 – im Zweifel nicht rot
        return False


def internet_view(pis: list[dict]) -> dict | None:
    """Statusanzeige, Zeile «Internet»: nur mit «Dauerhaft verbunden». Weg, Stand jedes erreichbaren Pi
    (verbunden, erreichbar, Tunnel) und ob es gebraucht wird (dann rot, sonst gelb)."""
    from . import gast, payrexx, update_net
    if not update_net.keep_on():
        return None
    rows = [{"id": p["id"], **n} for p in pis
            if p.get("reachable") and isinstance(n := (p.get("info") or {}).get("net"), dict)]
    try:
        qr = bool(gast.config()["open"])
    except Exception:  # noqa: BLE001
        qr = False
    try:
        pay = bool(payrexx.config()["enabled"])
    except Exception:  # noqa: BLE001
        pay = False
    return {"via": update_net._raw().get("via") or "lan", "needed": pay or qr, "pay": pay, "qr": qr, "pis": rows}


def uplink_issue() -> dict | None:
    """Router eingerichtet und ohne Verbindung (wie die Statusanzeige: Fehler, Abfrage hängt, kein Netz) ->
    {"name", "needed"}; sonst None."""
    v = rt.monitor.uplink.view(uplink_admin.config())
    if not v or v.get("state") in ("pending", "cert_new"):
        return None
    stale = v.get("seconds") is not None and v["seconds"] > max(60, (v.get("interval") or 10) * 6)
    if v.get("state") == "ok" and not stale and (v.get("rating") or {}).get("level") != "bad":
        return None
    return {"name": v.get("name") or "", "needed": uplink_needed()}


def lamp() -> dict:
    """{"level": ok|wait|bad, "bad": [...], "wait": [...]} – Einträge {"key", "name"} bzw. {"key", "pi"}."""
    bad: list[dict] = []
    wait: list[dict] = []
    for r in ps.queue_status_cached(rt.store):
        if not r["enabled"]:                    # ausgeschaltet = nicht im Einsatz (wie die Statusanzeige)
            continue
        chk, name = rt.monitor.printers.get(r["id"], {}), r["name"]
        if rt.spooler.is_down(r["id"]) or chk.get("online") is False:
            bad.append({"key": _DOWN_KEY.get(rt.spooler.down_code(r["id"]) or "", "status.prn.p_down"), "name": name})
        elif chk.get("paper_out"):
            bad.append({"key": "status.prn.p_paper", "name": name})
        elif chk.get("cover_open"):
            bad.append({"key": "status.prn.p_cover", "name": name})
        elif chk.get("no_status"):
            bad.append({"key": "status.prn.p_no_status", "name": name})
        elif chk.get("error"):
            bad.append({"key": "status.prn.p_error", "name": name})
        elif r["failed"] + r["uncertain"]:
            wait.append({"key": "status.prn.w_check", "name": name})
        elif chk.get("paper_near_end"):
            wait.append({"key": "status.prn.w_paper_low", "name": name})
    for n in system_mod.nodes(cluster_info()):
        if not n["reachable"]:
            bad.append({"key": "status.cluster.p_unreachable", "pi": n["id"]})
    up = uplink_issue()
    if up:
        (bad if up["needed"] else wait).append({"key": "status.uplink.p_down", "name": up["name"] or i18n.t("status.uplink.name")})
    return {"level": "bad" if bad else "wait" if wait else "ok", "bad": bad, "wait": wait,
            "notice": system_mod.power_notice()}                     # T27: Balken in der Verwaltung


def _monitor_printers() -> list[dict]:
    rows = ps.queue_status(rt.store)
    return [{"id": r["id"], "host": r["host"], "port": r["port"], "enabled": bool(r["enabled"]),
             "busy": bool(r["pending"] or r["printing"]),     # nur abfragen, wenn nichts zu drucken
             "status_seen": bool(r.get("status_seen"))} for r in rows]


PRINTER_STATE = "printer_state"   # früher: Stand der Drucker in der Datenbank. Seit T9 fragen die Pi den druckenden direkt


def printer_state_get() -> dict:
    """Nur während eines Updates: ein alter druckender Pi legt den Stand noch hier ab (höchstens 3 Minuten alt, sonst
    «unbekannt»). Monitor.leader_printers nimmt ihn, wenn kein Pi «druckt» meldet. Im nächsten Release entfernen."""
    try:
        d = json.loads(rt.store.setting(PRINTER_STATE) or "{}")
    except ValueError:
        return {}
    if not isinstance(d, dict) or time.time() - (d.get("at") or 0) > 180:
        return {}
    return {int(k): v for k, v in (d.get("printers") or {}).items() if str(k).isdigit() and isinstance(v, dict)}


def printer_state_forget() -> None:
    """Beim Start: alten Eintrag einmal löschen, damit kein veralteter Stand in Sicherungen bleibt. Nur wenn es ihn
    gibt – auch ein DELETE ohne Treffer wäre ein Schreibzugriff auf allen Pi (T9)."""
    if rt.store.setting(PRINTER_STATE, None) is not None:
        rt.store.execute("DELETE FROM settings WHERE key=?", (PRINTER_STATE,))


# -- Statusanzeige (Bildschirm am Pi, Kiosk-Modus) --------------------------------


def _seen_devices() -> list[dict]:
    """Keine Handys eingetragen: die gesehenen, eines pro Gerät (T1). Mit Zertifikat zählt die Geräte-ID – nach einer
    Erneuerung der jüngste Eintrag –, sonst IP und App-Name. Gross der Gerätename, klein der Name aus der App (der darf
    auf mehreren Handys gleich sein). Sortiert nach Name, dann ID: gleichnamige tauschen nie die Plätze."""
    ids = devices.serial_map()
    best: dict[str, dict] = {}
    for d in rt.monitor.device_list():
        did, cert_name = ids.get(d.get("serial") or "", (None, None))
        key = f"c:{did}" if did else d["key"]
        if key in best and best[key]["last_seen"] >= d["last_seen"]:
            continue
        name = cert_name or d.get("cert_name") or d["name"] or d["ip"] or "?"
        best[key] = {**d, "id": key, "name": name, "app_name": d["name"] or None, "exit": key}
    out = sorted(best.values(), key=lambda d: (d["name"].casefold(), d["id"]))
    for d in out:
        for k in ("key", "serial", "cert_name", "last_seen"):
            d.pop(k, None)
    return out


def _device_viewer(request: Request) -> bool:
    """Bestell-Handy mit gültigem Gerätezertifikat am Handy-Port: darf die Statusanzeige ansehen (ohne Knöpfe) –
    so kommt die Bedienung in Fully von der Bestell-App zur Statusanzeige und zurück, ohne Anmeldung."""
    if not Config.phone_port or not devices.via_phone(request):
        return False
    info = devices.client_cert(request)
    return bool(info) and info["serial"] not in devices.revoked_serials()


def status_access(request: Request):
    """Vom Pi selbst (Kiosk-Bildschirm) ohne Anmeldung (nur anschauen), von Bestell-Handys mit Gerätezertifikat
    (nur anschauen), sonst nur mit der Anmeldung der Verwaltung. Mit Anmeldung – auch am Pi – zeigt die Anzeige
    je nach Rolle Knöpfe (z.B. Handy neu starten ab Festleitung)."""
    if request.client and request.client.host in LOCAL_HOSTS and not request.headers.get("authorization"):
        return
    if not request.headers.get("authorization") and _device_viewer(request):
        return
    admin(request)


JOB_STATE = {"done": "ok", "cancelled": "ok", "pending": "wait", "printing": "wait", "failed": "problem",
             "uncertain": "problem"}


def _with_print(orders: list[dict]) -> None:
    """Gerät «XCover101 (Anna)» und Druckstand pro Station (ok | wait | problem) an die Bestellungen hängen."""
    for o in orders:                                 # «XCover101 (Anna)»: Gerät und Name aus der App
        o["device"] = lb.device_label(o.pop("device_cert", None), o["device"])
    if not orders:
        return
    marks = ",".join("?" * len(orders))
    jobs = rt.store.query(
        f"SELECT j.order_id, s.name AS station, j.status FROM print_jobs j JOIN printers s ON s.id=j.station_id "
        f"WHERE j.order_id IN ({marks}) ORDER BY j.id", tuple(o["id"] for o in orders))
    rank = {"ok": 0, "wait": 1, "problem": 2}
    per: dict[int, dict[str, str]] = {}
    for j in jobs:
        st = per.setdefault(j["order_id"], {})
        cur, new = st.get(j["station"], "ok"), JOB_STATE.get(j["status"], "wait")
        st[j["station"]] = new if rank[new] > rank[cur] else cur
    for o in orders:
        o["print"] = [{"station": k, "state": v} for k, v in per.get(o["id"], {}).items()]


_ORDER_COLS = ("SELECT o.id, o.created_at, o.table_label, o.device, o.device_cert, o.test, o.no_print, o.payment, "
               "o.tab_id, o.total_cents - COALESCE((SELECT SUM(amount_cents) FROM cancellations WHERE order_id=o.id),0) "
               "AS total_cents, (SELECT COUNT(*) FROM cancellations WHERE order_id=o.id) AS stornos, "
               "(SELECT SUM(qty) FROM order_items WHERE order_id=o.id) AS items FROM orders o ")


def orders_since_closing(before: int | None, limit: int) -> dict:
    """T29: alle Bestellungen seit dem letzten Tagesabschluss, neueste zuerst, seitenweise."""
    where, args = "WHERE o.closing_id IS NULL", []
    if before:
        where += " AND o.id < ?"
        args.append(before)
    rows = rt.store.query(_ORDER_COLS + where + " ORDER BY o.id DESC LIMIT ?", (*args, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    _with_print(rows)
    sums = rt.store.query_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(o.total_cents - COALESCE((SELECT SUM(amount_cents) FROM cancellations "
        "WHERE order_id=o.id),0)),0) AS total_cents FROM orders o WHERE o.closing_id IS NULL AND o.test=0")
    return {"orders": rows, "more": more, "count": sums["count"], "total_cents": sums["total_cents"]}


def order_detail(oid: int) -> dict | None:
    """T29: eine Bestellung für die Statusanzeige – Positionen, Stornos, Bons pro Drucker, Zahlart, Trinkgeld."""
    rows = rt.store.query(_ORDER_COLS + "WHERE o.id=?", (oid,))
    if not rows:
        return None
    o = rows[0]
    _with_print([o])
    o["lines"] = rt.store.query("SELECT id, name, qty, price_cents, options, note, delivery FROM order_items "
                                "WHERE order_id=? ORDER BY line_no, id", (oid,))
    cancelled = {r["item_id"]: r["q"] for r in rt.store.query(
        "SELECT ci.item_id, SUM(ci.qty) AS q FROM cancellation_items ci JOIN cancellations c ON c.id=ci.cancellation_id "
        "WHERE c.order_id=? GROUP BY ci.item_id", (oid,))}
    for line in o["lines"]:
        line["cancelled"] = cancelled.get(line["id"], 0)
    o["cancellations"] = rt.store.query("SELECT reason, amount_cents, created_at, COALESCE(by_user, by_device) AS who "
                                        "FROM cancellations WHERE order_id=? ORDER BY id", (oid,))
    o["jobs"] = rt.store.query(
        "SELECT s.name AS station, p.name AS printer, j.kind, j.status, j.reprint, j.created_at, j.printed_at, "
        "j.error_code FROM print_jobs j JOIN printers s ON s.id=j.station_id JOIN printers p ON p.id=j.printer_id "
        "WHERE j.order_id=? ORDER BY j.id", (oid,))
    for j in o["jobs"]:
        j["state"] = JOB_STATE.get(j["status"], "wait")
    tip = rt.store.query_one("SELECT amount_cents FROM tips WHERE order_id=?", (oid,))
    o["tip_cents"] = tip["amount_cents"] if tip else 0
    return o


def _status_data(local: bool = False, user: dict | None = None) -> dict:
    from datetime import datetime, timedelta
    hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    totals = rt.store.query_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_cents),0) - COALESCE((SELECT SUM(c.amount_cents) FROM "
        "cancellations c JOIN orders o2 ON o2.id=c.order_id WHERE o2.test=0),0) AS total_cents, "
        "MAX(created_at) AS last_at, SUM(created_at >= ?) AS last_hour FROM orders WHERE test=0", (hour_ago,))
    recent = rt.store.query(
        "SELECT o.id, o.created_at, o.table_label, o.device, o.device_cert, o.test, "
        "o.total_cents - COALESCE((SELECT SUM(amount_cents) FROM cancellations WHERE order_id=o.id),0) AS total_cents, "
        "(SELECT COUNT(*) FROM cancellations WHERE order_id=o.id) AS stornos, "
        "(SELECT SUM(qty) FROM order_items WHERE order_id=o.id) AS items FROM orders o ORDER BY o.id DESC LIMIT 40")
    _with_print(recent)
    printers = []
    for r in ps.queue_status_cached(rt.store):
        if not r["enabled"]:                    # ausgeschaltet = nicht im Einsatz: nicht auf der Statusanzeige
            continue
        chk = rt.monitor.printers.get(r["id"], {})
        printers.append({"name": r["name"], "enabled": bool(r["enabled"]), "down": rt.spooler.is_down(r["id"]),
                         "reason": rt.spooler.down_reason(r["id"]), "reason_code": rt.spooler.down_code(r["id"]),
                         "pending": r["pending"] + r["printing"], "problems": r["failed"] + r["uncertain"],
                         "online": chk.get("online"), "paper_out": chk.get("paper_out"),
                         "paper_near_end": chk.get("paper_near_end"), "cover_open": chk.get("cover_open"),
                         "error": chk.get("error"), "no_status": chk.get("no_status")})
    info = cluster_info()                       # Zwischenstand – ein ausgesteckter Pi bremst nicht
    nodes = None
    if info.get("mode") == "rqlite":
        nodes = [{"id": k, "reachable": bool(v.get("reachable")), "leader": bool(v.get("leader"))}
                 for k, v in sorted((info.get("nodes") or {}).items())]
        if len(nodes) < 2:                     # ein einzelner Pi ist kein Cluster
            nodes = None
    # Handys: die in der Verwaltung eingetragenen (Name + IP), sonst die bisher gesehenen
    targets = _monitor_targets()
    listed = [t for t in targets if t["kind"] == "phone"]
    phones = [t for t in listed if t["active"]]
    net_count = sum(1 for t in targets if t["kind"] == "net" and t["active"])
    if listed:                                  # eingetragen (auch wenn gerade keines im Einsatz ist)
        checked = {(t["ip"], t["port"]): t["ok"] for t in rt.monitor.targets if t["kind"] == "phone"}
        devices = []
        fully = rt.monitor.fully_view()
        serials: dict[str, set[str]] = {}                      # Gerät -> seine Zertifikate (T4: über das Gerät)
        for serial, (did, _) in devices_mod.serial_map().items():
            serials.setdefault(did, set()).add(serial)
        alarm = mac_change.alarms() if any(t.get("cert") for t in phones) else {}
        for t in phones:
            app_ = rt.monitor.app_at(t["ip"], serials.get(t.get("cert") or "")) or {}
            dev = {"id": t.get("id") or f"ip:{t['ip']}", "name": t["name"], "ip": t["ip"], "ping": checked.get((t["ip"], t["port"])),
                   "online": bool(app_.get("online")), "seconds": app_.get("seconds"),
                   "outbox": app_.get("outbox", 0), "test": bool(app_.get("test")), "configured": True,
                   "exit": f"c:{t['cert']}" if t.get("cert") else f"ip:{t['ip']}",   # Testmodus beenden (T7)
                   "test_since": app_.get("test_since"),
                   "app_name": app_.get("app_name"),            # zuletzt gemeldeter Name aus der App (auf den Bestellungen)
                   "mac_alarm": t.get("cert") in alarm}         # zwei Geräte mit demselben Zertifikat (T4)
            dev.update({k: app_[k] for k in ("battery", "charging", "battery_age", "battery_src") if k in app_})
            f = fully.get(t["ip"]) if t.get("fully_pin") else None
            if f:
                data = f.get("data") or {}
                dev["fully"] = {"state": f["state"], "temp": data.get("temp"), "screen_on": data.get("screen_on"),
                                "data": data, "data_seconds": f.get("data_seconds"), "seconds": f["seconds"]}
                # Fully weiss es genauer und auch bei geschlossener App – wenn frischer als die Meldung der App
                if data.get("battery") is not None and f["data_seconds"] <= dev.get("battery_age", 10 ** 9):
                    dev.update(battery=data["battery"], charging=bool(data.get("charging")),
                               battery_age=f["data_seconds"], battery_src="fully")
            devices.append(dev)
    else:
        devices = _seen_devices()
    if any(d.get("test") for d in devices):                 # «wird beendet …», bis das Handy es übernommen hat (T7)
        reqs = test_mode.requests_()
        for d in devices:
            keys = [d["exit"]] + ([f"ip:{d['ip']}"] if d.get("ip") else [])
            d["test_ending"] = bool(d.get("test")) and test_mode.due(reqs, keys, d.get("test_since"))
    for d in devices:
        d.pop("test_since", None)
    pis = system_mod.nodes(info)
    return {
        "event_name": rt.store.setting("event_name"), "version": APP_VERSION, "server_time": time.time(),
        **logo_info(rt.store),
        "currency": rt.store.setting("currency", "CHF"), "table_word": rt.store.setting("table_word", "Tisch"),
        "node": Config.node, "printing_here": rt.spooler.is_leader, "cluster": nodes,
        "pis": pis,                                        # pro Pi: Modell, OS, Laufzeit … (Klick in der Anzeige)
        # wer schaut (Knöpfe je nach Rolle); am Pi-Bildschirm ohne Anmeldung None = nur anschauen
        "me": {"name": user.get("name"), "role": user.get("role"),
               "perms": sorted(auth.perms_of(user.get("role")))} if user else None,
        "einzelbetrieb": (Path(Config.rq_data) / cluster_mod.EINZELBETRIEB_FLAG).exists(),     # Notfall (festpos-notfall)
        "security": Config.phone_certs_problem or None,
        "warnings": warnings(info),                                 # Codes, Text in status.js
        "totals": totals, "recent": recent, "printers": printers, "devices": devices,
        "stock": stock_mod.status_list(rt.store),               # ausverkauft / Restmenge an der Warnschwelle
        "network": rt.monitor.hosts, "network_count": net_count,          # Netzwerkgeräte im Einsatz (0: Karte weg)
        "services": rt.monitor.services, "system": rt.monitor.system,
        "uplink": rt.monitor.uplink.view(uplink_admin.config()),     # Mobilfunk-Router (Empfang), None = keiner
        "uplink_needed": uplink_needed(),                           # ohne Verbindung rot (sonst nur gelb)
        "internet": internet_view(pis),                             # dauerhaft verbunden: Weg, erreichbar, Tunnel
        "update": update_mod.status_view(),                          # Update läuft / USB-Stick, None = nichts
        "notice": system_mod.power_notice(),                        # Herunterfahren/Neustart angekündigt (T27)
        # nur für den Bildschirm am Pi selbst: Drehung, Grösse, Touch
        "kiosk": ({k: v for k, v in _display_cfg(Config.node).items() if k in ("type", "rotate", "size", "touchtest", "cal")}
                  if local else None),
    }


@router.get("/api/status", dependencies=[Depends(status_access)])
async def status(request: Request):
    """Alles für die Statusanzeige in einem Aufruf (alle paar Sekunden abgefragt)."""
    local = bool(request.client and request.client.host in LOCAL_HOSTS)
    user = current_user(request)
    data = await db(_status_data, local, user)
    # Links zu Bestell-App und Verwaltung – nie am Bildschirm des Pi (dort gibt es kein Zurück)
    data["viewer"] = "user" if user else ("kiosk" if local else "device")
    # (auch nicht, wenn sich jemand am Pi anmeldet)
    # Handheld mit Rolle «Nur Statusanzeige»: kein Weg zur Bestell-App
    info = devices.client_cert(request) if not local and devices.via_phone(request) else None
    status_only = bool(info) and await db(lb.device_role, rt.store, info) == "status"
    data["status_only"] = status_only
    data["nav"] = {"app": not local and devices.via_phone(request) and not status_only, "admin": not local}
    return data


@router.get("/api/status/orders", dependencies=[Depends(status_access)])
async def status_orders(before: int | None = None, limit: int = 50):
    """T29: Bestellungen seit dem Tagesabschluss (Statusanzeige › «Letzte Bestellungen» antippen)."""
    return await db(orders_since_closing, before, max(1, min(limit, 100)))


@router.get("/api/status/orders/{oid}", dependencies=[Depends(status_access)])
async def status_order(oid: int):
    o = await db(order_detail, oid)
    if not o:
        raise HTTPException(404, i18n.t("err.order.not_found"))
    return o


class TouchCalIn(BaseModel):
    cal: list[float] = Field(min_length=6, max_length=6)


@router.post("/api/status/touchcal")
async def status_touchcal(request: Request, c: TouchCalIn):
    """Die Statusanzeige am Pi meldet die Touch-Einrichtung (nur vom Pi selbst)."""
    if not (request.client and request.client.host in LOCAL_HOSTS):
        raise HTTPException(403, i18n.t("err.status.local_only"))
    if any(abs(x) > 10 for x in c.cal):
        raise HTTPException(422, i18n.t("err.status.invalid"))
    await db(_display_update, Config.node, cal=[round(x, 5) for x in c.cal], touchtest=False)
    return {"ok": True}
