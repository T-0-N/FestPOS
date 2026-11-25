"""Im echten Browser (Chromium/Playwright): Wischen zwischen Kategorien, grosse Produktbilder, Testmodus,
Einführung – und ob wirklich JEDER sichtbare Text aus der Sprachdatei kommt (Pseudo-Sprache).
Wird übersprungen, wenn Playwright oder Chromium fehlt (z.B. auf dem Pi)."""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
httpx = pytest.importorskip("httpx2")

from tests.conftest import free_port  # noqa: E402
from tests.i18n_pseudo import leftover_words, write_pseudo  # noqa: E402
from tests.test_api import AUTH, setup_shop  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _start(tmp: Path, extra_env: dict | None = None, before_shop=None, target: str = "app.main:app",
           phone_port: bool = False):
    """phone_port: der Testserver gilt als Handy-Port (z.B. Statusanzeige für Handhelds mit Zertifikat)."""
    port = free_port()
    env = {**os.environ, "FESTPOS_DB": f"sqlite://{tmp}/b.db", "FESTPOS_ADMIN_PASSWORD": "geheim",
           "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_NODE": "a", **(extra_env or {}),
           **({"FESTPOS_PHONE_PORT": str(port)} if phone_port else {})}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if httpx.get(base + "/api/health", timeout=1).json().get("ready"):
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Server startet nicht")
    c = httpx.Client(base_url=base, timeout=10)
    if before_shop:
        before_shop(c)
    setup_shop(c)
    return proc, base, c


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("p4"))
    yield base, c
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium nicht startbar: {e}")
        yield b
        b.close()


def _favicon(m) -> bool:
    """Favicon-Anfrage des Browsers (sichtbarer Chromium): der Name steht in der URL, nicht im Text der Meldung."""
    return "favicon" in m.text or "favicon" in ((m.location or {}).get("url") or "")


def _confirm_ask(m) -> bool:
    """428 «Bitte bestätigen» (kritische Aktion, T-0-N/FestPOS#9): gehört zum Ablauf, kein Fehler."""
    return "status of 428" in m.text


def _watch(page) -> list[str]:
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
    page.on("console", lambda m: errs.append(f"console: {m.text}")
            if m.type == "error" and not _favicon(m) and not _confirm_ask(m) else None)
    return errs


def _preconfirm(page, pw: str = "geheim") -> None:
    """Kritische Aktionen für die nächsten 5 Minuten bestätigen (wie «Bitte bestätigen», #9), dann neu laden – für Tests,
    in denen es um die Aktion selbst geht. Die Verwaltung liest das Token beim Start aus dem sessionStorage."""
    page.evaluate("""async (pw) => {
      const r = await fetch('/api/admin/me/confirm', {method: 'POST', body: JSON.stringify({password: pw}), headers: {
        Authorization: 'Bearer ' + sessionStorage.getItem('festpos.token'), 'Content-Type': 'application/json', 'X-Festpos-UI': '1'}});
      if (!r.ok) throw new Error('confirm ' + r.status);
      sessionStorage.setItem('festpos.token', (await r.json()).token);
    }""", pw)
    page.reload()
    page.wait_for_selector(".side")


def _confirm(page, pw: str = "geheim", code: str | None = None) -> None:
    """Fenster «Bitte bestätigen» (kritische Aktion, T-0-N/FestPOS#9) abwarten und mit dem Passwort bestätigen –
    danach läuft die Aktion von selbst weiter."""
    page.wait_for_selector(".confirm-dlg[open]")
    page.fill(".confirm-dlg [name=password]", pw)
    if code is not None:
        page.fill(".confirm-dlg [name=code]", code)
    page.click(".confirm-dlg .btn.pri")


def _phone(browser, base, name=None):
    ctx = browser.new_context(viewport={"width": 412, "height": 900})
    if name:                                              # Gerätename schon gesetzt (keine Einführung)
        ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify({name!r}))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    # Bootstrap geladen (Testmodus, Produkte …): sonst fehlt z.B. «Testmodus starten» in den Einstellungen, wenn ein
    # Test schneller ist als die Antwort (CI, 1.1.0-rc.2)
    for _ in range(150):
        if page.evaluate("!!localStorage.getItem('festpos.boot')"):
            break
        page.wait_for_timeout(100)
    return ctx, page, errs


def _open_table(page, n="3"):
    for d in n:
        page.click(f'[data-act="digit"][data-d="{d}"]')
    page.click('[data-act="table-ok"]')
    page.wait_for_selector(".grid .tile")


def _drag(page, x0, x1, y):
    page.mouse.move(x0, y)
    page.mouse.down()
    for k in range(1, 9):
        page.mouse.move(x0 + (x1 - x0) * k / 8, y)
    page.mouse.up()
    page.wait_for_timeout(250)


def _cat(page) -> str:
    return page.inner_text('.cat[aria-pressed="true"]')


def test_swipe_between_categories(server, browser):
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"swipe_categories": True})
    ctx, page, errs = _phone(browser, base, "Swipe")
    _open_table(page)
    assert _cat(page) == "Getränke"
    box = page.locator(".grid .tile").first.bounding_box()
    y = box["y"] + box["height"] / 2
    _drag(page, box["x"] + box["width"] - 10, box["x"] + 20, y)        # nach links wischen, innerhalb EINER Kachel
    assert _cat(page) == "Essen"
    assert page.inner_text("#cnt") != page.evaluate("t('app.cart.show_n', {count: 1})"), "Wischen darf nichts bestellen"
    _drag(page, 40, 360, y)                                              # zurück
    assert _cat(page) == "Getränke"
    _drag(page, 40, 360, y)                                              # schon am Anfang: bleibt
    assert _cat(page) == "Getränke"
    page.wait_for_timeout(500)                                           # kurz nach dem Wischen zählt kein Tipp
    page.mouse.click(box["x"] + 30, y)                                   # normales Antippen geht weiterhin
    assert page.locator(".tile.on").count() == 1
    # ausgeschaltet in der Verwaltung
    c.put("/api/admin/settings", auth=AUTH, json={"swipe_categories": False})
    page.evaluate("loadBoot()")
    page.wait_for_function("() => !document.querySelector('.grid.swipe')")
    _drag(page, 380, 40, y + 140)
    assert _cat(page) == "Getränke"
    c.put("/api/admin/settings", auth=AUTH, json={"swipe_categories": True})
    assert not errs, errs
    ctx.close()


def test_long_press_does_not_select_text(server, browser):
    """Langes Drücken öffnet die Optionen – dabei darf Chrome keinen Text markieren (kein Kontextmenü)."""
    base, c = server
    pid = c.get("/api/bootstrap").json()["products"][0]["id"]
    c.put(f"/api/admin/products/{pid}/options", auth=AUTH, json={"groups": [{"name": "Glas", "choices": ["gross", "klein"]}]})
    ctx, page, errs = _phone(browser, base, "Lang")
    _open_table(page)
    style = lambda sel: page.locator(sel).first.evaluate("e => getComputedStyle(e).userSelect")   # noqa: E731
    assert style("body") == "none" and style(".tile .nm") == "none"
    page.click("#stchip")                                                # Status › Einstellungen (Version -31)
    page.click('.sheet [data-act="settings"]')
    assert style("#devname") == "text"                                   # Eingabefelder bleiben normal
    page.evaluate("closeSheet()")
    tile = page.locator(f'.tile[data-id="{pid}"]')
    box = tile.bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + 30)
    page.mouse.down()
    page.wait_for_selector(".sheet .optg", timeout=3000)                 # lange gedrückt: Optionen offen
    page.mouse.up()
    prevented = page.evaluate("""() => { const h = document.querySelector('.sheet h2');
      const ev = new MouseEvent('contextmenu', { bubbles: true, cancelable: true }); h.dispatchEvent(ev);
      return ev.defaultPrevented; }""")
    # so wie Chrome nach langem Drücken: Text im Fenster wird markiert -> die App hebt es sofort auf
    page.evaluate("""() => { const r = document.createRange(); r.selectNodeContents(document.querySelector('.sheet h2'));
      getSelection().removeAllRanges(); getSelection().addRange(r); }""")
    page.wait_for_timeout(150)
    assert prevented and page.evaluate("getSelection().toString()") == ""
    c.put(f"/api/admin/products/{pid}/options", auth=AUTH, json={"groups": [], "free": False})
    assert not errs, errs
    ctx.close()
    # die Verwaltung bleibt markierbar (Fingerabdruck kopieren usw.)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(base + "/admin.html")
    page.wait_for_selector("#pw")
    assert page.evaluate("getComputedStyle(document.body).userSelect") != "none"
    ctx.close()


def _image(color: str) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def test_product_image_sizes(server, browser):
    base, c = server
    pid = c.get("/api/bootstrap").json()["products"][0]["id"]
    c.post(f"/api/admin/products/{pid}/image", auth=AUTH, files={"file": ("x.jpg", _image("orange"))})
    c.put("/api/admin/settings", auth=AUTH, json={"product_images": "large"})
    ctx, page, errs = _phone(browser, base, "Bilder")
    _open_table(page)
    tile = page.locator(f'.tile[data-id="{pid}"]')
    tb, ib = tile.bounding_box(), tile.locator("img").bounding_box()
    assert abs(ib["height"] - (tb["height"] - 4)) <= 2, (tb, ib)        # ganze Höhe (ohne Rahmen)
    assert 0.5 < ib["width"] / tb["width"] < 0.65 and ib["x"] + ib["width"] >= tb["x"] + tb["width"] - 3   # rechts
    assert tb["height"] < 130                                            # Kachel bleibt gleich gross
    bg = tile.locator(".nm").evaluate("e => getComputedStyle(e).backgroundColor")
    assert bg == "rgb(255, 255, 255)"                                    # Text auf hellem Grund
    tile.click()
    assert tile.locator("img").is_visible()                              # gewählt: Bild bleibt (abgedunkelt)
    c.put("/api/admin/settings", auth=AUTH, json={"product_images": "none"})
    page.evaluate("loadBoot()")
    page.wait_for_function("() => !document.querySelector('.tile img')")
    c.put("/api/admin/settings", auth=AUTH, json={"product_images": "small"})
    page.evaluate("loadBoot()")
    page.wait_for_selector(".tile img", state="attached")
    assert page.locator(".tile img").first.evaluate("e => getComputedStyle(e).width") == "46px"   # klein: Ecke
    c.delete(f"/api/admin/products/{pid}/image", auth=AUTH)
    assert not errs, errs
    ctx.close()


def _enter_pin(page, act, pin):
    for d in pin:
        page.click(f'[data-act="{act}"][data-d="{d}"]')


def test_test_mode_marked_everywhere(server, browser):
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468", "test_print": False})
    before = c.get("/api/admin/stats", auth=AUTH).json()
    ctx, page, errs = _phone(browser, base, "Übungshandy")
    from tests.test_browser_receipt import _until                    # (dort wird diese Datei importiert)
    _until(page, "S.boot?.test?.enabled === true")                  # Testmodus bekannt (die Tischwahl steht vorher)
    page.click('[data-act="settings"]')
    page.click('[data-act="test-start"]')
    _enter_pin(page, "tpin", "1111")
    assert page.inner_text("#tpinmsg")                                   # falsche PIN: Meldung, kein Testmodus
    assert page.evaluate("document.body.classList.contains('testmode')") is False
    _enter_pin(page, "tpin", "2468")
    page.wait_for_selector("body.testmode .testbar")
    _open_table(page, "8")
    assert page.locator(".testbar").is_visible()
    page.click(".grid .tile >> nth=0")
    page.click("#send")
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    assert page.locator(".testbar").is_visible() and page.locator(".banner.test").is_visible()
    assert page.locator(".bon.test").count() == 1
    after = c.get("/api/admin/stats", auth=AUTH).json()
    assert after["orders"] == before["orders"] and after["test_orders"] == before["test_orders"] + 1
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    page.click('.testbar [data-act="test-exit"]')
    page.wait_for_function("() => !document.body.classList.contains('testmode')")
    assert page.locator(".testbar").count() == 0
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": False})
    assert not errs, errs
    ctx.close()


def test_tutorial_starts_after_naming_and_changes_nothing(server, browser):
    base, c = server
    c.put("/api/admin/settings", auth=AUTH, json={"tutorial_auto": True})
    before = c.get("/api/admin/stats", auth=AUTH).json()
    ctx, page, errs = _phone(browser, base)
    for _ in range(100):                                                  # Einstellungen geladen (sonst kennt die App
        if page.evaluate("!!(S.boot && S.boot.tutorial_auto)"):           # «Einführung automatisch» noch nicht – CI)
            break
        page.wait_for_timeout(100)
    page.click('[data-act="settings"]')
    page.fill("#devname", "XCover 9")
    page.press("#devname", "Enter")                                       # kein Speichern-Knopf: Enter übernimmt
    page.wait_for_selector("#tour .tour-bubble")
    seen = set()
    for _ in range(40):
        bubble = page.locator(".tour-bubble")
        assert bubble.is_visible()
        bb = bubble.bounding_box()
        assert bb["x"] >= 0 and bb["x"] + bb["width"] <= 412 and bb["y"] >= 0 and bb["y"] + bb["height"] <= 900
        seen.add(page.evaluate("S.view"))
        page.mouse.click(5, 5)                                           # Klicks neben die Blase tun nichts
        if page.locator('.tour-nav [data-act="tour-end"]').count():
            page.click('.tour-nav [data-act="tour-end"]')
            break
        page.click('[data-act="tour-next"]')
    page.wait_for_function("() => !document.getElementById('tour')")
    assert seen == {"table", "order", "pay"}
    assert page.evaluate("S.view") == "table" and page.evaluate("cartCount()") == 0
    assert page.evaluate("localStorage.getItem('festpos.outbox')") in (None, "[]")
    assert c.get("/api/admin/stats", auth=AUTH).json()["orders"] == before["orders"]
    # Zurück-Taste beendet die Einführung
    page.click('[data-act="settings"]')
    page.click('[data-act="tour-start"]')
    page.wait_for_selector("#tour")
    page.go_back()
    page.wait_for_function("() => !document.getElementById('tour')")
    # automatisch nur, wenn eingestellt
    c.put("/api/admin/settings", auth=AUTH, json={"tutorial_auto": False})
    page.evaluate("loadBoot()")
    page.wait_for_timeout(300)
    page.click('[data-act="settings"]')
    page.fill("#devname", "XCover 10")
    page.mouse.click(200, 60)                                            # daneben tippen: schliesst und übernimmt
    page.wait_for_timeout(600)
    assert page.locator("#tour").count() == 0
    assert page.locator(".sheet").count() == 0 and page.evaluate("S.device") == "XCover 10"
    c.put("/api/admin/settings", auth=AUTH, json={"tutorial_auto": True})
    assert not errs, errs
    ctx.close()


def test_settings_short_with_advanced(server, browser):
    base, c = server
    # ohne automatische Einführung (sie startet sonst nach dem neuen Namen)
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468", "tutorial_auto": False})
    for width in (412, 360):
        ctx = browser.new_context(viewport={"width": width, "height": 892})
        ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('XCover 3'))")
        page = ctx.new_page()
        errs = _watch(page)
        page.goto(base + "/index.html")
        page.wait_for_selector('[data-act="digit"]')
        from tests.test_browser_receipt import _until                # (dort wird diese Datei importiert)
        _until(page, "S.boot?.test?.enabled === true")              # Testmodus bekannt (die Tischwahl steht vorher)
        # Erstes Fenster: nur Name, Einführung, Testmodus und «Erweiterte Einstellungen» – kein Speichern
        page.click('[data-act="settings"]')
        acts = page.eval_on_selector_all(".sheet [data-act]", "els => els.map((e) => e.dataset.act)")
        assert acts == ["tour-start", "test-start", "settings-more"]
        assert page.locator("#devname").count() == 1 and page.locator(".sheet .go").count() == 0
        assert page.locator(".sheet .seg, #devlang, .navlinks").count() == 0
        # Name ändern und direkt weiter: gilt ohne Speichern
        page.fill("#devname", "XCover 4")
        page.click('[data-act="settings-more"]')
        assert page.inner_text(".sheet h2") == "Erweiterte Einstellungen"
        assert page.evaluate("S.device") == "XCover 4" and page.inner_text(".top .who") == "XCover 4"
        assert page.locator('.sheet [data-act="reload-boot"]').count() == 0                  # nur noch ein Knopf
        for sel in ('[data-act="theme"]', '[data-act="wake"]', '[data-act="app-reload"]',
                    '.navlinks a[href="/admin.html"]', '.navlinks a[href="/status.html"]'):
            assert page.locator(".sheet " + sel).count() >= 1, sel
        # Darstellung wirkt sofort, das Fenster bleibt offen
        page.click('[data-act="theme"][data-v="dark"]')
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        assert page.inner_text(".sheet h2") == "Erweiterte Einstellungen"
        page.click('[data-act="theme"][data-v="auto"]')
        # alles passt in die Breite
        assert page.evaluate("[...document.querySelectorAll('.sheet *')].every((e) => e.getBoundingClientRect().right <= innerWidth + 0.5)")
        # Zurück: wieder das erste Fenster
        page.click('.sheet [data-act="settings"]')
        assert page.locator("#devname").input_value() == "XCover 4"
        page.evaluate("closeSheet()")
        assert not errs, errs
        ctx.close()
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": False, "tutorial_auto": True})


# ------------------------------------------------------------------ Pseudo-Sprache: kein fester Text
@pytest.fixture(scope="module")
def pseudo_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pseudo")
    write_pseudo(tmp / "i18n", "qps")

    def prepare(c):
        r = c.put("/api/admin/settings", auth=AUTH, json={"language": "qps"})     # Standardsprache: alles pseudo
        assert r.status_code == 200, r.text
    proc, base, c = _start(tmp, {"FESTPOS_I18N_DIR": str(tmp / "i18n")}, prepare)
    kaffee = c.post("/api/admin/products", auth=AUTH, json={"name": "Kaffee", "price_cents": 350, "category_id": None}).json()["id"]
    c.put(f"/api/admin/products/{kaffee}/options", auth=AUTH,
          json={"groups": [{"name": "Zucker", "choices": ["ohne", "mit"], "required": True}], "free": True})
    c.put(f"/api/admin/products/{kaffee}/delivery", auth=AUTH, json={"enabled": True, "allowed": ["Pause", "Schluss"]})
    c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1357", "test_print": True})
    c.put("/api/admin/settings", auth=AUTH, json={"payment_mode": "optional"})          # Zahlart in der Kasse
    c.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": ""})             # MWST-Spalten
    yield base, c
    proc.terminate()
    proc.wait(10)


# Daten aus der Datenbank (Produkte, Kategorien, Drucker, Anlass, Lieferzeiten …) und Eigennamen
DATA = {"Bier", "Bratwurst", "Mineral", "Kaffee", "Zucker", "ohne", "mit", "Getränke", "Essen", "Getränkeausgabe", "Küche",
        "Vereinsfest", "CHF", "FestPOS", "Sofort", "Pause", "Schluss", "Deutsch", "Pseudo", "Pseudohandy", "Switch",
        "Access", "Point", "XCover", "Vera", "admin",
        "MikroTik", "RouterOS", "lte",                                            # Router-Modell, Schnittstelle «lte1»
        "English", "Français", "Italiano", "Rumantsch", "Vallader"}                 # Namen der Sprachen (Auswahl)

_COLLECT = """() => {
  const body = document.body.cloneNode(true);
  body.querySelectorAll("script, style, noscript, [translate=no]").forEach((e) => e.remove());   // Daten (Protokoll)
  const attrs = [];
  document.querySelectorAll("[placeholder], [title], [aria-label], [alt]").forEach((e) => {
    for (const a of ["placeholder", "title", "aria-label", "alt"]) { const v = e.getAttribute(a); if (v) attrs.push(v); }
  });
  const parts = [], walk = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) parts.push(walk.currentNode.nodeValue);       // Textknoten einzeln (sonst kleben Wörter)
  return { text: parts.join(" ") + " " + document.title, attrs };
}"""


def _check(page, where: str, problems: list[str]):
    got = page.evaluate(_COLLECT)
    left = leftover_words(got["text"], DATA)
    if left:
        problems.append(f"{where}: {left}")
    for a in got["attrs"]:
        bad = leftover_words(a, DATA)
        if bad:
            problems.append(f"{where} (Attribut «{a}»): {bad}")


def test_every_visible_text_comes_from_language_file(pseudo_server, browser):
    base, c = pseudo_server
    problems: list[str] = []
    # ---- Bestell-App
    ctx, page, errs = _phone(browser, base, "Pseudohandy")
    page.wait_for_function("() => document.querySelector('header h1').textContent.includes('⟦')")
    _check(page, "Tischwahl", problems)
    page.click('[data-act="settings"]')
    _check(page, "Einstellungen", problems)
    page.click('[data-act="test-start"]')
    _check(page, "Test-PIN", problems)
    _enter_pin(page, "tpin", "1357")
    page.wait_for_selector("body.testmode")
    _open_table(page, "5")
    _check(page, "Bestellen (Testmodus)", problems)
    page.click('[data-act="cat"][data-id="none"]')
    page.click(".grid .tile >> nth=0")                                  # Kaffee: Pflicht-Option -> Fenster
    page.wait_for_selector(".sheet .optg")
    _check(page, "Optionen", problems)
    page.click('[data-act="opt-pick"] >> nth=0')
    page.click('[data-act="opt-when"] >> nth=0')
    page.click('[data-act="opt-ok"]')
    page.click('[data-act="cart"]')
    _check(page, "Warenkorb", problems)
    page.click('.sheet [data-act="send"]')
    page.wait_for_selector('#paystate[data-s="sent"]', timeout=10000)
    _check(page, "Kasse Rückgeld", problems)
    for mode in ("split", "part"):
        page.click(f'[data-act="mode"][data-m="{mode}"]')
        _check(page, f"Kasse {mode}", problems)
    page.click('[data-act="pick"] >> nth=0')
    _check(page, "Kasse Einzeln gewählt", problems)
    page.click('[data-act="ez-next"]')
    _check(page, "Kasse Einzeln kassieren", problems)
    page.click('[data-act="back"]')                                    # Zurück-Pfeil, gewählt: Rückfrage
    page.wait_for_selector('.sheet [data-act="back"]')
    _check(page, "Rückfrage Einzeln", problems)
    page.click('.sheet [data-act="close-sheet"]')
    page.click('[data-act="ez-back"]')
    page.click('[data-act="mode"][data-m="change"]')
    page.click("#stchip")
    _check(page, "Status", problems)
    page.evaluate("closeSheet()")
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    page.click('[data-act="history"]')
    _check(page, "Verlauf", problems)
    page.go_back()
    page.click('.testbar [data-act="test-exit"]')
    page.click('[data-act="settings"]')
    page.click('[data-act="tour-start"]')
    for i in range(40):
        page.wait_for_selector(".tour-bubble")
        _check(page, f"Einführung {i + 1}", problems)
        if page.locator('.tour-choice').count():                         # Testmodus erlaubt: letzter Schritt fragt (T7)
            page.click('[data-act="tour-test"][data-v="0"]')
            break
        if page.locator('.tour-nav [data-act="tour-end"]').count():
            page.click('.tour-nav [data-act="tour-end"]')
            break
        page.click('[data-act="tour-next"]')
    c.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": True, "pin": "2580"})
    page.evaluate("loadBoot()")
    page.wait_for_selector("#lock")
    _check(page, "PIN-Sperre", problems)
    c.put("/api/admin/settings", auth=AUTH, json={"pin_enabled": False})
    assert not errs, errs
    ctx.close()
    # ---- Verwaltung
    c.post("/api/admin/users", auth=AUTH, json={"name": "Vera", "role": "view", "password": "geheim123"})
    for n in c.get("/api/admin/system", auth=AUTH).json()["nodes"]:     # Modell/Betriebssystem dieses Rechners sind Daten
        for v in (n["info"].get(k) for k in ("model", "os", "kernel", "version")):
            DATA.update(re.findall(r"[^\W\d_]{2,}", str(v or "")))
    ctx = browser.new_context(viewport={"width": 1300, "height": 900})
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/admin.html")
    page.wait_for_selector("#pw")
    _check(page, "Anmeldung", problems)
    page.fill("#pw", "geheim")
    page.press("#pw", "Enter")
    page.wait_for_selector(".side")
    # eine echte Bestellung mit Storno: Bestellungen, Tagesabschluss (Kassensturz) und Protokoll haben Inhalt
    pid = c.get("/api/bootstrap").json()["products"][0]["id"]
    oid = c.post("/api/orders", json={"client_uuid": "pseudo-0001", "table": "5", "device": "Pseudohandy", "payment": "card",
                                      "lines": [{"product_id": pid, "qty": 2}]}).json()["order_id"]
    c.post(f"/api/admin/orders/{oid}/storno", auth=AUTH, json={"client_uuid": "pseudo-s-0001", "reason": "wrong",
                                                                "lines": [{"line_no": 1, "qty": 1}]})
    for pg in ("overview", "orders", "jobs", "closing", "audit", "products", "tables", "pos", "devices", "users", "maint"):
        page.click(f'.side button[data-page="{pg}"]')
        page.wait_for_timeout(900)
        _check(page, f"Verwaltung {pg}", problems)
        for i in range(page.locator(".tabs [data-tab]").count()):             # jeder Reiter (Karten ein-/ausgeblendet)
            page.locator(".tabs [data-tab]").nth(i).click()
            page.wait_for_timeout(400)                                        # T34: manche Reiter zeichnen neu
            _check(page, f"Verwaltung {pg} Reiter {i + 1}", problems)
        if pg == "products":
            page.click('.tabs [data-tab="products"]')
        if pg == "pos":                                                       # T28: Karten der Kasse aufklappen
            page.click('.tabs [data-tab="kasse"]')
            page.wait_for_selector("#kpage .k-card")
            for i in range(page.locator("#kpage .k-card > summary").count()):
                page.locator("#kpage .k-card > summary").nth(i).click()
            _check(page, "Verwaltung Kasse aufgeklappt", problems)
        if pg == "devices":                                                   # T5: «Gerät hinzufügen», Art und Angaben
            page.click('.tabs [data-tab="list"]')
            page.click("#dev-add")
            _check(page, "Gerät hinzufügen", problems)
            page.click("#wiz-next")
            page.wait_for_selector("#wiz-data")
            _check(page, "Gerät hinzufügen › Angaben", problems)
            page.click("#wiz-cancel")
            page.wait_for_selector("#dev-add")
            page.click('.tabs [data-tab="pi"]')                              # T34: Geräte & Netz › Pi
            page.wait_for_selector('[data-power="reboot"][data-node="all"]')
            for action in ("reboot", "poweroff"):
                page.click(f'[data-power="{action}"][data-node="all"]')
                page.wait_for_selector("dialog[open] #powerform")
                _check(page, f"Dialog {action}", problems)
                page.keyboard.press("Escape")
                page.wait_for_selector("dialog[open]", state="detached")
        if pg == "orders":
            page.click(f'[data-order="{oid}"]')
            page.wait_for_selector("dialog[open] #ordform")
            _check(page, "Dialog Bestellung", problems)
            page.keyboard.press("Escape")
            page.click('[data-oscope="all"]')
            page.wait_for_timeout(300)
            _check(page, "Verwaltung Bestellungen (alle)", problems)
        if pg == "jobs":
            page.click('.seg button[data-f="all"]')
            page.wait_for_timeout(300)
            _check(page, "Verwaltung Druckaufträge (alle)", problems)
        if pg == "products":
            page.click("#ptable [data-edit] >> nth=0")                    # T28 Stufe 2: Bearbeiten im Fenster
            _check(page, "Dialog Produkt", problems)
            page.click('dialog[open] [data-more="opts"]')
            _check(page, "Dialog Optionen", problems)
            page.keyboard.press("Escape")
            page.click("#ptable [data-edit] >> nth=0")
            page.click('dialog[open] [data-more="deliv"]')
            _check(page, "Dialog Lieferzeit", problems)
            page.keyboard.press("Escape")
    # ---- Statusanzeige (aus der Verwaltung geöffnet)
    with page.expect_popup() as pop:
        page.click('a[href="/status.html"]')
    st = pop.value
    st.wait_for_function("() => document.getElementById('state').textContent.includes('⟦') && document.querySelector('#printers .printer')",
                         timeout=10000)
    _check(st, "Statusanzeige", problems)
    st.click("[data-pi] >> nth=0")                                       # Pi antippen: Details, Knöpfe, Rückfrage
    st.wait_for_selector("#detail-sheet .panel")
    _check(st, "Details Pi", problems)
    st.click('#detail-sheet [data-act="go"] >> nth=0')
    _check(st, "Details Pi Rückfrage", problems)
    st.click('#detail-sheet [data-act="no"]')
    st.click('#detail-sheet [data-act="close"]')
    if st.locator("[data-dev]").count():
        st.click("[data-dev] >> nth=0")
        st.wait_for_selector("#detail-sheet .panel")
        _check(st, "Details Handy", problems)
        st.keyboard.press("Escape")
    assert not errs, errs
    ctx.close()
    assert not problems, "Fester Text (nicht aus der Sprachdatei):\n" + "\n".join(problems)
