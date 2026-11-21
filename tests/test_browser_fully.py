"""Im echten Browser: die Bestell-App meldet den Akku (Chrome-Schnittstelle oder Fully), die Statusanzeige warnt
bei wenig Akku, und in der Verwaltung lässt sich Fully pro Handy einrichten. Wird übersprungen ohne Chromium."""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("fully"))
    yield base, c
    proc.terminate()
    proc.wait(10)


def _device(c, name, timeout=8.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        dev = {d["name"]: d for d in c.get("/api/status", auth=AUTH).json()["devices"]}
        if name in dev and "battery" in dev[name]:
            return dev[name]
        time.sleep(0.2)
    raise AssertionError(f"{name}: kein Akkustand gemeldet")


@pytest.mark.parametrize("stub,want", [
    ("Object.defineProperty(navigator, 'getBattery', {value: async () => "
     "({level: 0.42, charging: false, addEventListener() {}})})", (42, False)),                 # Chrome
    ("window.fully = {getBatteryLevel: () => 77.4, isPlugged: () => true};"
     "Object.defineProperty(navigator, 'getBattery', {value: async () => ({level: 0.1, charging: false, addEventListener() {}})})",
     (77, True)),                                                                               # Fully geht vor
    ("Object.defineProperty(navigator, 'getBattery', {value: undefined})", None),               # kann es nicht
])
def test_app_reports_battery(server, browser, stub, want):  # noqa: F811
    base, c = server
    name = f"Akku {want[0] if want else 'keiner'}"
    ctx = browser.new_context(viewport={"width": 412, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify({name!r})); {stub}")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    if want:
        d = _device(c, name)
        assert (d["battery"], d["charging"], d["battery_src"]) == (*want, "app")
    else:
        time.sleep(1.5)
        dev = {d["name"]: d for d in c.get("/api/status", auth=AUTH).json()["devices"]}
        assert name in dev and "battery" not in dev[name]
    assert not errs, errs
    ctx.close()


def test_status_display_warns_on_low_battery(server, browser):  # noqa: F811
    base, c = server
    c.get("/api/bootstrap", headers={"X-Festpos-Device": "Leer%20Handy", "X-Festpos-Battery": "8", "X-Festpos-Charging": "0"})
    c.get("/api/bootstrap", headers={"X-Festpos-Device": "Lade%20Handy", "X-Festpos-Battery": "5", "X-Festpos-Charging": "1"})
    ctx = browser.new_context(viewport={"width": 800, "height": 480})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/status.html")                                   # vom Pi selbst: ohne Anmeldung
    page.wait_for_selector("#devices .bat")
    page.wait_for_function("() => document.getElementById('detail').textContent.includes('Leer Handy')")
    assert "Akku 8 %" in page.inner_text("#detail") and "Lade Handy" not in page.inner_text("#detail")  # lädt: keine Warnung
    assert page.locator("#devices .bat.bad").count() == 1
    assert "⚡" in page.inner_text("#devices")
    names = page.evaluate("() => [...document.querySelectorAll('#devices .lamp')].map((l) => l.scrollWidth <= l.clientWidth + 1)")
    assert all(names)                                                  # Namen nicht abgeschnitten
    assert not errs, errs
    ctx.close()


def _admin(br, base, w=1300, h=900):
    ctx = br.new_context(viewport={"width": w, "height": h}, accept_downloads=True)
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    return ctx, page, errs


def _go(page, where):
    """Andere Seite der Verwaltung (nur der Teil nach # ändert sich: neu laden, die Anmeldung bleibt)."""
    page.evaluate(f"location.hash = '#{where}'")
    page.reload()
    page.wait_for_selector("#main h2")


def _wizard(page, kind, name):
    """Geräte › «Gerät hinzufügen»: Art wählen, Namen eintragen -> Schritt nach den Angaben."""
    page.click('.side button[data-page="devices"]')
    page.click('.tabs [data-tab="list"]')                            # T28: Handys › Geräte
    page.click("#dev-add")
    page.check(f'input[name="kind"][value="{kind}"]')
    page.click("#wiz-next")
    page.wait_for_selector("#wiz-data")
    page.fill('#wiz-data input[name="name"]', name)


def test_admin_sets_up_fully_per_phone(server, browser):  # noqa: F811
    """T5: Fully über Geräte › «Gerät hinzufügen» › «Fully auf vorhandenem Handy» (Einmal-Passwort, WLAN-QR), danach
    alles in der Geräteansicht; die Einstellungen für alle unter Handys › Einrichtung."""
    base, c = server
    ctx, page, errs = _admin(browser, base)
    # ohne Fully-Passwort geht es nicht weiter – mit Hinweis wohin
    _wizard(page, "fully", "XCover 7")
    assert page.locator("#wiz-next").is_disabled()
    assert "Fully-Passwort" in page.inner_text(".warnbox")
    page.click('.warnbox [data-goto="devices/setup"]')
    page.wait_for_selector("#fullyform")
    assert page.locator('.tabs [data-tab="setup"]').get_attribute("aria-selected") == "true"
    page.fill('#fullyform input[name="pw"]', "geheim-fully")
    page.click("#fullyform button.pri")
    _until(page, "!!document.querySelector('.toast:not(.bad)')")
    assert c.get("/api/admin/settings", auth=AUTH).json()["fully_password_set"]
    # FestPOS-WLAN für den WLAN-QR
    page.fill('#fwlan-form input[name="ssid"]', "FestPOS")
    page.fill('#fwlan-form input[name="wifi_password"]', "wlan-geheim-1")
    page.click("#fwlan-form button.pri")
    page.wait_for_selector("#fwlan .status.ok")
    # jetzt: Angaben -> WLAN-QR -> Einmal-Passwort und IP des Handys
    _wizard(page, "fully", "XCover 7")
    page.click("#wiz-next")
    page.wait_for_selector(".qr-big svg")
    assert "FestPOS" in page.inner_text("#main") and "wlan-geheim" not in page.content()
    page.click(".qr-full-btn")                                           # bildschirmfüllend
    page.wait_for_selector(".qrfull svg")
    page.click(".qrfull button")
    assert not page.locator(".qrfull").count()
    page.click("#wiz-next")
    page.wait_for_selector("#wiz-ip")
    temp = page.inner_text(".bigpw").strip()
    assert len(temp) == 8 and temp != "geheim-fully"
    page.fill('#wiz-ip input[name="ip"]', "127.0.0.7")
    page.click("#wiz-ip button.pri")
    _until(page, "!!document.querySelector('#froll-steps li.bad')")       # hier antwortet kein Fully
    assert "nicht erreichbar" in page.inner_text('#froll-steps li[data-step="boot"]')
    page.click("#wiz-next")                                              # Fertig -> Liste: das Gerät ist eingetragen
    page.wait_for_selector('#main tr[data-key]:has(.dev-link b:text-is("XCover 7"))')
    assert "127.0.0.7" in page.inner_text('#main tr[data-key]:has(.dev-link b:text-is("XCover 7"))')
    # Geräteansicht: Fully einrichten (Zertifikat für die Fernverwaltung), dann die Aktionen
    page.locator('#main tr[data-key]:has(.dev-link b:text-is("XCover 7")) [data-open]').first.click()
    page.wait_for_selector('[data-fa="cert"]')
    with page.expect_download() as dl:
        page.click('[data-fa="cert"]')
    assert dl.value.suggested_filename == "fully-remote-admin-ca.p12"
    page.wait_for_selector('[data-fa="ca"]')
    web = page.locator("a.btn[target=_blank]")                            # Webinterface von Fully (neuer Tab)
    assert web.get_attribute("href") == "https://127.0.0.7:2323/"
    _until(page, "document.querySelector('#main').innerText.includes('nicht erreichbar')")   # beim Öffnen gleich abgefragt
    page.once("dialog", lambda d: d.accept())
    page.click('[data-fa="ca"]')                                         # Testserver ohne ca.crt
    _until(page, "!!document.querySelector('.toast.bad')?.textContent.includes('CA-Datei')")
    page.evaluate("document.querySelectorAll('.toast').forEach((x) => x.remove())")
    page.once("dialog", lambda d: d.accept())
    page.click('[data-fa="devcert"]')
    _until(page, "!!document.querySelector('.toast.bad')?.textContent.includes('nicht bereit')")
    assert not [x for x in c.get("/api/admin/devices", auth=AUTH).json()["certs"] if x["name"] == "XCover 7"]
    stored = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert len([d for d in stored if d.get("fully_pin")]) == 1
    # Vorlage für alle Handys (Handys › Einrichtung): das Handy steht zur Auswahl
    _go(page, "devices/setup")
    page.wait_for_selector("#ftpl")
    page.click("#ftpl > summary")
    assert page.locator("#ftpl-src option").all_inner_texts() == ["XCover 7"]
    assert page.locator("#ftpl-all").is_disabled() and page.locator("#ftpl-down").is_disabled()
    # «Im Einsatz» in der Liste: gleich gespeichert, die Statusanzeige zeigt das Handy nicht mehr
    page.click('.side button[data-page="devices"]')
    page.click('.tabs [data-tab="list"]')                            # T28: Handys › Geräte
    page.wait_for_selector('#main tr[data-key]:has(.dev-link b:text-is("XCover 7")) [data-active]')
    page.locator('#main tr[data-key]:has(.dev-link b:text-is("XCover 7")) .sw').click()
    page.wait_for_function("() => document.querySelector('.toast')?.textContent.includes('nicht im Einsatz')")
    stored = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert [d.get("active") for d in stored if d["name"] == "XCover 7"] == [False]
    assert "XCover 7" not in [d["name"] for d in c.get("/api/status", auth=AUTH).json()["devices"]]
    # 422: noch kein Passwort, 503: keine ca.crt, 409: Fully nicht bereit – alles gewollt
    assert not [e for e in errs if not any(f"status of {s}" in e for s in (422, 503, 409))], errs
    ctx.close()


def test_factory_reset_uses_the_settings_for_all(server, browser):  # noqa: F811
    """Werksreset: ohne APK klarer Hinweis (Admin: Knopf zu Handys › Einrichtung); aus dem Internet: grosser QR für
    dieses Handy, wartet aufs Scannen; ohne QR (Handy schon im WLAN): nicht erreichbar -> Schritt rot mit Grund."""
    base, c = server
    assert c.put("/api/admin/settings", auth=AUTH, json={"fully_password": "geheim-fully"}).status_code == 200
    ctx, page, errs = _admin(browser, base)
    _go(page, "devices/setup")
    page.wait_for_selector("#fprov")
    assert "keine Fully-APK" in page.inner_text("#fprov summary")
    if not page.locator("#fwlan .status.ok").count():                  # WLAN (evtl. schon vom Test oben)
        page.fill('#fwlan-form input[name="ssid"]', "FestPOS")
        page.click("#fwlan-form button.pri")
        page.wait_for_selector("#fwlan .status.ok")
    _wizard(page, "reset", "XCover 8")
    page.fill('#wiz-data input[name="ip"]', "127.0.0.8")
    assert page.locator("#wiz-next").is_disabled() and "Einrichtungs-QR" in page.inner_text(".warnbox")
    _go(page, "devices/setup")
    page.wait_for_selector("#fprov-form")
    page.select_option('#fprov-form select[name="apk_local"]', "0")      # APK aus dem Internet
    page.click("#fprov-form button.pri")
    page.wait_for_selector("#fprov .status.ok")
    assert "aus dem Internet" in page.inner_text("#fprov summary")
    _wizard(page, "reset", "XCover 8")
    page.fill('#wiz-data input[name="ip"]', "127.0.0.8")
    assert "Einrichtungs-QR bereit" in page.inner_text(".readyline")
    page.click("#wiz-next")
    page.wait_for_selector("#froll-qr .qr-big svg")
    assert page.locator(".qr-big").bounding_box()["width"] >= 500       # gross genug für die Kamera
    _until(page, "document.querySelector('#froll-steps li[data-step=\"scan\"]')?.className === 'run'")
    assert "30 Minuten" in page.inner_text('#froll-steps li[data-step="scan"]')
    page.click("#wiz-noqr")
    _until(page, "!!document.querySelector('#froll-steps li.bad')")
    assert page.locator('#froll-steps li[data-step="boot"]').get_attribute("class") == "bad"
    assert "nicht erreichbar" in page.inner_text('#froll-steps li[data-step="boot"]')
    assert not page.locator("#froll-steps li[data-step='scan']").count() and page.locator("#froll-qr").is_hidden()
    assert not page.locator("#wiz-noqr").is_disabled()
    log = [e["action"] for e in c.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert "device_rollout" in log
    assert not [e for e in errs if not any(f"status of {s}" in e for s in (422, 503, 409))], errs
    ctx.close()


def test_volume_keys_change_brightness(server, browser):  # noqa: F811
    """In Fully: Lautstärketasten machen den Bildschirm heller/dunkler (in Schritten, nie ganz schwarz)."""
    base, _ = server
    stub = ("window.__br = 128; window.__binds = {};"
            "window.fully = {getBatteryLevel: () => 80, isPlugged: () => false,"
            " bind: (ev, code) => { window.__binds[ev] = code; },"
            " getScreenBrightness: () => window.__br, setScreenBrightness: (v) => { window.__br = v; }};")
    ctx = browser.new_context(viewport={"width": 412, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify('Hell')); {stub}")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    page.wait_for_function("() => window.__binds.volumeUp && window.__binds.volumeDown")
    page.evaluate("eval(window.__binds.volumeUp)")                 # so ruft Fully den Code auf
    assert page.evaluate("window.__br") == 154
    assert "Helligkeit 60 %" in page.inner_text(".toast")
    for _ in range(12):
        page.evaluate("eval(window.__binds.volumeDown)")
    assert page.evaluate("window.__br") == 13                        # 5 %: nie ganz schwarz
    for _ in range(12):
        page.evaluate("eval(window.__binds.volumeUp)")
    assert page.evaluate("window.__br") == 255
    page.click('[data-act="settings"]')
    page.click('[data-act="settings-more"]')                         # Hinweis bei «Bildschirm»
    assert "Lautstärketasten" in page.inner_text(".sheet")
    assert not errs, errs
    ctx.close()
    # ohne Fully: kein Hinweis, keine Fehler
    ctx = browser.new_context(viewport={"width": 412, "height": 900})
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Ohne'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    page.click('[data-act="settings"]')
    page.click('[data-act="settings-more"]')
    assert "Lautstärketasten" not in page.inner_text(".sheet")
    assert not errs, errs
    ctx.close()


_FULLY_STUB = ("window.__calls = []; window.__br = 128;"
               "window.fully = new Proxy({getBatteryLevel: () => 80, isPlugged: () => false, bind: () => {},"
               " getScreenBrightness: () => window.__br, setScreenBrightness: (v) => { window.__br = v; }},"
               " {get: (o, k) => k in o ? o[k] : (typeof k === 'string' && /^[a-z]/.test(k) && !['then', 'toJSON'].includes(k)"
               "   ? (...a) => window.__calls.push([k, ...a]) : undefined)});")


@pytest.fixture(scope="module")
def phone_server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("fullyphone"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


@pytest.mark.parametrize("role", ["service", "lead"])
def test_fully_buttons_in_app_settings(phone_server, browser, role):  # noqa: F811
    """Rückmeldung Punkt 7: in Fully unter «Erweiterte Einstellungen» Helligkeit, Bildschirm aus, Cache, Fully neu
    starten; Kiosk-Modus aus/ein und Android-Einstellungen nur mit der Rolle «Festleitung». Ohne Fully: nichts davon."""
    base, c = phone_server
    phone = {"X-Test-Cert": "XCover 5", "X-Test-Client": "192.168.55.105"}
    assert c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 5", "role": role}).status_code == 200
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, extra_http_headers=phone)
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify('XCover 5')); {_FULLY_STUB}")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    _until(page, "S.boot?.can_kiosk === " + ("true" if role == "lead" else "false"))
    page.click('[data-act="settings"]')
    page.click('[data-act="settings-more"]')
    page.wait_for_selector(".fully-box")
    page.click('[data-act="fully-bright"][data-d="1"]')
    assert page.evaluate("window.__br") == 154
    has_kiosk = page.locator('[data-act="fully-kiosk-off"]').count() == 1
    assert has_kiosk == (role == "lead") and (page.locator('[data-act="fully-android"]').count() == 1) == (role == "lead")
    if role == "lead":
        page.once("dialog", lambda d: d.accept())
        page.click('[data-act="fully-kiosk-off"]')
        page.click('[data-act="fully-kiosk-on"]')
        page.once("dialog", lambda d: d.accept())
        page.click('[data-act="fully-android"]')
        assert page.evaluate("window.__calls.map((c) => c.join(':'))")[-4:] == [
            "unlockKiosk", "lockKiosk", "unlockKiosk", "startApplication:com.android.settings"]
    page.click('[data-act="fully-screen-off"]')
    assert page.evaluate("window.__calls.at(-1)[0]") == "turnScreenOff"
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()


def test_reboot_handheld_from_app(phone_server, browser):  # noqa: F811
    """«Handheld neu starten» in den erweiterten Einstellungen (alle Rollen, nur wenn Fully die Funktion
    kennt). Rückfrage; ist noch etwas offen (Ausgang, Zahlungen, abgelehnt, Warenkorb): Warnung mit Liste und ein
    zweites Mal bestätigen. Tut Fully nichts (keine Rechte), sagt es die App. «Ausschalten» gibt es nicht
    (fully.shutdown() geht nur mit Root) – auch wenn Fully die Funktion anbietet."""
    base, c = phone_server
    phone = {"X-Test-Cert": "XCover 6", "X-Test-Client": "192.168.55.106"}
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, extra_http_headers=phone)
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify('XCover 6')); {_FULLY_STUB}")
    page = ctx.new_page()
    errs = _watch(page)
    asked: list[str] = []

    def answer(*replies):
        def on(d):
            asked.append(d.message)
            d.accept() if replies[min(len(asked), len(replies)) - 1] else d.dismiss()
        asked.clear()
        page.on("dialog", on)
        return lambda: page.remove_listener("dialog", on)

    def more():
        page.wait_for_load_state("networkidle")                  # App fertig geladen: sonst zeichnet sie das Fenster neu
        page.click('[data-act="settings"]')
        page.click('[data-act="settings-more"]')
        page.wait_for_selector('.fully-box [data-act="fully-reboot"]')

    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    more()
    assert page.locator('[data-act="fully-shutdown"]').count() == 0 and "ausschalten" not in page.inner_text(".fully-box")
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    # nichts offen: eine Rückfrage – abbrechen tut nichts, bestätigen startet neu
    off = answer(False)
    page.click('[data-act="fully-reboot"]')
    off()
    assert asked == ["Handheld neu starten?"] and not page.evaluate("window.__calls.some((c) => c[0] === 'reboot')")
    off = answer(True)
    page.click('[data-act="fully-reboot"]')
    off()
    assert page.evaluate("window.__calls.at(-1)") == ["reboot"] and "startet neu" in page.inner_text(".toast")
    assert not page.locator(".sheet").count()
    # offen: Warenkorb und eine abgelehnte Bestellung -> Warnung mit Liste, dann nochmals fragen
    page.evaluate("""() => {
      localStorage.setItem('festpos.cart', JSON.stringify({a: {pid: 999, q: 3, opts: [], note: ''}}));
      localStorage.setItem('festpos.rejected', JSON.stringify([{id: 'x1', table: '7', lines: [], error: 'x', ts: 1}]));
    }""")
    page.reload()
    page.wait_for_selector('[data-act="digit"]')
    page.evaluate("POWER_WAIT = 300")
    page.evaluate("S.test = true; S.cart = {}")                    # Testmodus: der echte Warenkorb zählt trotzdem
    more()
    off = answer(True, False)                                       # Warnung ja, zweite Frage nein
    page.click('[data-act="fully-reboot"]')
    off()
    assert len(asked) == 2 and "Warenkorb mit 3 Stück" in asked[0] and "1 abgelehnte Bestellung" in asked[0]
    assert "erst nach dem Neustart" in asked[0] and asked[1].startswith("Wirklich neu starten")
    assert not page.evaluate("window.__calls.some((c) => c[0] === 'reboot')")       # nach dem Neuladen: nicht neu gestartet
    off = answer(False)                                             # schon die Warnung abgebrochen: keine zweite Frage
    page.click('[data-act="fully-reboot"]')
    off()
    assert len(asked) == 1
    off = answer(True, True)
    page.click('[data-act="fully-reboot"]')
    off()
    assert len(asked) == 2 and page.evaluate("window.__calls.at(-1)") == ["reboot"]
    # das Handheld läuft weiter (Fully ohne Geräteinhaber-Rechte): die App sagt es
    _until(page, "!!document.querySelector('.toast.bad')?.textContent.includes('Geräteinhaber')")
    assert not errs, errs
    ctx.close()
    # Fully ohne diese Funktionen: keine Knöpfe
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, extra_http_headers=phone)
    ctx.add_init_script("window.fully = {getBatteryLevel: () => 80, isPlugged: () => false, restartApp: () => {}};")
    page = ctx.new_page()
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    page.click('[data-act="settings"]')
    page.click('[data-act="settings-more"]')
    page.wait_for_selector(".fully-box")
    assert not page.locator('[data-act="fully-reboot"]').count() and "Handheld" not in page.inner_text(".fully-box")
    ctx.close()


def test_admin_warns_about_js_interface_without_whitelist(server, browser):  # noqa: F811
    """Geräte-Liste und Geräteansicht warnen, wenn Fully die JavaScript-Schnittstelle ohne passende
    Whitelist offen hat; «Whitelist setzen» fragt nach und zeigt danach den neuen Stand. (Fully selbst: test_fully.py)"""
    base, c = server
    assert c.put("/api/admin/settings", auth=AUTH, json={"fully_password": "geheim-fully"}).status_code == 200
    stored = json.loads(c.get("/api/admin/settings", auth=AUTH).json().get("monitor_devices") or "[]")
    stored = [d for d in stored if d.get("name") != "XCover W"] + [{"name": "XCover W", "kind": "phone", "ip": "127.0.0.9", "port": None}]
    assert c.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": stored}).status_code == 200
    assert c.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.9"}).status_code == 200
    web = {"state": "wide", "foreign": ["*"], "seconds": 5}
    data = {"battery": 70, "kiosk": True, "kiosk_locked": True, "licensed": True, "fully_version": "1.61.2"}
    res = lambda: {"name": "XCover W", "state": "ok", "seconds": 1, "data": data, "data_seconds": 1, "web": web}  # noqa: E731
    sent = []

    def overview(route):
        body = json.loads(route.fetch().text())
        body["results"]["127.0.0.9"] = res()
        route.fulfill(json=body)

    def whitelist(route):
        sent.append(route.request.post_data_json)
        web.clear()
        web.update(state="ok", seconds=0)
        route.fulfill(json={"state": "ok", "web": {"state": "ok"}, "lines": ["https://192.168.55.10", "https://192.168.55.10/*"]})
    ctx, page, errs = _admin(browser, base)
    page.route("**/api/admin/fully", overview)
    page.route("**/api/admin/fully/check", lambda r: r.fulfill(json=res()))
    page.route("**/api/admin/fully/whitelist", whitelist)
    page.click('.side button[data-page="devices"]')
    page.click('.tabs [data-tab="list"]')
    row = '#main tr[data-key]:has(.dev-link b:text-is("XCover W"))'
    page.wait_for_selector(f"{row} .web-warn")
    assert "JavaScript ohne Whitelist" in page.inner_text(row)
    page.locator(f"{row} [data-open]").first.click()
    page.wait_for_selector("#fa-web")
    assert "erlaubt mehr: *" in page.inner_text("#fullycard") and "fremde Seiten" in page.inner_text("#fa-web")
    page.once("dialog", lambda d: d.dismiss())                         # abgebrochen: nichts gesendet
    page.click('#fa-web [data-fa="whitelist"]')
    assert sent == []
    page.once("dialog", lambda d: d.accept())
    page.click('#fa-web [data-fa="whitelist"]')
    _until(page, "!!document.querySelector('.toast:not(.bad)')?.textContent.includes('nur noch FestPOS')")
    assert sent == [{"ip": "127.0.0.9"}]
    page.wait_for_selector("#fullycard")
    _until(page, "!document.querySelector('#fa-web')")
    assert "nur FestPOS (Whitelist)" in page.inner_text("#fullycard")
    page.set_viewport_size({"width": 360, "height": 800})              # Verwaltung am Handy: nichts ragt hinaus
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert not errs, errs
    ctx.close()


def test_admin_shows_certificate_message_instead_of_blank_page(server, browser):  # noqa: F811
    """CodeRabbit zu PR 80: noch angemeldet, inzwischen «Verwaltung nur mit Gerätezertifikat» und dieser Browser zeigt
    keines -> /api/admin/me gibt 403. Statt einer leeren Seite: Anmeldung mit der Meldung (wie beim Anmelden)."""
    base, _ = server
    ctx, page, errs = _admin(browser, base)
    msg = "Die Verwaltung geht nur mit gültigem Gerätezertifikat."
    page.route("**/api/admin/me", lambda r: r.fulfill(status=403, json={"detail": msg}))
    page.reload()
    page.wait_for_selector("#login .err")
    assert msg in page.inner_text("#login .err") and page.locator("#pw").count() == 1
    assert not [e for e in errs if "status of 403" not in e], errs
    ctx.close()
