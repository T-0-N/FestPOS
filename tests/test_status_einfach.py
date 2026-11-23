"""Einfache Statusanzeige /status-einfach für alte Geräte (iPad mini, iOS 9): fertiges HTML vom Server, ein kleines
Skript in ES5 tauscht alle 10 s nur den Inhalt aus (?teil=1), ohne JavaScript lädt die Seite ganz neu."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from tests.test_api import AUTH, setup_shop

STATIC = Path(__file__).resolve().parent.parent / "static"
CSS = STATIC / "status-einfach.css"
JS = STATIC / "status-einfach.js"


def test_needs_login_like_api_status(client):
    for url in ("/status-einfach", "/status-einfach?teil=1"):       # auch der Teil-Abruf des Skripts
        r = client.get(url)
        assert r.status_code == 401, url
        assert r.headers["www-authenticate"].startswith("Basic")   # Safari fragt nach Benutzer und Passwort


def test_page_shows_orders_printers_and_updates(client):
    setup_shop(client)
    pid = {p["name"]: p["id"] for p in client.get("/api/bootstrap").json()["products"]}
    body = {"client_uuid": str(uuid.uuid4()), "table": "7", "device": "XCover-1",
            "lines": [{"product_id": pid["Bier 5dl"], "qty": 2}]}
    assert client.post("/api/orders", json=body).status_code == 200
    r = client.get("/status-einfach", auth=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    html = r.text
    assert '<noscript><meta http-equiv="refresh" content="10"></noscript>' in html   # ohne JavaScript wie bisher
    assert re.findall(r"<script[^>]*>", html) == ['<script src="/status-einfach.js">']   # kein Inline-Skript (CSP)
    version = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    assert f'<div id="s" data-sec="10" data-v="{version}">' in html
    assert "Keine Verbindung zum Server" in html                   # Hinweis, sichtbar erst mit body.off
    assert "Tisch 7" in html and "XCover-1" in html                 # Bestellung
    assert "Getränkeausgabe" in html                               # Station/Drucker
    assert "Letzte Bestellungen" in html and "Handhelds" in html and "Server" in html
    assert re.search(r"<b>1</b> Bestellungen", html)
    assert "<b>13.00</b> CHF" in html                               # Summe in Schweizer Schreibweise
    assert "status." not in re.sub(r'(href|src)="[^"]+"', "", html)  # kein Schlüssel ohne Text

    # Teil-Abruf für status-einfach.js: nur der Inhalt, mit Version im Kopf
    r = client.get("/status-einfach?teil=1", auth=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-festpos-version"] == version
    part = r.text
    assert "<html" not in part and "<head>" not in part and "<script" not in part and 'id="s"' not in part
    assert "Tisch 7" in part and "<header" in part and "<footer" in part and "<b>13.00</b> CHF" in part
    assert part.lstrip().startswith(("<header", '<div class="notice">'))   # passt so in #s der ganzen Seite


def test_css_only_old_safari_features():
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)   # ohne Kommentare
    for bad in ("display: grid", "gap:", "min(", "max(", "clamp(", "color-mix", " / ."):
        assert bad not in css, bad


def test_script_only_es5():
    """Safari 9 kann kein ES2015: das Skript muss ohne let/const, Pfeilfunktionen, fetch und Promises auskommen."""
    js = JS.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"(?m)//.*$", "", code)                           # ohne Kommentare
    code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', code)               # ohne Zeichenketten
    for bad in (r"\blet\b", r"\bconst\b", r"=>", r"\bfetch\b", r"\bPromise\b", r"\basync\b", r"\bawait\b",
                r"\bclass\b", "`", r"\.\.\.", r"\bfor\s*\([^)]*\bof\b", r"\.(includes|startsWith|endsWith|find|assign)\("):
        assert not re.search(bad, code), bad
    assert "XMLHttpRequest" in code and "/status-einfach?teil=1" in js
