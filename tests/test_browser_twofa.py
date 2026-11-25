"""Verwaltung: zweiter Faktor und kritische Aktionen im Browser (T-0-N/FestPOS#9).

Admin meldet sich an -> Fenster «Zweiter Faktor einrichten» (QR-Code, Schlüssel, Code, «Später (noch 3 Mal)»),
danach die Wiederherstellungs-Codes («Fertig» erst nach dem Häkchen). Nächste Anmeldung in zwei Schritten, auch mit
Wiederherstellungs-Code. Kritische Aktion (Benutzer anlegen): Fenster «Bitte bestätigen» mit Passwort und Code, danach
läuft die Aktion ohne nochmals zu klicken. PC und Handy (412 und 360 px) ohne Überbreite."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PW = "Ada-passwort-1"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("twofa"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _code(secret: str) -> str:
    from app import totp
    return totp.code_at(secret.replace(" ", ""), int(time.time()) // 30)


def _open(browser, base, width):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.wait_for_selector("#login")
    return ctx, page, errs


def _fits(page, width):
    assert page.evaluate("document.documentElement.scrollWidth") <= width


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_setup_login_and_confirm(server, browser, width):  # noqa: F811
    base, c = server
    name = f"Ada{width}"
    assert c.post("/api/admin/users", auth=AUTH, json={"name": name, "role": "admin", "password": PW}).status_code == 200
    ctx, page, errs = _open(browser, base, width)
    page.fill("#user", name)
    page.fill("#pw", PW)
    page.press("#pw", "Enter")
    # Pflicht für Admin: Einrichten kommt von selbst, «Später» dreimal
    page.wait_for_selector(".totp-dlg[open] svg")
    dlg = page.locator(".totp-dlg")
    assert "Später (noch 3 Mal)" in dlg.inner_text() and dlg.locator(".btn.pri").count() == 1
    _fits(page, width)
    secret = page.inner_text(".totp-key")
    page.fill(".totp-dlg .code-in", "000000")
    page.click(".totp-dlg .btn.pri")
    page.wait_for_selector(".totp-dlg .err:not([hidden])")
    assert "stimmt nicht" in page.inner_text(".totp-dlg .err")
    page.fill(".totp-dlg .code-in", _code(secret))
    page.click(".totp-dlg .btn.pri")
    page.wait_for_selector(".codes-dlg[open]")
    codes = page.locator(".totp-codes li").all_inner_texts()
    assert len(codes) == 10
    assert page.is_disabled(".codes-dlg .btn.pri")
    _fits(page, width)
    page.check("#codes-noted")
    page.click(".codes-dlg .btn.pri")
    _until(page, "!document.querySelector('.codes-dlg')")
    # abmelden, wieder anmelden: zweiter Schritt (Wiederherstellungs-Code, der App-Code dieses Schritts ist verbraucht)
    page.evaluate("sessionStorage.clear()")
    page.goto(base + "/admin.html")
    page.fill("#user", name)
    page.fill("#pw", PW)
    page.press("#pw", "Enter")
    page.wait_for_selector("#code")
    assert name in page.inner_text("#login")
    _fits(page, width)
    page.click("#use-recovery")
    page.fill("#code", "AAAA-AAAA")
    page.press("#code", "Enter")
    page.wait_for_selector("#login .err")
    assert "stimmt nicht" in page.inner_text("#login .err")
    page.fill("#code", codes[0])
    page.press("#code", "Enter")
    page.wait_for_selector("#main h2")
    assert page.locator(".totp-dlg").count() == 0                       # eingerichtet: kein Fenster mehr
    # kritische Aktion: Benutzer anlegen -> «Bitte bestätigen» -> läuft danach von selbst
    page.goto(base + "/admin.html#users")
    page.reload()
    page.wait_for_selector("#user-add")
    page.click("#user-add")
    page.fill("#newuser [name=name]", f"Ben{width}")
    page.fill("#newuser [name=password]", "Ben-passwort-1")
    page.click('[data-dlg="save"]')
    page.wait_for_selector(".confirm-dlg[open]")
    _fits(page, width)
    page.fill(".confirm-dlg [name=password]", PW)
    page.fill(".confirm-dlg [name=code]", codes[1])
    page.click(".confirm-dlg .btn.pri")
    _until(page, "!document.querySelector('.confirm-dlg')")
    page.wait_for_selector(f"tr[data-uid] >> text=Ben{width}")
    assert page.locator(f"tr:has-text('{name}') .status.ok").count() == 1   # Spalte «2. Faktor»: an
    assert not [e for e in errs if "428" not in e and "422" not in e and "401" not in e], errs
    ctx.close()


def test_emergency_confirm_without_code_and_cancel(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _open(browser, base, 1300)
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    assert page.locator(".totp-dlg").count() == 0                       # Notfall-Login: kein zweiter Faktor
    page.goto(base + "/admin.html#users")
    page.reload()
    page.wait_for_selector("#idle-form")
    page.fill("#idle-min", "45")
    page.click("#idle-form .btn.pri")
    page.wait_for_selector(".confirm-dlg[open]")
    assert page.locator(".confirm-dlg [name=code]").count() == 0
    page.click(".confirm-dlg [data-x]")                                  # Abbrechen: nichts geändert
    page.wait_for_selector(".toast.bad")
    assert "Nicht bestätigt" in page.inner_text(".toast")
    page.click("#idle-form .btn.pri")
    page.fill(".confirm-dlg [name=password]", "geheim")
    page.click(".confirm-dlg .btn.pri")
    page.wait_for_selector(".toast:not(.bad)")
    assert "45 Minuten" in page.inner_text(".toast")
    page.click("#idle-form .btn.pri")                                    # 5 Minuten bestätigt: ohne Fenster
    page.wait_for_selector(".toast:not(.bad)")
    assert page.locator(".confirm-dlg").count() == 0
    assert not [e for e in errs if "428" not in e], errs
    ctx.close()
