"""Einfache Statusanzeige für alte Geräte (z.B. iPad mini mit iOS 9): /status-einfach.

Die normale Statusanzeige (status.html) braucht einen neuen Browser (ES2017, fetch, Pointer-Events, CSS min()).
Diese Seite baut der Server fertig als HTML, mit schlichtem CSS (status-einfach.css). Ein kleines Skript in ES5
(status-einfach.js) holt alle REFRESH Sekunden nur den Inhalt (?teil=1) und tauscht ihn aus – ohne Neuladen der
ganzen Seite, also ohne weisses Aufblitzen. Ohne JavaScript lädt <noscript><meta http-equiv="refresh"></noscript>
die Seite wie bisher neu. Nur anschauen, keine Knöpfe.
Gleiche Daten und gleicher Zugang wie /api/status (am Pi frei, Handheld mit Zertifikat, sonst Anmeldung der
Verwaltung – auf dem iPad fragt Safari nach Benutzer und Passwort)."""
from __future__ import annotations

import time
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from .core import LOCAL_HOSTS, current_user, db
from .i18n import t
from .status import _status_data, status_access

router = APIRouter()
REFRESH = 10                                          # Sekunden bis zum nächsten Abruf
MAX_ORDERS = 8

_SVC = {"rqlited": "status.svc.rqlited", "festpos-redirect": "status.svc.redirect", "chrony": "status.svc.chrony",
        "nftables": "status.svc.nftables", "keepalived": "status.svc.keepalived"}
_WARN = {"admin_pw_placeholder": "status.warn.admin_pw", "admin_pw_short": "status.warn.admin_pw",
         "backup_none": "status.warn.backup_none", "backup_old": "status.warn.backup_old",
         "backup_failed": "status.warn.backup_failed"}
_WARN_BAD = {"admin_pw_placeholder", "backup_failed"}


def _e(v) -> str:
    return escape(str(v if v is not None else ""))


def money(c) -> str:
    """Betrag in Schweizer Schreibweise (1'234.50), wie in status.js."""
    c = round(c or 0)
    v = abs(c)
    return ("−" if c < 0 else "") + f"{v // 100:,}".replace(",", "'") + f".{v % 100:02d}"


def _hhmm(iso: str | None) -> str:
    return iso[11:16] if iso else "–"


def _since(sec) -> str:
    if sec is None:
        return t("status.dev.offline")
    if sec < 60:
        return t("status.dev.just_active")
    m = round(sec / 60)
    return t("status.dev.minutes_ago", count=m) if m < 60 else t("status.dev.hours_ago", count=m // 60)


def _pi_label(pid: str) -> str:
    s = str(pid)
    if s[:2].lower() == "pi":
        s = s[2:].lstrip("-_ ")
    return t("status.cluster.pi", id=s.upper())


def _printers(s: dict, problems: list, waits: list) -> str:
    out = []
    for p in s.get("printers") or []:
        cls, st = "ok", t("status.prn.ready")
        sub = t("status.prn.in_work", count=p["pending"]) if p.get("pending") else t("status.prn.idle")
        if p.get("down") or p.get("online") is False:
            cls = "bad"
            code = p.get("reason_code") or "unreachable"
            wait = t("status.prn.waiting", count=p["pending"]) if p.get("pending") else ""
            if code == "paper_out":
                st, sub = t("status.prn.paper_out"), t("status.prn.refill") + wait
                problems.append(t("status.prn.p_paper", name=p["name"]))
            elif code == "cover_open":
                st, sub = t("status.prn.cover_open"), t("status.prn.close_cover") + wait
                problems.append(t("status.prn.p_cover", name=p["name"]))
            elif code == "no_status":
                st, sub = t("status.prn.no_status"), t("status.prn.check_paper_cover") + wait
                problems.append(t("status.prn.p_no_status", name=p["name"]))
            elif code == "printer_error":
                st, sub = t("status.prn.error"), t("status.prn.restart") + wait
                problems.append(t("status.prn.p_error", name=p["name"]))
            else:
                st, sub = t("status.prn.down"), t("status.prn.unreachable") + wait
                problems.append(t("status.prn.p_down", name=p["name"]))
        elif p.get("paper_out"):
            cls, st = "bad", t("status.prn.paper_out")
            problems.append(t("status.prn.p_paper", name=p["name"]))
        elif p.get("cover_open"):
            cls, st = "bad", t("status.prn.cover_open")
            problems.append(t("status.prn.p_cover", name=p["name"]))
        elif p.get("no_status"):
            cls, st, sub = "bad", t("status.prn.no_status"), t("status.prn.check_paper_cover")
            problems.append(t("status.prn.p_no_status", name=p["name"]))
        elif p.get("error"):
            cls, st, sub = "bad", t("status.prn.error"), t("status.prn.check_printer")
            problems.append(t("status.prn.p_error", name=p["name"]))
        elif p.get("problems"):
            cls, st, sub = "wait", t("status.prn.check"), t("status.prn.not_printed", count=p["problems"])
            waits.append(t("status.prn.w_check", name=p["name"]))
        elif p.get("paper_near_end"):
            cls, st = "wait", t("status.prn.paper_low")
            waits.append(t("status.prn.w_paper_low", name=p["name"]))
        out.append(f'<div class="prn {cls}"><b class="st">{_e(st)}</b><b>{_e(p["name"])}</b><small>{_e(sub)}</small></div>')
    return "".join(out) or f'<p class="empty">{_e(t("status.prn.none"))}</p>'


def _orders(s: dict) -> str:
    word = t("status.word.seat") if s.get("table_word") == "Platz" else t("status.word.table")
    down = {p["name"] for p in s.get("printers") or [] if p.get("down") or p.get("online") is False}
    rows = []
    for o in (s.get("recent") or [])[:MAX_ORDERS]:
        lamps = []
        for p in o.get("print") or []:
            stuck = p["state"] == "wait" and p["station"] in down
            cls = "bad" if p["state"] == "problem" or stuck else p["state"]
            w = t("status.lamp.waits") if stuck else t("status.lamp.prints") if p["state"] == "wait" \
                else t("status.lamp.check") if p["state"] == "problem" else ""
            lamps.append(f'<span class="lamp {cls}">{_e(p["station"])}{" " + _e(w) if w else ""}</span>')
        tags = (f'<span class="tag test">{_e(t("status.order.test_tag"))}</span> ' if o.get("test") else "") + \
               (f'<span class="tag">{_e(t("status.order.storno_tag"))}</span> ' if o.get("stornos") else "")
        none = t("status.order.test_no_bon") if o.get("test") else t("status.order.no_bon")
        meta = t("status.order.meta", count=o.get("items") or 0, no=str(o["id"]).zfill(4))
        rows.append(f'<tr class="{"test" if o.get("test") else ""}"><td class="tm">{_hhmm(o.get("created_at"))}</td>'
                    f'<td>{tags}<b>{_e(word)} {_e(o.get("table_label"))}</b> <small>{_e(o.get("device"))}</small>'
                    f'<div class="lamps">{"".join(lamps) or _e(none)}</div></td>'
                    f'<td class="num"><b>{money(o.get("total_cents"))}</b><small>{_e(meta)}</small></td></tr>')
    if not rows:
        return f'<p class="empty">{_e(t("status.order.none"))}</p>'
    return f'<table class="orders">{"".join(rows)}</table>'


def _devices(s: dict, problems: list, waits: list) -> str:
    out = []
    for d in s.get("devices") or []:
        name = d.get("name") or ""
        cls = "ok" if d.get("online") else ""
        note = t("status.dev.online") if d.get("online") else _since(d.get("seconds"))
        if d.get("configured") and not d.get("online"):
            note = t("status.dev.checking") if d.get("ping") is None else t("status.dev.unreachable")
        if not d.get("online") and d.get("ping") is True:
            cls, note = "idle", t("status.dev.app_closed")
        fd = (d.get("fully") or {}).get("data") if (d.get("fully") or {}).get("state") == "ok" else None
        if fd and (fd.get("kiosk") is False or fd.get("kiosk_locked") is False):
            cls, note = "wait", t("status.dev.kiosk_off")
            waits.append(t("status.dev.kiosk_off_warn", name=name))
        elif fd and fd.get("licensed") is False:
            cls, note = "wait", t("status.dev.unlicensed")
            waits.append(t("status.dev.unlicensed_warn", name=name))
        if d.get("outbox", 0) > 0:
            cls, note = "wait", t("status.dev.outbox", count=d["outbox"])
            waits.append(t("status.dev.resending", name=name))
        if d.get("test") and d.get("online"):
            cls, note = "wait", t("status.dev.test_ending") if d.get("test_ending") else t("status.dev.test")
            waits.append(t("status.dev.test_warn", name=name))
        if d.get("mac_alarm"):
            cls, note = "bad", t("status.dev.mac_alarm")
            problems.append(t("status.dev.mac_alarm_warn", name=name))
        bat = ""
        if d.get("battery") is not None:
            pct, charging = d["battery"], d.get("charging")
            low = "bad" if not charging and pct <= 10 else "wait" if not charging and pct <= 20 else ""
            if low == "bad":
                problems.append(t("status.dev.battery_low", name=name, pct=pct))
            elif low:
                waits.append(t("status.dev.battery_low", name=name, pct=pct))
            bat = f'<span class="bat {low}">{_e(pct)}&nbsp;%{" ⚡" if charging else ""}</span>'
        if ((d.get("fully") or {}).get("temp") or 0) >= 45:
            waits.append(t("status.dev.hot", name=name, temp=d["fully"]["temp"]))
        who = f' <small>{_e(d["app_name"])}</small>' if d.get("app_name") and d["app_name"] != name else ""
        out.append(f'<tr><td><span class="lamp {cls}">{_e(name)}</span>{who}</td>'
                   f'<td class="num">{bat}</td><td class="note {cls}">{_e(note)}</td></tr>')
    if not out:
        return f'<p class="empty">{_e(t("status.dev.none"))}</p>'
    return f'<table class="list">{"".join(out)}</table>'


def _network(s: dict, problems: list, waits: list) -> str:
    chips = []
    up = s.get("uplink")
    if up and up.get("state") not in ("ok", "pending"):            # Mobilfunk-Router ohne Verbindung
        name = up.get("name") or t("status.uplink.name")
        text = t("status.uplink.p_down", name=name)
        (problems if s.get("uplink_needed") else waits).append(text)
        chips.append(f'<span class="lamp {"bad" if s.get("uplink_needed") else "wait"}">{_e(name)}</span>')
    elif up:
        chips.append(f'<span class="lamp ok">{_e(up.get("name") or t("status.uplink.name"))}</span>')
    for h in s.get("network") or []:
        if not h.get("ok"):
            problems.append(t("status.net.unreachable", name=h["name"]))
        chips.append(f'<span class="lamp {"ok" if h.get("ok") else "bad"}">{_e(h["name"])}</span>')
    return "".join(chips)


def _pis(s: dict, problems: list, waits: list) -> str:
    pis = s.get("pis") or []
    many = len(pis) > 1
    rows = []
    for p in pis:
        i = p.get("info") or {}
        label = _pi_label(p["id"])
        if p.get("here") and s.get("services"):
            svc = {x["name"]: x["ok"] for x in s["services"]}
        else:
            svc = i.get("svc") or {}
        sysv = s.get("system") or {}
        temp = sysv.get("temp") if p.get("here") and sysv.get("temp") is not None else i.get("temp")
        if p.get("here") and sysv.get("disk_free") is not None:
            free = sysv["disk_free"]
        else:
            free = round(i["disk_free"] / i["disk_total"] * 100) if i.get("disk_total") else None
        down = [t(_SVC[n]) if n in _SVC else n for n, ok in svc.items() if not ok]
        cls, parts = "ok", []
        if not p.get("reachable"):
            cls, parts = "bad", [t("status.srv.unreachable")]
            problems.append(t("status.cluster.p_unreachable", name=label))
        elif down:
            cls, parts = "bad", [t("status.srv.svc_down", list=", ".join(down), count=len(down))]
            problems.append(t("status.srv.p_svc", pi=label, list=", ".join(down), count=len(down)))
        else:
            if temp is not None and temp >= 80:
                cls = "bad"
                problems.append(t("status.srv.p_hot", pi=label, temp=temp))
            elif temp is not None and temp >= 70:
                cls = "wait"
                waits.append(t("status.srv.w_warm", pi=label, temp=temp))
            if free is not None and free < 5:
                cls = "bad"
                problems.append(t("status.srv.p_disk", pi=label))
            elif free is not None and free < 15:
                cls = "wait" if cls == "ok" else cls
                waits.append(t("status.srv.w_disk", pi=label))
            if many and p.get("leader"):
                parts.append(t("status.cluster.prints"))
            if temp is not None and temp >= 70:
                parts.append(f"{temp} °C")
            if free is not None and free < 15:
                parts.append(t("status.sys.disk_free", pct=free))
        here = " " + t("status.cluster.here") if p.get("here") and many else ""
        rows.append(f'<tr><td><span class="lamp {cls}">{_e(label)}{_e(here)}</span></td>'
                    f'<td class="note {"" if cls == "ok" else cls}">{_e(" · ".join(parts))}</td></tr>')
    summary = ""
    nodes = s.get("cluster")
    if nodes:
        up, total = sum(1 for n in nodes if n.get("reachable")), len(nodes)
        leader = any(n.get("leader") for n in nodes)
        quorum = up > total / 2
        if not leader:
            problems.append(t("status.cluster.p_no_leader"))
        if not quorum:
            problems.append(t("status.cluster.p_no_quorum"))
        text = t("status.cluster.no_quorum", up=up, total=total) if not quorum else \
            t("status.cluster.electing") if not leader else \
            t("status.cluster.degraded", up=up, total=total) if up < total else t("status.cluster.all", total=total)
        summary = f'<p class="sum {"" if quorum and leader else "bad"}">{_e(text)}</p>'
    return f'<table class="list">{"".join(rows)}</table>{summary}'


def _notice(n: dict | None) -> str:
    """Herunterfahren / Neustart angekündigt (T27) – wie in common.js, nur ohne Abbrechen."""
    if not n:
        return ""
    left, off = int(n.get("left") or 0), n.get("action") == "poweroff"
    when = f"{left // 60}:{left % 60:02d}"
    if left > 0:
        text = t("common.power.off_in", time=when) if off else t("common.power.reboot_in", time=when)
    else:
        text = t("common.power.off_now") if off else t("common.power.reboot_now")
    return f'<div class="notice">{_e(text)}</div>'


def content(s: dict) -> str:
    """Nur der Inhalt (Hinweis, Kopf, Spalten, Fuss) – für den Rahmen aus render() und den Teil-Abruf (?teil=1)."""
    problems: list[str] = []
    waits: list[str] = []
    printers = _printers(s, problems, waits)
    orders = _orders(s)
    devices = _devices(s, problems, waits)
    network = _network(s, problems, waits)
    pis = _pis(s, problems, waits)
    if s.get("security"):
        problems.append(str(s["security"]))
    for p in s.get("stock") or []:
        waits.append(t("status.stock.out", name=p["name"]) if p.get("soldout") else
                     t("status.stock.low", name=p["name"], count=p.get("stock")))
    for w in s.get("warnings") or []:
        if w in _WARN:
            (problems if w in _WARN_BAD else waits).append(t(_WARN[w]))
    if s.get("einzelbetrieb"):
        problems.append(t("status.cluster.einzelbetrieb"))

    tot = s.get("totals") or {}
    pill = "bad" if problems else "wait" if waits else "ok"
    state = t("status.head.problems", count=len(problems)) if problems else \
        t("status.head.check") if waits else t("status.head.all_ok")
    detail = ", ".join(problems or waits) or (t("status.head.last_order", time=_hhmm(tot.get("last_at")))
                                              if tot.get("last_at") else t("status.head.ready_first"))
    now = time.strftime("%H:%M", time.localtime(s.get("server_time") or time.time()))
    if s.get("einzelbetrieb"):
        ver = t("status.foot.ver_emergency", version=s.get("version"))
    elif s.get("cluster"):
        ver = t("status.foot.ver_cluster", version=s.get("version"), count=len(s["cluster"]))
    else:
        ver = t("status.foot.ver_single", version=s.get("version"))
    banner = _notice(s.get("notice"))
    net_card = f'<section><h2>{_e(t("status.card.network"))}</h2><div class="chips">{network}</div></section>' if network else ""
    return f"""{banner}
<header class="{pill}">
  <div class="top"><span class="brand">FestPOS</span> <span class="event">{_e(s.get("event_name"))}</span><span class="clock">{now}</span></div>
  <div class="pill"><b>{_e(state)}</b> <span>{_e(detail)}</span></div>
</header>
<main>
  <div class="col">
    <div class="prns">{printers}</div>
    <section><h2>{_e(t("status.card.recent_orders"))}</h2>{orders}</section>
  </div>
  <div class="col">
    <section><h2>{_e(t("status.card.phones"))}</h2>{devices}</section>
    {net_card}
    <section><h2>{_e(t("status.card.cluster"))}</h2>{pis}</section>
  </div>
</main>
<footer>
  <span><b>{_e(tot.get("count") or 0)}</b> {_e(t("status.foot.orders"))}</span>
  <span><b>{_e(tot.get("last_hour") or 0)}</b> {_e(t("status.foot.last_hour"))}</span>
  <span><b>{money(tot.get("total_cents") or 0)}</b> {_e(s.get("currency") or "CHF")}</span>
  <span class="ver">{_e(ver)} · {_e(t("status.simple.updated", time=time.strftime("%H:%M:%S"), sec=REFRESH))}</span>
</footer>"""


def render(s: dict) -> str:
    """Die ganze Seite: Rahmen mit dem Inhalt in #s. data-v ist die Version – ändert sie sich (Update), lädt das
    Skript die ganze Seite neu (neues CSS und Skript)."""
    return f"""<!doctype html>
<html lang="de-CH" translate="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<noscript><meta http-equiv="refresh" content="{REFRESH}"></noscript>
<meta name="apple-mobile-web-app-capable" content="yes">
<title>{_e(t("status.head.title"))}</title>
<link rel="stylesheet" href="/status-einfach.css">
</head>
<body>
<div class="offline">{_e(t("status.simple.offline"))}</div>
<div id="s" data-sec="{REFRESH}" data-v="{_e(s.get("version"))}">
{content(s)}
</div>
<script src="/status-einfach.js"></script>
</body>
</html>"""


@router.get("/status-einfach", dependencies=[Depends(status_access)])
async def status_simple(request: Request, teil: bool = False):
    """teil=1: nur der Inhalt für status-einfach.js, mit der Version in X-FestPOS-Version."""
    local = bool(request.client and request.client.host in LOCAL_HOSTS)
    data = await db(_status_data, local, current_user(request))
    if teil:
        return HTMLResponse(content(data), headers={"Cache-Control": "no-store",
                                                    "X-FestPOS-Version": str(data.get("version") or "")})
    return HTMLResponse(render(data), headers={"Cache-Control": "no-store"})
