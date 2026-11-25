"""T22: QR-Bestellung am Tisch – mit einem gefälschten Payrexx (kein Internet): signierte Tische, Speisekarte, Preise nur
vom Server, Bestellung erst nach bestätigter Zahlung, Bons, Tagesabschluss; automatische Rückerstattung (ausverkauft,
nicht gedruckt), Storno erstattet zurück, Knopf in der Verwaltung, Fehler beim Zurückerstatten bleiben sichtbar."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_api import AUTH, setup_shop

KEY = "payrexx-test-schluessel-9876"
TOKEN = "gast-test-token-1234567890"


class FakePayrexx:
    """Gateway anlegen/abfragen/löschen, Transaktion zurückerstatten – mit derselben Signatur wie Payrexx."""

    def __init__(self):
        self.gateways: dict[int, dict] = {}
        self.refunds: list[tuple[str, int]] = []
        self.calls: list[tuple[str, str]] = []
        self.refund_fails = False
        self.refund_delay = 0.0          # T37: Rückerstattung ausführen, aber so lange mit der Antwort warten
        self.refund_fail_code = 400
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _ok(self, params, sig):
                want = base64.b64encode(hmac.new(KEY.encode(), urllib.parse.urlencode(params).encode(),
                                                 hashlib.sha256).digest()).decode()
                return hmac.compare_digest(want, sig or "")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                params = urllib.parse.parse_qsl(self.rfile.read(n).decode(), keep_blank_values=True)
                sig = dict(params).get("ApiSignature")
                params = [p for p in params if p[0] != "ApiSignature"]
                path = urllib.parse.urlsplit(self.path).path
                outer.calls.append(("POST", path))
                if not self._ok(params, sig):
                    return self._reply(401, {"status": "error"})
                if "/Transaction/" in path and path.endswith("/refund"):
                    if outer.refund_delay:
                        import time
                        time.sleep(outer.refund_delay)
                    if outer.refund_fails:                    # Payrexx lehnt ab (4xx); 5xx gälte als unklar
                        return self._reply(outer.refund_fail_code, {"status": "error", "message": "nope"})
                    outer.refunds.append((path.split("/")[-2], int(dict(params)["amount"])))
                    return self._reply(200, {"status": "success", "data": [{"status": "refunded"}]})
                gid = len(outer.gateways) + 1
                outer.gateways[gid] = {"id": gid, "status": "waiting", "link": f"https://pay.example/{gid}",
                                       "params": dict(params)}
                self._reply(200, {"status": "success", "data": [outer.gateways[gid]]})

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                outer.calls.append(("GET", u.path))
                if not self._ok([], q.get("ApiSignature")):
                    return self._reply(401, {"status": "error"})
                if u.path.endswith("/SignatureCheck/"):
                    return self._reply(200, {"status": "success", "data": []})
                gid = int(u.path.rstrip("/").rsplit("/", 1)[1])
                g = outer.gateways[gid]
                inv = [{"transactions": [{"id": 7000 + gid, "status": "confirmed", "payment": {"brand": "twint"}}]}] \
                    if g["status"] == "confirmed" else []
                self._reply(200, {"status": "success", "data": [{**g, "invoices": inv}]})

            def do_DELETE(self):
                gid = int(urllib.parse.urlsplit(self.path).path.rstrip("/").rsplit("/", 1)[1])
                outer.gateways[gid]["status"] = "cancelled"
                outer.calls.append(("DELETE", self.path))
                self._reply(200, {"status": "success", "data": []})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}/v1.0"


G = {"x-festpos-gast": TOKEN}


@pytest.fixture
def gast(client, monkeypatch, tmp_path):
    from app import core, gast as g, payrexx_api
    fake = FakePayrexx()
    monkeypatch.setattr(payrexx_api, "BASE", fake.base)
    tok = tmp_path / "gast.token"
    tok.write_text(TOKEN + "\n")
    monkeypatch.setattr(g, "TOKEN_FILE", tok)
    monkeypatch.setattr(core.Config, "cluster_secret", "test-cluster-geheimnis-0123456789")   # ohne: alles zu
    monkeypatch.setattr(core.rt.spooler, "is_down", lambda pid: False)       # Drucker gelten als bereit
    setup_shop(client)
    client.post("/api/admin/tables", auth=AUTH, json={"labels": ["7", "8", "Bar"]})
    r = client.put("/api/admin/gast", auth=AUTH, json={"open": True, "domain": "bestellen.example.ch",
                                                       "instance": "theater", "key": KEY, "max_cents": 30000})
    assert r.status_code == 200, r.text
    yield fake
    fake.srv.shutdown()


def _sig(table):
    from app import gast as g
    return g.table_sig(table)


def _products(client):
    return {p["name"]: p for p in client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}, headers=G).json()["products"]}


def _order(client, lines, key="key-0000001", table="7"):
    return client.post("/api/gast/order", headers=G, json={"t": table, "s": _sig(table), "key": key, "lines": lines})


def test_only_through_proxy_and_signed_tables(client, gast):
    assert client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}).status_code == 403          # ohne Schlüssel
    assert client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")},
                      headers={"x-festpos-gast": "falsch"}).status_code == 403
    assert client.get("/api/gast/menu", params={"t": "7", "s": _sig("8")}, headers=G).status_code == 403   # fremder Tisch
    assert client.get("/api/gast/menu", params={"t": "99", "s": _sig("99")}, headers=G).status_code == 403  # kein Tisch
    m = client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}, headers=G).json()
    assert m["table"] == "7" and m["closed"] is None and m["products"] and "price" in m["products"][0]
    assert "stock" not in m["products"][0] and "cost" not in json.dumps(m)


def test_pay_then_order_and_print(client, gast):
    from app import core
    p = _products(client)
    beer = next(iter(p.values()))
    r = _order(client, [{"product_id": beer["id"], "qty": 2, "price": 1}])            # Preis vom Gast wird ignoriert
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["status"] == "waiting" and o["link"] == "https://pay.example/1" and o["amount"] == 2 * beer["price"]
    params = gast.gateways[1]["params"]
    assert params["amount"] == str(2 * beer["price"]) and params["referenceId"] == "FestPOS-online-1"
    assert params["successRedirectUrl"] == f"https://bestellen.example.ch/?b={o['token']}"
    assert _order(client, [{"product_id": beer["id"], "qty": 2}]).json()["token"] == o["token"]   # gleicher Schlüssel
    assert core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 0                  # noch nicht bezahlt: keine Bestellung
    gast.gateways[1]["status"] = "confirmed"
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "ordered" and v["nr"] and v["method"] == "twint"
    order = core.rt.store.query_one("SELECT * FROM orders WHERE id=?", (v["nr"],))
    assert order["table_label"] == "7" and order["payment"] == "payrexx" and order["total_cents"] == 2 * beer["price"]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM print_jobs WHERE order_id=?", (v["nr"],)) >= 1
    # nochmals abgefragt / Webhook: keine zweite Bestellung
    client.post("/api/gast/webhook", headers=G, json={"transaction": {"referenceId": "FestPOS-online-1"}})
    assert client.get(f"/api/gast/order/{o['token']}", headers=G).json()["nr"] == v["nr"]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 1
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["count"] == 1 and s["payrexx"]["cents"] == 2 * beer["price"]
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert "gast_paid" in audit and KEY not in audit


def test_soldout_after_payment_is_refunded(client, gast):
    from app import core
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    core.rt.store.execute("UPDATE products SET soldout=1 WHERE id=?", (beer["id"],))      # inzwischen ausverkauft
    gast.gateways[1]["status"] = "confirmed"
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "rejected" and v["reason"] == "soldout" and v["refunded"] == beer["price"]
    assert gast.refunds == [("7001", beer["price"])]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM orders") == 0


def test_storno_refunds_and_closing_counts_net(client, gast):
    from app import core
    p = list(_products(client).values())
    o = _order(client, [{"product_id": p[0]["id"], "qty": 2}, {"product_id": p[1]["id"], "qty": 1}]).json()
    gast.gateways[1]["status"] = "confirmed"
    nr = client.get(f"/api/gast/order/{o['token']}", headers=G).json()["nr"]
    line = core.rt.store.query_one("SELECT line_no, price_cents FROM order_items WHERE order_id=? AND product_id=?",
                                   (nr, p[1]["id"]))
    r = client.post(f"/api/admin/orders/{nr}/storno", auth=AUTH, json={"reason": "wrong", "client_uuid": "st-000000001",
                                                                       "lines": [{"line_no": line["line_no"], "qty": 1}]})
    assert r.status_code == 200, r.text
    assert gast.refunds == [("7001", line["price_cents"])]
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["payrexx"]["cents"] == o["amount"] - line["price_cents"]               # nur, was wirklich geblieben ist


def test_admin_refund_button_and_failure_stays_visible(client, gast):
    from app import core
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 3}]).json()
    gast.gateways[1]["status"] = "confirmed"
    client.get(f"/api/gast/order/{o['token']}", headers=G)
    gid = client.get("/api/admin/gast/orders", auth=AUTH).json()["orders"][0]["id"]
    gast.refund_fails = True
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 502
    row = client.get("/api/admin/gast/orders", auth=AUTH).json()["orders"][0]
    assert row["refund_open"] == 3 * beer["price"] and "gast_refund_failed" in json.dumps(
        client.get("/api/admin/audit", auth=AUTH).json())
    gast.refund_fails = False
    r = client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={})
    assert r.status_code == 200 and r.json()["status"] == "refunded" and r.json()["refunded_cents"] == 3 * beer["price"]
    assert core.rt.store.scalar("SELECT COUNT(*) FROM cancellations") == 1             # Storno im Kassenbuch
    assert client.post(f"/api/admin/gast/orders/{gid}/refund", auth=AUTH, json={}).status_code == 409   # nie doppelt
    assert client.get(f"/api/gast/order/{o['token']}", headers=G).json()["status"] == "refunded"


def test_not_printed_in_time_is_refunded(client, gast):
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 1}]).json()
    gast.gateways[1]["status"] = "confirmed"
    client.get(f"/api/gast/order/{o['token']}", headers=G)
    core.rt.store.execute("UPDATE gast_orders SET paid_at='2020-01-01T10:00:00'")    # vor langer Zeit bezahlt
    import time
    for _ in range(50):                    # der Spooler im Test versucht gleichzeitig zu drucken (Drucker gibt es nicht)
        core.rt.store.execute("UPDATE print_jobs SET status='pending' WHERE status<>'cancelled'")   # nichts gedruckt
        g.tick()
        if core.rt.store.scalar("SELECT status FROM gast_orders") == "refunded":
            break
        time.sleep(0.05)
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "refunded" and v["reason"] == "printer" and gast.refunds == [("7001", beer["price"])]


def test_checks_closed_limits_options_and_cancel(client, gast):
    from app import core
    p = list(_products(client).values())
    pid = p[0]["id"]
    core.rt.store.execute("UPDATE products SET options=? WHERE id=?",
                          (json.dumps({"groups": [{"name": "Grösse", "choices": ["klein", "gross"], "required": True,
                                                   "multi": False}]}), pid))
    assert _order(client, [{"product_id": pid, "qty": 1}]).status_code == 409                         # Pflicht fehlt
    assert _order(client, [{"product_id": pid, "qty": 1, "options": ["riesig"]}], key="key-0000002").status_code == 409
    assert _order(client, [{"product_id": pid, "qty": 21, "options": ["gross"]}], key="key-0000003").status_code == 422
    client.put("/api/admin/gast", auth=AUTH, json={"open": True, "domain": "bestellen.example.ch", "instance": "theater",
                                                   "max_cents": 500})
    assert _order(client, [{"product_id": p[1]["id"], "qty": 10}], key="key-0000004").status_code == 409   # Höchstbetrag
    client.put("/api/admin/gast", auth=AUTH, json={"open": True, "domain": "bestellen.example.ch", "instance": "theater"})
    o = _order(client, [{"product_id": pid, "qty": 1, "options": ["gross"]}], key="key-0000005").json()
    assert json.loads(core.rt.store.scalar("SELECT lines FROM gast_orders WHERE token=?", (o["token"],)))[0]["options"] == ["gross"]
    v = client.post(f"/api/gast/order/{o['token']}/cancel", headers=G).json()
    assert v["status"] == "cancelled" and any(m == "DELETE" for m, _ in gast.calls)
    client.put("/api/admin/gast", auth=AUTH, json={"open": False, "domain": "bestellen.example.ch", "instance": "theater"})
    assert client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}, headers=G).json()["closed"] == "off"
    assert _order(client, [{"product_id": p[1]["id"], "qty": 1}], key="key-0000006").status_code == 409


def test_settings_key_sealed_and_qr_pdf(client, gast):
    from app import core
    v = client.get("/api/admin/gast", auth=AUTH).json()
    assert v["key_set"] and v["key_ok"] and "key" not in v and KEY not in json.dumps(v)
    assert KEY not in (core.rt.store.setting("gast_key_enc") or "")
    assert not any(k.startswith("gast_") for k in client.get("/api/admin/settings", auth=AUTH).json())
    assert client.post("/api/admin/gast/test", auth=AUTH).json() == {"ok": True}
    pdf = client.get("/api/admin/gast/qr.pdf", auth=AUTH)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_cloudflare_headers_never_reach_the_app(client, gast):
    """Falls der Tunnel aus Versehen auf FestPOS statt auf den Gästedienst zeigt: alles gesperrt."""
    assert client.get("/api/health", headers={"cf-ray": "abc"}).status_code == 403
    assert client.get("/api/admin/stats", auth=AUTH, headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 403


def test_pi_setup_units_and_firewall():
    """Tunnel nur auf den Gästedienst, eigener Benutzer in der Sandbox, Firewall lässt ihn nur zu FestPOS."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    node = (root / "deploy/festpos-node.sh").read_text()
    gast = (root / "deploy/systemd/festpos-gast.service").read_text()
    tunnel = (root / "deploy/systemd/festpos-tunnel.service").read_text()
    assert "User=festpos-gast" in gast and "--host 127.0.0.1 --port 8090" in gast
    for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "IPAddressDeny=any", "IPAddressAllow=localhost",
                 "SocketBindAllow=tcp:8090", "CapabilityBoundingSet="):
        assert line in gast, line
    hidden = next(x for x in gast.splitlines() if x.startswith("InaccessiblePaths=")).split("=", 1)[1].split()
    assert {"-/var/lib/festpos", "-/etc/festpos", "-/run/festpos-geheim", "-/var/lib/festpos-krypt"} <= set(hidden)
    assert "User=festpos-tunnel" in tunnel and "DynamicUser" not in tunnel.replace("statt DynamicUser", "")
    assert "EnvironmentFile=/etc/festpos/tunnel.env" in tunnel
    # Review 2026-11, S4: cloudflared erreicht auf dem Pi nur den Gästedienst, nicht rqlite und nicht das Festnetz
    assert "meta skuid $TUNNEL_UID ip daddr 127.0.0.1 tcp dport 8090 accept" in node
    assert 'meta skuid $TUNNEL_UID oifname \\"lo\\" drop' in node and "192.168.0.0/16" in node
    assert "useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin festpos-tunnel" in node
    assert "festpos-tunnel" in (root / "deploy/install-lib.sh").read_text()
    assert 'meta skuid $GAST_UID ip daddr 127.0.0.1 tcp dport 8443 accept' in node and "meta skuid $GAST_UID drop" in node
    assert "(umask 077; printf 'TUNNEL_TOKEN=%s" in node                                  # Token nur für root lesbar
    assert "systemctl disable --now festpos-tunnel.service festpos-gast.service" in node   # ohne Token: alles aus
    conf = (root / "deploy/festpos-conf.sh").read_text()
    assert "INTERNET_GATEWAY INTERNET_DNS TUNNEL_TOKEN" in conf


def test_paid_but_not_created_is_refunded_and_refund_never_twice(client, gast):
    """Bezahlt, aber die Bestellung liess sich nicht anlegen: nach 2 Minuten zurück. Rückerstattungen nie über den Betrag."""
    from app import core, gast as g
    beer = next(iter(_products(client).values()))
    o = _order(client, [{"product_id": beer["id"], "qty": 2}]).json()
    gid = core.rt.store.scalar("SELECT id FROM gast_orders WHERE token=?", (o["token"],))
    core.rt.store.execute("UPDATE gast_orders SET status='paid', transaction_id='7001', paid_at='2020-01-01T10:00:00' "
                          "WHERE id=?", (gid,))
    g.tick()
    v = client.get(f"/api/gast/order/{o['token']}", headers=G).json()
    assert v["status"] == "rejected" and v["refunded"] == 2 * beer["price"] and gast.refunds == [("7001", 2 * beer["price"])]
    assert g.do_refund(gid, 1, "test") is False and len(gast.refunds) == 1                     # nichts mehr offen
    # Webhook-Flut: höchstens alle paar Sekunden eine Nachfrage pro Zahlung
    o2 = _order(client, [{"product_id": beer["id"], "qty": 1}], key="key-0000099").json()
    gid2 = core.rt.store.scalar("SELECT id FROM gast_orders WHERE token=?", (o2["token"],))
    before = len([c for c in gast.calls if c[0] == "GET"])
    for _ in range(10):
        client.post("/api/gast/webhook", headers=G, json={"transaction": {"referenceId": f"FestPOS-online-{gid2}"}})
    assert len([c for c in gast.calls if c[0] == "GET"]) - before <= 1
    # gleicher Schlüssel, anderer Tisch: kein fremder Beleg
    assert _order(client, [{"product_id": beer["id"], "qty": 1}], key="key-0000099", table="8").status_code == 409


def test_old_orders_without_payment_are_cleaned_up(client, gast):
    """Review 2026-11, S7: angefangene Bezahlvorgänge ohne Geld verschwinden nach einem Tag (nie zur Zahlung gekommen)
    bzw. zwei Wochen (abgebrochen) – bezahlte, bestellte und zurückerstattete bleiben immer."""
    import time
    from app import gast as g
    from app.core import rt
    p = _products(client)
    paid = _order(client, [{"product_id": next(iter(p.values()))["id"], "qty": 1}], key="key-bezahlt1").json()
    gast.gateways[1]["status"] = "confirmed"
    assert client.get(f"/api/gast/order/{paid['token']}", headers=G).json()["status"] == "ordered"
    for key, status, at in (("key-neu-alt01", "new", "2020-01-01T10:00:00"), ("key-fehler-01", "error", "2020-01-01T10:00:00"),
                            ("key-abbruch-1", "cancelled", "2020-01-01T10:00:00"),
                            ("key-neu-heute", "new", time.strftime("%Y-%m-%dT%H:%M:%S")),
                            ("key-abbr-neu1", "cancelled", time.strftime("%Y-%m-%dT%H:%M:%S"))):
        rt.store.execute("INSERT INTO gast_orders(token, key, table_label, lines, amount_cents, status, created_at) "
                         "VALUES (?,?,?,?,?,?,?)", (f"tok-{key}", key, "7", "[]", 650, status, at))
    rt.store.execute("UPDATE gast_orders SET created_at='2020-01-01T10:00:00' WHERE token=?", (paid["token"],))
    assert g.cleanup() == 3
    left = {r["key"] for r in rt.store.query("SELECT key FROM gast_orders")}
    assert left == {"key-bezahlt1", "key-neu-heute", "key-abbr-neu1"}
    assert g.cleanup() == 0


def test_same_choice_in_two_groups_stays_apart(client, gast):
    """Rückmeldung Punkt 8: «Beilage › Pommes» und «Extra › Pommes» – gewählt wird über Gruppe + Auswahl, nie nur
    über den Text. Nur in Gruppe 1 gewählt = nur dort; die Küche sieht, welche Gruppe gemeint ist."""
    from app import core
    pid = list(_products(client).values())[0]["id"]
    core.rt.store.execute("UPDATE products SET options=? WHERE id=?", (json.dumps({"groups": [
        {"name": "Beilage", "choices": ["Pommes", "Reis"], "required": True, "multi": False},
        {"name": "Extra", "choices": ["Pommes", "Salat"], "required": False, "multi": True}]}), pid))
    o = _order(client, [{"product_id": pid, "qty": 1, "picks": [[0, "Pommes"]]}], key="key-pick-001").json()
    lines = json.loads(core.rt.store.scalar("SELECT lines FROM gast_orders WHERE token=?", (o["token"],)))
    assert lines[0]["options"] == ["Beilage: Pommes"]
    o = _order(client, [{"product_id": pid, "qty": 1, "picks": [[0, "Reis"], [1, "Pommes"]]}], key="key-pick-002").json()
    lines = json.loads(core.rt.store.scalar("SELECT lines FROM gast_orders WHERE token=?", (o["token"],)))
    assert lines[0]["options"] == ["Reis", "Extra: Pommes"]
    assert _order(client, [{"product_id": pid, "qty": 1, "picks": [[1, "Pommes"]]}], key="key-pick-003").status_code == 409
    assert _order(client, [{"product_id": pid, "qty": 1, "picks": [[0, "Salat"]]}], key="key-pick-004").status_code == 409
    assert _order(client, [{"product_id": pid, "qty": 1, "picks": [[5, "Pommes"]]}], key="key-pick-005").status_code == 409
    o = _order(client, [{"product_id": pid, "qty": 1, "picks": [[0, "Beilage", "Pommes"]]}], key="key-pick-006").json()
    assert json.loads(core.rt.store.scalar("SELECT lines FROM gast_orders WHERE token=?", (o["token"],)))[0]["options"] == ["Beilage: Pommes"]
    # Gruppen inzwischen umsortiert (Name passt nicht mehr zur Nummer): abgelehnt statt in der falschen Gruppe
    picks = [[0, "Beilage", "Reis"], [1, "Beilage", "Pommes"]]          # Pflichtgruppe gewählt: nur der Name ist falsch
    assert _order(client, [{"product_id": pid, "qty": 1, "picks": picks}], key="key-pick-007").status_code == 409
    ok = [[0, "Beilage", "Reis"], [1, "Extra", "Pommes"]]
    assert _order(client, [{"product_id": pid, "qty": 1, "picks": ok}], key="key-pick-008").status_code == 200
