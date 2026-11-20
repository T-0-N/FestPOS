"""Tests zu den Review-Punkten (Stufe 1): Leaderwechsel, Datenbankfehler, Geräte, CSV, Druckerbefehle."""
import asyncio
import uuid

import pytest

import app.print_service as ps
from app.csv_import import parse_delivery
from app.escpos import Bon
from app.print_service import PrintSpooler, reroute_pending
from app.store import RqliteStore, StoreError, StoreUnavailable
from tests.fake_printer import FakePrinter
from tests.test_api import AUTH, make_device_ca, setup_shop
from tests.test_printing import drain, order, run, seed, wait_leader


class Flaky:
    """Store-Hülle: leitet alles weiter, kann aber Leader-Antworten und Fehler einspielen."""

    def __init__(self, store):
        self._s = store
        self.leader_script: list = []          # nächste Antworten von leader_state (danach True)
        self.fail_printers_query = 0           # so oft scheitert "SELECT id FROM printers"

    def __getattr__(self, name):
        return getattr(self._s, name)

    def leader_state(self):
        return self.leader_script.pop(0) if self.leader_script else True

    def is_leader(self):
        return self.leader_state() is True

    def query(self, sql, params=()):
        if sql == "SELECT id FROM printers" and self.fail_printers_query:
            self.fail_printers_query -= 1
            raise StoreUnavailable("Leaderwahl läuft")
        return self._s.query(sql, params)

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=()):
        row = self.query_one(sql, params)
        return next(iter(row.values())) if row else None


def _slow_send(monkeypatch, seconds: float, during=None):
    real = ps.send_raw

    async def slow(addr, data, **kw):
        if during:
            during()
        await asyncio.sleep(seconds)
        await real(addr, data, **kw)
    monkeypatch.setattr(ps, "send_raw", slow)


# ------------------------------------------------------------ REV-002: Leader-Status unbekannt / kurz weg

@pytest.mark.parametrize("script", [
    [None, None, None, None, None],            # Statusaufruf scheitert mehrmals (Timeout)
    [False, None, None],                       # einmal "kein Leader", dann unbekannt: kein Abgeben
    [False, False, False],                     # wirklich kurz kein Leader -> abgeben, wieder Leader werden
])
def test_own_job_not_uncertain_on_leader_hiccup(store, monkeypatch, script):
    async def t():
        bar = await FakePrinter().start()
        kitchen = await FakePrinter().start()
        seed(store, bar.port, kitchen.port)
        fl = Flaky(store)
        sp = PrintSpooler(fl, leader_poll=0.05)
        await sp.start(); await wait_leader(sp)
        _slow_send(monkeypatch, 0.6, during=lambda: fl.leader_script.extend(script))
        order(fl, sp, "5", [(1, 1)])
        await drain(store)
        await asyncio.sleep(0.2)
        await sp.stop(); await bar.stop(); await kitchen.stop()
        return bar.bons
    bons = run(t())
    statuses = [r["status"] for r in store.query("SELECT status FROM print_jobs")]
    assert statuses == ["done"], statuses              # nicht "uncertain"
    assert len(bons) == 1


# ------------------------------------------------------------ REV-003: Überwachung stirbt nicht

def test_leader_loop_survives_error_on_promote(store):
    async def t():
        bar = await FakePrinter().start()
        seed(store, bar.port, bar.port)
        fl = Flaky(store)
        fl.fail_printers_query = 2                     # die ersten zwei Beförderungen scheitern
        sp = PrintSpooler(fl, leader_poll=0.05)
        await sp.start(); await wait_leader(sp)
        assert not sp._leader_task.done()
        order(fl, sp, "7", [(1, 1)])
        await drain(store)
        await sp.stop(); await bar.stop()
        return bar.bons
    assert len(run(t())) == 1


# ------------------------------------------------------------ REV-001: Cluster-Fehler sind vorübergehend

def test_rqlite_top_level_errors_are_unavailable(monkeypatch):
    s = RqliteStore("127.0.0.1", 1)
    monkeypatch.setattr(s, "_request", lambda *a, **k: {"error": "leadership lost while committing log"})
    with pytest.raises(StoreUnavailable):
        s.batch([("INSERT INTO x VALUES (1)", ())])
    with pytest.raises(StoreUnavailable):
        s.query("SELECT 1")
    monkeypatch.setattr(s, "_request", lambda *a, **k: {"results": [{"error": "UNIQUE constraint failed: orders.x"}]})
    with pytest.raises(StoreError) as e:
        s.batch([("INSERT INTO x VALUES (1)", ())])
    assert not isinstance(e.value, StoreUnavailable) and e.value.is_unique_violation
    monkeypatch.setattr(s, "status", lambda: (_ for _ in ()).throw(StoreUnavailable("timeout")))
    assert s.leader_state() is None and s.is_leader() is False


def test_order_gets_503_when_cluster_unavailable(client, monkeypatch):
    import app.main as main
    setup_shop(client)
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]

    def broken(*a, **k):
        raise StoreUnavailable("rqlite: leadership lost")
    monkeypatch.setattr(main.store, "batch", broken)
    r = client.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "1",
                                         "lines": [{"product_id": pid, "qty": 1}]})
    assert r.status_code == 503                         # Handy behält die Bestellung und sendet später


# ------------------------------------------------------------ REV-004: Sperre bleibt

def test_revoked_device_cannot_be_removed(client, tmp_path, monkeypatch):
    import app.main as main
    crt, key, _ = make_device_ca(tmp_path)
    monkeypatch.setattr(main.Config, "device_ca", str(crt))
    monkeypatch.setattr(main.Config, "device_ca_key", str(key))
    client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "XCover 9"})
    did = client.get("/api/admin/devices", auth=AUTH).json()["certs"][0]["id"]
    assert client.post(f"/api/admin/devices/{did}/revoke", auth=AUTH).status_code == 200
    r = client.delete(f"/api/admin/devices/{did}", auth=AUTH)
    assert r.status_code == 409
    assert main.store.scalar("SELECT COUNT(*) FROM device_certs WHERE revoked_at IS NOT NULL") == 1
    assert client.delete("/api/admin/devices/99999", auth=AUTH).status_code == 404
    # gültig und nicht gesperrt: bleibt auch (sonst liesse es sich nie mehr sperren); abgelaufen: darf weg
    client.post("/api/admin/devices/cert", auth=AUTH, json={"name": "XCover 10"})
    did2 = main.store.scalar("SELECT id FROM device_certs WHERE name='XCover 10'")
    r = client.delete(f"/api/admin/devices/{did2}", auth=AUTH)
    assert r.status_code == 409 and "sperren" in r.json()["detail"]
    main.store.execute("UPDATE device_certs SET expires_at='2020-01-01' WHERE id IN (?,?)", (did, did2))
    assert client.delete(f"/api/admin/devices/{did2}", auth=AUTH).status_code == 200
    assert client.delete(f"/api/admin/devices/{did}", auth=AUTH).status_code == 200   # gesperrt + abgelaufen


def test_duplicate_names_have_readable_messages(client):
    for path, body in (("/api/admin/products", {"name": "Bier", "price_cents": 500}),
                       ("/api/admin/categories", {"name": "Essen"}),
                       ("/api/admin/printers", {"name": "Bar", "host": "127.0.0.1"})):
        assert client.post(path, auth=AUTH, json=body).status_code == 200
        r = client.post(path, auth=AUTH, json={**body, "name": body["name"].upper()})
        assert r.status_code == 409 and "UNIQUE" not in r.json()["detail"] and "schon" in r.json()["detail"], r.text
    r = client.post("/api/admin/products", auth=AUTH, json={"name": "Wein", "price_cents": 500, "category_id": 999})
    assert r.status_code == 400 and "gibt" in r.json()["detail"] and "FOREIGN" not in r.json()["detail"]


# ------------------------------------------------------------ REV-015, REV-017, REV-018, REV-019

def test_reroute_resets_failed_jobs(store):
    seed(store, 1, 2)
    order(store, None, "3", [(1, 2)])
    store.execute("UPDATE print_jobs SET status='failed', last_error='kaputt'")
    assert reroute_pending(store, 1, 2) == 1
    row = store.query_one("SELECT status, printer_id FROM print_jobs")
    assert row == {"status": "pending", "printer_id": 2}


def test_control_chars_never_reach_printer_and_qr_is_utf8():
    out = Bon(48).text("Tisch 7\x1bd\xff\x10\x14\x00 ok\n").build()
    body = out.split(b"\x1bt\x13", 1)[1]                # nach Init + Zeichensatz
    assert b"\x1b" not in body and b"\x10" not in body and b"\x00" not in body and body.endswith(b" ok\n")
    assert "tisch:Bühne".encode() in Bon(48).qr("tisch:Bühne").build()


def test_delivery_export_can_be_imported_again():
    t = ["Sofort", "Pause", "Schluss"]
    assert parse_delivery("alle", t) == "alle"
    assert parse_delivery("alle*", t) == "alle*"
    assert parse_delivery("Pause|Schluss*", t) == "Pause|Schluss*"


# ------------------------------------------------------------ Aufräumen hängender Jobs, rqlite-HTTP

def test_sweep_marks_stuck_own_jobs_uncertain(store):
    seed(store, 1, 1)
    order(store, None, "8", [(1, 1)])
    store.execute("UPDATE print_jobs SET status='printing', claimed_by='local'")   # Statusupdate verloren

    async def t():
        sp = PrintSpooler(store, leader_poll=0.02, sweep_every=3)
        await sp.start(); await wait_leader(sp)
        store.execute("UPDATE print_jobs SET status='printing', claimed_by='local'")  # nach dem Befördern
        await asyncio.sleep(0.3)
        await sp.stop()
    run(t())
    assert store.scalar("SELECT status FROM print_jobs") == "uncertain"


def _http_server(handler_cls):
    import http.server
    import threading
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_rqlite_http_500_is_unavailable_and_post_timeout_not_resent():
    import http.server
    import time
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            hits.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if "slow" in self.path or self.path.startswith("/db/execute"):
                time.sleep(1.0)                          # länger als das Client-Timeout
                return
            self.send_response(500); self.send_header("Content-Length", "2"); self.end_headers()
            self.wfile.write(b"{}")

    srv = _http_server(H)
    try:
        s = RqliteStore("127.0.0.1", srv.server_address[1], timeout=0.3)
        with pytest.raises(StoreUnavailable):
            s.query("SELECT 1")                          # HTTP 500 -> vorübergehend
        hits.clear()
        with pytest.raises(StoreUnavailable):
            s.execute("UPDATE x SET y=1")                # Timeout nach dem Senden
        assert len(hits) == 1                            # nicht ein zweites Mal geschickt
    finally:
        srv.shutdown()


# ------------------------------------------------------------ REV-010: Drucker ohne Papier / Deckel offen

def test_send_raw_refuses_when_paper_out_or_cover_open():
    from app.escpos import PrinterAddr, PrinterError, send_raw

    async def t():
        pr = await FakePrinter().start()
        addr = PrinterAddr("127.0.0.1", pr.port)
        pr.paper_out = True
        with pytest.raises(PrinterError, match="Papier leer"):
            await send_raw(addr, b"BON1\n")
        pr.paper_out, pr.cover_open = False, True
        with pytest.raises(PrinterError, match="Deckel offen"):
            await send_raw(addr, b"BON2\n")
        pr.cover_open, pr.silent = False, True            # Modell ohne Statusantwort: trotzdem drucken
        await send_raw(addr, b"BON3\n", status_timeout=0.3)
        pr.silent = False
        await send_raw(addr, b"BON4\n")
        await asyncio.sleep(0.1)
        await pr.stop()
        return pr
    pr = run(t())
    assert pr.buffered == []                               # nichts in einen gestörten Drucker geschickt
    assert [r.endswith(b"BON3\n") or r.endswith(b"BON4\n") for r in pr.received] == [True, True]


def test_paper_out_triggers_reroute_instead_of_done(store):
    async def t():
        bar = await FakePrinter().start()
        kitchen = await FakePrinter().start()
        kitchen.paper_out = True
        seed(store, bar.port, kitchen.port)
        store.execute("UPDATE printers SET fail_mode='reroute', fail_target=1, fail_after=0 WHERE id=2")
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.05)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "9", [(4, 1)])                    # Pommes -> Küche
        await drain(store)
        await sp.stop(); await bar.stop(); await kitchen.stop()
        return bar, kitchen
    bar, kitchen = run(t())
    assert kitchen.buffered == [] and kitchen.received == []
    assert len(bar.bons) == 1 and "Umgeleitet" in bar.bons[0] and "Papier" not in bar.bons[0]
    row = store.query_one("SELECT status, printer_id FROM print_jobs")
    assert row == {"status": "done", "printer_id": 1}


def test_paper_out_waits_then_prints_when_refilled(store):
    async def t():
        bar = await FakePrinter().start()
        kitchen = await FakePrinter().start()
        kitchen.paper_out = True
        seed(store, bar.port, kitchen.port)
        sp = PrintSpooler(store, backoff=(0.05, 0.1), leader_poll=0.05)
        await sp.start(); await wait_leader(sp)
        order(store, sp, "10", [(4, 1)])
        await asyncio.sleep(0.6)
        # Der Job pendelt bei jedem Versuch kurz über 'printing' – auf den Wartezustand warten statt einmal lesen.
        for _ in range(100):
            job = store.query_one("SELECT status, last_error FROM print_jobs")
            if job["status"] == "pending":
                break
            await asyncio.sleep(0.01)
        assert job["status"] == "pending" and "Papier leer" in job["last_error"]
        assert kitchen.bons == []
        kitchen.paper_out = False                          # Papier nachgefüllt
        await drain(store)
        await sp.stop(); await bar.stop(); await kitchen.stop()
        return kitchen
    kitchen = run(t())
    assert len(kitchen.bons) == 1 and kitchen.buffered == []


def test_late_status_reply_does_not_truncate_bon():
    """Drucker (kleiner Empfangspuffer, noch beschäftigt) antwortet zu spät auf die Statusabfrage:
    der Bon muss trotzdem vollständig ankommen (neue Verbindung statt TCP-Reset beim Schliessen)."""
    import socket
    import threading
    import time as _t
    from app.escpos import PrinterAddr, send_raw
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    srv.bind(("127.0.0.1", 0)); srv.listen(2)
    port, got = srv.getsockname()[1], []

    def serve():
        for i in range(2):
            try:
                c, _ = srv.accept()
            except OSError:
                return
            data = b""
            if i == 0:
                _t.sleep(1.0)                              # beschäftigt: Statusantwort kommt zu spät
                try:
                    c.recv(3); c.sendall(b"\x12")
                except OSError:
                    pass
            try:
                while chunk := c.recv(65536):
                    data += chunk
            except OSError:
                data += b"<RESET>"
            got.append(data)
            c.close()
    th = threading.Thread(target=serve, daemon=True); th.start()
    bon = b"X" * 50_000 + b"ENDE"
    run(send_raw(PrinterAddr("127.0.0.1", port), bon, status_timeout=0.3))
    th.join(5); srv.close()
    assert any(g.endswith(bon) for g in got), [len(g) for g in got]


def test_printer_error_bit_and_invalid_status():
    from app.escpos import PrinterStatus, _valid_status
    assert PrinterStatus(online=True, error="Druckerfehler").ok is False
    assert _valid_status(0x12) and _valid_status(0x12 | 0x04 | 0x20 | 0x40) and not _valid_status(0x41)


def test_monitor_ok_clears_printer_down(store):
    sp = PrintSpooler(store)
    sp._down[2] = (0.0, "12:00"); sp._reason[2] = "Papier leer"; sp._warned.add(2)
    assert sp.down_reason(2) == "Papier leer"
    sp.mark_ok(2)
    assert not sp.is_down(2) and sp.down_reason(2) is None and 2 not in sp._warned
