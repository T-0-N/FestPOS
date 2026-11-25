"""FestPOS – FastAPI-Anwendung.

Start (Entwicklung):   FESTPOS_DB=sqlite:///./festpos.db uvicorn app.main:app --reload
Start (Cluster-Knoten): siehe deploy/festpos.service (rqlite://127.0.0.1:4001, TLS auf :443)

Aufteilung (REV-053): core (Konfiguration, Anmeldung), devices (Gerätezertifikate), display (Bildschirm am Pi),
status (Statusanzeige). Verwaltung: catalog (Preisliste, Bilder, Import/Export), printers (Drucker, Druckaufträge),
tables (Tische), settings_admin (Einstellungen), maintenance (Sicherung, Zurücksetzen).
Hier: Start, Middleware, Health und Zeit, Handy-App, Bestellungen, Anmeldung, Cluster und Kennzahlen.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import os
import posixpath
import re
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import gast as gast_mod
from . import web as web_mod
from . import bon_design
from . import plan as plan_mod
from . import tabs as tabs_mod
from . import tips as tips_mod
from . import auth, catalog, certs, cluster as cluster_mod, device_ids, devices, dhcp as dhcp_mod, display, fully, fully_apk, i18n, ledger, \
    cards, mac_change, maintenance, options as options_mod, payrexx, print_service as ps, printer_net, printers, receipts, settings_admin, \
    status as status_mod, status_simple, \
    stock as stock_mod, system as system_mod, tables, test_mode, time_admin, tunnel, update as update_mod, update_net, uplink_admin, \
    restore_check, secretbox, sicherheit, timesync, twofa, users as users_mod, firewall, saison
from . import release as release_mod
from . import krypt as krypt_mod
from . import umzug as umzug_mod
from . import ledger_base as lb
from .core import (APP_VERSION, LOGIN_MAX_FAILS, LOGIN_WINDOW_S, PW_WARN_LEN, Config, RequestGate, Throttle,  # noqa: F401
                   admin, admin_allowed_from, cluster_info, db, current_user, emergency_problem,
                   security_warnings as core_warnings, idle_seconds, issue_session, log, login_name,
                   login_throttle, login_user, logo_info, rt, admin_cert_needed)
from .devices import revoked_serials  # noqa: F401 – für Tests und Werkzeuge
from .display import _display_cfg, _display_sync, _display_update  # noqa: F401
from .i18n import t
from .orders import OrderError, create_order, delivery_times, may_nachbuchen
from .status import _status_data  # noqa: F401
from .store import Store, StoreError, StoreUnavailable, open_store

store: Store
spooler: ps.PrintSpooler
monitor = rt.monitor


def _tick() -> None:
    """Zusatz pro Durchlauf des Monitors: Bildschirm/Pi-Infos (system), MAC der Handys lernen, bei einem MAC-Wechsel
    nachführen, DHCP-Zuteilungen und Drucker-Netz schreiben (devices, mac_change, dhcp, printer_net)."""
    system_mod.tick()
    devices.learn_macs()                         # feste IP ohne MAC: MAC vom Handy lernen, dann reservieren
    mac_change.tick()                            # neue MAC bei bekanntem Zertifikat: Reservierung umschreiben (T4)
    dhcp_mod.sync()
    dhcp_mod.gateway_sync()                              # Gateway der Handhelds folgt dem Weg ins Internet
    try:                                         # Drucker im eigenen VLAN: festpos-druckernetz richtet es ein
        printer_net.sync()
    except Exception:  # noqa: BLE001 – z.B. Datenbank kurz weg: die Aufgaben danach sollen trotzdem laufen
        log.exception("Drucker-Netz nicht abgeglichen")
    time_admin.tick()                            # Uhrzeit: von einem anderen Pi holen, Internet, USB (T2)
    fully_apk.sync_tick()                        # Fully-APK von einem anderen Pi holen, wenn sie hier fehlt (T6)
    _payrexx_tick()                              # offene Payrexx-Zahlungen nach 24 h abschliessen (T13)
    _keep_tick()                                 # Internet dauerhaft (Seite «Internet», Schalter c) – jeder Pi
    _tunnel_tick()                               # Tunnel zur QR-Bestellung nach der Verwaltung einrichten – jeder Pi
    _secrets_tick()                              # Geheimnisse verschlüsseln, sobald alle Pi diese Version haben (#8)
    try:                                         # Netz umziehen (#2): vorbereiten, umstellen, bestätigen – jeder Pi
        umzug_mod.tick()
    except Exception:  # noqa: BLE001 – Monitor darf nie stehen bleiben
        log.exception("Netz umziehen: Takt gescheitert")


_payrexx_last = [0.0]
_secrets_last = [0.0]


def all_pis_current() -> bool:
    """Laufen alle Pi mindestens diese Version (laut ihren Infos in der Datenbank)? Einzelbetrieb: ja.
    FESTPOS_PEERS nennt alle Pi (auch diesen): im Cluster a, b, c – jeder braucht einen Eintrag mit aktueller Version;
    Einträge anderer Buchstaben (z.B. von einem früheren Versuch) zählen nicht."""
    if len(Config.peers) <= 1:
        return True
    expected = ("a", "b", "c")[:len(Config.peers)]
    for node in expected:
        try:
            v = json.loads(store.scalar("SELECT value FROM settings WHERE key=?", (f"node_info.{node}",)) or "{}") \
                .get("version")
        except (ValueError, AttributeError):
            return False
        if not v or release_mod.newer(APP_VERSION, v):
            return False
    return True


_current = {"at": 0.0, "ok": False}


def _pis_current_cached() -> bool:
    """all_pis_current für jedes Schreiben eines Geheimnisses (secretbox.gate). Nur «nein» gilt eine Minute weiter
    (vorsichtig: im Klartext wie bisher); «ja» wird jedes Mal frisch geprüft – meldet ein Pi inzwischen eine ältere
    Version, wird nicht mehr versiegelt."""
    if not _current["ok"] and time.time() - _current["at"] <= 60:
        return False
    try:
        _current["ok"] = all_pis_current()
    except Exception:                            # noqa: BLE001 – im Zweifel wie bisher im Klartext
        _current["ok"] = False
    _current["at"] = time.time()
    return _current["ok"]


secretbox.gate(_pis_current_cached)


def _secrets_tick() -> None:
    """Alle 10 Minuten: Geheimnisse, die noch im Klartext (oder im alten Verfahren) in der Datenbank stehen,
    verschlüsseln – erst wenn alle Pi diese Version haben (ein älterer Pi läse sonst den verschlüsselten Text)."""
    if time.time() - _secrets_last[0] < 600:
        return
    _secrets_last[0] = time.time()
    try:
        if all_pis_current() and (n := secretbox.migrate(store)):
            log.info("Geheimnisse: %d verschlüsselt", n)
    except Exception:                            # noqa: BLE001 – Monitor darf nie stehen bleiben
        log.exception("Geheimnisse verschlüsseln gescheitert")
_keep_last = [0.0]
_tunnel_last = [0.0]


def _tunnel_tick() -> None:
    """Jede Minute, jeder Pi: Token der Verwaltung einrichten oder Tunnel aus (tunnel.tick, T22)."""
    if time.time() - _tunnel_last[0] < 60:
        return
    _tunnel_last[0] = time.time()
    try:
        tunnel.tick()
    except Exception:                            # noqa: BLE001 – Monitor darf nie stehen bleiben
        log.exception("QR-Bestellung: Tunnel im Hintergrund gescheitert")


def _keep_tick() -> None:
    """Jede Minute, jeder Pi: Internet nach Schalter (c) halten oder abbauen (update_net.keep_tick)."""
    if time.time() - _keep_last[0] < 60:
        return
    _keep_last[0] = time.time()
    try:
        update_net.keep_tick()
    except Exception:                            # noqa: BLE001 – Monitor darf nie stehen bleiben
        log.exception("Internet dauerhaft: Hintergrund gescheitert")


def _payrexx_tick() -> None:
    """Jede Minute, nur der druckende Pi: offene Payrexx-Zahlungen nach 24 h abschliessen (T13)."""
    if not spooler.is_leader or time.time() - _payrexx_last[0] < 60:
        return
    _payrexx_last[0] = time.time()
    try:
        n = payrexx.expire_open()
        if n:
            log.info("Payrexx: %d offene Zahlung(en) nach 24 h als gescheitert markiert", n)
    except Exception:                            # noqa: BLE001 – Monitor darf nie stehen bleiben
        log.exception("Payrexx: Prüfen der offenen Zahlungen gescheitert")
    try:
        gast_mod.tick()                          # T22: QR-Bestellung – offene Zahlungen, nicht Gedrucktes zurück
    except Exception:                            # noqa: BLE001
        log.exception("QR-Bestellung: Hintergrund gescheitert")



def _ask_peers() -> dict:
    return timesync.ask_peers("/api/internal/node", Config.peers, Config.cluster_secret, Config.cafile, Config.node_ip)


async def _start_services() -> None:
    try:                                                     # T8: Sicherungs-Passwort bei der Einrichtung, Kennungen
        await asyncio.to_thread(maintenance.ensure_password)
        await asyncio.to_thread(maintenance.publish_system_ids)
    except StoreError as e:                                  # z.B. noch keine Mehrheit: beim nächsten Start
        log.warning("Sicherungs-Passwort/Kennungen nicht geschrieben: %s", e)
    try:
        await asyncio.to_thread(status_mod.printer_state_forget)      # alter Druckerstand in der Datenbank (T9)
    except StoreError as e:
        log.warning("Alter Druckerstand nicht gelöscht: %s", e)
    try:                                                     # T6: APK von vor T6 gilt für alle Pi (einmal)
        await asyncio.to_thread(fully_apk.adopt_local)
    except (StoreError, OSError) as e:
        log.warning("Fully-APK nicht übernommen: %s", e)
    await spooler.start()
    monitor.start(status_mod._monitor_targets, status_mod._monitor_printers, cluster=len(Config.peers) > 1,
                  extra=_tick, printer_ok=spooler.mark_ok,
                  printer_seen=lambda pid: ps.mark_status_seen(rt.store, pid),
                  fully_cfg=fully.config, fully_here=lambda: spooler.is_leader,    # Fully: nur der Pi, der druckt
                  uplink_cfg=uplink_admin.config,                                   # Mobilfunk-Router: jeder Pi
                  cluster_info=lambda: rt.store.cluster_info(),                     # Zustand der Pi (Zwischenstand)
                  printers_here=lambda: spooler.is_leader,                          # Drucker: nur der Pi, der druckt
                  # die anderen Pi direkt fragen: Infos und Drucker, nicht über die Datenbank (T9)
                  peers=_ask_peers if len(Config.peers) > 1 else None,
                  printers_legacy=status_mod.printer_state_get)
    update_mod.start()                                       # Updates: bin ich an der Reihe? (update.py)
    sicherheit.start()                                       # Sicherheitsereignisse ins Protokoll (#11)
    firewall.start()                                         # Modus der Firewall an festpos-firewall (#10)
    rt.ready = True
    log.info("Datenbank bereit – FestPOS läuft")
    asyncio.create_task(maintenance.after_setup())           # T11: Sprache, Sicherung aus der Ersteinrichtung


async def _wait_for_db_then_start() -> None:
    """Beim Booten startet rqlited evtl. später (es fragt zuerst die anderen Pi, siehe festpos-rqlited).
    Die App antwortet in der Zwischenzeit schon auf /api/health… – damit die anderen Pi sie fragen können."""
    n = 0
    while True:
        try:
            await asyncio.to_thread(store.init_schema)
            break
        except Exception as e:  # noqa: BLE001 – weiter warten, nie still aufgeben
            if n % 10 == 0:
                log.info("Warte auf Datenbank (%s)…", e, exc_info=not isinstance(e, StoreError))
            n += 1
            await asyncio.sleep(1)
    try:
        await _start_services()
    except Exception:  # noqa: BLE001
        log.critical("Start nach dem Warten auf die Datenbank gescheitert – Dienst wird neu gestartet", exc_info=True)
        os._exit(3)                                         # systemd startet neu (Restart=always)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store, spooler
    store = rt.store = open_store(Config.db)
    spooler = rt.spooler = ps.PrintSpooler(store, Config.node, idle_poll=2.0)
    i18n.set_default_loader(lambda: store.setting("language", i18n.SOURCE))       # Standardsprache (Verwaltung)
    auth.set_loader(users_mod.load_users)                                         # Benutzer der Verwaltung
    auth.set_roles_loader(users_mod.load_roles)                                   # eigene Rollen (Rechte)
    rt.ready = False
    if emergency_problem() == "placeholder":
        log.warning("Notfall-Passwort (ADMIN_PASSWORD) ist ein Platzhalter – Notfall-Login gesperrt, in festpos.conf ändern!")
    elif emergency_problem() == "short":
        log.warning("Notfall-Passwort ist kürzer als %d Zeichen – in festpos.conf ein längeres als ADMIN_PASSWORD "
                    "eintragen (gesperrt, sobald es einen eigenen Admin-Benutzer gibt)", PW_WARN_LEN)
    waiter = None
    try:
        await asyncio.to_thread(store.init_schema)          # Datenbank schon da (Neustart der App, Tests)
    except StoreError:
        waiter = asyncio.create_task(_wait_for_db_then_start(), name="wait-db")
    else:
        await _start_services()
    yield
    if waiter and not waiter.done():
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
    await update_mod.stop()
    await sicherheit.stop()
    await firewall.stop()
    await monitor.stop()
    await spooler.stop()


# API-Beschreibung (/api/docs) nur auf Wunsch: FESTPOS_API_DOCS=1 (REV-041)
_DOCS = os.environ.get("FESTPOS_API_DOCS") == "1"
app = FastAPI(title="FestPOS", lifespan=lifespan, docs_url="/api/docs" if _DOCS else None,
              openapi_url="/api/openapi.json" if _DOCS else None, redoc_url=None)


# Auf dem Verwaltungs-Port (8443, ohne Gerätezertifikat) nur die Verwaltung ausliefern –
# die Bestell-App und ihre Daten gibt es ausschliesslich auf dem Handy-Port.
ADMIN_PORT_PATHS = ("/admin", "/status", "/api/status", "/app.css", "/common.js", "/i18n", "/fonts/", "/icons/", "/media/", "/api/logo", "/api/nav",
                    "/api/admin/", "/api/health", "/api/internal/", "/api/device-cert/", "/api/docs", "/api/openapi.json",
                    "/api/tap/",      # T13: Ergebnis von «FestPOS Tap» (Einmal-Schlüssel, ohne Gerätezertifikat)
                    "/api/gast/")     # T22: QR-Bestellung, nur vom Gästedienst (eigener Schlüssel)
# ADMIN_FROM (festpos.conf): von anderen Adressen auf dem Verwaltungs-Port nur noch das hier – Zertifikat für ein
# neues Handy (Link im QR-Code), was die Pi untereinander brauchen (geschützt mit CLUSTER_SECRET) und das Ergebnis
# von «FestPOS Tap» vom Handy (T13, geschützt mit dem Einmal-Schlüssel der Zahlung)
ADMIN_FROM_OPEN = ("/api/device-cert/", "/api/health", "/api/internal/", "/api/tap/", "/api/gast/")


@functools.lru_cache(maxsize=256)
def _cert_id(der: bytes) -> tuple[str | None, str | None]:
    """Seriennummer und Gerätename aus dem Zertifikat (pro Zertifikat nur einmal ausgelesen)."""
    try:
        info = certs.describe(der)
    except Exception:  # noqa: BLE001
        return None, None
    return info["serial"], info["name"]


@app.middleware("http")
async def device_heartbeat(request: Request, call_next):
    """Jede Anfrage der Bestell-App zählt als Lebenszeichen des Handys (für die Statusanzeige), pro Gerät: mit
    Zertifikat dessen Seriennummer, sonst IP und App-Name (T1). Mit Gerätezertifikaten zählen nur Anfragen MIT Zertifikat – sonst könnte jeder im Netz mit einem
    erfundenen Header beliebig viele «Handys» anlegen (REV-036). Die Liste ist zudem begrenzt (monitor)."""
    name = request.headers.get("X-Festpos-Device")
    der = request.scope.get("extensions", {}).get("festpos.client_cert")
    if name is not None and request.url.path.startswith("/api/") and (der or not Config.phone_port):
        from urllib.parse import unquote
        name = unquote(name)
        serial, cert_name = _cert_id(der) if der else (None, None)   # das Gerät erkennt man am Zertifikat (T1)
        ip = request.client.host if request.client else None
        monitor.seen(name if (name.strip() or serial) else (ip or ""), ip,
                     request.headers.get("X-Festpos-Outbox"), request.headers.get("X-Festpos-Version"),
                     request.headers.get("X-Festpos-Test") == "1",
                     request.headers.get("X-Festpos-Battery"), request.headers.get("X-Festpos-Charging"),
                     serial=serial, cert_name=cert_name)
    return await call_next(request)


@app.middleware("http")
async def revoked_gate(request: Request, call_next):
    """Gesperrtes Gerätezertifikat (T3): an einer Stelle für alle Routen der App abweisen – vor dem Lebenszeichen,
    ein gesperrtes Handy erscheint also auch nicht als online. Die Prüfungen in den Routen bleiben als zweite Sicherung."""
    if rt.ready and devices.revoke_checked(request) and await db(devices.revoked_serials):
        info = devices.client_cert(request)
        if info and info["serial"] in await db(devices.revoked_serials):
            return JSONResponse({"detail": await db(devices.revoked_message, info["serial"])}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def no_cloudflare(request: Request, call_next):
    """T22: Über den Cloudflare Tunnel darf nur der Gästedienst erreichbar sein; er reicht keine Cloudflare-Header weiter.
    Kommt trotzdem eine solche Anfrage hier an, zeigt der Tunnel falsch (z.B. auf Port 8443) – dann alles sperren."""
    if "cf-ray" in request.headers or "cf-connecting-ip" in request.headers:
        log.warning("Anfrage über Cloudflare direkt an FestPOS (%s) – Tunnel muss auf localhost:8090 zeigen",
                    request.url.path[:60])
        return JSONResponse({"detail": t("err.gast.forbidden")}, status_code=403)
    return await call_next(request)


_BAD_PATH = re.compile(r"[?#%\x00-\x1f\x7f]")


@app.middleware("http")
async def admin_port_guard(request: Request, call_next):
    # Der Pfad, nach dem auch das Routing geht (scope["path"], schon dekodiert). request.url.path taugt dafür nicht:
    # es schneidet bei «?» und «#» ab und entfernt Zeilenumbrüche – «/admin%3F/../app/order.js» sah hier wie «/admin»
    # aus und lieferte doch die Bestell-App (Review 2026-11, S8).
    raw = request.scope.get("path") or request.url.path
    # «..» und doppelte Schrägstriche nie zulassen: sonst lieferte /admin/../app.js die App auf dem
    # Verwaltungs-Port aus, weil nur der Anfang des Pfads geprüft wird (REV-041). Ebenso «?», «#», «%» (doppelt
    # kodiert) und Steuerzeichen im dekodierten Pfad: keine Adresse von FestPOS enthält sie.
    if "/.." in raw or "//" in raw or "\\" in raw or _BAD_PATH.search(raw):
        return JSONResponse({"detail": t("err.path.invalid")}, status_code=404)
    if Config.phone_port and request.scope.get("server", (None, 0))[1] != Config.phone_port:
        path = posixpath.normpath(raw) if raw != "/" else raw
        if not admin_allowed_from(request.client.host if request.client else None) and \
                not path.startswith(ADMIN_FROM_OPEN):
            return JSONResponse({"detail": t("err.path.admin_from")}, status_code=403)
        if path in ("/", "/index.html"):
            return RedirectResponse("/admin.html", status_code=302)
        if not path.startswith(ADMIN_PORT_PATHS):
            return JSONResponse({"detail": t("err.path.admin_only")}, status_code=404)
    return await call_next(request)


@app.middleware("http")
async def admin_cert_gate(request: Request, call_next):
    """«Verwaltung nur mit Gerätezertifikat»: ohne gültiges Zertifikat keine Verwaltung (auch keine
    Anmeldung) auf dem Verwaltungs-Port. Die Seite admin.html selbst lädt und zeigt dann diese Meldung."""
    path = request.scope.get("path") or request.url.path
    if rt.ready and path.startswith("/api/admin/") and await asyncio.to_thread(admin_cert_needed, request):
        return JSONResponse({"detail": t("err.login.cert_required")}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def not_ready_yet(request: Request, call_next):
    """Bis die Datenbank bereit ist, beantworten nur /api/health… Anfragen (ehrliche 503 statt Fehlern)."""
    path = request.url.path
    if not rt.ready and path.startswith("/api/") and not path.startswith(("/api/health", "/api/internal/settime", "/api/internal/time")):
        return JSONResponse({"detail": t("err.server.starting")}, status_code=503, headers={"Retry-After": "5"})
    return await call_next(request)


@app.middleware("http")
async def no_stale_app_files(request: Request, call_next):
    """App-Dateien (index.html, app.js, sw.js …) immer beim Server nachfragen lassen: ohne Cache-Control
    darf der Browser sie heuristisch stundenlang als frisch behandeln – ein Update käme verspätet an (REV-021).
    Produktbilder setzen selbst «immutable», Schriften und Symbole ändern sich nicht."""
    response = await call_next(request)
    path = request.url.path
    if request.method == "GET" and "cache-control" not in response.headers and \
            not path.startswith(("/api/", "/media/", "/fonts/", "/icons/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Content-Security-Policy: Seiten dürfen nur eigene Skripte laden (keine eingeschleusten, keine fremden Server)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if response.headers.get("content-type", "").startswith("text/html") and not request.url.path.startswith("/api/docs"):
        response.headers.setdefault("Content-Security-Policy", CSP)        # (API-Doku nur mit FESTPOS_API_DOCS=1)
    return response


# Ganz aussen: Verwaltung ohne Anmeldung sofort abweisen, zu grosse Anfragen ablehnen (REV-037)
app.add_middleware(RequestGate)


def _details_ok(request: Request) -> bool:
    """Den Wortlaut eines Datenbankfehlers bekommt nur die angemeldete Verwaltung (zum Nachschauen), nie ein Handy
    oder ein Gast (Review 2026-11, S12): er nennt Tabellen, Spalten und interne Adressen."""
    return request.scope.get("path", "").startswith("/api/admin/") and current_user(request) is not None


@app.exception_handler(StoreUnavailable)
async def _unavailable(request: Request, exc: StoreUnavailable):
    log.warning("Datenbank nicht erreichbar: %s", exc)
    body = {"detail": t("err.server.db_unavailable")}
    if _details_ok(request):
        body["error"] = str(exc)
    return JSONResponse(body, status_code=503)


# Doppelte Namen: verständlich statt «UNIQUE constraint failed: products.name» (Tabelle.Spalte -> Text)
UNIQUE_TEXT = {"products.name": "err.product.exists", "categories.name": "err.category.exists",
               "printers.name": "err.printer.exists", "tables.label": "err.tables.exists",
               "plan_areas.name": "err.plan.exists", "users.name": "err.users.exists_any"}


def store_error_text(exc: StoreError, details: bool = True) -> str:
    msg = str(exc)
    m = re.search(r"UNIQUE constraint failed: (\w+\.\w+)", msg)
    if m:
        return t(UNIQUE_TEXT.get(m.group(1), "err.common.exists"))
    if "FOREIGN KEY constraint failed" in msg:
        return t("err.common.reference")
    return t("err.common.store_failed", error=msg[:200]) if details else t("err.common.store_failed_short")


@app.exception_handler(StoreError)
async def _store_error(request: Request, exc: StoreError):
    status = 409 if exc.is_unique_violation else 400
    log.warning("Datenbank lehnt ab: %s", exc)
    return JSONResponse({"detail": store_error_text(exc, _details_ok(request))}, status_code=status)


# ===========================================================================
# Health (für keepalived) & Zeit
# ===========================================================================

@app.get("/api/health")
async def health():
    leader = await db(store.is_leader)
    return {"node": Config.node, "leader": leader, "printing": spooler.is_leader, "time": time.time(),
            "version": APP_VERSION, "ready": rt.ready}


@app.get("/api/health/leader")
async def health_leader():
    """keepalived track_script: 200 nur auf dem Knoten, der Raft-Leader ist UND druckt."""
    if spooler.is_leader and await db(store.is_leader):
        return {"leader": Config.node}
    raise HTTPException(503, t("err.server.not_leader"))


@app.get("/api/health/cluster")
async def health_cluster():
    """Für festpos-rqlited auf den anderen Pi: Leader und Mitglieder aus Sicht dieses Knotens.
    Ein zurückkehrender Pi erkennt daran, dass er nicht mehr dazugehört (z.B. nach Einzelbetrieb)."""
    if store.kind != "rqlite":
        return {"node": Config.node, "state": "Leader", "leader": Config.node, "members": [Config.node],
                "einzelbetrieb": False}
    try:
        status = await db(store.status)
    except StoreError:
        status = None
    return cluster_mod.summarize(status, Config.node, Config.rq_data)


def pin_config(request: Request | None = None) -> dict:
    """PIN-Sperre für die App (prüft offline gegen den Hash). Nur über den Handy-Port ausgeliefert.
    T14: Hat dieses Gerät eine eigene PIN, bekommt es nur deren Hash (nie die der anderen Geräte); Zeitsperre und
    «sperren, wenn der Bildschirm aus war» bleiben gemeinsam."""
    info = devices.client_cert(request) if request is not None else None
    own = devices.own_pin(device_ids.device_id_of(store, info["serial"])) if info else None
    if own:
        return {"enabled": True, "own": True, "salt": own["salt"], "hash": own["hash"], "length": own["len"],
                "idle_minutes": int(store.setting("pin_idle_minutes", "0") or 0),
                "on_screen_off": store.setting("pin_on_screen_off", "0") == "1"}
    on = store.setting("pin_enabled", "0") == "1" and bool(store.setting("pin_hash"))
    return {"enabled": on, "salt": store.setting("pin_salt") if on else "", "hash": store.setting("pin_hash") if on else "",
            "idle_minutes": int(store.setting("pin_idle_minutes", "0") or 0),
            "on_screen_off": store.setting("pin_on_screen_off", "0") == "1",
            "length": int(store.setting("pin_len", "0") or 0) if on else 0}


def test_config() -> dict:
    """Testmodus (Üben): PIN wie bei der Sperre nur als gesalzener Hash; prüft das Handy auch ohne Verbindung.
    Ohne «Test-PIN verlangen» (T7) startet er am Handy ohne PIN (pin: false)."""
    need = store.setting("test_pin_required", "1") != "0"
    on = store.setting("test_enabled", "0") == "1" and (bool(store.setting("test_pin_hash")) or not need)
    pin = on and need
    return {"enabled": on, "pin": pin, "salt": store.setting("test_pin_salt") if pin else "",
            "hash": store.setting("test_pin_hash") if pin else "",
            "length": int(store.setting("test_pin_len", "0") or 0) if pin else 0,
            "print": store.setting("test_print", "0") == "1"}


def _setting_choice(key: str, allowed: tuple[str, ...], default: str) -> str:
    v = store.setting(key, default)
    return v if v in allowed else default


# ===========================================================================
# Kellner-App
# ===========================================================================

def tables_mode() -> str:
    from .orders import tables_mode as tm
    return tm(store)


@app.get("/api/bootstrap")
async def bootstrap(request: Request):
    """Alles, was das Handy braucht, in EINEM Request. Gesperrte Geräte bekommen nichts (auch keinen PIN-Hash)."""
    info = devices.client_cert(request)
    if info and info["serial"] in await db(devices.revoked_serials):
        raise HTTPException(403, await db(devices.revoked_message, info["serial"]))

    def load():
        return {
            "event_name": store.setting("event_name"),
            "currency": store.setting("currency", "CHF"),
            "categories": store.query("SELECT id, name, color, sort FROM categories ORDER BY sort, name"),
            "products": [{**p, "options": options_mod.loads(p["options"])} for p in store.query(
                "SELECT id, name, price_cents, category_id, single_ticket, image_id, sort, options, color, delivery, "
                "delivery_allowed, delivery_required, soldout, stock, stock_warn FROM products WHERE active=1 "
                "ORDER BY sort, name")],
            "tables": [r["label"] for r in store.query("SELECT label FROM tables ORDER BY sort, label")],
            "show_history": store.setting("show_history", "1") == "1",
            "show_recent_tables": store.setting("show_recent_tables", "1") == "1",
            "table_word": store.setting("table_word", "Tisch"),
            "tables_mode": tables_mode(),
            "plan": plan_mod.plan_for_phone(store),               # Tischplan (None = Tischwahl wie früher)
            "tabs": tabs_mod.config(store),                       # offene Tische: Modus, wer abrechnet, Hinweis nach Minuten
            "delivery_times": delivery_times(store),
            "pin": pin_config(request) if devices.via_phone(request) else None,
            "payrexx": payrexx.ready() if devices.via_phone(request) else False,   # T13: Zahlart anbieten
            "test": test_config() if devices.via_phone(request) else None,
            "product_images": _setting_choice("product_images", ("none", "small", "large"), "small"),
            **logo_info(store),
            "options_outside": _setting_choice("options_outside", ("close", "warn", "ignore"), "warn"),
            "swipe_categories": store.setting("swipe_categories", "1") == "1",
            "staff_qr": store.setting("staff_qr", "0") == "1",     # QR fürs Personal (Standard aus)
            "tutorial_auto": store.setting("tutorial_auto", "1") == "1",
            "language": i18n.default_lang(),
            "languages": i18n.available(),
            "cert": devices.cert_status(request),
            "payment_mode": ledger.payment_mode(store),
            "stock_check": stock_mod.check_config(store),          # vor dem Kassieren auf den Server warten
            "can_storno": bool(info) and lb.device_role(store, info) in lb.STORNO_ROLES,
            "can_soldout": bool(info) and lb.device_role(store, info) == "lead",   # Ausverkauft markieren
            "can_kiosk": bool(info) and lb.device_role(store, info) == "lead",     # Fully: Kiosk entsperren, Android-Einstellungen
            "status_only": bool(info) and lb.device_role(store, info) == "status",  # Rolle «Nur Statusanzeige»
            "can_nachbuchen": may_nachbuchen(store, info),   # «Nachbuchen ohne Bon» (schon geliefert)
            "receipt": receipts.enabled(store),               # Quittungen eingerichtet (Drucker gewählt)
            "tips": tips_mod.enabled(store),                  # Trinkgeld an der Kasse erfassen (app/tips.py)
            "version": APP_VERSION,
            "server_time": time.time(),
        }
    return await db(load)


def phone_printers() -> list[dict]:
    """Drucker für die Statuszeile der Handys: ok | rerouted (Bons gehen auf einen anderen Drucker) | down (Bons
    warten). Die Handys sprechen mit dem druckenden Pi (die Adresse .10 folgt dem Leader), der weiss es genau."""
    rows = ps.queue_status_cached(store)
    by_id = {r["id"]: r for r in rows}
    out = []
    for r in rows:
        if not r["enabled"]:
            continue
        chk = (rt.monitor.printers.get(r["id"]) if rt.monitor else None) or {}
        code = spooler.down_code(r["id"])
        if not code:
            code = ("paper_out" if chk.get("paper_out") else "cover_open" if chk.get("cover_open")
                    else "no_status" if chk.get("no_status")
                    else "unreachable" if chk.get("online") is False else None)
        if not code and r["pending"] and r.get("last_error_code") not in (None, "rerouted"):
            code = r.get("last_error_code") or "unreachable"
        state, target = "ok", None
        if code:
            tgt = by_id.get(r.get("fail_target") or 0)
            if r.get("fail_mode") == "reroute" and tgt and tgt["id"] != r["id"] and tgt["enabled"] \
                    and not spooler.is_down(tgt["id"]):
                state, target = "rerouted", tgt["name"]
            else:
                state = "down"
        out.append({"name": r["name"], "state": state, "code": code, "target": target,
                    "waiting": int(r["pending"] or 0) + int(r["printing"] or 0),
                    # Papier fast leer (Sensor im Drucker): die App zeigt es nur Handys mit Rolle Festleitung
                    "paper_low": bool(chk.get("paper_near_end")) and not chk.get("paper_out")})
    return out


def _uplink_for_phone() -> dict | None:
    up = status_mod.uplink_issue()
    if up and up["needed"]:
        return {"name": up["name"]}
    return {"kind": "internet"} if update_net.phone_issue() else None      # WLAN/USB des Pi ohne Internet


@app.get("/api/phone/status")
async def phone_status(request: Request):
    """Statuszeile der Bestell-App (alle paar Sekunden): Drucker, Quittungen eingerichtet, Uhrzeit des Servers,
    ausverkaufte Produkte und Restmengen; test_exit, wenn die Statusanzeige den Testmodus beendet hat (T7)."""
    info = devices.client_cert(request)
    if info and info["serial"] in await db(devices.revoked_serials):
        raise HTTPException(403, await db(devices.revoked_message, info["serial"]))

    def load():
        return {"printers": phone_printers(), "receipt": receipts.enabled(store), "server_time": time.time(),
                "stock": stock_mod.availability(store),             # ausverkauft / Restmengen: Kacheln sofort grau
                "test_print": store.setting("test_print", "0") == "1",  # Text im Testmodus-Balken (T19)
                "notice": system_mod.phone_notice(),                 # Herunterfahren/Neustart angekündigt (T27, T32)
                "uplink": _uplink_for_phone(),                       # Router ohne Verbindung, nur wenn gebraucht
                "payrexx_double": payrexx.doubles_for(store, info),  # zweite Kartenbelastung (T44): Hinweis
                **test_mode.phone_status_extra(request)}
    return await db(load)


@app.get("/i18n/{name}.json", include_in_schema=False)
async def i18n_catalog(name: str, request: Request):
    """Sprachdatei für die Browser-Seiten: «auto» = Standardsprache aus der Verwaltung. Fehlende Texte
    sind aus der Ausgangssprache (de) ergänzt. Mit ETag: unverändert -> 304."""
    if name != "auto" and not i18n.CODE_RE.match(name):
        raise HTTPException(404)
    if name != "auto" and not i18n.normalize(name):
        raise HTTPException(404, t("err.i18n.unknown", lang=name))
    cat = await db(i18n.catalog, None if name == "auto" else name)    # liest evtl. die Einstellung (Datenbank)
    body = json.dumps(cat, ensure_ascii=False, separators=(",", ":")).encode()
    etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json; charset=utf-8", headers=headers)


@app.get("/media/{image_id}")
async def media(image_id: str):
    row = await db(store.query_one, "SELECT mime, data FROM images WHERE id=?", (image_id,))
    if not row:
        raise HTTPException(404)
    return Response(base64.b64decode(row["data"]), media_type=row["mime"],
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


class OrderLine(BaseModel):
    product_id: int
    qty: int = Field(gt=0, le=999)
    options: list[str] = Field(default_factory=list, max_length=128)   # gewählte Optionen
    note: str | None = Field(default=None, max_length=200)              # Freitext
    when: str | None = Field(default=None, max_length=40)               # Lieferzeit (Pause, Schluss …)


class OrderIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    table: str = Field(min_length=1, max_length=40)
    device: str | None = Field(default=None, max_length=40)
    lines: list[OrderLine] = Field(min_length=1, max_length=200)
    test: bool = False                                    # Testmodus der App (Übung)
    payment: str | None = Field(default=None, pattern=r"^(cash|card|twint|other)$")   # schon an der Kasse gewählt
    no_print: bool = False                                # «Nachbuchen ohne Bon»: war schon geliefert, keine Bons


@app.post("/api/orders")
async def post_order(o: OrderIn, request: Request, _: None = Depends(devices.phone_only)):
    name = o.device
    cert = devices.client_cert(request)                  # ausgerolltes Gerät (z.B. XCover101), neben dem App-Namen
    print_test = True
    if o.test:
        cfg = await db(test_config)
        if not cfg["enabled"]:
            raise HTTPException(422, t("err.order.test_off"))
        print_test = cfg["print"]
    if cert and await db(lb.device_role, store, cert) == "status":           # «Nur Statusanzeige»: nie bestellen
        raise HTTPException(403, t("err.order.status_only"))
    async def rejected(msg: str) -> None:
        """T42: jede Ablehnung auch im Protokoll – die Festleitung sieht sie, selbst wenn das Handy ausfällt."""
        try:
            await db(lb.audit, store, f"device:{lb.device_label(cert['name'] if cert else None, name) or '?'}",
                     "order_rejected", uuid=o.client_uuid, table=o.table, reason=msg[:200], test=o.test,
                     lines=sum(x.qty for x in o.lines))
        except Exception:  # noqa: BLE001 – das Protokoll darf die Antwort (422) nicht ändern
            log.warning("Ablehnung nicht protokolliert (%s)", o.client_uuid, exc_info=True)

    if o.no_print and not await db(may_nachbuchen, store, cert):
        await rejected(t("err.order.nachbuchen_forbidden"))
        raise HTTPException(422, t("err.order.nachbuchen_forbidden"))   # 422: landet bei «abgelehnt», blockiert nichts
    try:
        res = await db(create_order, store, client_uuid=o.client_uuid, table_label=o.table,
                       lines=[l.model_dump() for l in o.lines], device=name, test=o.test, print_test=print_test,
                       payment=o.payment, device_cert=cert["name"] if cert else None,
                       tab_owner=await db(tabs_mod.owner_of, store, cert), no_print=o.no_print)
    except OrderError as e:
        await rejected(str(e))
        if e.stock:                                    # ausverkauft / nicht genug: alle Produkte und der neue Stand
            return JSONResponse({"detail": str(e), "stock_problems": e.stock,
                                 "stock": await db(stock_mod.availability, store)}, status_code=422)
        raise HTTPException(422, str(e)) from None
    for pid in res.printer_ids:
        spooler.notify(pid)
    return {"order_id": res.order_id, "table": res.table_label, "total_cents": res.total_cents,
            "created_at": res.created_at, "duplicate": res.duplicate, "warnings": res.warnings}


# ===========================================================================
# Verwaltung: Anmeldung, Cluster, Kennzahlen (die Seiten der Verwaltung: siehe oben)
# ===========================================================================

adm = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


class LoginIn(BaseModel):
    name: str = Field(default="", max_length=60)        # leer oder «admin» = Notfall-Login (festpos.conf)
    password: str = Field(max_length=256)


@app.post("/api/admin/login")
async def admin_login(body: LoginIn, request: Request):
    """Name + Passwort einmal prüfen, Sitzungs-Token zurück (die Verwaltung speichert nur das Token)."""
    ip = request.client.host if request.client else "?"
    login_throttle.check(ip)
    name = login_name(body.name)
    login_throttle.check_name(ip, name)                  # zu viele Fehlversuche für diesen Namen (alle Adressen, S6)
    user = await asyncio.to_thread(login_user, body.name, body.password, ip)     # scrypt: nicht im Event-Loop
    if not user:
        login_throttle.fail(ip, name)
        if name == auth.EMERGENCY_NAME:
            from . import emergency
            if not emergency.local_ok(ip):
                raise HTTPException(401, t("err.login.emergency_remote"))
            problem = emergency_problem()
            if problem == "placeholder":
                raise HTTPException(401, t("err.login.emergency_placeholder"))
            if problem == "short_locked":
                raise HTTPException(401, t("err.login.emergency_short"))
        raise HTTPException(401, t("err.login.wrong"))
    if user.get("totp_on"):                              # zweiter Faktor: zweiter Schritt (T-0-N/FestPOS#9)
        from . import twofa
        return {"second_step": True, "pending": twofa.pending_token(user), "name": user["name"]}
    login_throttle.ok(ip, name)
    if user.get("emergency"):                            # jede Notfall-Anmeldung im Protokoll (T-0-N/FestPOS#15)
        await db(lb.audit, store, auth.EMERGENCY_NAME, "emergency_login", ip=ip, node=Config.node)
    if user["id"]:
        await db(store.execute, "UPDATE users SET last_login=? WHERE id=?", (time.strftime("%Y-%m-%dT%H:%M:%S"), user["id"]))
    idle_s = await db(idle_seconds, user)
    return {**issue_session(user["id"], user["gen"], idle_s), **await db(users_mod.me_info, user)}


class LoginTotpIn(BaseModel):
    pending: str = Field(max_length=200)
    code: str = Field(max_length=20)


@app.post("/api/admin/login/totp")
async def admin_login_totp(body: LoginTotpIn, request: Request):
    """Zweiter Schritt der Anmeldung: Code aus der App oder Wiederherstellungs-Code -> Sitzungs-Token."""
    from . import twofa
    ip = request.client.host if request.client else "?"
    login_throttle.check(ip)
    user = await db(twofa.pending_user, body.pending)
    if not user:
        raise HTTPException(409, t("err.totp.pending_expired"))   # zurück zum Passwort
    name = login_name(user["name"])
    login_throttle.check_name(ip, name)
    state = await db(twofa.verify_code, user["id"], body.code, user["name"])
    if state != "ok":
        login_throttle.fail(ip, name)
        raise twofa.code_error(state, 401)
    login_throttle.ok(ip, name)
    await db(store.execute, "UPDATE users SET last_login=? WHERE id=?", (time.strftime("%Y-%m-%dT%H:%M:%S"), user["id"]))
    idle_s = await db(idle_seconds, user)
    return {**issue_session(user["id"], user["gen"], idle_s), **await db(users_mod.me_info, user)}


@app.get("/api/logo")
async def logo():
    """Logo für die Verwaltung (auch schon bei der Anmeldung): {"logo": "/media/…" oder null, "logo_plate": bool}."""
    return JSONResponse(await db(logo_info, store), headers={"Cache-Control": "no-store"})


@app.get("/api/nav")
async def nav(request: Request):
    """Für die Links zwischen Bestell-App, Verwaltung und Statusanzeige (auch in Fully): Kam die Seite über den
    Handy-Port? Dann ist die Bestell-App gleich unter «/» erreichbar, sonst über den Handy-Port (nur mit Zertifikat)."""
    # tabs: Seite «Offene Tische» im Menü nur mit «Tisch offen lassen» (oder solange noch einer offen ist)
    tabs = await db(lambda: tabs_mod.mode(store) == "open" or tabs_mod.count_open(store) > 0)
    return {"phone": devices.via_phone(request), "phone_port": Config.phone_port or None, "tabs": tabs}


# -- Cluster ------------------------------------------------------------------
@adm.get("/cluster")
async def cluster():
    info = await db(cluster_info)               # Zwischenstand aus dem Monitor (core.cluster_info)
    return {"this_node": Config.node, "printing_here": spooler.is_leader, **info}


@adm.get("/lamp")
async def lamp():
    """Ampel der Seitenleiste (Link zur Statusanzeige): Störungen und Hinweise zu Druckern und Pi."""
    return await db(status_mod.lamp)


@adm.get("/stats")
async def stats():
    """Kennzahlen ohne Übungsbestellungen (Testmodus); diese werden nur gezählt. Dazu Hinweise (warnings)."""
    row = await db(store.query_one, """
        SELECT COUNT(*) AS orders,
               COALESCE(SUM(total_cents),0) - COALESCE((SELECT SUM(c.amount_cents) FROM cancellations c
                   JOIN orders o2 ON o2.id=c.order_id WHERE o2.test=0), 0) AS total_cents,
               (SELECT COUNT(*) FROM orders WHERE closing_id IS NULL AND test=0) AS open_orders,
               (SELECT COUNT(*) FROM orders WHERE test=1) AS test_orders,
               (SELECT COUNT(*) FROM print_jobs WHERE status IN ('failed','uncertain')) AS problems
        FROM orders WHERE test=0""")
    row["certs_expiring"] = await db(devices.expiring_manual)     # nur die von Hand zu erneuernden (T3)
    warn, newest = await db(status_mod.backup_state)
    dup = ["device_names_dup"] if await db(device_ids.conflicts, store) else []    # Altbestand (T1): Name doppelt
    row["mac_alarms"] = await db(devices.mac_alarm_names)       # zwei Geräte mit demselben Zertifikat (T4)
    row["fully_outdated"] = [p["name"] for p in await db(fully_apk.outdated)]    # Fully-Version weicht ab (T6)
    row["test_devices"] = await db(test_mode.active)             # Handys gerade im Testmodus (T7)
    row["restore"] = await db(restore_check.overview_state)      # Sicherung eingespielt, Prüfliste offen (T8)
    pw = await db(maintenance.password_info)
    unprinted = ["backup_pw_unprinted"] if pw["set"] and not pw["printed"] else []
    season = ["season_due"] if await db(saison.due) else []      # Quittungen älter als 3 Monate auf dem Pi (#12)
    return {**row, "warnings": await db(core_warnings) + warn + dup + unprinted + season, "backup": newest}


app.include_router(adm)
app.include_router(time_admin.router)
app.include_router(time_admin.admin_router)
app.include_router(settings_admin.admin_router)
app.include_router(printers.admin_router)
app.include_router(printer_net.admin_router)
app.include_router(payrexx.router)
app.include_router(payrexx.result_router)
app.include_router(payrexx.admin_router)
app.include_router(gast_mod.router)
app.include_router(gast_mod.admin_router)
app.include_router(web_mod.router)               # Webseite & Rechtliches zur QR-Bestellung
app.include_router(web_mod.admin_router)
app.include_router(catalog.admin_router)
app.include_router(tables.admin_router)
app.include_router(plan_mod.admin_router)
app.include_router(bon_design.admin_router)
app.include_router(tabs_mod.router)
app.include_router(tabs_mod.admin_router)
app.include_router(maintenance.admin_router)
app.include_router(restore_check.admin_router)
app.include_router(cards.admin_router)
app.include_router(krypt_mod.router)
app.include_router(krypt_mod.admin_router)
app.include_router(umzug_mod.admin_router)
app.include_router(ledger.router)
app.include_router(ledger.admin_router)
app.include_router(stock_mod.router)
app.include_router(stock_mod.admin_router)
app.include_router(devices.router)
app.include_router(devices.admin_router)
app.include_router(display.admin_router)
app.include_router(fully.admin_router)
app.include_router(fully.files_router)
app.include_router(fully_apk.admin_router)
app.include_router(fully_apk.internal)
app.include_router(uplink_admin.router)
app.include_router(update_mod.router)
app.include_router(update_mod.internal)
app.include_router(update_net.router)
app.include_router(users_mod.admin_router)
app.include_router(saison.admin_router)                   # Saison abschliessen: Archiv, Gastdaten löschen (#12)
app.include_router(firewall.admin_router)                 # Firewall: Verbindungen ins Internet (#10)
app.include_router(sicherheit.admin_router)               # Sicherheitsereignisse in der Übersicht (#11)
app.include_router(twofa.admin_router)                    # zweiter Faktor, kritische Aktionen bestätigen (#9)
app.include_router(system_mod.router)
app.include_router(system_mod.admin_router)
app.include_router(status_mod.router)
app.include_router(status_simple.router)    # einfache Anzeige für alte Geräte (iOS 9)
app.include_router(test_mode.router)
app.include_router(test_mode.admin_router)


# app.js und sw.js mit der Versionsnummer aus VERSION (REV-052): ändert sich die Version, ändert sich sw.js –
# daran erkennen die Handys das Update.
_versioned: dict[str, tuple[float, bytes, str]] = {}


def _versioned_file(name: str, request: Request) -> Response:
    path = Config.static / name
    try:
        mtime = path.stat().st_mtime
        cached = _versioned.get(name)
        if not cached or cached[0] != mtime:
            body = path.read_text(encoding="utf-8").replace("__FESTPOS_VERSION__", APP_VERSION).encode()
            cached = _versioned[name] = (mtime, body, '"' + hashlib.sha1(body).hexdigest()[:16] + '"')
    except OSError:
        raise HTTPException(404) from None
    _, body, etag = cached
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="text/javascript; charset=utf-8", headers=headers)


@app.get("/app.js", include_in_schema=False)
async def app_js(request: Request):
    return _versioned_file("app.js", request)


@app.get("/sw.js", include_in_schema=False)
async def service_worker(request: Request):
    return _versioned_file("sw.js", request)


if Config.static.is_dir():
    app.mount("/", StaticFiles(directory=Config.static, html=True), name="static")
