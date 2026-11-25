"""Überwachung für die Statusanzeige: Handys (online?), Netzwerkgeräte (Switch, Access Point),
Drucker (Papier/erreichbar), Dienste auf dem Pi, Systemwerte und der Empfang des Mobilfunk-Routers (uplink.py).

Läuft als Hintergrund-Aufgabe alle paar Sekunden; die Statusanzeige liest nur den letzten Stand.
Nichts davon beeinflusst Bestellen oder Drucken.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import shutil
import time

from . import uplink as uplink_mod
from .escpos import PrinterAddr, query_status

log = logging.getLogger("festpos.monitor")

IPS_KEPT = 4                # pro Gerät so viele zuletzt gemeldete Adressen (T4, MAC-Wechsel)
ONLINE_SECONDS = 60          # Handys fragen alle 20 s nach – 60 s ohne Lebenszeichen = offline
BATTERY_MAX_AGE = 15 * 60    # Akkustand älter als 15 min (App zu, Handy aus) wird nicht mehr angezeigt
def _default_hosts() -> str:
    """Vorgabe für die Netzwerkgeräte: Switch .30 und Access Point .40 im FestPOS-Netz (T-0-N/FestPOS#2)."""
    from . import netz
    ex = netz.info()["examples"]
    return f"Switch={ex['switch']}; Access Point={ex['ap']}"


DEFAULT_HOSTS = _default_hosts()


def host_checks() -> bool:
    """FESTPOS_HOST_CHECKS=0 nur für die Tests: keine Prüfungen, die vom Netz und den Diensten des Testrechners
    abhängen – Netzwerkgeräte ausserhalb dieses Rechners entfallen, Handys gelten als «Ping unbekannt», Dienste werden
    nicht abgefragt. Adressen auf diesem Rechner (127.x) werden weiter geprüft."""
    return os.environ.get("FESTPOS_HOST_CHECKS", "1") != "0"


def _local(ip: str) -> bool:
    return ip.startswith("127.") or ip in ("::1", "localhost")
SERVICES = ["rqlited", "festpos-redirect", "chrony", "nftables", "keepalived"]
CLUSTER_EVERY = 4.0          # Sekunden zwischen zwei Abfragen des Zustands der Pi
CLUSTER_MAX_AGE = 30.0       # älter: nicht mehr verwenden (dann direkt fragen)
PEERS_EVERY = 10.0           # Sekunden zwischen zwei Abfragen der anderen Pi (Infos, Drucker; T9)
PEER_MAX_AGE = 30.0          # Antwort eines anderen Pi so lange verwenden


def parse_hosts(text: str) -> list[tuple[str, str]]:
    """'Switch=192.168.55.30; Access Point=192.168.55.40' -> [(Name, Adresse)]"""
    out = []
    for part in (text or "").replace("\n", ";").split(";"):
        name, sep, host = part.partition("=")
        name, host = name.strip(), host.strip()
        if not sep:
            name, host = part.strip(), part.strip()
        if host:
            out.append((name[:30] or host, host[:64]))
    return out[:10]


def parse_battery(value) -> int | None:
    """Akkustand 0–100 aus der Meldung des Handys, sonst None (fehlt, Unsinn)."""
    try:
        level = round(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return level if 0 <= level <= 100 else None


HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,62}$")      # IPv4 oder Hostname
MAX_DEVICES = 60                                                 # so viele Handys merkt sich die Statusanzeige
PHONE_ID_RE = re.compile(r"p[0-9a-f]{12}")                       # ID eines Handys in monitor_devices (T1)
DEVICE_ID_RE = re.compile(r"[0-9A-Za-z]{1,64}")                  # ID eines Geräts mit Zertifikat (= Seriennummer)


def parse_targets(text: str | None, legacy_hosts: str | None = None) -> list[dict]:
    """Überwachte Geräte aus der Einstellung (JSON-Liste), sonst die alte Form 'Name=IP; …' als Netzwerkgeräte.
    -> [{"name", "kind": "phone"|"net", "ip", "port": int|None, "active": bool}]. Nicht im Einsatz (active False):
    bleibt eingetragen, wird aber weder geprüft noch angezeigt."""
    out: list[dict] = []
    try:
        items = json.loads(text) if text else None
    except ValueError:
        items = None
    if isinstance(items, list):
        for it in items[:40]:
            if not isinstance(it, dict):
                continue
            ip = str(it.get("ip") or "").strip()
            if not HOST_RE.match(ip):
                continue
            port = it.get("port")
            port = int(port) if isinstance(port, (int, str)) and str(port).isdigit() and 0 < int(port) < 65536 else None
            entry = {"name": (str(it.get("name") or "").strip() or ip)[:30],
                     "kind": "phone" if it.get("kind") == "phone" else "net", "ip": ip, "port": port,
                     "active": it.get("active") is not False}          # «Im Einsatz» (Verwaltung)
            pin = str(it.get("fully_pin") or "")
            if entry["kind"] == "phone" and re.fullmatch(r"[0-9a-f]{64}", pin):     # Fully per HTTPS eingerichtet
                entry["fully_pin"] = pin
                entry["fully_until"] = str(it.get("fully_until") or "")[:10] or None
            if entry["kind"] == "phone":         # Geräte-ID (T1) und – wenn vorhanden – das zugehörige Zertifikat
                if PHONE_ID_RE.fullmatch(str(it.get("id") or "")):
                    entry["id"] = it["id"]
                if DEVICE_ID_RE.fullmatch(str(it.get("cert") or "")):
                    entry["cert"] = it["cert"]
            out.append(entry)
        return out
    for name, host in parse_hosts(legacy_hosts if legacy_hosts is not None else DEFAULT_HOSTS):
        h, _, p = host.partition(":")
        if not HOST_RE.match(h):                 # alte Einstellung genauso prüfen: «-s 65000» ist kein Host (REV-040)
            continue
        out.append({"name": name, "kind": "net", "ip": h, "port": int(p) if p.isdigit() and 0 < int(p) < 65536 else None,
                    "active": True})
    return out


class Monitor:
    def __init__(self):
        self.devices: dict[str, dict] = {}       # Schlüssel (key_for) -> {last_seen, ip, name, serial, …}
        self.hosts: list[dict] = []              # Netzwerkgeräte {name, host, ok}
        self.targets: list[dict] = []            # alle überwachten Geräte {name, kind, ip, port, ok}
        self.printers: dict[int, dict] = {}
        self.services: list[dict] = []
        self.system: dict = {}
        self.fully: dict[str, dict] = {}         # IP -> letzte Fully-Abfrage {name, state, at, data, data_at}
        self.fully_web: dict[str, dict] = {}     # IP -> JavaScript-Schnittstelle/Whitelist {state, at, …} (fully.web_due)
        self.uplink = uplink_mod.Tracker()      # Mobilfunk-Router: letzter Stand und Verlauf
        self._task: asyncio.Task | None = None
        self._fully_task: asyncio.Task | None = None
        self._uplink_task: asyncio.Task | None = None
        self._uplink_wake: asyncio.Event | None = None
        self.cluster: tuple[float, dict] | None = None   # (Zeit, Mitglieder/Erreichbarkeit der Pi) – siehe core.cluster_info
        self._cluster_task: asyncio.Task | None = None
        self.peers: dict[str, dict] = {}         # anderer Pi -> {at, data} (Antwort von /api/internal/node, T9)
        self._peers_down: set[str] = set()       # Adressen, die zuletzt nicht antworteten (nur fürs Journal)
        self._peers_task: asyncio.Task | None = None
        self._printers_at = 0.0                  # wann dieser Pi die Drucker zuletzt selbst abgefragt hat

    # -- Handys: jede Anfrage der App ist ein Lebenszeichen ----------------------
    # Schlüssel ist das Gerät, nie ein Name (T1): mit Gerätezertifikat dessen Seriennummer, sonst IP und App-Name.
    # Der Name aus der App («Name dieses Geräts») darf auf mehreren Handys gleich sein – er ist nur Anzeige.
    @staticmethod
    def key_for(ip: str | None, name: str, serial: str | None) -> str:
        return f"c:{serial}" if serial else f"a:{ip or '-'}|{name}"

    def seen(self, name: str, ip: str | None, outbox: str | None, version: str | None, test: bool = False,
             battery: str | None = None, charging: str | None = None, serial: str | None = None,
             cert_name: str | None = None) -> None:
        name = (name or "").strip()[:40]
        serial = (serial or "").strip()[:64] or None
        if not name and not serial:
            return
        try:
            ob = max(0, int(outbox or 0))
        except ValueError:
            ob = 0
        now = time.time()
        key = self.key_for(ip, name, serial)
        old = self.devices.get(key, {})
        # letzte Adressen mit Zeit (T4): zwei Handys mit demselben Zertifikat wechseln sich hier ab
        ips = {k: v for k, v in (old.get("ips") or {}).items() if k != ip}
        if ip:
            ips[ip] = now
        ips = dict(sorted(ips.items(), key=lambda kv: kv[1])[-IPS_KEPT:])
        entry = {"last_seen": now, "ip": ip, "ips": ips, "outbox": ob, "version": (version or "")[:20], "name": name,
                 "serial": serial, "cert_name": (cert_name or "").strip()[:40] or None,
                 "ping": old.get("ping") if old.get("ip") == ip else None, "test": bool(test),
                 # seit wann im Testmodus (T7): ein «beenden» von aussen gilt nur, wenn es danach kam
                 "test_since": (old.get("test_since") or now if old.get("test") else now) if test else None,
                 "battery": old.get("battery"), "charging": old.get("charging"), "battery_at": old.get("battery_at")}
        level = parse_battery(battery)
        if level is not None:                    # Akku meldet die App (Chrome oder Fully), wenn das Handy es kann
            entry.update(battery=level, charging=charging == "1", battery_at=now)
        self.devices[key] = entry
        if len(self.devices) > MAX_DEVICES:      # begrenzen: das am längsten nicht gesehene fällt raus (REV-036)
            del self.devices[min(self.devices, key=lambda k: self.devices[k]["last_seen"])]

    def app_at(self, ip: str, serials: set[str] | None = None) -> dict | None:
        """Letztes Lebenszeichen der App für ein überwachtes Handy: mit Zertifikat (serials, T4) über das Gerät – so ist
        es auch auf einer anderen Adresse online, z. B. nach einem MAC-Wechsel –, sonst von dieser IP."""
        hits = [d for d in self.devices.values() if serials and d.get("serial") in serials] or \
            [d for d in self.devices.values() if d.get("ip") == ip]
        if not hits:
            return None
        d = max(hits, key=lambda x: x["last_seen"])
        sec = int(time.time() - d["last_seen"])
        return {"online": sec < ONLINE_SECONDS, "seconds": sec, "outbox": d["outbox"], "version": d["version"],
                "test": d.get("test", False), "test_since": d.get("test_since"), "serial": d.get("serial"),
                "app_name": d["name"] or d.get("cert_name"), **self._battery(d)}   # app_name: steht auf der Bestellung

    @staticmethod
    def _battery(d: dict) -> dict:
        """Akku aus der App-Meldung, höchstens BATTERY_MAX_AGE alt."""
        at = d.get("battery_at")
        if d.get("battery") is None or not at or time.time() - at > BATTERY_MAX_AGE:
            return {}
        return {"battery": d["battery"], "charging": bool(d.get("charging")), "battery_age": int(time.time() - at),
                "battery_src": "app"}

    def recent_ips(self, max_age: float, now: float | None = None) -> list[tuple[str, str, float]]:
        """(Seriennummer, IP, Zeit) aller Meldungen MIT Zertifikat aus den letzten max_age Sekunden (T4)."""
        now = time.time() if now is None else now
        return [(d["serial"], ip, at) for d in list(self.devices.values()) if d.get("serial")
                for ip, at in (d.get("ips") or {}).items() if now - at <= max_age]

    def device_list(self) -> list[dict]:
        """Alle gesehenen Handys, eines pro Schlüssel (key). name = Name aus der App, cert_name = Gerätename."""
        now = time.time()
        return [{"key": k, "name": d["name"], "cert_name": d.get("cert_name"), "serial": d.get("serial"),
                 "last_seen": d["last_seen"], "online": now - d["last_seen"] < ONLINE_SECONDS,
                 "seconds": int(now - d["last_seen"]), "outbox": d["outbox"], "ip": d["ip"], "version": d["version"],
                 "ping": d.get("ping"), "test": d.get("test", False), "test_since": d.get("test_since"),
                 **self._battery(d)}
                for k, d in self.devices.items()]

    # -- Hintergrund-Prüfung ----------------------------------------------------
    def start(self, get_targets, get_printers, cluster: bool, extra=None, printer_ok=None,
              fully_cfg=None, fully_here=None, uplink_cfg=None, cluster_info=None, printers_here=None,
              peers=None, printers_legacy=None, printer_seen=None) -> None:
        self._get_targets, self._get_printers, self._cluster = get_targets, get_printers, cluster
        self._extra = extra                       # zusätzlich pro Durchlauf (z. B. Bildschirm-Einstellung)
        self._printer_ok = printer_ok             # Drucker meldet wieder "alles ok" (z.B. Papier nachgefüllt)
        self._printer_seen = printer_seen         # Drucker hat die Statusabfrage beantwortet (T15, Datenbank)
        # Drucker fragt nur der Pi ab, der druckt (sonst stören sich Abfrage und Senden an Port 9100). Die anderen Pi
        # holen den Stand bei ihm ab (peers, /api/internal/node) – nicht über die Datenbank (T9). printers_legacy:
        # Stand aus der Datenbank, solange während eines Updates noch ein alter druckender Pi ihn dort ablegt.
        self._printers_here, self._printers_legacy = printers_here, printers_legacy
        self._task = asyncio.create_task(self._loop(), name="monitor")
        if fully_cfg:                             # Fully Kiosk: eigener Takt, damit ein schlafendes Handy nichts bremst
            self._fully_cfg, self._fully_here = fully_cfg, fully_here
            self._fully_task = asyncio.create_task(self._fully_loop(), name="fully")
        if uplink_cfg:                            # Mobilfunk-Router: auf jedem Pi (jede Statusanzeige zeigt ihn)
            self._uplink_cfg = uplink_cfg
            self._uplink_wake = asyncio.Event()
            self._uplink_task = asyncio.create_task(self._uplink_loop(), name="uplink")
        if cluster_info:                          # Zustand der Pi: im Hintergrund, nie im Aufruf der Statusanzeige
            self._cluster_fn = cluster_info
            self._cluster_task = asyncio.create_task(self._cluster_loop(), name="cluster")
        if peers:                                 # die anderen Pi direkt fragen (nur im Cluster)
            self._peers_fn = peers
            self._peers_task = asyncio.create_task(self._peers_loop(), name="peers")

    async def stop(self) -> None:
        for task in (self._task, self._fully_task, self._uplink_task, self._cluster_task, self._peers_task):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    # -- Fully Kiosk (nur Handys mit eingerichtetem Zertifikat, nur HTTPS – siehe fully.py) -----------
    async def _fully_loop(self) -> None:
        from . import fully as fully_mod, fully_apk
        while True:
            try:
                if self._fully_here is None or self._fully_here():      # nur der Pi, mit dem die Handys reden
                    cfg = await asyncio.to_thread(self._fully_cfg)
                    await self.poll_fully(cfg)
                    await fully_mod.web_due(cfg)                        # JavaScript-Schnittstelle nur mit Whitelist
                    renewed = await fully_mod.auto_renew(cfg)           # Gerätezertifikate vor Ablauf erneuern (T3)
                    await asyncio.to_thread(fully_apk.note_versions, self.fully_view())   # Fully-Versionen (T6)
                    if not renewed:                                     # nie beides an einem Handy in einer Runde
                        await fully_apk.push_pending(cfg)               # APK nachliefern (T6)
            except asyncio.CancelledError:
                raise
            except Exception:                      # noqa: BLE001 – darf den Server nie stören
                log.exception("Fully-Abfrage fehlgeschlagen")
            await asyncio.sleep(fully_mod.INTERVAL)

    async def poll_fully(self, cfg: dict) -> None:
        from . import fully as fully_mod
        targets = [t for t in cfg["targets"] if t.get("active", True)] if cfg.get("password") else []
        res = await asyncio.gather(*(fully_mod.query(t["ip"], cfg["port"], t["pin"], cfg["password"]) for t in targets))
        keep = {t["ip"] for t in targets}
        self.fully = {ip: v for ip, v in self.fully.items() if ip in keep}    # nicht mehr eingerichtete vergessen
        self.fully_web = {ip: v for ip, v in self.fully_web.items() if ip in keep}
        for t, r in zip(targets, res):
            self.fully_store(t["name"], t["ip"], r)

    def fully_store(self, name: str, ip: str, res: dict) -> None:
        now = time.time()
        old = self.fully.get(ip, {})
        entry = {"name": name, "state": res.get("state", "bad"), "at": now}
        if entry["state"] == "ok":
            entry.update(data=res.get("data") or {}, data_at=now)
        elif old.get("data") and now - old.get("data_at", 0) < BATTERY_MAX_AGE:
            entry.update(data=old["data"], data_at=old["data_at"])        # Handy schläft: letzter Stand mit Alter
        self.fully[ip] = entry

    def fully_forget(self, ip: str) -> None:
        self.fully.pop(ip, None)
        self.fully_web.pop(ip, None)

    def fully_web_store(self, ip: str, res: dict) -> None:
        self.fully_web[ip] = {**res, "at": time.time()}

    def fully_view(self) -> dict[str, dict]:
        now = time.time()
        web = {ip: {**{k: x for k, x in w.items() if k != "at"}, "seconds": int(now - w["at"])}
               for ip, w in self.fully_web.items()}
        return {ip: {"name": v["name"], "state": v["state"], "seconds": int(now - v["at"]),
                     **({"data": v["data"], "data_seconds": int(now - v["data_at"])} if v.get("data") else {}),
                     **({"web": web[ip]} if ip in web else {})}
                for ip, v in self.fully.items()}

    # -- Zustand der Pi (Raft-Mitglieder, erreichbar?) ----------------------------------------------------
    # Ein ausgesteckter Pi lässt die Abfrage bis zur Zeitgrenze warten. Darum hier im Hintergrund abfragen und
    # merken; Statusanzeige und Verwaltung lesen nur den Zwischenstand (core.cluster_info) und bleiben schnell.
    async def _cluster_loop(self) -> None:
        while True:
            await self.refresh_cluster()
            await asyncio.sleep(CLUSTER_EVERY)

    async def refresh_cluster(self) -> None:
        try:
            self.cluster = (time.time(), await asyncio.to_thread(self._cluster_fn))
        except asyncio.CancelledError:
            raise
        except Exception as e:                     # noqa: BLE001 – Datenbank (noch) nicht da: alter Stand bleibt
            log.debug("Zustand der Pi nicht abfragbar: %s", e)

    def cluster_cached(self, max_age: float = CLUSTER_MAX_AGE) -> dict | None:
        """Letzter Stand, wenn höchstens max_age Sekunden alt – sonst None."""
        if self.cluster and time.time() - self.cluster[0] <= max_age:
            return self.cluster[1]
        return None

    # -- Die anderen Pi: Infos und Drucker direkt beim anderen abholen, nicht über die Datenbank (T9) -----------
    async def _peers_loop(self) -> None:
        while True:
            try:
                self.peers_store(await asyncio.to_thread(self._peers_fn))
            except asyncio.CancelledError:
                raise
            except Exception:                      # noqa: BLE001 – darf den Server nie stören
                log.exception("Abfrage der anderen Pi fehlgeschlagen")
            await asyncio.sleep(PEERS_EVERY)

    def peers_store(self, res: dict) -> None:
        """Ergebnis von timesync.ask_peers: {Adresse: (Antwort, gesendet, Antwortzeit) | Fehlertext}."""
        now = time.time()
        for base, r in res.items():
            if isinstance(r, tuple) and isinstance(r[0].get("node"), str):
                self.peers[r[0]["node"]] = {"at": now, "data": r[0]}
                if base in self._peers_down:
                    self._peers_down.discard(base)
                    log.info("Pi %s antwortet wieder (Infos, Drucker)", base)
            elif base not in self._peers_down:     # nur beim Wechsel ins Journal, nicht alle 10 s
                self._peers_down.add(base)
                log.info("Pi %s antwortet nicht (Infos, Drucker): %s", base, r)
        here = getattr(self, "_printers_here", None)
        if here and not here():                    # gleich übernehmen, nicht erst im nächsten Durchlauf
            self.printers = self.leader_printers()

    def peer(self, node: str, max_age: float = PEER_MAX_AGE) -> dict | None:
        """{at, data} eines anderen Pi, wenn die Antwort höchstens max_age Sekunden alt ist."""
        p = self.peers.get(node)
        return p if p and time.time() - p["at"] <= max_age else None

    def peer_seen(self, node: str) -> float | None:
        """Wann dieser Pi zuletzt eine Antwort von node bekam (None: seit dem Start nie)."""
        p = self.peers.get(node)
        return p["at"] if p else None

    def printers_here(self) -> dict | None:
        """Stand der Drucker für die anderen Pi – nur wenn dieser Pi sie selbst abgefragt hat (er druckt)."""
        if time.time() - self._printers_at > PEER_MAX_AGE:
            return None
        return {str(k): v for k, v in self.printers.items()}

    def leader_printers(self) -> dict[int, dict]:
        """Stand der Drucker vom druckenden Pi (frische Antwort mit leader); sonst «unbekannt» ({})."""
        for p in self.peers.values():
            d = p["data"]
            if time.time() - p["at"] <= PEER_MAX_AGE and d.get("leader") and isinstance(d.get("printers"), dict):
                return {int(k): v for k, v in d["printers"].items() if str(k).isdigit() and isinstance(v, dict)}
        if getattr(self, "_printers_legacy", None):
            try:
                return self._printers_legacy()
            except Exception:                      # noqa: BLE001 – Datenbank gerade nicht lesbar: unbekannt
                return {}
        return {}

    # -- Mobilfunk-Router (Empfang; nur HTTPS mit festgehaltenem Zertifikat – siehe uplink.py) ---------------
    async def _uplink_loop(self) -> None:
        while True:
            try:
                await self.poll_uplink(await asyncio.to_thread(self._uplink_cfg))
            except asyncio.CancelledError:
                raise
            except Exception:                      # noqa: BLE001 – darf den Server nie stören
                log.exception("Router-Abfrage fehlgeschlagen")
            try:                                   # warten – oder sofort, wenn die Verwaltung etwas geändert hat
                await asyncio.wait_for(self._uplink_wake.wait(), uplink_mod.INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._uplink_wake.clear()

    async def poll_uplink(self, cfg: dict | None) -> None:
        if not cfg:
            if self.uplink.key is not None:
                self.uplink.reset()
            return
        self.uplink.store(cfg, await uplink_mod.poll(cfg, slow=self.uplink.want_slow(cfg)))

    def uplink_now(self) -> None:
        """Nach einer Änderung in der Verwaltung gleich neu abfragen (nicht erst nach INTERVAL)."""
        if self._uplink_wake:
            self._uplink_wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:                      # Anzeige darf nie den Server stören
                log.exception("Monitor-Durchlauf fehlgeschlagen")
            await asyncio.sleep(10)

    async def check(self) -> None:
        listed = await asyncio.to_thread(self._get_targets)
        targets = [t for t in listed if t.get("active", True)                  # nur Geräte im Einsatz
                   and (t["kind"] != "net" or host_checks() or _local(t["ip"]))]
        printers = await asyncio.to_thread(self._get_printers)
        here = getattr(self, "_printers_here", None) is None or self._printers_here()
        printers_q = printers if here else []
        n = len(targets)
        results = await asyncio.gather(*(self._check_target(t) for t in targets),
                                       *(self._printer(p) for p in printers_q), self._services())
        self.targets = [{**t, "ok": ok} for t, ok in zip(targets, results[:n])]
        self.hosts = [{"name": t["name"], "host": t["ip"] + (f":{t['port']}" if t["port"] else ""), "ok": t["ok"]}
                      for t in self.targets if t["kind"] == "net"]
        if here:
            self.printers = {p["id"]: r for p, r in zip(printers_q, results[n:n + len(printers_q)])}
            self._printers_at = time.time()
        else:
            self.printers = await asyncio.to_thread(self.leader_printers)
        if here and getattr(self, "_printer_ok", None):
            for pid, r in self.printers.items():
                if r.get("ok"):
                    self._printer_ok(pid)
        if here and getattr(self, "_printer_seen", None):
            for p, r in zip(printers_q, results[n:n + len(printers_q)]):
                if r.get("answered") and not p.get("status_seen"):
                    await asyncio.to_thread(self._printer_seen, p["id"])     # einmal pro Drucker (T15)
        self.services = results[-1]
        if not any(t["kind"] == "phone" for t in listed):
            await self._ping_devices()            # keine Handys eingetragen: die gesehenen anpingen
        self.system = await asyncio.to_thread(self._system)
        if getattr(self, "_extra", None):
            try:
                await asyncio.to_thread(self._extra)
            except Exception:                      # noqa: BLE001 – darf den Monitor nie stoppen
                log.exception("Zusatz-Prüfung fehlgeschlagen")

    async def _check_target(self, t: dict) -> bool | None:
        """Mit Port: nur dieser TCP-Port. Netzwerkgerät ohne Port: Ping, sonst Weboberfläche. Handy: Ping."""
        if t["port"]:
            return await self._reach(f"{t['ip']}:{t['port']}")
        if t["kind"] == "phone":
            return await self._ping(t["ip"])
        return await self._reach(t["ip"])

    @staticmethod
    async def _reach(host: str) -> bool:
        """Erreichbar? Erst Ping, sonst Web-Oberfläche (Port 80/443) – Switch und AP haben beides.
        "IP:Port" prüft nur diesen Port."""
        ports = (80, 443)
        if host.count(":") == 1:
            host, _, p = host.partition(":")
            if p.isdigit():
                ports = (int(p),)
        if shutil.which("ping") and len(ports) > 1:
            try:
                proc = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "1", "--", host,
                                                            stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.DEVNULL)
                if await asyncio.wait_for(proc.wait(), 3) == 0:
                    return True
            except (OSError, asyncio.TimeoutError):
                pass
        for port in ports:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 1.5)
                w.close()
                return True
            except (OSError, asyncio.TimeoutError):
                continue
        return False

    @staticmethod
    async def _ping(ip: str) -> bool | None:
        """Ein Ping, 1 s. None = Ping auf diesem System nicht möglich."""
        if not shutil.which("ping") or not (host_checks() or _local(ip)):
            return None
        try:
            proc = await asyncio.create_subprocess_exec("ping", "-c", "1", "-W", "1", "-n", "--", ip,
                                                        stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.DEVNULL)
            return await asyncio.wait_for(proc.wait(), 3) == 0
        except (OSError, asyncio.TimeoutError):
            return None

    async def _ping_devices(self) -> None:
        """Handys mit bekannter IP anpingen: zeigt, ob ein Handy noch im WLAN ist, auch wenn die App zu ist.
        (Handys im Standby antworten manchmal nicht – darum zählt die App-Meldung mehr als der Ping.)"""
        items = [(k, d["ip"]) for k, d in self.devices.items() if d.get("ip") and d["ip"] not in ("127.0.0.1", "::1")]
        res = await asyncio.gather(*(self._ping(ip) for _, ip in items))
        for (k, ip), ok in zip(items, res):
            if k in self.devices and self.devices[k].get("ip") == ip:
                self.devices[k]["ping"] = ok

    @staticmethod
    async def _printer(p: dict) -> dict:
        """Papier/Deckel nur abfragen, wenn der Drucker gerade nichts zu drucken hat (stört sonst nicht)."""
        if not p["enabled"] or p["busy"]:
            return {}
        st = await query_status(PrinterAddr(p["host"], p["port"]), timeout=1.5)
        # T15: hat er früher geantwortet und schweigt jetzt, ist er nicht bereit (Epson «Zustand BUSY»: Papier leer)
        return {"online": st.online, "paper_out": st.paper_out, "paper_near_end": st.paper_near_end,
                "cover_open": st.cover_open, "error": st.error if st.online else None,
                "ok": st.ok and not st.no_answer, "no_status": bool(st.online and st.no_answer and p.get("status_seen")),
                "answered": bool(st.online and not st.no_answer)}

    async def _services(self) -> list[dict]:
        if not shutil.which("systemctl") or not host_checks():
            return []
        dirs = ("/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system")
        # Ausfallschutz (keepalived) läuft nur im Cluster – im Einzelbetrieb ist er absichtlich aus
        cluster = os.path.exists("/run/festpos/cluster") if os.path.isdir("/run/festpos") else self._cluster
        names = [s for s in SERVICES if (s != "keepalived" or cluster)
                 and any(os.path.exists(f"{d}/{s}.service") for d in dirs)]   # nur installierte Dienste
        if not names:
            return []
        try:
            proc = await asyncio.create_subprocess_exec("systemctl", "is-active", *names,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
        except (OSError, asyncio.TimeoutError):
            return []
        states = out.decode().split()
        return [{"name": n, "ok": s == "active", "state": s} for n, s in zip(names, states)]

    @staticmethod
    def _system() -> dict:
        info: dict = {}
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                info["temp"] = round(int(f.read().strip()) / 1000)
        except (OSError, ValueError):
            pass
        try:
            du = shutil.disk_usage(os.environ.get("FESTPOS_DATA", "/var/lib/festpos") if
                                   os.path.isdir(os.environ.get("FESTPOS_DATA", "/var/lib/festpos")) else "/")
            info["disk_free"] = round(du.free / du.total * 100)
        except OSError:
            pass
        try:
            with open("/proc/uptime") as f:
                info["uptime"] = int(float(f.read().split()[0]))
            info["load"] = round(os.getloadavg()[0], 2)
        except (OSError, ValueError):
            pass
        return info
