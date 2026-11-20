"""Offene Tische am Handy (static/app/tabs.js): nach «Senden» bleibt der Tisch offen, Nachfrage beim Wählen eines offenen
Tisches, Abrechnen mit der Kasse (Rückgeld, Einzeln) über alle Bestellungen, Rechte, Stand im Tischplan.
Wird übersprungen, wenn Playwright oder Chromium fehlt."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_browser_p4 import AUTH, _open_table, _start, _watch, browser  # noqa: E402,F401  (browser = Fixture)
from tests.test_browser_receipt import _until  # noqa: E402


def _headers(name: str) -> dict:
    return {"X-Test-Cert": name, "X-Test-Client": "192.168.55.101"}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    proc, base, c = _start(tmp_path_factory.mktemp("tabsui"), target="tests.asgi_testphone:app")
    c.put("/api/admin/settings", auth=AUTH, json={"tab_mode": "open", "tab_who": "own", "payment_mode": "optional"})
    yield base, c
    proc.terminate()
    proc.wait(10)


def _phone(browser, base, cert="XCover 1", name="Anna", width=412):  # noqa: F811
    ctx = browser.new_context(viewport={"width": width, "height": 892}, extra_http_headers=_headers(cert), has_touch=True)
    ctx.add_init_script(f"localStorage.setItem('festpos.device', JSON.stringify({name!r}))")
    page = ctx.new_page()
    errs = _watch(page)
    page.goto(base + "/index.html")
    page.wait_for_selector('[data-act="digit"]')
    return ctx, page, errs


def _send(page, tiles=(0,)):
    for i in tiles:
        page.locator(".grid .tile").nth(i).click()
    page.click("#send")
    page.wait_for_selector("#tabopen")
    page.wait_for_timeout(700)                  # Doppeltipp-Schutz der Kasse (REV-047)


def _open_tabs(c) -> dict:
    return {t["table"]: t for t in c.get("/api/admin/tabs", auth=AUTH).json()["tabs"]}


def _type_table(page, n: str):
    for d in n:
        page.click(f'[data-act="digit"][data-d="{d}"]')
    page.click('[data-act="table-ok"]')


def test_table_stays_open_then_settle_whole(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "12")
    _send(page, (0, 0))
    assert page.locator("#payfoot .go").inner_text().strip() == "Nächste Bestellung"
    assert page.locator(".calc").count() == 0                          # keine Rückgeld-Rechnung: bleibt offen
    _until(page, "document.querySelector('#tabopen b') !== null")      # offener Betrag vom Server
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    # nochmals Tisch 12: die App fragt (weiter bestellen / abrechnen)
    _type_table(page, "12")
    page.wait_for_selector('.sheet [data-act="tab-order"]')
    assert page.locator('.sheet [data-act="tab-pay"]').count() == 1
    page.click('.sheet [data-act="tab-order"]')
    page.wait_for_selector(".grid .tile")
    _send(page, (1,))
    t12 = _open_tabs(c)["12"]
    assert t12["orders"] == 2
    # Jetzt abrechnen: Kasse über beide Bestellungen
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    page.wait_for_timeout(700)
    assert "2 Bestellungen" in page.inner_text("#paystate")
    assert page.inner_text("#bon .total b").replace("'", "") == f"{t12['net'] / 100:.2f}"
    page.click('[data-act="pay-kind"][data-v="card"]')
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    assert "12" not in _open_tabs(c)
    s = c.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["by_payment"].get("card", 0) >= t12["net"]
    assert errs == [], errs
    ctx.close()


def test_settle_per_person_and_rights(server, browser):  # noqa: F811
    base, c = server
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "5")
    _send(page, (0, 1))
    # anderes Handy ohne Recht (Einstellung «nur das eigene Handy»): bestellen ja, abrechnen nein
    ctx2, page2, errs2 = _phone(browser, base, cert="XCover 2", name="Luca")
    _type_table(page2, "5")
    page2.wait_for_selector('.sheet [data-act="tab-order"]')
    assert page2.locator('.sheet [data-act="tab-pay"]').count() == 0
    assert "Anna" in page2.inner_text(".sheet") or "XCover 1" in page2.inner_text(".sheet")
    ctx2.close()
    # Einzeln: Person 1 zahlt eine Position, Rest beim Schliessen
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    page.click('[data-act="mode"][data-m="part"]')
    page.locator('[data-act="pick"]').first.click()
    page.click('[data-act="ez-next"]')
    page.click('[data-act="part-pay"]')
    tid = _open_tabs(c)["5"]["id"]
    _until(page, "true")
    for _ in range(30):
        d = c.get(f"/api/admin/tabs/{tid}", auth=AUTH).json()
        if d["payments"]:
            break
        page.wait_for_timeout(100)
    assert len(d["payments"]) == 1 and d["payments"][0]["part"] == 1 and d["paid"] > 0
    page.click('[data-act="mode"][data-m="change"]')
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    d = c.get(f"/api/admin/tabs/{tid}", auth=AUTH).json()
    assert d["closed_at"] and d["open"] == 0 and len(d["payments"]) == 2
    assert errs == [], errs
    ctx.close()


def test_plan_shows_open_tables(server, browser):  # noqa: F811
    base, c = server
    p = c.post("/api/admin/plan/areas", auth=AUTH, json={"name": "Saal"}).json()
    aid, ids = p["areas"][0]["id"], {x["label"]: x["id"] for x in p["tables"]}
    c.put(f"/api/admin/plan/areas/{aid}", auth=AUTH, json={"tables": [
        {"id": ids["8"], "x": 100, "y": 100, "w": 120, "h": 80}, {"id": ids["9"], "x": 500, "y": 600, "w": 120, "h": 80}]})
    ctx2, page2, _ = _phone(browser, base, cert="XCover 2", name="Luca")
    page2.click('[data-act="table-view"][data-v="number"]')
    _open_table(page2, "9")
    _send(page2, (0,))
    ctx2.close()
    ctx = browser.new_context(viewport={"width": 412, "height": 892}, extra_http_headers=_headers("XCover 1"), has_touch=True)
    ctx.add_init_script("localStorage.setItem('festpos.device', JSON.stringify('Anna'))")
    page = ctx.new_page()
    page.goto(base + "/index.html")
    page.wait_for_selector("#plwrap .pl-t")
    _until(page, "document.querySelector('[data-t=\"9\"]').classList.contains('other')")
    assert page.locator(".pl-legend").count() == 1
    assert "Luca" in page.inner_text('[data-t="9"] small') or "XCover 2" in page.inner_text('[data-t="9"] small')
    ctx.close()
    c.delete(f"/api/admin/plan/areas/{aid}", auth=AUTH)


def test_admin_open_tables_page_settles(server, browser):  # noqa: F811
    base, c = server
    ctx, page, _ = _phone(browser, base, cert="XCover 3", name="Mia")
    _open_table(page, "17")
    _send(page, (0,))
    ctx.close()
    actx = browser.new_context(viewport={"width": 1300, "height": 900})
    a = actx.new_page()
    errs = _watch(a)
    a.goto(base + "/admin.html")
    a.fill("#user", "admin")
    a.fill("#pw", "geheim")
    a.press("#pw", "Enter")
    a.wait_for_selector('.side button[data-page="opentabs"]')       # «Tisch offen lassen» ist eingeschaltet
    a.click('.side button[data-page="opentabs"]')
    a.wait_for_selector("[data-tab-settle]")
    row = a.locator("xpath=//tr[td[1]/b[normalize-space()='17']]")    # nur die Tischnummer (nicht «seit 17:05»)
    row.locator("[data-tab-settle]").click()
    a.select_option("dialog select[name=pay]", "twint")
    a.click("dialog button.pri")
    _until(a, "!document.querySelector('dialog')")
    assert "17" not in _open_tabs(c)
    assert errs == [], errs
    actx.close()


def test_receipt_when_settling_table(server, browser):  # noqa: F811
    base, c = server
    pid = c.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    c.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": pid})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "33")
    _send(page, (0,))
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector("#rcptbtn")
    page.wait_for_timeout(700)
    page.click("#rcptbtn")
    _until(page, "document.querySelector('#rcptbtn small')?.textContent === 'Kopie'")
    tid = next(t["id"] for t in c.get("/api/admin/tabs", auth=AUTH).json()["tabs"] if t["table"] == "33")
    oid = c.get(f"/api/admin/tabs/{tid}", auth=AUTH).json()["orders_list"][0]["id"]
    recs = c.get(f"/api/admin/orders/{oid}", auth=AUTH).json()["receipts"]
    assert len(recs) == 1 and recs[0]["tab"] and recs[0]["no"]
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    assert errs == [], errs
    ctx.close()
    c.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": 0})


@pytest.mark.parametrize("width", [412, 360])
def test_open_tables_strip_and_list_without_plan(server, browser, width):  # noqa: F811
    """T35: ohne Tischplan stehen die offenen Tische in einer Leiste über dem grünen Knopf (links/rechts
    scrollbar, eigene zuerst und grün, mit Betrag); «Alle ›» zeigt die Liste; Antippen fragt wie beim Eintippen."""
    base, c = server
    ctx, page, errs = _phone(browser, base)
    page.set_viewport_size({"width": width, "height": 892})
    for tbl in ("21", "22"):
        _open_table(page, tbl)
        _send(page, (0,))
        page.click('[data-act="done"]')
        page.wait_for_selector('[data-act="digit"]')
    _until(page, "document.querySelectorAll('#tabstrip .ts').length >= 2")
    chips = [" ".join(x.split()) for x in page.locator("#tabstrip .ts").all_inner_texts()]
    assert any(x.startswith("21 ") for x in chips) and any(x.startswith("22 ") for x in chips), chips
    assert page.locator("#tabstrip .ts.mine").count() >= 2
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    go = page.locator('[data-act="table-ok"]').bounding_box()
    assert go["y"] + go["height"] <= 892                                    # grüner Knopf bleibt sichtbar
    page.click('#tabstrip [data-act="tabs-all"]')
    page.wait_for_selector(".sheet .tab-list .tl-row")
    assert "Offene Tische" in page.inner_text(".sheet h2")
    page.locator(".sheet .tl-row", has_text="Tisch 22").click()
    page.wait_for_selector('.sheet [data-act="tab-order"]')                  # dieselbe Nachfrage wie beim Eintippen
    assert "Tisch 22" in page.inner_text(".sheet h2")
    assert not errs, errs
    ctx.close()
    for t in _open_tabs(c).values():
        if t["table"] in ("21", "22"):
            c.post(f"/api/admin/tabs/{t['id']}/settle", auth=AUTH, json={"payment": "cash"})


def test_storno_when_settling_open_table(server, browser):  # noqa: F811
    """Abrechnen am offenen Tisch: «Stornieren …» mit Storno-Recht, über alle Bestellungen; der Bon zeigt das Storno,
    «Einzeln» bezahlte Stück lassen sich nicht stornieren."""
    base, c = server
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "41")
    _send(page, (0, 0))                                  # 2× Bier
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    _type_table(page, "41")
    page.click('.sheet [data-act="tab-order"]')
    page.wait_for_selector(".grid .tile")
    _send(page, (0, 1))                                  # 1× Bier, 1× Mineral
    assert page.locator('[data-act="storno"]').count() == 0          # «Tisch bleibt offen»: erst beim Abrechnen
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    page.wait_for_timeout(700)
    # Person 1 zahlt «Einzeln» 2 Bier
    page.click('[data-act="mode"][data-m="part"]')
    page.click('.pi[data-id="1"]')
    page.click('.pi[data-id="1"]')
    page.click('[data-act="ez-next"]')
    page.click('[data-act="part-pay"]')
    _until(page, "S.part.paid.length === 1 && S.tabPaidKeys.length === 1")
    page.click('[data-act="storno"]')
    page.wait_for_selector(".sheet [data-storno]")
    sheet = page.inner_text(".sheet")
    assert "Alle Bestellungen" in sheet and "2 schon bezahlt" in sheet
    page.click('.sheet [data-act="storno-all"]')                     # nur, was noch geht: 1 Bier, 1 Mineral
    page.click('.sheet [data-act="storno-reason"][data-v="wrong"]')
    assert "10.50" in page.inner_text('.sheet [data-act="storno-ok"]')
    page.click('.sheet [data-act="storno-ok"]')
    page.wait_for_selector(".sheet", state="detached")
    assert "Storniert" in page.inner_text(".toast")
    t41 = _open_tabs(c)["41"]
    assert t41["net"] == 1300 and t41["open"] == 0
    page.click('[data-act="mode"][data-m="change"]')
    bon = page.inner_text("#bon")
    assert "2× Bier 5dl" in bon and "1 storniert" in bon and page.locator("#bon .line.gone").count() == 1
    assert page.inner_text("#bon .total b") == "13.00"
    page.click('[data-act="done"]')                                  # alles bezahlt: Tisch schliessen
    page.wait_for_selector('[data-act="digit"]')
    assert "41" not in _open_tabs(c)
    assert not errs, errs
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    ctx.close()


def test_storno_sheet_takes_payments_of_other_handhelds(server, browser):  # noqa: F811
    """CodeRabbit PR 71: Öffnet die Kasse das Storno-Fenster, übernimmt sie auch die Zahlungen, die inzwischen ein
    anderes Handheld gebucht hat – der offene Betrag stimmt danach. Ganz Storniertes zeigt seine Variante."""
    base, c = server
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "storno"})
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "42")
    _send(page, (0, 0))                                  # 2× Bier 13.00
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    page.wait_for_timeout(700)
    tid = _open_tabs(c)["42"]["id"]
    r = c.post(f"/api/tabs/{tid}/pay", headers=_headers("XCover 1"), json={"key": "other-hh-000001", "amount_cents": 500})
    assert r.status_code == 200, r.text
    page.click('[data-act="storno"]')
    page.wait_for_selector(".sheet [data-storno]")
    _until(page, "S.part.paid.length === 1 && S.part.paid[0].amount === 500 && S.tabPaidKeys.length === 1")
    assert "8.00" in page.inner_text("#partsum")         # noch offen 13.00 − 5.00
    # CodeRabbit PR 72: eine Zahlung dieses Handys ist unterwegs (key, noch nicht bestätigt) mit Trinkgeld, die nächste
    # Person hat schon eins; ein älterer Stand vom Server kommt dazwischen – beide bleiben, Trinkgeld zieht mit
    got = page.evaluate("""(() => {
      const h = S.pay, mine = { amount: 300, items: {}, key: 'unterwegs-0001', own: true };
      h.paid.push(mine); S.part.paid = h.paid;
      h.tips = [{ k: 'p2', a: 100 }, { k: 'p3', a: 50 }];
      const d = { id: h.tab, net: 1300, items: [{ id: 1, pid: 1, name: 'Bier 5dl', opt: '', price: 650, qty: 2, cancelled: 0 }],
        payments: [{ key: 'anderes-0001', amount_cents: 200, items: {} }, { key: 'other-hh-000001', amount_cents: 500, items: {} }] };
      tabRefresh(d);
      return { keys: S.pay.paid.map((p) => p.key), tips: S.pay.tips.map((x) => x.k + ':' + x.a).sort(), same: S.part.paid === S.pay.paid };
    })()""")
    assert got["keys"] == ["anderes-0001", "other-hh-000001", "unterwegs-0001"] and got["same"]
    assert got["tips"] == ["p3:100", "p4:50"]
    # CodeRabbit PR 72: Antwort auf die Zahlung geht verloren, ein Abgleich hat sie inzwischen schon gesehen – kein
    # «undo» daraus, und der nächste Abgleich bringt sie zurück
    lost = page.evaluate("""(async () => {
      const h = S.pay, realPost = tabPost, sent = [];
      let key = null;
      h.paid.push({ amount: 100, items: {}, own: true });
      tabPost = async (tab, path, body) => {
        sent.push(path);
        if (path === 'pay') { key = body.key; S.tabPaidKeys.push(key); return null; }
        return realPost(tab, path, body);
      };
      try { await syncTabPaid(); } finally { tabPost = realPost; }
      const after = { gone: !h.paid.some((p) => p.key === key), kept: tabUndos(h.tab).includes(key) };
      tabRefresh({ id: h.tab, net: 1300, items: [{ id: 1, pid: 1, name: 'Bier 5dl', opt: '', price: 650, qty: 2, cancelled: 0 }],
        payments: [...h.paid.filter((p) => p.key).map((p) => ({ key: p.key, amount_cents: p.amount, items: p.items })),
          { key, amount_cents: 100, items: {} }] });
      return { ...after, back: S.pay.paid.some((p) => p.key === key), undo: sent.includes('undo') };
    })()""")
    assert lost == {"gone": True, "kept": False, "back": True, "undo": False}
    # CodeRabbit PR 72: während die Zahlung unterwegs ist, nimmt die Bedienung sie selbst zurück und die Antwort geht
    # verloren – die Rücknahme bleibt gemerkt: undo bei jedem Abgleich, und ein Stand vom Server bringt sie nicht zurück
    undo = page.evaluate("""(async () => {
      const h = S.pay, realPost = tabPost, sent = [];
      const mine = { amount: 100, items: {}, own: true };
      h.paid.push(mine);
      let key = null;
      tabPost = async (tab, path, body) => {
        sent.push(path);
        if (path === 'pay') { key = body.key; tabDropPaid(mine); return null; }   // Bedienung nimmt sie zurück
        return { ok: true };
      };
      try { await syncTabPaid(); await syncTabPaid(); } finally { tabPost = realPost; }
      tabRefresh({ id: h.tab, net: 1300, items: [{ id: 1, pid: 1, name: 'Bier 5dl', opt: '', price: 650, qty: 2, cancelled: 0 }],
        payments: [...h.paid.filter((p) => p.key).map((p) => ({ key: p.key, amount_cents: p.amount, items: p.items })),
          { key, amount_cents: 100, items: {} }] });
      return { sent, back: S.pay.paid.some((p) => p.key === key), kept: tabUndos(h.tab).includes(key) };
    })()""")
    assert undo == {"sent": ["pay", "undo"], "back": False, "kept": True}
    # CodeRabbit PR 72: zwei Abgleiche zugleich laufen nacheinander (undo nie vor der Zahlung, die noch unterwegs ist);
    # jede Anfrage geht an den Tisch der Zahlung, auch wenn inzwischen ein anderer Bildschirm offen ist
    order = page.evaluate("""(async () => {
      const h = S.pay, realPost = tabPost, log = [];
      const mine = { amount: 100, items: {}, own: true };
      h.paid.push(mine);
      tabPost = async (tab, path, body) => {
        const who = tab === h.tab ? 'eigener' : 'fremder';
        if (path === 'pay') {
          log.push(`pay-start@${who}:${body.key}`);
          await new Promise((ok) => setTimeout(ok, 300));
          log.push(`pay-ende@${who}:${body.key}`);
        } else log.push(`${path}@${who}:${body?.key || ''}`);
        return { ok: true };
      };
      try {
        const first = syncTabPaid();
        await new Promise((ok) => setTimeout(ok, 50));
        tabDropPaid(mine);                                    // Bedienung nimmt zurück, während die Zahlung unterwegs ist
        const second = syncTabPaid();
        const other = { ...h, tab: 999999, paid: [] };
        S.pay = other;                                        // inzwischen ein anderer Tisch offen
        const r = [await first, await second];
        S.pay = h;
        const done = log.indexOf(`pay-ende@eigener:${mine.key}`), undo = log.indexOf(`undo@eigener:${mine.key}`);
        return { log, r, undoAfterPay: done !== -1 && undo > done, foreign: log.some((x) => x.includes('fremder')) };
      } finally { tabPost = realPost; }
    })()""")
    assert order["undoAfterPay"] and not order["foreign"] and order["r"] == [True, True], order
    # CodeRabbit PR 72: kommt eine Rücknahme nicht an, schliesst «Bezahlt · Tisch schliessen» den Tisch nicht
    settle = page.evaluate("""(async () => {
      const h = S.pay, realPost = tabPost, sent = [];
      tabUndos(h.tab).push('undo-geht-nicht-01');
      tabPost = async (tab, path) => { sent.push(path); return path === 'undo' ? null : { ok: true }; };
      try { await settleTab(); } finally { tabPost = realPost; tabUndos(h.tab).splice(tabUndos(h.tab).indexOf('undo-geht-nicht-01'), 1); }
      return sent;
    })()""")
    assert "close" not in settle and "pay" not in settle, settle
    # CodeRabbit PR 72: während «Tisch schliessen» läuft, nimmt «rückgängig» nichts zurück
    blocked = page.evaluate("""(() => {
      S.tabSettling = true;
      const before = S.part.paid.length;
      S.part.paid.push({ amount: 50, items: {}, own: true, key: 'beim-schliessen-01' });
      S.pay.paid = S.part.paid;
      try {
        const b = document.createElement('button'); b.dataset.act = 'part-undo'; document.body.append(b); b.click(); b.remove();
        return { kept: S.part.paid.length === before + 1, undo: tabUndos(S.pay.tab).includes('beim-schliessen-01') };
      } finally { S.tabSettling = false; S.part.paid.pop(); S.pay.paid = S.part.paid; }
    })()""")
    assert blocked == {"kept": True, "undo": False}, blocked
    # CodeRabbit PR 72: ein Stand vom Server, angefragt bevor die Zahlung bestätigt wurde, lässt sie nicht kurz
    # verschwinden; ein neuerer Stand ohne sie (anderswo zurückgenommen) nimmt sie weg
    stale = page.evaluate("""(() => {
      const h = S.pay, key = 'spaet-bestaetigt-01';
      const asOf = Date.now() - 1000;                         // Stand vor der Bestätigung angefragt
      h.paid.push({ amount: 100, items: {}, own: true, key }); S.part.paid = h.paid;
      S.tabPaidKeys.push(key); (S.tabPaidAt ||= {})[key] = Date.now();
      const snap = { id: h.tab, net: 1300, items: [{ id: 1, pid: 1, name: 'Bier 5dl', opt: '', price: 650, qty: 2, cancelled: 0 }],
        payments: h.paid.filter((p) => p.key && p.key !== key).map((p) => ({ key: p.key, amount_cents: p.amount, items: p.items })) };
      tabRefresh(snap, asOf);
      const kept = h.paid.some((p) => p.key === key) && S.tabPaidKeys.includes(key);
      tabRefresh(snap, Date.now() + 1000);                    // neuerer Stand: die Zahlung gibt es nicht mehr
      return { kept, gone: !h.paid.some((p) => p.key === key) };
    })()""")
    assert stale == {"kept": True, "gone": True}, stale
    # dasselbe über den echten Ablauf (CodeRabbit PR 73): das Storno-Fenster holt den Stand, die Antwort kommt verzögert;
    # dazwischen wird eine Zahlung über syncTabPaid beim Server bestätigt – sie bleibt, ihr key bleibt bestätigt
    page.keyboard.press("Escape")
    flow = page.evaluate("""(async () => {
      const h = S.pay, realApi = api;
      let gate;
      const held = new Promise((ok) => { gate = ok; });
      api = async (path, opts) => {
        const r = await realApi(path, opts);
        if (path === `/api/tabs/${h.tab}` && !opts) await held;   // alter Stand, kommt erst später an
        return r;
      };
      try {
        closeSheet();
        const open = openStorno();
        await new Promise((ok) => setTimeout(ok, 200));
        const mine = { amount: 100, items: {}, own: true };
        h.paid.push(mine); S.part.paid = h.paid;
        const synced = await syncTabPaid();                    // echte Zahlung, vom Server bestätigt
        await new Promise((ok) => setTimeout(ok, 50));          // Antwort kommt merklich nach der Bestätigung an
        gate();
        await open;
        return { refreshed: !!document.querySelector('.sheet [data-storno]'), synced, kept: S.pay.paid.includes(mine), confirmed: S.tabPaidKeys.includes(mine.key),
          stamped: !!S.tabPaidAt?.[mine.key] };
      } finally { api = realApi; closeSheet(); }
    })()""")
    assert flow == {"refreshed": True, "synced": True, "kept": True, "confirmed": True, "stamped": True}, flow
    html = page.evaluate("bonLine({ id: 9, name: 'Wurst', opt: 'mit Senf', qty: 1, price: 600 }, { cut: { 9: 1 } })")
    assert "mit Senf" in html and "gone" in html
    assert not errs, errs
    c.put("/api/admin/devices/role", auth=AUTH, json={"name": "XCover 1", "role": "service"})
    ctx.close()


def test_settle_table_split_per_person(server, browser):  # noqa: F811
    """offener Tisch – Teilen pro Person mit Zahlart; jede Person geht als Zahlung an den Tisch (Zahlart,
    Person), danach «Bezahlt · Tisch schliessen»."""
    base, c = server
    ctx, page, errs = _phone(browser, base)
    _open_table(page, "8")
    _send(page, (0, 1))
    page.click('[data-act="tab-pay-table"]')
    page.wait_for_selector('#paystate[data-s="sent"]')
    page.click('[data-act="mode"][data-m="split"]')
    for kind in ("card", "twint"):
        page.click('#payfoot [data-act="split-go"]')
        page.click(f'#personkind [data-v="{kind}"]')
        page.click('#payfoot [data-act="split-paid"]')
    page.wait_for_selector("#splitdone")
    tid = _open_tabs(c)["8"]["id"]
    for _ in range(40):
        d = c.get(f"/api/admin/tabs/{tid}", auth=AUTH).json()
        if len(d["payments"]) == 2:
            break
        page.wait_for_timeout(100)
    assert sorted((p["part"], p["payment"]) for p in d["payments"]) == [(1, "card"), (2, "twint")]
    page.wait_for_timeout(700)
    page.click('[data-act="done"]')
    page.wait_for_selector('[data-act="digit"]')
    d = c.get(f"/api/admin/tabs/{tid}", auth=AUTH).json()
    assert d["closed_at"] and d["open"] == 0
    assert errs == [], errs
    ctx.close()
