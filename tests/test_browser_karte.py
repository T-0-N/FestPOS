"""Verwaltung im Browser (T10): Geräte & Netz › Pi › «SD-Karte schreiben» – Liste mit gesperrten Geräten, Buchstabe wählen,
Bestätigen mit dem Buchstaben, Fortschritt, Fertig; Aufruf aus «Auf 3 Pi erweitern»; 360 px ohne Überbreite.
Der root-Helfer läuft hier als normaler Benutzer mit gefälschten Programmen (tests/test_karte.py)."""
from __future__ import annotations

import subprocess
import sys
import threading

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import browser, _preconfirm, _start, _watch  # noqa: E402,F401  (browser = Fixture)
from tests.test_karte import KARTE, WH, pi  # noqa: E402,F401  (pi = Fixture)


@pytest.fixture
def server(pi):  # noqa: F811
    for k in ("jobs", "neu", "out"):
        (pi["tmp"] / k).mkdir()
    dirs = {"FESTPOS_WH_DIR": str(pi["tmp"] / "jobs"), "FESTPOS_WH_NEU": str(pi["tmp"] / "neu"),
            "FESTPOS_WH_ERGEBNIS": str(pi["tmp"] / "out"), "FESTPOS_KARTE_DIR": str(pi["kdir"])}
    env = {**pi["env"], **dirs, "FESTPOS_WH_TROCKEN": "1", "FESTPOS_KARTE_BIN": str(KARTE), "FESTPOS_KARTE_PYTHON": "1"}
    stop = threading.Event()

    def helper():                         # wie festpos-wiederherstellen.path: Aufträge abarbeiten, sobald sie da sind
        while not stop.wait(0.2):
            if any((pi["tmp"] / "jobs").iterdir()):
                subprocess.run([sys.executable, "-I", str(WH), "--anfrage"], env=env, capture_output=True, timeout=60)
    th_ = threading.Thread(target=helper, daemon=True)
    th_.start()
    (pi["tmp"] / "srv").mkdir()
    proc, base, c = _start(pi["tmp"] / "srv", dirs)
    yield base, env
    stop.set()
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width=1300, height=900):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    errs = _watch(page)
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/admin.html#devices/pi")
    page.fill("#user", "admin")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#c-open")
    _preconfirm(page)                                    # kritische Aktionen: schon bestätigt (#9)
    page.wait_for_selector("#c-open")
    return ctx, page, errs


def test_karte_schreiben(server, browser, pi):  # noqa: F811
    base, env = server
    ctx, page, errs = _admin(browser, base)
    page.click("#c-open")
    page.wait_for_selector("#c-devs")
    text = page.inner_text("#c-devs")
    assert "Sicherungs- oder Update-Stick" in text and "keine Karte im Leser" in text and "Systemplatte" in text
    assert page.locator("#c-devs input:disabled").count() == 5 and page.is_disabled("#c-next")
    page.check('#c-devs label:not(.off) input')
    page.click("#c-next")
    page.wait_for_selector("#c-letters")
    page.check('#c-letters input[value="b"]')
    assert "Nicht während des Betriebs" in page.inner_text("#c-busy")
    page.click("#c-next")
    page.wait_for_selector("#c-confirm")
    assert "32 GB" in page.inner_text("#cwiz") and page.is_disabled("#c-next")
    page.fill("#c-confirm", "c")
    assert page.is_disabled("#c-next")
    page.fill("#c-confirm", "B")
    assert not page.is_disabled("#c-next")
    page.click("#c-next")
    page.wait_for_selector(".pbar")                               # Fortschritt; der Dienst schreibt (hier direkt)
    assert page.locator("#c-next").count() == 0                   # kein Hauptknopf während des Schreibens
    r = subprocess.run([sys.executable, "-I", str(KARTE), "--schreiben"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    page.wait_for_selector("#c-done", timeout=15000)
    assert "Karte für Pi B ist bereit" in page.inner_text("#cwiz")
    assert page.inner_text("#c-next") == "Fertig"
    page.click("#c-next")
    page.wait_for_selector("#systemcard")
    # «Auf 3 Pi erweitern»: Karte B ist geschrieben, C noch nicht; Knopf öffnet den Ablauf mit C vorgewählt
    page.click("#x-open")
    page.wait_for_selector("#x-written")
    assert "geschrieben" in page.inner_text("#x-written li:first-child")
    assert "noch nicht geschrieben" in page.inner_text("#x-written li:last-child")
    page.click('[data-write="c"]')
    page.wait_for_selector("#c-devs")
    page.click("#c-cancel")
    page.wait_for_selector("#x-written")                           # zurück in «Auf 3 Pi erweitern»
    assert [e for e in errs if "Failed to load resource" not in e] == [], errs
    ctx.close()


def test_karte_360(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base, width=360, height=800)
    page.click("#c-open")
    page.wait_for_selector("#c-devs")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.check('#c-devs label:not(.off) input')
    page.click("#c-next")
    page.check('#c-letters input[value="a"]')
    assert "Ersatzkarte für diesen Pi" in page.inner_text("#c-letters")
    page.click("#c-next")
    page.wait_for_selector("#c-confirm")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()
