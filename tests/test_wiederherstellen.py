"""Root-Helfer festpos-wiederherstellen (T8): Schlüssel aus einer Sicherung auf die Boot-Partition, Kartenpaket für
weitere Pi, Pi A auf 3 Pi umstellen – mit Ordnern statt /boot und /etc (die CI läuft als normaler Benutzer)."""
from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import backup
from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-wiederherstellen"
CONF = "# FestPOS\nCLUSTER_SIZE=1\nVIP=192.168.55.10\nADMIN_PASSWORD=bitte-aendern  # ändern!\nCLUSTER_SECRET=bitte-aendern-lang-und-zufaellig\n"


def _pem_key(k) -> bytes:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def make_tls(name: str = "Test") -> dict[str, bytes]:
    """CA, Server-Zertifikat (von der CA), Geräte-CA – wie make-certs.sh, nur klein."""
    now = dt.datetime.now(dt.timezone.utc)

    def cert(subject, issuer, key, sign_key, ca):
        b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + dt.timedelta(days=30)))
        if ca:
            b = b.add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        return b.sign(sign_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
    ca_key, srv_key, dca_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{name} CA")])
    srv_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "festpos.local")])
    dca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{name} Geraete-CA")])
    return {"ca.crt": cert(ca_name, ca_name, ca_key, ca_key, True),
            "server.crt": cert(srv_name, ca_name, srv_key, ca_key, False), "server.key": _pem_key(srv_key),
            "device-ca.crt": cert(dca_name, dca_name, dca_key, dca_key, True), "device-ca.key": _pem_key(dca_key)}


@pytest.fixture
def pi(tmp_path):
    """Boot-Partition, Auftrags- und Ergebnisordner wie auf dem Pi (Einzelbetrieb)."""
    boot, etc = tmp_path / "boot", tmp_path / "etc"
    (boot / "festpos-tls").mkdir(parents=True)
    (etc / "tls").mkdir(parents=True)
    old = make_tls("Alt")
    for n, v in old.items():
        (boot / "festpos-tls" / n).write_bytes(v)
    (boot / "festpos.conf").write_text(CONF)
    (boot / "festpos-update.pub").write_text("ssh-ed25519 AAAA alt")
    d = {"boot": boot, "etc": etc, "jobs": tmp_path / "jobs", "neu": tmp_path / "neu", "out": tmp_path / "out", "old": old}
    for k in ("jobs", "neu", "out"):
        d[k].mkdir()
    d["env"] = {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_WH_DIR": str(d["jobs"]),
                "FESTPOS_WH_ERGEBNIS": str(d["out"]), "FESTPOS_BOOT": str(boot), "FESTPOS_ETC": str(etc),
                "FESTPOS_WH_TROCKEN": "1"}
    return d


def run_job(pi, kind: str, **payload) -> dict:
    job = "0123456789abcdef"
    (pi["jobs"] / f"auftrag-{job}.json").write_text(json.dumps({"id": job, "kind": kind, **payload}))
    r = subprocess.run([sys.executable, "-I", str(HELPER), "--anfrage"], env=pi["env"], capture_output=True, text=True,
                       timeout=60)
    assert not list(pi["jobs"].iterdir())                       # Auftrag weg (sonst startet systemd immer wieder)
    res = json.loads((pi["out"] / f"{job}.json").read_text())
    assert (r.returncode == 0) == res["ok"], r.stderr
    return res


def _read_conf(path: Path, *keys) -> list[str]:
    out = subprocess.run(["bash", "-c", f'source "{ROOT}/deploy/festpos-conf.sh"; festpos_read_conf "{path}"; '
                          + "; ".join(f'printf "%s\\n" "${k}"' for k in keys)], capture_output=True, text=True)
    return out.stdout.split("\n")[:len(keys)]


def test_keys_written_to_boot_and_old_kept(pi):
    new = make_tls("Sicherung")
    res = run_job(pi, "keys", tls={n: v.decode() for n, v in new.items()},
                  conf={"cluster_secret": "auto-" + "ab" * 32, "admin_password": 'Not "fall" #1'},
                  update_keys={"festpos-update.pub": "ssh-ed25519 AAAA neu", "evil.sh": "x"})
    assert res["ok"] and res["restart"]
    for n, v in new.items():
        assert (pi["boot"] / "festpos-tls" / n).read_bytes() == v
    alt = [p for p in pi["boot"].iterdir() if p.name.startswith("festpos-tls.alt-")]
    assert len(alt) == 1 and (alt[0] / "ca.crt").read_bytes() == pi["old"]["ca.crt"]
    assert any(p.name.startswith("festpos.conf.alt-") for p in pi["boot"].iterdir())
    assert _read_conf(pi["boot"] / "festpos.conf", "CLUSTER_SECRET", "ADMIN_PASSWORD", "VIP") == \
        ["auto-" + "ab" * 32, 'Not "fall" #1', "192.168.55.10"]
    assert (pi["boot"] / "festpos-update.pub").read_text() == "ssh-ed25519 AAAA alt"   # vorhandener bleibt
    assert not (pi["boot"] / "evil.sh").exists()


def test_keys_never_add_an_update_key(pi):
    """Review 2026-11, S2: «Schlüssel aus der Sicherung übernehmen» legt nie einen Update-Schlüssel ab – sonst könnte,
    wer das Admin-Passwort hat, mit einer selbst gebauten Sicherung einen eigenen Schlüssel einschleusen und danach
    eigenen Code als root einspielen. Fehlende werden nur gemeldet."""
    from app import release
    before = sorted(p.name for p in pi["boot"].iterdir() if p.name.startswith("festpos-update"))
    res = run_job(pi, "keys", tls={n: v.decode() for n, v in make_tls("Sicherung").items()}, conf={},
                  update_keys={"festpos-update-zweit.pub": "ssh-ed25519 AAAA fremd", "festpos-update.pub": "ssh-ed25519 AAAA neu"})
    assert res["ok"] and res["update_keys_skipped"] == ["festpos-update-zweit.pub"]
    assert sorted(p.name for p in pi["boot"].iterdir() if p.name.startswith("festpos-update")) == before
    assert (pi["boot"] / "festpos-update.pub").read_text() == "ssh-ed25519 AAAA alt"
    assert [k.file for k in release.load_keys(pi["boot"])[0]] == []      # (der Test-Schlüssel ist kein echter)


@pytest.mark.parametrize("conf", [{"admin_password": "lang-genug-1234\nPHONE_CERTS=0"},
                                  {"cluster_secret": "geheim-geheim-geheim\rSSH_FROM=0.0.0.0/8"},
                                  {"admin_password": "tab\tdrin-und-lang"}])
def test_keys_refuse_control_characters_for_festpos_conf(pi, conf):
    """Ein Zeilenumbruch im Notfall-Passwort oder CLUSTER_SECRET schriebe fremde Zeilen in festpos.conf: abgewiesen,
    bevor irgendetwas ersetzt ist."""
    res = run_job(pi, "keys", tls={n: v.decode() for n, v in make_tls("Sicherung").items()}, conf=conf)
    assert res == {"ok": False, "code": "conf_value"}
    assert (pi["boot"] / "festpos.conf").read_text() == CONF
    assert (pi["boot"] / "festpos-tls" / "ca.crt").read_bytes() == pi["old"]["ca.crt"]
    assert not [p for p in pi["boot"].iterdir() if ".alt-" in p.name]


def test_conf_line_refuses_control_characters():
    for bad in ("a\nb", "a\rb", "a\x00b", "a\x7fb"):
        with pytest.raises(ValueError):
            backup.conf_set("X=1\n", "ADMIN_PASSWORD", bad)
    with pytest.raises(ValueError):
        backup.conf_line("ADMIN PASSWORD", "x")
    assert backup.conf_value(backup.conf_set("X=1\n", "ADMIN_PASSWORD", 'a "b" $c #d'), "ADMIN_PASSWORD") == 'a "b" $c #d'


@pytest.mark.parametrize("change, code", [
    (lambda t: t.update({"server.key": make_tls()["server.key"]}), "tls_pair"),
    (lambda t: t.update({"ca.crt": make_tls("Fremd")["ca.crt"]}), "tls_issuer"),
    (lambda t: t.update({"server.crt": b"kein pem"}), "tls_pem"),
    (lambda t: t.update({"../boot.txt": b"x"}), "tls_files"),
    (lambda t: t.pop("server.key"), "tls_files"),
])
def test_keys_checked_before_anything_is_replaced(pi, change, code):
    new = make_tls()
    change(new)
    res = run_job(pi, "keys", tls={n: v.decode("latin-1") for n, v in new.items()}, conf={})
    assert res == {"ok": False, "code": code}
    assert (pi["boot"] / "festpos-tls" / "ca.crt").read_bytes() == pi["old"]["ca.crt"]
    assert (pi["boot"] / "festpos.conf").read_text() == CONF


def test_keys_refused_in_cluster(pi):
    (pi["boot"] / "festpos.conf").write_text(CONF.replace("CLUSTER_SIZE=1", "CLUSTER_SIZE=3"))
    assert run_job(pi, "keys", tls={n: v.decode() for n, v in make_tls().items()}, conf={})["code"] == "cluster"


def test_kartenpaket(pi):
    res = run_job(pi, "kartenpaket", letters=["b", "c"], readme="Kartenpaket – Anleitung")
    assert res["ok"] and res["letters"] == ["b", "c"]
    z = zipfile.ZipFile(io.BytesIO((pi["out"] / "0123456789abcdef.zip").read_bytes()))
    names = set(z.namelist())
    assert "LIESMICH.txt" in names and "karte-b/festpos-update.pub" in names
    assert {f"karte-c/festpos-tls/{n}" for n in backup.TLS_FILES} <= names
    assert z.read("karte-b/node.conf") == b"NODE=b\n" and z.read("karte-c/node.conf") == b"NODE=c\n"
    conf = z.read("karte-b/festpos.conf").decode()
    derived = backup.derived_secret(pi["old"]["server.key"])     # wie die App bei Platzhalter: festgeschrieben
    assert backup.conf_value(conf, "CLUSTER_SIZE") == "3" and backup.conf_value(conf, "CLUSTER_SECRET") == derived
    assert backup.conf_value(conf, "VIP") == "192.168.55.10" and "# FestPOS" in conf     # Rest unverändert
    assert run_job(pi, "kartenpaket", letters=["x"])["code"] == "letters"


def test_cluster3_only_from_single(pi):
    assert run_job(pi, "cluster3") == {"ok": True, "restart": True}
    conf = (pi["boot"] / "festpos.conf").read_text()
    assert backup.conf_value(conf, "CLUSTER_SIZE") == "3"
    assert backup.conf_value(conf, "CLUSTER_SECRET") == backup.derived_secret(pi["old"]["server.key"])
    assert run_job(pi, "cluster3") == {"ok": False, "code": "not_single"}


def test_unknown_or_broken_job(pi):
    (pi["jobs"] / "fremd.txt").write_text("x")
    assert run_job(pi, "rm-rf") == {"ok": False, "code": "job"}


@pytest.fixture
def helper_dirs(pi, monkeypatch):
    """App schreibt Aufträge in die Ordner des Tests; der Helfer läuft beim Warten auf die Antwort."""
    from app import cards
    monkeypatch.setattr(cards, "WH_DIR", pi["jobs"])
    monkeypatch.setattr(cards, "WH_NEU", pi["neu"])
    monkeypatch.setattr(cards, "WH_ERGEBNIS", pi["out"])
    real_wait = cards.wait

    def wait(job, timeout=30.0):
        subprocess.run([sys.executable, "-I", str(HELPER), "--anfrage"], env=pi["env"], capture_output=True, timeout=60)
        return real_wait(job, 2.0)
    monkeypatch.setattr(cards, "wait", wait)
    return pi


def test_kartenpaket_download_and_expand(client, helper_dirs):
    from app import core
    r = client.post("/api/admin/cluster/kartenpaket", auth=AUTH, json={"letters": ["b", "c"]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "Pi B" in z.read("LIESMICH.txt").decode()
    assert client.post("/api/admin/cluster/kartenpaket", auth=AUTH, json={"letters": ["local"]}).status_code == 422
    st = client.get("/api/admin/cluster/expand", auth=AUTH).json()
    assert st["single"] and st["helper"] and st["checks"]["backup"] is False     # noch keine Sicherung
    assert client.post("/api/admin/cluster/expand?confirm=JA", auth=AUTH).status_code == 409
    client.post("/api/admin/backup", auth=AUTH, json={"password": "x" * 12})     # frische Sicherung
    st = client.get("/api/admin/cluster/expand", auth=AUTH).json()
    assert st["checks"] == {"single": True, "backup": True, "idle": True}, st
    assert client.post("/api/admin/cluster/expand?confirm=JA", auth=AUTH).json() == {"restart": True}
    assert backup.conf_value((helper_dirs["boot"] / "festpos.conf").read_text(), "CLUSTER_SIZE") == "3"
    acts = [e["action"] for e in client.get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert "kartenpaket" in acts and "cluster_expand" in acts
    assert core.rt.store.scalar("SELECT COUNT(*) FROM settings WHERE value LIKE '%BEGIN%'") == 0   # nichts davon in der DB


def test_restore_on_new_pi_writes_keys(client, helper_dirs, tmp_path):
    """Neuer Pi mit anderer festpos-tls: das Einspielen gibt die Schlüssel der Sicherung an den Helfer, der Pi startet
    neu. Gleiches System: nur Hinweis, Übernehmen auf Wunsch."""
    from app import core
    here, there = tmp_path / "tls-hier", tmp_path / "tls-sicherung"
    for d, name in ((here, "Neu"), (there, "Sicherung")):
        d.mkdir()
        for n, v in make_tls(name).items():
            (d / n).write_bytes(v)
    core.Config.server_cert = str(there / "server.crt")
    data = client.post("/api/admin/backup", auth=AUTH, json={"password": "Sicherung-123"}).content
    core.Config.server_cert = str(here / "server.crt")
    r = client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("s.festpos", data)},
                    data={"password": "Sicherung-123"}).json()
    assert r["case"] == "new" and r["keys"] == "restoring" and r["restart"] is True
    jobs = list(helper_dirs["jobs"].iterdir())
    job = json.loads(jobs[0].read_text())
    assert job["kind"] == "keys" and job["tls"]["ca.crt"] == (there / "ca.crt").read_text()
    jobs[0].unlink()
    # gleiches System: kein Auftrag, Hinweis in der Prüfliste; auf Wunsch übernehmen
    core.rt.store.execute("INSERT INTO device_certs(name, serial, created_at) VALUES ('X', 'S', '2026-10-01T10:00:00')")
    r = client.post("/api/admin/restore?confirm=JA", auth=AUTH, files={"file": ("s.festpos", data)},
                    data={"password": "Sicherung-123"}).json()
    assert r["case"] == "same" and r["keys"] == "differ" and not r["restart"] and not list(helper_dirs["jobs"].iterdir())
    rc = client.get("/api/admin/restore-check", auth=AUTH).json()
    assert any(i["id"] == "keys" and i["state"] == "warn" for i in rc["items"])
    r = client.post("/api/admin/restore/keys", auth=AUTH, files={"file": ("s.festpos", data)},
                    data={"password": "Sicherung-123"})
    assert r.json() == {"restart": True} and len(list(helper_dirs["jobs"].iterdir())) == 1
