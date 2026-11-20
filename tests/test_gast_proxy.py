"""T22: Gästedienst (app/gast_proxy.py) – das Einzige hinter dem Cloudflare Tunnel. Reicht nur die Gäste-Anfragen weiter,
mit eigenem Schlüssel, ohne Header des Gastes; alles andere 404; Bremse, Grössenlimit, Sicherheits-Header."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient


class Upstream:
    """Statt FestPOS: merkt sich jede Anfrage und antwortet mit JSON."""

    def __init__(self):
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                outer.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                   "body": body})
                out = json.dumps({"ok": True, "path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            do_GET = do_POST = _do

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    from app import gast_proxy as gp
    up = Upstream()
    tok = tmp_path / "token"
    tok.write_text("geheimer-gast-token\n")
    monkeypatch.setattr(gp, "UPSTREAM_PORT", up.port)
    monkeypatch.setattr(gp, "UPSTREAM_TLS", False)
    monkeypatch.setattr(gp, "TOKEN_FILE", tok)
    gp._hits.clear()
    yield TestClient(gp.app, client=("127.0.0.1", 50000)), up
    up.srv.shutdown()


def test_forwards_only_guest_routes_with_own_token(proxy):
    c, up = proxy
    r = c.get("/api/gast/menu?t=7&s=abcdefgh", headers={"Cookie": "x=1", "Authorization": "Basic abc",
                                                        "CF-Connecting-IP": "203.0.113.9", "CF-Ray": "123",
                                                        "X-Festpos-Gast": "vom-gast"})
    assert r.status_code == 200 and r.json()["path"] == "/api/gast/menu?t=7&s=abcdefgh"
    h = {k.lower(): v for k, v in up.seen[-1]["headers"].items()}
    assert h["x-festpos-gast"] == "geheimer-gast-token" and h["x-festpos-gast-ip"] == "203.0.113.9"
    assert not {"cookie", "authorization", "cf-ray", "cf-connecting-ip"} & set(h)
    for path in ("/api/admin/stats", "/admin.html", "/api/bootstrap", "/api/internal/x", "/api/gast/../admin/stats",
                 "/index.html/../../etc/passwd", "/app.js", "/api/phone/payrexx"):
        assert c.get(path).status_code == 404, path
    assert c.post("/api/admin/gast", json={}).status_code == 404
    assert c.put("/api/gast/order").status_code == 405
    n = len(up.seen)
    assert c.post("/api/gast/order", json={"lines": []}).status_code == 200 and len(up.seen) == n + 1


def test_pages_headers_limits(proxy):
    c, up = proxy
    r = c.get("/?t=7&s=x")
    assert r.status_code == 200 and b"/gast.js" in r.content
    for k in ("content-security-policy", "strict-transport-security", "x-frame-options", "referrer-policy"):
        assert k in r.headers
    assert "script-src 'self'" in r.headers["content-security-policy"] and "unsafe-eval" not in r.headers["content-security-policy"]
    assert c.get("/gast.js").status_code == 200 and c.get("/app.css").status_code == 200
    assert c.get("/fonts/barlow-condensed-latin-700-normal.woff2").status_code == 200
    assert c.post("/api/gast/order", content=b"x" * 40000, headers={"content-type": "application/json"}).status_code == 413
    codes = [c.post("/api/gast/order", json={}).status_code for _ in range(15)]
    assert codes.count(429) >= 2                                            # Bremse pro Gast


def test_rate_limit_counts_ipv6_per_64_and_survives_many_addresses(proxy, monkeypatch):
    """Review 2026-11, S7: ein IPv6-Anschluss hat beliebig viele Adressen – gezählt wird das /64. Und viele Adressen
    heben die Bremse nicht mehr für alle auf (früher wurden bei 20 000 Zählern alle gelöscht)."""
    from app import gast_proxy as gp
    c, up = proxy
    assert gp.rate_key("2001:db8:1:2:aaaa::1") == gp.rate_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert gp.rate_key("2001:db8:1:3::1") != gp.rate_key("2001:db8:1:2::1")
    assert gp.rate_key("::ffff:203.0.113.9") == gp.rate_key("203.0.113.9") == "203.0.113.9"
    codes = [c.post("/api/gast/order", json={}, headers={"CF-Connecting-IP": f"2001:db8:1:2::{i:x}"}).status_code
             for i in range(1, 16)]
    assert codes.count(429) >= 2 and codes[0] == 200
    # gesperrter Gast bleibt gesperrt, auch wenn der Speicher voll läuft
    monkeypatch.setattr(gp, "HITS_MAX", 50)
    for i in range(200):
        gp.limited(f"198.51.100.{i}", "get")
    assert len(gp._hits) <= 60
    assert c.post("/api/gast/order", json={}, headers={"CF-Connecting-IP": "2001:db8:1:2::77"}).status_code == 429


def test_new_orders_are_limited_over_all_guests(proxy, monkeypatch):
    """Neue Bestellungen (Zahlungsseite bei Payrexx, Schreibzugriffe auf allen Pi) von allen Gästen zusammen begrenzt;
    Speisekarte und Stand einer Bestellung zählen nicht dazu."""
    from app import gast_proxy as gp
    c, up = proxy
    monkeypatch.setitem(gp.RATE, "order", (5, 60.0))
    codes = [c.post("/api/gast/order", json={}, headers={"CF-Connecting-IP": f"203.0.113.{i}"}).status_code for i in range(1, 9)]
    assert codes == [200] * 5 + [429] * 3
    assert c.get("/api/gast/menu?t=7&s=abcdefgh", headers={"CF-Connecting-IP": "203.0.113.200"}).status_code == 200
    assert c.post("/api/gast/webhook", json={}, headers={"CF-Connecting-IP": "203.0.113.201"}).status_code == 200


def test_web_pages_get_the_strict_policy_without_scripts(proxy):
    """Review 2026-11, S18: Startseite ohne QR-Code und Rechtsseiten mit der strengen Regel aus app/web.py."""
    from app import gast_proxy as gp, web
    c, up = proxy
    assert gp.WEB_CSP == web.CSP
    for path in ("/", "/impressum", "/agb?l=fr"):
        r = c.get(path)
        assert r.status_code == 200 and "script-src 'none'" in r.headers["content-security-policy"], path
    assert "script-src 'self'" in c.get("/?t=7&s=x").headers["content-security-policy"]      # Bestellseite: eigene Skripte
    assert c.get("/api/gast/menu%0A").status_code == 404


def test_client_ip_only_trusted_from_local_cloudflared(proxy):
    from app import gast_proxy as gp
    from starlette.requests import Request
    req = Request({"type": "http", "headers": [(b"cf-connecting-ip", b"203.0.113.9")], "client": ("192.168.55.50", 1)})
    assert gp.client_ip(req) == "192.168.55.50"                            # nicht von cloudflared: nicht glauben
    req = Request({"type": "http", "headers": [(b"cf-connecting-ip", b"<script>")], "client": ("127.0.0.1", 1)})
    assert gp.client_ip(req) == "127.0.0.1"


def test_upstream_down_says_offline(proxy, monkeypatch):
    from app import gast_proxy as gp
    c, up = proxy
    monkeypatch.setattr(gp, "UPSTREAM_PORT", 1)
    assert c.get("/api/gast/menu?t=7&s=abcdefgh").status_code == 503
