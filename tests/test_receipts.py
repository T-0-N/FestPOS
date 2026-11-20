"""Version 2026.10.10-30: Quittung für den Gast (Kassenbeleg) – fortlaufende Nummer, Inhalt festgehalten und mit
Prüfsumme gesichert, jeder weitere Druck als KOPIE, Einzeln pro Person, MWST wahlweise, im Tagesabschluss."""
from __future__ import annotations

import json
import uuid

import pytest

from tests.test_api import AUTH
from tests.test_ledger import _order, _storno, shop  # noqa: F401 (shop = Fixture)


def _rid() -> str:
    return str(uuid.uuid4())


def _setup(client, header="Verein Muster\nMusterweg 1, 6000 Luzern", footer="Vielen Dank!"):
    pid = client.get("/api/admin/printers", auth=AUTH).json()[0]["id"]
    r = client.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": pid, "receipt_header": header,
                                                           "receipt_footer": footer})
    assert r.status_code == 200, r.text
    return pid


def _phone(client, cu, **kw):
    return client.post("/api/orders/receipt", json={"order_uuid": cu, "request_id": _rid(), **kw})


def _render(client, rid_or_job):
    from app import core, print_service as ps
    st = core.rt.store
    job = st.query_one("SELECT * FROM print_jobs WHERE kind='receipt' ORDER BY id DESC LIMIT 1") if rid_or_job is None \
        else st.query_one("SELECT * FROM print_jobs WHERE id=?", (rid_or_job,))
    data = ps.render_job(st, job, ps.load_printers(st))
    from app.escpos import CODEPAGE_PY
    return data.decode(CODEPAGE_PY, errors="replace")


def test_reprint_in_jobs_is_original_until_printed_then_copy(client, shop):  # noqa: F811
    """Verwaltung › Druckaufträge › Nachdrucken: nie gedruckt -> Original; gedruckt -> KOPIE, festgehalten."""
    from app import core
    st = core.rt.store
    _setup(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    assert _phone(client, cu).status_code == 200
    job = st.query_one("SELECT id FROM print_jobs WHERE kind='receipt' ORDER BY id DESC LIMIT 1")["id"]
    st.execute("UPDATE print_jobs SET status='failed', last_error='Papier leer' WHERE id=?", (job,))
    new = client.post(f"/api/admin/jobs/{job}/reprint", auth=AUTH).json()["job_id"]
    assert st.scalar("SELECT reprint FROM print_jobs WHERE id=?", (new,)) == 0       # ersetzt das Original
    assert "KOPIE" not in _render(client, new)
    assert st.scalar("SELECT COUNT(*) FROM receipt_copies") == 0
    st.execute("UPDATE print_jobs SET status='done' WHERE id=?", (new,))
    again = client.post(f"/api/admin/jobs/{new}/reprint", auth=AUTH).json()["job_id"]
    assert st.scalar("SELECT reprint FROM print_jobs WHERE id=?", (again,)) == 1 and "KOPIE" in _render(client, again)
    assert st.scalar("SELECT COUNT(*) FROM receipt_copies") == 1
    d = client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()
    assert d["receipts"][0]["copies"] == 1


def test_receipt_off_until_printer_chosen(client, shop):  # noqa: F811
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    assert client.get("/api/bootstrap").json()["receipt"] is False
    r = _phone(client, cu)
    assert r.status_code == 409 and "Quittung" in r.json()["detail"]
    _setup(client)
    assert client.get("/api/bootstrap").json()["receipt"] is True
    assert client.get("/api/phone/status").json()["receipt"] is True
    assert client.put("/api/admin/settings", auth=AUTH, json={"receipt_printer_id": 999}).status_code == 422


def test_numbering_copy_and_content(client, shop):  # noqa: F811
    _setup(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}, {"product_id": shop["Mineral"], "qty": 1}],
                     payment="cash")
    rid = _rid()
    r = client.post("/api/orders/receipt", json={"order_uuid": cu, "request_id": rid})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["copy"] is False and j["receipts"][0]["no"] == 1 and j["receipts"][0]["total_cents"] == 1700
    assert j["printer"] == "Getränkeausgabe"
    again = client.post("/api/orders/receipt", json={"order_uuid": cu, "request_id": rid})   # Doppeltipp
    assert again.json()["duplicate"] is True and again.json()["receipts"][0]["no"] == 1
    from app import core
    assert core.rt.store.scalar("SELECT COUNT(*) FROM print_jobs WHERE kind='receipt'") == 1
    text = _render(client, None)
    for part in ("Verein Muster", "QUITTUNG", "Nr. 00001", "2 x Bier 5dl", "13.00", "je 6.50", "TOTAL CHF",
                 "17.00", "Bezahlt", "Bar", "Vielen Dank!", "Pr"):
        assert part in text, (part, text)
    assert "MWST" not in text and "KOPIE" not in text
    # nochmals für dieselbe Bestellung: alles quittiert -> Kopie (gleiche Nummer, KOPIE)
    r2 = _phone(client, cu).json()
    assert r2["copy"] is True and r2["receipts"][0]["no"] == 1
    assert "KOPIE" in _render(client, None) and "Nr. 00001" in _render(client, None)
    # nächste Bestellung: Nummer 2, Kopie direkt über die Nummer
    oid2, cu2 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    r3 = _phone(client, cu2).json()
    assert r3["receipts"][0]["no"] == 2
    assert _phone(client, cu2, copy_of=r3["receipts"][0]["id"]).json()["copy"] is True
    assert _phone(client, cu, copy_of=r3["receipts"][0]["id"]).status_code == 404        # fremde Quittung
    d = client.get(f"/api/admin/orders/{oid}", auth=AUTH).json()
    assert [(x["no"], x["copies"]) for x in d["receipts"]] == [(1, 1)]
    log = [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert log.count("receipt") == 2 and log.count("receipt_copy") == 2
    jobs = client.get("/api/admin/jobs", auth=AUTH).json()
    labels = [x["label"] for x in (jobs["jobs"] if isinstance(jobs, dict) else jobs) if x["kind"] == "receipt"]
    assert any("Quittung 00001" in x and "Kopie" in x for x in labels), labels


def test_part_receipts_per_person_and_storno(client, shop):  # noqa: F811
    _setup(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 3}, {"product_id": shop["Mineral"], "qty": 1}])
    p1 = _phone(client, cu, part=1, items=[{"product_id": shop["Bier 5dl"], "qty": 1}]).json()
    assert p1["receipts"][0]["total_cents"] == 650 and p1["receipts"][0]["part"] == 1
    assert "Person 1" in _render(client, None)
    p2 = _phone(client, cu, part=2, items=[{"product_id": shop["Bier 5dl"], "qty": 2},
                                          {"product_id": shop["Mineral"], "qty": 1}]).json()
    assert p2["receipts"][0]["total_cents"] == 1700 and p2["receipts"][0]["no"] == 2
    # mehr als offen: abgelehnt, nichts gedruckt
    r = _phone(client, cu, part=3, items=[{"product_id": shop["Bier 5dl"], "qty": 1}])
    assert r.status_code == 409 and "Bier 5dl" in r.json()["detail"]
    # ganze Bestellung: alles quittiert -> Kopien beider Quittungen
    allc = _phone(client, cu).json()
    assert allc["copy"] is True and [x["no"] for x in allc["receipts"]] == [1, 2]
    # Storno vor der Quittung: nur noch der Rest
    oid2, cu2 = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    assert _storno(client, oid2, {1: 1}).status_code == 200
    r = _phone(client, cu2).json()
    assert r["receipts"][0]["total_cents"] == 650
    oid3, cu3 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    assert _storno(client, oid3).status_code == 200
    assert _phone(client, cu3).status_code == 409                                         # alles storniert
    assert _phone(client, "unbekannt-12345").status_code == 404


def test_vat_receipt_and_recipient(client, shop):  # noqa: F811
    _setup(client)
    assert client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"}).status_code == 200
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    _phone(client, cu)
    text = _render(client, None)
    for part in ("MWST-Nr. CHE-123.456.789", "13.00 A", "A 8.1%", "0.97", "inkl. MWST"):
        assert part in text, (part, text)
    # über CHF 400: das Handy druckt nicht (verweist an die Verwaltung), die Verwaltung nur mit Empfänger
    oid2, cu2 = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 62}])
    r = _phone(client, cu2, part=1, items=[{"product_id": shop["Bier 5dl"], "qty": 62}])
    assert r.status_code == 409 and "Verwaltung" in r.json()["detail"]
    assert _phone(client, cu2).status_code == 409
    r = client.post(f"/api/admin/orders/{oid2}/receipt", auth=AUTH, json={"request_id": _rid()})
    assert r.status_code == 422 and "Name und Adresse" in r.json()["detail"]
    r = client.post(f"/api/admin/orders/{oid2}/receipt", auth=AUTH,
                    json={"request_id": _rid(), "recipient": "Firma Muster AG\nBahnhofstr. 1, 6000 Luzern"}).json()
    assert r["receipts"][0]["no"] == 2 and r["receipts"][0]["total_cents"] == 62 * 650
    assert "Firma Muster AG" in _render(client, None)
    # bis CHF 400 (auch Einzeln pro Person) geht es am Handy weiter
    assert _phone(client, cu2).json()["copy"] is True                                   # schon quittiert: Kopie
    oid3, cu3 = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 62}])
    assert _phone(client, cu3, part=1, items=[{"product_id": shop["Bier 5dl"], "qty": 30}]).status_code == 200


def test_receipts_in_closing_and_digest(client, shop):  # noqa: F811
    _setup(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}], payment="cash")
    r = _phone(client, cu).json()
    _phone(client, cu)                                                                    # Kopie
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["receipts"]["count"] == 1 and s["receipts"]["copies"] == 1 and s["receipts"]["first"] == 1
    assert s["check"]["bad"] == []
    # nachträglich geändert: fällt auf
    from app import core
    core.rt.store.execute("UPDATE receipts SET total_cents=1 WHERE id=?", (r["receipts"][0]["id"],))
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["check"]["bad"] == ["Q1"]
    core.rt.store.execute("UPDATE receipts SET total_cents=650 WHERE id=?", (r["receipts"][0]["id"],))
    c = client.post("/api/admin/closings", auth=AUTH, json={"counted_cents": 650}).json()
    assert c["summary"]["receipts"]["count"] == 1
    assert core.rt.store.scalar("SELECT closing_id FROM receipts") == c["id"]
    assert core.rt.store.scalar("SELECT closing_id FROM receipt_copies") == c["id"]
    pdf = client.get(f"/api/admin/closings/{c['id']}/pdf", auth=AUTH)
    assert pdf.status_code == 200
    # Nummer läuft nach «Alle Bestellungen löschen» weiter (Quittungen bleiben)
    assert client.post("/api/admin/reset-orders?confirm=JA", auth=AUTH).status_code == 200
    oid2, cu2 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    assert _phone(client, cu2).json()["receipts"][0]["no"] == 2


def test_test_orders_print_test_receipts_without_number(client, shop):  # noqa: F811
    _setup(client)
    client.put("/api/admin/settings", auth=AUTH, json={"test_enabled": True, "test_pin": "1234", "test_print": False})
    oid, cu = _order(client, [{"product_id": shop["Mineral"], "qty": 1}], test=True)
    assert _phone(client, cu).status_code == 409                                          # Übung ohne Druck
    client.put("/api/admin/settings", auth=AUTH, json={"test_print": True})
    r = _phone(client, cu).json()
    assert r["receipts"][0]["no"] is None and r["receipts"][0]["test"] is True
    assert "TEST" in _render(client, None)
    oid2, cu2 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    assert _phone(client, cu2).json()["receipts"][0]["no"] == 1                           # Übung zählt nicht
    from app import core
    # Der Druckdienst versucht im Hintergrund zu drucken (kein Drucker da). Ein Auftrag, der gerade gesendet wird,
    # bleibt beim Löschen absichtlich stehen – je nach Zeitpunkt. Deshalb: Aufträge als erledigt markieren.
    core.rt.store.execute("UPDATE print_jobs SET status='failed' WHERE status IN ('pending', 'printing')")
    assert client.post("/api/admin/test-orders/delete?confirm=JA", auth=AUTH).json()["deleted_orders"] == 1
    assert core.rt.store.scalar("SELECT COUNT(*) FROM receipts") == 1


@pytest.mark.parametrize("width", [48, 32])
def test_receipt_fits_narrow_printers(width):
    from app import print_service as ps
    from app.escpos import CODEPAGE_PY
    dev = ps.PrinterCfg(1, "Bar", "127.0.0.1", 9100, width, True, True)
    rec = {"id": 7, "no": 12, "order_id": 42, "test": 0, "created_at": "2026-10-18T14:06:00", "total_cents": 3300,
           "digest": "abcd1234:" + "f" * 64,
           "lines": json.dumps([{"line_no": 1, "name": "Coretto Grappa mit langem Namen", "options": "ohne Zucker",
                                 "qty": 3, "price": 600, "amount": 1800, "vat_code": "normal", "vat_bp": 810},
                                {"line_no": 2, "name": "Bratwurst", "qty": 1, "price": 1500, "amount": 1500,
                                 "vat_code": "reduced", "vat_bp": 260}]),
           "data": json.dumps({"header": ["Verein Muster"], "vat": True, "vat_number": "CHE-123.456.789 MWST",
                               "table": "12", "table_word": "Tisch", "order_no": 42, "device": "XCover101 (Anna)",
                               "payment": "card", "part": None})}
    text = ps.render_receipt(dev, rec).decode(CODEPAGE_PY, errors="replace")
    lines = [x for x in text.replace("\r", "").split("\n")]
    import re
    plain = [re.sub(r"[\x00-\x1f]|\x1b.|\x1d.", "", x) for x in lines]
    assert "Karte" in text and "B 2.6%" in text and "Q7" in text and "Nr. 00012" in text
    assert "abcd" not in text and "ffff-ffff" in text                     # Prüfnummer aus der Prüfsumme (ohne Kennung)
    assert all(len(x) <= width + 6 for x in plain), [x for x in plain if len(x) > width + 6]


def test_share_receipts_per_person_when_splitting(client, shop):  # noqa: F811
    """Teilen pro Person – Quittung über den Anteil (ohne Positionen), MWST anteilig, Zahlart der Person,
    pro Person ein Original (danach Kopie); Anteile und Positionen schliessen sich aus; zusammen höchstens das Total."""
    _setup(client)
    assert client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"}).status_code == 200
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}, {"product_id": shop["Mineral"], "qty": 1}])
    share = lambda n, a, **kw: _phone(client, cu, share={"person": n, "persons": 3, "amount": a}, **kw)  # noqa: E731
    r = share(1, 570, payment="card").json()
    assert r["copy"] is False and r["receipts"][0]["total_cents"] == 570
    text = _render(client, None)
    for part in ("Anteil an Total 17.00", "5.70 A", "Teilbetrag Person 1", "inkl. MWST"):
        assert part in text, (part, text)
    assert share(1, 570).json()["copy"] is True                                          # Person 1 nochmals: Kopie
    assert share(2, 565).json()["copy"] is False
    assert share(3, 600).status_code == 409                                              # mehr als das Total
    assert _phone(client, cu, part=4, items=[{"product_id": shop["Mineral"], "qty": 1}]).status_code == 409
    whole = _phone(client, cu).json()                                                    # ganze Bestellung: Kopien
    assert whole["copy"] is True and len(whole["receipts"]) == 2
    # umgekehrt: schon über Positionen quittiert -> kein Anteil mehr
    oid2, cu2 = _order(client, [{"product_id": shop["Mineral"], "qty": 1}])
    assert _phone(client, cu2).status_code == 200
    assert _phone(client, cu2, share={"person": 1, "persons": 2, "amount": 200}).status_code == 409
    # Prüfsumme und Tagesabschluss wie jede Quittung
    s = client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]
    assert s["receipts"]["count"] >= 3 and not s["receipts"].get("bad")
