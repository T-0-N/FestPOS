"""Version 2026.10.10-12: Statusanzeige in allen Ansichten (3,5" am Pi, Pi-Bildschirm, Tablet hoch/quer, PC),
Name von der Bestellung bei den Handys, Mauszeiger, deutlichere Rahmen in der Einführung.
Im Browser; wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import json
import time
import uuid

import pytest

pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402



def _zu_breit(page, width: int) -> str:
    """Für die Fehlermeldung: welche Elemente ragen über den rechten Rand (Tag, Klasse, rechter Rand, Text)?"""
    rows = page.evaluate("""(w) => [...document.querySelectorAll('body *')].map((el) => {
        const r = el.getBoundingClientRect();
        return [Math.round(r.right), el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
          (typeof el.className === 'string' && el.className ? '.' + el.className.trim().replace(/\\s+/g, '.') : ''),
          (el.innerText || '').replace(/\\s+/g, ' ').slice(0, 60)];
      }).filter((x) => x[0] > w).sort((a, b) => b[0] - a[0]).slice(0, 8)""", width)
    return f"zu breit (> {width} px): {rows}"

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("status")
    # automatische Sicherung auf den USB-Stick: steht in der Zeile des Pi und muss in jeder Ansicht Platz haben
    (tmp / "sicherung.json").write_text(json.dumps({"state": "ok", "at": int(time.time()), "last_ok": int(time.time()) - 420,
                                                   "file": "festpos-a-20261021-201500.sqlite", "size": 123456}))
    proc, base, c = _start(tmp, {"FESTPOS_SICHERUNG_STATUS": str(tmp / "sicherung.json")})
    c.post("/api/admin/printers", auth=AUTH, json={"name": "Kaffee-Bar mit langem Namen", "host": "127.0.0.1", "port": 1})
    phones = [{"name": f"XCover {i}", "kind": "phone", "ip": f"127.0.0.{i}", "port": None} for i in (2, 3, 4)]
    phones.append({"name": "Switch", "kind": "net", "ip": "127.0.0.1", "port": None})   # sonst fällt «Netzwerk» weg
    c.put("/api/admin/settings", auth=AUTH, json={"monitor_devices": phones, "event_name": "Sommerfest Muster"})
    pid = c.get("/api/bootstrap").json()["products"][0]["id"]
    for i, who in ((2, "Anna"), (3, "XCover 3")):                 # XCover 3 heisst in der App gleich
        hc = httpx.Client(base_url=base, transport=httpx.HTTPTransport(local_address=f"127.0.0.{i}"))
        hc.get("/api/bootstrap", headers={"X-Festpos-Device": who, "X-Festpos-Battery": "61", "X-Festpos-Charging": "0"})
        for k in range(5):
            hc.post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": str(10 + k), "device": who,
                                         "lines": [{"product_id": pid, "qty": 1}]}, headers={"X-Festpos-Device": who})
    yield base, c
    proc.terminate()
    proc.wait(10)


def test_pi_row_shows_backup_age(server, browser):  # noqa: F811
    base, c = server
    for _ in range(40):                                          # Infos des Pi kommen mit dem Monitor (alle 10 s)
        pis = c.get("/api/status", auth=AUTH).json()["pis"]
        if (pis[0].get("info") or {}).get("backup"):
            break
        time.sleep(0.5)
    assert pis[0]["info"]["backup"]["state"] == "ok"
    ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    page = ctx.new_page()
    page.goto(base + "/status.html?ansicht=pc")
    page.wait_for_selector("#cluster .item")
    assert "gesichert vor 7 Min." in " ".join(page.inner_text("#cluster").split())    # nur bei « · » umbrechen
    page.click("#cluster .item")                                          # Details: Sicherung und alle Werte
    page.wait_for_selector("#detail-sheet .panel")
    assert "Sicherung (USB-Stick)" in page.inner_text("#detail-sheet")
    ctx.close()


def test_phone_shows_name_from_the_orders(server):
    _, c = server
    dev = {d["name"]: d for d in c.get("/api/status", auth=AUTH).json()["devices"]}
    assert dev["XCover 2"]["app_name"] == "Anna" and dev["XCover 3"]["app_name"] == "XCover 3"
    assert dev["XCover 4"]["app_name"] is None                    # noch nie gemeldet


# Ansicht, Grösse, erwartete Spalten im Hauptbereich (Anzahl verschiedener x-Positionen der Bereiche)
VIEWS = [("", 480, 320, "small"), ("", 800, 480, "v-pi"), ("", 1920, 1080, "v-pi"), ("", 720, 1280, "v-pi"),
         ("tablet", 800, 1280, "v-tablet"), ("tablet", 1280, 800, "v-tablet"), ("pc", 1920, 1080, "v-pc"),
         ("pc", 1366, 768, "v-pc")]

_LAYOUT = """() => {
  const box = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
    return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }; };
  const cut = [];
  for (const e of document.querySelectorAll(".printer .st, #devices .lamp, .order .amt, header .clock")) {
    if (!e.offsetParent) continue;                               // ausgeblendete Bestellung (passt nicht mehr)
    const r = e.getBoundingClientRect(), card = e.closest(".printer, .card, header").getBoundingClientRect();
    if (r.right > card.right + 1 || r.left < card.left - 1) cut.push(e.textContent.trim().slice(0, 30));
  }
  for (const e of document.querySelectorAll("#devices .lamp"))
    if (e.scrollWidth > e.clientWidth + 1) cut.push("Name: " + e.textContent.trim());
  for (const e of document.querySelectorAll("#cluster .item"))       // Server: eine Zeile pro Pi, nichts abgeschnitten
    if (e.getBoundingClientRect().right > e.closest(".card").getBoundingClientRect().right + 1) cut.push("Server: " + e.textContent.trim());
  return { cls: document.documentElement.className, printers: box("#printers"), log: box(".log"), phones: box(".phones"),
           net: box(".net"), srv: box(".clu"), orders: [...document.querySelectorAll(".order")].filter((e) => e.offsetParent).length, cut,
           lastIn: (() => { const l = document.querySelector(".log").getBoundingClientRect(), o = [...document.querySelectorAll(".order")].filter((e) => e.offsetParent).pop();
             return !o || o.getBoundingClientRect().bottom <= l.bottom + 1; })(),
           page: document.documentElement.scrollWidth <= innerWidth + 1 };
}"""


@pytest.mark.parametrize("view,w,h,cls", VIEWS)
def test_status_views_fit(server, browser, view, w, h, cls):  # noqa: F811
    base, _ = server
    ctx = browser.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/status.html" + (f"?ansicht={view}" if view else ""))
    page.wait_for_selector("#devices .item")
    page.wait_for_timeout(600)
    lay = page.evaluate(_LAYOUT)
    assert cls in lay["cls"].split(), lay["cls"]
    assert lay["page"] and not lay["cut"], lay
    assert lay["orders"] >= 1 and lay["lastIn"], lay              # nie eine halb abgeschnittene Bestellung
    p, log, ph = lay["printers"], lay["log"], lay["phones"]
    if cls == "small":
        pass
    elif h > w * 0.8:                                             # hochkant: Drucker, Handys + Netzwerk, Bestellungen
        assert p["y"] < ph["y"] < log["y"] and abs(ph["y"] - lay["net"]["y"]) < 4
        assert lay["net"]["y"] < lay["srv"]["y"] < log["y"]          # Server unter dem Netzwerk
        assert log["h"] > h * 0.3                                  # Bestellungen nutzen den Rest
    elif w >= 1000:                                               # quer und breit: Drucker als Reihe, darunter Bestellungen
        assert p["x"] == log["x"] < ph["x"] and p["y"] < log["y"] and abs(p["y"] - ph["y"]) < 4
        assert log["w"] > ph["w"] * 2 and lay["orders"] >= 8          # eine Zeile pro Bestellung, mehr sichtbar
    else:                                                         # Pi 7": zwei Spalten
        assert p["x"] == log["x"] < ph["x"]
    if "Anna" in page.inner_text("#devices"):
        assert page.locator("#devices .who").first.inner_text() == "Anna"
    assert page.locator("#devices .who", has_text="XCover 3").count() == 0     # gleicher Name: nicht doppelt
    assert not errs, errs
    ctx.close()


def test_view_is_remembered_and_auto_resets(server, browser):  # noqa: F811
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    page = ctx.new_page()
    page.goto(base + "/status.html?ansicht=tablet")
    page.wait_for_selector("#devices .item")
    page.goto(base + "/status.html")
    page.wait_for_selector("#devices .item")
    assert "v-tablet" in page.evaluate("document.documentElement.className")      # gemerkt
    page.goto(base + "/status.html?ansicht=auto")
    page.wait_for_selector("#devices .item")
    assert "v-pi" in page.evaluate("document.documentElement.className")          # 127.0.0.1 = Bildschirm am Pi
    ctx.close()


def test_mouse_pointer_appears_while_moving(server, browser):  # noqa: F811
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    page = ctx.new_page()
    page.goto(base + "/status.html?ansicht=pc")
    page.wait_for_selector("#devices .item")
    cursor = "() => getComputedStyle(document.querySelector('.log')).cursor"
    # in Ruhe kein Zeiger – meldet Chromium beim Laden selbst eine Mausbewegung, erst nach deren 3 s (CI, langsam)
    _until(page, "getComputedStyle(document.querySelector('.log')).cursor === 'none'", 4500)
    page.mouse.move(300, 300)
    page.mouse.move(320, 310)
    assert page.evaluate(cursor) != "none"
    page.wait_for_timeout(3300)
    assert page.evaluate(cursor) == "none"                        # nach 3 s Ruhe wieder weg
    ctx.close()


def test_tour_frame_is_clearly_visible(server, browser):  # noqa: F811
    base, _ = server
    ctx = browser.new_context(viewport={"width": 412, "height": 860})
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Rahmen'))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    page.click('[data-act="settings"]')
    page.click('[data-act="tour-start"]')
    seen = 0
    for _ in range(20):
        page.wait_for_selector(".tour-bubble")
        spot = page.evaluate("""() => { const s = document.querySelector('.tour-spot'), r = s.getBoundingClientRect();
          return { none: s.classList.contains('none'), x0: r.left, y0: r.top, x1: r.right, y1: r.bottom,
                   shadow: getComputedStyle(s).boxShadow, ring: getComputedStyle(s, '::after').borderTopWidth }; }""")
        if not spot["none"]:
            seen += 1
            assert spot["x0"] >= 14 and spot["y0"] >= 14 and spot["x1"] <= 412 - 14 and spot["y1"] <= 860 - 14, spot
            assert "rgb(255, 212, 0)" in spot["shadow"] and spot["ring"] == "4px"   # gelber Rahmen + Ring
        if page.locator('.tour-nav [data-act="tour-end"]').count():
            break
        page.click('[data-act="tour-next"]')
    assert seen >= 8
    assert not errs, errs
    ctx.close()


def test_display_helper_sets_mouse_pointer(tmp_path, monkeypatch):
    from tests.test_stufe2 import _display_helper
    mod, calls = _display_helper(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "set_config", lambda lines: False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(mod.os.path, "exists", lambda p: True if p.endswith("festpos-kiosk.service") else
                        __import__("os").path.isfile(p) or __import__("os").path.isdir(p))
    wish, cur = tmp_path / "display.json", tmp_path / "festpos-kiosk-cursor"

    def run(kind, cursor=None):
        wish.write_text(json.dumps({"type": kind, "reboot": "", **({"cursor": cursor} if cursor else {})}))
        calls.clear()
        mod.main()
        return cur.read_text().strip(), ("systemctl", "restart", "festpos-kiosk") in calls

    assert run("hdmi") == ("on", True)                            # HDMI: eher eine Maus dran
    assert run("hdmi")[0] == "on"
    assert run("tft35") == ("off", True)                          # Touch: Pfeil würde mitspringen
    assert run("tft35", "on") == ("on", True)
    assert run("tft35", "on")[0] == "on"
    assert run("hdmi", "off") == ("off", True)
    time.sleep(0)


def test_server_card_shows_each_pi_and_names_the_problem(server, browser):  # noqa: F811
    """2026.10.10-27: eine Zeile pro Pi (druckt, Temperatur, Speicher); ein Dienst, der nicht läuft, steht beim Pi –
    nicht mehr nur die Werte des Pi, an dem der Bildschirm hängt."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    page = ctx.new_page()
    errs = _watch(page)

    def three_pis(route):
        r = route.fetch()
        data = r.json()
        data["cluster"] = [{"id": "a", "reachable": True, "leader": True}, {"id": "b", "reachable": True, "leader": False},
                           {"id": "c", "reachable": False, "leader": False}]
        gb = 10 ** 9
        data["pis"] = [
            {"id": "a", "here": True, "leader": True, "reachable": True, "info": {"temp": 99, "disk_free": 5 * gb, "disk_total": 10 * gb}},
            {"id": "b", "here": False, "leader": False, "reachable": True,
             "info": {"temp": 51, "disk_free": 86 * gb, "disk_total": 100 * gb, "svc": {"rqlited": True, "nftables": False}}},
            {"id": "c", "here": False, "leader": False, "reachable": False, "info": {"temp": 40}}]
        data["services"], data["system"] = [{"name": "rqlited", "ok": True}], {"temp": 48, "disk_free": 87}   # dieser Pi: frisch
        route.fulfill(response=r, json=data)
    page.route("**/api/status", three_pis)
    page.goto(base + "/status.html")
    page.wait_for_selector("#cluster .item >> nth=2")
    rows = [" ".join(x.split()) for x in page.locator("#cluster .item").all_inner_texts()]
    # kurz: Temperatur und Speicher nur, wenn warm bzw. knapp – die Werte stehen in den Details
    assert rows == ["Pi A (hier) druckt", "Pi B Firewall läuft nicht", "Pi C nicht erreichbar"], rows
    assert [page.locator("#cluster .item .lamp").nth(i).get_attribute("class") for i in range(3)] == ["lamp ok", "lamp bad", "lamp bad"]
    detail = page.inner_text("#detail")
    assert "Pi B: Firewall läuft nicht" in detail and "Pi C nicht erreichbar" in detail
    assert "2 von 3 erreichbar" in page.inner_text("#cluster-sum")
    page.click("#cluster .item >> nth=1")                                  # antippen: Details dieses Pi
    page.wait_for_selector("#detail-sheet .panel")
    assert "Pi B" in page.inner_text("#detail-sheet")
    assert not errs, errs
    ctx.close()


def test_phone_without_fully_licence_is_orange(server, browser):  # noqa: F811
    """T5: meldet Fully «keine Lizenz», steht das Handy orange da (wie Kiosk aus), in den Details
    «Fully-Lizenz: fehlt»."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    page = ctx.new_page()
    errs = _watch(page)

    def unlicensed(route):
        r = route.fetch()
        data = r.json()
        for d in data["devices"]:
            if d["name"] == "XCover 4":
                d["fully"] = {"state": "ok", "seconds": 3, "data_seconds": 3,
                              "data": {"kiosk": True, "kiosk_locked": True, "licensed": False, "battery": 80}}
        route.fulfill(response=r, json=data)
    page.route("**/api/status", unlicensed)
    page.goto(base + "/status.html")
    page.wait_for_selector(".item .lamp.kiosk")
    item = page.locator(".item", has=page.locator(".lamp.kiosk"))
    assert "XCover 4" in item.inner_text() and "ohne Lizenz" in item.inner_text()
    item.click()
    page.wait_for_selector("#detail-sheet .panel")
    assert "Fully-Lizenz" in page.inner_text("#detail-sheet") and "fehlt" in page.inner_text("#detail-sheet")
    assert not errs, errs
    ctx.close()


def test_phone_with_copied_certificate_is_red(server, browser):  # noqa: F811
    """T4: zwei Geräte mit demselben Zertifikat – das Handy steht rot da, «Zertifikat doppelt», oben als Problem."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": 1280, "height": 720})
    page = ctx.new_page()
    errs = _watch(page)

    def alarm(route):
        r = route.fetch()
        data = r.json()
        for d in data["devices"]:
            d["mac_alarm"] = d["name"] == "XCover 3"
        route.fulfill(response=r, json=data)
    page.route("**/api/status", alarm)
    page.goto(base + "/status.html")
    page.wait_for_selector(".item .lamp.bad")
    item = page.locator(".item", has=page.locator(".lamp.bad"))
    assert "XCover 3" in item.inner_text() and "Zertifikat doppelt" in item.locator(".note").text_content()   # Text evtl. ausgeblendet (eng)
    assert "XCover 3: zwei Geräte mit demselben Zertifikat" in page.inner_text("header")
    assert not errs, errs
    ctx.close()


def test_internet_row_red_only_when_needed_and_never_twice(server, browser):  # noqa: F811
    """mit «Dauerhaft verbunden» eine Zeile Internet (Weg, Pi, erreichbar, Tunnel). Ohne Verbindung rot,
    wenn Karte/TWINT oder QR-Bestellung es brauchen, sonst gelb; über den Router meldet sich nur der Router."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": 412, "height": 892})
    page = ctx.new_page()
    errs = _watch(page)
    now = int(time.time())
    case = {"via": "wlan", "online": True, "needed": True, "router": None, "reason": None}

    def fake(route):
        r = route.fetch()
        data = r.json()
        data["internet"] = {"via": case["via"], "needed": case["needed"], "pay": case["needed"], "qr": False, "pis": [
            {"id": data["pis"][0]["id"], "state": "up", "online": case["online"], "device": True, "share": True,
             "since": now - 600, "tunnel": None, "reason": case["reason"], "ssid": "Fest-Hotspot"}]}
        data["uplink"], data["uplink_needed"] = case["router"], case["router"] is not None
        route.fulfill(response=r, json=data)
    page.route("**/api/status", fake)
    page.goto(base + "/status.html")
    page.wait_for_selector("#inet:not([hidden]) .val")
    assert " ".join(page.inner_text("#inet").split()) == "Internet erreichbar über WLAN"
    assert "ok" in page.get_attribute("#inet", "class")
    case["online"] = False                                                # weg und gebraucht: rot, oben gemeldet
    page.wait_for_selector("#inet.bad")
    assert "Internet nicht erreichbar" in page.inner_text("#detail")
    case["reason"] = "wlan_not_found"                                     # mit Grund (T-0-N/FestPOS#1)
    _until(page, "document.getElementById('inet').innerText.includes('nicht gefunden')")
    assert "WLAN «Fest-Hotspot» nicht gefunden" in page.inner_text("#inet")
    case["reason"] = "dns"
    _until(page, "document.getElementById('inet').innerText.includes('DNS')")
    case["needed"] = False                                                # nicht gebraucht: nur gelb
    page.wait_for_selector("#inet.wait")
    assert "Internet nicht erreichbar" not in page.inner_text("#detail")    # kein Fehler (gestörte Drucker stehen vorne)
    case.update(via="lan", needed=True, router={"name": "Router", "state": "unreachable", "seconds": 3, "interval": 10})
    page.wait_for_selector("#uplink.bad")                                 # Router rot – Internet nicht nochmals
    _until(page, "document.getElementById('inet').classList.contains('wait')")
    assert "Internet nicht erreichbar" not in page.inner_text("#detail") and "Router" in page.inner_text("#detail")
    page.click("#inet")                                                   # Details: Weg, wofür, jeder Pi
    page.wait_for_selector("#detail-sheet .panel")
    sheet = page.inner_text("#detail-sheet")
    assert "über Router" in sheet and "Karte/TWINT" in sheet and "gibt weiter" in sheet and "nicht erreichbar" in sheet
    assert "Namensauflösung (DNS) geht nicht" in sheet
    assert page.evaluate("document.documentElement.scrollWidth") <= 412, _zu_breit(page, 412)
    assert not errs, errs
    ctx.close()


def test_orders_since_closing_paged(server):
    """T29: Bestellungen seit dem Tagesabschluss seitenweise, neueste zuerst; eine Bestellung im Detail."""
    _, c = server
    first = c.get("/api/status/orders?limit=3", auth=AUTH).json()
    assert len(first["orders"]) == 3 and first["more"] is True and first["count"] >= 10
    ids = [o["id"] for o in first["orders"]]
    assert ids == sorted(ids, reverse=True)
    nxt = c.get(f"/api/status/orders?limit=3&before={ids[-1]}", auth=AUTH).json()
    assert nxt["orders"][0]["id"] < ids[-1]
    d = c.get(f"/api/status/orders/{ids[0]}", auth=AUTH).json()
    assert d["lines"] and d["lines"][0]["qty"] >= 1 and "jobs" in d and d["table_label"]
    assert c.get("/api/status/orders/999999", auth=AUTH).status_code == 404
    # dieselben Rechte wie /api/status: am Pi selbst (hier 127.0.0.1) ohne Anmeldung
    assert c.get("/api/status/orders").status_code == c.get("/api/status").status_code == 200


@pytest.mark.parametrize("width,ansicht", [(1280, "pc"), (360, "auto"), (480, "klein")])
def test_recent_orders_card_opens_list_and_order(server, browser, width, ansicht):  # noqa: F811
    """T29: «Letzte Bestellungen» antippen → alle seit dem Tagesabschluss (scrollbar), eine antippen → Details."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": width, "height": 892 if width < 480 else 720 if width > 480 else 320})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + f"/status.html?ansicht={ansicht}")
    page.wait_for_selector("#orders .order")
    page.click(".card.log h2")
    page.wait_for_selector("#detail-sheet .ol-row")
    assert "Bestellungen seit dem Tagesabschluss" in page.inner_text("#detail-sheet .head")
    assert page.locator("#detail-sheet .ol-row").count() >= 10
    assert page.inner_text("#detail-sheet .ol-sum").startswith("10 Bestellungen")
    page.locator("#detail-sheet .ol-row").first.click()
    page.wait_for_selector("#detail-sheet .od-lines .l")
    sheet = page.inner_text("#detail-sheet")
    assert "Positionen" in sheet and "Zahlart" in sheet and "#00" in page.inner_text("#detail-sheet .head")
    page.click('#detail-sheet [data-act="orders-back"]')
    page.wait_for_selector("#detail-sheet .ol-row")
    assert page.evaluate("document.documentElement.scrollWidth") <= width, _zu_breit(page, width)
    assert page.evaluate("(() => { const p = document.querySelector('#detail-sheet .panel'); return p.scrollWidth <= p.clientWidth + 1; })()")
    assert not errs, errs
    ctx.close()


@pytest.mark.parametrize("width,ansicht", [(1280, "pc"), (800, "auto")])
def test_tap_order_opens_its_details(server, browser, width, ansicht):  # noqa: F811
    """Rückmeldung Punkt 2: eine Bestellung in «Letzte Bestellungen» antippen öffnet gleich diese Bestellung – nicht erst
    die Liste. «‹ Alle Bestellungen» führt danach zur Liste."""
    base, _ = server
    ctx = browser.new_context(viewport={"width": width, "height": 480 if width == 800 else 720})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + f"/status.html?ansicht={ansicht}")
    page.wait_for_selector("#orders .order")
    row = page.locator("#orders .order").nth(1)
    no = row.get_attribute("data-order")
    row.click()
    page.wait_for_selector("#detail-sheet .od-lines .l")
    assert f"#{no.zfill(4)}" in page.inner_text("#detail-sheet .head") and page.locator("#detail-sheet .ol-row").count() == 0
    page.click('#detail-sheet [data-act="orders-back"]')
    page.wait_for_selector("#detail-sheet .ol-row")
    assert "Bestellungen seit dem Tagesabschluss" in page.inner_text("#detail-sheet .head")
    assert not errs, errs
    ctx.close()
