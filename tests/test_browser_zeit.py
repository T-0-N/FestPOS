"""T2: Uhrzeit in der Verwaltung im Browser – Hinweis «Bitte Uhrzeit prüfen» nach der Anmeldung (Festleitung, Admin),
«Nicht jetzt» bis zur nächsten Anmeldung, «Zeit stimmt», Uhr in der Seitenleiste, Karte «Uhrzeit» mit manueller
Einstellung; die Rolle Anzeige sieht nichts davon; nichts zu breit bei 412 und 360 px.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_devices import _login  # noqa: E402
from tests.test_browser_p4 import AUTH, _start, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("zeit"))
    for name, role in (("Lea", "lead"), ("Vera", "view")):
        assert c.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role,
                                                              "password": f"{name}-passwort"}).status_code == 200
    yield base, c
    proc.terminate()
    proc.wait(10)


def _wide(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth")


def test_ask_later_then_confirm(server, browser):  # noqa: F811
    base, c = server
    # noch nie bestätigt: Hinweis über der Seite, Uhr in der Kopfzeile rot – am Handy, auch 360 px breit
    ctx, page, errs = _login(browser, base, "admin", "geheim", 360, 800)
    page.wait_for_selector(".side-clock:not([hidden])")
    page.wait_for_timeout(300)
    assert page.evaluate("document.querySelector('#timeask').hidden")    # T28: andere Seite, keine Abweichung -> kein Balken
    page.evaluate("location.hash = '#overview'")
    page.reload()
    page.wait_for_selector("#timeask .time-ask")                         # auf der Übersicht schon
    assert "noch nie bestätigt" in page.inner_text("#timeask")
    assert page.locator(".side-bar .side-clock.bad").is_visible() and _wide(page) <= 360
    page.click('#timeask [data-time="later"]')                          # «Nicht jetzt»: weg, auch nach dem Neuladen
    _until(page, "document.querySelector('#timeask').hidden")
    page.reload()
    page.wait_for_selector("#main h2")
    page.wait_for_selector(".side-clock:not([hidden])")
    assert page.evaluate("document.querySelector('#timeask').hidden")
    ctx.close()
    assert not errs, errs
    # neue Anmeldung (Festleitung, 412 px): wieder da; «Zeit stimmt» bestätigt
    ctx, page, errs = _login(browser, base, "Lea", "Lea-passwort", 412, 892)
    page.evaluate("location.hash = '#overview'")
    page.reload()
    page.wait_for_selector("#timeask .time-ask")
    page.click('#timeask [data-time="ok"]')
    _until(page, "document.querySelector('#timeask').hidden")
    _until(page, "!document.querySelector('.side-bar .side-clock').classList.contains('bad')")
    conf = c.get("/api/admin/time", auth=AUTH).json()["confirmed"]
    assert conf["how"] == "ok" and conf["by"] == "Lea"
    ctx.close()
    assert not errs, errs


def test_time_card_and_manual(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, "admin", "geheim", 360, 800)
    page.click('#main .tabs [data-tab="pi"]')                          # Anmeldung landet unter Handhelds & PC
    page.wait_for_selector("#systemcard .pi-row")
    card = page.inner_text("#timecard")                                 # T33: Uhrzeit beim Pi, Aktionen darunter
    assert "Zuletzt bestätigt" in card and "Lea" in card and "RTC" in page.inner_text("#systemcard .pi-row")
    assert page.evaluate("document.querySelector('#systemcard').getBoundingClientRect().right") <= 360
    assert page.locator("#time-local").is_hidden()                       # «Von Hand setzen …» zugeklappt
    page.click("#time-manual-d summary")
    assert page.input_value("#time-local")[:4] >= "2026"
    page.fill("#time-local", "")                                        # leer: Hinweis, nichts gesendet
    page.click('#timecard [data-time="set"]')
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('Datum und Uhrzeit')")
    ctx.close()
    assert not errs, errs


def test_view_role_sees_nothing(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, "Vera", "Vera-passwort", 412, 892)
    page.wait_for_selector("#main h2")
    page.wait_for_timeout(800)
    assert page.evaluate("document.querySelector('#timeask').hidden")
    assert page.evaluate("[...document.querySelectorAll('.side-clock')].every(e => e.hidden)")
    assert not page.locator('.side [data-page="devices"]').count()
    ctx.close()
    assert not errs, errs
