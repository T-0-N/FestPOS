"""Kassenbuch (Review 2026-10): gleichzeitige Stornos und Quittungen, Kette der Abschlüsse mit Schlüssel,
CLUSTER_SECRET vor einem neuen Server-Schlüssel festschreiben."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_api import AUTH
from tests.test_ledger import _order, shop  # noqa: F401 (shop = Fixture)
from tests.test_receipts import _phone, _setup

LIB = Path(__file__).resolve().parent.parent / "deploy" / "festpos-conf.sh"


def test_concurrent_storno_never_exceeds_order(client, shop, monkeypatch):  # noqa: F811
    """Während ein Storno rechnet, storniert ein anderes Gerät: der erste rechnet mit dem neuen Stand nochmals."""
    from app import core, ledger
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 3}])
    real = ledger.cancelled_qty
    state = {"inner": False}

    def racing(store, order_id):
        res = real(store, order_id)
        if not state["inner"]:
            state["inner"] = True                                  # «gleichzeitig»: anderes Gerät storniert 2
            ledger.cancel(store, order_id=order_id, lines={1: 2}, reason="wrong", by_user="anna", client_uuid="innen-1234")
        return res
    monkeypatch.setattr(ledger, "cancelled_qty", racing)
    r = client.post(f"/api/admin/orders/{oid}/storno", auth=AUTH,
                    json={"client_uuid": "aussen-1234", "lines": [{"line_no": 1, "qty": 3}], "reason": "wrong"})
    assert r.status_code == 422                                     # nur noch 1 offen – nicht 3 zusätzlich
    st = core.rt.store
    assert st.scalar("SELECT SUM(qty) FROM cancellation_items") == 2
    assert st.scalar("SELECT COUNT(*) FROM cancellations WHERE order_id=?", (oid,)) == 1


def test_concurrent_receipt_only_once_original(client, shop, monkeypatch):  # noqa: F811
    from app import core, receipts
    _setup(client)
    oid, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    real = receipts.open_lines
    state = {"inner": False}

    def racing(store, order):
        res = real(store, order)
        if not state["inner"]:
            state["inner"] = True                                  # anderes Handy quittiert im selben Moment
            receipts.issue(store, order_id=oid, request_id="innen-quittung-1", by_device="XCover2")
        return res
    monkeypatch.setattr(receipts, "open_lines", racing)
    j = _phone(client, cu).json()
    assert j["copy"] is True                                        # schon quittiert: Kopie statt zweitem Original
    st = core.rt.store
    assert st.scalar("SELECT COUNT(*) FROM receipts WHERE order_uuid=?", (cu,)) == 1


def test_closing_chain_is_keyed_and_old_ones_still_verify(client, shop, monkeypatch):  # noqa: F811
    from app import core, ledger
    from app import ledger_base as lb
    st = core.rt.store

    def verify(cid):
        return client.get(f"/api/admin/closings/{cid}/verify", auth=AUTH).json()

    def rehash(cid, legacy, **change):
        row = st.query_one("SELECT * FROM closings WHERE id=?", (cid,))
        row.update(change)
        h = ledger.chain_hash(row, row["summary"], ledger._unpack(row["orders_csv"]), row["prev_hash"], legacy=legacy)
        st.execute("UPDATE closings SET net_cents=?, chain_hash=? WHERE id=?", (row["net_cents"], h, cid))
        return h

    # Abschluss 1 wie aus einer früheren Version (reines SHA-256): bleibt prüfbar, als «ältere Art»
    _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}], payment="cash")
    c1 = client.post("/api/admin/closings", auth=AUTH, json={}).json()
    assert c1["chain_hash"].startswith("h2:" + lb.key_id() + ":") and verify(c1["id"])["state"] == "ok"
    legacy = rehash(c1["id"], True)
    st.execute("DELETE FROM settings WHERE key='closing_head'")      # frühere Versionen kannten das Ende der Kette nicht
    assert verify(c1["id"])["state"] == "legacy" and verify(c1["id"])["ok"]
    # Abschluss 2 mit Schlüssel, verkettet mit dem alten
    _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}], payment="cash")
    c2 = client.post("/api/admin/closings", auth=AUTH, json={}).json()
    v2 = verify(c2["id"])
    assert v2["ok"] and v2["state"] == "ok" and v2["chain_ok"]
    assert st.scalar("SELECT prev_hash FROM closings WHERE id=?", (c2["id"],)) == legacy
    # Verändert und ohne Schlüssel neu gerechnet (auch in der alten Art): fällt auf
    row_net = st.scalar("SELECT net_cents FROM closings WHERE id=?", (c2["id"],))
    st.execute("UPDATE closings SET net_cents=? WHERE id=?", (row_net + 1000, c2["id"]))
    assert verify(c2["id"])["state"] == "bad"
    rehash(c2["id"], True, net_cents=row_net + 1000)
    assert verify(c2["id"])["state"] == "bad"                       # passt nicht zum Ende der Kette (mit Schlüssel)
    assert ledger.check_chain_hash(st.query_one("SELECT * FROM closings WHERE id=?", (c2["id"],)),
                                   ledger._unpack(st.scalar("SELECT orders_csv FROM closings WHERE id=?", (c2["id"],))),
                                   keyed_before=True) == "bad"      # nach einem Abschluss mit Schlüssel: keine alte Art
    rehash(c2["id"], False, net_cents=row_net)
    st.execute("UPDATE settings SET value=? WHERE key='closing_head'", (ledger.chain_head(c2["id"], st.scalar(
        "SELECT chain_hash FROM closings WHERE id=?", (c2["id"],))),))
    assert verify(c2["id"])["ok"]
    # anderer Schlüssel (z.B. CLUSTER_SECRET geändert): nicht «verändert», sondern nicht prüfbar
    monkeypatch.setattr(core.Config, "cluster_secret", "ganz-anderes-geheimnis-123")
    v3 = verify(c2["id"])
    assert v3["state"] == "other_key" and not v3["ok"]


def test_secret_derived_warning(client, monkeypatch):
    from app import core
    monkeypatch.setattr(core.Config, "cluster_secret_derived", True)
    assert "secret_derived" in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]
    assert "secret_derived" in core.security_warnings()


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("openssl"), reason="bash/openssl fehlen")
def test_shell_pins_the_same_secret_as_the_app(tmp_path, monkeypatch):
    """festpos_secret_fix rechnet das Geheimnis genau wie app/core.py aus dem bisherigen Schlüssel."""
    key = tmp_path / "server.key"
    key.write_bytes(b"-----BEGIN PRIVATE KEY-----\nalter-schluessel\n-----END PRIVATE KEY-----\n")
    conf = tmp_path / "festpos.conf"
    conf.write_bytes(b"# Kommentar\r\nADMIN_PASSWORD=ein-langes-passwort\r\nCLUSTER_SECRET=bitte-aendern-lang-und-zufaellig\r\n")

    def fix(arg):
        return subprocess.run(["bash", "-c", f'source "{LIB}"; festpos_secret_fix "{conf}" "{arg}"'],
                              env={"PATH": os.environ["PATH"]}, capture_output=True, text=True)
    assert fix(key).returncode == 0
    text = conf.read_text()
    want = "auto-" + hashlib.sha256(b"festpos-cluster-secret\0" + key.read_bytes()).hexdigest()
    assert f"CLUSTER_SECRET={want}\n" in text and "# Kommentar" in text and "ADMIN_PASSWORD=ein-langes-passwort" in text
    from app import core
    monkeypatch.setenv("FESTPOS_CLUSTER_SECRET", "bitte-aendern-lang-und-zufaellig")
    monkeypatch.setenv("FESTPOS_TLS_KEY", str(key))
    assert core._cluster_secret() == (want, True)                   # gleich wie bisher abgeleitet
    monkeypatch.setenv("FESTPOS_CLUSTER_SECRET", want)
    assert core._cluster_secret() == (want, False)                  # jetzt fest
    assert fix(key).returncode == 1                                 # schon gesetzt: nichts mehr ändern
    conf.write_text("CLUSTER_SECRET=bitte-aendern\n")
    assert fix("--neu").returncode == 0
    neu = conf.read_text().strip().split("=", 1)[1]
    assert len(neu) == 64 and int(neu, 16) >= 0
    conf.write_text("ADMIN_PASSWORD=x\n")                           # Zeile fehlt: wird angehängt
    assert fix(key).returncode == 0 and conf.read_text().endswith(f"CLUSTER_SECRET={want}\n")
    assert fix(tmp_path / "fehlt.key").returncode == 1
