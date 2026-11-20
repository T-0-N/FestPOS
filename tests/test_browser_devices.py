"""T5: Seite «Geräte» im Browser – jedes Gerät genau einmal, Rolle und «Im Einsatz» direkt in der Liste, alles andere
in der Geräteansicht; «Gerät hinzufügen» durch die Festleitung (Nur Zugang); Löschen nur Admin; Seitenleiste am PC
(fixiert, Gruppen klappen bei wenig Höhe zu) und am Handy (Menü-Knopf), nichts zu breit bei 412 und 360 px.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_api import make_device_ca  # noqa: E402
from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("devices")
    crt, key, _ = make_device_ca(tmp)
    proc, base, c = _start(tmp, {"FESTPOS_DEVICE_CA": str(crt), "FESTPOS_DEVICE_CA_KEY": str(key)})
    assert c.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "Lea-passwort"}).status_code == 200
    for name in ("Service 1", "Service 1", "Kasse"):                  # «Service 1» erneuert: zwei Zertifikate
        assert c.post("/api/admin/devices/cert", auth=AUTH, json={"name": name, "renew": name == "Service 1"}).status_code == 200
    c.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": [
        {"name": "Switch", "kind": "net", "ip": "127.0.0.30", "port": None},
        {"name": "Service 1", "kind": "phone", "ip": "127.0.0.11", "port": None},
        {"name": "Kasse", "kind": "phone", "ip": "127.0.0.12", "port": None}]})
    yield base, c
    proc.terminate()
    proc.wait(10)


def _login(br, base, name="admin", pw="geheim", w=1300, h=900):
    ctx = br.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html#devices/list")
    if name != "admin":
        page.fill("#user", name)
    page.fill("#pw", pw)
    page.press("#pw", "Enter")
    page.wait_for_selector("#main h2")
    return ctx, page, errs


def _rows(page):
    """Namen der Geräte in der Liste (klein). Die Zeilen tragen die Geräte-ID (T1), nicht den Namen."""
    page.wait_for_selector("#main tr[data-key]")
    return [r.inner_text().lower() for r in page.locator("#main tr[data-key] .dev-link b").all()]


def _row(page, name):
    return page.locator("#main tr[data-key]").filter(has=page.locator(f'.dev-link b:text-is("{name}")'))


def test_each_device_once_role_and_in_use_in_the_list(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, "Lea", "Lea-passwort")
    assert _rows(page) == ["kasse", "service 1"]                       # erneuert: trotzdem eine Zeile
    row = _row(page, "Service 1")
    assert "127.0.0.11" in row.inner_text() and "Switch" not in page.inner_text("#main")
    row.locator("select").select_option("lead")                       # Rolle «Festleitung» darf die Festleitung vergeben
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('Service 1')")
    assert list(c.get("/api/admin/devices", auth=AUTH).json()["roles"].values()) == ["lead"]   # nach Geräte-ID
    _row(page, "Kasse").locator(".sw").click()                         # «Im Einsatz» aus: gleich gespeichert
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('nicht im Einsatz')")
    mon = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert {d["name"]: d.get("active", True) for d in mon} == {"Switch": True, "Service 1": True, "Kasse": False}
    # Geräteansicht: Verlauf statt zweiter Zeile; Festleitung: sperren ja, löschen nein
    _row(page, "Service 1").locator("[data-open]").first.click()
    page.wait_for_selector("details.hist")
    assert "1 früheres Zertifikat" in page.inner_text("details.hist summary")
    assert page.locator("[data-revoke]").count() == 1 and page.locator("#dev-delete").count() == 0
    assert page.locator('#dev-data input[name="name"]').get_attribute("readonly") is None    # umbenennen: neues Zertifikat
    assert "neues Zertifikat" in page.inner_text("#dev-data .hint")
    # Rolle und «Im Einsatz» auch in der Geräteansicht – speichern sofort (Rückmeldung zur -57)
    page.select_option("#dev-data .dev-quick select", "storno")
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('darf jetzt stornieren')")
    assert list(c.get("/api/admin/devices", auth=AUTH).json()["roles"].values()) == ["storno"]
    page.locator("#dev-data .dev-quick .sw").click()
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('nicht im Einsatz')")
    mon = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert next(d for d in mon if d["name"] == "Service 1")["active"] is False
    assert "noch nicht reserviert" in page.inner_text("#dev-data .warnbox")    # MAC unbekannt: Hinweis
    page.click("[data-close]")
    page.wait_for_selector("#dev-add")
    assert not errs, errs
    ctx.close()


def test_lead_adds_access_only_device(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _login(browser, base, "Lea", "Lea-passwort")
    page.click("#dev-add")
    assert page.locator('input[name="kind"]').count() == 3
    page.check('input[name="kind"][value="access"]')
    page.click("#wiz-next")
    page.fill('#wiz-data input[name="name"]', "kasse")                 # schon vergeben (ohne Gross/Klein)
    page.click("#wiz-next")
    assert page.locator('#wiz-data input[name="name"]').evaluate("(e) => e.validationMessage").startswith("Es gibt schon")
    page.fill('#wiz-data input[name="name"]', "Ersatz 1")
    page.click("#wiz-next")
    page.wait_for_selector(".warnbox")                                 # noch kein WLAN: Hinweis an den Admin, weiter geht
    assert "Admin" in page.inner_text(".warnbox") and not page.locator(".warnbox [data-goto]").count()
    page.click("#wiz-next")
    page.wait_for_selector(".qr-big svg")
    assert len(page.inner_text(".bigpw").strip()) >= 6
    page.click("#wiz-next")                                            # Fertig
    assert "ersatz 1" in _rows(page)
    log = [(e["action"], e["actor"]) for e in c.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert ("device_cert", "Lea") in log
    assert not errs, errs
    ctx.close()


def test_admin_deletes_device_and_revokes(server, browser):  # noqa: F811
    base, c = server
    assert c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Weg"}).status_code == 200
    ctx, page, errs = _login(browser, base)
    _row(page, "Weg").locator("[data-open]").first.click()
    page.wait_for_selector("#dev-delete")
    page.click("#dev-delete")                                          # ein Dialog (T3): «Zertifikat sperren» vorgewählt
    page.wait_for_selector("dialog.dlg[open]")
    assert page.is_checked('dialog.dlg input[name="revoke"]')
    page.click("dialog.dlg button.danger")
    page.wait_for_selector("#dev-add")
    assert "weg" not in _rows(page)
    page.click("details.set-card > summary")                           # unter «Gesperrt»
    assert "Weg" in page.inner_text("details.set-card")
    certs = [x for x in c.get("/api/admin/devices", auth=AUTH).json()["certs"] if x["name"] == "Weg"]
    assert certs and all(x["revoked_at"] for x in certs)
    page.on("dialog", lambda d: d.accept())                            # endgültig löschen? ja
    page.click('details.set-card [data-remove][data-n="Weg"]')        # endgültig löschen (Admin): weg aus der Liste
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('endgültig gelöscht')")
    # die Meldung kommt vor dem Neuzeichnen (RENDER.devices() lädt die Liste erst danach): auf die Liste warten
    _until(page, "!document.querySelector('#main').innerText.includes('Weg')")
    assert not [x for x in c.get("/api/admin/devices", auth=AUTH).json()["certs"] if x["name"] == "Weg"]
    assert not errs, errs
    ctx.close()


def test_delete_dialog_cancel_and_without_revoke(server, browser):  # noqa: F811
    """T3: Abbrechen lässt alles, wie es ist; ohne Häkchen bleibt das gültige Zertifikat (und das Gerät in der Liste)."""
    base, c = server
    assert c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Bleibt"}).status_code == 200
    ctx, page, errs = _login(browser, base)
    _row(page, "Bleibt").locator("[data-open]").first.click()
    page.click("#dev-delete")
    page.click("dialog.dlg #delcancel")
    page.wait_for_selector("dialog.dlg", state="detached")
    assert page.locator("#dev-delete").count() == 1                    # noch in der Geräteansicht
    page.click("#dev-delete")
    page.uncheck('dialog.dlg input[name="revoke"]')
    page.click("dialog.dlg button.danger")
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('noch gültig')")
    assert "bleibt" in _rows(page)
    assert not [x for x in c.get("/api/admin/devices", auth=AUTH).json()["certs"] if x["name"] == "Bleibt" and x["revoked_at"]]
    assert not errs, errs
    ctx.close()


def test_revoked_device_with_fixed_ip_is_under_blocked(server, browser):  # noqa: F811
    """T3: Sperren in der Geräteansicht (ohne Löschen) – das Gerät verlässt die Liste, die feste IP bleibt fürs Entsperren."""
    base, c = server
    r = c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Gesperrt 1"}).json()
    assert c.put("/api/admin/devices/phone", auth=AUTH, json={"name": "Gesperrt 1", "ip": "127.0.0.21",
                                                              "cert": r["device_id"]}).status_code == 200
    ctx, page, errs = _login(browser, base)
    _row(page, "Gesperrt 1").locator("[data-open]").first.click()
    page.on("dialog", lambda d: d.accept())                            # sperren? ja
    page.click("[data-revoke]")
    page.wait_for_selector("#main [data-unrevoke]")                    # Geräteansicht bleibt offen: jetzt gesperrt
    page.click("[data-close]")
    page.wait_for_selector("#dev-add")
    assert "gesperrt 1" not in _rows(page)
    page.click("details.set-card > summary")
    assert "Gesperrt 1 (127.0.0.21)" in page.inner_text("details.set-card")
    page.click('details.set-card [data-unrevoke][data-n="Gesperrt 1"]')
    _until(page, "[...document.querySelectorAll('#main tr[data-key] .dev-link b')].some((b) => b.textContent === 'Gesperrt 1')")
    assert "127.0.0.21" in _row(page, "Gesperrt 1").inner_text()       # mit seiner festen IP zurück
    assert not errs, errs
    ctx.close()


def test_renew_state_in_list_and_device_view(server, browser, tmp_path_factory):  # noqa: F811
    """T3: nahe am Ablauf zeigt die Liste, wer erneuert – der Pi (Fully) oder jemand von Hand."""
    import sqlite3
    base, c = server
    a = c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Hand 1"}).json()        # Test-CA: läuft in 30 Tagen ab
    b = c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Fully 1"}).json()
    mon = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    mon.append({"id": "p0000000000f1", "name": "Fully 1", "kind": "phone", "ip": "127.0.0.22", "port": None,
                "cert": b["device_id"], "fully_pin": "ab" * 32,           # Fully eingerichtet (geht sonst nur über Fully)
                "fully_until": "2026-01-01"})                             # Zertifikat der Fernverwaltung: abgelaufen
    db = next(tmp_path_factory.getbasetemp().glob("devices*/b.db"))
    with sqlite3.connect(db) as con:
        con.execute("UPDATE settings SET value=? WHERE key='monitor_devices'", (json.dumps(mon),))
    ctx, page, errs = _login(browser, base)
    assert "von Hand erneuern" in _row(page, "Hand 1").inner_text()
    assert "wird automatisch erneuert" in _row(page, "Fully 1").inner_text()
    _row(page, "Fully 1").locator("[data-open]").first.click()
    page.wait_for_selector("#dev-newcert")
    assert page.inner_text("#dev-newcert") == "Jetzt erneuern"                # Fully: über die Fernverwaltung
    assert "am Handheld ist nichts zu tun" in page.inner_text("#main")
    assert "Fully-Fernverwaltung läuft am" in page.inner_text("#main")      # F6: Hinweis aufs Fully-Zertifikat
    page.click("[data-close]")
    _row(page, "Hand 1").locator("[data-open]").first.click()
    page.wait_for_selector("#dev-newcert")
    assert page.inner_text("#dev-newcert") == "Neues Zertifikat" and "Ohne Fully" in page.inner_text("#main")
    assert a["device_id"] != b["device_id"]
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [801, 1100])
def test_table_fits_its_card(server, browser, width):  # noqa: F811
    """Rückmeldung zur -55: bei 801 px ragte die Tabelle über die Karte hinaus."""
    base, _ = server
    ctx, page, errs = _login(browser, base, w=width, h=900)
    _rows(page)
    over = page.evaluate("""() => { const c = document.querySelector('#main .devtbl').closest('.card').getBoundingClientRect();
      return [...document.querySelectorAll('#main .devtbl td, #main .devtbl th')].filter((e) => e.offsetParent)
        .some((e) => e.getBoundingClientRect().right > c.right + 1); }""")
    assert not over
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert not errs, errs
    ctx.close()


def test_sidebar_fixed_and_groups_always_open(server, browser):  # noqa: F811
    """T28: alle Menügruppen immer offen (Überschrift statt Klappknopf); reicht die Höhe nicht, scrollt die Leiste."""
    base, _ = server
    ctx, page, errs = _login(browser, base, w=1280, h=560)
    side = page.locator(".side")
    assert side.evaluate("(e) => getComputedStyle(e).position") == "sticky"
    assert side.bounding_box()["height"] <= 560
    assert [g.get_attribute("data-grp") for g in page.locator(".side .grp").all()] == ["op", "prep", "sys"]
    assert page.locator(".side button.grp").count() == 0                     # nichts zum Zuklappen
    assert not page.evaluate("[...document.querySelectorAll('.side .grp-items')].some((e) => e.hidden)")
    page.locator('.side [data-page="maint"]').scroll_into_view_if_needed()
    assert page.locator('.side [data-page="maint"]').is_visible()             # Leiste scrollt selbst
    page.evaluate("scrollTo(0, 10000)")
    assert abs(side.bounding_box()["y"]) < 1                          # Leiste bleibt stehen
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width", [412, 360])
def test_phone_menu_and_no_overflow(server, browser, width):  # noqa: F811
    base, _ = server
    ctx, page, errs = _login(browser, base, w=width, h=892)
    wide = "document.documentElement.scrollWidth <= innerWidth + 1"
    assert page.inner_text(".side .here") == "Geräte & Netz" and not page.locator(".side [data-page=products]").is_visible()
    assert page.evaluate(wide)
    page.click(".side .menu-btn")
    page.wait_for_selector(".side [data-page=products]", state="visible")
    page.click(".side [data-page=products]")                          # Wahl schliesst das Menü
    page.wait_for_selector(".side [data-page=products]", state="hidden")
    assert page.inner_text(".side .here") == "Produkte"
    page.click(".side .menu-btn")
    page.click(".side [data-page=devices]")
    page.click("#dev-add")
    assert page.evaluate(wide)                                         # Art
    page.click("#wiz-next")
    page.wait_for_selector("#wiz-data")
    assert page.evaluate(wide)                                         # Angaben (mit Hinweis an den Admin)
    assert not errs, errs
    ctx.close()


def test_legacy_duplicate_names_are_two_devices_and_marked(server, browser, tmp_path_factory):  # noqa: F811
    """T1: zwei gleichnamige, gültige Altgeräte (vor T1, ohne Geräte-ID) – zwei Zeilen, «Name doppelt», Hinweis in der
    Geräteansicht und in der Übersicht; neue doppelte Namen nimmt der Server nicht."""
    import sqlite3
    base, c = server
    db = next(tmp_path_factory.getbasetemp().glob("devices*/b.db"))
    with sqlite3.connect(db) as con:
        for serial in ("d1", "d2"):
            con.execute("INSERT INTO device_certs(name, serial, created_at, expires_at) VALUES ('Doppelt', ?, "
                        "'2026-01-01T10:00:00', '2036-01-01')", (serial,))
    assert c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "doppelt"}).status_code == 409
    for w in (1300, 360):
        ctx, page, errs = _login(browser, base, w=w, h=892)
        assert _rows(page).count("doppelt") == 2
        assert _row(page, "Doppelt").locator(".dup").count() == 2
        assert "Mehrere Geräte heissen gleich (2)" in page.inner_text("#dev-dup")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        _row(page, "Doppelt").first.locator("[data-open]").first.click()
        page.wait_for_selector("#dev-data")
        assert "heisst auch «Doppelt»" in page.inner_text("#main .warnbox")
        page.goto(base + "/admin.html#overview")
        page.reload()
        page.wait_for_selector("#main .kpi")
        assert "Mehrere Geräte heissen gleich" in page.inner_text("#main")
        assert not errs, errs
        ctx.close()


@pytest.mark.parametrize("width", [1300, 360])
def test_mac_change_alarm_and_decision(server, browser, tmp_path_factory, width):  # noqa: F811
    """T4: neue MAC mit demselben Zertifikat – Marke in der Liste, Alarm mit beiden MAC in der Geräteansicht,
    «Alte MAC behalten» bzw. «Neue MAC übernehmen», Karte in der Übersicht, Einstellung «Feste IP bei neuer MAC»."""
    import sqlite3
    base, c = server
    name = f"MAC {width}"
    r = c.post("/api/admin/devices/cert", auth=AUTH, json={"name": name}).json()
    did, pid = r["device_id"], f"p0000000{width:05d}"
    old, new = f"a4:bb:cc:dd:ee:{width % 100:02d}", f"6a:11:22:33:44:{width % 100:02d}"   # eigene pro Durchlauf
    mon = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    mon.append({"id": pid, "name": name, "kind": "phone", "ip": f"127.0.1.{width % 250}", "port": None, "cert": did, "mac": old})
    db = next(tmp_path_factory.getbasetemp().glob("devices*/b.db"))

    def state(st):
        with sqlite3.connect(db) as con:
            con.execute("UPDATE settings SET value=? WHERE key='monitor_devices'", (json.dumps(mon),))
            con.execute("INSERT INTO settings(key,value) VALUES ('mac_change_state', ?) ON CONFLICT(key) DO UPDATE "
                        "SET value=excluded.value", (json.dumps({did: st}),))
    state({"old": old, "new": new, "new_ip": "127.0.0.150", "first": 1, "state": "alarm", "reason": "both", "since": 1})
    ctx, page, errs = _login(browser, base, w=width, h=892)
    page.on("dialog", lambda d: d.accept())
    wide = "document.documentElement.scrollWidth <= innerWidth + 1"
    assert "Zertifikat doppelt" in _row(page, name).inner_text()
    _row(page, name).locator("[data-open]").first.click()
    page.wait_for_selector("#mac-box")
    box = page.inner_text("#mac-box")
    assert "Zwei Geräte mit demselben Zertifikat" in box and old in box and new in box and "127.0.0.150" in box
    assert page.locator("#mac-box [data-mac]").count() == 2 and page.locator("#mac-box [data-revoke]").count() == 1
    assert page.evaluate(wide)
    page.goto(base + "/admin.html#overview")
    page.reload()
    page.wait_for_selector("#main .mac-alarm")
    assert f"«{name}»: zwei Geräte mit demselben Zertifikat" in page.inner_text("#main")
    page.click("#main .mac-alarm a")
    _row(page, name).locator("[data-open]").first.click()
    page.click("#mac-box [data-mac=keep]")
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('bleibt bei der bisherigen MAC')")
    page.wait_for_selector("#dev-data .hint >> text=abgewiesen")
    st = c.get("/api/admin/devices", auth=AUTH).json()["mac"][did]
    assert st["state"] == "ignore" and st["new"] == new
    # Wartezeit: «Neue MAC übernehmen» schreibt gleich um
    state({"old": old, "new": new, "new_ip": "127.0.0.150", "first": 1, "state": "wait", "due": 2000000000})
    page.click("[data-close]")
    assert "Neue MAC" in _row(page, name).inner_text()
    _row(page, name).locator("[data-open]").first.click()
    page.wait_for_selector("#mac-box")
    assert "geht die feste IP um" in page.inner_text("#mac-box")
    assert "zufällige WLAN-Adresse" not in page.inner_text("#main")     # reserviert ist eine Hersteller-MAC
    page.click("#mac-box [data-mac=take]")
    _until(page, "!!document.querySelector('.toast')?.textContent.includes('umgeschrieben')")
    page.wait_for_selector("#dev-data .hint >> text=zufällige WLAN-Adresse")   # jetzt eine zufällige: Hinweis
    mon2 = json.loads(c.get("/api/admin/settings", auth=AUTH).json()["monitor_devices"])
    assert next(d for d in mon2 if d.get("id") == pid)["mac"] == new
    assert page.evaluate(wide)
    # Einstellung
    page.goto(base + "/admin.html#devices/setup")
    page.reload()
    page.click("#maccfg >> xpath=ancestor::details/summary")
    page.select_option("#maccfg select", "30")
    page.click("#maccfg .btn.pri")
    _until(page, "!!document.querySelector('.toast')")
    assert c.get("/api/admin/settings", auth=AUTH).json()["mac_change_minutes"] == "30"
    c.put("/api/admin/settings", auth=AUTH, json={"mac_change_minutes": 10})
    assert not errs, errs
    ctx.close()


def test_time_card_only_for_android_handhelds(server, browser):  # noqa: F811
    """T31: Karte «Uhrzeit» (Zeitserver per USB) nur bei Android-Handhelds – nicht bei «Nur Zugang»
    (PC, Laptop, iPhone)."""
    base, c = server
    assert c.post("/api/admin/devices/cert", auth=AUTH, json={"name": "Laptop Kasse"}).status_code == 200
    ctx, page, errs = _login(browser, base)
    _row(page, "Service 1").locator("[data-open]").first.click()
    page.wait_for_selector("#dev-usb-go")                                    # Handheld: Karte mit Knopf
    assert "nur Android" in page.inner_text("#dev-usb-go")
    page.click("[data-close]")
    _row(page, "Laptop Kasse").locator("[data-open]").first.click()
    page.wait_for_selector("[data-close]")
    _until(page, "!document.querySelector('#dev-time') || !document.querySelector('#dev-time .hint')")
    page.wait_for_timeout(500)
    assert page.locator("#dev-time").count() == 0                            # nur Zugang: keine Uhrzeit
    assert not errs, errs
    ctx.close()
