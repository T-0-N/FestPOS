"""T27: Vor dem Herunterfahren und Neustart zeigen Bestell-App, Statusanzeige und Verwaltung dieselbe
Meldung mit Countdown. Die App bleibt bedienbar; die Festleitung kann in der Verwaltung abbrechen.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import AUTH  # noqa: E402
from tests.test_browser_p4 import _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 1", "X-Test-Client": "192.168.55.101"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("power"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def test_notice_on_phone_status_and_admin_with_cancel(server, browser):  # noqa: F811
    base, c = server
    r = c.post("/api/admin/system/power", auth=AUTH, json={"action": "reboot", "node": "all", "phones": "none"})
    assert r.status_code == 200, r.text
    # Bestell-App (360 px): roter Balken oben, Countdown, App weiter bedienbar
    ctx = browser.new_context(viewport={"width": 360, "height": 800}, has_touch=True, extra_http_headers=PHONE)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 1'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "!!document.getElementById('pwnotice')", 12000)
    text = page.inner_text("#pwnotice")
    assert text.startswith("System startet in 0:") and "wieder bereit" in text, text
    assert page.locator("#pwnotice .pwcancel").count() == 0                     # abbrechen nur in der Verwaltung
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    top = page.evaluate("document.querySelector('.top').getBoundingClientRect().top")
    assert top >= page.evaluate("document.getElementById('pwnotice').getBoundingClientRect().bottom") - 1
    page.click('[data-act="digit"][data-d="1"]')                                # weiter bedienbar
    assert not errs, errs
    # Statusanzeige: derselbe Text, über allem
    st = browser.new_page()                                                     # Pi-Bildschirm (ohne Handy-Zertifikat)
    serr = _watch(st)
    st.goto(base + "/status.html?ansicht=pc")
    _until(st, "!!document.getElementById('pwnotice')", 8000)
    assert st.inner_text("#pwnotice").startswith("System startet in 0:")
    assert st.evaluate("getComputedStyle(document.getElementById('pwnotice')).position") == "fixed"
    assert not serr, serr
    # Verwaltung: Balken mit «Abbrechen» (Admin = ab Festleitung)
    adm = browser.new_context(viewport={"width": 1300, "height": 900}).new_page()
    aerr = _watch(adm)
    adm.goto(base + "/admin.html")
    adm.fill("#user", "admin")
    adm.fill("#pw", "geheim")
    adm.press("#pw", "Enter")
    adm.wait_for_selector("#pwnotice .pwcancel", timeout=8000)
    adm.click("#pwnotice .pwcancel")
    _until(adm, "!document.getElementById('pwnotice')", 8000)
    assert c.get("/api/phone/status").json()["notice"] is None
    page.evaluate("pollStatus()")                                               # Handy: Balken verschwindet
    _until(page, "!document.getElementById('pwnotice')", 8000)
    assert not [e for e in aerr if "Failed to load resource" not in e], aerr
    ctx.close()
    st.close()
    adm.context.close()


def test_one_pi_of_several_only_on_status_and_admin(server, browser):  # noqa: F811
    """T32: startet nur ein Pi von mehreren neu, zeigen Statusanzeige und Verwaltung «Pi B startet … – die
    anderen Pi laufen weiter»; die Handhelds zeigen nichts (Bestellen geht weiter)."""
    import json
    base, _ = server
    one = {"action": "reboot", "left": 25, "token": "t-b", "cancel": True, "pis": ["b"], "all": False, "phones": False}

    def fake(route):
        r = route.fetch()
        j = r.json()
        j["notice"] = one
        route.fulfill(response=r, body=json.dumps(j))
    st = browser.new_page()
    errs = _watch(st)
    st.route("**/api/status", fake)
    st.goto(base + "/status.html?ansicht=pc")
    _until(st, "!!document.getElementById('pwnotice')", 8000)
    text = st.inner_text("#pwnotice")
    assert text.startswith("Pi B startet in 0:") and "die anderen Pi laufen weiter" in text, text
    assert not errs, errs
    st.close()


def test_phone_notice_only_for_the_whole_system(client, monkeypatch):
    """T32: Server – ein Pi von dreien: /api/status mit «pis», die Bestell-App ohne Meldung; alle Pi: beide."""
    from app import system
    monkeypatch.setattr(system, "_display_nodes", lambda: ["a", "b", "c"])
    monkeypatch.setattr(system, "_display_update", lambda *a, **k: None)
    monkeypatch.setattr(system, "_display_sync", lambda: None)
    try:
        r = client.post("/api/admin/system/power", auth=AUTH, json={"action": "reboot", "node": "b", "phones": "none"})
        assert r.status_code == 200, r.text
        n = system.power_notice()
        assert n["pis"] == ["b"] and n["all"] is False and system.phone_notice() is None
        assert client.get("/api/phone/status").json()["notice"] is None
        r = client.post("/api/admin/system/power", auth=AUTH, json={"action": "poweroff", "node": "all", "phones": "none"})
        assert r.status_code == 200
        assert system.power_notice()["all"] is True and client.get("/api/phone/status").json()["notice"]["action"] == "poweroff"
    finally:
        client.post("/api/admin/system/power/cancel", auth=AUTH)
