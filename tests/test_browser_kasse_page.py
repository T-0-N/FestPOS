"""T28: Verwaltung › Kasse – aufklappbare Karten mit Zustand im Titel und EIN Speichern-Balken
(«Geändert – Verwerfen · Speichern»). Dazu das Menü: T34 Kasse & App und Geräte & Netz mit Reitern, keine alten
Adressen mehr. Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("kassepage"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, hash_="pos/kasse", width=1300):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#" + hash_)
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    return ctx, page, errs


def test_kasse_cards_status_and_one_save_bar(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _admin(browser, base)
    page.wait_for_selector("#kpage .k-card")
    titles = [" ".join(x.split()) for x in page.locator("#kpage .k-card > summary h3").all_inner_texts()]
    assert titles == ["Abrechnen", "Zahlarten & Trinkgeld", "MWST", "Karte/TWINT (Payrexx)", "QR-Bestellung am Tisch",
                      "Webseite & Rechtliches"]
    assert page.inner_text("#kst-tab") == "Sofort kassieren" and page.inner_text("#kst-pay") == "Nur bar · ohne Trinkgeld"
    assert page.inner_text("#kst-vat") == "aus" and page.inner_text("#kst-gast") == "geschlossen"
    assert page.locator("#kpage .k-card[open]").count() == 0                  # alles zugeklappt
    assert page.locator("#main .btn.pri:visible").count() == 0                # kein grüner Knopf, solange nichts geändert ist
    page.click('[data-card="pay"] > summary')
    page.select_option('[data-k="payment_mode"]', "optional")
    page.check('[data-k="tips_enabled"]')
    assert page.is_visible("#savebar") and "Zahlart an der Kasse" in page.inner_text("#savebar-what")
    assert page.inner_text("#kst-pay") == "Zahlart freiwillig · mit Trinkgeld"   # Zustand zeigt schon das Neue
    assert page.locator("#main .btn.pri:visible").count() == 1                # genau ein grüner Knopf: Speichern
    page.click("#save-discard")                                               # Verwerfen: alter Stand
    _until(page, "document.getElementById('savebar').hidden")
    assert page.inner_text("#kst-pay") == "Nur bar · ohne Trinkgeld"
    # ändern und speichern: auch MWST und QR-Bestellung über denselben Balken
    if not page.locator('[data-card="pay"][open]').count():
        page.click('[data-card="pay"] > summary')
    page.select_option('[data-k="payment_mode"]', "required")
    page.click('[data-card="vat"] > summary')
    page.fill('[data-k="number"][data-api="vat"]', "CHE-123.456.789 MWST")
    page.click('[data-card="tab"] > summary')
    page.check('input[name="tabmode"][value="open"]')
    page.click("#save-go")
    _until(page, "document.getElementById('savebar') && document.getElementById('savebar').hidden")
    s = c.get("/api/admin/settings", auth=AUTH).json()
    assert s["payment_mode"] == "required" and s["tab_mode"] == "open"
    assert c.get("/api/admin/vat", auth=AUTH).json()["number"] == "CHE-123.456.789 MWST"
    _until(page, "!!document.querySelector('.side [data-page=\"opentabs\"]')")   # «Offene Tische» im Menü
    assert page.inner_text("#kst-tab") == "Tisch offen lassen"
    # ungespeichert wegklicken: Rückfrage
    page.select_option('[data-k="payment_mode"]', "off")
    page.once("dialog", lambda d: d.dismiss())
    page.click('.side [data-page="overview"]')
    assert page.inner_text("#main h2") == "Kasse & App"                       # geblieben
    page.once("dialog", lambda d: d.accept())
    page.click('.side [data-page="overview"]')
    _until(page, "document.querySelector('#main h2').textContent === 'Übersicht'")
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "now"})
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


@pytest.mark.parametrize("width", [412, 360])
def test_kasse_on_phone_fits(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base, width=width)
    page.wait_for_selector("#kpage .k-card")
    page.click('[data-card="pay"] > summary')
    page.select_option('[data-k="payment_mode"]', "optional")
    assert page.is_visible("#savebar")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.click("#save-discard")
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_new_menu_tabs_and_no_old_addresses(server, browser):  # noqa: F811
    """T34: Kasse & App und Geräte & Netz je ein Menüpunkt mit Reitern; alte Adressen gibt es nicht mehr."""
    base, _ = server
    ctx, page, errs = _admin(browser, base, hash_="overview")
    pages = [b.get_attribute("data-page") for b in page.locator(".side button[data-page]").all()]
    assert pages == ["overview", "orders", "jobs", "closing", "audit", "products", "tables", "pos", "devices", "users", "maint"]
    assert page.inner_text('.side [data-page="pos"]') == "Kasse & App"
    assert page.inner_text('.side [data-page="devices"]') == "Geräte & Netz"
    page.click('.side [data-page="pos"]')
    page.wait_for_selector("#set [data-k=event_name]")
    assert page.inner_text("#main h2") == "Kasse & App"
    assert [b.inner_text() for b in page.locator("#main .tabs [data-tab]").all()] == [
        "Veranstalter", "Kasse", "Bestell-App", "Bons & Quittung"]
    for tab, sel in [("kasse", "#kpage .k-card"), ("app", "#appsettings"), ("bon", "#bondesign .bk-item"), ("event", "#set")]:
        page.click(f'#main .tabs [data-tab="{tab}"]')
        page.wait_for_selector(sel)
        assert page.evaluate("location.hash") == f"#pos/{tab}"
    assert not page.locator(".k-moved").count()                              # Hinweis «Neu an anderer Stelle» ist weg
    page.click('.side [data-page="devices"]')
    page.wait_for_selector("#systemcard")
    assert page.inner_text("#main h2") == "Geräte & Netz"
    assert [b.inner_text() for b in page.locator("#main .tabs [data-tab]").all()] == [
        "Pi", "Handhelds & PC", "Einstellungen", "Drucker", "Netz & Internet"]
    assert not page.locator("#netform").count()                               # Netzwerk: unter Netz & Internet
    for tab, sel in [("list", "#dev-add"), ("setup", "#fapk"), ("printers", "#prn-add"), ("net", "#netform")]:
        page.click(f'#main .tabs [data-tab="{tab}"]')
        page.wait_for_selector(sel)
        assert page.evaluate("location.hash") == f"#devices/{tab}"
        assert page.inner_text("#main h2") == "Geräte & Netz"
    assert not page.locator("#bondesign").count()                             # Bons & Quittung: Kasse & App
    for old in ["settings/kasse", "settings/bon", "settings/app", "phones", "system", "printers", "internet", "kasse",
                "event", "update", "import", "bon"]:
        page.goto(base + "/admin.html#" + old)
        page.reload()
        page.wait_for_selector("#main h2")
        _until(page, "location.hash === '#overview'")
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_tabs_on_phone_scroll(server, browser):  # noqa: F811
    """T34: am Handy (360 px) passen die 5 Reiter von Geräte & Netz in eine Zeile, die waagrecht scrollt."""
    base, _ = server
    ctx, page, errs = _admin(browser, base, hash_="devices/net", width=360)
    page.wait_for_selector("#netform")
    bar = page.locator("#main .tabs")
    assert len({round(b.bounding_box()["y"]) for b in bar.locator("[data-tab]").all()}) == 1
    assert page.evaluate("document.querySelector('#main .tabs').scrollLeft") > 0       # gewählter Reiter sichtbar
    box = bar.locator('[aria-selected="true"]').bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_festpos_tap_card_under_handhelds_setup(server, browser):  # noqa: F811
    """die App «FestPOS Tap» auch unter Handhelds › Einrichtung – Stand, hochladen, installieren."""
    base, _ = server
    ctx, page, errs = _admin(browser, base, hash_="devices/setup")
    page.wait_for_selector("#ftap")
    assert "noch keine App" in page.inner_text("#ftap summary")
    assert page.locator("#ftap-install").is_disabled() and page.locator("#ftap-pick").count() == 1
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_printers_list_and_edit_dialog(server, browser):  # noqa: F811
    """T28 Stufe 2: Drucker als kompakte Liste; «Aktiv» speichert sofort; Bearbeiten/+ Drucker im Fenster."""
    base, c = server
    ctx, page, errs = _admin(browser, base, hash_="devices/printers")
    page.wait_for_selector("table.prtbl tr[data-id]")
    assert page.locator("table.prtbl tr[data-id]").count() == 2 and page.locator("#main input[name=host]").count() == 0
    row = page.locator('table.prtbl tr[data-id]:has-text("Küche")')
    pid = int(row.get_attribute("data-id"))
    row.locator("[data-active]").uncheck()                                    # aus: sofort gespeichert
    _until(page, "!!document.querySelector('.toast')")
    assert not next(p for p in c.get("/api/admin/printers", auth=AUTH).json() if p["id"] == pid)["enabled"]
    page.locator('table.prtbl tr[data-id]:has-text("Küche") [data-a="edit"]').click()
    page.wait_for_selector("dialog[open] .printer")
    page.fill('dialog[open] [name="name"]', "Küche Zelt")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector("dialog[open]", state="detached")
    p = next(p for p in c.get("/api/admin/printers", auth=AUTH).json() if p["id"] == pid)
    assert p["name"] == "Küche Zelt" and not p["enabled"]                     # Name neu, «aus» bleibt
    c.put(f"/api/admin/printers/{pid}", auth=AUTH, json={**p, "name": "Küche", "enabled": True})
    page.click("#prn-add")
    page.wait_for_selector("dialog[open] .printer")
    page.click('dialog[open] [data-dlg="cancel"]')
    page.wait_for_selector("dialog[open]", state="detached")
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_save_bar_on_event_and_app_settings(server, browser):  # noqa: F811
    """T28 Stufe 2: Veranstalter und Bestell-App (Kasse & App) mit EINEM Speichern-Balken statt eigener Knöpfe pro Karte."""
    base, c = server
    ctx, page, errs = _admin(browser, base, hash_="pos/event")
    page.wait_for_selector("#set [data-k=event_name]")
    assert page.locator("#main .btn.pri:visible").count() == 0
    page.fill("#set [data-k=event_name]", "Sommerfest")
    page.select_option("#set [data-k=table_word]", "Platz")
    assert page.is_visible("#set [data-savebar]")
    page.click("#set [data-savebar-go]")
    _until(page, "document.querySelector('#set [data-savebar]')?.hidden !== false")
    s = c.get("/api/admin/settings", auth=AUTH).json()
    assert s["event_name"] == "Sommerfest" and s["table_word"] == "Platz"
    c.put("/api/admin/settings", auth=AUTH, json={"event_name": "Vereinsfest", "table_word": "Tisch"})
    page.goto(base + "/admin.html#pos/app")
    page.reload()
    page.wait_for_selector("#appsettings [data-k=pin_enabled]")
    page.check("#appsettings [data-k=pin_enabled]")
    page.fill("#appsettings [data-k=pin]", "12")                             # zu kurz: Hinweis, nichts gespeichert
    page.click("#appsettings [data-savebar-go]")
    _until(page, "!!document.querySelector('.toast.bad')")
    page.fill("#appsettings [data-k=pin]", "4321")
    page.click("#appsettings [data-savebar-go]")
    _until(page, "document.querySelector('#appsettings [data-savebar]')?.hidden !== false")
    s = c.get("/api/admin/settings", auth=AUTH).json()
    assert s["pin_enabled"] == "1" and s["pin_set"]
    c.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": False})
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_table_word_from_event_in_admin_texts(server, browser):  # noqa: F811
    """Bezeichnung aus Anlass (Tisch oder Platz) auch in der Verwaltung – Menü, Tische, Tischplan."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"table_word": "Platz"})
    try:
        ctx, page, errs = _admin(browser, base, hash_="tables")
        page.wait_for_selector("#main h2")
        assert page.inner_text('.side [data-page="tables"]') == "Plätze"
        assert page.inner_text("#main h2") == "Plätze" and "Platzplan" in page.inner_text("#main")
        assert "Tischplan" not in page.inner_text("#main")
        assert not [e for e in errs if "Failed to load resource" not in e], errs
        ctx.close()
    finally:
        c.put("/api/admin/settings", auth=AUTH, json={"table_word": "Tisch"})


def test_reroute_open_bons_under_print_jobs(server, browser):  # noqa: F811
    """T34: «Offene Bons umleiten» auch unter Druckaufträge – Auswahl bleibt beim Aktualisieren (alle 5 s) stehen."""
    base, c = server
    printers = c.get("/api/admin/printers", auth=AUTH).json()
    ctx, page, errs = _admin(browser, base, hash_="jobs")
    page.wait_for_selector("#jobs-rr #rr-go")
    assert page.locator("#rr-from option").count() == len(printers) >= 2
    page.select_option("#rr-to", str(printers[0]["id"]))
    page.select_option("#rr-from", str(printers[1]["id"]))
    page.wait_for_timeout(5600)                                              # eine Aktualisierung abwarten
    assert page.input_value("#rr-from") == str(printers[1]["id"]) and page.input_value("#rr-to") == str(printers[0]["id"])
    page.click("#rr-go")
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('umgeleitet')")
    page.select_option("#rr-to", str(printers[1]["id"]))                     # gleicher Drucker: Hinweis
    page.click("#rr-go")
    _until(page, "!!document.querySelector('.toast.bad')")
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_gast_tunnel_token_status_and_remove(server, browser):  # noqa: F811
    """T22: Tunnel-Token in der Karte QR-Bestellung (über den Speichern-Balken, nie zurück), Stand pro Pi, entfernen."""
    from tests.test_tunnel import TOK
    base, c = server
    ctx, page, errs = _admin(browser, base)
    page.click('[data-card="gast"] > summary')
    page.wait_for_selector('[data-k="tunnel"]')
    assert page.inner_text(".gast-pis").startswith("Pi ") and "wird eingerichtet" not in page.inner_text(".gast-pis")
    assert page.locator("#gast-tunnel-off").count() == 0
    page.fill('[data-k="tunnel"]', TOK)
    assert "Tunnel-Token" in page.inner_text("#savebar-what")
    page.click("#save-go")
    _until(page, "!!document.getElementById('gast-tunnel-off')")
    assert c.get("/api/admin/gast", auth=AUTH).json()["tunnel"]["set"] is True
    assert page.input_value('[data-k="tunnel"]') == "" and "6ff42ae2" in page.get_attribute('[data-k="tunnel"]', "placeholder")
    assert "cloudflared fehlt" in page.inner_text(".gast-pis")               # Testrechner: ohne cloudflared
    page.once("dialog", lambda d: d.accept())
    page.click("#gast-tunnel-off")
    _until(page, "!document.getElementById('gast-tunnel-off')")
    assert c.get("/api/admin/gast", auth=AUTH).json()["tunnel"]["set"] is False
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_web_card_save_css_check_template_preview(server, browser):  # noqa: F811
    """Webseite & Rechtliches: über den Speichern-Balken, unsicheres CSS abgelehnt, Vorlage, Vorschau ohne Skripte."""
    base, c = server
    ctx, page, errs = _admin(browser, base)
    page.click('[data-card="web"] > summary')
    page.wait_for_selector('[data-k="org_name"]')
    assert "fehlen" in page.inner_text("#kst-web")
    page.fill('[data-k="org_name"]', "Verein Muster")
    page.fill('[data-k="org_address"]', "Musterstrasse 1, 6000 Luzern")
    page.fill('[data-k="org_email"]', "fest@example.ch")
    page.check('input[name="web-privacy"][value="link"]')
    assert page.is_visible('[data-k="privacy_url"]') and not page.is_visible('[data-k="privacy_text"]')
    page.fill('[data-k="privacy_url"]', "https://www.example.ch/datenschutz")
    page.click('[data-tpl="terms"]')
    _until(page, "document.querySelector('[data-k=\"terms_text\"]').value.includes('Verein Muster')")
    page.click('[data-tpl="refund"]')
    _until(page, "document.querySelector('[data-k=\"refund_text\"]').value.length > 50")
    page.fill('[data-k="css"]', ".w-hero { background: url(https://evil.example/x.png) }")
    page.click("#save-go")
    page.wait_for_selector(".toast")
    assert "url(" in page.inner_text(".toast") or "Adressen" in page.inner_text(".toast")
    assert c.get("/api/admin/web", auth=AUTH).json()["org_name"] == ""          # nichts gespeichert
    page.fill('[data-k="css"]', ".w-hero h1 { color: gold }")
    page.click("#save-go")
    _until(page, "document.getElementById('savebar') && document.getElementById('savebar').hidden")
    v = c.get("/api/admin/web", auth=AUTH).json()
    assert v["missing"] == [] and v["privacy_mode"] == "link" and "gold" in v["css"]
    page.click('[data-card="web"] > summary') if not page.locator('[data-card="web"][open]').count() else None
    _until(page, "document.getElementById('kst-web').textContent === 'vollständig'")
    page.click("#web-preview")
    _until(page, "(document.getElementById('web-frame')?.srcdoc || '').includes('w-hero')")
    assert page.get_attribute("#web-frame", "sandbox") == "allow-same-origin"            # keine Skripte
    page.click('[data-prev="terms"]')
    _until(page, "document.getElementById('web-frame').srcdoc.includes('<h3>')")
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()


def test_payrexx_untested_hint_and_confirm(server, browser):  # noqa: F811
    """Payrexx ist ungetestet (PAYREXX_GEPRUEFT): Hinweis auf beiden Payrexx-Karten, Rückfrage beim Einschalten –
    «Abbrechen» lässt den Schalter aus, «OK» schaltet ihn ein (gespeichert wird erst über den Balken)."""
    base, c = server
    assert c.get("/api/admin/payrexx", auth=AUTH).json()["tested"] is False
    assert c.get("/api/admin/gast", auth=AUTH).json()["tested"] is False
    ctx, page, errs = _admin(browser, base)
    page.wait_for_selector("#kpage .k-card")
    page.click('[data-card="payrexx"] > summary')
    assert "Noch nicht mit einem echten Payrexx-Konto getestet" in page.inner_text("#prxform [data-untested]")
    page.click('[data-card="gast"] > summary')
    assert page.locator("#gastform [data-untested]").count() == 1
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.click('#prxform [data-k="enabled"]')
    assert asked and "noch nicht mit einem echten Payrexx-Konto getestet" in asked[0]
    assert not page.is_checked('#prxform [data-k="enabled"]')
    page.once("dialog", lambda d: d.accept())
    page.click('#prxform [data-k="enabled"]')
    assert page.is_checked('#prxform [data-k="enabled"]')
    page.click("#save-discard")
    assert not errs
    ctx.close()
