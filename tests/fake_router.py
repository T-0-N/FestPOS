"""Nachgebauter Mobilfunk-Router (MikroTik RouterOS 7, REST über HTTPS) für Tests und die Vorschau.

    python3 -m tests.fake_router 8443        # läuft, bis Ctrl+C; Fingerabdruck steht in der Ausgabe

Antwortet wie RouterOS: alle Werte als Text, Fehler als {"error", "message", "detail"}. Merkt sich jede Anfrage
(Kopfzeilen), damit Tests prüfen können, dass Zugangsdaten nur an das übernommene Zertifikat gehen.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import ssl
import sys
import tempfile
import threading
from pathlib import Path

USER, PASSWORD = "festpos", "Router-Geheim 7!"

MONITOR = {                       # so meldet ein LtAP mini LTE (2024) im Netz von Swisscom
    "pin-status": "ok", "registration-status": "registered", "functionality": "full",
    "manufacturer": "Quectel", "model": "EC200A", "revision": "EC200AEUHAR01A12M16",
    "current-operator": "22801", "lac": "1234", "current-cellid": "1234567", "enb-id": "4822", "sector-id": "135",
    "phy-cellid": "351", "access-technology": "Evolved 3G (LTE)", "session-uptime": "2h14m9s",
    "imei": "860000000000001", "imsi": "228010000000001", "uicc": "8941000000000000001",
    "subscriber-number": "+41790000000", "primary-band": "B3@20Mhz earfcn: 1300 phy-cellid: 351",
    "ca-band": "B20@10Mhz earfcn: 6300 phy-cellid: 351", "cqi": "11", "ri": "2", "mcs": "",
    "rssi": "-63", "rsrp": "-92", "rsrq": "-11", "sinr": "14",
}


NOSIM = {                         # echte Antwort eines LtAP mini LTE kit (2024) ohne SIM-Karte (+ IMEI zum Prüfen)
    "functionality": "tx and rx rf circuit disabled", "manufacturer": "Quectel", "model": "EC200A",
    "pin-status": "SIM not inserted", "revision": "EC200AEUHAR01A19M16", "status": "radio off",
    "imei": "860000000000001",
}


def make_cert(cn: str = "festpos-router") -> tuple[str, str, str]:
    """Selbst signiertes Zertifikat wie «/certificate sign» auf dem Router -> (cert.pem, key.pem, Fingerabdruck)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + dt.timedelta(days=3650)).sign(key, hashes.SHA256()))
    d = Path(tempfile.mkdtemp())
    (d / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    pin = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return str(d / "c.pem"), str(d / "k.pem"), pin


class FakeRouter:
    """mode: https (normal) | http (nur unverschlüsselt) | rights (Benutzer ohne genug Rechte) | old (kein
    /interface/lte/settings, keine Zusatzwerte) | nosim (keine SIM-Karte eingelegt)."""

    def __init__(self, mode: str = "https", host: str = "127.0.0.1", port: int = 0):
        self.mode, self.host = mode, host
        self.cert, self.key, self.pin = make_cert()
        self.values = dict(MONITOR)
        self.iface = "lte1"
        self.heads: list[bytes] = []
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self._port = port
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(5)

    def new_cert(self) -> None:
        """Router bekommt ein neues Zertifikat (oder ein anderes Gerät antwortet unter derselben Adresse)."""
        self.cert, self.key, self.pin = make_cert("anderes-geraet")
        self.ctx.load_cert_chain(self.cert, self.key)

    @property
    def authorized(self) -> list[bytes]:
        return [h for h in self.heads if b"authorization:" in h.lower()]

    def _answer(self, method: str, path: str, head: bytes, body: bytes) -> tuple[int, object]:
        auth = next((ln.split(b":", 1)[1].strip() for ln in head.split(b"\r\n") if ln.lower().startswith(b"authorization:")), b"")
        want = b"Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode())
        if auth != want:
            return 401, {"error": 401, "message": "Unauthorized"}
        if method == "POST" and path == "/rest/interface/lte/monitor":
            if self.mode == "rights":
                return 400, {"error": 400, "message": "Bad Request", "detail": "not enough permissions (9)"}
            req = json.loads(body or b"{}")
            if req.get("numbers") != self.iface or "once" not in req:
                return 400, {"error": 400, "message": "Bad Request", "detail": "no such item"}
            return 200, [{".section": "0", **(NOSIM if self.mode == "nosim" else self.values)}]
        if self.mode == "old":
            return 400, {"error": 400, "message": "Bad Request", "detail": "no such command"}
        if method == "GET" and path == f"/rest/interface/{self.iface}":
            if self.mode == "nosim":
                return 200, {".id": "*1", "name": self.iface, "type": "lte", "running": "false", "rx-byte": "0",
                             "tx-byte": "0", "mtu": "1500"}
            return 200, {".id": "*1", "name": self.iface, "type": "lte", "running": "true", "rx-byte": "48211337",
                         "tx-byte": "5120000", "mtu": "1500"}
        if method == "GET" and path == "/rest/system/resource":
            return 200, {"board-name": "LtAP mini LTE", "version": "7.16.1 (stable)", "uptime": "3d4h5m6s",
                         "cpu-load": "4", "free-memory": "33554432"}
        if method == "GET" and path == "/rest/interface/lte/settings":
            return 200, {"firmware-path": "", "sim-slot": "down"}
        return 400, {"error": 400, "message": "Bad Request", "detail": "no such command"}

    async def _handle(self, reader, writer):
        try:
            if self.mode == "http":                   # TLS-Begrüssung statt HTTP: wie ein Webserver -> 400
                self.heads.append(await reader.read(4096))
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            head = await reader.readuntil(b"\r\n\r\n")
            self.heads.append(head)
            line = head.split(b"\r\n", 1)[0].decode().split()
            n = next((int(ln.split(b":", 1)[1]) for ln in head.split(b"\r\n") if ln.lower().startswith(b"content-length:")), 0)
            body = await reader.readexactly(n) if n else b""
            status, data = self._answer(line[0], line[1], head, body)
            out = json.dumps(data).encode()
            writer.write(b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (status, len(out), out))
            await writer.drain()
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.ctx = None
        if self.mode != "http":
            self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ctx.load_cert_chain(self.cert, self.key)
        self.srv = self.loop.run_until_complete(asyncio.start_server(self._handle, self.host, self._port, ssl=self.ctx))
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


if __name__ == "__main__":
    r = FakeRouter(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8443)
    print(f"Router auf 127.0.0.1:{r.port}  Benutzer {USER}  Fingerabdruck {r.pin}")
    try:
        r.thread.join()
    except KeyboardInterrupt:
        r.stop()
