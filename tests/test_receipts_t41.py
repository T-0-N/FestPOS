"""T41: Prüfsumme der Quittung, Version 2 – deckt alles ab, was gedruckt wird (Empfänger, Zahlart, MWST-Nummer,
Positionsbeträge …). Alte Quittungen (Version 1) bleiben gültig; die Version herabsetzen nützt nichts."""
from __future__ import annotations

import json

from tests.test_api import AUTH
from tests.test_ledger import _order, shop  # noqa: F401 (shop = Fixture)
from tests.test_receipts import _phone, _render, _rid, _setup


def _bad(client):
    return client.get("/api/admin/closings/open", auth=AUTH).json()["summary"]["check"]["bad"]


def _big_receipt(client, shop):  # noqa: F811
    _setup(client)
    client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"})
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 62}], payment="card")
    r = client.post(f"/api/admin/orders/{oid}/receipt", auth=AUTH,
                    json={"request_id": _rid(), "recipient": "Firma Muster AG\nBahnhofstr. 1, 6000 Luzern"})
    assert r.status_code == 200, r.text
    return r.json()["receipts"][0]["id"]


def test_v2_covers_recipient_payment_vat_number_and_amounts(client, shop):  # noqa: F811
    from app import core
    st = core.rt.store
    rid = _big_receipt(client, shop)
    rec = st.query_one("SELECT * FROM receipts WHERE id=?", (rid,))
    assert rec["digest_v"] == 2 and _bad(client) == []
    data, lines = rec["data"], rec["lines"]

    def tamper(col, value):
        st.execute(f"UPDATE receipts SET {col}=? WHERE id=?", (value, rid))
        bad = _bad(client)
        st.execute(f"UPDATE receipts SET {col}=? WHERE id=?", (rec[col], rid))
        return bad

    d = json.loads(data)
    assert tamper("data", json.dumps({**d, "recipient": ["Jemand Anders", "Irgendwo 2"]})) == ["Q1"]
    assert tamper("data", json.dumps({**d, "payment": "cash"})) == ["Q1"]
    assert tamper("data", json.dumps({**d, "vat_number": "CHE-999.999.999 MWST"})) == ["Q1"]
    ls = json.loads(lines)
    ls[0]["amount"] = 1                                                     # gedruckter Positionsbetrag
    assert tamper("lines", json.dumps(ls)) == ["Q1"]
    # Version herabsetzen und Empfänger ändern: fällt trotzdem auf
    st.execute("UPDATE receipts SET digest_v=NULL, data=? WHERE id=?",
               (json.dumps({**d, "recipient": ["Jemand Anders"]}), rid))
    assert _bad(client) == ["Q1"]
    st.execute("UPDATE receipts SET digest_v=2, data=? WHERE id=?", (data, rid))
    assert _bad(client) == []


def test_old_v1_receipts_stay_valid(client, shop):  # noqa: F811
    from app import core, ledger_base as lb
    st = core.rt.store
    rid = _big_receipt(client, shop)
    rec = st.query_one("SELECT * FROM receipts WHERE id=?", (rid,))
    v1 = lb.receipt_digest({**rec, "digest_v": None})                      # so hätte eine ältere Version gerechnet
    st.execute("UPDATE receipts SET digest=?, digest_v=NULL WHERE id=?", (v1, rid))
    assert _bad(client) == []


def test_copy_prints_same_check_number(client, shop):  # noqa: F811
    from app import core, ledger_base as lb
    _setup(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}], payment="cash")
    r = _phone(client, cu).json()["receipts"][0]
    first = _render(client, None)
    assert _phone(client, cu).json()["copy"] is True
    copy = _render(client, None)
    code = lb.short_check(core.rt.store.scalar("SELECT digest FROM receipts WHERE id=?", (r["id"],)))
    assert code in first and code in copy and "KOPIE" in copy
