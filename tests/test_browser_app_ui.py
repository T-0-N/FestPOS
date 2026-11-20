"""Version 2026.10.10-19: «App neu laden», offener Tisch in der Tischwahl, Tippen neben die Optionsauswahl
(einstellbar). Im Browser; wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _open_table, _phone, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("appui"))
    kaffee = c.post("/api/admin/products", auth=AUTH, json={"name": "Kaffee", "price_cents": 350, "category_id": None}).json()["id"]
    c.put(f"/api/admin/products/{kaffee}/options", auth=AUTH,
          json={"groups": [{"name": "Zucker", "choices": ["ohne", "mit"], "required": True}], "free": False})
    yield base, c
    proc.terminate()
    proc.wait(10)


def _kaffee(page):
    page.click('[data-act="cat"][data-id="none"]')
    page.click('.grid .tile:has-text("Kaffee")')
    page.wait_for_selector(".sheet .optg")


def _outside(page):
    page.mouse.click(200, 20)                                       # oben: abgedunkelter Hintergrund, nicht das Fenster


def test_tap_outside_options_follows_setting(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _phone(browser, base, "Optionen")
    _open_table(page, "5")
    # Standard: warnen – erst beim zweiten Tippen daneben zu
    _kaffee(page)
    _outside(page)
    assert page.locator(".sheet").count() == 1 and "Daneben getippt" in page.inner_text(".sheet .sheet-warn")
    assert "hint" in page.get_attribute('.sheet [data-act="close-sheet"]', "class")
    _outside(page)
    page.wait_for_selector(".sheet", state="detached")
    # nichts tun: nur «Abbrechen» schliesst
    c.put("/api/admin/settings", auth=AUTH, json={"options_outside": "ignore"})
    page.evaluate("loadBoot()")
    page.wait_for_function("() => S.boot.options_outside === 'ignore'")
    _kaffee(page)
    _outside(page)
    _outside(page)
    _outside(page)
    assert page.locator(".sheet").count() == 1 and "nur mit «Abbrechen»" in page.inner_text(".sheet .sheet-warn")
    page.click('.sheet [data-act="opt-pick"] >> nth=0')               # Auswahl geht weiter, Hinweis verschwindet
    assert page.locator(".sheet .sheet-warn").count() == 0
    page.click('.sheet [data-act="close-sheet"]')
    page.wait_for_selector(".sheet", state="detached")
    # schliessen wie bisher
    c.put("/api/admin/settings", auth=AUTH, json={"options_outside": "close"})
    page.evaluate("loadBoot()")
    page.wait_for_function("() => S.boot.options_outside === 'close'")
    _kaffee(page)
    _outside(page)
    page.wait_for_selector(".sheet", state="detached")
    assert not errs, errs
    ctx.close()
    c.put("/api/admin/settings", auth=AUTH, json={"options_outside": "warn"})


def test_open_table_shown_and_app_reload_keeps_it(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _phone(browser, base, "Offen")
    assert page.locator(".openbar").count() == 0
    _open_table(page, "7")
    page.click('.grid .tile >> nth=0')
    page.click('.grid .tile >> nth=0')
    page.click('[data-act="back"]')
    page.click('.sheet [data-act="leave"][data-d="keep"]')          # «Behalten und Tisch wechseln»
    page.wait_for_selector(".openbar")
    assert "Tisch 7 noch offen – 2 Artikel nicht gesendet" in page.inner_text(".openbar")
    # App neu laden (Einstellungen › Erweiterte Einstellungen): Warenkorb bleibt, Hinweis wieder da
    page.click('[data-act="settings"]')
    page.click('[data-act="settings-more"]')
    with page.expect_navigation():
        page.click('[data-act="app-reload"]')
    page.wait_for_selector(".openbar")
    assert "Tisch 7" in page.inner_text(".openbar")
    # Antippen: weiter bei Tisch 7 mit den Artikeln
    page.click(".openbar")
    page.wait_for_selector(".grid .tile")
    assert "7" in page.inner_text("header.top h1") and page.evaluate("cartCount()") == 2
    assert not errs, errs
    ctx.close()


# Schrift grösser wie bei «Schriftgrösse» in Android (Text-Zoom): alle px-Schriftgrössen der Regeln mal z
_TEXT_ZOOM = """(z) => { for (const sh of document.styleSheets) {
  const walk = (list) => { for (const r of list) { if (r.cssRules) walk(r.cssRules);
    if (r.style && r.style.fontSize.endsWith('px')) r.style.fontSize = (parseFloat(r.style.fontSize) * z) + 'px'; } };
  walk(sh.cssRules); } }"""
_BAR = """() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
  const b = document.querySelector('#tot'), s = document.querySelector('#cnt');
  return { h: r('.bar').height, small: s.getClientRects().length, smallTop: r('#cnt').height,
           amountFits: b.scrollWidth <= b.parentElement.clientWidth + 1 && r('#tot').right <= r('#send').left,
           amountLines: Math.round(r('#tot').height / parseFloat(getComputedStyle(b).fontSize)) }; }"""


@pytest.mark.parametrize("width,zoom", [(412, 1), (360, 1), (412, 1.15), (360, 1.15)])
def test_cart_bar_keeps_height(server, browser, width, zoom):  # noqa: F811
    """Leiste Warenkorb | Senden: leer, gewählt und mit grossem Betrag gleich hoch; Hinweis und Betrag einzeilig."""
    base, _ = server
    ctx, page, errs = _phone(browser, base, "Leiste")
    page.set_viewport_size({"width": width, "height": 880})
    page.evaluate(_TEXT_ZOOM, zoom)
    _open_table(page, "7")
    font = float(page.evaluate("parseFloat(getComputedStyle(document.querySelector('#cnt')).lineHeight)"))
    states = [page.evaluate(_BAR)]
    tile = page.locator(".grid .tile").first
    tile.click()
    states.append(page.evaluate(_BAR))
    for _ in range(25):                                             # Betrag über CHF 100
        tile.click()
    states.append(page.evaluate(_BAR))
    assert float(page.inner_text("#tot").split()[-1].replace("'", "")) >= 100
    assert {round(s["h"]) for s in states} == {86}, states
    for s in states:
        assert s["smallTop"] <= font + 1, states                     # Hinweis auf einer Zeile
        assert s["amountFits"] and s["amountLines"] == 1, states    # Betrag ganz und einzeilig
    assert not errs, errs
    ctx.close()
