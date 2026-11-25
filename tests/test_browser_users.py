"""Version 2026.10.10-13: Benutzer und Rollen in der Verwaltung, Herunterfahren/Neu starten, Geräte-Details
in der Statusanzeige. Im Browser; wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.test_browser_p4 import AUTH, browser, _confirm, _start, _watch  # noqa: E402,F401  (browser = Fixture)

NAV = '.side button[data-page]'


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("users"))
    for name, role in (("Vera", "view"), ("Lea", "lead")):
        r = c.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": f"{name}-passwort"})
        assert r.status_code == 200, r.text
    c.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [{"name": "XCover 2", "kind": "phone", "ip": "127.0.0.2", "port": None}]})
    httpx.Client(base_url=base, transport=httpx.HTTPTransport(local_address="127.0.0.2")).get(
        "/api/bootstrap", headers={"X-Festpos-Device": "Anna", "X-Festpos-Battery": "58", "X-Festpos-Charging": "1"})
    yield base, c
    proc.terminate()
    proc.wait(10)


def _login(browser, base, name, pw):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.fill("#user", name)
    page.fill("#pw", pw)
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    page.wait_for_selector("#main h2")
    return ctx, page, errs


def test_each_role_sees_only_its_pages(server, browser):  # noqa: F811
    base, _ = server
    want = {"Vera": ["overview", "orders", "jobs"],
            "Lea": ["overview", "orders", "jobs", "closing", "audit", "products", "tables", "pos", "devices"]}   # T34
    for name, pages in want.items():
        ctx, page, errs = _login(browser, base, name, f"{name}-passwort")
        assert [b.get_attribute("data-page") for b in page.locator(NAV).all()] == pages
        assert name in page.inner_text(".side .userbtn")
        groups = [g.text_content().strip() for g in page.locator(".side .grp").all()]
        assert groups == (["Betrieb"] if name == "Vera" else ["Betrieb", "Vorbereiten", "System"])
        assert page.locator("[data-power]").count() == 0          # Herunterfahren: nur unter Geräte & Netz › Pi
        if name == "Lea":
            page.click(f'{NAV}[data-page="pos"]')                    # T34: Kasse & App – nur Bestell-App mit dem Testmodus
            page.wait_for_selector('#main .card h3:has-text("Testmodus")')
            assert page.locator("#main .tabs").count() == 0 and page.evaluate("location.hash") == "#pos"
            assert page.locator("#main .card h3").all_inner_texts() == ["Testmodus (Üben)"]
            page.click(f'{NAV}[data-page="devices"]')                # Geräte & Netz: ohne Einstellungen (nur Admin)
            page.wait_for_selector('#main .sys-sub h4:has-text("Uhrzeit")')      # T33: Uhrzeit beim Pi
            assert [b.get_attribute("data-tab") for b in page.locator("#main .tabs [data-tab]").all()] == [
                "pi", "list", "printers", "net"]
            heads = page.locator("#main .card h3:visible").all_inner_texts()
            assert heads == ["Pi"] and page.locator("[data-power]").count() > 0   # ab Festleitung; Netz, Zertifikate nur Admin
            page.evaluate("location.hash = '#maint'")              # nicht erlaubte Seite (Lesezeichen): Übersicht
            page.reload()
            page.wait_for_selector('#main h2:has-text("Übersicht")')
        assert not errs, errs
        ctx.close()


def test_power_dialog_offers_phone_choice_only_with_fully(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, "Lea", "Lea-passwort")
    page.click(f'{NAV}[data-page="devices"]')                      # Geräte & Netz › Pi
    page.click('[data-power="poweroff"][data-node="all"]')
    page.wait_for_selector("dialog[open] #powerform")
    assert page.locator('dialog[open] input[name="phones"]').count() == 0      # Fully nicht eingerichtet
    page.keyboard.press("Escape")
    assert not errs, errs
    ctx.close()


def test_admin_manages_users(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, "admin", "geheim")
    page.click(f'{NAV}[data-page="users"]')
    page.click("#user-add")                                                # T28 Stufe 2: im Fenster
    page.wait_for_selector("dialog[open] #newuser")
    page.fill('#newuser input[name="name"]', "Max")
    page.select_option('#newuser select[name="role"]', "lead")
    page.fill('#newuser input[name="password"]', "max-passwort")
    page.click('dialog[open] [data-dlg="save"]')
    _confirm(page)                                                         # Benutzer anlegen: kritisch (#9)
    page.wait_for_selector('tr[data-uid]:has-text("Max")')
    users = {u["name"]: u for u in c.get("/api/admin/users", auth=AUTH).json()["users"]}
    assert users["Max"]["role"] == "lead" and users["Max"]["active"]
    page.locator('tr[data-uid]:has-text("Max") .u-active').uncheck()
    page.wait_for_selector('tr.inactive:has-text("Max")')
    assert not {u["name"]: u for u in c.get("/api/admin/users", auth=AUTH).json()["users"]}["Max"]["active"]
    r = httpx.post(base + "/api/admin/login", json={"name": "Max", "password": "max-passwort"})
    assert r.status_code == 401                                   # gesperrt: keine Anmeldung mehr
    page.locator('tr[data-uid]:has-text("Max") .u-edit').click()          # Rolle ändern im Fenster
    page.wait_for_selector("dialog[open] .u-form")
    page.select_option('dialog[open] select[name="role"]', "view")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector("dialog[open]", state="detached")
    assert {u["name"]: u for u in c.get("/api/admin/users", auth=AUTH).json()["users"]}["Max"]["role"] == "view"
    assert not errs, errs
    ctx.close()


def _status(browser, base, token=None):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    if token:
        ctx.add_init_script(f"sessionStorage.setItem('festpos.token', {token!r})")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/status.html")
    page.wait_for_selector("[data-pi]")
    return ctx, page, errs


def test_status_details_on_tap(server, browser):  # noqa: F811
    base, c = server
    # Statusanzeige am Pi (ohne Anmeldung): Details ja, Knöpfe nein
    ctx, page, errs = _status(browser, base)
    page.click("[data-pi] >> nth=0")
    page.wait_for_selector("#detail-sheet .panel")
    text = page.inner_text("#detail-sheet")
    assert "Betriebssystem" in text and "FestPOS-Version" in text
    assert page.locator('#detail-sheet [data-act="go"]').count() == 0
    page.click('#detail-sheet [data-act="close"]')
    assert page.locator("#detail-sheet").is_hidden()
    page.click("[data-dev] >> nth=0")
    page.wait_for_selector("#detail-sheet .panel")
    text = page.inner_text("#detail-sheet")
    assert "XCover 2" in text and "Anna" in text and "58 %, lädt" in text
    page.keyboard.press("Escape")
    assert not errs, errs
    ctx.close()
    # aus der Verwaltung als Festleitung: Neu starten mit Rückfrage
    token = httpx.post(base + "/api/admin/login", json={"name": "Lea", "password": "Lea-passwort"}).json()["token"]
    ctx, page, errs = _status(browser, base, token)
    page.wait_for_function("() => LAST && LAST.me && LAST.me.role === 'lead'")
    page.click("[data-pi] >> nth=0")
    page.wait_for_selector('#detail-sheet [data-act="go"]')
    page.click('#detail-sheet [data-act="go"] >> nth=0')
    assert "Wirklich" in page.inner_text("#detail-sheet .acts")
    page.click('#detail-sheet [data-act="no"]')
    assert page.locator('#detail-sheet [data-act="go"]').count() == 2
    assert not errs, errs
    ctx.close()


def test_phone_shutdown_with_fully(server, browser):  # noqa: F811
    """Mit eingerichtetem Fully: beim Herunterfahren zuerst «Handys ausschalten», im Dashboard pro Handy
    «Handy ausschalten» mit Rückfrage – der Befehl kommt bei Fully an."""
    from tests.test_fully import PASSWORD, FakeFully
    base, c = server
    # Die Test-Drucker (Port 1) gibt es nicht: nach der nächsten Druckerprüfung wären sie «gestört» und die
    # Gesamtlampe rot – je nach Laufzeit des Tests. Hier geht es nur um die Handys: Drucker ausschalten.
    for p in c.get("/api/admin/printers", auth=AUTH).json():
        body = {k: p[k] for k in ("name", "host", "port", "chars_per_line", "show_prices", "fail_mode", "fail_after") if k in p}
        assert c.put(f"/api/admin/printers/{p['id']}", auth=AUTH, json={**body, "enabled": False}).status_code == 200
    c.put("/api/admin/settings", auth=AUTH, json={"fully_password": PASSWORD, "monitor_devices": [
        {"name": "XCover 1", "kind": "phone", "ip": "127.0.0.1", "port": None},
        {"name": "XCover 2", "kind": "phone", "ip": "127.0.0.2", "port": None}]})
    p12 = c.post("/api/admin/fully/cert", auth=AUTH, json={"ip": "127.0.0.1"}).content
    srv = FakeFully("https", p12)
    try:
        c.put("/api/admin/settings", auth=AUTH, json={"fully_port": srv.port})
        assert c.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"}).json()["state"] == "ok"
        ctx, page, errs = _login(browser, base, "Lea", "Lea-passwort")
        page.click(f'{NAV}[data-page="devices"]')
        page.click('[data-power="poweroff"][data-node="all"]')
        page.wait_for_selector('dialog[open] input[name="phones"]')
        radios = page.locator('dialog[open] input[name="phones"]')
        assert [r.get_attribute("value") for r in radios.all()] == ["shutdown", "screen_off", "none"]
        assert radios.first.is_checked() and "Einschalttaste" in page.inner_text("dialog[open] fieldset")
        page.keyboard.press("Escape")
        assert not errs, errs
        ctx.close()
        token = httpx.post(base + "/api/admin/login", json={"name": "Lea", "password": "Lea-passwort"}).json()["token"]
        ctx, page, errs = _status(browser, base, token)
        page.wait_for_function("() => LAST?.devices?.some((d) => d.name === 'XCover 1' && d.fully) && LAST.me")
        page.click('[data-dev]:has-text("XCover 1")')
        page.wait_for_selector('#detail-sheet [data-act="go"]')
        go = page.locator('#detail-sheet [data-act="go"]')
        assert go.all_inner_texts() == ["Seite neu laden", "App neu starten", "Handheld neu starten", "Handheld ausschalten", "Kiosk-Modus aus"]
        # Kiosk-Modus aus und wieder ein: danach zeigt die Anzeige gleich den neuen Zustand und den anderen Knopf
        go.nth(4).click()
        page.click('#detail-sheet [data-act="yes"]')
        page.wait_for_selector('#detail-sheet button[data-act="go"]:has-text("Kiosk-Modus ein")', timeout=8000)
        assert "aus (entsperrt)" in page.inner_text("#detail-sheet dl") and srv.commands[-2:] == ["unlockKiosk", "deviceInfo"]
        # in der Liste orange markiert, oben «Bitte prüfen»
        item = page.locator('[data-dev]:has-text("XCover 1")')
        assert item.locator(".lamp.kiosk").count() == 1 and "Kiosk aus" in item.inner_text()
        assert "wait" in page.get_attribute("#pill", "class")
        assert page.evaluate("getComputedStyle(document.querySelector('[data-dev] .note.kiosk')).color") == "rgb(255, 138, 31)"
        assert "Erledigt" in page.inner_text("#detail-sheet .msg")              # Meldung bleibt beim Neuzeichnen
        page.click('#detail-sheet button[data-act="go"]:has-text("Kiosk-Modus ein")')
        page.click('#detail-sheet [data-act="yes"]')
        page.wait_for_selector('#detail-sheet button[data-act="go"]:has-text("Kiosk-Modus aus")', timeout=8000)
        page.wait_for_function("() => !document.querySelector('[data-dev] .lamp.kiosk')")   # wieder normal
        # Handy neu starten ohne Rechte (z.B. KNOX aus): klare Meldung statt «gesendet»
        go.nth(2).click()
        page.click('#detail-sheet [data-act="yes"]')
        page.wait_for_selector('#detail-sheet .msg:has-text("KNOX")')
        go.nth(3).click()
        assert "Wirklich: Handheld ausschalten?" in page.inner_text("#detail-sheet .acts")
        page.click('#detail-sheet [data-act="yes"]')
        page.wait_for_selector("#detail-sheet .msg")
        assert srv.commands[-1] == "shutdownDevice"
        assert not errs, errs
        ctx.close()
    finally:
        srv.stop()


def test_logo_in_admin_status_and_app(server, browser):  # noqa: F811
    """Logo in den Einstellungen hochladen: erscheint in der Verwaltung, in der Statusanzeige und in der Bestell-App."""
    import io

    from PIL import Image

    from tests.test_browser_p4 import _phone
    base, c = server
    im = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
    im.paste((180, 20, 40, 255), (100, 40, 700, 160))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    ctx, page, errs = _login(browser, base, "admin", "geheim")
    assert page.locator(".side .brand img").count() == 0 and page.locator(".side .brand i").count() == 1
    page.click(f'{NAV}[data-page="pos"]')                             # T34: Kasse & App › Veranstalter › Logo
    page.wait_for_selector("#logocard #logofile", state="attached")
    page.set_input_files("#logofile", files=[{"name": "logo.png", "mimeType": "image/png", "buffer": buf.getvalue()}])
    page.wait_for_selector("#logocard .logo-prev img.logo.plate")
    loaded = "(el) => el.complete && el.naturalWidth > 0"
    assert page.eval_on_selector(".side .brand img.logo", loaded)                    # Seitenleiste gleich mit
    page.click("#logoplate")                                                         # weissen Grund abschalten
    page.wait_for_selector("#logocard .logo-prev img.logo:not(.plate)")
    assert not errs, errs
    ctx.close()
    # Anmeldung zeigt es auch
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    page.goto(base + "/admin.html")
    page.wait_for_selector("#login img.login-logo")
    ctx.close()
    # Statusanzeige (am Pi, ohne Anmeldung)
    ctx, page, errs = _status(browser, base)
    page.wait_for_selector("header .brand img.logo:not(.plate)")
    assert page.eval_on_selector("header .brand img.logo", loaded)
    assert not errs, errs
    ctx.close()
    # Bestell-App: im Kopf der Tischwahl
    ctx, page, errs = _phone(browser, base, "Logo-Handy")
    page.wait_for_selector("header.top img.logo")
    assert page.eval_on_selector("header.top img.logo", loaded)
    assert not errs, errs
    ctx.close()
    c.delete("/api/admin/logo", auth=AUTH)
    c.put("/api/admin/settings", auth=AUTH, json={"logo_plate": "white"})


def test_admin_builds_a_role_from_rights(server, browser):  # noqa: F811
    """Rückmeldung Punkt 6: Benutzer › Rollen: «+ Rolle» mit Häkchen pro Recht; feste Rollen ohne «Bearbeiten»; ein
    Benutzer mit der eigenen Rolle sieht genau die passenden Seiten und Reiter."""
    base, c = server
    ctx, page, errs = _login(browser, base, "admin", "geheim")
    page.click(f'{NAV}[data-page="users"]')
    page.wait_for_selector("#roles-card")
    assert page.locator('#roles-card tr[data-rid="admin"] .r-edit').count() == 0
    assert "alles" in page.inner_text('#roles-card tr[data-rid="admin"]')
    page.click("#role-add")
    page.wait_for_selector("dialog[open] .perm-list")
    assert page.locator('dialog[open] input[name="perm"]').count() == 13
    page.fill('dialog[open] input[name="name"]', "Küche")
    for p in ("stock", "printers", "jobs"):
        page.check(f'dialog[open] input[name="perm"][value="{p}"]')
    page.click('dialog[open] [data-dlg="save"]')
    _confirm(page)                                                         # Rollen: kritisch (#9), danach 5 Minuten
    page.wait_for_selector('#roles-card tr[data-rid]:has-text("Küche")')
    rid = page.locator('#roles-card tr[data-rid]:has-text("Küche")').get_attribute("data-rid")
    assert "Drucker" in page.inner_text(f'#roles-card tr[data-rid="{rid}"]')
    page.click("#user-add")
    page.wait_for_selector("dialog[open] #newuser")
    page.fill('#newuser input[name="name"]', "Koch")
    page.select_option('#newuser select[name="role"]', rid)
    page.fill('#newuser input[name="password"]', "koch-passwort")
    page.click('dialog[open] [data-dlg="save"]')
    page.wait_for_selector('tr[data-uid]:has-text("Koch")')
    assert "Küche" in page.inner_text('tr[data-uid]:has-text("Koch")')
    assert not errs, errs
    ctx.close()
    ctx, page, errs = _login(browser, base, "Koch", "koch-passwort")
    assert [b.get_attribute("data-page") for b in page.locator(NAV).all()] == ["overview", "jobs", "products", "devices"]
    assert "Küche" in page.inner_text(".side .userbtn")
    page.click(f'{NAV}[data-page="devices"]')
    page.wait_for_selector("#main h2")
    page.wait_for_timeout(400)
    assert page.locator("#main .tabs").count() == 0 and page.evaluate("location.hash").startswith("#devices")   # nur Drucker
    assert "Testdruck" in page.inner_text("#main")
    assert not [e for e in errs if "status of 403" not in e], errs
    ctx.close()
