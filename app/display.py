"""Bildschirm am Pi (Statusanzeige): Einstellungen pro Pi, Abgleich mit dem root-Helfer festpos-display."""
from __future__ import annotations

import json
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import i18n
from .core import Config, admin, cluster_info, db, log, rt

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Bildschirm am Pi (Statusanzeige) ---------------------------------------
# Wunsch pro Pi in settings.display_cfg.<pi> (JSON). Jeder Pi schreibt seinen Teil nach DISPLAY_FILE,
# das root-Programm festpos-display setzt ihn um (Treiber, Statusanzeige ein/aus, Neustart).
DISPLAY_TYPES = {
    "none": "admin.display.type_none",                # Bezeichnungen: Sprachdatei
    "hdmi": "admin.display.type_hdmi",
    "dsi7": "admin.display.type_dsi7",
    "dsi7v2": "admin.display.type_dsi7v2",
    "dsi5v2": "admin.display.type_dsi5v2",
    "tft35": "admin.display.type_tft35",
}
DISPLAY_FILE = os.environ.get("FESTPOS_DISPLAY_FILE", "/var/lib/festpos/display.json")
DISPLAY_STATUS_FILE = os.environ.get("FESTPOS_DISPLAY_STATUS", "/run/festpos/display-status.json")
SIZE_RE = r"^(|\d{3,4}x\d{3,4})$"


def _load(key: str) -> dict:
    try:
        d = json.loads(rt.store.setting(key) or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def _display_cfg(node: str) -> dict:
    """Einstellung eines Pi. Seit REV-042 ein eigener Schlüssel pro Pi (display_cfg.<pi>), damit gleichzeitige
    Änderungen an verschiedenen Pi sich nicht gegenseitig überschreiben; der alte gemeinsame Wert gilt als Vorgabe."""
    c = {"type": None, "rotate": 0, "size": "", "touchtest": False, "cal": None, "reboot": "", "cursor": "auto"}
    c.update(_load("display_cfg").get(node) or {})
    c.update(_load(f"display_cfg.{node}"))
    return c


def _display_update(node: str, **changes) -> dict:
    cur = _display_cfg(node)
    cur.update(changes)
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (f"display_cfg.{node}", json.dumps(cur)))
    return cur


def _display_nodes() -> list[str]:
    info = cluster_info()
    nodes = sorted((info.get("nodes") or {}).keys()) if info.get("mode") == "rqlite" else []
    return nodes or [Config.node]


def _display_local_status() -> dict:
    """Live-Zustand dieses Pi: angeschlossene Bildschirme, Statusanzeige an?, plus Meldung von festpos-display."""
    import glob
    import shutil
    import subprocess
    st: dict = {}
    try:
        with open(DISPLAY_STATUS_FILE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        pass
    con = []
    for c in sorted(glob.glob("/sys/class/drm/card*-*")):
        try:
            with open(c + "/status") as f:
                if f.read().strip() != "disconnected" and "Writeback" not in c:
                    con.append(os.path.basename(c).split("-", 1)[1])
        except OSError:
            continue
    st["connectors"] = con
    st["kiosk_installed"] = bool(shutil.which("cage") and shutil.which("chromium"))
    from .monitor import host_checks                 # Tests: nichts vom Testrechner abfragen
    try:
        st["kiosk"] = subprocess.run(["systemctl", "is-active", "--quiet", "festpos-kiosk"], capture_output=True,
                                     timeout=3).returncode == 0 if shutil.which("systemctl") and host_checks() else False
    except (OSError, subprocess.TimeoutExpired):
        st["kiosk"] = False
    st["helper"] = os.path.exists(DISPLAY_STATUS_FILE)
    return st


_display_last: dict = {}


def _display_sync() -> None:
    """Alle 10 s (Monitor): Wunsch für diesen Pi ablegen, Zustand für die Verwaltung melden."""
    cfg = _display_cfg(Config.node)
    power = cfg.get("power") if isinstance(cfg.get("power"), dict) else {}
    if cfg["type"] in DISPLAY_TYPES or power.get("token"):   # Herunterfahren/Neustart auch ohne eingerichteten Bildschirm
        w = {"type": cfg["type"] if cfg["type"] in DISPLAY_TYPES else None, "reboot": cfg.get("reboot") or "",
             "cursor": cfg.get("cursor") or "auto"}
        if power.get("token"):
            w["power"] = power
        wish = json.dumps(w)
        try:
            with open(DISPLAY_FILE) as f:
                same = f.read() == wish
        except OSError:
            same = False
        if not same and os.path.isdir(os.path.dirname(DISPLAY_FILE)):
            try:                                   # direkt schreiben: festpos-display.path reagiert aufs Schliessen
                with open(DISPLAY_FILE, "w") as f:
                    f.write(wish)
            except OSError as e:
                log.warning("Bildschirm-Einstellung nicht ablegbar: %s", e)
    st = _display_local_status()
    st.pop("time", None)
    if st != _display_last.get("st"):
        _display_last["st"] = st
        rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (f"display_status.{Config.node}", json.dumps({**st, "seen": int(time.time())})))


class DisplayIn(BaseModel):
    type: str = Field(pattern="^(" + "|".join(DISPLAY_TYPES) + ")$")
    rotate: int = Field(default=0)
    size: str = Field(default="", pattern=SIZE_RE)
    cursor: str = Field(default="auto", pattern="^(auto|on|off)$")   # Mauszeiger: auto = nur bei HDMI


class DisplayActionIn(BaseModel):
    action: str = Field(pattern="^(calibrate|reset|stop)$")


@admin_router.get("/display")
async def get_display():
    def run():
        out = []
        for n in _display_nodes():
            try:
                st = json.loads(rt.store.setting(f"display_status.{n}") or "{}")
            except ValueError:
                st = {}
            out.append({"node": n, "here": n == Config.node, "cfg": _display_cfg(n), "status": st})
        return {"types": {k: i18n.t(v) for k, v in DISPLAY_TYPES.items()}, "nodes": out}
    return await db(run)


def _check_node(node: str) -> None:
    if node not in _display_nodes():
        raise HTTPException(404, i18n.t("err.display.unknown_pi"))


@admin_router.put("/display/{node}")
async def put_display(node: str, d: DisplayIn):
    if d.rotate not in (0, 90, 180, 270):
        raise HTTPException(422, i18n.t("err.display.rotate"))
    await db(_check_node, node)
    return await db(_display_update, node, type=d.type, rotate=d.rotate, size=d.size, cursor=d.cursor)


@admin_router.post("/display/{node}/touch")
async def display_touch(node: str, a: DisplayActionIn):
    await db(_check_node, node)
    if a.action == "calibrate":
        return await db(_display_update, node, touchtest=True)
    if a.action == "stop":
        return await db(_display_update, node, touchtest=False)
    return await db(_display_update, node, touchtest=False, cal=None)


@admin_router.post("/display/{node}/reboot")
async def display_reboot(node: str):
    await db(_check_node, node)
    # mit Zeitstempel: festpos-display führt nur frische Aufträge aus (nicht aus einer Sicherung, REV-024)
    return await db(_display_update, node, reboot=f"{int(time.time())}-{secrets.token_hex(4)}")
