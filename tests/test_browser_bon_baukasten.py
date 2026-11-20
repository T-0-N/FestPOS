"""Verwaltung › Einstellungen › Bons & Quittung (T24, static/admin/bondesign.js): Bausteine ziehen (Maus), ▲ ▼ am
Handy (360 px), Auge ein/aus, Pflicht ohne Auge, Vorschau antippen wählt den Baustein, Eigenschaften, Freitext dazu,
Verwerfen und Speichern, Kasse ohne die alte Karte «Quittung für Gäste», keine JavaScript-Fehler.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("bonbaukasten"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _login(browser, base, width=1400, height=1000):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#pos/bon")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#bk-blocks .bk-item")
    _until(page, "document.querySelectorAll('#bk-hits .bk-hit').length > 3")
    return ctx, page, errs


def _types(page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('#bk-blocks .bk-item b')].map((b) => b.textContent)")


def _api(c, kind):
    return [b["type"] for b in c.get("/api/admin/bon-design", auth=AUTH).json()[kind]["blocks"]]


def test_builder_drag_props_save(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base)
    # Quittung: KOPIE/TEST zuerst, Prüfnummer zuletzt, Pflicht ohne Auge; Kasse ohne alte Karte
    names = _types(page)
    assert names[0] == "KOPIE / TEST" and names[-1] == "Prüfnummer"
    assert page.locator('#bk-blocks .bk-item:has-text("Positionen") .bk-eye').count() == 0
    assert page.locator('#bk-blocks .bk-item:has-text("Fusszeile") .bk-eye').count() == 1
    assert page.locator("#rcptform").count() == 0
    assert page.locator("#bk-bar").is_hidden()
    # Küchenbon: Tischnummer in der Vorschau antippen -> Eigenschaften, riesig + weiss auf schwarz
    page.click('[data-kind="ticket"]')
    _until(page, "document.querySelectorAll('#bk-hits .bk-hit').length > 3")
    table_i = page.evaluate("[...document.querySelectorAll('#bk-blocks .bk-item')].findIndex((li) => li.textContent.includes('Tischnummer'))")
    page.click(f'#bk-hits .bk-hit[data-hit="{table_i}"]', force=True)
    page.wait_for_selector('#bk-props h3:has-text("Tischnummer")')
    assert page.locator(f'#bk-blocks .bk-item[data-i="{table_i}"]').get_attribute("aria-current") == "true"
    page.click('#bk-props [data-p="size"]:has-text("riesig")')
    page.check('#bk-props input[data-p="invert"]')
    assert page.locator("#bk-bar").is_visible()
    assert "riesig" in page.locator(f'#bk-blocks .bk-item[data-i="{table_i}"] small').text_content()
    # «Zeit und Handy» mit der Maus über die Tischnummer ziehen
    src = page.locator('#bk-blocks .bk-item:has-text("Zeit und Handheld") .bk-grip').bounding_box()
    dst = page.locator('#bk-blocks .bk-item:has-text("Tischnummer")').bounding_box()
    y_before = page.evaluate("scrollY")
    page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
    page.mouse.down()
    page.mouse.move(src["x"] + 5, src["y"] - 10, steps=3)
    page.mouse.move(dst["x"] + 40, dst["y"] + 5, steps=6)
    # die Zeile schwebt (Kopie mit Schatten), an ihrem Platz eine Lücke, die über der Tischnummer steht
    assert page.locator(".bk-ghost").count() == 1 and page.locator("#bk-blocks .bk-hole").count() == 1
    # die anderen Zeilen gleiten zur Seite; Greifen am Rand (über dem Balken) scrollt nicht von selbst
    _until(page, """(() => { const h = document.querySelector('#bk-blocks .bk-hole').getBoundingClientRect();
      const t = [...document.querySelectorAll('#bk-blocks .bk-item')].find((li) => li.textContent.includes('Tischnummer'));
      return h.top < t.getBoundingClientRect().top; })()""", 3000)
    assert page.evaluate("scrollY") == y_before
    page.mouse.up()
    _until(page, "!document.querySelector('.bk-ghost') && !document.querySelector('.bk-hole')")
    names = _types(page)
    assert names.index("Zeit und Handheld") < names.index("Tischnummer")
    # Freitext dazu, Text eintippen -> Vorschau hat einen Baustein mehr
    hits = page.locator("#bk-hits .bk-hit").count()
    page.click('[data-add="text"]')
    page.wait_for_selector('#bk-props h3:has-text("Freitext")')
    page.fill('#bk-props textarea[data-p="text"]', "Bon bitte an der Ausgabe abgeben")
    _until(page, f"document.querySelectorAll('#bk-hits .bk-hit').length > {hits}")
    # Lieferzeit in der Vorschau umschalten
    page.click('[data-when="Pause"]')
    _until(page, "document.querySelector('[data-when=\"Pause\"]').getAttribute('aria-pressed') === 'true'")
    # Signalton ist nicht Pflicht: Auge schaltet ein
    page.click('#bk-blocks .bk-item:has-text("Signalton") .bk-eye')
    # Speichern
    page.click('#bk-bar [data-act="save"]')
    page.wait_for_selector("#bk-bar", state="hidden")
    d = c.get("/api/admin/bon-design", auth=AUTH).json()["ticket"]["blocks"]
    types = [b["type"] for b in d]
    assert types.index("time") < types.index("table") and "text" in types
    table = next(b for b in d if b["type"] == "table")
    assert table["size"] == "4" and table["invert"] is True
    assert next(b for b in d if b["type"] == "beep")["on"] is True
    assert next(b for b in d if b["type"] == "text")["text"] == "Bon bitte an der Ausgabe abgeben"
    # Verwerfen: Änderung weg, Balken weg
    page.click('#bk-blocks .bk-item:has-text("Anlass und Drucker") .bk-eye')
    assert page.locator("#bk-bar").is_visible()
    page.click('#bk-bar [data-act="discard"]')
    page.wait_for_selector("#bk-bar", state="hidden")
    assert not page.locator('#bk-blocks .bk-item:has-text("Anlass und Drucker")').get_attribute("class").count("off")
    # Quittung: Kopf bearbeiten landet in den Einstellungen
    page.click('[data-kind="receipt"]')
    page.click('#bk-blocks .bk-item:has-text("Kopf: Verein")')
    page.fill('#bk-props textarea[data-p="text"]', "Verein Muster\nFest 2026")
    page.click('#bk-bar [data-act="save"]')
    page.wait_for_selector("#bk-bar", state="hidden")
    assert c.get("/api/admin/settings", auth=AUTH).json()["receipt_header"] == "Verein Muster\nFest 2026"
    assert errs == [], errs
    ctx.close()


def test_builder_phone_buttons_and_width(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, 360, 800)
    page.click('[data-kind="ticket"]')
    page.wait_for_selector("#bk-blocks .bk-item .bk-mv button")
    assert page.locator("#bk-blocks .bk-item .bk-mv").first.is_visible()
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert over <= 0, over                                              # nichts ragt über den Rand
    before = _types(page)
    i = before.index("Anlass und Drucker")
    page.click(f'#bk-blocks .bk-item[data-i="{i}"] [data-mv="1"]')
    after = _types(page)
    assert after[i + 1] == "Anlass und Drucker" and after[i] == before[i + 1]
    # feste Bausteine haben keine Knöpfe; ganz oben bleibt TEST / Umgeleitet
    assert page.locator("#bk-blocks .bk-item.fixed .bk-mv").count() == 0
    # Ziehen an den unteren Rand: die Seite scrollt mit
    page.evaluate("document.querySelector('#bk-blocks').scrollIntoView({block: 'start'})")
    g = page.locator('#bk-blocks .bk-item:has-text("Logo") .bk-grip').bounding_box()
    y0 = page.evaluate("scrollY")
    page.mouse.move(g["x"] + g["width"] / 2, g["y"] + g["height"] / 2)
    page.mouse.down()
    page.mouse.move(g["x"] + 10, 790, steps=8)
    page.wait_for_timeout(600)
    assert page.evaluate("scrollY") > y0 + 50
    page.mouse.up()
    _until(page, "!document.querySelector('.bk-ghost')")
    page.click('#bk-bar [data-act="discard"]')
    assert errs == [], errs
    ctx.close()


def test_delivery_times_each_and_prices_for_all(server, browser):  # noqa: F811
    """beim Lieferzeitpunkt jede Lieferzeit einzeln wählbar; «Preise drucken» einmal für alle Küchen- und
    Getränkebons (nicht mehr beim Drucker)."""
    base, c = server
    ctx, page, errs = _login(browser, base)
    page.click('[data-kind="ticket"]')
    page.click('#bk-blocks .bk-item:has-text("Lieferzeitpunkt")')
    page.wait_for_selector('#bk-props [data-pt="when"]')
    labels = [x.get_attribute("data-tv") for x in page.locator('#bk-props [data-pt="when"]').all()]
    assert labels == ["Sofort", "Pause", "Schluss"]
    checked = [x.is_checked() for x in page.locator('#bk-props [data-pt="when"]').all()]
    assert checked == [False, True, True]                                    # Standard: alle ausser «Sofort»
    page.uncheck('#bk-props [data-pt="when"][data-tv="Pause"]')
    page.click('#bk-blocks .bk-item:has-text("Positionen")')
    page.check('#bk-props [data-p="prices"]')
    page.click('#bk-bar [data-act="save"]')
    page.wait_for_selector("#bk-bar", state="hidden")
    d = c.get("/api/admin/bon-design", auth=AUTH).json()["ticket"]["blocks"]
    assert next(b for b in d if b["type"] == "delivery")["when"] == ["Schluss"]
    assert next(b for b in d if b["type"] == "items")["prices"] is True
    page.goto(base + "/admin.html#devices/printers")
    page.reload()
    page.wait_for_selector("table.prtbl tr[data-id]")
    page.click('table.prtbl tr[data-id] >> nth=0 >> [data-a="edit"]')      # T28 Stufe 2: Bearbeiten im Fenster
    page.wait_for_selector("dialog[open] .printer")
    assert page.locator('dialog[open] [name="show_prices"]').count() == 0     # nicht mehr beim Drucker
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()
