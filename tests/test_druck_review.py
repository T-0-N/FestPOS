"""Druck (Review 2026-10): Storniertes auf Bons, Abbruch mitten im Senden, Hänger-Erkennung in UTC, Drucker-Abfrage
nur vom druckenden Pi, Umleiten nur auf einen brauchbaren Drucker."""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import threading
import time

from app.escpos import CODEPAGE_PY, PrinterAddr, PrinterError
from tests.test_api import AUTH
from tests.test_ledger import _order, shop  # noqa: F401 (shop = Fixture)


def _text(data: bytes) -> str:
    return data.decode(CODEPAGE_PY, errors="replace")


def _render(job_id):
    from app import core, print_service as ps
    st = core.rt.store
    return ps.render_job(st, st.query_one("SELECT * FROM print_jobs WHERE id=?", (job_id,)), ps.load_printers(st))


def _storno(client, oid, line, qty, uid):
    r = client.post(f"/api/admin/orders/{oid}/storno", auth=AUTH,
                    json={"client_uuid": uid, "lines": [{"line_no": line, "qty": qty}], "reason": "wrong"})
    assert r.status_code == 200, r.text


def test_reprint_after_partial_storno_shows_current_state(client, shop):  # noqa: F811
    from app import core
    st = core.rt.store
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 3}, {"product_id": shop["Mineral"], "qty": 1}])
    job = st.query_one("SELECT id FROM print_jobs WHERE order_id=? AND kind='table'", (oid,))["id"]
    before = _text(_render(job))
    assert "3x" in before and "Storniert" not in before
    st.execute("UPDATE print_jobs SET status='done' WHERE id=?", (job,))
    _storno(client, oid, 1, 2, "storno-teil-1")
    new = client.post(f"/api/admin/jobs/{job}/reprint", auth=AUTH).json()["job_id"]
    data = _render(new)
    text = _text(data)
    assert "1x" in text and "3x" not in text and "Storniert" in text and b"\x1dv0" in data    # durchgestrichen = Bild
    assert "6.50" in text                                             # Total: nur noch 1 Bier + 1 Mineral
    _storno(client, oid, 2, 1, "storno-teil-2")                       # Mineral ganz storniert
    text = _text(_render(new))
    assert "Mineral" not in text.split("Storniert")[0]


def test_single_ticket_marks_cancelled_pieces(client, shop):  # noqa: F811
    from app import core
    st = core.rt.store
    oid, _ = _order(client, [{"product_id": shop["Bratwurst"], "qty": 3}])
    line = st.scalar("SELECT line_no FROM order_items WHERE order_id=?", (oid,))
    singles = [r["id"] for r in st.query("SELECT id FROM print_jobs WHERE order_id=? AND kind='single' ORDER BY n", (oid,))]
    _storno(client, oid, line, 1, "storno-wurst-1")
    assert "1 von 3 storniert" in _text(_render(singles[0]))
    _storno(client, oid, line, 2, "storno-wurst-2")
    data = _render(singles[0])
    assert b"\x1dv0" in data and "storniert" not in _text(data).split("Bratwurst")[-1]


def _rst_server():
    """Nimmt eine Verbindung an und bricht sie sofort hart ab (RST) – wie ein Drucker, dem der Strom ausgeht."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.recv(1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()
        srv.close()
    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_send_aborted_midway_is_marked_sent():
    from app.escpos import send_raw
    port = _rst_server()
    try:
        asyncio.run(send_raw(PrinterAddr("127.0.0.1", port), b"x" * (8 * 1024 * 1024), check_status=False))
    except PrinterError as e:
        assert e.sent is True
    else:
        raise AssertionError("kein Fehler")
    try:                                                              # nicht erreichbar: noch nichts gesendet
        asyncio.run(send_raw(PrinterAddr("127.0.0.1", 1), b"x", check_status=False))
    except PrinterError as e:
        assert e.sent is False


def test_spooler_marks_aborted_send_uncertain(client, shop, monkeypatch):  # noqa: F811
    """Abbruch nach Beginn des Sendens: nicht blind nochmals drucken (könnte halb gedruckt sein) – «unklar»."""
    from app import core, print_service as ps
    st = core.rt.store

    async def broken(addr, payload, **kw):
        raise PrinterError("Verbindung abgerissen", sent=True)
    monkeypatch.setattr(ps, "send_raw", broken)
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    deadline = time.time() + 10
    while time.time() < deadline:
        s = st.scalar("SELECT status FROM print_jobs WHERE order_id=?", (oid,))
        if s == "uncertain":
            break
        time.sleep(0.05)
    job = st.query_one("SELECT status, error_code, attempts FROM print_jobs WHERE order_id=?", (oid,))
    assert job["status"] == "uncertain" and job["error_code"] == "uncertain" and job["attempts"] == 1


def test_stuck_detection_uses_utc(client, shop):  # noqa: F811
    from app import core, print_service as ps
    st = core.rt.store
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    job = st.scalar("SELECT id FROM print_jobs WHERE order_id=?", (oid,))
    st.execute("UPDATE print_jobs SET status='printing', claimed_by='anderer', claimed_at=? WHERE id=?", (ps.utc_iso(), job))
    assert client.post(f"/api/admin/jobs/{job}/reprint", auth=AUTH).status_code == 409
    st.execute("UPDATE print_jobs SET claimed_at=? WHERE id=?", (ps.utc_iso(time.time() - ps.STUCK_AFTER_S - 5), job))
    assert client.post(f"/api/admin/jobs/{job}/reprint", auth=AUTH).status_code == 200
    assert ps.utc_iso(0) == "1970-01-01T00:00:00Z"


def test_only_printing_pi_queries_printers(monkeypatch):
    """Nur der druckende Pi fragt die Drucker ab; die anderen nehmen seinen Stand aus seiner Antwort (T9: direkt beim
    anderen Pi, nicht über die Datenbank)."""
    from app import monitor as mon
    m = mon.Monitor()
    queried = []

    async def fake_status(addr, timeout=2.0):
        queried.append(addr)
        from app.escpos import PrinterStatus
        return PrinterStatus(online=True)
    monkeypatch.setattr(mon, "query_status", fake_status)
    m._get_targets, m._cluster = (lambda: []), False
    m._get_printers = lambda: [{"id": 1, "host": "10.0.0.1", "port": 9100, "enabled": True, "busy": False}]
    m._printers_here, m._printers_legacy = (lambda: False), None
    m.peers_store({"https://10.0.0.2:8443": ({"node": "b", "leader": True, "printers": {"1": {"online": True, "paper_out": True}}},
                                             0.0, 0.01)})
    asyncio.run(m.check())
    assert queried == [] and m.printers == {1: {"online": True, "paper_out": True}} and m.printers_here() is None
    m._printers_here = lambda: True
    asyncio.run(m.check())
    assert len(queried) == 1 and m.printers_here()["1"]["online"] is True


def test_printer_state_legacy_and_forget(store, monkeypatch):
    """Alter Eintrag printer_state: lesen nur noch während eines Updates (alter druckender Pi), beim Start einmal
    löschen – und ohne Eintrag gar nicht schreiben (T9)."""
    from app import core, status
    monkeypatch.setattr(core.rt, "store", store)
    now = json.dumps({"at": int(time.time()), "printers": {"3": {"online": False, "error": "x"}}})
    store.execute("INSERT INTO settings(key,value) VALUES ('printer_state', ?)", (now,))
    assert status.printer_state_get() == {3: {"online": False, "error": "x"}}
    old = json.dumps({"at": int(time.time()) - 600, "printers": {"3": {"online": True}}})
    store.execute("UPDATE settings SET value=? WHERE key='printer_state'", (old,))
    assert status.printer_state_get() == {}                           # zu alt: druckender Pi meldet sich nicht mehr
    status.printer_state_forget()
    assert store.setting("printer_state", None) is None
    writes = []
    monkeypatch.setattr(store, "execute", lambda *a: writes.append(a))
    status.printer_state_forget()
    assert writes == []


def test_printer_state_not_in_settings(client):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES ('printer_state', '{}') "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    assert "printer_state" not in client.get("/api/admin/settings", auth=AUTH).json()


def test_reroute_only_to_usable_printer(client, shop):  # noqa: F811
    printers = {p["name"]: p for p in client.get("/api/admin/printers", auth=AUTH).json()}
    a, b = printers["Getränkeausgabe"]["id"], printers["Küche"]["id"]
    assert client.post(f"/api/admin/printers/{a}/reroute?to={a}", auth=AUTH).status_code == 422
    assert client.post(f"/api/admin/printers/{a}/reroute?to=999", auth=AUTH).status_code == 422
    k = printers["Küche"]
    client.put(f"/api/admin/printers/{b}", auth=AUTH, json={**{x: k[x] for x in ("name", "host", "port", "chars_per_line")},
                                                         "show_prices": bool(k["show_prices"]), "enabled": False})
    assert client.post(f"/api/admin/printers/{a}/reroute?to={b}", auth=AUTH).status_code == 422
