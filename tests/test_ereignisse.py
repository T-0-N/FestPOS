"""Sicherheitsereignisse (T-0-N/FestPOS#11): der root-Dienst sammelt SSH, sudo, Neustart und Boot-Partition, die App
trägt sie ins Protokoll ein, die Übersicht zeigt sie bis «Gesehen» – dazu Anmeldesperre und Notfall-Login."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-ereignisse"
US = 1_800_000_000_000_000                               # Zeit im Journal (Mikrosekunden)


def _line(ident, msg, n):
    return {"SYSLOG_IDENTIFIER": ident, "MESSAGE": msg, "__CURSOR": f"c{n}", "__REALTIME_TIMESTAMP": str(US + n)}


@pytest.fixture
def pi(tmp_path):
    """Nachgebildeter Pi: Boot-Partition, boot_id, Journal (journalctl gibt aus, was in journal.jsonl steht)."""
    r = tmp_path / "root"
    (r / "boot" / "firmware" / "overlays").mkdir(parents=True)
    (r / "boot" / "firmware" / "config.txt").write_text("dtoverlay=disable-bt\n")
    (r / "boot" / "firmware" / "cmdline.txt").write_text("console=tty1 root=PARTUUID=1\n")
    (r / "boot" / "firmware" / "overlays" / "x.dtbo").write_bytes(b"\0" * 100)
    (r / "proc" / "sys" / "kernel" / "random").mkdir(parents=True)
    (r / "proc" / "sys" / "kernel" / "random" / "boot_id").write_text("boot-1\n")
    journal = tmp_path / "journal.jsonl"
    journal.write_text("")
    fake = tmp_path / "journalctl"
    fake.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/args\ncat {journal}\n: > {journal}\n')
    fake.chmod(0o755)
    env = {**os.environ, "FESTPOS_TEST_ROOT": str(r), "FESTPOS_JOURNALCTL": str(fake), "FESTPOS_DEST": str(ROOT),
           "FESTPOS_EREIGNISSE_DIR": str(r / "var" / "lib" / "festpos-ereignisse")}
    env.pop("FESTPOS_BOOT", None)

    def run(*lines):
        journal.write_text("".join(json.dumps(x) + "\n" for x in lines))
        res = subprocess.run([sys.executable, "-I", str(HELPER)], env=env, capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, res.stderr
        return json.loads((r / "var" / "lib" / "festpos-ereignisse" / "ereignisse.json").read_text())
    return {"r": r, "run": run, "args": tmp_path / "args", "dir": r / "var" / "lib" / "festpos-ereignisse"}


def test_collector_ssh_sudo_reboot_and_boot_partition(pi):
    d = pi["run"](_line("sshd", "Accepted publickey for pi from 192.168.55.201 port 51234 ssh2: ED25519 SHA256:x", 1),
                  _line("sshd", "Connection closed by 10.0.0.1 port 22", 2),
                  _line("sudo", "     pi : TTY=pts/0 ; PWD=/home/pi ; USER=root ; COMMAND=/usr/bin/systemctl restart festpos", 3),
                  _line("sudo", "pi : 3 incorrect password attempts ; TTY=pts/0 ; PWD=/home/pi ; USER=root ; COMMAND=/bin/sh", 4),
                  _line("sshd-session", "Accepted password for root from 10.1.2.3 port 5 ssh2", 5))
    ev = d["events"]
    assert [e["kind"] for e in ev] == ["ssh", "sudo", "sudo_failed", "ssh"]              # erster Lauf: kein Neustart
    assert ev[0]["user"] == "pi" and ev[0]["ip"] == "192.168.55.201" and ev[0]["at"] == US // 1_000_000
    assert ev[1]["cmd"] == "/usr/bin/systemctl restart festpos" and ev[1]["as"] == "root"
    assert ev[2]["count"] == 3 and ev[3]["user"] == "root" and [e["seq"] for e in ev] == [1, 2, 3, 4]
    assert "--since" in pi["args"].read_text()
    # für die App lesbar, der Zustand nur für root
    assert stat.S_IMODE((pi["dir"] / "ereignisse.json").stat().st_mode) == 0o644
    assert stat.S_IMODE((pi["dir"] / "stand").stat().st_mode) == 0o700
    # nächster Lauf: ab dem Cursor, nichts Neues; Änderung im Betrieb (FestPOS selbst) still übernommen
    boot = pi["r"] / "boot" / "firmware"
    (boot / "config.txt").write_text("dtoverlay=disable-bt\n# FestPOS\n")
    assert pi["run"]()["seq"] == 4
    assert "--after-cursor c5" in pi["args"].read_text().splitlines()[-1]
    # Neustart, dazwischen die Karte an einem anderen Rechner geändert
    (pi["r"] / "proc" / "sys" / "kernel" / "random" / "boot_id").write_text("boot-2\n")
    (boot / "cmdline.txt").write_text("console=tty1 root=PARTUUID=1 init=/bin/sh\n")
    (boot / "neu.txt").write_text("x")
    (boot / "overlays" / "x.dtbo").unlink()
    ev = pi["run"]()["events"][4:]
    assert [e["kind"] for e in ev] == ["reboot", "boot_changed"]
    assert ev[1]["files"] == ["cmdline.txt", "+neu.txt", "-overlays/x.dtbo"] and ev[1]["count"] == 3
    # gleicher Start, nochmals: nichts
    assert len(pi["run"]()["events"]) == 6
    # am PC geändert, Grösse und Zeit gleich gelassen: nach dem Neustart trotzdem erkannt (Prüfsumme neu)
    import os
    st = (boot / "config.txt").stat()
    (boot / "config.txt").write_text("dtoverlay=enable-bt\n# FestPOS\n")
    os.utime(boot / "config.txt", ns=(st.st_atime_ns, st.st_mtime_ns))
    (pi["r"] / "proc" / "sys" / "kernel" / "random" / "boot_id").write_text("boot-3\n")
    ev = pi["run"]()["events"][6:]
    assert [e["kind"] for e in ev] == ["reboot", "boot_changed"] and ev[1]["files"] == ["config.txt"]


def test_append_keeps_the_last_ones(tmp_path, monkeypatch):
    from app import ereignisse
    monkeypatch.setattr(ereignisse, "DIR", tmp_path)
    monkeypatch.setattr(ereignisse, "OUT", tmp_path / "ereignisse.json")
    monkeypatch.setattr(ereignisse, "KEEP", 5)
    for i in range(8):
        ereignisse.append({"kind": "ssh", "user": f"u{i}"})
    d = ereignisse.read(tmp_path / "ereignisse.json")
    assert d["seq"] == 8 and [e["seq"] for e in d["events"]] == [4, 5, 6, 7, 8] and d["run"]
    assert ereignisse.read(tmp_path / "fehlt.json")["events"] == []


def _file(path: Path, run: str, events: list[dict]) -> None:
    path.write_text(json.dumps({"run": run, "seq": max([e["seq"] for e in events] or [0]), "events": events}))


def test_app_imports_shows_and_seen(client, tmp_path, monkeypatch):
    from app import core, sicherheit
    f = tmp_path / "ereignisse.json"
    monkeypatch.setattr(sicherheit, "FILE", f)
    assert sicherheit.import_new() == 0                                              # noch keine Datei
    _file(f, "r1", [{"seq": 1, "at": 1, "kind": "ssh", "user": "pi", "ip": "192.168.55.201"},
                    {"seq": 2, "at": 2, "kind": "boot_changed", "files": ["config.txt"], "count": 1},
                    {"seq": 3, "at": 3, "kind": "unbekannt"}])
    assert sicherheit.import_new() == 2 and sicherheit.import_new() == 0             # nur einmal
    s = client.get("/api/admin/security", auth=AUTH).json()
    assert s["count"] == 2 and [e["action"] for e in s["events"]] == ["sec_boot_changed", "sec_ssh"]
    assert s["events"][1]["details"]["ip"] == "192.168.55.201" and s["events"][1]["details"]["node"] == core.Config.node
    assert s["events"][1]["actor"].startswith("Pi ")
    assert client.post(f"/api/admin/security/seen?upto={s['last']}", auth=AUTH).status_code == 200
    assert client.get("/api/admin/security", auth=AUTH).json()["count"] == 0         # Karte weg
    # etwas Neues: Karte wieder da
    _file(f, "r1", [{"seq": 4, "at": 4, "kind": "reboot"}])
    sicherheit.import_new()
    assert client.get("/api/admin/security", auth=AUTH).json()["count"] == 1
    # Datei neu entstanden (Neuinstallation): zählt von vorne
    _file(f, "r2", [{"seq": 1, "at": 5, "kind": "update_foreign", "code": "unknown_key", "file": "festpos.zip"}])
    assert sicherheit.import_new() == 1
    # «Gesehen» zurück geht nicht (ein Pi mit älterem Stand)
    client.post("/api/admin/security/seen?upto=1", auth=AUTH)
    assert client.get("/api/admin/security", auth=AUTH).json()["count"] == 2
    # Protokoll: nur Sicherheit
    rows = client.get("/api/admin/audit?sec=true", auth=AUTH).json()["entries"]
    assert rows and all(r["action"].startswith("sec_") or r["action"] == "emergency_login" for r in rows)


def test_lockout_and_emergency_login_count(client, tmp_path, monkeypatch):
    from app import core, sicherheit
    monkeypatch.setattr(sicherheit, "FILE", tmp_path / "keine.json")
    core.login_throttle.clear()
    for _ in range(core.LOGIN_MAX_FAILS + 2):
        client.post("/api/admin/login", json={"name": "Mallory", "password": "falsch"})
    core.login_throttle.clear()
    assert client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).status_code == 200
    assert sicherheit.import_new() == 1                                              # Sperre einmal, nicht 3×
    s = client.get("/api/admin/security", auth=AUTH).json()
    acts = [e["action"] for e in s["events"]]
    assert "sec_lockout" in acts and "emergency_login" in acts
    lock = next(e for e in s["events"] if e["action"] == "sec_lockout")["details"]
    assert lock["name"] == "Mallory" and lock["by"] == "ip"


def test_view_role_cannot_see_or_dismiss(client):
    r = client.post("/api/admin/users", auth=AUTH, json={"name": "Vera", "role": "view", "password": "sicher-123"})
    assert r.status_code == 200
    assert client.get("/api/admin/security", auth=("Vera", "sicher-123")).status_code == 403
    assert client.post("/api/admin/security/seen?upto=9", auth=("Vera", "sicher-123")).status_code == 403
