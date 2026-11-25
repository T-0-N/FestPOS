"""Notfall-Passwort nur als Prüfwert, Mindestlänge 16, nur aus dem FestPOS-Netz (T-0-N/FestPOS#15)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from starlette.testclient import TestClient

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
LONG = "Tannen-Laterne-74-Kiesweg"


def test_hash_same_on_all_pis_and_per_installation():
    from app import emergency
    h = emergency.make_hash(LONG, "s" * 40)
    assert h == emergency.make_hash(LONG, "s" * 40)                    # alle Pi rechnen dasselbe
    assert h != emergency.make_hash(LONG, "t" * 40)                    # andere Installation: anderes Salz
    assert emergency.check(LONG, h) and not emergency.check("falsch", h) and LONG not in h


def test_conf_converted_and_plain_text_removed(tmp_path):
    """festpos-node beim Start: Klartext -> Prüfwert und Länge, Klartext weg; Platzhalter bleibt (gesperrt)."""
    from app import backup, emergency
    conf = tmp_path / "festpos.conf"
    conf.write_text(f"# Kommentar\nVIP=192.168.55.10\nADMIN_PASSWORD=\"{LONG}\"\nCLUSTER_SECRET={'k' * 40}\n")
    r = subprocess.run([sys.executable, "-m", "app.emergency", "--conf", str(conf)], cwd=ROOT, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    text = conf.read_text()
    assert LONG not in text and "# Kommentar" in text and backup.conf_value(text, "ADMIN_PASSWORD") is None
    assert backup.conf_value(text, "ADMIN_PASSWORD_HASH") == emergency.make_hash(LONG, "k" * 40)
    assert backup.conf_value(text, "ADMIN_PASSWORD_LAENGE") == str(len(LONG))
    assert subprocess.run([sys.executable, "-m", "app.emergency", "--conf", str(conf)], cwd=ROOT).returncode == 0
    assert conf.read_text() == text                                   # nochmals: nichts mehr zu tun
    assert emergency.convert_text("ADMIN_PASSWORD=bitte-aendern\n", "x") is None
    # neues Passwort von Hand eingetragen: ersetzt den alten Prüfwert
    new = emergency.convert_text(text + "ADMIN_PASSWORD=Ganz-neues-Passwort-2027\n", "k" * 40)
    assert emergency.check("Ganz-neues-Passwort-2027", backup.conf_value(new, "ADMIN_PASSWORD_HASH"))


def test_login_with_hash_minimum_length_and_lock(client, monkeypatch):
    from app import core, emergency, main
    monkeypatch.setattr(core, "PW_WARN_LEN", 16)
    monkeypatch.setattr(core.Config, "cluster_secret", "c" * 40)
    monkeypatch.setattr(core.Config, "admin_password", "")
    monkeypatch.setattr(core.Config, "admin_password_hash", emergency.make_hash(LONG, "c" * 40))
    monkeypatch.setattr(core.Config, "admin_password_len", len(LONG))
    core.login_throttle.clear()
    assert client.post("/api/admin/login", json={"name": "admin", "password": LONG}).status_code == 200
    assert client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).status_code == 401
    assert client.get("/api/admin/stats", auth=("admin", LONG)).status_code == 200       # Basic Auth ebenso
    audit = client.get("/api/admin/audit", auth=("admin", LONG)).json()
    rows = audit if isinstance(audit, list) else audit.get("entries") or audit.get("rows") or []
    assert any("emergency_login" in str(r) for r in rows)                                 # im Protokoll
    # kürzer als 16: ohne eigenen Admin nur Warnung, mit eigenem Admin gesperrt
    short = "kurz-aber-ok-12"
    monkeypatch.setattr(core.Config, "admin_password_hash", emergency.make_hash(short, "c" * 40))
    monkeypatch.setattr(core.Config, "admin_password_len", len(short))
    core.login_throttle.clear()
    assert core.emergency_problem() == "short"
    assert client.get("/api/admin/stats", auth=("admin", short)).status_code == 200
    r = client.post("/api/admin/users", auth=("admin", short), json={"name": "Chefin", "role": "admin",
                                                                      "password": "chefin-pass-123"})
    assert r.status_code == 200, r.text
    from app import auth
    auth.forget()
    assert core.emergency_problem() == "short_locked"
    r = client.post("/api/admin/login", json={"name": "admin", "password": short})
    assert r.status_code == 401 and "16 Zeichen" in r.json()["detail"]
    assert "admin_pw_short_locked" in client.get("/api/admin/stats", auth=("Chefin", "chefin-pass-123")).json()["warnings"]
    assert main.APP_VERSION


def test_emergency_only_from_festpos_network(client, monkeypatch):
    from app import core, emergency
    core.login_throttle.clear()
    assert emergency.local_ok("127.0.0.1") and emergency.local_ok("192.168.55.200") and not emergency.local_ok("8.8.8.8")
    monkeypatch.setattr(core.Config, "node_ip", "10.20.0.11")
    monkeypatch.setenv("FESTPOS_NET_CIDR", "22")
    assert emergency.local_ok("10.20.3.4") and not emergency.local_ok("192.168.55.200")
    outside = TestClient(client.app, client=("8.8.8.8", 4000))
    r = outside.post("/api/admin/login", json={"name": "admin", "password": "geheim"})
    assert r.status_code == 401 and "FestPOS-Netz" in r.json()["detail"]
    assert outside.get("/api/admin/stats", auth=AUTH).status_code == 401
    core.login_throttle.clear()


def test_backup_carries_hash_not_plain_text():
    from app import backup, emergency
    h = emergency.make_hash(LONG, "c" * 40)
    text = backup.conf_admin("ADMIN_PASSWORD=alt\nVIP=1\n", {"admin_password_hash": h, "admin_password_len": "25"})
    assert backup.conf_value(text, "ADMIN_PASSWORD") is None and backup.conf_value(text, "ADMIN_PASSWORD_HASH") == h
    old = backup.conf_admin("VIP=1\n", {"admin_password": "aus-alter-Sicherung"})              # ältere Sicherung
    assert backup.conf_value(old, "ADMIN_PASSWORD") == "aus-alter-Sicherung"
    assert backup.conf_admin("VIP=1\n", {"admin_password_hash": "kaputt"}) == "VIP=1\n"
