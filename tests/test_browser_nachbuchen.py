"""Nachbuchen ohne Bon am Handy (static/app/send.js): Link im Warenkorb (auch bei einem neuen Tisch) und beim
Abrechnen eines offenen Tisches; oranger Modus, grüner Knopf «Nachbuchen ohne Bon», keine Bons. Der Modus gilt nur
für diesen einen Vorgang. Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _open_table, _start, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_browser_tabs import _open_tabs, _phone, _send, _type_table  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("nbui"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _jobs(c, order_id: int) -> list[dict]:
    return [j for j in c.get("/api/admin/jobs?limit=200", auth=AUTH).json() if j["order_id"] == order_id]


def _last_order(c) -> dict:
    return c.get("/api/admin/orders?scope=all", auth=AUTH).json()["orders"][0]


def _fits(page) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "seitlich zu breit"


@pytest.mark.parametrize("width", [412, 360])
def test_new_table_from_cart(server, browser, width):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "now", "nachbuchen": "all"})
    ctx, page, errs = _phone(browser, base, width=width)
    _open_table(page, "7")
    tiles = page.locator(".grid .tile")
    tiles.nth(0).click()
    tiles.nth(1).click()
    page.click('[data-act="cart"]')
    page.wait_for_selector(".sheet .nb-warn", state="detached")
    assert page.inner_text('.sheet [data-act="send"]').strip() == "Senden"          # gewohnt: grün «Senden»
    page.click('.sheet [data-act="nb-on"]')
    page.wait_for_selector(".sheet .nb-warn")
    assert "Kein Bon für Küche und Bar" in page.inner_text(".sheet .nb-warn")
    assert page.inner_text(".sheet h2").startswith("Nachbuchen")
    assert page.inner_text('.sheet [data-act="send"]').strip() == "Nachbuchen ohne Bon"
    assert page.locator(".sheet .go").count() == 1                                   # ein grüner Knopf
    _fits(page)
    # «Doch mit Bon senden» schaltet zurück, nochmals ein
    page.click('.sheet [data-act="nb-off"]')
    page.wait_for_selector(".sheet .nb-warn", state="detached")
    assert page.inner_text('.sheet [data-act="send"]').strip() == "Senden"
    page.click('.sheet [data-act="nb-on"]')
    page.click('.sheet [data-act="close-sheet"]')                                    # weiter auswählen: oranger Balken
    page.wait_for_selector(".nb-band")
    assert page.inner_text("#send").strip() == "Nachbuchen"
    _fits(page)
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]')
    assert "nachgebucht (ohne Bon)" in page.inner_text(".toast")
    o = _last_order(c)
    assert o["table_label"] == "7" and o["no_print"] == 1 and _jobs(c, o["id"]) == []
    # nächste Bestellung: wieder normal mit Bon
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    _open_table(page, "7")
    assert page.locator(".nb-band").count() == 0
    assert page.inner_text("#send").strip() == "Senden"
    page.locator(".grid .tile").nth(0).click()
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]')
    o2 = _last_order(c)
    assert o2["no_print"] == 0 and _jobs(c, o2["id"])
    assert errs == [], errs
    ctx.close()


def test_open_table_from_settle(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open", "tab_who": "own", "nachbuchen": "all"})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "12")
    _send(page, (0, 0))                                                  # 2× Bier 5dl
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    assert page.inner_text("#bon .total b") == "13.00"
    # Abbrechen: zurück zur Abrechnung, nichts gebucht
    page.click('[data-act="nb-tab"]')
    page.wait_for_selector(".nb-band")
    page.locator(".grid .tile").nth(1).click()
    page.click('.nb-band [data-act="nb-cancel"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    assert page.inner_text("#bon .total b") == "13.00"
    assert page.evaluate("cartCount()") == 0 and page.evaluate("S.nb") is None
    # nachbuchen: 1× Bier 5dl, zurück in der Abrechnung mit neuem Total und Markierung
    page.wait_for_timeout(700)
    page.click('[data-act="nb-tab"]')
    page.wait_for_selector(".nb-band")
    assert page.locator('.sheet [data-act="tab-order"]').count() == 0           # keine Nachfrage «offener Tisch»
    page.locator(".grid .tile").nth(0).click()
    page.click('[data-act="cart"]')
    assert page.locator('.sheet [data-act="nb-off"]').count() == 0              # vom Tisch aus: nur «Abbrechen» oben
    page.click('.sheet [data-act="send"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    _until(page, "document.querySelector('#bon .total b')?.textContent === '19.50'")
    assert "nachgebucht" in page.inner_text("#bon .nbmark")
    t12 = _open_tabs(c)["12"]
    assert t12["orders"] == 2 and t12["net"] == 1950
    o = _last_order(c)
    assert o["no_print"] == 1 and _jobs(c, o["id"]) == []
    assert page.evaluate("S.nb") is None
    # Zurück führt wie gewohnt zur Tischwahl – nicht in die Produkte und nicht in eine zweite Abrechnung
    page.wait_for_timeout(700)
    page.click('[data-act="back"]')
    page.wait_for_selector('[data-act="digit"]')
    assert errs == [], errs
    ctx.close()
    c.post(f"/api/admin/tabs/{t12['id']}/settle", auth=AUTH, json={"payment": "cash"})


def test_not_allowed_shows_no_link(server, browser):  # noqa: F811
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open", "tab_who": "own", "nachbuchen": "off"})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "14")
    page.locator(".grid .tile").nth(0).click()
    page.click('[data-act="cart"]')
    assert page.locator('[data-act="nb-on"]').count() == 0
    page.click('.sheet [data-act="send"]')
    page.wait_for_selector("#tabopen")
    page.wait_for_timeout(700)
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    assert page.locator('[data-act="nb-tab"]').count() == 0
    assert errs == [], errs
    ctx.close()
    tid = _open_tabs(c)["14"]["id"]
    c.post(f"/api/admin/tabs/{tid}/settle", auth=AUTH, json={"payment": "cash"})
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "now", "nachbuchen": "all"})


def test_open_table_nachbuchen_from_einzeln_and_total_after_send(server, browser):  # noqa: F811
    """T35: «Nachbuchen ohne Bon» auch in «Einzeln» – danach zurück in Einzeln, die Auswahl bleibt.
    Nach dem Senden auf einen offenen Tisch: «Vorher …» und «Total offen» MIT der neuen Bestellung."""
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open", "tab_who": "own", "nachbuchen": "all"})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "15")
    _send(page, (0,))                                                    # 1× Bier 5dl = 6.50
    _until(page, "document.querySelector('#tabopen .tot b')?.textContent === '6.50'")
    assert page.locator("#tabopen .prev").count() == 0                  # erste Bestellung: kein «Vorher»
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    _type_table(page, "15")                                              # offen: die App fragt zuerst
    page.wait_for_selector('.sheet [data-act="tab-order"]')
    page.click('.sheet [data-act="tab-order"]')
    page.wait_for_selector(".grid .tile")
    _send(page, (0, 0))                                                  # 2× Bier 5dl = 13.00
    _until(page, "document.querySelector('#tabopen .tot b')?.textContent === '19.50'")   # Total MIT der neuen
    assert " ".join(page.inner_text("#tabopen .prev").split()).startswith("Vorher: 1 Bestellung seit")
    assert page.inner_text("#tabopen .prev b") == "6.50"
    page.click("#tabopen .tab-all summary")                              # alle Positionen am Tisch
    _until(page, "!!document.querySelector('#tabopen .tab-all .line')")
    assert "3× Bier 5dl" in page.inner_text("#tabopen .tab-all")
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    # Abrechnen › Einzeln: eine Position wählen, nachbuchen, zurück in Einzeln mit derselben Auswahl
    page.wait_for_timeout(700)
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    page.click('[data-act="mode"][data-m="part"]')
    page.locator('#pickpanel .pi[data-act="pick"]').first.click()
    assert page.evaluate("JSON.stringify(S.part.sel)") != "{}"
    sel = page.evaluate("JSON.stringify(S.part.sel)")
    page.click('#pickpanel [data-act="nb-tab"]')
    page.wait_for_selector(".nb-band")
    page.locator(".grid .tile").nth(1).click()
    page.click('[data-act="cart"]')
    page.click('.sheet [data-act="send"]')
    page.wait_for_selector("#pickpanel")                                  # wieder in Einzeln
    assert page.evaluate("S.payMode") == "part" and page.evaluate("JSON.stringify(S.part.sel)") == sel
    _until(page, "document.querySelectorAll('#pickpanel .pi').length === 2")   # nachgebucht: neue Position hinten
    assert errs == [], errs
    ctx.close()
    tid = _open_tabs(c)["15"]["id"]
    c.post(f"/api/admin/tabs/{tid}/settle", auth=AUTH, json={"payment": "cash"})
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "now"})
