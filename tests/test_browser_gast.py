"""T22 im Browser (Handy 412 und 360 px): Gästeseite hinter dem echten Gästedienst – Speisekarte, Warenkorb, weiter zu
Payrexx (gefälscht), zurück, «Bezahlt», Bestellnummer; ein grüner Hauptknopf unten; strenge CSP ohne Fehler."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_gast import KEY, FakePayrexx  # noqa: E402
from tests.conftest import free_port  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SECRET = "browser-test-geheimnis-0123456789abcdef"
TOKEN = "browser-gast-token-0123456789"


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gast")
    fake = FakePayrexx()
    tok = tmp / "token"
    tok.write_text(TOKEN)
    proc, base, c = _start(tmp, {"FESTPOS_PAYREXX_BASE": fake.base, "FESTPOS_CLUSTER_SECRET": SECRET,
                                 "FESTPOS_GAST_TOKEN_FILE": str(tok)})
    r = c.put("/api/admin/gast", auth=AUTH, json={"open": True, "domain": "bestellen.example.ch", "instance": "theater",
                                                  "key": KEY})
    assert r.status_code == 200, r.text
    port = free_port()
    env = {**os.environ, "FESTPOS_GAST_UPSTREAM_PORT": base.rsplit(":", 1)[1], "FESTPOS_GAST_UPSTREAM_TLS": "0",
           "FESTPOS_GAST_TOKEN_FILE": str(tok), "FESTPOS_STATIC": str(ROOT / "static")}
    gp = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.gast_proxy:app", "--host", "127.0.0.1", "--port", str(port)],
                          cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gbase = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(gbase + "/", timeout=1).status_code == 200:
                break
        except Exception:  # noqa: BLE001 – startet noch
            time.sleep(0.1)
    yield base, gbase, c, fake
    gp.terminate()
    proc.terminate()
    gp.wait(10)
    proc.wait(10)
    fake.srv.shutdown()


def _url(table: str) -> str:
    from app import core, gast
    old = core.Config.cluster_secret
    core.Config.cluster_secret = SECRET
    try:
        return f"/?t={table}&s={gast.table_sig(table)}"
    finally:
        core.Config.cluster_secret = old


def _greens(page) -> int:
    return page.evaluate("[...document.querySelectorAll('.g-foot .go')].filter(b => b.offsetParent).length")


@pytest.mark.parametrize("width", [412, 360])
def test_menu_and_cart_fit(shop, browser, width):  # noqa: F811
    base, gbase, c, fake = shop
    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, locale="de-CH")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(gbase + _url("7"))
    page.wait_for_selector(".g-p")
    assert "Tisch 7" in page.inner_text(".g-top") and _greens(page) == 1
    assert page.locator(".g-foot .go").is_disabled()                       # noch nichts gewählt
    page.click(".g-p .g-add >> nth=0")
    page.click(".g-p .qty button:has-text('+') >> nth=0")
    assert "CHF" in page.inner_text(".g-foot .go") and page.locator(".g-foot .go").is_enabled()
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.click(".g-foot .go")
    page.wait_for_selector(".g-total")
    assert _greens(page) == 1 and "Bezahlen" in page.inner_text(".g-foot .go")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.select_option(".g-lang select", "fr")
    _until(page, "document.querySelector('.g-foot .go').textContent.includes('CHF') && document.documentElement.lang.startsWith('fr')")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_pay_and_see_order(shop, browser):  # noqa: F811
    base, gbase, c, fake = shop
    ctx = browser.new_context(viewport={"width": 412, "height": 892}, has_touch=True, locale="de-CH")
    ctx.route("https://pay.example/**", lambda r: r.fulfill(status=200, content_type="text/html",
                                                              body="<h1>Payrexx (Test)</h1>"))
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(gbase + _url("8"))
    page.wait_for_selector(".g-p")
    page.click(".g-p .g-add >> nth=0")
    page.click(".g-foot .go")
    page.wait_for_selector(".g-total")
    page.click(".g-foot .go")                                            # Bezahlen -> Payrexx
    page.wait_for_url("https://pay.example/**")
    gid = max(fake.gateways)
    back = fake.gateways[gid]["params"]["successRedirectUrl"]
    token = parse_qs(urlsplit(back).query)["b"][0]
    fake.gateways[gid]["status"] = "confirmed"                          # Gast hat bezahlt
    page.goto(f"{gbase}/?b={token}")                                     # Payrexx leitet zurück
    page.wait_for_selector(".g-mark.ok", timeout=15000)
    assert "Nr." in page.inner_text(".g-nr") and _greens(page) == 1
    assert c.get("/api/admin/gast/orders", auth=AUTH).json()["orders"][0]["status"] == "ordered"
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_tip_choice_when_enabled(shop, browser):  # noqa: F811
    """Trinkgeld (Verwaltung › Kasse eingeschaltet): im Warenkorb kein / aufrunden / 5 % / 10 %, Payrexx-Betrag mit
    Trinkgeld, Beleg zeigt es separat. Auch 360 px breit."""
    base, gbase, c, fake = shop
    c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": True})
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, has_touch=True, locale="de-CH")
    ctx.route("https://pay.example/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<h1>Payrexx</h1>"))
    page = ctx.new_page()
    errs = _watch(page)
    try:
        page.goto(gbase + _url("9"))
        page.wait_for_selector(".g-p")
        page.click(".g-p .g-add >> nth=0")
        page.click(".g-foot .go")
        page.wait_for_selector(".g-tips")
        chips = page.locator(".g-tips .chip")
        assert chips.count() == 4 and chips.nth(0).get_attribute("aria-pressed") == "true"      # Standard: kein
        total = page.inner_text(".g-total >> nth=0").split("CHF")[-1].strip()
        chips.nth(3).click()                                              # 10 %
        cents = round(float(total) * 100)
        tip = max(10, int(cents * 10 / 1000 + 0.5) * 10)                 # 10 % auf 10 Rappen gerundet (wie die Seite)
        want = f"{(cents + tip) / 100:.2f}"
        assert want in page.inner_text(".g-foot .go") and "Total mit Trinkgeld" in page.inner_text("main")
        assert page.evaluate("document.documentElement.scrollWidth") <= 360 and _greens(page) == 1
        page.click(".g-foot .go")
        page.wait_for_url("https://pay.example/**")
        assert fake.gateways[max(fake.gateways)]["params"]["amount"] == str(round(float(want) * 100))
        assert not [e for e in errs if "Failed to load resource" not in e], errs
    finally:
        c.put("/api/admin/settings", auth=AUTH, json={"tips_enabled": False})
        ctx.close()


@pytest.mark.parametrize("width", [412, 360])
def test_website_and_terms_before_paying(shop, browser, width):  # noqa: F811
    """Webseite (ohne QR-Code) und Rechtsseiten am Handy, eigenes CSS wirkt nur dort; AGB vor dem Bezahlen."""
    base, gbase, c, fake = shop
    r = c.put("/api/admin/web", auth=AUTH, json={
        "org_name": "Verein Muster", "org_address": "Musterstrasse 1\n6000 Luzern", "org_email": "fest@example.ch",
        "intro": "Theater, Essen und Bar.", "when": "12.–25. März", "where": "Pfarreisaal", "terms_text": "# 1. Geltung\nGilt.",
        "privacy_text": "Nur Bestellung und Tisch.", "refund_text": "Automatisch zurück.",
        "css": ".w-hero h1 { color: rgb(1, 2, 3); }"})
    assert r.status_code == 200, r.text
    ctx = browser.new_context(viewport={"width": width, "height": 892}, has_touch=True, locale="de-CH")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(gbase + "/")
    page.wait_for_selector(".w-hero h1")
    assert page.evaluate("getComputedStyle(document.querySelector('.w-hero h1')).color") == "rgb(1, 2, 3)"
    assert "Speisekarte" in page.inner_text("main") and page.locator(".w-item").count() >= 1
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.click(".w-foot >> text=AGB")
    page.wait_for_selector(".w-doc h3")
    assert page.inner_text(".w-doc h3") == "1. Geltung" and page.url.endswith("/agb")
    page.click(".w-foot >> text=FR")
    _until(page, "document.documentElement.lang === 'fr'")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    # Bestellseite: eigenes CSS gilt dort nicht, AGB-Kästchen vor dem Bezahlen
    page.goto(gbase + _url("7"))
    page.wait_for_selector(".g-p")
    assert not page.evaluate("[...document.styleSheets].some(s => (s.href || '').includes('custom.css'))")
    page.click(".g-p .g-add >> nth=0")
    page.click(".g-foot .go")
    page.wait_for_selector(".g-accept")
    assert page.locator(".g-foot .go").is_disabled() and _greens(page) == 1
    page.check(".g-accept input")
    _until(page, "!document.querySelector('.g-foot .go').disabled")
    assert page.get_attribute(".g-accept a >> nth=0", "href") == "/agb"
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    c.put("/api/admin/web", auth=AUTH, json={"terms_text": "", "privacy_text": "", "refund_text": "", "css": ""})
    ctx.close()


def test_same_choice_in_two_groups(shop, browser):  # noqa: F811
    """Punkt 8: «Pommes» in Gruppe Beilage antippen wählt NICHT auch «Pommes» unter Extra."""
    import json
    base, gbase, c, fake = shop
    p = c.get("/api/admin/products", auth=AUTH).json()[0]
    r = c.put(f"/api/admin/products/{p['id']}/options", auth=AUTH, json={"groups": [
        {"name": "Beilage", "choices": ["Pommes", "Reis"], "required": True, "multi": False},
        {"name": "Extra", "choices": ["Pommes", "Salat"], "required": False, "multi": True}]})
    assert r.status_code == 200, r.text
    ctx = browser.new_context(viewport={"width": 412, "height": 892}, has_touch=True, locale="de-CH")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(gbase + _url("9"))
    page.wait_for_selector(".g-p")
    page.click(f".g-p .g-add[data-p='{p['id']}']")
    page.wait_for_selector(".sheet")
    page.click(".sheet .chip[data-g='0'][data-c='0']")                   # Beilage › Pommes
    pressed = page.evaluate("[...document.querySelectorAll('.sheet .chip')].map(b => b.dataset.g + b.dataset.c + b.getAttribute('aria-pressed'))")
    assert pressed == ["00true", "01false", "10false", "11false"], pressed
    page.click(".sheet .chip[data-g='1'][data-c='0']")                   # Extra › Pommes dazu
    page.click(".sheet .chip[data-g='0'][data-c='0']")                   # Beilage › Pommes wieder weg
    pressed = page.evaluate("[...document.querySelectorAll('.sheet .chip')].map(b => b.dataset.g + b.dataset.c + b.getAttribute('aria-pressed'))")
    assert pressed == ["00false", "01false", "10true", "11false"], pressed
    page.click(".sheet .chip[data-g='0'][data-c='1']")                   # Reis
    page.click(".sheet [data-act='sheet-add']")
    cart = page.evaluate("JSON.parse(localStorage.getItem('festpos.gast.cart.9'))")
    assert cart[0]["picks"] == [[0, "Beilage", "Reis"], [1, "Extra", "Pommes"]] and cart[0]["options"] == ["Reis", "Extra: Pommes"], json.dumps(cart)
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()
