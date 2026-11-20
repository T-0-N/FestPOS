"""Version 2026.10.10-11: Akkustand der Handys (über die App) und Zusatzinfos von Fully Kiosk – nur über HTTPS mit
dem selbst erstellten Zertifikat. Ein nachgebauter Fully-Server prüft vor allem eines: das Passwort verlässt den
Pi nie unverschlüsselt und nie zu einem Gerät mit fremdem Zertifikat."""
from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path

import pytest

from tests.test_api import AUTH

PASSWORD = "Fully-Geheim 1&2"
INFO = {"deviceName": "XCover", "batteryLevel": 64, "isPlugged": False, "plugged": False, "batteryTemperature": 31,
        "screenOn": True, "wifiSignalLevel": 7, "SSID": "\"FestPOS\"", "kioskMode": True, "kioskLocked": True,
        "foregroundApp": "de.ozerov.fully", "currentPage": "https://192.168.55.10/", "internalStorageFreeSpace": 12_300_000_000,
        "internalStorageTotalSpace": 64_000_000_000, "ramFreeMemory": 900_000_000, "appVersionName": "1.56.2",
        "androidVersion": "14", "deviceModel": "SM-G556B", "deviceManufacturer": "samsung", "isLicensed": True,
        "deviceID": "abc", "serial": "SOLL-NICHT-UEBERNOMMEN-WERDEN"}


# Auszug aus listSettings eines echten XCover (Werte geändert): Ja/Nein, Zahlen, Text, Geheimes, pro Handy
FULLY_SETTINGS = {"kioskMode": True, "enablePullToRefresh": False, "timeToScreenOffV2": "500", "actionBarFgColor": -1,
                  "startURL": "https://192.168.55.10", "deviceName": "XCover101", "actionBarTitle": "FEST | 101",
                  "clientCaUrl": "file:///storage/emulated/0/FestPOS/alt.p12", "clientCaPassword": "123456",
                  "kioskPinEnc": "enc_xyz", "remoteAdminPasswordEnc": "enc_abc", "mqttBrokerPassword": "geheim",
                  "volumeLicenseKey": "LIZENZ", "wifiConfiguration": "{\"psk\":\"x\"}", "remoteAdmin": True,
                  "remoteAdminLan": True, "isRunning": True, "lastVersionInfo": "1.57.1"}


def _pem_files(p12: bytes) -> tuple[str, str]:
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
    key, cert, _ = pkcs12.load_key_and_certificates(p12, b"fully")
    d = Path(tempfile.mkdtemp())
    (d / "c.pem").write_bytes(cert.public_bytes(Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()))
    return str(d / "c.pem"), str(d / "k.pem")


class FakeFully:
    """Nachgebautes Fully (Remote Admin) in einem eigenen Thread. mode:
    https – HTTPS mit dem Zertifikat aus p12 | http – unverschlüsselt, antwortet wie ein echter Webserver |
    silent – nimmt die Verbindung an und schweigt | mute – HTTPS, aber keine Antwort auf die Anfrage |
    plain – frisch eingerichtetes Fully: Fernverwaltung ohne Zertifikat, also nur HTTP"""

    def __init__(self, mode: str = "https", p12: bytes | None = None, answer: bytes | None = None, port: int = 0):
        self.want_port = port                               # gleicher Port wie ein Vorgänger (Fully neu gestartet)
        self.mode, self.p12, self.received, self.commands = mode, p12, bytearray(), []
        self.kiosk_locked = True
        self.licensed = True
        self.password = PASSWORD                            # Remote-Admin-Passwort dieses Fully
        self.take_password = True                           # False: setzt remoteAdminPassword nicht (Fehlerfall)
        self.files, self.settings, self.cas = {}, {}, []      # Einrichten: entpackt, gesetzt, installiert
        self.zip_urls: list[str] = []
        self.dirs = {"/storage/emulated/0"}                   # frisch eingerichtet: nur der oberste Ordner
        self.fetch = True                                   # False: ZIP nicht abholen (z.B. Pi nicht erreichbar)
        self.delay = 0.0                                    # Sekunden bis zur Antwort (Fully überlegt manchmal)
        self.pin_error: str | None = None                   # Kiosk-PIN: Fehler melden (…)
        self.pin_takes = True                               # … und ihn trotzdem übernehmen
        self.answer = answer
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def _load_zip(self, url: str, directory: str) -> None:
        """loadZipFile: Fully lädt im Hintergrund und entpackt (Unterordner aus der ZIP legt es an). Hier direkt aus
        dem Speicher der App (wie über festpos-redirect.py, das die Einmal-ZIP lokal bei der App abholt)."""
        import io
        import zipfile
        from app import fully
        self.zip_urls.append(url)
        if not self.fetch:
            return
        got = fully.zips.take(url.rsplit("/", 1)[1].removesuffix(".zip"))
        if got:
            with zipfile.ZipFile(io.BytesIO(got[0])) as z:
                for n in z.namelist():
                    if n.endswith("/"):
                        self.dirs.add(f"{directory}/{n.rstrip('/')}")
                    else:
                        self.files[f"{directory}/{n}"] = z.read(n)
                        self.dirs.add(f"{directory}/{n}".rsplit("/", 1)[0])

    def _reply(self, path: bytes, data: bytes = b"") -> bytes:
        from urllib.parse import parse_qs, urlsplit
        if self.answer is not None:
            return self.answer
        q = {k: v[0] for k, v in parse_qs(urlsplit(path.split(b" ")[1].decode()).query).items()}
        self.commands.append(q.get("cmd", ""))
        if q.get("password") != self.password:
            body = json.dumps({"status": "Error", "statustext": "Please login"}).encode()
        elif q.get("cmd") == "loadZipFile" and q.get("dir") not in self.dirs:      # wie Fully: Ordner muss es geben
            body = json.dumps({"status": "Error", "statustext": "Output folder is not writable"}).encode()
        elif q.get("cmd") == "loadZipFile":
            threading.Timer(0.2, self._load_zip, (q["url"], q["dir"])).start()   # im Hintergrund, wie Fully
            body = json.dumps({"status": "OK", "statustext": "Loading and unzipping in the background"}).encode()
        elif q.get("cmd") == "setStringSetting" and q.get("key") == "kioskPin":   # Fully zeigt ihn nur verschlüsselt
            if self.pin_takes:
                self.settings["kioskPinEnc"] = "enc_" + hashlib.sha256(q["value"].encode()).hexdigest()[:10]
            body = json.dumps({"status": "Error" if self.pin_error else "OK",
                               "statustext": self.pin_error or "Saved"}).encode()
        elif q.get("cmd") == "setStringSetting" and q.get("key") == "remoteAdminPassword":
            if self.take_password:
                self.password = q["value"]
            body = json.dumps({"status": "OK", "statustext": "Saved"}).encode()
        elif q.get("cmd") == "setStringSetting":
            self.settings[q["key"]] = q["value"]
            body = json.dumps({"status": "OK", "statustext": "Saved"}).encode()
        elif q.get("cmd") == "setBooleanSetting":
            self.settings[q["key"]] = q["value"] == "true"
            body = json.dumps({"status": "OK", "statustext": "Saved"}).encode()
        elif q.get("cmd") == "listSettings":
            body = json.dumps({**FULLY_SETTINGS, **self.settings}).encode()
        elif q.get("cmd") == "installUserCa":
            self.cas.append(q["url"])
            body = json.dumps({"status": "OK", "statustext": "Certificate installed"}).encode()
        elif b"cmd=shutdownDevice" in path:                 # Handy geht aus, bevor Fully antwortet
            return b""
        elif b"cmd=lockKiosk" in path or b"cmd=unlockKiosk" in path:
            self.kiosk_locked = b"cmd=lockKiosk" in path
            body = json.dumps({"status": "OK", "statustext": "Kiosk " + ("locked" if self.kiosk_locked else "unlocked")}).encode()
        elif b"cmd=rebootDevice" in path:                   # wie Fully ohne Geräteinhaber/Root
            body = json.dumps({"status": "Error", "statustext": "Missing device owner, system or root rights to reboot the device"}).encode()
        elif b"cmd=deviceInfo" not in path:
            body = json.dumps({"status": "OK", "statustext": "Restarting app..."}).encode()
        else:
            body = json.dumps({**INFO, "kioskMode": self.settings.get("kioskMode", INFO["kioskMode"]), "kioskLocked": self.kiosk_locked,
                               "isLicensed": self.licensed or "volumeLicenseKey" in self.settings}).encode()
        return b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body)

    async def _handle(self, reader, writer):
        try:
            if self.mode == "http":
                data = await reader.read(4096)                 # TLS-Begrüssung statt HTTP: wie NanoHTTPD -> 400
                self.received += data
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            elif self.mode == "silent":
                self.received += await reader.read(4096)
                await asyncio.sleep(10)
            else:
                line = await reader.readexactly(1) if self.mode == "plain" else b""
                if line == b"\x16":                            # TLS-Begrüssung an ein Fully ohne Zertifikat
                    self.received += line + await reader.read(4096)
                    return
                line += await reader.readline()
                self.received += line
                length = 0
                while True:
                    h = await reader.readline()
                    self.received += h
                    if h.lower().startswith(b"content-length:"):
                        length = int(h.split(b":")[1])
                    if h in (b"\r\n", b""):
                        break
                data = await reader.readexactly(length) if length else b""
                self.received += data
                if line and self.mode in ("https", "plain") and line.startswith((b"GET ", b"POST ")):
                    if self.delay:
                        await asyncio.sleep(self.delay)
                    writer.write(self._reply(line, data))
                    await writer.drain()
                elif line:
                    await asyncio.sleep(10)
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        ctx = None
        if self.mode in ("https", "mute"):
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*_pem_files(self.p12))
        self.srv = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", self.want_port, ssl=ctx,
                                                                                reuse_address=True))
        self.port = self.srv.sockets[0].getsockname()[1]
        self.ready.set()
        self.loop.run_forever()
        self.loop.close()

    def stop(self):
        async def shutdown():
            self.srv.close()
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def _cert(ip="127.0.0.1"):
    from app import fully
    return fully.make_cert("XCover 9", ip)


# ------------------------------------------------------------------ Zertifikat
def test_certificate_for_fully():
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
    p12, pin, until = _cert("192.168.55.101")
    key, cert, extra = pkcs12.load_key_and_certificates(p12, b"fully")        # Kennwort, das Fully verlangt
    assert isinstance(key, rsa.RSAPrivateKey) and key.key_size == 2048 and not extra
    assert hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest() == pin
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(x) for x in san.get_values_for_type(x509.IPAddress)] == ["192.168.55.101"]
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert until >= time.strftime("%Y", time.gmtime(time.time() + 9 * 365 * 86400))
    assert _cert("192.168.55.101")[1] != pin                                 # jedes Mal ein neuer Schlüssel


# ------------------------------------------------------------------ Antwort deuten (ohne Netzwerk)
def _http(body: bytes, status: str = "200 OK", extra: str = "") -> bytes:
    return f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n{extra}\r\n".encode() + body


def test_interpret_answers():
    from app.fully import interpret
    ok = interpret(_http(json.dumps(INFO).encode()))
    assert ok["state"] == "ok"
    d = ok["data"]
    assert d["battery"] == 64 and d["charging"] is False and d["temp"] == 31 and d["screen_on"] is True
    assert d["ssid"] == "FestPOS" and d["kiosk"] is True and d["fully_version"] == "1.56.2" and d["model"] == "samsung SM-G556B"
    assert "serial" not in json.dumps(d) and "SOLL" not in json.dumps(d)            # nur bekannte Werte
    chunked = json.dumps({"batteryLevel": 5, "isPlugged": True}).encode()
    raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"%x\r\n" % 10 + chunked[:10] + b"\r\n" + \
        b"%x\r\n" % (len(chunked) - 10) + chunked[10:] + b"\r\n0\r\n\r\n"
    assert interpret(raw) == {"state": "ok", "data": {"battery": 5, "charging": True}}
    assert interpret(_http(b'{"status":"Error","statustext":"Please login"}'))["state"] == "auth"
    assert interpret(_http(b"<html><form>Password: <input type=password></form></html>"))["state"] == "auth"
    assert interpret(_http(b"", "401 Unauthorized"))["state"] == "auth"
    assert interpret(_http(b"{}", "500 Internal Server Error"))["state"] == "bad"
    assert interpret(_http(b'{"something": 1}'))["state"] == "bad"
    assert interpret(b"garbage")["state"] == "bad"
    weird = interpret(_http(json.dumps({"batteryLevel": 250, "isPlugged": "yes", "screenOn": "true",
                                        "appVersionName": "1.5\x1b[31m" + "x" * 99, "batteryTemperature": "nan"}).encode()))
    assert weird["state"] == "ok"
    assert "battery" not in weird["data"] and "charging" not in weird["data"] and "temp" not in weird["data"]
    assert weird["data"]["screen_on"] is True and "\x1b" not in weird["data"]["fully_version"]
    assert len(weird["data"]["fully_version"]) <= 20
    # Android 15 ohne Standort-Recht: kein Netzname, nur die Signalstärke
    a15 = interpret(_http(json.dumps({**INFO, "SSID": "<unknown ssid>"}).encode()))["data"]
    assert "ssid" not in a15 and a15["wifi"] == 7


def test_interpret_command_answers():
    from app.fully import interpret_command
    ans = lambda st, text: interpret_command(_http(json.dumps({"status": st, "statustext": text}).encode()))  # noqa: E731
    assert ans("OK", "Rebooting...") == {"state": "ok", "text": "Rebooting..."}
    assert ans("Error", "Missing device owner, system or root rights to reboot the device")["state"] == "no_rights"
    assert ans("Error", "Rooted devices only")["state"] == "no_rights"
    assert ans("Error", "Kiosk mode not enabled") == {"state": "refused", "text": "Kiosk mode not enabled"}
    assert ans("Error", "Please login")["state"] == "auth"


# ------------------------------------------------------------------ Abfrage über das Netzwerk
def _query(port, pin, password=PASSWORD, timeout=2.0):
    from app.fully import query
    return asyncio.run(query("127.0.0.1", port, pin, password, timeout=timeout))


def test_https_with_matching_certificate():
    p12, pin, _ = _cert()
    srv = FakeFully("https", p12)
    try:
        res = _query(srv.port, pin)
        assert res["state"] == "ok" and res["data"]["battery"] == 64
        assert b"cmd=deviceInfo" in srv.received and b"type=json" in srv.received
        assert _query(srv.port, pin, "falsch")["state"] == "auth"
    finally:
        srv.stop()


def test_password_never_sent_to_a_different_certificate():
    p12, pin, _ = _cert()
    other_p12, _, _ = _cert()                       # z.B. ein anderes Gerät mit derselben IP
    srv = FakeFully("https", other_p12)
    try:
        assert _query(srv.port, pin)["state"] == "cert"
        assert b"password" not in srv.received and not srv.received.strip(b"\r\n")
    finally:
        srv.stop()


@pytest.mark.parametrize("mode", ["http", "silent"])
def test_password_never_sent_without_https(mode):
    _, pin, _ = _cert()
    srv = FakeFully(mode)
    try:
        t0 = time.monotonic()
        assert _query(srv.port, pin)["state"] == "no_https"
        assert time.monotonic() - t0 < 2.5
        assert srv.received                                        # Verbindung kam an …
        assert b"password" not in srv.received and PASSWORD.encode() not in srv.received   # … das Passwort nie
        assert b"GET " not in srv.received
    finally:
        srv.stop()


def test_unreachable_and_no_answer():
    p12, pin, _ = _cert()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed = s.getsockname()[1]
    s.close()
    assert _query(closed, pin)["state"] == "unreachable"
    srv = FakeFully("mute", p12)                                   # HTTPS ok, aber keine Antwort (hängt)
    try:
        t0 = time.monotonic()
        assert _query(srv.port, pin)["state"] == "timeout"
        assert time.monotonic() - t0 < 2.5
    finally:
        srv.stop()
    assert _query(closed, "nicht-hex")["state"] == "cert" and _query(closed, pin, "")["state"] == "auth"


def test_monitor_keeps_last_values_while_phone_sleeps(monkeypatch):
    from app import fully, monitor as mon
    m = mon.Monitor()
    answers = iter([{"state": "ok", "data": {"battery": 80}}, {"state": "timeout"}])

    async def fake_query(ip, port, pin, password, timeout=4.0):
        return next(answers)
    monkeypatch.setattr(fully, "query", fake_query)
    cfg = {"password": "x", "port": 2323, "targets": [{"name": "XCover 1", "ip": "10.0.0.1", "pin": "a" * 64}]}
    asyncio.run(m.poll_fully(cfg))
    asyncio.run(m.poll_fully(cfg))
    v = m.fully_view()["10.0.0.1"]
    assert v["state"] == "timeout" and v["data"]["battery"] == 80 and v["data_seconds"] >= 0
    asyncio.run(m.poll_fully({**cfg, "targets": []}))
    assert m.fully_view() == {}                                    # nicht mehr eingerichtet: vergessen
    asyncio.run(m.poll_fully({**cfg, "password": ""}))             # ohne Passwort: niemand wird gefragt
    assert m.fully_view() == {}


# ------------------------------------------------------------------ Akku über die App
def test_battery_from_app_heartbeat(client):
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "Akku%20Handy", "X-Festpos-Battery": "42",
                                          "X-Festpos-Charging": "0"})
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "Lade%20Handy", "X-Festpos-Battery": "99.6",
                                          "X-Festpos-Charging": "1"})
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "Alt%20Handy", "X-Festpos-Battery": "abc"})
    dev = {d["name"]: d for d in client.get("/api/status", auth=AUTH).json()["devices"]}
    assert dev["Akku Handy"]["battery"] == 42 and dev["Akku Handy"]["charging"] is False
    assert dev["Akku Handy"]["battery_src"] == "app"
    assert dev["Lade Handy"]["battery"] == 100 and dev["Lade Handy"]["charging"] is True
    assert "battery" not in dev["Alt Handy"]
    # ohne neue Meldung bleibt der letzte Stand (z.B. Browser ohne Akku-Schnittstelle bei einer Anfrage)
    client.get("/api/bootstrap", headers={"X-Festpos-Device": "Akku%20Handy"})
    dev = {d["name"]: d for d in client.get("/api/status", auth=AUTH).json()["devices"]}
    assert dev["Akku Handy"]["battery"] == 42


# ------------------------------------------------------------------ Verwaltung: einrichten, prüfen, vergessen
def test_fully_setup_check_and_status(client):
    from app import core
    phones = [{"name": "XCover 9", "kind": "phone", "ip": "127.0.0.1", "port": None},
              {"name": "Switch", "kind": "net", "ip": "10.0.0.2", "port": None}]
    assert client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones}).status_code == 200
    # Passwort: nur setzen, nie zurückgeben
    s = client.put("/api/admin/settings", auth=AUTH, json={"fully_password": PASSWORD}).json()
    assert s["fully_password_set"] is True and "fully_password" not in s and PASSWORD not in json.dumps(s)
    assert client.put("/api/admin/settings", auth=AUTH, json={"fully_password": ""}).json()["fully_password_set"]
    assert core.rt.store.setting("fully_password") == PASSWORD                    # leer = unverändert
    assert client.put("/api/admin/settings", auth=AUTH, json={"fully_password": "a\nb"}).status_code == 422
    # Einrichten: nur für eingetragene Handys
    r = client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "10.9.9.9"})
    assert r.status_code == 404 and "Überwachte Geräte" in r.json()["detail"]
    assert client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "10.0.0.2"}).status_code == 404   # Switch
    assert client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"}).status_code == 422
    r = client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.1"})
    assert r.status_code == 200 and 'filename="fully-remote-admin-ca.p12"' in r.headers["content-disposition"]
    p12 = r.content
    stored = json.loads(core.rt.store.setting("monitor_devices"))
    pin = stored[0]["fully_pin"]
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
    assert hashlib.sha256(pkcs12.load_key_and_certificates(p12, b"fully")[1].public_bytes(Encoding.DER)).hexdigest() == pin
    assert "BEGIN" not in core.rt.store.setting("monitor_devices")              # kein Schlüssel in der Datenbank
    # Liste neu speichern (die Verwaltung schickt keine Zertifikate mit): bleibt; auch mit neuer IP, gleicher Name
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones})
    assert json.loads(core.rt.store.setting("monitor_devices"))[0]["fully_pin"] == pin
    moved = [{**phones[0], "ip": "127.0.0.2"}, phones[1]]
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": moved})
    assert json.loads(core.rt.store.setting("monitor_devices"))[0]["fully_pin"] == pin
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones})
    forged = [{**phones[0], "fully_pin": "b" * 64}, phones[1]]                 # mitgeschickt: wird ignoriert
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": forged})
    assert json.loads(core.rt.store.setting("monitor_devices"))[0]["fully_pin"] == pin
    # Prüfen gegen ein nachgebautes Fully mit diesem Zertifikat
    srv = FakeFully("https", p12)
    try:
        client.put("/api/admin/settings", auth=AUTH, json={"fully_port": srv.port})
        res = client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert res["state"] == "ok" and res["data"]["battery"] == 64 and res["data"]["kiosk"] is True
        over = client.get("/api/admin/fully", auth=AUTH).json()
        assert over["password_set"] and over["port"] == srv.port and over["results"]["127.0.0.1"]["state"] == "ok"
        dev = client.get("/api/status", auth=AUTH).json()["devices"]
        assert dev[0]["name"] == "XCover 9" and dev[0]["battery"] == 64 and dev[0]["battery_src"] == "fully"
        f = dev[0]["fully"]
        assert (f["state"], f["temp"], f["screen_on"]) == ("ok", 31, True)
        assert f["data"]["android"] == "14" and f["data"]["battery"] == 64     # Details beim Antippen
        # neues Zertifikat: das alte (noch auf dem «Handy») passt nicht mehr – Passwort bleibt hier
        client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.1"})
        before = bytes(srv.received)
        assert client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"}).json()["state"] == "cert"
        assert PASSWORD.encode() not in bytes(srv.received)[len(before):] and b"password" not in bytes(srv.received)[len(before):]
    finally:
        srv.stop()
    # Vergessen
    assert client.post("/api/admin/fully/forget", auth=AUTH, json={"ip": "127.0.0.1"}).json() == {"ok": True}
    assert "fully_pin" not in json.loads(core.rt.store.setting("monitor_devices"))[0]
    assert "127.0.0.1" not in client.get("/api/admin/fully", auth=AUTH).json()["results"]
    assert client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"}).status_code == 422
    assert client.post("/api/admin/fully/cert", json={"ip": "127.0.0.1"}).status_code == 401    # nur Verwaltung


# ------------------------------------------------------------------ Einrichten über Fully: CA und Gerätezertifikat
def test_setup_only_known_commands_and_settings():
    from app import fully
    run = lambda **kw: asyncio.run(fully.setup_command("127.0.0.1", 1, "a" * 64, "x", **kw))  # noqa: E731
    for bad in ({"cmd": "wipeDevice"}, {"cmd": "loadURL", "url": "https://boese"},
                {"cmd": "setStringSetting", "key": "remoteAdminPassword", "value": "x"},
                {"cmd": "setStringSetting", "key": "remoteAdminPasswordEnc", "value": "x"},
                {"cmd": "setStringSetting", "key": "clientCaUrls", "value": "x"},
                {"cmd": "setBooleanSetting", "key": "deviceName", "value": "true"},
                {"cmd": "setStringSetting", "key": "a&cmd=wipeDevice", "value": "x"}):
        with pytest.raises(ValueError):
            run(**bad)
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(fully.make_zip({"a.p12": b"P12", "festpos-ca.crt": b"CA"}))) as z:
        assert z.namelist() == ["a.p12", "festpos-ca.crt"] and z.read("a.p12") == b"P12"


def test_zip_only_for_the_pi_itself_and_only_once(client, monkeypatch):
    from app import fully
    token = fully.zips.put(b"ZIP", "festpos.zip")
    assert client.get(f"/api/device-cert/fully/{token}").status_code == 403       # nur festpos-redirect.py
    monkeypatch.setattr(fully, "LOCAL_HOSTS", {"testclient"})
    r = client.get(f"/api/device-cert/fully/{token}")
    assert r.status_code == 200 and r.content == b"ZIP" and r.headers["content-type"] == "application/zip"
    assert client.get(f"/api/device-cert/fully/{token}").status_code == 404       # nur einmal


def test_redirect_hands_out_the_zip(monkeypatch):
    """deploy/festpos-redirect.py: /fully/<Code>.zip holt die ZIP lokal bei der App, alles andere wie bisher."""
    import http.client
    import importlib.util
    from http.server import ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location("festpos_redirect", Path(__file__).parent.parent / "deploy" / "festpos-redirect.py")
    red = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(red)
    asked = []
    monkeypatch.setattr(red, "fetch_zip", lambda token: asked.append(token) or (b"PK-ZIP" if token == "a" * 32 else None))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), red.Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    def get(path):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        out = (r.status, r.getheader("Content-Type"), r.read())
        c.close()
        return out
    try:
        assert get(f"/fully/{'a' * 32}.zip") == (200, "application/zip", b"PK-ZIP")
        assert get(f"/fully/{'b' * 32}.zip")[0] == 404                 # unbekannt oder schon abgeholt
        assert get("/fully/kurz.zip")[0] == 301 and get("/fully/../x.zip")[0] == 301   # wie jede andere Adresse
        assert asked == ["a" * 32, "b" * 32]                       # ungültige Codes fragen die App gar nicht
    finally:
        srv.shutdown()


def _fully_phone(client, tmp_path, monkeypatch):
    """Handy 127.0.0.1 mit eingerichtetem Fully, Server-CA und Geräte-CA -> (nachgebautes Fully, CA-Datei)."""
    from app import core, fully
    from tests.test_api import make_device_ca
    monkeypatch.setattr(fully, "FETCH_WAIT", 2.0)
    monkeypatch.setattr(fully, "UNZIP_WAIT", 0.1)
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(core.Config, "device_ca", str(crt))
    monkeypatch.setattr(core.Config, "device_ca_key", str(key))
    ca = tmp_path / "ca.crt"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(core.Config, "cafile", str(ca))
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [
        {"name": "XCover 9", "kind": "phone", "ip": "127.0.0.1", "port": None}], "fully_password": PASSWORD})
    srv = FakeFully("https", client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.1"}).content)
    client.put("/api/admin/settings", auth=AUTH, json={"fully_port": srv.port})
    return srv, ca


def test_install_ca_via_fully(client, tmp_path, monkeypatch):
    from app import core
    srv, ca = _fully_phone(client, tmp_path, monkeypatch)
    try:
        r = client.post("/api/admin/fully/ca", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["state"] == "ok" and r["step"] == "install"
        assert srv.files == {"/storage/emulated/0/FestPOS/festpos-ca.crt": ca.read_bytes()}
        assert srv.cas == ["file:///storage/emulated/0/FestPOS/festpos-ca.crt"]
        assert srv.commands == ["deviceInfo", "loadZipFile", "installUserCa"]          # vorher geprüft
        assert srv.zip_urls[0].startswith("http://testserver/fully/") and srv.zip_urls[0].endswith(".zip")
        # Fully holt die ZIP nicht ab (z.B. Pi nicht erreichbar): nicht installiert, Adresse danach ungültig
        srv.fetch = False
        r = client.post("/api/admin/fully/ca", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert (r["step"], r["state"]) == ("download", "not_fetched") and len(srv.cas) == 1
        from app import fully
        assert not fully.zips.pending(srv.zip_urls[-1].rsplit("/", 1)[1].removesuffix(".zip"))
    finally:
        srv.stop()
    # Fully antwortet nicht: nichts geht hinaus, klare Meldung
    r = client.post("/api/admin/fully/ca", auth=AUTH, json={"ip": "127.0.0.1"})
    assert r.status_code == 409 and "nicht bereit" in r.json()["detail"]
    monkeypatch.setattr(core.Config, "cafile", str(tmp_path / "fehlt.crt"))
    assert client.post("/api/admin/fully/ca", auth=AUTH, json={"ip": "127.0.0.1"}).status_code == 503
    assert client.post("/api/admin/fully/ca", json={"ip": "127.0.0.1"}).status_code == 401


def test_device_cert_via_fully(client, tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.serialization import pkcs12
    from app import core
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    rows = lambda: core.rt.store.query("SELECT serial, replaces, revoked_at FROM device_certs WHERE name='XCover 9' ORDER BY id")  # noqa: E731
    try:
        r = client.post("/api/admin/fully/device-cert", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 9"}).json()
        assert (r["step"], r["state"], r["renewed"], r["name"]) == ("restart", "ok", False, "XCover 9")
        url = srv.settings["clientCaUrl"]
        assert url.startswith("file:///storage/emulated/0/FestPOS/festpos-xcover-9-") and url.endswith(".p12")
        pw = srv.settings["clientCaPassword"]
        assert len(pw) >= 24                                                         # nicht 6 Ziffern zum Abtippen
        key, cert, _ = pkcs12.load_key_and_certificates(srv.files[url[len("file://"):]], pw.encode())
        assert "CN=XCover 9" in cert.subject.rfc4514_string() and key is not None
        assert srv.commands[-1] == "restartApp"
        first = rows()
        assert len(first) == 1 and first[0]["replaces"] is None
        assert pw not in json.dumps(core.rt.store.query("SELECT * FROM device_certs"))   # Kennwort nicht gespeichert
        # gleicher Name: erneuern – das neue ersetzt das alte (gesperrt, sobald das Handy das neue benutzt)
        r = client.post("/api/admin/fully/device-cert", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 9"}).json()
        assert r["renewed"] is True and rows()[1]["replaces"] == first[0]["serial"]
        assert srv.settings["clientCaPassword"] != pw
        # Fully holt die ZIP nicht ab: kein neues Zertifikat bleibt zurück, Fully unverändert
        before = dict(srv.settings)
        srv.fetch = False
        r = client.post("/api/admin/fully/device-cert", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 9"}).json()
        assert (r["step"], r["state"]) == ("download", "not_fetched") and len(rows()) == 2 and srv.settings == before
    finally:
        srv.stop()
    assert client.post("/api/admin/fully/device-cert", auth=AUTH, json={"ip": "127.0.0.1", "name": "a/b"}).status_code == 422
    assert client.post("/api/admin/fully/device-cert", json={"ip": "127.0.0.1", "name": "X"}).status_code == 401


# ------------------------------------------------------------------ Vorlage: gleiche Einstellungen auf allen Handys
def test_template_keeps_secrets_and_per_phone_values_out():
    from app import fully
    t = fully.clean_template({**FULLY_SETTINGS, "remoteAdmin": False, "nested": {"a": 1}, "lang": ["de"],
                              "bad key": True, "longText": "x" * 5000, "nichts": None})
    assert t["kioskMode"] is True and t["timeToScreenOffV2"] == "500" and t["actionBarFgColor"] == -1
    assert t["startURL"] == "https://192.168.55.10"
    for k in ("deviceName", "actionBarTitle", "clientCaUrl", "clientCaPassword", "kioskPinEnc", "remoteAdminPasswordEnc",
              "mqttBrokerPassword", "volumeLicenseKey", "wifiConfiguration", "isRunning", "lastVersionInfo",
              "nested", "lang", "bad key", "longText", "nichts"):
        assert k not in t, k
    assert t["remoteAdmin"] is True and t["remoteAdminLan"] is True              # nie aussperren
    assert "123456" not in json.dumps(t) and "geheim" not in json.dumps(t) and "LIZENZ" not in json.dumps(t)
    # vergleichen: Zahl als Text ist gleich, unbekannte Einstellungen (ältere Fully-Version) werden nicht gesetzt
    d = fully.differences({"timeToScreenOffV2": 500, "kioskMode": True, "neuInFully2": True, "enablePullToRefresh": True},
                          {"timeToScreenOffV2": "500", "kioskMode": True, "enablePullToRefresh": False})
    assert d == {"enablePullToRefresh": True}


def test_template_from_phone_compare_and_apply(client, tmp_path, monkeypatch):
    from app import core
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    try:
        assert client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1"}).status_code == 422  # keine Vorlage
        r = client.post("/api/admin/fully/template/from", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["state"] == "ok" and r["source"] == "XCover 9" and r["count"] >= 5
        stored = core.rt.store.setting("fully_template")
        assert "123456" not in stored and "enc_" not in stored and "LIZENZ" not in stored    # nichts Geheimes gespeichert
        assert "fully_template" not in client.get("/api/admin/settings", auth=AUTH).json()
        tpl = client.get("/api/admin/fully/template", auth=AUTH).json()
        assert tpl["settings"]["kioskMode"] is True and "deviceName" in tpl["skipped"]
        # gleich wie die Vorlage: nichts zu tun
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r == {"state": "ok", "different": [], "changed": [], "failed": []}
        # bearbeitete Vorlage hochladen (auch als reine Fully-Exportdatei): Geheimes fällt weg
        new = {**tpl["settings"], "enablePullToRefresh": True, "timeToScreenOffV2": "900", "kioskPinEnc": "enc_boese"}
        r = client.put("/api/admin/fully/template", auth=AUTH, json={"settings": new}).json()
        assert r["dropped"] == ["kioskPinEnc"] and r["source"] == "Datei"
        assert client.put("/api/admin/fully/template", auth=AUTH, json={"settings": {"x y": 1}}).status_code == 422
        # vergleichen ändert nichts, erst apply=true setzt – Ja/Nein als Ja/Nein, alles andere als Text
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["different"] == ["enablePullToRefresh", "timeToScreenOffV2"] and srv.settings == {}
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1", "apply": True}).json()
        assert sorted(r["changed"]) == ["enablePullToRefresh", "timeToScreenOffV2"] and r["failed"] == []
        assert srv.settings == {"enablePullToRefresh": True, "timeToScreenOffV2": "900"}
        assert "kioskPinEnc" not in srv.settings and "deviceName" not in srv.settings
    finally:
        srv.stop()
    assert client.post("/api/admin/fully/template/from", json={"ip": "127.0.0.1"}).status_code == 401
    assert client.put("/api/admin/fully/template", json={"settings": {}}).status_code == 401


# ------------------------------------------------------------------ Neues Handy ausrollen
def test_provision_qr_settings_and_secret_wifi_password(client):
    from app import fully
    prov = client.get("/api/admin/fully/provision", auth=AUTH).json()
    assert prov["apk_url"].startswith("https://") and prov["wifi_password_set"] is False
    body = {"apk_url": "https://example.org/fully-emm.apk", "checksum": "A" * 43, "code": "FFF", "ssid": "FestPOS",
            "security": "WPA", "wifi_password": "WLAN-Geheim", "apk_local": "0"}
    r = client.put("/api/admin/fully/provision", auth=AUTH, json=body).json()
    assert r["wifi_password_set"] is True and "WLAN-Geheim" not in json.dumps(r)
    assert client.put("/api/admin/fully/provision", auth=AUTH, json={**body, "wifi_password": ""}).json()["wifi_password_set"]
    assert not [k for k in client.get("/api/admin/settings", auth=AUTH).json() if k.startswith("fully_prov")]
    data = json.loads(fully.provision_json("192.168.55.10"))
    p = "android.app.extra.PROVISIONING_"
    assert data[f"{p}DEVICE_ADMIN_COMPONENT_NAME"] == "com.fullykiosk.emm/de.ozerov.fully.DeviceOwnerReceiver"
    assert data[f"{p}DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION"] == body["apk_url"]
    assert data[f"{p}WIFI_SSID"] == "FestPOS" and data[f"{p}WIFI_PASSWORD"] == "WLAN-Geheim"
    assert data[f"{p}ADMIN_EXTRAS_BUNDLE"] == {"FULLY_PROVISIONING_CODE": "FFF"}
    svg = client.get("/api/admin/fully/provision/qr", auth=AUTH).json()["svg"]
    assert svg.startswith("<svg") and "<script" not in svg
    from app import backup
    assert "fully_prov_wifi_password" in backup.SECRET_KEYS                     # nicht in der Sicherung
    assert client.put("/api/admin/fully/provision", auth=AUTH, json={**body, "apk_url": "javascript:alert(1)"}).status_code == 422
    assert client.get("/api/admin/fully/provision/qr").status_code == 401


@pytest.mark.parametrize("loads_on", ["restartApp", "rebootDevice"])
def test_rollout_bootstrap_fresh_phone(client, monkeypatch, loads_on):
    """Frisch eingerichtetes Fully (nur HTTP): einmal unverschlüsselt – nur mit Bestätigung – das Zertifikat für die
    Fernverwaltung hinbringen; nach dem Neustart spricht es HTTPS mit genau diesem Zertifikat. Lädt Fully es nicht
    schon beim Neustart der App (am XCover so), startet FestPOS das Handy neu."""
    from app import core, fully
    monkeypatch.setattr(fully, "FETCH_WAIT", 2.0)
    monkeypatch.setattr(fully, "UNZIP_WAIT", 0.1)
    monkeypatch.setattr(fully, "RESTART_WAIT", 4.0)
    monkeypatch.setattr(fully, "BOOT_WAIT", 15.0)
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [], "fully_password": PASSWORD})
    plain = FakeFully("plain")
    client.put("/api/admin/settings", auth=AUTH, json={"fully_port": plain.port})
    after: list[FakeFully] = []
    watcher = threading.Thread(target=lambda: restart_with_certificate())

    def restart_with_certificate():                       # «Fully» startet neu und lädt die Datei aus dem Speicher
        end = time.monotonic() + 15
        while time.monotonic() < end and loads_on not in plain.commands:
            time.sleep(0.05)
        p12 = plain.files.get("/storage/emulated/0/fully-remote-admin-ca.p12")
        plain.stop()
        if p12:
            after.append(FakeFully("https", p12, port=plain.port))
    try:
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 104"}).json()
        assert r == {"state": "plain_needed"}
        assert PASSWORD.encode() not in plain.received and b"password" not in plain.received   # ohne Bestätigung nie
        stored = json.loads(core.rt.store.setting("monitor_devices"))
        assert stored[0]["name"] == "XCover 104" and stored[0]["ip"] == "127.0.0.1"           # gleich eingetragen
        watcher.start()
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH,
                        json={"ip": "127.0.0.1", "name": "XCover 104", "plain_ok": True}).json()
        watcher.join(5)
        assert r == ({"state": "ok"} if loads_on == "restartApp" else {"state": "ok", "rebooted": True})
        assert plain.commands[:3] == ["deviceInfo", "loadZipFile", "restartApp"]
        pin = json.loads(core.rt.store.setting("monitor_devices"))[0]["fully_pin"]
        from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
        cert = pkcs12.load_key_and_certificates(plain.files["/storage/emulated/0/fully-remote-admin-ca.p12"], b"fully")[1]
        assert hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest() == pin
        # nochmals: schon eingerichtet – nichts mehr unverschlüsselt
        before = len(after[0].received)
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH,
                        json={"ip": "127.0.0.1", "name": "XCover 104", "plain_ok": True}).json()
        assert r == {"state": "already"} and after[0].commands[-1] == "deviceInfo"
        assert len(after[0].commands) == 2 and len(after[0].received) > before
    finally:
        if watcher.is_alive():
            watcher.join(10)
        for srv in after:
            srv.stop()


def test_rollout_never_plain_to_a_phone_with_https(client):
    """Spricht das Handy schon HTTPS (mit einem unbekannten Zertifikat), geht das Passwort nie unverschlüsselt hinaus."""
    p12, _, _ = _cert()
    other = FakeFully("https", p12)
    try:
        client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [], "fully_password": PASSWORD,
                                                             "fully_port": other.port})
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH,
                        json={"ip": "127.0.0.1", "name": "Fremd", "plain_ok": True}).json()
        assert r == {"state": "cert"} and PASSWORD.encode() not in other.received and b"password" not in other.received
    finally:
        other.stop()


def test_fully_apk_from_the_pi(client, tmp_path, monkeypatch):
    """Fully-APK einmal hochladen: Einrichtungs-QR zeigt auf den Pi, Prüfsumme rechnet der Pi; «Fully aktualisieren»."""
    import base64
    from app import fully
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    apk = b"PK\x03\x04" + b"apk-inhalt" * 300_000          # ~3 MB: grösser als die übliche Schranke von 1 MB
    body = {"apk_url": "https://example.org/x.apk", "checksum": "A" * 43, "apk_local": "1"}
    client.put("/api/admin/fully/provision", auth=AUTH, json=body)
    r = client.get("/api/admin/fully/provision/qr", auth=AUTH)
    assert r.status_code == 409 and "keine Fully-APK" in r.json()["detail"]
    assert client.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("x.apk", b"keine apk")}).status_code == 422
    r = client.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("Fully-1.61.2-emm.apk", apk)}).json()
    want = base64.urlsafe_b64encode(hashlib.sha256(apk).digest()).decode().rstrip("=")
    assert r["apk"]["checksum"] == want and r["apk"]["size"] == len(apk) and r["apk"]["name"] == "Fully-1.61.2-emm.apk"
    assert (tmp_path / "fully" / "fully.apk").read_bytes() == apk and not list((tmp_path / "fully").glob("*.part"))
    assert client.get("/api/admin/fully/apk", auth=AUTH).json()["apk"]["checksum"] == want
    data = json.loads(fully.provision_json("192.168.55.10"))
    p = "android.app.extra.PROVISIONING_"
    assert data[f"{p}DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION"] == "http://192.168.55.10/fully/fully.apk"
    assert data[f"{p}DEVICE_ADMIN_PACKAGE_CHECKSUM"] == want
    assert client.get("/api/admin/fully/provision/qr", auth=AUTH).status_code == 200
    assert client.post("/api/admin/fully/apk", files={"apk": ("x.apk", apk)}).status_code == 401
    # Fully aktualisieren: nur über HTTPS, Fully holt die APK vom Pi
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    try:
        r = client.post("/api/admin/fully/update-app", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["state"] == "ok" and srv.commands[-1] == "loadApkFile"
        assert b"url=http%3A%2F%2Ftestserver%2Ffully%2Ffully.apk" in srv.received
    finally:
        srv.stop()


def test_redirect_hands_out_the_apk(tmp_path, monkeypatch):
    import http.client
    import importlib.util
    from http.server import ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location("festpos_redirect2", Path(__file__).parent.parent / "deploy" / "festpos-redirect.py")
    red = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(red)
    monkeypatch.setattr(red, "APK", str(tmp_path / "fully.apk"))
    monkeypatch.setattr(red, "TAP_APK", str(tmp_path / "festpos-tap.apk"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), red.Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request(method, path)
        r = c.getresponse()
        out = (r.status, r.getheader("Content-Type"), r.read())
        c.close()
        return out
    try:
        assert get("/fully/fully.apk")[0] == 404                       # noch keine hochgeladen
        (tmp_path / "fully.apk").write_bytes(b"PK\x03\x04APK")
        assert get("/fully/fully.apk") == (200, "application/vnd.android.package-archive", b"PK\x03\x04APK")
        assert get("/fully/fully.apk", "HEAD")[:2] == (200, "application/vnd.android.package-archive")
        assert get("/fully/../fully/fully.apk")[0] == 301
        assert get("/fully/festpos-tap.apk")[0] == 404                 # Begleit-App für NFC-Zahlung (T13)
        (tmp_path / "festpos-tap.apk").write_bytes(b"PK\x03\x04TAP")
        assert get("/fully/festpos-tap.apk") == (200, "application/vnd.android.package-archive", b"PK\x03\x04TAP")
    finally:
        srv.shutdown()


def test_one_qr_rollout_settings_file_and_fixed_address(client, monkeypatch, tmp_path):
    """Ein QR-Code pro Handy: Fully holt beim Einrichten seine Einstellungen; der Pi merkt sich dabei die MAC und
    trägt die feste Adresse ein. Die Datei gibt es nur für den Pi selbst (festpos-redirect.py) und nur 30 Minuten."""
    from app import core, dhcp, fully
    client.put("/api/admin/settings", auth=AUTH, json={"fully_password": PASSWORD, "monitor_devices": [
        {"name": "Alt", "kind": "phone", "ip": "192.168.55.104", "port": None}]})
    old = json.loads(core.rt.store.setting("monitor_devices"))
    old[0].update(fully_pin="a" * 64, mac="aa:bb:cc:dd:ee:99")
    core.rt.store.execute("UPDATE settings SET value=? WHERE key='monitor_devices'", (json.dumps(old),))
    client.put("/api/admin/fully/provision", auth=AUTH, json={"apk_url": "https://example.org/x.apk",
                                                              "checksum": "A" * 43, "apk_local": "0", "ssid": "FestPOS"})
    client.put("/api/admin/fully/template", auth=AUTH, json={"settings": {"kioskMode": True, "timeToScreenOffV2": "300"}})
    assert client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "XCover 104", "ip": "192.168.55.999"}).status_code == 422
    r = client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "XCover 104", "ip": "192.168.55.104"}).json()
    token = r["id"]
    assert r["svg"].startswith("<svg") and fully.ROLLOUT_RE.match(token)
    data = json.loads(fully.provision_json("192.168.55.10", f"http://192.168.55.10/fully/{token}.json"))
    assert data["android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE"] == {        # ohne Fully-Code: «FFF», sonst fragt Fully
        "FULLY_SETTINGS_DOWNLOAD_LOCATION": f"http://192.168.55.10/fully/{token}.json", "FULLY_PROVISIONING_CODE": "FFF"}
    assert client.get(f"/api/admin/fully/rollout/{token}", auth=AUTH).json()["state"] == "wait"
    assert "fully_rollouts" not in client.get("/api/admin/settings", auth=AUTH).json()           # Codes nie sichtbar
    # nur der Pi selbst (festpos-redirect.py) bekommt die Datei, mit der Adresse des Handys
    url = f"/api/device-cert/fully/settings/{token}"
    assert client.get(url, headers={"X-Festpos-Client": "192.168.55.161"}).status_code == 403
    monkeypatch.setattr(fully, "LOCAL_HOSTS", {"testclient"})
    monkeypatch.setattr(dhcp, "HOSTS_DIR", tmp_path)                 # nie das echte Verzeichnis (auf einem Pi gäbe es das)
    monkeypatch.setattr(dhcp, "mac_for_ip", lambda ip: "6a:11:22:33:44:55" if ip == "192.168.55.161" else None)
    assert client.get(url).status_code == 400                                                   # ohne Adresse
    assert client.get("/api/device-cert/fully/settings/" + "x" * 32, headers={"X-Festpos-Client": "192.168.55.161"}).status_code == 404
    s = client.get(url, headers={"X-Festpos-Client": "192.168.55.161"}).json()
    assert s["remoteAdmin"] is True and s["remoteAdminLan"] is True
    assert s["remoteAdminPassword"] != PASSWORD and len(s["remoteAdminPassword"]) >= 20      # nur ein Einmal-Passwort
    assert s["deviceName"] == "XCover 104" and s["kioskMode"] is True and s["timeToScreenOffV2"] == "300"
    assert s["startURL"] == "https://testserver" and s["forceManageExternalStoragePermission"] is True
    assert s["urlWhitelist"] == "https://testserver\nhttps://testserver/*"          # von Anfang an nur die Bestell-App
    st = client.get(f"/api/admin/fully/rollout/{token}", auth=AUTH).json()
    assert (st["state"], st["seen_ip"], st["mac"]) == ("fetched", "192.168.55.161", "6a:11:22:33:44:55")
    dev = json.loads(core.rt.store.setting("monitor_devices"))
    assert dev[0]["name"] == "XCover 104" and dev[0]["mac"] == "6a:11:22:33:44:55" and "fully_pin" not in dev[0]
    assert dhcp.hosts(dev).splitlines()[1] == "6a:11:22:33:44:55,192.168.55.104,xcover-104,1h"
    # nach 30 Minuten: weg
    r = json.loads(core.rt.store.setting("fully_rollouts"))
    r[token]["at"] -= fully.ROLLOUT_TTL + 1
    core.rt.store.execute("UPDATE settings SET value=? WHERE key='fully_rollouts'", (json.dumps(r),))
    assert client.get(url, headers={"X-Festpos-Client": "192.168.55.161"}).status_code == 404
    assert client.get(f"/api/admin/fully/rollout/{token}", auth=AUTH).json()["state"] == "fetched"  # bleibt abgeholt
    t2 = client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "Nie", "ip": "192.168.55.105"}).json()["id"]
    r = json.loads(core.rt.store.setting("fully_rollouts"))
    r[t2]["at"] -= fully.ROLLOUT_TTL + 1
    core.rt.store.execute("UPDATE settings SET value=? WHERE key='fully_rollouts'", (json.dumps(r),))
    assert client.get(f"/api/admin/fully/rollout/{t2}", auth=AUTH).json()["state"] == "gone"      # nie gescannt
    assert client.post("/api/admin/fully/rollout", json={"name": "X", "ip": "192.168.55.5"}).status_code == 401


def test_redirect_passes_the_phone_address(monkeypatch):
    import http.client
    import importlib.util
    from http.server import ThreadingHTTPServer
    spec = importlib.util.spec_from_file_location("festpos_redirect3", Path(__file__).parent.parent / "deploy" / "festpos-redirect.py")
    red = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(red)
    asked = []
    monkeypatch.setattr(red, "fetch_settings", lambda token, client: asked.append((token, client)) or b'{"a":1}')
    srv = ThreadingHTTPServer(("127.0.0.1", 0), red.Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", f"/fully/{'t' * 32}.json")
        r = c.getresponse()
        assert (r.status, r.getheader("Content-Type"), r.read()) == (200, "application/json", b'{"a":1}')
        assert asked == [("t" * 32, "127.0.0.1")]
    finally:
        srv.shutdown()


def test_share_internet_only_on_the_gateway_pi(client, tmp_path, monkeypatch):
    """Internet beim Ausrollen: nur auf dem Pi, den die Handys als Gateway bekommen, mit der Verbindung von «Update aus
    dem Internet»; aus schaltet immer."""
    from app import dhcp, fully, update_net
    calls = []
    monkeypatch.setattr(update_net, "_net_up", lambda cfg, psk, share=False: calls.append(("up", cfg["via"], psk, share)) or "wlan0")
    monkeypatch.setattr(update_net, "_net_down", lambda: calls.append(("down",)))
    conf = tmp_path / "dnsmasq.conf"
    monkeypatch.setattr(dhcp, "CONF", conf)
    on = lambda: client.post("/api/admin/fully/internet", auth=AUTH, json={"on": True})  # noqa: E731
    r = on()
    assert r.status_code == 409 and "DHCP" in r.json()["detail"]                       # DHCP aus
    conf.write_text("dhcp-option=option:router,192.0.2.10\n")
    r = on()
    assert r.status_code == 409 and "192.0.2.10" in r.json()["detail"]                 # anderer Pi ist Gateway
    conf.write_text("dhcp-option=option:router,127.0.0.1\n")
    r = on()
    assert r.status_code == 409 and "Verwaltung › Geräte & Netz › Netz & Internet" in r.json()["detail"]      # keine Verbindung eingerichtet
    client.put("/api/admin/update/net", auth=AUTH, json={"repo": "", "via": "wlan", "ssid": "Hotspot", "psk": "geheim-wlan-123"})
    r = on().json()
    assert r["state"] == "on" and r["on"] is True and r["via"] == "wlan" and r["ssid"] == "Hotspot"
    assert calls == [("up", "wlan", "geheim-wlan-123", True)]
    assert client.get("/api/admin/fully/provision", auth=AUTH).json()["share"]["on"] is True
    r = client.post("/api/admin/fully/internet", auth=AUTH, json={"on": False}).json()
    assert r["state"] == "off" and calls[-1] == ("down",)
    # Seite «Internet» (T5): Schalter (b) aus -> keine Weitergabe; Schalter (a) aus -> kein Suchen nach Updates
    assert client.put("/api/admin/settings", auth=AUTH, json={"net_phones": False, "net_updates": False}).status_code == 200
    r = on()
    assert r.status_code == 409 and "ausgeschaltet" in r.json()["detail"] and calls[-1] == ("down",)
    assert client.get("/api/admin/fully/provision", auth=AUTH).json()["share"]["allowed"] is False
    assert client.get("/api/admin/update/net", auth=AUTH).json()["allowed"] is False
    r = client.post("/api/admin/update/net/check", auth=AUTH)
    assert r.status_code == 409 and "ausgeschaltet" in r.json()["detail"]
    assert client.put("/api/admin/settings", auth=AUTH, json={"net_phones": True, "net_updates": True}).status_code == 200
    assert on().json()["state"] == "on"
    client.post("/api/admin/fully/internet", auth=AUTH, json={"on": False})
    assert client.post("/api/admin/fully/internet", json={"on": True}).status_code == 401
    assert fully.dhcp_gateway() == "127.0.0.1"
    # dauerhaft über den Router verbunden (Gateway automatisch = Router): die Handhelds sind schon im Internet
    client.put("/api/admin/update/net", auth=AUTH, json={"repo": "", "via": "lan", "gateway": "192.0.2.1", "dns": "1.1.1.1"})
    conf.write_text("dhcp-option=option:router,192.0.2.1\n")
    monkeypatch.setitem(update_net._keep, "state", "up")
    n = len(calls)
    assert on().json()["state"] == "on" and len(calls) == n
    fully._share.clear()



def test_fully_license_volume_key_or_device_id(client, tmp_path, monkeypatch):
    """Letzter Schritt beim Ausrollen: Volumenlizenz setzt FestPOS, bei Einzellizenzen zeigt es die Geräte-ID."""
    from app import core, fully
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    srv.licensed = False
    try:
        r = client.post("/api/admin/fully/license", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert (r["state"], r["licensed"], r["device_id"], r["volume_key_set"]) == ("ok", False, "abc", False)
        assert client.post("/api/admin/fully/license", auth=AUTH, json={"ip": "127.0.0.1", "volume": True}).status_code == 422
        base = {"apk_url": "https://example.org/x.apk", "checksum": "A" * 43, "apk_local": "0"}
        p = client.put("/api/admin/fully/provision", auth=AUTH, json={**base, "volume_key": "VOL-KEY-123"}).json()
        assert p["volume_key_set"] is True and "VOL-KEY-123" not in json.dumps(p)
        assert "fully_volume_key" not in client.get("/api/admin/settings", auth=AUTH).json()
        from app import backup
        assert "fully_volume_key" in backup.SECRET_KEYS
        r = client.post("/api/admin/fully/license", auth=AUTH, json={"ip": "127.0.0.1", "volume": True}).json()
        assert r["licensed"] is True and srv.settings["volumeLicenseKey"] == "VOL-KEY-123"
        # beim Einrichten steht sie gleich in der Einstellungsdatei
        token = client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "X", "ip": "192.168.55.9"}).json()["id"]
        monkeypatch.setattr(fully, "LOCAL_HOSTS", {"testclient"})
        s = client.get(f"/api/device-cert/fully/settings/{token}", headers={"X-Festpos-Client": "192.168.55.160"}).json()
        assert s["volumeLicenseKey"] == "VOL-KEY-123"
        # entfernen (Einzellizenzen)
        client.put("/api/admin/fully/provision", auth=AUTH, json={**base, "volume_key_clear": True})
        assert not core.rt.store.setting("fully_volume_key")
    finally:
        srv.stop()
    assert client.post("/api/admin/fully/license", json={"ip": "127.0.0.1"}).status_code == 401



def test_kiosk_pin_belongs_to_the_template_but_stays_secret(client, tmp_path, monkeypatch):
    from app import core, fully
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    try:
        assert client.put("/api/admin/fully/template/pin", auth=AUTH, json={"pin": "12 34"}).status_code == 422
        assert client.put("/api/admin/fully/template/pin", auth=AUTH, json={"pin": "4711"}).json() == {"pin_set": True}
        assert client.get("/api/admin/fully/template", auth=AUTH).json()["pin_set"] is True
        assert "4711" not in json.dumps(client.get("/api/admin/fully/template", auth=AUTH).json())
        assert not [k for k in client.get("/api/admin/settings", auth=AUTH).json() if "kiosk_pin" in k]
        from app import backup
        assert "fully_kiosk_pin" in backup.SECRET_KEYS
        # auch ohne übrige Vorlage: der PIN weicht ab (Fully kennt nur kioskPinEnc) und wird gesetzt
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["different"] == ["kioskPin"] and "4711" not in json.dumps(r)
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1", "apply": True}).json()
        assert r["changed"] == ["kioskPin"] and srv.settings["kioskPinEnc"].startswith("enc_")
        assert core.rt.store.setting("fully_kiosk_pin_enc") == srv.settings["kioskPinEnc"]   # gelernt
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["different"] == []                                                          # jetzt gleich
        # beim Einrichten steht er in der Einstellungsdatei
        client.put("/api/admin/fully/provision", auth=AUTH, json={"apk_url": "https://example.org/x.apk",
                                                                  "checksum": "A" * 43, "apk_local": "0"})
        token = client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "X", "ip": "192.168.55.9"}).json()["id"]
        monkeypatch.setattr(fully, "LOCAL_HOSTS", {"testclient"})
        s = client.get(f"/api/device-cert/fully/settings/{token}", headers={"X-Festpos-Client": "192.168.55.160"}).json()
        assert s["kioskPin"] == "4711"
        # neuer PIN: wieder abweichend; in einer hochgeladenen Vorlage zählt kioskPin nicht
        client.put("/api/admin/fully/template/pin", auth=AUTH, json={"pin": "0815"})
        assert client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1"}).json()["different"] == ["kioskPin"]
        assert "kioskPin" not in fully.clean_template({"kioskPin": "1", "kioskMode": True})
        assert client.put("/api/admin/fully/template/pin", auth=AUTH, json={"clear": True}).json() == {"pin_set": False}
    finally:
        srv.stop()



def test_kiosk_pin_error_from_fully(client, tmp_path, monkeypatch):
    """Beim Ausrollen meldete der Schritt «Vorlage» einen Fehler beim Kiosk-PIN, obwohl er übernommen war: Fully hatte
    ihn schon aus der Einstellungsdatei. Jetzt: dann nur den verschlüsselten Wert merken; meldet Fully einen Fehler,
    zählt, ob sich kioskPinEnc geändert hat; sonst steht Fullys Antwort beim Fehler."""
    from app import core, fully
    client.put("/api/admin/settings", auth=AUTH, json={"fully_password": PASSWORD})
    client.put("/api/admin/fully/template/pin", auth=AUTH, json={"pin": "4711"})
    # 1. beim Ausrollen schon mit der Einstellungsdatei gesetzt -> nicht nochmals
    client.put("/api/admin/fully/provision", auth=AUTH, json={"apk_url": "https://example.org/x.apk",
                                                              "checksum": "A" * 43, "apk_local": "0"})
    token = client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "X", "ip": "127.0.0.1"}).json()["id"]
    with monkeypatch.context() as m:
        m.setattr(fully, "LOCAL_HOSTS", {"testclient"})
        assert client.get(f"/api/device-cert/fully/settings/{token}",
                          headers={"X-Festpos-Client": "192.168.55.160"}).json()["kioskPin"] == "4711"
    assert "4711" not in json.dumps(fully._rollouts())
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)                # danach: Fernverwaltung abgesichert
    try:
        srv.settings["kioskPinEnc"] = "enc_vom_einrichten"
        srv.pin_error = "Setting kioskPin not found"
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1", "apply": True}).json()
        assert (r["different"], r["changed"], r["failed"]) == ([], [], [])
        assert "setStringSetting" not in srv.commands
        assert core.rt.store.setting("fully_kiosk_pin_enc") == "enc_vom_einrichten"
        # 2. neuer PIN: Fully meldet einen Fehler, übernimmt ihn aber -> gesetzt
        client.put("/api/admin/fully/template/pin", auth=AUTH, json={"pin": "0815"})
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1", "apply": True}).json()
        assert (r["changed"], r["failed"]) == (["kioskPin"], [])
        assert core.rt.store.setting("fully_kiosk_pin_enc") == srv.settings["kioskPinEnc"] != "enc_vom_einrichten"
        # 3. Fehler und nicht übernommen -> fehlgeschlagen, mit Fullys Antwort (nie der PIN)
        client.put("/api/admin/fully/template/pin", auth=AUTH, json={"pin": "1234"})
        srv.pin_takes = False
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1", "apply": True}).json()
        assert r["failed"] == ["kioskPin"] and r["errors"] == {"kioskPin": "Setting kioskPin not found"}
        assert "1234" not in json.dumps(r)
    finally:
        srv.stop()


def test_rollout_with_one_time_password_never_sends_the_real_one_unencrypted(client, monkeypatch):
    """Mit QR-Code ausgerollt: Fully hat ein Einmal-Passwort; damit laufen die unverschlüsselten Befehle. Das echte
    setzt FestPOS erst über HTTPS – klappt das nicht, holt ein neuer Versuch es nach."""
    from app import core, fully
    monkeypatch.setattr(fully, "FETCH_WAIT", 2.0)
    monkeypatch.setattr(fully, "UNZIP_WAIT", 0.1)
    monkeypatch.setattr(fully, "RESTART_WAIT", 4.0)
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [], "fully_password": PASSWORD})
    client.put("/api/admin/fully/provision", auth=AUTH, json={"apk_url": "https://example.org/x.apk",
                                                              "checksum": "A" * 43, "apk_local": "0"})
    client.post("/api/admin/fully/rollout", auth=AUTH, json={"name": "XCover 105", "ip": "127.0.0.1"})
    temp = next(iter(json.loads(core.rt.store.setting("fully_rollouts")).values()))["temp_pw"]
    plain = FakeFully("plain")
    plain.password = temp                                          # so aus der Einstellungsdatei
    client.put("/api/admin/settings", auth=AUTH, json={"fully_port": plain.port})
    after: list[FakeFully] = []

    def restart_with_certificate():
        end = time.monotonic() + 15
        while time.monotonic() < end and "restartApp" not in plain.commands:
            time.sleep(0.05)
        p12 = plain.files.get("/storage/emulated/0/fully-remote-admin-ca.p12")
        plain.stop()
        if p12:
            srv = FakeFully("https", p12, port=plain.port)
            srv.password, srv.take_password = temp, False          # zuerst: Fully übernimmt das echte nicht
            after.append(srv)
    watcher = threading.Thread(target=restart_with_certificate)
    try:
        watcher.start()
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 105"}).json()
        watcher.join(5)
        assert r == {"state": "pw_not_switched"}                     # ohne Bestätigung: echtes ging nie hinaus
        assert PASSWORD.encode() not in plain.received and temp.encode() in plain.received
        assert after[0].password == temp
        after[0].take_password = True                                # nochmals: jetzt klappt das Umstellen
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 105"}).json()
        assert r == {"state": "ok"} and after[0].password == PASSWORD
        stored = json.loads(core.rt.store.setting("fully_rollouts"))
        assert all("temp_pw" not in v and v.get("pw_done") for v in stored.values())   # Einmal-Passwort vergessen
        r = client.post("/api/admin/fully/bootstrap", auth=AUTH, json={"ip": "127.0.0.1", "name": "XCover 105"}).json()
        assert r == {"state": "already"}
    finally:
        if watcher.is_alive():
            watcher.join(10)
        for srv in after:
            srv.stop()


def test_install_apk_unlocks_waits_and_locks_again(monkeypatch):
    """T30: «Disable APK Installs» und KNOX «Disable non-Google-Play Apps» sperren das Installieren. Der Pi
    sucht sie in listSettings, schaltet sie kurz aus, installiert, wartet auf getInstallApkState und schaltet sie
    wieder ein – auch wenn die Installation scheitert."""
    import asyncio
    from app import fully
    assert fully.install_locks({"disableApkInstall": True, "knoxDisableNonGooglePlayApps": "true", "kioskPin": "1234",
                                "disableOtherApps": True, "knoxDisableCamera": False}) == \
        ["disableApkInstall", "knoxDisableNonGooglePlayApps"]
    assert fully.install_result({"status": "Installation successful"}) == "installed"
    assert fully.install_result({"status": "Install failed: blocked"}) == "failed"
    assert fully.install_result({"status": "downloading"}) is None and fully.install_result(None) is None
    calls, states = [], [{"status": "downloading"}, {"status": "installed"}]

    async def settings(args):
        return "ok", {"disableApkInstall": True, "knoxDisableNonGooglePlayApps": False, "kioskMode": True}

    async def cmd(ip, port, pin, password, c, **kw):
        calls.append((c, kw.get("key"), kw.get("value")))
        return {"state": "ok"}

    async def state(args):
        return states.pop(0) if states else {"status": "installed"}
    monkeypatch.setattr(fully, "read_settings", settings)
    monkeypatch.setattr(fully, "setup_command", cmd)
    monkeypatch.setattr(fully, "install_state", state)
    monkeypatch.setattr(fully, "INSTALL_POLL", 0.01)
    r = asyncio.run(fully.install_apk("192.168.55.101", 2323, "pin", "pw", "http://pi/fully/festpos-tap.apk"))
    assert r["state"] == "installed" and r["locks"] == ["disableApkInstall"]          # nur, was an war
    assert calls == [("setBooleanSetting", "disableApkInstall", "false"), ("loadApkFile", None, None),
                     ("setBooleanSetting", "disableApkInstall", "true")]
    # Fully lehnt ab: Sperre trotzdem wieder ein
    calls.clear()

    async def refuse(ip, port, pin, password, c, **kw):
        calls.append((c, kw.get("key"), kw.get("value")))
        return {"state": "refused"} if c == "loadApkFile" else {"state": "ok"}
    monkeypatch.setattr(fully, "setup_command", refuse)
    r = asyncio.run(fully.install_apk("192.168.55.101", 2323, "pin", "pw", "http://pi/x.apk"))
    assert r["state"] == "refused" and calls[-1] == ("setBooleanSetting", "disableApkInstall", "true")
    # Fully selbst (wait=False): Antwort sofort, Sperre später wieder ein
    calls.clear()
    monkeypatch.setattr(fully, "setup_command", cmd)
    monkeypatch.setattr(fully, "RELOCK_AFTER", 0.01)

    async def later():
        r = await fully.install_apk("192.168.55.101", 2323, "pin", "pw", "http://pi/fully.apk", wait=False)
        await asyncio.sleep(0.1)
        return r
    r = asyncio.run(later())
    assert r["state"] == "ok" and calls[-1] == ("setBooleanSetting", "disableApkInstall", "true")


def test_more_remote_commands(client, monkeypatch):
    """Rückmeldung Punkt 7: Seite neu laden, Cache leeren (danach neu laden), Vordergrund, Bildschirm, Helligkeit;
    Kiosk «ein» schaltet einen ganz ausgeschalteten Kiosk-Modus zuerst wieder ein. Andere Befehle gibt es nicht."""
    from app import core, fully
    client.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [
        {"name": "XCover 9", "kind": "phone", "ip": "127.0.0.1", "port": None}], "fully_password": PASSWORD})
    srv = FakeFully("https", client.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.1"}).content)
    monkeypatch.setattr(fully, "KIOSK_SETTLE", 0.05)
    try:
        client.put("/api/admin/settings", auth=AUTH, json={"fully_port": srv.port})
        cmd = lambda c, **kw: client.post("/api/admin/fully/command", auth=AUTH, json={"ip": "127.0.0.1", "cmd": c, **kw})  # noqa: E731
        for c in ("loadStartURL", "toForeground", "screenOn", "screenOff"):
            assert cmd(c).json()["state"] == "ok" and srv.commands[-1] == c
        assert cmd("clearCache").json()["state"] == "ok" and srv.commands[-2:] == ["clearCache", "loadStartURL"]
        r = cmd("brightness", value=128).json()
        assert r["state"] == "ok" and srv.settings["screenBrightness"] == "128"
        assert cmd("brightness").status_code == 422 and cmd("brightness", value=300).status_code == 422
        assert cmd("loadUrl").status_code == 422 and cmd("setStringSetting").status_code == 422
        # Kiosk-Modus ganz aus: «ein» fragt Fully frisch und setzt ihn zuerst wieder (nicht dem letzten Stand trauen)
        core.rt.monitor.fully_store("XCover 9", "127.0.0.1", {"state": "ok", "data": {"kiosk": True, "kiosk_locked": True}})
        srv.settings["kioskMode"] = False
        r = cmd("lockKiosk").json()
        assert r["state"] == "ok" and srv.settings.get("kioskMode") is True
        assert srv.commands[-4:] == ["deviceInfo", "setBooleanSetting", "lockKiosk", "deviceInfo"]
        r = cmd("lockKiosk").json()                                          # schon ein: nichts zusätzlich setzen
        assert srv.commands[-3:] == ["deviceInfo", "lockKiosk", "deviceInfo"]
    finally:
        srv.stop()


# ------------------------------------------------------------------ JavaScript-Schnittstelle nur mit Whitelist
def test_whitelist_check_rules():
    """Warnung, wenn die JavaScript-Schnittstelle an ist und die URL Whitelist fehlt oder mehr erlaubt
    als die Bestell-App; ohne die Schlüssel (andere Fully-Version) «nicht prüfbar», nie eine falsche Warnung."""
    from app import dhcp, fully
    on = {"startURL": "https://192.168.55.10", "websiteIntegration": True}
    # ohne Schlüssel für die Schnittstelle: nicht prüfbar – mit den Namen, die danach klingen (nie Werte)
    assert fully.web_check({}) == {"state": "unknown", "keys": []}
    assert fully.web_check({"enableJavascriptX": True, "urlWhitelist": "", "kioskMode": True}) == \
        {"state": "unknown", "keys": ["enableJavascriptX", "urlWhitelist"]}
    # (XCover 7, Fully 1.61.4): heisst websiteIntegration; leere Whitelist fehlt evtl. ganz -> «fehlt»
    assert fully.web_check({"websiteIntegration": True}) == {"state": "missing"}
    assert fully.web_check({"jsInterface": True, "urlWhitelist": "https://192.168.55.10/*"}) == {"state": "ok"}
    assert fully.web_check({**on, "websiteIntegration": False, "urlWhitelist": ""}) == {"state": "off"}
    assert fully.web_check({**on, "websiteIntegration": "true", "urlWhitelist": " \n"}) == {"state": "missing"}
    assert fully.web_check({**on, "urlWhitelist": "https://192.168.55.10\r\nhttps://192.168.55.10/*"}) == {"state": "ok"}
    assert fully.web_check({**on, "urlWhitelist": "https://192.168.55.10/admin.html"}) == {"state": "ok"}
    for wide in ("*", "*192.168.55.10*", "https://192.168.55.10*", "https://192.168.55.10.evil.ch/*",
                 "https://192.168.55.10/*, *", "http://192.168.55.10/*", "https://192.168.55.11/*"):
        assert fully.web_check({**on, "urlWhitelist": f"https://192.168.55.10/*\n{wide}"}) == \
            {"state": "wide", "foreign": [wide]}, wide
    # was «Whitelist setzen» einträgt: Start-URL und alles darunter (http: dazu die HTTPS-Adresse der Weiterleitung)
    assert fully.whitelist_value("https://192.168.55.10") == "https://192.168.55.10\nhttps://192.168.55.10/*"
    assert fully.whitelist_value("http://192.168.55.10/") == \
        "http://192.168.55.10\nhttp://192.168.55.10/*\nhttps://192.168.55.10\nhttps://192.168.55.10/*"
    assert fully.whitelist_value("https://pi.local:8443/index.html") == "https://pi.local:8443\nhttps://pi.local:8443/*"
    assert fully.whitelist_value("") == fully.whitelist_value("javascript:alert(1)") == \
        f"https://{dhcp.VIP}\nhttps://{dhcp.VIP}/*"
    for start in ("https://192.168.55.10", "http://192.168.55.10/"):                 # das Gesetzte gilt als ok
        assert fully.web_check({"startURL": start, "websiteIntegration": True,
                                "urlWhitelist": fully.whitelist_value(start)}) == {"state": "ok"}
    # die Whitelist gehört nicht in die Vorlage (sonst kopierte sie eine fremde Start-URL), setzen darf FestPOS sie
    assert not fully.template_key("urlWhitelist") and "urlWhitelist" not in fully.clean_template({"urlWhitelist": "*"})


def test_whitelist_check_and_set_via_fully(client, tmp_path, monkeypatch):
    import asyncio
    from app import core, fully
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    check = lambda: client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"}).json()  # noqa: E731
    put = lambda: client.post("/api/admin/fully/whitelist", auth=AUTH, json={"ip": "127.0.0.1"})  # noqa: E731
    try:
        assert check()["web"] == {"state": "unknown", "keys": [], "seconds": 0}   # dieses Fully kennt die Schlüssel nicht
        r = put()
        assert r.status_code == 409 and "urlWhitelist" not in srv.settings
        srv.settings.update(websiteIntegration=True, urlWhitelist="*\nhttps://192.168.55.10/*")
        assert check()["web"]["state"] == "wide" and check()["web"]["foreign"] == ["*"]
        r = put().json()
        assert r["state"] == "ok" and r["web"]["state"] == "ok" and r["lines"] == ["https://192.168.55.10", "https://192.168.55.10/*"]
        assert srv.settings["urlWhitelist"] == "https://192.168.55.10\nhttps://192.168.55.10/*"
        assert srv.settings["websiteIntegration"] is True                                # die Schnittstelle bleibt, wie sie ist
        assert client.get("/api/admin/fully", auth=AUTH).json()["results"]["127.0.0.1"]["web"]["state"] == "ok"
        # im Hintergrund: erst wieder nach WEB_EVERY, höchstens WEB_PER_ROUND pro Runde
        srv.settings["urlWhitelist"] = ""
        cfg = fully.config()
        n = srv.commands.count("listSettings")
        asyncio.run(fully.web_due(cfg))
        assert srv.commands.count("listSettings") == n and core.rt.monitor.fully_web["127.0.0.1"]["state"] == "ok"
        monkeypatch.setattr(fully, "WEB_EVERY", 0.0)
        monkeypatch.setattr(fully, "WEB_RETRY", 0.0)
        asyncio.run(fully.web_due(cfg))
        assert core.rt.monitor.fully_web["127.0.0.1"]["state"] == "missing"
        asyncio.run(fully.web_due({**cfg, "password": ""}))                        # ohne Passwort: niemand gefragt
        # Fully antwortet gerade nicht (Standby): nicht fragen
        core.rt.monitor.fully_store("XCover 9", "127.0.0.1", {"state": "timeout"})
        n = srv.commands.count("listSettings")
        asyncio.run(fully.web_due(cfg))
        assert srv.commands.count("listSettings") == n
        # Vorlage: die Whitelist kommt nicht mit (und eine alte Vorlage mit Whitelist setzt sie nicht zurück)
        assert client.post("/api/admin/fully/template/from", auth=AUTH, json={"ip": "127.0.0.1"}).json()["state"] == "ok"
        assert "urlWhitelist" not in client.get("/api/admin/fully/template", auth=AUTH).json()["settings"]
        t = json.loads(core.rt.store.setting("fully_template"))
        t["settings"]["urlWhitelist"] = "*"
        core.rt.store.execute("UPDATE settings SET value=? WHERE key='fully_template'", (json.dumps(t),))
        r = client.post("/api/admin/fully/settings", auth=AUTH, json={"ip": "127.0.0.1", "apply": True}).json()
        assert "urlWhitelist" not in r["different"] and srv.settings["urlWhitelist"] == ""
        # vergessen: auch der Stand der Whitelist
        client.post("/api/admin/fully/forget", auth=AUTH, json={"ip": "127.0.0.1"})
        assert "127.0.0.1" not in core.rt.monitor.fully_web
    finally:
        srv.stop()
    assert client.post("/api/admin/fully/whitelist", json={"ip": "127.0.0.1"}).status_code == 401
