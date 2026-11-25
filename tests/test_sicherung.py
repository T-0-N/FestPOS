"""Sicherung (T8): verschlüsselt mit allem drin herunterladen und einspielen, eigenes Passwort, gleiches System vs.
neuer Pi, Sperren und Zertifikate seit der Sicherung, Quittungsnummern, automatisch auf einen USB-Stick
(root-Helfer festpos-sicherung), Hinweise in Übersicht und Statusanzeige."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import backup
from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-sicherung"
SECRETS = {"fully_password": "Fully-Geheim-1", "uplink_password": "Router-Geheim-2",
           "update_net_token": "ghp_TokenGeheim3", "update_net_psk": "WlanGeheim4"}
PW = "Theater-Sicherung-2026"


def _set(key, value):
    from app import core
    core.rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))


def _st():
    from app import core
    return core.rt.store


def _settings_of(db: bytes) -> dict:
    con = sqlite3.connect(":memory:")
    con.deserialize(backup.no_wal(db))
    try:
        return dict(con.execute("SELECT key, value FROM settings").fetchall())
    finally:
        con.close()


def _download(client, pw: str = PW) -> bytes:
    r = client.post("/api/admin/backup", auth=AUTH, json={"password": pw})
    assert r.status_code == 200, r.text
    return r.content


def _restore(client, data: bytes, pw: str = "", name: str = "s.festpos"):
    return client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": (name, data)},
                       data={"password": pw} if pw else None)


def _same_system():
    """Laufende Datenbank mit einem Gerätezertifikat: «gleiches System» beim Einspielen."""
    _st().execute("INSERT INTO device_certs(name, serial, created_at) VALUES ('XCover 9', 'S-LAUFEND', '2026-10-01T10:00:00')")


def test_download_is_encrypted_with_everything(client):
    for k, v in SECRETS.items():
        _set(k, v)
    _set("event_name", "Theaterfest")
    assert client.post("/api/admin/backup", auth=AUTH, json={"password": "kurz"}).status_code == 422
    data = _download(client)
    head = backup.read_header(data)
    assert head and head["format"] == 1 and head["node"] == "local"
    for plain in (*SECRETS.values(), "Theaterfest", PW, _st().setting("backup_password")):
        assert plain.encode() not in data                         # nichts im Klartext
    content = backup.unpack(data, PW)
    cfg = _settings_of(content["festpos.sqlite"])
    from app import secretbox                    # in der Datenbank evtl. schon verschlüsselt (migrate, T-0-N/FestPOS#8)
    assert cfg["event_name"] == "Theaterfest" and all(secretbox.unseal(k, cfg[k]) == v for k, v in SECRETS.items())
    assert json.loads(content["conf.json"])["admin_password"] == "geheim"
    with pytest.raises(backup.BadPassword):
        backup.unpack(data, PW + "x")
    tampered = data[:-5] + bytes([data[-5] ^ 1]) + data[-4:]
    with pytest.raises(backup.BadPassword):                      # veränderte Datei fällt auf (GCM)
        backup.unpack(tampered, PW)


def test_generated_password_in_any_spelling():
    pw = backup.new_password()
    data = backup.pack({"festpos.sqlite": b"x"}, pw, kdf={"n": 1024, "r": 8, "p": 1})
    assert backup.unpack(data, pw.lower().replace("-", " "))["festpos.sqlite"] == b"x"
    assert len(pw) == 29 and backup.PW_RE.match(pw.replace("-", ""))


def test_password_created_once_and_restore_with_own_needs_no_typing(client):
    from app import maintenance
    own = _st().setting("backup_password")
    assert own and backup.PW_RE.match(own.replace("-", ""))
    maintenance.ensure_password()
    assert _st().setting("backup_password") == own                # bleibt
    info = client.get("/api/admin/backup/password", auth=AUTH).json()
    assert info["password"] is None and info["prefix"] == own[:4] and info["printed"] is None
    assert client.get("/api/admin/backup/password?show=1", auth=AUTH).json()["password"] == own
    assert "backup_pw_unprinted" in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]
    client.post("/api/admin/backup/password/printed", auth=AUTH)
    assert "backup_pw_unprinted" not in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]
    _set("event_name", "Stick")
    data = maintenance.make_backup(own)                           # wie die automatische Sicherung
    _set("event_name", "anders")
    r = _restore(client, data)
    assert r.status_code == 200, r.text
    assert _st().setting("event_name") == "Stick"
    # neues Passwort: das frühere probiert der Pi weiterhin selbst
    new = client.post("/api/admin/backup/password/new?confirm=JA", auth=AUTH).json()
    assert new["has_old"] and new["printed"] is None and _st().setting("backup_password") != own
    assert _restore(client, data).status_code == 200


def test_foreign_password_needed_and_wrong_rejected(client):
    data = _download(client)
    r = _restore(client, data)
    assert r.status_code == 428 and r.json()["detail"]
    assert _restore(client, data, "falsches-passwort-1").status_code == 422
    assert _restore(client, data, PW).status_code == 200
    assert _restore(client, b"irgendwas").status_code == 422


def test_same_system_keeps_running_secrets_and_password(client):
    _same_system()
    for k, v in SECRETS.items():
        _set(k, v)
    own = _st().setting("backup_password")
    data = _download(client)
    _set("fully_password", "Fully-Neu")
    _set("event_name", "anders")
    r = _restore(client, data, PW)
    assert r.status_code == 200 and r.json()["case"] == "same"
    assert _st().setting("fully_password") == "Fully-Neu"         # vom laufenden System
    assert _st().setting("update_net_token") == SECRETS["update_net_token"]
    assert _st().setting("backup_password") == own


def test_new_pi_takes_secrets_from_backup(client):
    _set("fully_password", "aus-der-Sicherung")
    _set("backup_password_printed", str(int(time.time())))
    data = _download(client)
    _st().execute("DELETE FROM settings WHERE key IN ('fully_password', 'backup_password_printed')")
    _set("backup_password", backup.new_password())               # Ersatzkarte hat ihr eigenes erzeugt
    r = _restore(client, data, PW)
    assert r.status_code == 200 and r.json()["case"] == "new"
    assert _st().setting("fully_password") == "aus-der-Sicherung"
    assert _st().setting("backup_password_printed")               # das ausgedruckte gilt wieder


def test_old_unencrypted_backup_still_restores(client):
    """Sicherung aus einer früheren Version (SQLite-Datei) auf einer neuen Karte."""
    _set("uplink_password", "aus-alter-Sicherung")
    data = _st().backup()
    _st().execute("DELETE FROM settings WHERE key='uplink_password'")
    r = _restore(client, data, name="s.sqlite")
    assert r.status_code == 200, r.text
    assert _st().setting("uplink_password") == "aus-alter-Sicherung"
    assert r.json()["keys"] == "unknown"


def test_revocations_and_new_certs_survive_older_backup(client):
    st = _st()
    st.execute("INSERT INTO device_certs(name, serial, created_at) VALUES ('XCover 1', 'S1', '2026-10-01T10:00:00')")
    data = _download(client)
    st.execute("UPDATE device_certs SET revoked_at='2026-10-02T10:00:00' WHERE serial='S1'")   # seither gesperrt
    st.execute("INSERT INTO device_certs(name, serial, created_at, device_id) VALUES ('XCover 2', 'S2', "
               "'2026-10-02T11:00:00', 'S2')")
    st.execute("INSERT INTO device_role_ids(device_id, role) VALUES ('S2', 'storno')")
    r = _restore(client, data, PW)
    assert r.status_code == 200 and r.json()["case"] == "same"
    assert st.scalar("SELECT revoked_at FROM device_certs WHERE serial='S1'") == "2026-10-02T10:00:00"
    assert st.scalar("SELECT name FROM device_certs WHERE serial='S2'") == "XCover 2"
    assert st.scalar("SELECT role FROM device_role_ids WHERE device_id='S2'") == "storno"
    from app import devices
    assert "S1" in devices.revoked_serials()
    # nie eine Sperre aufheben: in der Sicherung gesperrt, jetzt (fälschlich) offen -> bleibt gesperrt
    st.execute("UPDATE device_certs SET revoked_at=NULL WHERE serial='S1'")
    data2 = _download(client)
    st.execute("UPDATE device_certs SET revoked_at='2026-10-03T10:00:00' WHERE serial='S1'")
    data3 = _download(client)
    st.execute("UPDATE device_certs SET revoked_at=NULL WHERE serial='S1'")
    assert _restore(client, data3, PW).status_code == 200
    assert st.scalar("SELECT revoked_at FROM device_certs WHERE serial='S1'") == "2026-10-03T10:00:00"
    assert data2


def test_pi_state_not_from_backup(client):
    _set("node_info.a", '{"seen": 1}')
    _set("printer_state", '{"at": 1}')
    data = _download(client)
    _set("node_info.a", '{"seen": 2}')
    _st().execute("DELETE FROM settings WHERE key='printer_state'")
    assert _restore(client, data, PW).status_code == 200
    assert _st().setting("node_info.a") == '{"seen": 2}' and _st().setting("printer_state", None) is None


def _receipt(no: int):
    _st().execute("INSERT INTO receipts(no, request_id, order_id, order_uuid, lines, total_cents, data, created_at) "
                  "VALUES (?, ?, 1, ?, '[]', 100, '{}', '2026-10-01T10:00:00')", (no, f"r{no}", f"u{no}"))


def test_receipt_numbers_continue_on_same_system(client):
    _same_system()
    _receipt(1295)
    data = _download(client)
    _receipt(1296)
    _receipt(1297)                                                 # nach der Sicherung (auf Papier beim Gast)
    assert _restore(client, data, PW).status_code == 200
    rc = client.get("/api/admin/restore-check", auth=AUTH).json()
    item = next(i for i in rc["items"] if i["id"] == "receipts")
    assert item["next"] == 1298 and item["last_backup"] == 1295   # ohne Lücke weiter, keine doppelte Nummer


def test_receipt_numbers_jump_on_new_pi(client):
    _receipt(1295)
    data = _download(client)
    _st().execute("DELETE FROM receipts")
    r = _restore(client, data, PW)
    assert r.status_code == 200 and r.json()["case"] == "new"
    rc = client.get("/api/admin/restore-check", auth=AUTH).json()
    item = next(i for i in rc["items"] if i["id"] == "receipts")
    assert item["next"] == 1400 and item["state"] == "warn"
    assert client.post("/api/admin/restore-check/receipt-next", auth=AUTH, json={"next": 1350}).status_code == 422
    rc = client.post("/api/admin/restore-check/receipt-next", auth=AUTH, json={"next": 2000}).json()
    assert next(i for i in rc["items"] if i["id"] == "receipts")["next"] == 2000
    gaps = [e for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"] if e["action"] == "receipt_gap"]
    assert len(gaps) == 2


def test_receipt_floor_used_for_next_number(client):
    from app import receipts
    _set("receipt_no_floor", "1399")
    assert receipts.next_no(_st()) == 1400
    _receipt(1500)
    assert receipts.next_no(_st()) == 1501


def test_checklist_and_overview(client):
    data = _download(client)
    assert client.get("/api/admin/restore-check", auth=AUTH).json() == {"active": False}
    r = _restore(client, data, PW)
    assert r.json()["check"] is True
    rc = client.get("/api/admin/restore-check", auth=AUTH).json()
    assert rc["active"] and rc["case"] == "new" and rc["encrypted"] and rc["show_overview"]
    ids = {i["id"] for i in rc["items"]}
    assert {"device_ca_key", "time", "secrets", "ledger_key"} <= ids
    assert {m["id"] for m in rc["manual"]} >= {"m_test", "m_orders", "m_try", "m_new"}
    stats = client.get("/api/admin/stats", auth=AUTH).json()
    assert stats["restore"] is not None
    done = client.post("/api/admin/restore-check/done", auth=AUTH).json()
    assert done["done"] and not done["show_overview"]
    assert client.get("/api/admin/stats", auth=AUTH).json()["restore"] is None


def test_restore_check_endpoints_admin_only(client):
    from tests.test_users import PW as UPW, _hdr
    client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": UPW})
    h = _hdr(client, "Lea")
    for method, path in (("get", "/api/admin/restore-check"), ("get", "/api/admin/backup/password"),
                         ("post", "/api/admin/backup"), ("get", "/api/admin/cluster/expand")):
        assert getattr(client, method)(path, headers=h).status_code == 403, path


def test_rotation_only_own_files():
    names = [backup.file_name("a", 1_800_000_000 + i * 900) for i in range(25)]
    names += [backup.file_name("b", 1_800_000_000), "notizen.txt", "festpos-a-kaputt.festpos",
              "festpos-a-20200101-120000.sqlite"]                  # ältere Sicherung dieses Pi: zählt mit
    gone = backup.to_delete(names, "a")
    assert len(gone) == 6 and all(n.startswith("festpos-a-") for n in gone)
    assert "festpos-a-20200101-120000.sqlite" in gone


def test_warnings():
    now = 1_800_000_000
    ok = {"state": "ok", "at": now - 60, "last_ok": now - 60, "file": "f.festpos"}
    assert backup.warnings({"a": None}, single=True, now=now) == (["backup_none"], None)
    assert backup.warnings({"a": None}, single=False, now=now) == ([], None)              # Cluster: kein Muss
    w, newest = backup.warnings({"a": ok}, single=True, now=now)
    assert w == [] and newest == {"node": "a", "at": now - 60, "file": "f.festpos"}
    old = {"state": "no_stick", "at": now, "last_ok": now - 2 * 3600, "file": "f.festpos"}
    assert backup.warnings({"a": old}, single=True, now=now)[0] == ["backup_old"]
    assert backup.warnings({"a": old}, single=False, now=now)[0] == []
    bad = {"state": "failed", "at": now - 30, "code": "full", "last_ok": now - 900}
    assert backup.warnings({"a": bad, "b": ok}, single=False, now=now)[0] == ["backup_failed"]
    assert backup.warnings({"a": bad}, single=True, now=now)[0] == ["backup_failed"]


def test_conf_read_and_write_like_shell(tmp_path):
    """backup.conf_set schreibt, was festpos_read_conf (Shell) genau so wieder liest."""
    text = 'CLUSTER_SIZE=1\nADMIN_PASSWORD=alt # Kommentar\nVIP=192.168.55.10\n'
    value = 'a"b$c\\d e # f `x`'
    new = backup.conf_set(backup.conf_set(text, "ADMIN_PASSWORD", value), "CLUSTER_SECRET", "auto-abc")
    assert backup.conf_value(new, "ADMIN_PASSWORD") == value and backup.conf_value(new, "VIP") == "192.168.55.10"
    (tmp_path / "f.conf").write_text(new)
    out = subprocess.run(["bash", "-c", f'source "{ROOT}/deploy/festpos-conf.sh"; festpos_read_conf "{tmp_path}/f.conf"; '
                          'printf "%s|%s" "$ADMIN_PASSWORD" "$CLUSTER_SECRET"'], capture_output=True, text=True)
    assert out.stdout == f"{value}|auto-abc", out.stderr


def _helper_env(tmp_path, db: Path) -> dict:
    (tmp_path / "env").write_text('FESTPOS_NODE="a"\nFESTPOS_ADMIN_PASSWORD="Notfall-Passwort-1"\n'
                                  'FESTPOS_CLUSTER_SECRET="bitte-aendern"\n')
    tls = tmp_path / "tls"
    tls.mkdir(exist_ok=True)
    (tls / "server.key").write_bytes(b"-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----\n")
    (tls / "ca.crt").write_bytes(b"CA")
    boot = tmp_path / "boot"
    boot.mkdir(exist_ok=True)
    (boot / "festpos-update.pub").write_text("ssh-ed25519 AAAA test")
    return {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_SICHERUNG_DATEI": str(db),
            "FESTPOS_SICHERUNG_STATUS": str(tmp_path / "status" / "status.json"), "FESTPOS_NODE_ENV": str(tmp_path / "env"),
            "FESTPOS_SICHERUNG_TLS": str(tls), "FESTPOS_BOOT": str(boot), "FESTPOS_FULLY_DIR": str(tmp_path / "fully"),
            "FESTPOS_DIENST_USER": ""}


def _run_helper(env, *args):
    return subprocess.run([sys.executable, "-I", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=120)


def test_helper_writes_encrypted_folder_and_status(tmp_path):
    from app.store import SQLiteStore
    db = tmp_path / "quelle.db"
    st = SQLiteStore(db)
    st.init_schema()
    st.execute("INSERT INTO settings(key,value) VALUES ('fully_password', 'Fully-Geheim-1')")
    stick = tmp_path / "stick" / backup.STICK_DIR
    stick.mkdir(parents=True)
    (stick / "festpos-b-20260101-120000.festpos").write_bytes(b"x")            # anderer Pi: bleibt
    env = _helper_env(tmp_path, db)
    # ohne Sicherungs-Passwort: nichts schreiben, nie unverschlüsselt
    r = _run_helper(env, "--ordner", str(stick))
    assert r.returncode == 2 and len(list(stick.iterdir())) == 1
    assert json.loads((tmp_path / "status" / "status.json").read_text())["code"] == "password"
    pw = backup.new_password()
    st.execute("INSERT INTO settings(key,value) VALUES ('backup_password', ?)", (pw,))
    (tmp_path / "fully").mkdir()
    (tmp_path / "fully" / "fully.apk").write_bytes(b"APK-INHALT")
    (tmp_path / "fully" / "fully.json").write_text(json.dumps({"checksum": "abc", "name": "Fully 1.61"}))
    r = _run_helper(env, "--ordner", str(stick))
    assert r.returncode == 0, r.stderr
    mine = sorted(p.name for p in stick.iterdir() if p.name.startswith("festpos-a-"))
    assert len(mine) == 1 and mine[0].endswith(".festpos")
    data = (stick / mine[0]).read_bytes()
    assert b"Fully-Geheim-1" not in data and backup.read_header(data)["node"] == "a"
    content = backup.unpack(data, pw)
    assert _settings_of(content["festpos.sqlite"])["fully_password"] == "Fully-Geheim-1"
    conf = json.loads(content["conf.json"])
    assert conf["admin_password"] == "Notfall-Passwort-1" and conf["cluster_secret"].startswith("auto-")
    assert "tls/server.key" in content and "update-keys/festpos-update.pub" in content
    assert (stick / "fully.apk").read_bytes() == b"APK-INHALT"                  # APK einmal daneben
    status = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status["state"] == "ok" and status["file"] == mine[0] and abs(status["last_ok"] - time.time()) < 60
    # Datenbank nicht lesbar: «failed», letzte gute Sicherung bleibt im Status
    env["FESTPOS_SICHERUNG_DATEI"] = str(tmp_path / "fehlt.db")
    assert _run_helper(env, "--ordner", str(stick)).returncode == 2
    status2 = json.loads((tmp_path / "status" / "status.json").read_text())
    assert status2["state"] == "failed" and status2["code"] == "db" and status2["last_ok"] == status["last_ok"]


def test_helper_fetches_apk_from_stick(tmp_path):
    from app import fully
    stick = tmp_path / "stick"
    stick.mkdir()
    (stick / "fully.apk").write_bytes(b"APK-VOM-STICK")
    env = _helper_env(tmp_path, tmp_path / "egal.db")
    (stick / "fully.json").write_text(json.dumps({"checksum": "falsch"}))
    assert _run_helper(env, "--apk-holen", str(stick)).returncode == 3          # Prüfsumme passt nicht
    (stick / "fully.json").write_text(json.dumps({"checksum": fully.apk_checksum(stick / "fully.apk"), "name": "x"}))
    assert _run_helper(env, "--apk-holen", str(stick)).returncode == 0
    assert (tmp_path / "fully" / "fully.apk").read_bytes() == b"APK-VOM-STICK"


def test_helper_never_follows_links_in_the_apk_folder(tmp_path):
    """Review 2026-11, S3: der APK-Ordner gehört dem Dienstbenutzer. Der root-Helfer schreibt dort nie über eine
    Verknüpfung und liest über keine (sonst käme der Inhalt einer fremden Datei auf den Stick)."""
    from app import fully
    stick = tmp_path / "stick"
    stick.mkdir()
    (stick / "fully.apk").write_bytes(b"APK-VOM-STICK")
    (stick / "fully.json").write_text(json.dumps({"checksum": fully.apk_checksum(stick / "fully.apk"), "name": "x"}))
    env = _helper_env(tmp_path, tmp_path / "egal.db")
    apk_dir = tmp_path / "fully"
    apk_dir.mkdir()
    fremd = tmp_path / "fremd"
    fremd.write_bytes(b"GEHEIM")
    # Ziel ist eine Verknüpfung: sie wird ersetzt (umbenennen), die fremde Datei bleibt, wie sie ist
    (apk_dir / "fully.apk").symlink_to(fremd)
    (apk_dir / ".fully.apk.neu").symlink_to(fremd)
    assert _run_helper(env, "--apk-holen", str(stick)).returncode == 0
    assert fremd.read_bytes() == b"GEHEIM"
    assert not (apk_dir / "fully.apk").is_symlink() and (apk_dir / "fully.apk").read_bytes() == b"APK-VOM-STICK"
    # Quelle ist eine Verknüpfung: nichts davon kommt auf den Stick
    from app.store import SQLiteStore
    db = tmp_path / "quelle.db"
    st = SQLiteStore(db)
    st.init_schema()
    st.execute("INSERT INTO settings(key,value) VALUES ('backup_password', ?)", (backup.new_password(),))
    env = _helper_env(tmp_path, db)
    (apk_dir / "fully.apk").unlink()
    (apk_dir / "fully.apk").symlink_to(fremd)
    (apk_dir / "fully.json").write_text(json.dumps({"checksum": "neu", "name": "x"}))
    ziel = tmp_path / "stick2" / backup.STICK_DIR
    ziel.mkdir(parents=True)
    assert _run_helper(env, "--ordner", str(ziel)).returncode == 0            # die Sicherung selbst ist geschrieben
    assert not (ziel / "fully.apk").exists()
    assert all(b"GEHEIM" != p.read_bytes() for p in ziel.iterdir() if p.is_file())


def test_overview_shows_backup(client, tmp_path, monkeypatch):
    from app import system
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"state": "ok", "at": int(time.time()), "last_ok": int(time.time()) - 120,
                                  "file": "festpos-local-20261021-101500.festpos", "size": 1234}))
    monkeypatch.setattr(backup, "STATUS_FILE", status)
    monkeypatch.setattr(backup, "HELPER", status)                  # «Helfer installiert»
    system._published.update(at=0.0, static=None)
    system.publish()
    s = client.get("/api/admin/stats", auth=AUTH).json()
    assert s["backup"]["node"] == "local" and "backup_none" not in s["warnings"]
    st = client.get("/api/status", auth=AUTH).json()
    assert not {"backup_none", "backup_old"} & set(st["warnings"])
    status.write_text(json.dumps({"state": "no_stick", "at": int(time.time())}))
    system._published.update(at=0.0, static=None)
    system.publish()
    assert "backup_none" in client.get("/api/admin/stats", auth=AUTH).json()["warnings"]


def test_read_tool_unpacks(tmp_path):
    """tools/sicherung_lesen.py: Sicherung am PC entschlüsseln und entpacken."""
    data = backup.pack(backup.members(b"SQLite format 3\x00rest", None, {"cluster_secret": "x"}), PW,
                       node="a", kdf={"n": 1024, "r": 8, "p": 1})
    (tmp_path / "s.festpos").write_bytes(data)
    tool = ROOT / "tools" / "sicherung_lesen.py"
    r = subprocess.run([sys.executable, str(tool), str(tmp_path / "s.festpos"), str(tmp_path / "aus")], input="falsch\n",
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and not (tmp_path / "aus").exists()
    r = subprocess.run([sys.executable, str(tool), str(tmp_path / "s.festpos"), str(tmp_path / "aus")], input=PW + "\n",
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "aus" / "festpos.sqlite").read_bytes().startswith(b"SQLite") and (tmp_path / "aus" / "conf.json").exists()


def test_mixed_stick_folder(client, tmp_path):
    """T17: Im Ordner festpos-sicherung liegen neben den automatischen Sicherungen auch heruntergeladene (eigenes
    Passwort) und solche anderer Pi. Das Aufräumen lässt fremde und heruntergeladene Dateien in Ruhe, die Dateien sind
    unterscheidbar (Name und Kopf), und die automatische von vor der Sperre spielt sich ohne Passwort ein."""
    from app import core, maintenance
    own = _st().setting("backup_password")
    manual = _download(client)
    r = client.post("/api/admin/backup", auth=AUTH, json={"password": PW})
    name = r.headers["content-disposition"].split('filename="')[1].rstrip('"')
    assert name.startswith("festpos-manuell-local-") and not backup.FILE_RE.match(name)
    assert backup.read_header(manual)["kind"] == "manual"
    _set("event_name", "vor der Sperre")
    auto = maintenance.make_backup(own, kind="auto")
    assert backup.read_header(auto)["kind"] == "auto"
    names = [backup.file_name("local", 1_800_000_000 + i * 900) for i in range(22)]
    names += [name, "festpos-sicherung-20261031-1930.festpos", backup.file_name("b", 1_800_000_000), "fully.apk"]
    gone = backup.to_delete(names, "local")
    assert len(gone) == 2 and all(backup.FILE_RE.match(n) and "-local-" in n for n in gone)
    # Sperre, danach die automatische Sicherung von vorher: kein Passwort, das Gerät bleibt gesperrt
    core.rt.store.execute("INSERT INTO device_certs(name, serial, created_at) VALUES ('XCover', 'S1', '2026-10-01T10:00:00')")
    core.rt.store.execute("UPDATE device_certs SET revoked_at='2026-10-31T19:00:00' WHERE serial='S1'")
    _set("event_name", "nachher")
    r = _restore(client, auto)
    assert r.status_code == 200, r.text
    assert _st().setting("event_name") == "vor der Sperre"
    assert core.rt.store.scalar("SELECT revoked_at FROM device_certs WHERE serial='S1'") is not None
    assert _restore(client, manual).status_code == 428                          # heruntergeladen: eigenes Passwort
