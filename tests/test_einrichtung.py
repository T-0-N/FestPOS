"""T11: Ersteinrichtung im Browser – Einrichtungscode, Wege «neu», «Sicherung», «Kartenpaket», root-Helfer (nur ohne
festpos.conf), Zertifikate wie make-certs.sh, nach dem Neustart spielt die App die Sicherung selbst ein.
Ohne root: Boot-Partition und Laufzeitordner sind Ordner des Tests (FESTPOS_WH_TROCKEN=1)."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from app import backup, zertifikate
from tests.test_api import AUTH
from tests.test_wiederherstellen import CONF, make_tls

ROOT = Path(__file__).resolve().parent.parent
WH = ROOT / "deploy" / "festpos-wiederherstellen"
VALUES = {"prefix": "192.168.55", "vip": "192.168.55.10", "a": "192.168.55.11", "b": "192.168.55.12",
          "c": "192.168.55.13", "dhcp": True, "cluster_size": 1, "node": "a", "rtc_akku": True, "tz": "Europe/Zurich"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Frischer Pi ohne festpos.conf: Boot-Partition, Einrichtungscode, Auftragsordner, Ersteinrichtungs-App."""
    from fastapi.testclient import TestClient
    from app import setup_mode
    boot, run, data = tmp_path / "boot", tmp_path / "run", tmp_path / "data"
    for d in (boot, run, data, tmp_path / "jobs", tmp_path / "neu", tmp_path / "out", tmp_path / "etc"):
        d.mkdir()
    (run / "einrichtung").touch()
    (data / "code").write_text("K7MP-3QXA\n")
    env = {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_WH_DIR": str(tmp_path / "jobs"),
           "FESTPOS_WH_ERGEBNIS": str(tmp_path / "out"), "FESTPOS_BOOT": str(boot), "FESTPOS_ETC": str(tmp_path / "etc"),
           "FESTPOS_WH_TROCKEN": "1", "FESTPOS_EINRICHTUNG_RUN": str(run / "einrichtung"),
           "FESTPOS_EINRICHTUNG_DATA": str(data), "FESTPOS_DEST": str(ROOT)}
    for k, v in (("CODE_FILE", data / "code"), ("WH_DIR", tmp_path / "jobs"), ("WH_NEU", tmp_path / "neu"),
                 ("WH_ERGEBNIS", tmp_path / "out"), ("STASH", tmp_path / "stash")):
        monkeypatch.setattr(setup_mode, k, v)
    monkeypatch.setitem(setup_mode.S, "tokens", set())
    for k, v in (("fails", 0), ("locked_until", 0.0), ("ca_key", None), ("ca_crt", None), ("done", None)):
        monkeypatch.setitem(setup_mode.S, k, v)
    real_wait = setup_mode._wait

    def wait(job, timeout=60.0):
        subprocess.run([sys.executable, "-I", str(WH), "--anfrage"], env=env, capture_output=True, timeout=120)
        return real_wait(job, 2.0)
    monkeypatch.setattr(setup_mode, "_wait", wait)
    monkeypatch.setattr(setup_mode, "LOCK_S", 300)
    with TestClient(setup_mode.app) as c:
        yield {"c": c, "boot": boot, "env": env, "tmp": tmp_path, "data": data, "mod": setup_mode}


def _token(c) -> str:
    r = c.post("/api/einrichtung/code", json={"code": "k7mp 3qxa"})               # Schreibweise egal
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_pages_say_not_set_up(setup):
    c = setup["c"]
    for path in ("/", "/index.html", "/admin.html", "/status.html"):
        r = c.get(path)
        assert r.status_code == 200 and "einrichtung.js" in r.text and "script-src 'self'" in r.headers["content-security-policy"]
    info = c.get("/api/einrichtung").json()
    assert info["setup"] and info["defaults"]["vip"] == "192.168.55.10" and any(x["code"] == "de" for x in info["langs"])
    assert "K7MP" not in json.dumps(info)                                            # Code nie im Netz


def test_code_wrong_and_lockout(setup):
    c, mod = setup["c"], setup["mod"]
    mod.MAX_FAILS = 3
    try:
        for _ in range(3):
            assert c.post("/api/einrichtung/code", json={"code": "FALSCH"}).status_code == 403
        assert c.post("/api/einrichtung/code", json={"code": "K7MP-3QXA"}).status_code == 429   # gesperrt
    finally:
        mod.MAX_FAILS = 10
    r = c.post("/api/einrichtung/neu", json={"token": "x", "admin_password": "Geheim-12345", "values": VALUES})
    assert r.status_code == 401


def test_new_single_pi(setup):
    c, boot = setup["c"], setup["boot"]
    tok = _token(c)
    assert c.post("/api/einrichtung/neu", json={"token": tok, "admin_password": "kurz", "values": VALUES}).status_code == 422
    r = c.post("/api/einrichtung/neu", json={"token": tok, "lang": "fr", "admin_password": "Theater-Fest-2026",
                                             "values": VALUES, "time_ms": 1_800_000_000_000})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "node": "a", "ca_key": True}
    conf = (boot / "festpos.conf").read_text()
    get = lambda k: backup.conf_value(conf, k)  # noqa: E731
    # Notfall-Passwort nur als Prüfwert auf der Karte (T-0-N/FestPOS#15)
    from app import emergency
    assert get("ADMIN_PASSWORD") is None and "Theater-Fest-2026" not in conf
    assert emergency.check("Theater-Fest-2026", get("ADMIN_PASSWORD_HASH")) and get("ADMIN_PASSWORD_LAENGE") == "17"
    assert get("ADMIN_PASSWORD_HASH") == emergency.make_hash("Theater-Fest-2026", get("CLUSTER_SECRET"))
    assert get("CLUSTER_SIZE") == "1" and get("RTC_AKKU") == "1"
    assert get("VIP") == "192.168.55.10" and len(get("CLUSTER_SECRET")) >= 32 and get("DHCP") == "1"
    assert (boot / "node.conf").read_text() == "NODE=a\n"
    tls = {n: (boot / "festpos-tls" / n).read_bytes() for n in backup.TLS_FILES}
    assert not (boot / "festpos-tls" / "ca.key").exists()                            # nie auf die Karte
    ca = x509.load_pem_x509_certificate(tls["ca.crt"])
    srv = x509.load_pem_x509_certificate(tls["server.crt"])
    srv.verify_directly_issued_by(ca)
    sans = srv.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {str(i) for i in sans.get_values_for_type(x509.IPAddress)} >= {"192.168.55.10", "192.168.55.13", "127.0.0.1"}
    assert ca.extensions.get_extension_for_class(x509.NameConstraints).critical
    # ca.key nur verschlüsselt: kein Download im Klartext, Passwort mindestens 12 Zeichen
    old = c.get(f"/api/einrichtung/ca.key?token={tok}")
    assert old.status_code in (404, 405) and b"PRIVATE KEY" not in old.content
    assert c.post("/api/einrichtung/ca.key", json={"token": tok, "password": "zu-kurz"}).status_code == 422
    assert c.post("/api/einrichtung/ca.key", json={"token": "falsch", "password": "Kassen-Ordner-26"}).status_code == 401
    key = c.post("/api/einrichtung/ca.key", json={"token": tok, "password": "Kassen-Ordner-26"})
    assert key.status_code == 200 and key.content.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    assert b"BEGIN PRIVATE KEY" not in key.content
    from cryptography.hazmat.primitives import serialization
    ca_key = serialization.load_pem_private_key(key.content, b"Kassen-Ordner-26")
    assert ca_key.public_key() == ca.public_key()                                   # passt zur CA
    assert c.get("/ca.crt").content == tls["ca.crt"]
    assert json.loads((setup["data"] / "start.json").read_text()) == {"lang": "fr"}
    # danach endgültig aus: weder die App noch der Helfer richten nochmals ein
    r = c.post("/api/einrichtung/neu", json={"token": tok, "admin_password": "Theater-Fest-2026", "values": VALUES})
    assert r.status_code == 409
    assert c.get("/api/einrichtung").json()["done"]["mode"] == "neu"


def test_ca_key_to_usb_stick_only_encrypted(setup):
    """ca.key auf den USB-Stick am Pi: der root-Helfer schreibt nur einen verschlüsselten Schlüssel (festpos-ca/),
    einen anderen ca.key auf dem Stick überschreibt er nicht."""
    c, env, tmp = setup["c"], setup["env"], setup["tmp"]
    stick = tmp / "stick"
    stick.mkdir()
    env["FESTPOS_STICK_ORDNER"] = str(stick)
    tok = _token(c)
    assert c.post("/api/einrichtung/neu", json={"token": tok, "admin_password": "Theater-Fest-2026",
                                                "values": VALUES}).status_code == 200
    assert c.post("/api/einrichtung/ca.key/usb", json={"token": tok, "password": "kurz"}).status_code == 422
    r = c.post("/api/einrichtung/ca.key/usb", json={"token": tok, "password": "Kassen-Ordner-26"})
    assert r.status_code == 200 and r.json()["path"] == "festpos-ca/ca.key", r.text
    key = (stick / "festpos-ca" / "ca.key").read_bytes()
    assert key.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    assert (stick / "festpos-ca" / "ca.crt").read_bytes() == c.get("/ca.crt").content
    r = c.post("/api/einrichtung/ca.key/usb", json={"token": tok, "password": "Kassen-Ordner-26"})
    assert r.status_code == 200 and r.json()["path"].startswith("festpos-ca/ca-")         # anderer Inhalt: daneben
    assert (stick / "festpos-ca" / "ca.key").read_bytes() == key
    # Klartext nimmt der Helfer nie an (auch nicht, wenn die App es versuchte)
    job = setup["mod"]._submit("ca_key_usb", {"key": "-----BEGIN PRIVATE KEY-----\nx\n", "crt": "-----BEGIN CERTIFICATE-----"})
    res = setup["mod"]._wait(job)
    assert res and not res["ok"] and res["code"] == "job"


def test_new_with_own_update_key(setup):
    """Eigener Update-Schlüssel (optional) kommt als festpos-update.pub auf die Boot-Partition; ein ungültiger wird
    abgewiesen, bevor etwas eingerichtet ist."""
    from tests.release_util import keypair
    c, boot = setup["c"], setup["boot"]
    tok = _token(c)
    body = {"token": tok, "admin_password": "Muster-Fest-2026", "values": VALUES}
    r = c.post("/api/einrichtung/neu", json={**body, "update_key": "ssh-rsa AAAAB3Nza kein-ed25519"})
    assert r.status_code == 422 and "Update-Schlüssel" in r.json()["detail"]
    assert not (boot / "festpos.conf").exists()
    _, line = keypair("Eigener Schlüssel")
    r = c.post("/api/einrichtung/neu", json={**body, "update_key": line + "\r\n"})
    assert r.status_code == 200, r.text
    assert (boot / "festpos-update.pub").read_text() == line.replace("ü", "") + "\n"   # Kommentar nur ASCII


def _helper(setup, job: dict) -> dict:
    jid = "0123456789abcdef"
    (setup["tmp"] / "jobs" / f"auftrag-{jid}.json").write_text(json.dumps({"id": jid, **job}))
    subprocess.run([sys.executable, "-I", str(WH), "--anfrage"], env=setup["env"], capture_output=True, timeout=120)
    return json.loads((setup["tmp"] / "out" / f"{jid}.json").read_text())


def test_helper_refuses_when_configured_or_values_bad(setup):
    bad = {**VALUES, "vip": "10.0.0.1"}
    assert _helper(setup, {"kind": "einrichtung", "mode": "neu", "values": {**bad, "admin_password": "x" * 12}})["code"] == "ips"
    v = {**VALUES, "cluster_size": 1, "node": "b", "admin_password": "x" * 12}
    assert _helper(setup, {"kind": "einrichtung", "mode": "neu", "values": v})["code"] == "node"
    (setup["boot"] / "festpos.conf").write_text(CONF)
    res = _helper(setup, {"kind": "einrichtung", "mode": "neu", "values": {**VALUES, "admin_password": "x" * 12}})
    assert res == {"ok": False, "code": "configured"}
    assert (setup["boot"] / "festpos.conf").read_text() == CONF


def test_from_backup(setup, client):
    """Ersatz-Pi: Schlüssel, CLUSTER_SECRET und Notfall-Passwort aus der Sicherung; die App spielt die Daten nach dem
    Neustart selbst ein."""
    from app import core, maintenance
    tls = make_tls("Fest")
    tdir = setup["tmp"] / "tls"
    tdir.mkdir()
    for n, v in tls.items():
        (tdir / n).write_bytes(v)
    core.Config.server_cert = str(tdir / "server.crt")
    client.put("/api/admin/settings", auth=AUTH, json={"event_name": "Herbstfest"})
    data = client.post("/api/admin/backup", auth=AUTH, json={"password": "Sicherung-2026-x"}).content
    c = setup["c"]
    tok = _token(c)
    r = c.post("/api/einrichtung/sicherung", headers={"X-Festpos-Setup": tok},
               data={"token": tok, "password": "falsch-falsch", "values": json.dumps(VALUES)},
               files={"file": ("s.festpos", data)})
    assert r.status_code == 422
    r = c.post("/api/einrichtung/sicherung", headers={"X-Festpos-Setup": tok},
               data={"token": tok, "password": "Sicherung-2026-x", "values": json.dumps(VALUES), "lang": "de"},
               files={"file": ("s.festpos", data)})
    assert r.status_code == 200, r.text
    boot = setup["boot"]
    conf = (boot / "festpos.conf").read_text()
    assert backup.conf_value(conf, "ADMIN_PASSWORD") == "geheim"
    assert (boot / "festpos-tls" / "server.key").read_bytes() == tls["server.key"]
    stash = setup["tmp"] / "stash"
    assert (stash / "sicherung.festpos").read_bytes() == data and oct((stash / "passwort").stat().st_mode)[-3:] == "600"
    # nach dem Neustart (hier: dieselbe App mit leerer Datenbank) spielt sie die Daten ein
    client.put("/api/admin/settings", auth=AUTH, json={"event_name": "leer"})
    import asyncio
    old = maintenance.STASH
    maintenance.STASH = stash
    try:
        asyncio.run(maintenance.after_setup())
    finally:
        maintenance.STASH = old
    assert core.rt.store.setting("event_name") == "Herbstfest"
    assert not (stash / "sicherung.festpos").exists() and not (stash / "passwort").exists()


def test_from_card_package(setup):
    """Pi B zu einem bestehenden Pi A: Ordner karte-b aus dem Kartenpaket."""
    tls = make_tls("A")
    buf = io.BytesIO()
    conf3 = backup.conf_set(backup.conf_set(CONF, "CLUSTER_SIZE", "3"), "CLUSTER_SECRET", "geheimnis-" + "x" * 30)
    conf3 = backup.conf_set(conf3, "NODE_A_IP", "192.168.55.11")
    with zipfile.ZipFile(buf, "w") as z:
        for letter in ("b", "c"):
            z.writestr(f"karte-{letter}/festpos.conf", conf3)
            z.writestr(f"karte-{letter}/node.conf", f"NODE={letter}\n")
            for n, v in tls.items():
                z.writestr(f"karte-{letter}/festpos-tls/{n}", v)
            z.writestr(f"karte-{letter}/festpos-update.pub", "ssh-ed25519 AAAA test")
        z.writestr("../boese.txt", "x")
    c = setup["c"]
    tok = _token(c)
    r = c.post("/api/einrichtung/kartenpaket", headers={"X-Festpos-Setup": tok}, data={"token": tok}, files={"file": ("k.zip", buf.getvalue())})
    assert r.json() == {"letters": ["b", "c"]}
    r = c.post("/api/einrichtung/kartenpaket", headers={"X-Festpos-Setup": tok}, data={"token": tok, "letter": "b"}, files={"file": ("k.zip", buf.getvalue())})
    assert r.status_code == 200, r.text
    boot = setup["boot"]
    assert (boot / "node.conf").read_text() == "NODE=b\n"
    assert backup.conf_value((boot / "festpos.conf").read_text(), "CLUSTER_SIZE") == "3"
    assert (boot / "festpos-tls" / "device-ca.key").read_bytes() == tls["device-ca.key"]
    assert (boot / "festpos-update.pub").exists()
    assert c.post("/api/einrichtung/kartenpaket", headers={"X-Festpos-Setup": tok}, data={"token": tok}, files={"file": ("x.zip", b"kein zip")}).status_code == 409


def test_uploads_need_the_session_key_before_anything_is_read(setup):
    """Review 2026-11, S13: Sicherung und Kartenpaket liest der Pi erst, wenn die Kopfzeile X-Festpos-Setup den
    Schlüssel aus der Code-Eingabe trägt – und nie mehr als die Höchstgrösse. Sonst könnte jeder im Netz während der
    Ersteinrichtung beliebig grosse Dateien schicken."""
    from app import setup_mode
    c = setup["c"]
    gross = b"x" * (200 * 1024)
    for path in ("/api/einrichtung/sicherung", "/api/einrichtung/kartenpaket"):
        assert c.post(path, data={"token": "x"}, files={"file": ("f", gross)}).status_code == 401
        assert c.post(path, headers={"X-Festpos-Setup": "falsch"}, data={"token": "x"}, files={"file": ("f", gross)}).status_code == 401
    tok = _token(c)
    r = c.post("/api/einrichtung/kartenpaket", headers={"X-Festpos-Setup": tok}, data={"token": tok},
               files={"file": ("k.zip", b"x" * (setup_mode.UPLOAD_LIMITS["/api/einrichtung/kartenpaket"] + 1))})
    assert r.status_code == 413
    # alle anderen Anfragen sind klein
    assert c.post("/api/einrichtung/code", content=b"x" * (setup_mode.DEFAULT_LIMIT + 1),
                  headers={"Content-Type": "application/json"}).status_code == 413
    assert "X-Festpos-Setup" in (ROOT / "static" / "einrichtung.js").read_text()


def test_certs_like_make_certs():
    made = zertifikate.make_all("10.1.2.0/24", ["10.1.2.10", "10.1.2.11", "10.1.2.12", "10.1.2.13"])
    ca = x509.load_pem_x509_certificate(made["ca.crt"])
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    assert {str(s.value) for s in nc.permitted_subtrees} >= {"10.1.2.0/24", "127.0.0.1/32", "festpos.local", "localhost"}
    srv = x509.load_pem_x509_certificate(made["server.crt"])
    eku = srv.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert len(list(eku)) == 2
    assert isinstance(srv.public_key(), ec.EllipticCurvePublicKey)
    with pytest.raises(ValueError):
        zertifikate.make_all("10.1.2.0/24", ["10.9.9.9"])


def test_units_and_node_script():
    unit = (ROOT / "deploy" / "systemd" / "festpos-einrichtung.service").read_text()
    assert "ConditionPathExists=/run/festpos/einrichtung" in unit and "app.setup_serve" in unit
    for name in ("festpos.service", "rqlited.service", "festpos-redirect.service"):
        assert "ConditionPathExists=!/run/festpos/einrichtung" in (ROOT / "deploy" / "systemd" / name).read_text(), name
    node = (ROOT / "deploy" / "festpos-node.sh").read_text()
    assert 'if [ ! -f "$CONF" ]; then' in node and "festpos-einrichtung.txt" in node
    assert "openssl req -x509" in node and "tcp dport { 80, 443 }" in node and "tcp dport 22 ct state new limit rate" in node and 'rm -rf "$EINR_DIR/tls"' in node
    assert "festpos.conf.example" not in (ROOT / "deploy" / "setup.sh").read_text().split("Konfiguration auf der Boot")[1][:600]


def test_setup_serve_https_and_redirect(tmp_path, monkeypatch):
    """B1: https mit vorläufigem Zertifikat, Port 80 leitet nur um; ohne Zertifikat Rückfall http."""
    import asyncio
    from app import setup_serve, zertifikate
    made = zertifikate.make_all("192.168.55.0/24", ["192.168.55.10"])
    (tmp_path / "c.pem").write_bytes(made["server.crt"])
    (tmp_path / "k.pem").write_bytes(made["server.key"])
    monkeypatch.setattr(setup_serve, "CERT", tmp_path / "c.pem")
    monkeypatch.setattr(setup_serve, "KEY", tmp_path / "k.pem")
    srv = setup_serve.servers()
    assert [s.config.port for s in srv] == [443, 80] and srv[0].config.ssl_certfile == str(tmp_path / "c.pem")
    monkeypatch.setattr(setup_serve, "CERT", tmp_path / "fehlt.pem")
    assert [s.config.port for s in setup_serve.servers()] == [80]
    sent = []

    async def send(m):
        sent.append(m)
    asyncio.run(setup_serve.redirect({"type": "http", "path": "/admin.html", "headers": [(b"host", b"festpos.local")]},
                                     None, send))
    assert sent[0]["status"] == 308 and (b"location", b"https://festpos.local/admin.html") in sent[0]["headers"]
