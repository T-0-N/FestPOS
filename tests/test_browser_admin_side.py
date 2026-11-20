"""Seitenleiste der Verwaltung – oben die Statusanzeige als Knopf mit Ampel (live) und die Bestell-App,
unten der Name; ein Klick darauf öffnet Sprache, Passwort ändern, Überall abmelden, Abmelden und Version.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("side"))
    r = c.post("/api/admin/users", auth=AUTH, json={"name": "Maria", "role": "admin", "password": "Muster-passwort"})
    assert r.status_code == 200, r.text                    # normaler Benutzer (der Notfall-Admin hat kein «Passwort ändern»)
    yield base, c
    proc.terminate()
    proc.wait(10)


LAMP = {"level": "bad", "bad": [{"key": "status.prn.p_paper", "name": "Küche"}], "wait": []}


def _admin(browser, base, width=1300, scheme="light"):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = ctx.new_page()
    errs = _watch(page)
    state = {"lamp": LAMP}
    page.route("**/api/admin/lamp", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(state["lamp"])))
    page.goto(base + "/admin.html")
    page.fill("#user", "Maria")
    page.fill("#pw", "Muster-passwort")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    page.wait_for_selector("#main h2")
    return ctx, page, errs, state


def test_status_button_with_live_lamp(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs, state = _admin(browser, base)
    st = page.locator('.side .jumpc[data-jump="status"]')
    first = page.evaluate("document.querySelector('.side [data-page]').getBoundingClientRect().top")
    assert st.bounding_box()["y"] < first                                  # oben, vor den Seiten
    _until(page, "document.querySelector('.lampdot').dataset.l === 'bad'")
    assert page.inner_text(".lamptext") == "Küche: Papier leer"
    bg = page.evaluate("getComputedStyle(document.querySelector('.lampdot')).backgroundColor")
    assert bg == "rgb(200, 16, 46)"                                        # rot (--bad)
    state["lamp"] = {"level": "ok", "bad": [], "wait": []}                  # live: nach höchstens 5 s
    _until(page, "document.querySelector('.lampdot').dataset.l === 'ok'", 8000)
    assert page.inner_text(".lamptext") == "Alles in Ordnung"
    state["lamp"] = {"level": "wait", "bad": [], "wait": [{"key": "status.prn.w_paper_low", "name": "Bar"},
                                                           {"key": "status.prn.w_check", "name": "Küche"}]}
    _until(page, "document.querySelector('.lampdot').dataset.l === 'wait'", 8000)
    assert page.inner_text(".lamptext") == "Bitte prüfen"                  # mehrere Hinweise: zusammengefasst
    assert "Bar: Papier fast leer" in st.get_attribute("title")            # alle im Tooltip
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [1300, 412])
def test_user_menu(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs, _ = _admin(browser, base, width)
    if width < 760:
        page.click(".side .menu-btn")
    btn, menu = page.locator(".side .userbtn"), page.locator("#usermenu")
    assert "Maria" in btn.inner_text() and "Admin" in btn.inner_text()
    assert menu.is_hidden() and page.locator('[data-logout]').is_hidden()   # Abmelden & Co. erst im Menü
    btn.click()
    assert menu.is_visible() and btn.get_attribute("aria-expanded") == "true"
    items = " ".join(menu.inner_text().split())
    for want in ("Angemeldet als Maria (Admin)", "Passwort ändern", "Überall abmelden", "Abmelden", "Version"):
        assert want in items, items
    box = menu.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width and box["y"] >= 0 and box["y"] + box["height"] <= 900   # ganz sichtbar
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.keyboard.press("Escape")
    assert menu.is_hidden() and btn.get_attribute("aria-expanded") == "false"
    btn.click()
    page.click(".side .brand")                                             # daneben (feste Fläche, nie ein Scrollbalken): zu
    assert menu.is_hidden()
    if width < 760 and not btn.is_visible():
        page.click(".side .menu-btn")
    btn.click()
    page.click('#usermenu [data-logout]')
    page.wait_for_selector("#login")
    assert not errs, errs
    ctx.close()


def test_side_dark(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs, _ = _admin(browser, base, scheme="dark")
    page.click(".side .userbtn")
    bg = page.evaluate("getComputedStyle(document.querySelector('#usermenu')).backgroundColor")
    fg = page.evaluate("getComputedStyle(document.querySelector('#usermenu [data-ownpw]')).color")
    assert bg != fg
    assert not errs, errs
    ctx.close()
