"""Mobilfunk-Router (Internet für Kartenzahlungen): Empfang abholen und für die Statusanzeige aufbereiten.

Aufbau – damit später weitere Router-Modelle dazukommen können:
  - Pro Modell ein Treiber (Klasse mit `Driver` als Vorlage) in DRIVERS. Er kennt nur die Schnittstelle seines
    Routers: Pfade, Antwortformat, Fehlermeldungen, Einrichtungsbefehle. `read()` liefert die Werte immer in
    derselben Form (FIELDS).
  - Alles andere ist für alle Modelle gleich: Verbindung (Conn), Bewertung in Balken (rate), Verlauf und letzter
    Stand (Tracker), Verwaltung (uplink_admin.py), Statusanzeige (status.js).
  Neues Modell: Klasse schreiben, in DRIVERS eintragen, Test mit nachgebautem Router dazu – fertig.

Sicherheit (wie bei Fully Kiosk):
  - Nur HTTPS. Das Zertifikat des Routers ist selbst signiert; FestPOS hält seinen SHA-256-Fingerabdruck fest,
    sobald jemand ab der Rolle Admin ihn in der Verwaltung bestätigt hat («Zertifikat übernehmen»).
  - Benutzer und Passwort gehen erst hinaus, wenn der Router genau dieses Zertifikat zeigt. Vorher, oder wenn ein
    anderes Gerät mit derselben Adresse antwortet, wird nichts gesendet.
  - Nur lesen. Auf dem Router genügt ein Benutzer mit Leserechten; FestPOS ändert dort nie etwas.
  - Nur ausgewählte Werte werden übernommen, geprüft und gekürzt. Kennungen der SIM-Karte (IMSI, ICCID) und des
    Modems (IMEI) bleiben weg.
Die Abfrage läuft im Hintergrund auf jedem Pi (alle INTERVAL Sekunden, mit Zeitgrenze). Bestellen und Drucken
hängen nicht davon ab.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import re
import ssl
import time
from collections import deque
from urllib.parse import quote

from . import rawhttp

log = logging.getLogger("festpos.uplink")

INTERVAL = 10.0              # Sekunden zwischen zwei Abfragen (zum Ausrichten der Antenne nicht zu träge)
SLOW_EVERY = 6               # Router-Angaben (Modell, Version, Laufzeit, SIM) nur jede 6. Abfrage = etwa 1x pro Minute
TIMEOUT = 8.0                # pro Abfrage, alle Anfragen zusammen
HISTORY = 180                # Messpunkte im Verlauf: 180 x 10 s = 30 Minuten
DATA_MAX_AGE = 5 * 60        # Router antwortet nicht mehr: letzte Werte so lange noch zeigen (mit Alter)
MAX_ANSWER = 256 * 1024
STATES = ("ok", "cert_new", "cert", "no_https", "auth", "rights", "iface", "unreachable", "timeout", "bad")
TRANSPORT = ("cert_new", "cert", "no_https", "auth", "unreachable", "timeout")   # betrifft die ganze Verbindung
PIN_RE = re.compile(r"^[0-9a-f]{64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,62}$")       # wie monitor.HOST_RE: IPv4 oder Hostname
IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")
USER_RE = re.compile(r"^[^\x00-\x1f\x7f:]{0,40}$")               # «:» trennt Benutzer und Passwort (HTTP Basic)

# Einheitliche Werte, die ein Treiber liefern kann (alle freiwillig):
FIELDS = {
    "registered": "im Netz angemeldet (bool)",
    "status": "Anmeldestatus im Wortlaut des Routers",
    "problem": "warum kein Netz, einer von PROBLEMS (vom Treiber erkannt)",
    "sim_status": "SIM-Status im Wortlaut des Routers (z.B. «ok», «SIM not inserted»)",
    "link": "Datenverbindung über Mobilfunk steht (bool)",
    "operator": "Anbieter (Name; bekannte Netzkennungen werden übersetzt)",
    "roaming": "Roaming (bool)",
    "tech": "Netz: 5G, LTE, 3G, 2G",
    "band": "Hauptband, z.B. B3", "bw": "Bandbreite in MHz", "ca": "weitere Bänder (Carrier Aggregation), Liste",
    "rssi": "dBm", "rsrp": "dBm", "rsrq": "dB", "sinr": "dB", "cqi": "0–15",
    "nr_rsrp": "5G: dBm", "nr_sinr": "5G: dB",
    "cell": "Funkzelle (Kennung)", "session": "Verbindung besteht seit (Sekunden)",
    "rx_bytes": "empfangen seit Start des Routers", "tx_bytes": "gesendet seit Start des Routers",
    "sim": "aktiver SIM-Steckplatz",
    "model": "Router-Modell", "version": "Router-Software", "uptime": "Router läuft seit (Sekunden)", "cpu": "Auslastung %",
    "raw": "Antwort des Routers ohne Kennungen von SIM und Modem – nur für die Verwaltung (Fehlersuche)",
}
# Gründe für «kein Netz», für alle Modelle gleich (Texte: common.uplink.p_*)
PROBLEMS = ("no_sim", "pin", "puk", "radio_off", "denied", "searching", "not_registered", "no_data")
IDENT_KEYS = ("imei", "imsi", "iccid", "uicc", "subscriber", "msisdn", "meid", "esn", "serial")   # nie übernehmen
SLOW_FIELDS = ("model", "version", "uptime", "cpu", "sim")

# Netzkennung (MCC+MNC) -> Name, falls der Router nur die Nummer meldet. Schweiz und Nachbarländer (Grenznähe).
OPERATORS = {
    "22801": "Swisscom", "22802": "Sunrise", "22803": "Salt", "22853": "Sunrise",
    "29501": "Swisscom FL", "29502": "Salt FL", "29505": "FL1",
    "26201": "Telekom", "26202": "Vodafone", "26203": "O2",
    "23201": "A1", "23203": "Magenta", "23210": "Drei",
    "20801": "Orange", "20810": "SFR", "20820": "Bouygues",
    "22201": "TIM", "22210": "Vodafone IT", "22288": "WINDTRE",
}


class UplinkError(Exception):
    """Abfrage gescheitert. state: siehe STATES; pin: Fingerabdruck, den der Router gezeigt hat (cert_new/cert)."""

    def __init__(self, state: str, text: str = "", pin: str | None = None):
        super().__init__(state)
        self.state, self.text, self.pin = state, text, pin

    def result(self) -> dict:
        out: dict = {"state": self.state}
        if self.text:
            out["text"] = self.text
        if self.pin:
            out["pin"] = self.pin
        return out


# -- Verbindung (für alle Modelle gleich) -------------------------------------------------------------------
class Conn:
    """HTTPS zum Router, eine Verbindung pro Anfrage. Zugangsdaten nur an das festgehaltene Zertifikat.
    Alle Anfragen einer Abfrage teilen sich eine Zeitgrenze."""

    def __init__(self, host: str, port: int, pin: str | None, user: str, password: str, timeout: float = TIMEOUT):
        self.host, self.port, self.pin, self.user, self.password = host, port, pin, user, password
        self.deadline = time.monotonic() + timeout

    def _left(self, cap: float | None = None) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0.05:
            raise UplinkError("timeout")
        return min(left, cap) if cap else left

    async def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        """-> (HTTP-Status, Inhalt). 401 und alle Verbindungsprobleme als UplinkError."""
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self._left(3))
        except asyncio.TimeoutError:
            raise UplinkError("timeout") from None
        except OSError:
            raise UplinkError("unreachable") from None
        try:
            try:
                await asyncio.wait_for(writer.start_tls(rawhttp.tls_client(), server_hostname=None), self._left(5))
            except asyncio.TimeoutError:
                raise UplinkError("timeout") from None
            except (ssl.SSLError, OSError, EOFError):
                raise UplinkError("no_https") from None     # spricht kein HTTPS – Zugangsdaten bleiben hier
            pin = rawhttp.peer_pin(writer)
            if not pin:
                raise UplinkError("no_https")
            if not self.pin:
                raise UplinkError("cert_new", pin=pin)       # noch nicht bestätigt: nichts senden
            if not hmac.compare_digest(pin, self.pin):
                log.warning("Router %s:%s zeigt ein anderes Zertifikat – Zugangsdaten nicht gesendet", self.host, self.port)
                raise UplinkError("cert", pin=pin)
            if not self.user or not self.password:
                raise UplinkError("auth")                    # nichts eingetragen: gar nicht erst senden
            data = json.dumps(body, separators=(",", ":")).encode() if body is not None else b""
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            head = (f"{method} {path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nAuthorization: Basic {token}\r\n"
                    f"User-Agent: FestPOS\r\nAccept: application/json\r\nConnection: close\r\n")
            if body is not None:
                head += f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
            writer.write(head.encode() + b"\r\n" + data)
            try:
                raw = await asyncio.wait_for(rawhttp.read_all(reader, writer, MAX_ANSWER), self._left())
            except asyncio.TimeoutError:
                raise UplinkError("timeout") from None
            if raw is None:
                raise UplinkError("bad", text="too large")
            status, _, content = rawhttp.parse(raw)
            if status == 401:
                raise UplinkError("auth")
            if status == 0:
                raise UplinkError("bad", text="no HTTP")
            return status, content
        except (ssl.SSLError, OSError, EOFError):
            raise UplinkError("unreachable") from None
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, ssl.SSLError, asyncio.TimeoutError):
                pass


async def fingerprint(host: str, port: int, timeout: float = TIMEOUT) -> str:
    """Fingerabdruck des Zertifikats, das der Router jetzt zeigt – ohne etwas anderes zu senden."""
    try:
        await Conn(host, port, None, "", "", timeout).request("GET", "/")
    except UplinkError as e:
        if e.state == "cert_new" and e.pin:
            return e.pin
        raise
    raise UplinkError("bad")


# -- Werte prüfen -----------------------------------------------------------------------------------------
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _num(v, lo: float, hi: float) -> float | None:
    """Zahl aus «-98», «-98dBm», -98 … – ausserhalb [lo, hi] ist es keine Messung."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        m = _NUM.search(str(v))
        if not m:
            return None
        x = float(m.group())
    return x if lo <= x <= hi else None


def _int(v, lo: float, hi: float) -> int | None:
    x = _num(v, lo, hi)
    return None if x is None else int(round(x))


def _dec(v, lo: float, hi: float) -> float | None:
    x = _num(v, lo, hi)
    return None if x is None else round(x, 1)


def _txt(v, n: int) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    return _CTRL.sub("", str(v)).strip().strip('"')[:n] or None


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false", "yes", "no"):
        return v.lower() in ("true", "yes")
    return None


_DUR = re.compile(r"(\d+)\s*([wdhms])")


def _dur(v) -> int | None:
    """RouterOS-Dauer «1w2d3h4m5s» oder «2d03:04:05» -> Sekunden."""
    s = _txt(v, 40)
    if not s:
        return None
    total, hms = 0, re.search(r"(\d+):(\d{2}):(\d{2})", s)
    if hms:
        total += int(hms[1]) * 3600 + int(hms[2]) * 60 + int(hms[3])
        s = s[:hms.start()]
    unit = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
    found = _DUR.findall(s)
    if not found and not hms:
        return None
    total += sum(int(n) * unit[u] for n, u in found)
    return total


def _operator(v) -> str | None:
    s = _txt(v, 30)
    return OPERATORS.get(s, s) if s else None


def _tech(v) -> str | None:
    s = _txt(v, 30)
    if not s:
        return None
    low = s.lower()
    if "5g" in low or re.search(r"\bnr\b", low):
        return "5G NSA" if "nsa" in low else "5G"
    if "lte" in low or "4g" in low:
        return "LTE"
    if any(x in low for x in ("3g", "umts", "hspa", "wcdma")):
        return "3G"
    if any(x in low for x in ("gsm", "edge", "gprs", "2g")):
        return "2G"
    return s[:12]


_BAND = re.compile(r"(?<![A-Za-z0-9])([Bn]\d{1,3})(?:@(\d+)\s*[Mm][Hh][Zz])?")


def _bands(primary, ca) -> dict:
    out: dict = {}
    m = _BAND.search(_txt(primary, 200) or "")
    if m:
        out["band"] = m[1]
        if m[2]:
            out["bw"] = int(m[2])
    if isinstance(ca, list):
        ca = ",".join(str(x) for x in ca)
    extra = [b for b, _ in _BAND.findall(_txt(ca, 400) or "")]
    if extra:
        out["ca"] = list(dict.fromkeys(extra))[:4]
    return out


def _registered(status: str | None) -> bool | None:
    if not status:
        return None
    low = status.lower()
    if "not" in low or "unreg" in low or "denied" in low or "search" in low:
        return False
    return "registered" in low or low in ("roaming", "home", "connected")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != []}


def raw_view(d: dict, limit: int = 60) -> dict:
    """Antwort des Routers für die Fehlersuche in der Verwaltung: ohne Kennungen (IMEI, IMSI, ICCID, Nummer …),
    nur einfache Werte, gekürzt."""
    out = {}
    for k, v in list(d.items())[:limit]:
        key = _txt(k, 40)
        if not key or key.startswith(".") or any(x in key.lower() for x in IDENT_KEYS):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        val = _txt(v, 100)
        if val is not None:
            out[key] = val
    return out


# -- Treiber ----------------------------------------------------------------------------------------------
class Driver:
    """Vorlage für ein Router-Modell."""
    key = ""                      # gespeichert in der Einstellung, nie ändern
    label = ""                    # so steht es in der Verwaltung (Produktname, wird nicht übersetzt)
    default_port = 443
    default_iface = ""            # Mobilfunk-Schnittstelle auf dem Router (leer = gibt es bei diesem Modell nicht)

    def setup(self, host: str) -> str:
        """Befehle/Schritte für die Einrichtung am Router (wird in der Verwaltung zum Kopieren gezeigt)."""
        return ""

    async def read(self, conn: Conn, iface: str, slow: bool) -> dict:
        """Werte abholen -> dict mit Schlüsseln aus FIELDS. slow: auch die selten nötigen (SLOW_FIELDS).
        Fehler als UplinkError."""
        raise NotImplementedError


class MikroTik(Driver):
    """MikroTik RouterOS 7 (z.B. LtAP mini LTE kit, Chateau LTE): REST-Schnittstelle über HTTPS (Dienst www-ssl)."""
    key = "mikrotik"
    label = "MikroTik (RouterOS 7)"
    default_port = 443
    default_iface = "lte1"

    def setup(self, host: str) -> str:
        parts = host.split(".")
        net = ".".join(parts[:3]) + ".0/24" if len(parts) == 4 and all(p.isdigit() for p in parts) else "192.168.55.0/24"
        return "\n".join([
            "/certificate add name=festpos-https common-name=festpos-router days-valid=3650 key-size=2048 "
            "key-usage=digital-signature,key-encipherment,tls-server",
            "/certificate sign festpos-https",
            f"/ip service set www-ssl certificate=festpos-https disabled=no address={net}",
            "/user group add name=festpos-lesen policy=read,api,rest-api",
            f"/user add name=festpos group=festpos-lesen address={net} password=PASSWORT",
            "/certificate print detail where name=festpos-https",
        ])

    @staticmethod
    async def _json(conn: Conn, method: str, path: str, body: dict | None = None):
        status, content = await conn.request(method, path, body)
        try:
            data = json.loads(content.decode("utf-8", "replace")) if content else None
        except ValueError:
            data = None
        if status == 200:
            if data is None:
                raise UplinkError("bad", text="no JSON")
            return data
        # Fehler: {"error": 400, "message": "Bad Request", "detail": "no such item"}
        detail = ""
        if isinstance(data, dict):
            detail = " ".join(str(data.get(k) or "") for k in ("message", "detail")).strip()
        detail = _CTRL.sub("", detail)[:120]
        low = detail.lower()
        if status == 403 or "permission" in low or "not allowed" in low:
            raise UplinkError("rights", text=detail)
        if "no such item" in low or "does not match any value" in low:
            raise UplinkError("iface", text=detail)
        raise UplinkError("bad", text=detail or f"HTTP {status}")

    @staticmethod
    def problem(m: dict, status: str | None) -> str | None:
        """Warum kein Netz? Aus SIM-/PIN-Status, Funk-Zustand und Anmeldung (Wortlaut von RouterOS)."""
        sim = " | ".join(str(m.get(k) or "") for k in ("pin-status", "sim-status", "status")).lower()
        if any(x in sim for x in ("not inserted", "no sim", "not present", "sim-not-inserted", "sim missing")):
            return "no_sim"
        if "puk" in sim:
            return "puk"
        if "pin required" in sim or "sim pin" in sim or "pin-required" in sim or "enter pin" in sim:
            return "pin"
        func = str(m.get("functionality") or "").strip().lower()
        if func and not func.startswith("full"):        # «minimum», «tx/rx RF circuit disabled» …
            return "radio_off"
        reg = (status or "").lower()
        if "denied" in reg:
            return "denied"
        if "search" in reg:
            return "searching"
        return None

    @staticmethod
    def monitor(m: dict) -> dict:
        """Antwort von «/interface/lte/monitor … once» -> einheitliche Werte (ohne IMSI/ICCID/IMEI)."""
        status = _txt(m.get("registration-status") or m.get("registration-state"), 30)
        roaming = _bool(m.get("roaming"))
        if roaming is None and status and "roaming" in status.lower():
            roaming = True
        return _clean({
            "problem": MikroTik.problem(m, status), "sim_status": _txt(m.get("pin-status"), 30),
            "registered": _registered(status), "status": status, "roaming": roaming,
            "operator": _operator(m.get("current-operator")), "tech": _tech(m.get("access-technology")),
            "rssi": _int(m.get("rssi"), -130, -10), "rsrp": _int(m.get("rsrp"), -150, -30),
            "rsrq": _dec(m.get("rsrq"), -40, 0), "sinr": _dec(m.get("sinr"), -30, 50), "cqi": _int(m.get("cqi"), 0, 15),
            "nr_rsrp": _int(m.get("nr-rsrp"), -160, -30), "nr_sinr": _dec(m.get("nr-sinr"), -30, 50),
            "cell": _txt(m.get("current-cellid"), 20), "session": _dur(m.get("session-uptime")),
            **_bands(m.get("primary-band"), m.get("ca-band")),
        })

    @staticmethod
    def interface(d: dict) -> dict:
        return _clean({"rx_bytes": _int(d.get("rx-byte"), 0, 1e15), "tx_bytes": _int(d.get("tx-byte"), 0, 1e15),
                       "link": _bool(d.get("running"))})

    @staticmethod
    def resource(d: dict) -> dict:
        version = _txt(d.get("version"), 30)
        return _clean({"model": _txt(d.get("board-name"), 40), "version": version.split(" ")[0] if version else None,
                       "uptime": _dur(d.get("uptime")), "cpu": _int(d.get("cpu-load"), 0, 100)})

    @staticmethod
    def settings(d: dict) -> dict:
        return _clean({"sim": _txt(d.get("sim-slot"), 12)})

    async def read(self, conn: Conn, iface: str, slow: bool) -> dict:
        mon = await self._json(conn, "POST", "/rest/interface/lte/monitor", {"numbers": iface, "once": ""})
        if isinstance(mon, list):
            mon = mon[0] if mon else None
        if not isinstance(mon, dict):
            raise UplinkError("bad", text="no monitor data")
        out = {**self.monitor(mon), "raw": raw_view(mon)}
        extra = [(f"/rest/interface/{quote(iface, safe='')}", self.interface)]
        if slow:
            extra += [("/rest/system/resource", self.resource), ("/rest/interface/lte/settings", self.settings)]
        for path, pick in extra:                    # Zusatzangaben: fehlen sie, zählt der Empfang trotzdem
            try:
                d = await self._json(conn, "GET", path)
            except UplinkError as e:
                if e.state in TRANSPORT:
                    break
                continue
            if isinstance(d, list):
                d = d[0] if d else {}
            if isinstance(d, dict):
                out.update(pick(d))
        return out


DRIVERS: dict[str, Driver] = {d.key: d for d in (MikroTik(),)}


# -- Einstellung --------------------------------------------------------------------------------------------
def parse_config(text: str | None, password: str | None) -> dict | None:
    """Einstellung «uplink» (JSON) + Passwort -> {kind, name, host, port, user, password, iface, pin, pin_at}
    oder None (kein Router eingerichtet)."""
    try:
        c = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(c, dict):
        return None
    kind = str(c.get("kind") or "")
    drv = DRIVERS.get(kind)
    host = str(c.get("host") or "").strip()
    if not drv or not HOST_RE.match(host):
        return None
    port = c.get("port")
    port = port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else drv.default_port
    iface = str(c.get("iface") or "")
    user = str(c.get("user") or "")
    pin = str(c.get("pin") or "")
    return {"kind": kind, "name": _txt(c.get("name"), 30), "host": host, "port": port,
            "user": user if USER_RE.match(user) else "", "password": password or "",
            "iface": iface if IFACE_RE.match(iface) else drv.default_iface,
            "pin": pin if PIN_RE.match(pin) else None, "pin_at": _txt(c.get("pin_at"), 10)}


async def poll(cfg: dict, slow: bool = True, timeout: float = TIMEOUT) -> dict:
    """Eine Abfrage -> {"state": "ok", "data": {…}} oder {"state": …, "text"?, "pin"?}."""
    drv = DRIVERS.get(cfg.get("kind", ""))
    if not drv:
        return {"state": "bad", "text": "unknown model"}
    conn = Conn(cfg["host"], cfg.get("port") or drv.default_port, cfg.get("pin"), cfg.get("user") or "",
                cfg.get("password") or "", timeout)
    try:
        data = await drv.read(conn, cfg.get("iface") or drv.default_iface, slow)
    except UplinkError as e:
        return e.result()
    except asyncio.CancelledError:
        raise
    except Exception:                               # noqa: BLE001 – unerwartete Antwort darf nichts stören
        log.exception("Router-Abfrage: unerwartete Antwort")
        return {"state": "bad"}
    return {"state": "ok", "data": data}


# -- Bewertung und Verlauf (für alle Modelle gleich) --------------------------------------------------------
WORDS = ("very_weak", "weak", "fair", "good", "excellent")


def rate(d: dict) -> dict:
    """Empfang in 0–4 Balken. Massgebend ist RSRP (Signalstärke, LTE/5G), begrenzt durch SINR (Störabstand);
    ohne RSRP (3G/2G) die RSSI. level: ok | wait (schwach) | bad (kein Netz, mit problem = Grund).
    Ohne Messwerte gilt nur «angemeldet» als gut – keine SIM, kein Netz o.ä. ist nie «in Ordnung»."""
    def none(problem: str) -> dict:
        return {"bars": 0, "level": "bad", "word": "none", "problem": problem}
    if d.get("problem") in PROBLEMS:
        return none(d["problem"])
    if d.get("registered") is False:
        return none("not_registered")
    rsrp, rssi = d.get("rsrp", d.get("nr_rsrp")), d.get("rssi")             # 5G ohne LTE-Anker: Werte von NR
    sinr = d.get("sinr", d.get("nr_sinr"))
    if rsrp is not None:
        bars = 4 if rsrp >= -80 else 3 if rsrp >= -90 else 2 if rsrp >= -100 else 1 if rsrp >= -110 else 0
    elif rssi is not None:
        bars = 4 if rssi >= -65 else 3 if rssi >= -75 else 2 if rssi >= -85 else 1 if rssi >= -95 else 0
    elif d.get("registered") is True and d.get("link") is not False:
        return {"bars": None, "level": "ok", "word": None}         # angemeldet, Router meldet nur keine Werte
    else:
        return none("not_registered")
    if sinr is not None:
        bars = min(bars, 1 if sinr < 0 else 2 if sinr < 5 else 3 if sinr < 13 else 4)
    if d.get("link") is False:                                       # Netz da, aber keine Datenverbindung (APN?)
        return {"bars": bars, "level": "bad", "word": WORDS[bars], "problem": "no_data"}
    return {"bars": bars, "level": "ok" if bars >= 2 else "wait", "word": WORDS[bars]}


class Tracker:
    """Letzter Stand und Verlauf eines Routers (lebt im Monitor, die Statusanzeige liest nur)."""

    def __init__(self):
        self.reset()

    def reset(self, key: tuple | None = None) -> None:
        self.key = key
        self.state: str | None = None
        self.at = 0.0
        self.text: str | None = None
        self.offered: str | None = None             # Fingerabdruck, den der Router zeigt (noch nicht übernommen)
        self.data: dict | None = None
        self.data_at = 0.0
        self.slow: dict = {}
        self.slow_at = 0.0
        self.raw: dict | None = None                # Antwort des Routers (ohne Kennungen), nur für die Verwaltung
        self.polls = 0
        self.hist: deque = deque(maxlen=HISTORY)

    @staticmethod
    def key_of(cfg: dict | None) -> tuple | None:
        return (cfg["kind"], cfg["host"], cfg["port"], cfg["iface"]) if cfg else None

    def want_slow(self, cfg: dict) -> bool:
        return self.key != self.key_of(cfg) or not self.slow or self.polls % SLOW_EVERY == 0

    def store(self, cfg: dict, res: dict, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.key != self.key_of(cfg):            # anderer Router: Verlauf gehört nicht dazu
            self.reset(self.key_of(cfg))
        self.polls += 1
        st = res.get("state")
        st = st if st in STATES else "bad"
        self.state, self.at, self.text = st, now, _txt(res.get("text"), 120)
        pin = res.get("pin")
        self.offered = pin if st in ("cert_new", "cert") and isinstance(pin, str) and PIN_RE.match(pin) else None
        if st == "ok":
            data = dict(res.get("data") or {})
            raw = data.pop("raw", None)
            self.raw = raw if isinstance(raw, dict) else None
            slow = {k: data.pop(k) for k in SLOW_FIELDS if k in data}
            if slow:
                self.slow, self.slow_at = slow, now
            self.data, self.data_at = data, now
            self.hist.append((now, data.get("rsrp", data.get("nr_rsrp")), data.get("sinr", data.get("nr_sinr"))))
        else:
            self.hist.append((now, None, None))
            if self.data and now - self.data_at > DATA_MAX_AGE:
                self.data = None

    def view(self, cfg: dict | None, admin: bool = False, now: float | None = None) -> dict | None:
        """Für Statusanzeige und Verwaltung. None = kein Router eingerichtet."""
        if not cfg:
            return None
        now = time.time() if now is None else now
        drv = DRIVERS.get(cfg["kind"])
        out: dict = {"name": cfg.get("name"), "model": drv.label if drv else cfg["kind"], "host": cfg["host"],
                     "interval": INTERVAL, "state": "pending"}
        if self.key != self.key_of(cfg) or not self.at:
            return out
        out.update(state=self.state, seconds=int(now - self.at))
        if self.text:
            out["text"] = self.text
        if self.data:
            d = {**self.data, **self.slow}
            if "uptime" in self.slow:
                d["uptime"] = self.slow["uptime"] + int(now - self.slow_at)
            out.update(data=d, data_seconds=int(now - self.data_at))
            if self.state == "ok":
                out["rating"] = rate(self.data)
        span = HISTORY * INTERVAL * 1.2
        out["hist"] = [[int(now - t), r, s] for t, r, s in self.hist if now - t <= span]
        if admin and self.offered:
            out["offered_pin"] = self.offered
        if admin and self.raw and self.data:
            out["raw"] = self.raw
        return out
