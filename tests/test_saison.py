"""Saison abschliessen: Archiv der Belege, danach Gastdaten auf dem Pi löschen (T-0-N/FestPOS#12)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tests.test_api import AUTH
from tests.test_ledger import _order, shop  # noqa: F401 (shop = Fixture)
from tests.test_receipts import _phone, _rid, _setup

ROOT = Path(__file__).resolve().parent.parent
PW = "Saison-Archiv-2026"


def _season(client, shop):  # noqa: F811
    """Zwei Tage: Quittungen (eine über CHF 400 mit Name und Ort), je ein Tagesabschluss."""
    _setup(client)
    assert client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"}).status_code == 200
    _, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 2}])
    assert _phone(client, cu).status_code == 200
    oid2, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 62}])
    r = client.post(f"/api/admin/orders/{oid2}/receipt", auth=AUTH,
                    json={"request_id": _rid(), "recipient_name": "Muster AG", "recipient_place": "6000 Luzern"})
    assert r.status_code == 200, r.text
    assert client.post("/api/admin/closings", auth=AUTH, json={}).status_code == 200
    _, cu3 = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    assert _phone(client, cu3).status_code == 200
    assert _phone(client, cu3).json()["copy"] is True                                  # eine Kopie
    assert client.post("/api/admin/closings", auth=AUTH, json={}).status_code == 200


def test_recipient_name_and_place(client, shop):  # noqa: F811
    _setup(client)
    assert client.put("/api/admin/vat", auth=AUTH, json={"enabled": True, "number": "CHE-123.456.789 MWST"}).status_code == 200
    oid, _ = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 62}])
    r = client.post(f"/api/admin/orders/{oid}/receipt", auth=AUTH, json={"request_id": _rid(), "recipient_name": "Muster AG"})
    assert r.status_code == 422 and "Name und Ort" in r.json()["detail"]
    r = client.post(f"/api/admin/orders/{oid}/receipt", auth=AUTH,                 # länger als eine Bonzeile: nie gekürzt
                    json={"request_id": _rid(), "recipient_name": "M" * 49, "recipient_place": "6000 Luzern"})
    assert r.status_code == 422
    r = client.post(f"/api/admin/orders/{oid}/receipt", auth=AUTH,
                    json={"request_id": _rid(), "recipient_name": " Muster  AG ", "recipient_place": "6000 Luzern"})
    assert r.status_code == 200
    from app import core
    data = json.loads(core.rt.store.scalar("SELECT data FROM receipts WHERE no=?", (r.json()["receipts"][0]["no"],)))
    assert data["recipient"] == ["Muster AG", "6000 Luzern"]


def test_archive_confirm_and_delete(client, shop, tmp_path):  # noqa: F811
    from app import backup, core, saison
    _season(client, shop)
    st = client.get("/api/admin/season", auth=AUTH).json()
    assert st["open"]["closings"] == 2 and st["open"]["receipts"] == 3 and st["open"]["recipients"] == 1
    assert st["pending"] is None and st["last"] is None and not st["due"]
    # ohne Archiv: nichts löschen
    assert client.post("/api/admin/season/close", auth=AUTH, json={"sha": "0" * 64}).status_code == 409
    assert client.post("/api/admin/season/archive", auth=AUTH, json={"password": "kurz"}).status_code == 422
    p = client.post("/api/admin/season/archive", auth=AUTH, json={"password": PW}).json()
    assert p["check"]["ok"] and p["receipts"]["count"] == 3 and p["receipts"]["recipients"] == 1 and not p["verified"]
    assert p["name"].startswith("festpos-saison-") and p["name"].endswith(".festpos")
    data = client.get("/api/admin/season/archive", auth=AUTH).content
    assert hashlib.sha256(data).hexdigest() == p["sha"] and b"Muster AG" not in data          # verschlüsselt
    members = backup.unpack(data, PW)
    assert {"archiv.json", "abschluesse.json", "quittungen.json", "kopien.json", "LIESMICH.txt",
            "pdf/abschluss-00001.pdf", "pdf/abschluss-00002.pdf"} <= set(members)
    recs = json.loads(members["quittungen.json"])
    assert len(recs) == 3 and len(json.loads(members["kopien.json"])) == 1
    # unvollständig, auch wenn die vorhandenen Prüfsummen stimmen: nie «in Ordnung»
    info = json.loads(members["archiv.json"])
    assert saison.verify(members)["ok"]
    for drop in ("kopien.json", "pdf/abschluss-00002.pdf"):
        cut = {n: c for n, c in members.items() if n != drop}
        info2 = {**info, "files": {n: h for n, h in info["files"].items() if n != drop}}
        assert not saison.verify({**cut, "archiv.json": json.dumps(info2).encode()})["ok"]
    assert not saison.verify({**members, "archiv.json": json.dumps({**info, "files": {}}).encode()})["ok"]
    bad_count = {**info, "receipts": {**info["receipts"], "count": 4}}
    assert not saison.verify({**members, "archiv.json": json.dumps(bad_count).encode()})["ok"]
    for bad in (b"[1]", b'[null]', b'{"x": 1}'):                                  # kein Absturz, sondern «nein»
        assert saison.verify({**members, "quittungen.json": bad})["ok"] is False
        assert saison.verify({**members, "abschluesse.json": bad})["ok"] is False
    db_recs = core.rt.store.query("SELECT * FROM receipts ORDER BY id")
    assert [r["digest"] for r in recs] == [r["digest"] for r in db_recs]                       # unverändert
    # noch nicht bestätigt: nicht löschen
    assert client.post("/api/admin/season/close", auth=AUTH, json={"sha": p["sha"]}).status_code == 409
    # falsche Datei / falsches Passwort
    r = client.post("/api/admin/season/verify", auth=AUTH, files={"file": ("x.festpos", data)}, data={"password": "falsch-falsch"})
    assert r.status_code == 422
    # wieder hochladen: bestätigt
    r = client.post("/api/admin/season/verify", auth=AUTH, files={"file": ("x.festpos", data)}, data={"password": PW}).json()
    assert r["matches_pending"] and r["check"]["ok"] and r["pending"]["verified"]["how"] == "upload"
    max_no = core.rt.store.scalar("SELECT MAX(no) FROM receipts")
    r = client.post("/api/admin/season/close", auth=AUTH, json={"sha": p["sha"]})
    assert r.status_code == 200 and r.json()["receipts"] == 3
    # keine Gastdaten mehr, Abschlüsse bleiben und prüfen weiter, Nummern laufen weiter
    assert core.rt.store.scalar("SELECT COUNT(*) FROM receipts") == 0
    assert core.rt.store.scalar("SELECT COUNT(*) FROM receipt_copies") == 0
    assert all(client.get(f"/api/admin/closings/{i}/verify", auth=AUTH).json()["ok"] for i in (1, 2))
    assert not list(saison.ARCHIV_DIR.glob("*.festpos"))                                     # auch nicht im Archiv-Ordner
    _, cu = _order(client, [{"product_id": shop["Bier 5dl"], "qty": 1}])
    assert _phone(client, cu).json()["receipts"][0]["no"] == max_no + 1
    st = client.get("/api/admin/season", auth=AUTH).json()
    assert st["last"]["receipts"] == 3 and st["open"]["closings"] == 0 and st["pending"] is None
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert "season_archive" in audit and "season_close" in audit
    # das Archiv lässt sich später wieder hochladen und prüfen
    r = client.post("/api/admin/season/verify", auth=AUTH, files={"file": ("x.festpos", data)}, data={"password": PW}).json()
    assert r["check"]["ok"] and not r["matches_pending"] and r["check"]["info"]["receipts"]["count"] == 3
    # nächste Saison: nur die neuen Abschlüsse
    assert client.post("/api/admin/closings", auth=AUTH, json={}).status_code == 200
    assert client.get("/api/admin/season", auth=AUTH).json()["open"]["closings"] == 1


def test_usb_and_changes_after_archive(client, shop, tmp_path, monkeypatch):  # noqa: F811
    """Stick: der Helfer schreibt und liest zurück (hier ein Ordner statt des Sticks). Ändert sich danach etwas
    (neue Quittung desselben Abschlusses gibt es nicht, aber ein Archiv eines anderen Standes): nicht löschen."""
    from app import cards
    _season(client, shop)
    p = client.post("/api/admin/season/archive", auth=AUTH, json={"password": PW}).json()
    calls = {}

    def fake_submit(kind, payload=None):
        calls["job"] = (kind, payload)
        return "j1"

    monkeypatch.setattr(cards, "available", lambda: True)
    monkeypatch.setattr(cards, "submit", fake_submit)
    monkeypatch.setattr(cards, "wait", lambda job, timeout=30: {"ok": True, "sha": "f" * 64, "device": "sda1", "path": "x"})
    assert client.post("/api/admin/season/usb", auth=AUTH).status_code == 409                  # nicht dasselbe
    assert calls["job"] == ("archiv_usb", {"name": p["name"]})
    monkeypatch.setattr(cards, "wait", lambda job, timeout=30: {"ok": False, "code": "no_stick"})
    r = client.post("/api/admin/season/usb", auth=AUTH)
    assert r.status_code == 409 and "USB-Stick" in r.json()["detail"]
    monkeypatch.setattr(cards, "wait", lambda job, timeout=30: {"ok": True, "sha": p["sha"], "device": "sda1",
                                                                "path": "festpos-archiv/" + p["name"]})
    r = client.post("/api/admin/season/usb", auth=AUTH).json()
    assert r["verified"]["how"] == "usb"
    # Quittung eines archivierten Abschlusses fehlt plötzlich (Datenbank verändert): neues Archiv nötig
    from app import core
    core.rt.store.execute("DELETE FROM receipt_copies")
    core.rt.store.execute("DELETE FROM receipts WHERE id=(SELECT MAX(id) FROM receipts)")
    assert client.post("/api/admin/season/close", auth=AUTH, json={"sha": p["sha"]}).status_code == 409
    assert client.delete("/api/admin/season/archive", auth=AUTH).status_code == 200
    assert client.get("/api/admin/season", auth=AUTH).json()["pending"] is None


def test_helper_writes_archive_to_stick(tmp_path):
    """festpos-wiederherstellen: Archiv aus dem Ordner der App auf den Stick, zurückgelesen; nichts überschreiben."""
    import importlib.machinery
    import importlib.util
    import os
    arch, stick = tmp_path / "archiv", tmp_path / "stick"
    arch.mkdir()
    stick.mkdir()
    os.environ["FESTPOS_ARCHIV_DIR"], os.environ["FESTPOS_STICK_ORDNER"] = str(arch), str(stick)
    try:
        loader = importlib.machinery.SourceFileLoader("wh_archiv", str(ROOT / "deploy" / "festpos-wiederherstellen"))
        spec = importlib.util.spec_from_loader("wh_archiv", loader)
        wh = importlib.util.module_from_spec(spec)
        loader.exec_module(wh)
    finally:
        del os.environ["FESTPOS_ARCHIV_DIR"], os.environ["FESTPOS_STICK_ORDNER"]
    from app import backup
    name = "festpos-saison-2026-06-01_2026-09-30.festpos"
    data = backup.pack({"a": b"x"}, PW)
    (arch / name).write_bytes(data)
    r1 = wh.auftrag_archiv_usb({"name": name})
    r2 = wh.auftrag_archiv_usb({"name": name})
    assert r1["sha"] == r2["sha"] == hashlib.sha256(data).hexdigest()
    assert r1["path"] == f"festpos-archiv/{name}" and r2["path"].endswith("-1.festpos")
    for bad in ("../x.festpos", "irgendwas.festpos", ""):
        try:
            wh.auftrag_archiv_usb({"name": bad})
            raise AssertionError(bad)
        except wh.Fehler as e:
            assert e.code == "job"
    (arch / "festpos-saison-2026-01-01_2026-01-02.festpos").write_bytes(b"kein Archiv")
    try:
        wh.auftrag_archiv_usb({"name": "festpos-saison-2026-01-01_2026-01-02.festpos"})
        raise AssertionError("unverschlüsselt")
    except wh.Fehler as e:
        assert e.code == "job"


def test_reminder_after_three_months(client, shop):  # noqa: F811
    from app import core
    _season(client, shop)
    assert "season_due" not in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]
    core.rt.store.execute("UPDATE receipts SET created_at='2020-01-01T10:00:00'")
    assert "season_due" in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]


def test_pc_tool_opens_and_checks(tmp_path):
    from app import backup
    files = {"LIESMICH.txt": b"x", "quittungen.json": b"[]", "abschluesse.json": b'[{"id": 1}, {"id": 2}]',
             "kopien.json": b"[]", "pdf/abschluss-00001.pdf": b"%PDF-1", "pdf/abschluss-00002.pdf": b"%PDF-2"}
    info = {"kind": "saison", "files": {n: hashlib.sha256(c).hexdigest() for n, c in files.items()},
            "closings": {"from": 1, "to": 2, "count": 2}, "receipts": {"count": 0, "copies": 0}}
    arch = tmp_path / "festpos-saison-2026-06-01_2026-09-30.festpos"
    arch.write_bytes(backup.pack({**files, "archiv.json": json.dumps(info).encode()}, PW))
    pw = tmp_path / "pw"
    pw.write_text(PW)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "archiv.py"), str(arch), "--ziel", str(tmp_path / "aus"),
                        "--passwort-datei", str(pw)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "aus" / "LIESMICH.txt").read_bytes() == b"x" and "vollständig" in r.stdout
    info["files"]["LIESMICH.txt"] = "0" * 64
    arch.write_bytes(backup.pack({**files, "archiv.json": json.dumps(info).encode()}, PW))
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "archiv.py"), str(arch), "--ziel", str(tmp_path / "aus2"),
                        "--passwort-datei", str(pw)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "LIESMICH.txt" in r.stderr
    info["files"]["LIESMICH.txt"] = hashlib.sha256(b"x").hexdigest()
    no_pdf = {n: h for n, h in info["files"].items() if n != "pdf/abschluss-00002.pdf"}
    for meta in (None, b"[]", json.dumps({"files": {"LIESMICH.txt": info["files"]["LIESMICH.txt"]}}).encode(),
                 json.dumps({**info, "files": no_pdf}).encode(),                                  # PDF fehlt
                 json.dumps({**info, "closings": {"count": 3}}).encode()):                         # Anzahl passt nicht
        arch.write_bytes(backup.pack({**files, **({"archiv.json": meta} if meta else {})}, PW))   # unvollständig
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "archiv.py"), str(arch), "--ziel", str(tmp_path / "aus3"),
                            "--passwort-datei", str(pw)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 1 and "archiv.json" in r.stderr and not (tmp_path / "aus3").exists()


def test_no_archive_without_closing_pdf(client, shop, monkeypatch):  # noqa: F811
    """Entscheid: jeder Abschluss hat sein PDF im Archiv – fehlt eines, gibt es kein Archiv (und nichts zu löschen)."""
    from app import ledger

    def broken(r):
        raise RuntimeError("kaputt")
    _season(client, shop)
    monkeypatch.setattr(ledger, "closing_pdf", broken)
    r = client.post("/api/admin/season/archive", auth=AUTH, json={"password": PW})
    assert r.status_code == 500 and "PDF" in r.json()["detail"]
    assert client.get("/api/admin/season", auth=AUTH).json()["pending"] is None
