"""Einfache Statusanzeige /status-einfach für alte Geräte (iPad mini, iOS 9): fertiges HTML ohne JavaScript."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from tests.test_api import AUTH, setup_shop

CSS = Path(__file__).resolve().parent.parent / "static" / "status-einfach.css"


def test_needs_login_like_api_status(client):
    r = client.get("/status-einfach")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")       # Safari fragt nach Benutzer und Passwort


def test_page_shows_orders_printers_and_reloads(client):
    setup_shop(client)
    pid = {p["name"]: p["id"] for p in client.get("/api/bootstrap").json()["products"]}
    body = {"client_uuid": str(uuid.uuid4()), "table": "7", "device": "XCover-1",
            "lines": [{"product_id": pid["Bier 5dl"], "qty": 2}]}
    assert client.post("/api/orders", json=body).status_code == 200
    r = client.get("/status-einfach", auth=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    html = r.text
    assert '<meta http-equiv="refresh" content="10">' in html
    assert "<script" not in html                                    # läuft auch ohne neues JavaScript
    assert "Tisch 7" in html and "XCover-1" in html                 # Bestellung
    assert "Getränkeausgabe" in html                               # Station/Drucker
    assert "Letzte Bestellungen" in html and "Handhelds" in html and "Server" in html
    assert re.search(r"<b>1</b> Bestellungen", html)
    assert "<b>13.00</b> CHF" in html                               # Summe in Schweizer Schreibweise
    assert "status." not in re.sub(r'href="[^"]+"', "", html)       # kein Schlüssel ohne Text


def test_css_only_old_safari_features():
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)   # ohne Kommentare
    for bad in ("display: grid", "gap:", "min(", "max(", "clamp(", "color-mix", " / ."):
        assert bad not in css, bad
