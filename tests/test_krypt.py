"""Daten verschlüsselt, Schlüssel im Chip (T-0-N/FestPOS#3): deploy/festpos-krypt mit nachgebildetem cryptsetup,
Chip (rpi-otp-private-key), mount und mkfs – neuer Pi, Umstellen mit Neustart und Rückfall, Karte in einem anderen Pi,
Geheimnisse von der Boot-Partition in den Container, Kartenpaket und Verwaltung."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import backup, geheim, kartenboot
from tests.test_api import AUTH
from tests.test_wiederherstellen import make_tls

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-krypt"

FAKE_CS = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
from pathlib import Path
a = sys.argv[1:]
mapdir = Path(os.environ["FAKE_MAPDIR"])
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("cryptsetup " + " ".join(x for x in a if x.startswith(("open", "close", "lu", "status", "--test"))) + "\n")
h = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
opt = lambda n: a[a.index(n) + 1]
pos, i = [], 0
while i < len(a):
    if a[i] in ("--type", "--cipher", "--key-size", "--sector-size", "--pbkdf", "--pbkdf-force-iterations",
                "--pbkdf-memory", "--iter-time", "--key-file"):
        i += 2
        continue
    if not a[i].startswith("--"):
        pos.append(a[i])
    i += 1
def load(f):
    try:
        return json.loads(Path(f).read_text())
    except Exception:
        return None
def save(f, d):
    Path(f).write_text(json.dumps(d))
cmd = pos[0]
if cmd == "luksFormat":
    save(pos[1], {"slots": [h(opt("--key-file"))]}); sys.exit(0)
if cmd == "luksAddKey":
    d = load(pos[1])
    if not d or h(opt("--key-file")) not in d["slots"]: sys.exit(2)
    d["slots"].append(h(pos[2])); save(pos[1], d); sys.exit(0)
if cmd == "luksRemoveKey":
    d = load(pos[1]); k = h(pos[2])
    if k not in d["slots"]: sys.exit(2)
    d["slots"].remove(k); save(pos[1], d); sys.exit(0)
if cmd == "open":
    d = load(pos[1])
    ok = bool(d) and h(opt("--key-file")) in d["slots"]
    if "--test-passphrase" in a or not ok: sys.exit(0 if ok else 2)
    (mapdir / pos[2]).write_text(pos[1]); sys.exit(0)
if cmd == "close":
    (mapdir / pos[1]).unlink(missing_ok=True); sys.exit(0)
if cmd == "status":
    sys.exit(0 if (mapdir / pos[1]).exists() else 4)
sys.exit(9)
'''
# mkfs: leerer Inhalt neben der Container-Datei; mount: Einhängepunkt wird ein Verweis darauf
FAKE_MKFS = r'''#!/usr/bin/env python3
import shutil, sys
from pathlib import Path
store = Path(Path(sys.argv[-1]).read_text() + ".inhalt")
shutil.rmtree(store, ignore_errors=True); (store / "lost+found").mkdir(parents=True)
'''
FAKE_MOUNT = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
a = sys.argv[1:]
src = os.path.realpath(a[1]) if a[0] == "--bind" else Path(a[-2]).read_text() + ".inhalt"
t = Path(a[-1])
t.rmdir()                                  # wie ein leerer Einhängepunkt (Fehler, wenn etwas drin ist)
t.symlink_to(src)
'''
FAKE_UMOUNT = r'''#!/usr/bin/env python3
import sys
from pathlib import Path
t = Path(sys.argv[-1])
if not t.is_symlink(): sys.exit(32)
t.unlink(); t.mkdir()
'''
FAKE_OTP = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path
if os.environ.get("FAKE_OTP_NONE"): sys.exit(1)
f = Path(os.environ["FAKE_OTP"])
cur = f.read_text().strip() if f.exists() else "0" * 64
a = sys.argv[1:]
if a[:2] == ["-y", "-w"]:
    if cur != "0" * 64: sys.exit(1)
    f.write_text(a[2]); sys.exit(0)
print(cur)
'''
FAKE_LOGGER = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ["FAKE_LOG"], "a") as f: f.write(os.path.basename(sys.argv[0]) + " " + " ".join(sys.argv[1:]) + "\n")
'''
SECRET = "geheim-" + "x" * 40
TUNNEL = "eyJ" + "A" * 80
CONF = f"""# FestPOS
CLUSTER_SIZE=1
VIP=192.168.55.10
CLUSTER_SECRET={SECRET}
ADMIN_PASSWORD="Theater-Fest-2026-lang"  # Notfall
TUNNEL_TOKEN={TUNNEL}
"""


def _exe(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def pi(tmp_path):
    r = tmp_path / "root"
    boot = tmp_path / "boot"
    (boot / "festpos-tls").mkdir(parents=True)
    tls = make_tls()
    for n, v in tls.items():
        (boot / "festpos-tls" / n).write_bytes(v)
    (boot / "festpos.conf").write_text(CONF)
    data = r / "var/lib/festpos"
    (data / "einrichtung").mkdir(parents=True)
    (data / "einrichtung" / "start.json").write_text('{"lang": "de"}')
    (r / "etc/festpos").mkdir(parents=True)
    tools, mapdir = tmp_path / "bin", tmp_path / "mapper"
    tools.mkdir()
    mapdir.mkdir()
    log, otp = tmp_path / "log", tmp_path / "otp"
    health = tmp_path / "health"
    env = {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_TEST_ROOT": str(r),
           "FESTPOS_BOOT": str(boot), "FESTPOS_KRYPT_MAPPER": str(mapdir), "FAKE_MAPDIR": str(mapdir),
           "FAKE_LOG": str(log), "FAKE_OTP": str(otp), "FESTPOS_KRYPT_SIZE": str(1 << 20),
           "FESTPOS_CRYPTSETUP": _exe(tools / "cryptsetup", FAKE_CS), "FESTPOS_MKFS": _exe(tools / "mkfs.ext4", FAKE_MKFS),
           "FESTPOS_MOUNT": _exe(tools / "mount", FAKE_MOUNT), "FESTPOS_UMOUNT": _exe(tools / "umount", FAKE_UMOUNT),
           "FESTPOS_OTP": _exe(tools / "rpi-otp-private-key", FAKE_OTP),
           "FESTPOS_SYSTEMCTL": _exe(tools / "systemctl", FAKE_LOGGER), "FESTPOS_FSTRIM": _exe(tools / "fstrim", FAKE_LOGGER),
           "FESTPOS_KRYPT_HEALTH": health.as_uri(), "FESTPOS_KRYPT_WAIT": "1"}

    def run(*args, stdin: str = "") -> tuple[subprocess.CompletedProcess, dict]:
        p = subprocess.run([sys.executable, str(HELPER), *args], env=env, input=stdin, capture_output=True, text=True,
                           timeout=60)
        try:
            st = json.loads((r / "run/festpos-krypt/stand.json").read_text())
        except OSError:
            st = {}
        return p, st

    def reboot() -> None:
        """Neustart: Arbeitsspeicher weg, nichts eingehängt, Container zu."""
        for d in ("run",):
            shutil.rmtree(r / d, ignore_errors=True)
        if data.is_symlink():
            data.unlink()
            data.mkdir()
        for f in mapdir.iterdir():
            f.unlink()

    return {"r": r, "boot": boot, "data": data, "kdir": r / "var/lib/festpos-krypt", "env": env, "run": run,
            "reboot": reboot, "log": log, "otp": otp, "health": health, "tls": tls,
            "quelle": r / "run/festpos-geheim/quelle", "offen": r / "run/festpos-geheim/wiederherstellung"}


def slots(pi) -> list[str]:
    return json.loads((pi["kdir"] / "daten.luks").read_text())["slots"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def chip(pi) -> bytes:
    import hmac
    return hmac.new(bytes.fromhex(pi["otp"].read_text()), b"festpos-daten-luks-v1", "sha256").digest()


# ------------------------------------------------------------------ gemeinsam
def test_recovery_key_format():
    k = geheim.recovery_new()
    assert len(k) == 39 and k.count("-") == 7 and all(c in geheim.ALPHABET for c in k.replace("-", ""))
    assert geheim.recovery_new() != k
    assert geheim.recovery_norm(" " + k.lower().replace("-", " ") + "\n") == k
    assert geheim.recovery_norm(k[:-1]) is None and geheim.recovery_norm(k.replace(k[0], "0", 1)) is None


def test_mask_and_merge_keep_everything_else():
    masked = geheim.mask(CONF)
    assert SECRET not in masked and TUNNEL not in masked and "Theater" not in masked
    assert backup.conf_value(masked, "CLUSTER_SECRET") == geheim.MARKER and "VIP=192.168.55.10" in masked
    assert "# FestPOS" in masked
    stored = geheim.secret_values(CONF)
    assert geheim.merge(masked, stored) == CONF.replace('"Theater-Fest-2026-lang"  # Notfall', '"Theater-Fest-2026-lang"') \
        or backup.conf_value(geheim.merge(masked, stored), "CLUSTER_SECRET") == SECRET
    # was im Container fehlt, bleibt nicht als «im-container» stehen
    assert backup.conf_value(geheim.merge(masked, {}), "CLUSTER_SECRET") is None


# ------------------------------------------------------------------ neuer Pi
def test_new_pi_encrypts_itself_provisionally_then_chip_after_confirm(pi):
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0, p.stdout + p.stderr
    assert st["state"] == "vorlaeufig" and st["offen"] and st["chip"] == "empty"     # Chip erst nach Bestätigung
    assert not pi["otp"].exists()
    assert (pi["data"] / "einrichtung" / "start.json").read_text() == '{"lang": "de"}'  # Daten im Container
    rec = pi["offen"].read_text()
    assert geheim.recovery_norm(rec) == rec
    assert slots(pi) == [sha((pi["kdir"] / "vorlaeufig.key").read_bytes()), sha(rec.encode())]
    # Geheimnisse: nicht mehr auf der Boot-Partition, für festpos-node im Arbeitsspeicher
    conf = (pi["boot"] / "festpos.conf").read_text()
    assert SECRET not in conf and TUNNEL not in conf and "Theater" not in conf and "VIP=192.168.55.10" in conf
    assert backup.conf_value(conf, "CLUSTER_SECRET") == "im-container"
    assert not (pi["boot"] / "festpos-tls" / "server.key").exists() and (pi["boot"] / "festpos-tls" / "server.crt").exists()
    q = (pi["quelle"] / "festpos.conf").read_text()
    assert backup.conf_value(q, "CLUSTER_SECRET") == SECRET and backup.conf_value(q, "TUNNEL_TOKEN") == TUNNEL
    assert (backup.conf_value(q, "ADMIN_PASSWORD_HASH") or "").startswith("scrypt$")      # Klartext -> Prüfwert (#15)
    assert backup.conf_value(q, "ADMIN_PASSWORD") is None
    assert (pi["quelle"] / "server.key").read_bytes() == pi["tls"]["server.key"]
    assert "fstrim" in pi["log"].read_text()
    # bestätigen: Schlüssel in den Chip, vorläufiger weg
    p, st = pi["run"]("--bestaetigen")
    assert p.returncode == 0 and json.loads(p.stdout.splitlines()[-1]) == {"ok": True}, p.stderr
    assert st["state"] == "aktiv" and st["chip"] == "set" and not st["offen"] and st["bestaetigt"]
    assert not (pi["kdir"] / "vorlaeufig.key").exists() and not pi["offen"].exists()
    assert slots(pi) == [sha(rec.encode()), sha(chip(pi))]
    # nächster Start: mit dem Chip, alles da
    pi["reboot"]()
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "aktiv"
    assert (pi["data"] / "einrichtung" / "start.json").exists() and (pi["quelle"] / "server.key").exists()
    # am PC einen neuen Wert eingetragen: gilt und wandert beim Start in den Container
    conf = (pi["boot"] / "festpos.conf").read_text().replace("TUNNEL_TOKEN=", "TUNNEL_TOKEN=eyJneu#")
    (pi["boot"] / "festpos.conf").write_text(backup.conf_set(conf, "TUNNEL_TOKEN", "eyJ" + "B" * 80))
    pi["reboot"]()
    pi["run"]("--oeffnen")
    assert backup.conf_value((pi["quelle"] / "festpos.conf").read_text(), "TUNNEL_TOKEN") == "eyJ" + "B" * 80
    assert backup.conf_value((pi["boot"] / "festpos.conf").read_text(), "TUNNEL_TOKEN") == "im-container"


def test_card_in_other_pi_locked_then_opened_with_recovery_file(pi):
    pi["run"]("--oeffnen")
    rec = pi["offen"].read_text()
    pi["run"]("--bestaetigen")
    pi["reboot"]()
    pi["otp"].write_text(os.urandom(32).hex())                  # anderer Pi
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 1 and st["state"] == "gesperrt"       # FestPOS startet nicht (Requires=)
    assert "Wiederherstellungs-Schl" in (pi["boot"] / "festpos-gesperrt.txt").read_text()
    assert not any(pi["data"].iterdir())                         # nie mit leerer Datenbank weiter
    (pi["boot"] / "festpos-wiederherstellung.txt").write_text(rec.lower().replace("-", " ") + "\r\n")
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "aktiv", p.stdout + p.stderr
    assert not (pi["boot"] / "festpos-wiederherstellung.txt").exists() and not (pi["boot"] / "festpos-gesperrt.txt").exists()
    assert sha(chip(pi)) in slots(pi)                            # ab jetzt mit dem Chip dieses Pi
    pi["reboot"]()
    assert pi["run"]("--oeffnen")[0].returncode == 0


def test_pi_without_chip_keeps_key_on_card(pi):
    pi["env"]["FAKE_OTP_NONE"] = "1"
    p, st = pi["run"]("--oeffnen")
    assert st["state"] == "vorlaeufig" and st["chip"] == "none"
    p, st = pi["run"]("--bestaetigen")
    assert p.returncode == 0 and st["state"] == "kein_chip" and (pi["kdir"] / "vorlaeufig.key").exists()


def test_existing_pi_is_not_encrypted_without_order(pi):
    (pi["data"] / "rqlite").mkdir()
    (pi["data"] / "rqlite" / ".festpos-node").write_text("a")
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "aus" and st["tool"] and not (pi["kdir"] / "daten.luks").exists()
    assert SECRET in (pi["boot"] / "festpos.conf").read_text()   # unverschlüsselt: alles wie bisher


# ------------------------------------------------------------------ bestehender Pi: umstellen
@pytest.fixture
def old_pi(pi):
    (pi["data"] / "rqlite").mkdir()
    (pi["data"] / "rqlite" / ".festpos-node").write_text("a")
    (pi["data"] / "rqlite" / "db.sqlite").write_bytes(b"Bestellungen" * 1000)
    (pi["data"] / "fully").mkdir()
    (pi["data"] / "fully" / "link").symlink_to("../rqlite/db.sqlite")
    pi["run"]("--oeffnen")
    return pi


def test_switch_over_with_two_restarts(old_pi):
    pi = old_pi
    rec = geheim.recovery_new()
    p, _ = pi["run"]("--umstellen", stdin=json.dumps({"recovery": rec.lower()}))
    assert json.loads(p.stdout.splitlines()[-1]) == {"ok": True, "restart": True}
    assert "systemctl start festpos-sicherung.service" in pi["log"].read_text()      # Sicherung vorher
    assert json.loads((pi["kdir"] / "auftrag.json").read_text())["recovery"] == rec
    # 1. Neustart: umstellen, bevor FestPOS läuft; Schlüssel in den Chip (der Wiederherstellungs-Schlüssel ist gesichert)
    pi["reboot"]()
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 1 and st["state"] == "umstellen", p.stdout + p.stderr     # FestPOS startet nicht
    assert "systemctl reboot --no-block" in pi["log"].read_text()
    assert pi["otp"].exists() and slots(pi) == [sha(chip(pi)), sha(rec.encode())]
    assert (pi["kdir"] / "alt" / "rqlite" / "db.sqlite").exists() and not any(pi["data"].iterdir())
    assert not (pi["kdir"] / "auftrag.json").exists()
    # 2. Neustart: geht mit dem Chip auf; die alten Daten bleiben, bis FestPOS läuft
    pi["reboot"]()
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "umstellen" and (pi["kdir"] / "gesund").exists()
    assert (pi["data"] / "rqlite" / "db.sqlite").read_bytes() == b"Bestellungen" * 1000
    assert os.readlink(pi["data"] / "fully" / "link") == "../rqlite/db.sqlite"
    assert SECRET in (pi["boot"] / "festpos.conf").read_text()   # erst nach der Prüfung verschoben
    pi["health"].write_text("ok")
    p, st = pi["run"]("--nachpruefen")
    assert p.returncode == 0 and st["state"] == "aktiv" and st["bestaetigt"], p.stdout + p.stderr
    assert not (pi["kdir"] / "alt").exists() and not (pi["kdir"] / "gesund").exists()
    assert SECRET not in (pi["boot"] / "festpos.conf").read_text() and not (pi["boot"] / "festpos-tls" / "server.key").exists()
    assert (pi["quelle"] / "server.key").exists()


def test_switch_over_falls_back_when_chip_does_not_open(old_pi):
    pi = old_pi
    pi["run"]("--umstellen", stdin=json.dumps({"recovery": geheim.recovery_new()}))
    pi["reboot"]()
    pi["run"]("--oeffnen")
    pi["reboot"]()
    pi["otp"].write_text(os.urandom(32).hex())                  # geht mit dem Chip nicht auf
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "aus" and st["error"] == "verify"
    assert (pi["data"] / "rqlite" / "db.sqlite").read_bytes() == b"Bestellungen" * 1000   # wie vorher
    assert not (pi["kdir"] / "daten.luks").exists() and not (pi["kdir"] / "alt").exists()


def test_switch_over_falls_back_when_festpos_does_not_run(old_pi):
    pi = old_pi
    pi["run"]("--umstellen", stdin=json.dumps({"recovery": geheim.recovery_new()}))
    pi["reboot"]()
    pi["run"]("--oeffnen")
    pi["reboot"]()
    pi["run"]("--oeffnen")
    (pi["data"] / "rqlite" / "neu.txt").write_text("inzwischen bestellt")
    p, st = pi["run"]("--nachpruefen")                         # FestPOS antwortet nicht
    assert p.returncode == 1 and st["state"] == "aus" and st["error"] == "health", p.stdout + p.stderr
    log = pi["log"].read_text()
    assert "systemctl stop festpos.service" in log and log.rstrip().endswith("systemctl reboot --no-block")
    assert (pi["data"] / "rqlite" / "neu.txt").read_text() == "inzwischen bestellt"    # nichts verloren
    assert not pi["data"].is_symlink() and not (pi["kdir"] / "daten.luks").exists()


@pytest.mark.parametrize("job, code", [({"recovery": "zu-kurz"}, "job"), ({}, "job")])
def test_switch_over_refuses_bad_order(old_pi, job, code):
    p, _ = old_pi["run"]("--umstellen", stdin=json.dumps(job))
    assert p.returncode == 1 and json.loads(p.stdout.splitlines()[-1]) == {"ok": False, "code": code}
    assert not (old_pi["kdir"] / "auftrag.json").exists()


def test_switch_over_refused_when_already_encrypted(pi):
    pi["run"]("--oeffnen")
    p, _ = pi["run"]("--umstellen", stdin=json.dumps({"recovery": geheim.recovery_new()}))
    assert json.loads(p.stdout.splitlines()[-1]) == {"ok": False, "code": "exists"}


def test_move_in_after_new_certificates(pi):
    pi["run"]("--oeffnen")
    new = make_tls("Neu")
    (pi["boot"] / "festpos-tls" / "server.key").write_bytes(new["server.key"])          # festpos-zertifikate
    p, _ = pi["run"]("--einziehen")
    assert p.returncode == 0 and not (pi["boot"] / "festpos-tls" / "server.key").exists()
    assert (pi["quelle"] / "server.key").read_bytes() == new["server.key"]


# ------------------------------------------------------------------ Kartenpaket, Skripte
def test_card_package_gets_secrets_from_container(pi, monkeypatch):
    pi["run"]("--oeffnen")
    monkeypatch.setattr(geheim, "RUN_DIR", pi["quelle"])
    files = kartenboot.boot_files(pi["boot"], pi["r"] / "etc/festpos", "b")
    conf = files["festpos.conf"].decode()
    assert backup.conf_value(conf, "CLUSTER_SECRET") == SECRET and "im-container" not in conf
    assert files["festpos-tls/server.key"] == pi["tls"]["server.key"]
    assert files["festpos-tls/device-ca.key"] == pi["tls"]["device-ca.key"]


def test_scripts_use_runtime_copies():
    node = (ROOT / "deploy/festpos-node.sh").read_text()
    assert 'ln -sfn "$GRUN/env" "$ETC/env"' in node and 'ln -sfn "$GRUN/tls" "$ETC/tls"' in node
    assert 'festpos_read_conf "$QUELLE/festpos.conf"' in node and "rm -rf /etc/festpos-gast" in node
    for unit in ("festpos-node", "rqlited", "festpos", "festpos-dhcp"):
        text = (ROOT / f"deploy/systemd/{unit}.service").read_text()
        assert "Requires=festpos-krypt.service" in text and "After=" in text and "festpos-krypt.service" in text
    assert "festpos-krypt festpos-krypt 0755" in (ROOT / "deploy/install-lib.sh").read_text()
    assert "cryptsetup-bin" in (ROOT / "deploy/setup.sh").read_text()
    z = (ROOT / "deploy/festpos-zertifikate").read_text()
    assert 'mktemp "$ENVF.XXXXXX"' in z and '"$KRYPT" --einziehen' in z
    assert z.index('cp -a "$GEHEIM/tls" "$KVOR/tls"') < z.index('"$KRYPT" --einziehen') < z.index(
        'cp -a "$KVOR/tls" "$GEHEIM/tls"')                                # Container beim Rückfall mit zurück


# ------------------------------------------------------------------ Verwaltung
def test_status_in_pi_info(client, tmp_path, monkeypatch):
    from app import krypt, system
    st = tmp_path / "stand.json"
    st.write_text(json.dumps({"state": "vorlaeufig", "chip": "empty", "tool": True, "offen": True, "bestaetigt": None,
                              "error": None, "size": 2 << 30, "used": 1, "free": 2}))
    monkeypatch.setattr(krypt, "STAND", str(st))
    info = system.local_info()
    assert info["krypt"]["state"] == "vorlaeufig" and info["krypt"]["offen"]
    assert "used" not in system.durable(info)["krypt"]                   # nicht bei jeder Änderung in die Datenbank
    st.write_text("kaputt")
    assert system.local_info()["krypt"] is None


def test_admin_routes(client, tmp_path, monkeypatch):
    from app import krypt
    r = client.post("/api/admin/krypt/schluessel", auth=AUTH)
    assert r.status_code == 200 and geheim.recovery_norm(r.json()["recovery"]) == r.json()["recovery"]
    tok = client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).json()["token"]
    body = {"recovery": r.json()["recovery"], "printed": True}
    assert client.post("/api/admin/krypt/local/umstellen", headers={"Authorization": f"Bearer {tok}"},
                       json=body).status_code == 428                         # kritisch: bestätigen
    assert client.post("/api/admin/krypt/a/umstellen", auth=AUTH, json={**body, "printed": False}).status_code == 422
    monkeypatch.setattr(krypt, "STAND", str(tmp_path / "fehlt.json"))
    monkeypatch.setattr(krypt.Config, "node", "a")
    assert client.post("/api/admin/krypt/a/umstellen", auth=AUTH, json=body).status_code == 409   # kein Helfer
    assert client.post("/api/admin/krypt/x/umstellen", auth=AUTH, json=body).status_code == 422
    pending = tmp_path / "offen"
    monkeypatch.setattr(krypt, "OFFEN", str(pending))
    assert client.get("/api/admin/krypt/a/offen", auth=AUTH).status_code == 409
    pending.write_text(body["recovery"])
    assert client.get("/api/admin/krypt/a/offen", auth=AUTH).json() == {"recovery": body["recovery"]}
    assert client.get("/api/admin/krypt/a/offen", headers={"Authorization": f"Bearer {tok}"}).status_code == 428
    # anderer Pi ohne bekannte Adresse: nicht erreichbar (keine beliebigen Ziele)
    assert client.post("/api/admin/krypt/b/bestaetigen", auth=AUTH, json={"printed": True}).status_code == 503
    assert client.post("/api/internal/krypt", json={"action": "offen"}).status_code == 403          # nur die Pi


def test_aborted_build_is_cleaned_up(old_pi):
    """Strom weg beim Anlegen (Umstellen): beim nächsten Start halben Container wegräumen, die Daten gelten weiter."""
    pi = old_pi
    pi["kdir"].mkdir(exist_ok=True)
    (pi["kdir"] / "daten.luks").write_text("halb")
    (pi["kdir"] / "im-bau").write_text("1")
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "aus" and st["error"] == "aborted", p.stdout + p.stderr
    assert not (pi["kdir"] / "daten.luks").exists() and (pi["data"] / "rqlite" / "db.sqlite").exists()


@pytest.mark.parametrize("weg, leer", [("alt", True), ("alt", False), ("neu-alt", True)])
def test_build_interrupted_after_moving_data_restores_it(old_pi, weg, leer):
    """Strom weg, nachdem der Container fertig ist, aber die Daten erst halb beiseite sind (Markierung noch da): die
    Daten kommen zurück, der Container weg – nie ein Container über den Klartext-Daten."""
    import shutil
    pi = old_pi
    pi["kdir"].mkdir(exist_ok=True)
    (pi["kdir"] / "daten.luks").write_text("fertig")
    (pi["kdir"] / "im-bau").write_text("1")
    shutil.move(str(pi["data"]), str(pi["kdir"] / weg))
    if leer:
        pi["data"].mkdir()                                                   # leerer Einhängepunkt schon da
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["error"] == "aborted", p.stdout + p.stderr
    assert (pi["data"] / "rqlite" / "db.sqlite").exists() and not (pi["kdir"] / weg).exists()
    assert not (pi["kdir"] / "daten.luks").exists() and not (pi["kdir"] / "im-bau").exists()


def test_new_pi_mount_failure_stays_unencrypted(pi):
    """Neuer Pi, Einhängen scheitert: zurück zu den unverschlüsselten Daten (nie ein leerer Datenordner)."""
    bad = pi["env"]["FESTPOS_MOUNT"] + "-kaputt"
    Path(bad).write_text(f'#!/bin/sh\n[ "$1" = --bind ] && exit 32\nexec {pi["env"]["FESTPOS_MOUNT"]} "$@"\n')
    Path(bad).chmod(0o755)
    pi["env"]["FESTPOS_MOUNT"] = bad
    p, st = pi["run"]("--oeffnen")
    assert p.returncode == 0 and st["state"] == "aus" and st["error"] == "mount", p.stdout + p.stderr
    assert (pi["data"] / "einrichtung" / "start.json").exists() and not (pi["kdir"] / "daten.luks").exists()
    assert not (pi["kdir"] / "vorlaeufig.key").exists()
