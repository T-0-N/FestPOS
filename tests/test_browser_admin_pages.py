"""Verwaltung: jede Seite und jeder Reiter öffnet ohne JavaScript-Fehler (seit admin.js in ES-Module aufgeteilt ist,
static/admin/). Ein vergessener Import fiele sonst erst beim Klick auf: als ReferenceError oder – weil guard() jeden
Fehler abfängt – als rote Meldung. Im Browser; wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402,F401

# rote Meldungen mitschreiben (sie verschwinden nach 7 s wieder)
BAD_TOASTS = """new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => {
  if (n.classList && n.classList.contains("toast") && n.classList.contains("bad")) (window.__bad ||= []).push(n.textContent);
}))).observe(document, { childList: true, subtree: true });"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("adminpages"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def test_every_page_and_tab_opens_without_errors(server, browser):  # noqa: F811
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    ctx.add_init_script(BAD_TOASTS)
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    pages = [b.get_attribute("data-page") for b in page.locator(".side button[data-page]").all()]
    assert len(pages) == 11, pages                       # Admin sieht alle Seiten (T34: Kasse & App, Geräte & Netz)
    seen = []
    for pg in pages:
        page.click(f'.side button[data-page="{pg}"]')
        page.wait_for_selector(f'.side button[data-page="{pg}"][aria-current="page"]')
        page.wait_for_selector("#main h2")
        page.wait_for_timeout(300)                       # nachgeladene Karten (Promise.all) abwarten
        tabs = [b.get_attribute("data-tab") for b in page.locator("#main .tabs [data-tab]").all()]
        for tab in tabs:
            page.click(f'#main .tabs [data-tab="{tab}"]')
            page.wait_for_selector(f'#main .tabs [data-tab="{tab}"][aria-selected="true"]')
            page.wait_for_timeout(150)
        seen.append((pg, tabs))
    assert sum(len(tabs) for _, tabs in seen) == 15, seen    # T34: Produkte 4, Kasse & App 4, Geräte & Netz 5, Wartung 2
    # neu laden mit angemeldeter Sitzung (Start ohne Login-Formular) – ebenfalls ohne Fehler
    page.goto(base + "/admin.html#overview")
    page.wait_for_selector("#main h2")
    page.wait_for_timeout(300)
    net = [e for e in errs if "Failed to load resource" not in e]   # erwartete 4xx (z.B. noch kein Router) zählen nicht
    assert net == [], net
    assert page.evaluate("window.__bad || []") == []
    ctx.close()


def test_stock_fields_greyed_without_stock(server, browser):  # noqa: F811
    """Produkte › Bestand: bei «Ohne Bestand» sind «Noch da» und «Warnen ab» ausgegraut, bei «Mit Restmenge» offen."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#products")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.click("#ptable [data-edit] >> nth=0")                            # T28 Stufe 2: über das Fenster
    page.click('dialog[open] [data-more="stock"]')
    page.wait_for_selector("#stockform")
    vals = page.locator("#stockvals")
    assert vals.is_visible() and vals.evaluate("e => e.disabled")    # Normalfall: ohne Bestand
    assert page.locator('#stockform input[name="stock"]').is_disabled()
    assert page.evaluate("getComputedStyle(document.querySelector('#stockvals')).opacity") == "0.45"
    page.check('#stockform input[name="mode"][value="on"]')
    assert not page.locator('#stockform input[name="warn"]').is_disabled()
    page.fill('#stockform input[name="stock"]', "20")
    page.check('#stockform input[name="mode"][value="off"]')
    assert page.locator('#stockform input[name="stock"]').is_disabled()
    page.click('#stockform button[type="submit"]')       # ohne Bestand gespeichert: Spalte «–»
    page.wait_for_selector("#stockform", state="detached")
    page.click("#ptable [data-edit] >> nth=0")
    assert page.inner_text('dialog[open] [data-more="stock"]').endswith("–")
    page.click('dialog[open] [data-dlg="cancel"]')
    assert [e for e in errs if "Failed to load resource" not in e] == []
    ctx.close()


def test_products_sort_az_button(server, browser):  # noqa: F811
    """T26: Knopf «A–Z sortieren» über der Preisliste – nach der Rückfrage stehen Kategorien und Produkte A–Z."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#products")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#ptable")
    groups = lambda: page.evaluate("[...document.querySelectorAll('#ptable tr.grp td')].map((td) => td.firstChild.textContent.trim())")  # noqa: E731
    assert groups() == ["Getränke", "Essen"]                              # Demodaten: Getränke zuerst
    page.once("dialog", lambda d: d.accept())
    page.click("#sortaz")
    page.wait_for_selector(".toast")
    page.wait_for_timeout(300)
    assert groups() == ["Essen", "Getränke"]
    names = [page.locator(f"#ptable tr[data-id] >> nth={i} >> .p-name").inner_text() for i in range(3)]
    assert names == ["Bratwurst", "Bier 5dl", "Mineral"]
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_products_compact_rows_and_edit_dialog(server, browser):  # noqa: F811
    """T28 Stufe 2: Preisliste als kompakte Zeilen; «+ Produkt» und «Bearbeiten» im Fenster; «Aktiv» sofort."""
    base, c = server
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#products")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#ptable")
    assert page.locator('#ptable input[data-f="name"], #ptable input[data-f="price"]').count() == 0
    page.click("#prod-add")
    page.wait_for_selector("dialog[open] #prodform")
    page.fill('dialog[open] [name="name"]', "Kaffee")
    page.fill('dialog[open] [name="price"]', "3.50")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector('#ptable .p-name:text-is("Kaffee")')
    pid = next(p["id"] for p in c.get("/api/admin/products", auth=AUTH).json() if p["name"] == "Kaffee")
    page.click(f'#ptable [data-edit="{pid}"]')
    page.wait_for_selector("dialog[open] #prodform")
    page.fill('dialog[open] [name="price"]', "4")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector("dialog[open]", state="detached")
    assert next(p for p in c.get("/api/admin/products", auth=AUTH).json() if p["id"] == pid)["price_cents"] == 400
    page.uncheck(f'#ptable tr[data-id="{pid}"] [data-f="active"]')          # Betriebsschalter: sofort
    page.wait_for_selector(f'#ptable tr.inactive[data-id="{pid}"]')
    assert not next(p for p in c.get("/api/admin/products", auth=AUTH).json() if p["id"] == pid)["active"]
    c.delete(f"/api/admin/products/{pid}", auth=AUTH)
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_table_cards_ask_to_switch_on_qr_scan(server, browser):  # noqa: F811
    """Rückmeldung Punkt 4: Tischkarten drucken, während «Tisch per QR-Code scannen» aus ist -> Fenster «Jetzt
    einschalten?». Abbrechen druckt nicht, «Einschalten und weiter» schaltet ein und druckt, danach ohne Frage."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"staff_qr": False})
    c.post("/api/admin/tables", auth=AUTH, json={"range_from": 1, "range_to": 3})
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#tables")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#qr")
    jobs = len(c.get("/api/admin/jobs", auth=AUTH).json())
    page.click("#qr button.pri")                                       # als Bon drucken
    page.wait_for_selector("dialog.dlg[open]")
    assert "QR-Scan am Handheld ist ausgeschaltet" in page.inner_text("dialog.dlg")
    page.click('dialog.dlg [data-dlg="cancel"]')                        # abbrechen: nichts gedruckt, bleibt aus
    page.wait_for_selector("dialog.dlg", state="detached")
    assert len(c.get("/api/admin/jobs", auth=AUTH).json()) == jobs
    assert c.get("/api/admin/settings", auth=AUTH).json().get("staff_qr") != "1"
    page.click("#qr button.pri")
    page.click('dialog.dlg [data-dlg="save"]')                          # einschalten und weiter
    for _ in range(50):                                                # gedruckt: Aufträge kommen dazu
        if len(c.get("/api/admin/jobs", auth=AUTH).json()) > jobs:
            break
        page.wait_for_timeout(100)
    after = len(c.get("/api/admin/jobs", auth=AUTH).json())
    assert after > jobs and c.get("/api/admin/settings", auth=AUTH).json().get("staff_qr") == "1"
    page.click("#qr button.pri")                                       # jetzt ohne Frage, druckt wieder
    for _ in range(50):
        if len(c.get("/api/admin/jobs", auth=AUTH).json()) > after:
            break
        page.wait_for_timeout(100)
    assert len(c.get("/api/admin/jobs", auth=AUTH).json()) > after and page.locator("dialog.dlg").count() == 0
    assert not errs, errs
    ctx.close()
