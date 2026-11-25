"""Verwaltung › Drucker › Drucker-Netz (VLAN): einschalten, Stand pro Pi, Warnung bei Druckern ausserhalb, ausschalten.
Im Browser, auch 360 px breit; wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import browser, _preconfirm, _start, _watch  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("druckernetz")
    (tmp / "dn").mkdir()
    proc, base, c = _start(tmp, {"NODE_IP": "192.168.55.11", "FESTPOS_PRINTER_NET_DIR": str(tmp / "dn")})
    yield base, c, tmp / "dn" / "druckernetz.json"
    proc.terminate()
    proc.wait(10)


def _login(ctx, base):
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#devices/printers")   # direkt auf die Seite: schmal ist das Menü eingeklappt
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#pnetcard")
    _preconfirm(page)                                    # kritische Aktionen: schon bestätigt (#9)
    page.wait_for_selector("#pnetcard")
    return page, errs


def test_switch_printer_vlan_on_and_off(server, browser):  # noqa: F811
    base, _, file = server
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page, errs = _login(ctx, base)
    assert page.locator("#pnetcard [data-pn]").first.is_hidden()             # aus: VLAN und Netz ausgeblendet
    page.click("#pnetcard .sw")
    assert page.locator("#pnetcard input[name=vlan]").input_value() == "56"
    page.fill("#pnetcard input[name=net]", "192.168.56")
    page.click("#pnetcard button.pri")
    page.wait_for_selector("#pnetcard table")
    rows = page.locator("#pnetcard tbody tr")
    assert rows.count() == 1
    assert "192.168.56.11" in rows.first.inner_text()
    assert page.locator("#pnetcard tbody .status.pending").count() == 1      # gleich nach dem Speichern: wird eingerichtet
    hint = page.locator("#pnetcard p.hint").last.inner_text()
    assert "VLAN 56" in hint and "192.168.56.11" in hint
    # Drucker aus dem Testladen stehen noch im alten Netz: Warnung
    assert page.locator("#pnetcard p.err").count() >= 1
    assert json.loads(file.read_text()) == {"vlan": 56, "net": "192.168.56"}
    # neuer Drucker: Vorschlag aus dem Drucker-Netz
    page.click("#prn-add")
    assert page.locator("dialog[open] input[name=host]").get_attribute("placeholder") == "192.168.56.21"
    page.click('dialog[open] [data-dlg="cancel"]')
    # ausschalten: erst nach Rückfrage
    page.once("dialog", lambda d: d.dismiss())
    page.click("#pnetcard .sw")
    page.click("#pnetcard button.pri")
    page.wait_for_timeout(300)
    assert json.loads(file.read_text())["vlan"] == 56                        # abgebrochen: bleibt
    page.once("dialog", lambda d: d.accept())
    page.click("#pnetcard button.pri")
    _until(page, "!document.querySelector('#pnetcard table')")
    assert json.loads(file.read_text()) == {"vlan": 0, "net": ""}
    assert page.locator("#pnetcard input[name=net]").input_value() == "192.168.56"   # Netz bleibt zum Wiedereinschalten
    assert [e for e in errs if "Failed to load resource" not in e] == []
    ctx.close()


def test_card_fits_on_narrow_screen(server, browser):  # noqa: F811
    base, c, _ = server
    c.put("/api/admin/printer-net", json={"vlan": 56, "net": "192.168.56"}, auth=("admin", "geheim"))
    ctx = browser.new_context(viewport={"width": 360, "height": 800})
    page, errs = _login(ctx, base)
    page.wait_for_selector("#pnetcard table")
    # die Karte und alles darin passt; nur die Tabelle darf in ihrem Rahmen (.scroll) seitlich scrollen
    wide = page.evaluate("""[document.querySelector('#pnetcard'), ...document.querySelectorAll('#pnetcard *')]
      .filter((e) => !e.closest('.scroll') && e.getBoundingClientRect().right > innerWidth + 1).map((e) => e.outerHTML.slice(0, 60))""")
    assert wide == []
    assert [e for e in errs if "Failed to load resource" not in e] == []
    c.put("/api/admin/printer-net", json={"vlan": 0, "net": ""}, auth=("admin", "geheim"))
    ctx.close()
