"""Übersicht: gelbe Karte «Sicherheit: n neue Ereignisse» bis «Gesehen» (T-0-N/FestPOS#11). Der root-Dienst schreibt die
Datei, die App trägt sie ein (alle 10 s), die Übersicht fragt alle 5 s – ein neues Ereignis erscheint ohne Neuladen."""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

NOW = int(time.time())


def _write(path, run, events):
    path.write_text(json.dumps({"run": run, "seq": max(e["seq"] for e in events), "events": events}))


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("ereignisse")
    _write(d / "ereignisse.json", "r1", [
        {"seq": 1, "at": NOW - 60, "kind": "ssh", "user": "pi", "ip": "192.168.55.201", "how": "publickey"},
        {"seq": 2, "at": NOW - 50, "kind": "sudo", "user": "pi", "as": "root", "cmd": "/usr/bin/systemctl restart festpos"},
        {"seq": 3, "at": NOW - 40, "kind": "update_foreign", "code": "unknown_key", "file": "festpos.zip"}])
    proc, base, c = _start(d, {"FESTPOS_EREIGNISSE_DIR": str(d)})
    yield base, d
    proc.terminate()
    proc.wait(10)


def _open(browser, base, width):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    return ctx, page, errs


@pytest.mark.parametrize("width", [412, 360])
def test_card_fits_on_phone(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _open(browser, base, width)
    page.wait_for_selector("#sec-card", timeout=20000)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert not errs, errs
    ctx.close()


def test_card_until_seen_and_new_event_appears(server, browser):  # noqa: F811
    base, d = server
    ctx, page, errs = _open(browser, base, 1300)
    page.wait_for_selector("#sec-card", timeout=20000)
    card = page.inner_text("#sec-card")
    assert "neue Ereignisse" in card and "Passwörter ändern" in card and "Notfall-Login" in card   # Anmeldung eben
    assert "Benutzer pi von 192.168.55.201" in card and "/usr/bin/systemctl restart festpos" in card
    assert page.locator("#sec-card .status.bad").inner_text() == "Fremd signiertes Update abgewiesen"
    assert page.locator("#sec-card td:nth-child(2)").first.inner_text() == "A"
    page.click("#sec-seen")
    _until(page, "!document.getElementById('sec-card')")
    # neues Ereignis (z.B. SSH-Anmeldung auf einem anderen Pi): erscheint von selbst wieder
    _write(d / "ereignisse.json", "r1", [{"seq": 4, "at": int(time.time()), "kind": "reboot"}])
    _until(page, "!!document.getElementById('sec-card')", 30000)
    assert "Sicherheit: 1 neues Ereignis" in page.inner_text("#sec-card") and "Neustart" in page.inner_text("#sec-card")
    # Protokoll: nur Sicherheit
    page.goto(base + "/admin.html#audit")
    page.reload()
    page.wait_for_selector("#auditsec")
    page.check("#auditsec")
    _until(page, "!document.querySelector('#auditlist').textContent.includes('Preisliste')")
    text = page.inner_text("#auditlist")
    assert "SSH-Anmeldung" in text and "Notfall-Login" in text and "Preis" not in text
    assert not errs, errs
    ctx.close()
