"""Verwaltung: Abmelden nach Inaktivität – im Browser mit vorgestellter Uhr (Playwright clock).

Ohne Bedienung erscheint eine Minute vor der Frist «Noch da?»; «Angemeldet bleiben» holt ein neues Token. Das
automatische Aktualisieren der Übersicht verlängert nicht. Nach der Frist: Anmeldung mit Grund. Rolle Anzeige ohne
Frist. Einstellung unter Benutzer › Zugriff auf die Verwaltung. Am Handy (412 und 360 px) ohne Überbreite."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _confirm, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


def _jump(page, span: str) -> None:
    """Uhr vorstellen (fällige Zeitgeber laufen je einmal) und eine Sekunde weiterlaufen lassen."""
    page.clock.fast_forward(span)
    page.clock.run_for(1000)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("idle"))
    r = c.post("/api/admin/users", auth=AUTH, json={"name": "Vera", "role": "view", "password": "Vera-passwort"})
    assert r.status_code == 200, r.text
    yield base, c
    proc.terminate()
    proc.wait(10)


def _login(browser, base, name="admin", pw="geheim", width=1300):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    page.clock.install()
    errs = _watch(page)
    refreshes: list[str] = []
    page.on("request", lambda r: refreshes.append(r.url) if r.url.endswith("/api/admin/me/refresh") else None)
    page.goto(base + "/admin.html")
    page.fill("#user", name)
    page.fill("#pw", pw)
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    page.wait_for_selector("#main h2")
    return ctx, page, errs, refreshes


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_warning_stay_and_logout(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs, refreshes = _login(browser, base, width=width)
    _jump(page, "28:30")                                  # Übersicht aktualisiert sich – zählt nicht
    assert page.locator("#idle-dlg").count() == 0 and not refreshes
    _jump(page, "01:00")
    page.wait_for_selector("#idle-dlg[open]")
    assert "abgemeldet" in page.inner_text("#idle-left")
    assert page.locator("#idle-dlg .btn.pri").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.click("#idle-stay")
    _until(page, "!document.getElementById('idle-dlg')")
    assert len(refreshes) == 1
    _jump(page, "29:05")                                  # neue Frist ab «Angemeldet bleiben»
    assert page.locator("#idle-dlg").count() == 1
    _jump(page, "01:05")
    page.wait_for_selector("#login")
    assert "Nach 30 Minuten ohne Bedienung abgemeldet" in page.inner_text("#login")
    assert page.evaluate("sessionStorage.getItem('festpos.token')") is None
    assert not [e for e in errs if "401" not in e], errs
    ctx.close()


def test_activity_extends_at_most_once_a_minute(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs, refreshes = _login(browser, base)
    _jump(page, "02:00")
    for _ in range(5):
        page.mouse.click(5, 890)                                 # irgendwo klicken = Bedienung
    _until(page, "true")
    page.wait_for_timeout(300)
    assert len(refreshes) == 1
    _jump(page, "28:30")                                  # Frist ab dem Klick: noch kein Hinweis
    assert page.locator("#idle-dlg").count() == 0
    _jump(page, "00:35")
    page.wait_for_selector("#idle-dlg[open]")
    assert not errs, errs
    ctx.close()


def test_display_role_never_idles(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs, refreshes = _login(browser, base, "Vera", "Vera-passwort")
    _jump(page, "02:00:00")
    assert page.locator("#idle-dlg").count() == 0 and page.locator("#login").count() == 0 and not refreshes
    assert not errs, errs
    ctx.close()


def test_setting_on_users_page(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs, _ = _login(browser, base)
    page.goto(base + "/admin.html#users")
    page.reload()
    page.wait_for_selector("#idle-form")
    assert page.input_value("#idle-min") == "30"
    page.fill("#idle-min", "5")                                  # zu kurz: der Browser lässt nicht speichern
    page.click("#idle-form button[type=submit]")
    assert page.evaluate("document.getElementById('idle-min').checkValidity()") is False
    assert "admin_idle_minutes" not in c.get("/api/admin/settings", auth=AUTH).json()
    page.fill("#idle-min", "45")
    page.click("#idle-form button[type=submit]")
    _confirm(page)                                               # Zugriffs-Einstellung: kritisch (#9)
    page.wait_for_selector(".toast:not(.bad)")
    assert c.get("/api/admin/settings", auth=AUTH).json()["admin_idle_minutes"] == "45"
    c.put("/api/admin/settings", auth=AUTH, json={"admin_idle_minutes": 30})
    assert not errs, errs
    ctx.close()
