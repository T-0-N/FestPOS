"""Verwaltung › Geräte & Netz › Pi › «Netz umziehen» (T-0-N/FestPOS#2, Teil 2): Plan mit Prüfung und Ablauf mit Frist."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("umzug")
    proc, base, c = _start(d, {"FESTPOS_NET": "192.168.55.0/24", "FESTPOS_VIP": "192.168.55.10",
                               "NODE_IP": "192.168.55.11"})
    yield base, d
    proc.terminate()
    proc.wait(10)


def _admin(browser, base, width):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#devices/pi")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector("#umzugcard")
    return ctx, page, errs


def _set(d, key, value):
    con = sqlite3.connect(d / "b.db")
    con.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value) if not isinstance(value, str) else value))
    con.commit()
    con.close()


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_plan_with_checks(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _admin(browser, base, width)
    assert "Netz jetzt: 192.168.55.0/24" in page.inner_text("#umzugcard")
    page.click("[data-um=plan]")
    page.wait_for_selector("#um-form")
    assert page.input_value("#um-form [name=net]") == "10.20.0.0/22"
    page.click("[data-um=check]")
    page.wait_for_selector(".um-checks")
    text = page.inner_text("#umzugcard")
    assert "Schritt 1 von 5 · Planen" in text and "10.20.0.0 – 10.20.3.255 · Platz für 1022 Geräte" in text
    assert "Keine frische Sicherung" in text                                  # Testserver: keine Sicherung
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.fill("#um-form [name=net]", "192.168.55.0/25")
    page.click("[data-um=check]")
    page.wait_for_selector("text=überschneidet das alte")
    page.click("[data-um=cancel]")
    page.wait_for_selector("[data-um=plan]")
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [1300, 412, 360])
def test_running_move_shows_deadline(server, browser, width):  # noqa: F811
    base, d = server
    now = int(time.time())
    _set(d, "net_move", {"id": "m1", "phase": "switching", "at": now - 60, "deadline": now + 500, "until": now + 90000,
                         "old": {"net": "192.168.55.0/24", "vip": "192.168.55.10", "a": "192.168.55.11"},
                         "new": {"net": "10.20.0.0/22", "vip": "10.20.0.10", "a": "10.20.0.11"},
                         "backup_at": now - 900, "phones": {"Kasse 1": {"ca": "ok"}, "XCover 4": {"ca": "unreachable"}}})
    _set(d, "net_move.node.a", {"id": "m1", "net": "10.20.0.0/22", "at": now})
    ctx, page, errs = _admin(browser, base, width)
    page.wait_for_selector(".um-urgent")
    text = page.inner_text("#umzugcard")
    assert "Netz umziehen: 192.168.55.0/24 → 10.20.0.0/22" in text and "https://10.20.0.10:8443" in text
    assert "XCover 4" in text and "1 von 2" in text
    left = page.inner_text("[data-um-left=deadline]")
    assert ":" in left and int(left.split(":")[0]) >= 7
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.locator("[data-um=back]").is_visible()
    assert not errs, errs
    ctx.close()
    _set(d, "net_move", "{}")
