"""Verwaltung im Browser (T8): Sicherungs-Passwort anzeigen und als A4 drucken, verschlüsselte Sicherung herunterladen und
einspielen (Passwort nötig), Prüfliste und Karte in der Übersicht, «Auf 3 Pi erweitern» – auch 360 px breit.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import browser, _preconfirm, _start, _watch  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PW = "Theater-Sicherung-2026"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("sicherung"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width=1300, height=900):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": height}, accept_downloads=True)
    page = ctx.new_page()
    errs = _watch(page)
    page.on("dialog", lambda d: d.accept())                        # confirm() beim Einspielen
    page.goto(base + "/admin.html")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    _preconfirm(page)                                    # kritische Aktionen: schon bestätigt (#9)
    return ctx, page, errs


def _go(page, base, hash_):
    page.goto(base + "/admin.html" + hash_)
    page.reload()
    page.wait_for_selector("#main h2")


def test_password_download_restore_and_checklist(server, browser, tmp_path):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base)
    _go(page, base, "#overview")
    page.wait_for_selector(".pw-unprinted")                       # Übersicht: noch nicht ausgedruckt
    _go(page, base, "#maint/backup")
    page.wait_for_selector("#bk-pw")
    assert "••••" in page.inner_text("#bk-pwtext")
    page.click('[data-bk="pw-show"]')
    _until(page, "!document.getElementById('bk-pwtext').textContent.includes('•')")
    pw = page.inner_text("#bk-pwtext").strip()
    assert len(pw) == 29 and pw.count("-") == 5
    page.click('[data-bk="pw-a4"]')                               # Druckansicht mit Passwort und QR-Code
    page.wait_for_selector(".pwprint svg")
    assert pw[:14] in page.inner_text(".pwprint .pwbig")
    page.click('.pwprint [data-pp="close"]')
    assert page.locator(".pwprint").count() == 0
    page.click('[data-bk="pw-printed"]')
    _until(page, "!document.querySelector('#bk-pw .warnbox')")
    # Herunterladen: Passwörter müssen gleich und lang genug sein
    page.fill('#bk-dlform [name="pw"]', PW)
    page.fill('#bk-dlform [name="pw2"]', PW + "x")
    page.click("#bk-dlform button")
    page.wait_for_selector(".toast.bad")
    page.fill('#bk-dlform [name="pw2"]', PW)
    with page.expect_download() as dl:
        page.click("#bk-dlform button")
    path = tmp_path / dl.value.suggested_filename
    dl.value.save_as(path)
    assert path.suffix == ".festpos" and path.read_bytes().startswith(b"FESTPOS-SICHERUNG\n")
    # Einspielen: Kopf wird gezeigt, fremdes Passwort -> Feld erscheint
    page.set_input_files('#bk-rsform input[type="file"]', str(path))
    page.wait_for_selector("#bk-rsinfo .filebox")
    assert "Pi A" in page.inner_text("#bk-rsinfo")
    page.click("#bk-rsgo .btn.danger")
    page.wait_for_selector("#bk-rspw")
    page.fill("#bk-rspw", PW)
    page.click("#bk-rsgo .btn.danger")
    page.wait_for_selector("#bk-check .chk li")
    text = page.inner_text("#bk-check")
    assert "Prüfliste nach dem Einspielen" in text and "Von Hand" in text and "Testmodus (Üben) auf den Handhelds: nicht erlaubt" in text
    _go(page, base, "#overview")
    page.wait_for_selector(".restore-open")
    card = page.inner_text(".restore-open")                     # 0 offen: grünes ✓, kein «0 Punkte offen»
    assert "0 Punkte" not in card and ("✓" in card) == ("in Ordnung" in card)
    _go(page, base, "#maint/backup")
    page.wait_for_selector('[data-bk="done"]')
    page.click('[data-bk="done"]')
    page.wait_for_selector("#bk-check details")
    _go(page, base, "#overview")
    page.wait_for_selector("#main .cols")
    assert page.locator(".restore-open").count() == 0
    assert [e for e in errs if "Failed to load resource" not in e] == [], errs
    ctx.close()


def test_backup_page_fits_360(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base, width=360, height=800)
    _go(page, base, "#maint/backup")
    page.wait_for_selector("#bk-dl")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    _go(page, base, "#devices/pi")
    page.wait_for_selector("#x-open")                             # (die Tabellen der Seite sind schon vor T8 breiter)
    assert page.evaluate("document.getElementById('x-open').getBoundingClientRect().right <= window.innerWidth")
    page.click("#x-open")
    page.wait_for_selector("#xwiz")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()


def test_expand_wizard(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base)
    _go(page, base, "#devices/pi")
    page.click("#x-open")
    page.wait_for_selector("#xwiz")
    assert "Karten für Pi B und C vorbereiten" in page.inner_text("#xwiz")
    assert page.is_disabled("#x-next")
    assert "noch nicht geschrieben" in page.inner_text("#x-written")       # T10: Karten hier schreiben
    page.click(".pcway summary")                                  # Weg über einen PC
    page.check("#x-ready")
    page.click("#x-next")
    page.wait_for_selector("#xwiz .chk li")                      # Prüfungen; ohne root-Helfer: Hinweis, kein Umstellen
    assert "Einzelbetrieb" in page.inner_text("#xwiz") and page.is_disabled("#x-next")
    page.click("#x-back")
    page.wait_for_selector("#x-ready")
    page.click("#x-cancel")
    page.wait_for_selector("#systemcard")
    assert [e for e in errs if "Failed to load resource" not in e] == [], errs
    ctx.close()
