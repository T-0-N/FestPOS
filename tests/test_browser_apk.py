"""T6 im Browser: Karte «Fully-APK» (Version, Stand pro Pi, Verteilen), Einrichtungs-QR nur mit den Feldern der
gewählten Art, Hinweis «andere Version» in Geräte (Erneut verteilen nur Admin) und in der Übersicht – auch 360 px breit.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402
from tests.test_fully_apk import apk_bytes, axml  # noqa: E402

PHONES = [("Service 1", "127.0.0.21", "1.61.2-emm", 1612, "owner"), ("Service 2", "127.0.0.22", "1.60.0-emm", 1600, "owner"),
          ("Kasse", "127.0.0.23", "1.60.0-emm", 1600, "owner"), ("Service 3", "127.0.0.24", "1.60.1-play", 1601, "play")]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Vier Handys mit Fully, APK 1.61.2 auf dem Pi; Versionen wie von Fully gemeldet (zwei älter, eines Play Store)."""
    tmp = tmp_path_factory.mktemp("apk")
    proc, base, c = _start(tmp, {"FESTPOS_FULLY_DIR": str(tmp / "fully")})
    assert c.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "Lea-passwort"}).status_code == 200
    c.put("/api/admin/settings", auth=AUTH, json={"fully_password": "Fully-Test-123", "monitor_devices": [
        {"name": n, "kind": "phone", "ip": ip, "port": None} for n, ip, *_ in PHONES]})
    for _, ip, *_ in PHONES:
        assert c.post("/api/admin/fully/cert", auth=AUTH, json={"ip": ip}).status_code == 200
    assert c.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("Fully-Kiosk-Browser-v1.61.2-emm.apk", apk_bytes(axml()))}).status_code == 200
    db = sqlite3.connect(tmp / "b.db")
    ids = {d["name"]: d["id"] for d in json.loads(db.execute("SELECT value FROM settings WHERE key='monitor_devices'").fetchone()[0])}
    vers = {ids[n]: {"name": v, "code": code, "variant": var, "at": int(time.time())} for n, _, v, code, var in PHONES}
    db.execute("INSERT INTO settings(key,value) VALUES ('fully_versions', ?)", (json.dumps(vers),))
    db.commit()
    db.close()
    yield base, c
    proc.terminate()
    proc.wait(10)


def _login(br, base, where, name="admin", pw="geheim", w=1300, h=900):
    ctx = br.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(f"{base}/admin.html#{where}")
    if name != "admin":
        page.fill("#user", name)
    page.fill("#pw", pw)
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    return ctx, page, errs


def _wide(page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")


@pytest.mark.parametrize("width", [1300, 360])
def test_apk_card_and_only_the_fields_of_the_chosen_source(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _login(browser, base, "devices/setup", w=width)
    page.wait_for_selector("#fapk")
    card = page.inner_text("#fapk")
    assert "1.61.2-emm" in card and "Code 1612" in card and "2 Handhelds abweichend" in card
    assert page.locator("#fapk .apk-pis .status.ok").count() == 1 and page.locator("#fapk-push").count() == 1
    f = "#fprov-form"
    assert page.locator(f'{f} input[name="apk_url"]').is_hidden() and page.locator(f'{f} input[name="checksum"]').is_hidden()
    assert "rechnet der Pi" in page.inner_text("#fprov")
    page.select_option(f'{f} select[name="apk_local"]', "0")
    assert page.locator(f'{f} input[name="apk_url"]').is_visible() and page.locator(f'{f} input[name="checksum"]').is_visible()
    assert page.evaluate(f"document.querySelector('{f}').apk_url.required") is True
    assert page.locator("#fprov .hint.prov-net").is_visible() and page.locator("#fprov .hint.prov-pi").is_hidden()
    assert not _wide(page)                                              # auch die Adresse passt bei 360 px
    page.select_option(f'{f} select[name="apk_local"]', "1")
    assert page.locator(f'{f} input[name="apk_url"]').is_hidden()
    assert not _wide(page)
    assert not errs, errs
    ctx.close()


def test_upload_asks_to_distribute(server, browser, tmp_path):  # noqa: F811
    base, _ = server
    ctx, page, errs = _login(browser, base, "devices/setup")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))       # Nein: nichts verteilen
    apk = tmp_path / "Fully-Kiosk-Browser-v1.61.2-emm.apk"
    apk.write_bytes(apk_bytes(axml(), filler=60_000))
    page.set_input_files("#fapk-file", str(apk))
    _until(page, "document.querySelector('.toast')?.textContent.includes('gespeichert')")
    _until(page, "!!document.querySelector('#fapk')")
    assert asked and "1.61.2-emm" in asked[0] and "2 Handhelds" in asked[0]
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [1300, 360])
def test_devices_show_versions_and_outdated_phones(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _login(browser, base, "devices/list", w=width)
    page.wait_for_selector("#dev-apk")
    box = page.inner_text("#dev-apk")
    assert "Service 2" in box and "Kasse" in box and "Service 3" not in box and "Service 1" not in box   # Play Store, aktuell
    assert page.locator("#dev-apk-push").count() == 1
    assert page.locator("#main .devtbl .status", has_text="Fully veraltet").count() == 2
    assert "(Play Store)" in page.inner_text("#main .devtbl")
    assert not _wide(page)
    page.locator("#main tr[data-key]").filter(has_text="Service 3").locator(".dev-link").click()
    page.wait_for_selector("[data-close]")
    assert "Play Store" in page.inner_text("#main")
    assert not errs, errs
    ctx.close()


def test_lead_sees_the_warning_but_cannot_distribute(server, browser):  # noqa: F811
    base, _ = server
    ctx, page, errs = _login(browser, base, "devices/list", "Lea", "Lea-passwort")
    page.wait_for_selector("#dev-apk")
    assert page.locator("#dev-apk-push").count() == 0
    page.evaluate("location.hash = '#overview'")
    page.reload()
    page.wait_for_selector("#main .fully-old")
    assert "2 Geräten" in page.inner_text("#main .fully-old")
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [1300, 360])
def test_festpos_tap_per_handheld_in_device_view(server, browser, width):  # noqa: F811
    """«FestPOS Tap» direkt in der Geräteansicht installieren oder aktualisieren. Fully meldet die
    installierten Apps nicht – angezeigt wird, was der Pi zuletzt geschickt hat (hier nicht erreichbar: Testnetz)."""
    base, c = server
    r = c.post("/api/admin/payrexx/apk", auth=AUTH,
               files={"file": ("festpos-tap.apk", apk_bytes(axml("ch.festpos.tap", "1.4.0", 14)))})
    assert r.status_code == 200, r.text
    ctx, page, errs = _login(browser, base, "devices/list", w=width)
    page.wait_for_selector("#main tr[data-key]")
    page.locator("#main tr[data-key]").filter(has_text="Kasse").locator(".dev-link").click()
    page.wait_for_selector("#dev-tap")
    assert "noch nie geschickt" in page.inner_text("#dev-tap") and page.inner_text("#dev-tap-go") == "FestPOS Tap installieren"
    page.click("#dev-tap-go")
    _until(page, "!!document.querySelector('.toast')")                     # T30: im Hintergrund, Stand folgt
    assert "wird installiert" in page.inner_text(".toast")
    _until(page, "document.getElementById('dev-tap-st').textContent.includes('letzter Versuch')", 20000)  # 127.0.0.23 antwortet nicht
    assert "fehlgeschlagen" in page.inner_text("#dev-tap-st")
    card = page.inner_text("#fullycard")                                    # T30: Fully als Liste, Knöpfe in Gruppen
    assert "Apps" in card and "Prüfen" in card and "Zertifikate" in card and "Installationssperren" in card
    assert not _wide(page)
    assert not [e for e in errs if "Failed to load resource" not in e], errs
    ctx.close()
