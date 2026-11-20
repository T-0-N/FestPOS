"""T13: Karte/TWINT mit NFC über Payrexx – Zahlung mit Einmal-Schlüssel und Startadresse für «FestPOS Tap»,
Ergebnis der Payrexx-App über /api/tap/result (wie es die Begleit-App schickt), nie doppelt, falscher Schlüssel oder
Betrag wird abgewiesen, Erfolg nach «Abbrechen» trotzdem verbucht, Bestätigen von Hand, Übung simuliert,
Verwaltung (Einstellungen, APK), Tagesabschluss mit eigener Zeile."""
from __future__ import annotations

import json
import time
from urllib.parse import unquote

import pytest

from tests.test_api import AUTH, setup_shop

ROOT = __import__('pathlib').Path(__file__).resolve().parent.parent


@pytest.fixture
def pay(client, monkeypatch):
    from app import devices, payrexx
    monkeypatch.setattr(payrexx, "SIM_S", 0.2)
    monkeypatch.setattr(devices, "phone_only", lambda request=None: None)
    for r in client.app.router.routes:                     # Handy-Port im Test: ohne Zertifikat
        if getattr(r, "path", "").startswith("/api/phone/payrexx"):
            r.dependant.dependencies.clear()
    setup_shop(client)
    yield


def _order(client, test=False) -> str:
    import uuid
    pid = client.get("/api/bootstrap").json()["products"][0]["id"]
    cu = str(uuid.uuid4())
    r = client.post("/api/orders", json={"client_uuid": cu, "table": "7", "lines": [{"product_id": pid, "qty": 2}],
                                         "test": test})
    assert r.status_code == 200, r.text
    return cu


def _enable(client, **kw):
    r = client.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _extras(launch: str) -> dict:
    """S.key=wert aus intent://…#Intent;…;end"""
    assert launch.startswith("intent://pay#Intent;scheme=festpostap;package=ch.festpos.tap;") and launch.endswith(";end")
    return {k[2:]: unquote(v) for k, v in (p.split("=", 1) for p in launch.split(";") if p.startswith("S."))}


def _result(amount: str, ref: str, status="successful", code="0", tx="TX-77") -> str:
    """Antwort der Payrexx-App (com.payrexx.taptopay.RESULT), wie im Payrexx-Beispiel (TapToPayECRDemo)."""
    body = {"responseBody": {"transaction": {"transaction_key": tx, "status": status, "action_code": code,
                                             "amount": amount, "currency": "CHF", "order_reference": ref,
                                             "payment_method": "mastercard"}, "extras": {}, "message": None}}
    return json.dumps({"payload": json.dumps(body)})


def _start_pay(client, cu, amount, part="all", key="k-all-000001"):
    r = client.post("/api/phone/payrexx", json={"client_uuid": cu, "amount_cents": amount, "part": part, "key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_parse_result():
    from app import payrexx
    ok = payrexx.parse_result(_result("12.50", "FestPOS-0001-1"))
    assert ok == {"ok": True, "transaction": "TX-77", "amount": 1250, "reference": "FestPOS-0001-1", "message": None}
    assert not payrexx.parse_result(_result("12.50", "x", status="failed", code="05"))["ok"]
    assert payrexx.parse_result("kein json") == {"ok": False, "message": "format"}


def test_pay_whole_order(client, pay):
    from app import core
    _enable(client, method="TWINT")
    cu = _order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (cu,))
    p = _start_pay(client, cu, total)
    assert p["status"] == "waiting" and not p["test"]
    x = _extras(p["launch"])
    assert x["id"] == str(p["id"]) and len(x["token"]) >= 20 and x["amount"] == f"{total / 100:.2f}"
    assert x["ref"].startswith("FestPOS-") and x["server"].startswith("https://") and x["method"] == "TWINT"
    assert "token" not in client.get(f"/api/phone/payrexx/{p['id']}").json()        # Schlüssel nur in der Startadresse
    # nochmals senden (gleicher Schlüssel oder neu geöffnet): keine zweite Zahlung
    assert _start_pay(client, cu, total)["id"] == p["id"]
    assert _start_pay(client, cu, total, key="k-all-000002")["id"] == p["id"]
    # falscher Schlüssel: abgewiesen
    bad = client.post("/api/tap/result", json={"id": p["id"], "token": "falsch-falsch", "ok": True,
                                               "result": _result(x["amount"], x["ref"])})
    assert bad.status_code == 403
    r = client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True,
                                             "result": _result(x["amount"], x["ref"])})
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["transaction"] == "TX-77"
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE client_uuid=?", (cu,)) == "payrexx"
    assert client.get(f"/api/phone/payrexx/{p['id']}").json()["status"] == "confirmed"
    # nochmals gemeldet: bleibt eine Zahlung
    client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True, "result": _result(x["amount"], x["ref"])})
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_paid'") == 1
    assert client.post("/api/phone/payrexx", json={"client_uuid": cu, "amount_cents": total, "key": "k-all-000003"}).status_code == 409
    # Tagesabschluss: eigene Zeile, nicht in der Kasse
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["count"] == 1 and s["payrexx"]["cents"] == total and s["payrexx"]["list"][0]["transaction"] == "TX-77"
    client.post("/api/admin/closings", auth=AUTH, json={"float_cents": 0, "counted_cents": s["net"] - total})
    cl = client.get("/api/admin/closings", auth=AUTH).json()["closings"][0]
    assert cl["difference_cents"] == 0 and cl["expected_cents"] == s["net"] - total
    assert client.get(f"/api/admin/closings/{cl['id']}/pdf", auth=AUTH).status_code == 200


def test_declined_wrong_amount_and_cancel(client, pay):
    from app import core
    _enable(client)
    cu = _order(client)
    p = _start_pay(client, cu, 500, part="p1", key="k-p1-000001")
    x = _extras(p["launch"])
    r = client.post("/api/tap/result", json={"id": p["id"], "token": x["token"], "ok": True,
                                             "result": _result("5.00", x["ref"], status="failed", code="05")})
    assert r.json()["status"] == "failed"
    # Erfolg mit anderem Betrag: nicht bezahlt, im Protokoll
    p2 = _start_pay(client, cu, 500, part="p1", key="k-p1-000002")
    x2 = _extras(p2["launch"])
    r = client.post("/api/tap/result", json={"id": p2["id"], "token": x2["token"], "ok": True,
                                             "result": _result("0.50", x2["ref"])})
    assert r.json()["status"] == "failed"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_mismatch'") == 1
    # Abgebrochen (Bedienung), dann doch Erfolg von der Payrexx-App: Geld ist geflossen -> verbucht
    p3 = _start_pay(client, cu, 500, part="p1", key="k-p1-000003")
    x3 = _extras(p3["launch"])
    assert client.post(f"/api/phone/payrexx/{p3['id']}/cancel").json()["status"] == "cancelled"
    r = client.post("/api/tap/result", json={"id": p3["id"], "token": x3["token"], "ok": True, "result": _result("5.00", x3["ref"])})
    assert r.json()["status"] == "confirmed"
    detail = core.rt.store.scalar("SELECT details FROM audit_log WHERE action='payrexx_paid'")
    assert '"after_cancel": true' in detail
    assert core.rt.store.scalar("SELECT payment FROM orders WHERE client_uuid=?", (cu,)) is None     # nur Person 1
    # Payrexx-App abgebrochen (kein Ergebnis)
    p4 = _start_pay(client, cu, 300, part="p2", key="k-p2-000001")
    r = client.post("/api/tap/result", json={"id": p4["id"], "token": _extras(p4["launch"])["token"], "ok": False,
                                             "message": "cancelled"})
    assert r.json()["status"] == "failed" and r.json()["error"] == "cancelled"


def test_manual_confirm(client, pay):
    from app import core
    _enable(client)
    cu = _order(client)
    p = _start_pay(client, cu, 500, part="p1", key="k-p1-000005")
    r = client.post(f"/api/phone/payrexx/{p['id']}/manual", headers={"X-Festpos-Device": "XCover%201"})
    assert r.json()["status"] == "confirmed" and r.json()["transaction"] is None
    detail = core.rt.store.scalar("SELECT details FROM audit_log WHERE action='payrexx_paid'")
    assert "manual:XCover 1" in detail


def test_closing_shows_what_was_confirmed_by_hand(client, pay):
    """Review 2026-11, F1: eine von Hand bestätigte Kartenzahlung (ohne Transaktionsnummer von Payrexx) zählt im
    Kassensturz wie jede Payrexx-Zahlung – steht im Abschluss aber eigens da, zum Vergleichen mit der Abrechnung."""
    from app import core
    _enable(client)
    a, b = _order(client), _order(client)
    total = core.rt.store.scalar("SELECT total_cents FROM orders WHERE client_uuid=?", (a,))
    p1 = _start_pay(client, a, total, key="k-hand-000001")
    assert client.post(f"/api/phone/payrexx/{p1['id']}/manual", headers={"X-Festpos-Device": "XCover%201"}).json()["status"] == "confirmed"
    p2 = _start_pay(client, b, total, key="k-nfc-0000001")
    ex = _extras(p2["launch"])
    r = client.post("/api/tap/result", json={"id": p2["id"], "token": ex["token"], "ok": True,
                                             "result": _result(ex["amount"], ex["ref"], tx="TX-88")})
    assert r.json()["status"] == "confirmed"
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["count"] == 2 and s["payrexx"]["cents"] == 2 * total
    assert s["payrexx"]["manual"] == {"count": 1, "cents": total}
    c = client.post("/api/admin/closings", auth=AUTH, json={"float_cents": 0, "counted_cents": 0}).json()
    assert c["summary"]["payrexx"]["manual"] == {"count": 1, "cents": total}
    assert client.get(f"/api/admin/closings/{c['id']}/pdf", auth=AUTH).status_code == 200
    assert client.get(f"/api/admin/closings/{c['id']}/verify", auth=AUTH).json()["ok"]
    assert "prxmanual" in (ROOT / "static" / "admin" / "closing.js").read_text()


def test_test_mode_simulated(client, pay):
    from app import core
    client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin_required": False})
    cu = _order(client, test=True)                                         # Übung: auch ohne Einstellung
    p = _start_pay(client, cu, 500, key="k-test-00001")
    assert p["test"] and p["status"] == "waiting" and p["launch"] is None  # Payrexx-App wird nie gestartet
    time.sleep(0.3)
    assert client.get(f"/api/phone/payrexx/{p['id']}").json()["status"] == "confirmed"
    assert core.rt.store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='payrexx_paid'") == 0


def test_off_and_settings(client, pay):
    cu = _order(client)
    r = client.post("/api/phone/payrexx", json={"client_uuid": cu, "amount_cents": 500, "key": "k-off-000001"})
    assert r.status_code == 409
    assert client.get("/api/bootstrap").json().get("payrexx") is False
    assert client.put("/api/admin/payrexx", auth=AUTH, json={"enabled": True, "method": "BITCOIN"}).status_code == 422
    v = _enable(client, method="CONTACTLESS", show_result=True)
    assert v["enabled"] and v["method"] == "CONTACTLESS" and v["show_result"] and v["apk"] is None
    assert not any(k.startswith("payrexx") for k in client.get("/api/admin/settings", auth=AUTH).json())
    assert "payrexx_settings" in json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    x = _extras(_start_pay(client, cu, 500, key="k-on-0000001")["launch"])
    assert x["method"] == "CONTACTLESS" and x["show"] == "1"


def test_apk_upload_and_install(client, pay, monkeypatch, tmp_path):
    from app import fully, fully_apk
    monkeypatch.setattr(fully, "APK_DIR", tmp_path)
    assert client.post("/api/admin/payrexx/apk/install", auth=AUTH).status_code == 409        # noch keine App
    info = {"package": "de.ozerov.fully", "version_name": "1.0", "version_code": 1}
    monkeypatch.setattr(fully_apk, "apk_manifest", lambda path, name="": info)
    r = client.post("/api/admin/payrexx/apk", auth=AUTH, files={"file": ("x.apk", b"PK\x03\x04fremd")})
    assert r.status_code == 422 and not (tmp_path / "festpos-tap.apk").exists()               # fremdes Paket
    info.update(package="ch.festpos.tap", version_name="1.2.0", version_code=3)
    r = client.post("/api/admin/payrexx/apk", auth=AUTH, files={"file": ("festpos-tap.apk", b"PK\x03\x04tap")})
    assert r.status_code == 200 and r.json()["version"] == "1.2.0"
    assert (tmp_path / "festpos-tap.apk").read_bytes() == b"PK\x03\x04tap"
    assert client.get("/api/admin/payrexx", auth=AUTH).json()["apk"]["version"] == "1.2.0"
    sent = []
    result = {"state": "installed", "locks": ["disableApkInstall"]}

    async def fake_install(ip, port, pin, password, url, wait=True):
        import asyncio
        sent.append((ip, url, wait))
        await asyncio.sleep(0.3)                   # wie Fully: dauert – sonst ist «wird installiert» schon vorbei
        return dict(result)
    monkeypatch.setattr(fully, "config", lambda: {"password": "pw", "port": 2323,
                                                  "targets": [{"ip": "192.168.55.101", "pin": "1", "name": "XCover 1"},
                                                              {"ip": "192.168.55.102", "name": "ohne PIN"}]})
    monkeypatch.setattr(fully, "install_apk", fake_install)

    def done(ip="192.168.55.101"):                 # T30: im Hintergrund – warten, bis «wird installiert» vorbei ist
        for _ in range(100):
            m = client.get("/api/admin/payrexx", auth=AUTH).json()["sent"]
            if ip in m and not m[ip].get("installing"):
                return m
            time.sleep(0.05)
        raise AssertionError("installiert nicht")
    r = client.post("/api/admin/payrexx/apk/install", auth=AUTH)
    assert r.status_code == 200 and r.json()["results"] == [{"name": "XCover 1", "ip": "192.168.55.101", "state": "installing"}]
    assert r.json()["sent"]["192.168.55.101"]["installing"] == "1.2.0"
    sent_map = done()
    assert sent[0][1].endswith("/fully/festpos-tap.apk") and sent[0][2] is True     # wartet auf Fully
    # T30: pro Handheld – installiert (von Fully bestätigt), mit den kurz ausgeschalteten Sperren
    one = sent_map["192.168.55.101"]
    assert one["version"] == "1.2.0" and one["state"] == "installed" and one["locks"] == ["disableApkInstall"]
    assert "192.168.55.102" not in sent_map
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert "payrexx_tap_apk" in audit and "payrexx_tap_install" in audit
    info.update(version_name="1.3.0", version_code=4)
    client.post("/api/admin/payrexx/apk", auth=AUTH, files={"file": ("festpos-tap.apk", b"PK\x03\x04tap2")})
    result.update(state="unreachable", locks=[])
    r = client.post("/api/admin/payrexx/apk/install", auth=AUTH, json={"ip": "192.168.55.101"})
    assert r.status_code == 200
    one = done()["192.168.55.101"]
    assert one["version"] == "1.2.0" and one["failed"] == "unreachable" and one["failed_at"]   # alte Version bleibt
    result.update(state="sent")                    # ohne Bestätigung von Fully: «geschickt»
    client.post("/api/admin/payrexx/apk/install", auth=AUTH, json={"ip": "192.168.55.101"})
    one = done()["192.168.55.101"]
    assert one["version"] == "1.3.0" and one["state"] == "sent"
    assert client.post("/api/admin/payrexx/apk/install", auth=AUTH, json={"ip": "192.168.55.102"}).status_code == 404


def test_result_reaches_admin_port(client, pay, monkeypatch):
    """«FestPOS Tap» meldet über den Verwaltungs-Port (ohne Gerätezertifikat) – auch mit ADMIN_FROM."""
    import app.main as main
    from app import core
    _enable(client)
    cu = _order(client)
    x = _extras(_start_pay(client, cu, 500, part="p1", key="k-adm-000001")["launch"])
    assert x["server"].endswith(":8443")                                 # Verwaltungs-Port
    monkeypatch.setattr(main.Config, "phone_port", 443)                  # TestClient = Verwaltungs-Port
    monkeypatch.setattr(core.Config, "admin_from", "192.168.55.200-192.168.55.254")
    assert client.get("/api/bootstrap").status_code in (403, 404)       # sonst gesperrt
    r = client.post("/api/tap/result", json={"id": int(x["id"]), "token": x["token"], "ok": True,
                                             "result": _result(x["amount"], x["ref"])})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
