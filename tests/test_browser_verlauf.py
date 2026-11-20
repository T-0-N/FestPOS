"""Der Verlauf auf dem Handy fragt den Pi, was aus seinen Bestellungen geworden ist (Tagesabschluss,
«Bestellungen löschen», Übungsbestellungen gelöscht, ganz storniert) und zeigt es an. Abgeschlossenes und Gelöschtes
nur noch ansehen; gelöschte Übungsbestellungen verschwinden gleich, echte nach einem Tag. Offline bleibt alles.
Wird übersprungen ohne Chromium."""
from __future__ import annotations

import json
import time
import uuid

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402

PHONE = {"X-Test-Cert": "XCover 8", "X-Test-Client": "192.168.55.108"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("verlauf"), target="tests.asgi_testphone:app")
    yield base, c
    proc.terminate()
    proc.wait(10)


def _order(c, pid, test=False) -> tuple[str, int]:
    cu = str(uuid.uuid4())
    r = c.post("/api/orders", headers=PHONE, json={"client_uuid": cu, "table": "7", "device": "XCover 8", "test": test,
                                                   "lines": [{"product_id": pid, "qty": 2}]})
    assert r.status_code == 200, r.text
    return cu, r.json()["order_id"]


def _entry(cu, no, test=False, **extra) -> dict:
    return {"id": cu, "table": "7", "items": [{"id": 1, "pid": 1, "name": "Bier", "opt": "", "price": 650, "qty": 2}],
            "total": 1300, "ts": int(time.time() * 1000), "status": "sent", "order_id": no, "test": test or None, **extra}


def test_history_shows_what_became_of_the_orders(server, browser):  # noqa: F811
    base, c = server
    assert c.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "2468"}).status_code == 200
    pid = c.get("/api/bootstrap", headers=PHONE).json()["products"][0]["id"]
    closed, n1 = _order(c, pid)
    assert c.post("/api/admin/closings", auth=AUTH, json={}).status_code == 200     # n1 jetzt im Tagesabschluss
    still, n2 = _order(c, pid)
    practice, n3 = _order(c, pid, test=True)
    assert c.post("/api/admin/test-orders/delete?confirm=JA", auth=AUTH).status_code == 200
    old = int(time.time() * 1000) - 2 * 86400 * 1000
    hist = [_entry(still, n2), _entry(closed, n1), _entry(practice, n3, test=True),
            _entry(str(uuid.uuid4()), 99, goneAt=None), _entry(str(uuid.uuid4()), 98, goneAt=old)]
    hist[3].pop("goneAt")
    gone = hist[3]["id"]
    ctx = browser.new_context(viewport={"width": 360, "height": 892}, extra_http_headers=PHONE)
    ctx.add_init_script(f"if (!sessionStorage.getItem('seeded')) {{ sessionStorage.setItem('seeded', '1');"
                        f" localStorage.setItem('festpos.device', JSON.stringify('XCover 8'));"
                        f" localStorage.setItem('festpos.history', {json.dumps(json.dumps(hist))}); }}")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    page.click('[data-act="history"]')
    _until(page, "document.querySelectorAll('.hist [data-old]').length === 2")
    ids = page.evaluate("[...document.querySelectorAll('.hist [data-act=\"open-pay\"]')].map((b) => b.dataset.id)")
    assert ids == [still, closed, gone]                    # Übung gelöscht: gleich weg; gelöscht vor 2 Tagen: weg
    txt = lambda i: page.inner_text(f'.hist [data-id="{i}"]')  # noqa: E731
    assert "abgeschlossen" in txt(closed) and "gelöscht" in txt(gone)
    assert "abgeschlossen" not in txt(still) and "gelöscht" not in txt(still)
    stored = {h["id"]: h for h in page.evaluate("JSON.parse(localStorage.getItem('festpos.history'))")}
    assert stored[gone]["srv"] == "gone" and stored[gone]["goneAt"] > 0 and stored[closed]["srv"] == "closed"
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    # abgeschlossen: nur ansehen (keine Kasse)
    page.click(f'.hist [data-id="{closed}"]')
    page.wait_for_selector(".sheet .bon")
    assert "Tagesabschluss" in page.inner_text(".sheet") and "13.00" in page.inner_text(".sheet")
    assert page.evaluate("S.view") == "history"
    page.click('.sheet [data-act="close-sheet"]')
    # noch offen: die Kasse wie bisher
    page.click(f'.hist [data-id="{still}"]')
    _until(page, "S.view === 'pay'")
    page.go_back()
    _until(page, "S.view === 'history'")
    # inzwischen ganz storniert (Verwaltung): beim nächsten Öffnen des Verlaufs angezeigt
    body = {"client_uuid": str(uuid.uuid4()), "reason": "wrong", "note": ""}
    assert c.post(f"/api/admin/orders/{n2}/storno", auth=AUTH, json=body).status_code == 200
    page.go_back()
    page.click('[data-act="history"]')
    _until(page, f"document.querySelector('.hist [data-id=\"{still}\"] .stornotag') !== null")
    # CodeRabbit zu PR 81: gelöscht wird nur, was DIESE Antwort als gelöscht bestätigt – eine Abfrage für einen
    # anderen Eintrag lässt einen früher (vor 2 Tagen) als gelöscht gemerkten stehen, die nächste ganze Abfrage nicht
    page.evaluate(f"history_.push({{...history_[0], id: 'alt-geloescht-1', srv: 'gone', goneAt: {old}}})")
    page.evaluate(f"checkHistory(['{still}'])")
    _until(page, "HIST.busy === null && history_.some((h) => h.id === 'alt-geloescht-1')")
    page.evaluate("checkHistory()")
    _until(page, "!history_.some((h) => h.id === 'alt-geloescht-1')")
    # CodeRabbit zu PR 81: die Abfragen laufen nacheinander, nie gleichzeitig – die zweite beginnt erst, wenn die
    # erste beantwortet ist; so liest der Pi in derselben Reihenfolge, in der die Antworten verarbeitet werden
    page.evaluate("window.__api = api; window.__q = [];"
                  " api = (p, o) => (p.includes('/api/orders/status') ? new Promise((ok) => window.__q.push(ok))"
                  " : window.__api(p, o)); 0")
    page.evaluate(f"window.__both = Promise.all([checkHistory(), checkHistory(['{still}'])]); 0")
    _until(page, "window.__q.length === 1")
    time.sleep(0.2)
    assert page.evaluate("window.__q.length") == 1                 # die zweite wartet
    page.evaluate(f"window.__q.shift()(new Response(JSON.stringify({{states: {{'{still}': 'open'}}}}))); 0")
    _until(page, "window.__q.length === 1")                        # erst jetzt die zweite
    page.evaluate(f"window.__q.shift()(new Response(JSON.stringify({{states: {{'{still}': 'closed'}}}}))); 0")
    page.evaluate("window.__both")
    assert page.evaluate(f"history_.find((h) => h.id === '{still}').srv") == "closed"
    # hängt die Antwort (Kopfzeilen da, Inhalt nie): Zeitgrenze, die Warteschlange läuft danach weiter
    # (nur die gezielte Abfrage – die Abfrage 3 s nach dem Start der App darf normal laufen)
    page.evaluate("api = (p, o) => (p.includes('/api/orders/status') && JSON.parse(o.body).uuids.length === 1"
                  " ? Promise.resolve(new Response(new ReadableStream({ start() {} }))) : window.__api(p, o)); 0")
    assert page.evaluate(f"checkHistory(['{still}'])") is False
    page.evaluate("api = window.__api; 0")
    assert page.evaluate(f"checkHistory(['{still}'])") is True
    page.evaluate(f"history_.find((h) => h.id === '{still}').srv = 'storno'; 0")
    # verspätete Antwort beim Antippen: wer den Verlauf inzwischen verlassen hat – auch wenn er ihn vor der Antwort
    # wieder öffnet –, bekommt die Kasse nicht hinterher (CodeRabbit zu PR 81). Antwort zurückhalten, dann freigeben.
    page.evaluate("window.__api = api; window.__hold = new Promise((ok) => { window.__go = ok; });"
                  " api = (p, o) => (p.includes('/api/orders/status') ? window.__hold.then(() => window.__api(p, o))"
                  " : window.__api(p, o)); window.__ohe = openHistEntry;"
                  " openHistEntry = (id) => (window.__pend = window.__ohe(id)); 0")
    page.evaluate("history_[0].srv = 'open'")
    page.click(f'.hist [data-id="{still}"]')
    page.go_back()
    _until(page, "S.view === 'table'")
    page.click('[data-act="history"]')                             # vor der Antwort wieder im Verlauf
    _until(page, "S.view === 'history'")
    page.evaluate("window.__go(); 0")
    page.evaluate("window.__pend")                                 # wartet, bis das Antippen fertig ist
    assert page.evaluate("S.view") == "history" and not page.locator(".sheet").count()
    # dasselbe mit der Einführung dazwischen: sie wechselt die Ansicht ohne render() und stellt danach den Verlauf her
    page.evaluate("window.__hold = new Promise((ok) => { window.__go = ok; }); 0")
    page.click(f'.hist [data-id="{still}"]')
    page.evaluate("startTour(); endTour(); 0")
    _until(page, "S.view === 'history' && !S.tour")
    page.evaluate("window.__go(); 0")
    page.evaluate("window.__pend")
    assert page.evaluate("S.view") == "history" and not page.locator(".sheet").count()
    _until(page, "HIST.busy === null")                            # auch die Abfrage beim Öffnen des Verlaufs fertig
    page.evaluate("api = window.__api; openHistEntry = window.__ohe; 0")
    # offline: nichts verschwindet, Hinweis «nicht geprüft»
    page.route("**/api/orders/status", lambda r: r.abort())
    page.go_back()
    page.click('[data-act="history"]')
    _until(page, "!!document.querySelector('.hist-note')")
    assert "nicht geprüft" in page.inner_text(".hist-note")
    assert page.locator('.hist [data-act="open-pay"]').count() == 3
    assert not [e for e in errs if "net::ERR_FAILED" not in e], errs
    ctx.close()
