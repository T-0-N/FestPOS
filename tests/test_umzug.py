"""Netz umziehen im Betrieb (T-0-N/FestPOS#2, Teil 2): Adressen umrechnen, root-Helfer festpos-netz (vorbereiten,
umstellen mit peers.json, Frist und Rückfall), Ablauf in der App (Plan, Start, Umrechnen der Datenbank, Bestätigen nur
über die neue Adresse, Rückfall), Übergangszeit mit zwei Zertifikaten in app/serve.py."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography import x509

from app import backup, netz, zertifikate
from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-netz"
OLD = ipaddress.IPv4Network("192.168.55.0/24")
NEW = ipaddress.IPv4Network("10.20.0.0/22")
CONF = """# FestPOS
CLUSTER_SIZE=3
NET=192.168.55.0/24
NET_PREFIX=192.168.55
NET_CIDR=24
VIP=192.168.55.10
NODE_A_IP=192.168.55.11
NODE_B_IP=192.168.55.12
NODE_C_IP=192.168.55.13
DHCP_POOL=192.168.55.150-192.168.55.189
INTERNET_GATEWAY=192.168.55.1
ADMIN_FROM=192.168.55.200-192.168.55.220
CLUSTER_SECRET=im-container
IFACE=eth0
"""
NEW_PLAN = {"net": "10.20.0.0/22", "vip": "10.20.0.10", "a": "10.20.0.11", "b": "10.20.0.12", "c": "10.20.0.13",
            "dhcp_from": "10.20.1.1", "dhcp_to": "10.20.3.200"}
OLD_PLAN = {"net": "192.168.55.0/24", "vip": "192.168.55.10", "a": "192.168.55.11", "b": "192.168.55.12",
            "c": "192.168.55.13"}
LOGGER = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ["FAKE_LOG"], "a") as f: f.write(os.path.basename(sys.argv[0]) + " " + " ".join(sys.argv[1:]) + "\n")
'''


# ------------------------------------------------------------------ Adressen umrechnen
def test_move_ip_and_text():
    assert netz.move_ip("192.168.55.21", OLD, NEW) == "10.20.0.21"
    assert netz.move_ip("192.168.56.21", OLD, NEW) is None                       # nicht im alten Netz
    assert netz.move_ip("192.168.55.200", OLD, ipaddress.IPv4Network("172.16.5.0/26")) is None    # passt nicht
    t, missing = netz.move_text('[{"ip": "192.168.55.101"}, {"ip": "8.8.8.8"}] Switch=192.168.55.30', OLD, NEW)
    assert t == '[{"ip": "10.20.0.101"}, {"ip": "8.8.8.8"}] Switch=10.20.0.30' and missing == []
    assert netz.move_text("192.168.55.200", OLD, ipaddress.IPv4Network("172.16.5.0/26"))[1] == ["192.168.55.200"]


def test_move_certs_keep_server_key_and_constrain_to_new_net():
    old = zertifikate.make_all(str(OLD), ["192.168.55.10", "192.168.55.11"])
    new = zertifikate.for_move(str(NEW), ["10.20.0.10", "10.20.0.11"], old["server.crt"])
    srv, ca = (x509.load_pem_x509_certificate(new[k]) for k in ("server.crt", "ca.crt"))
    assert srv.public_key() == x509.load_pem_x509_certificate(old["server.crt"]).public_key()
    assert srv.issuer == ca.subject and "ca.key" in new and "server.key" not in new
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    assert "10.20.0.0/22" in {str(x.value) for x in nc.permitted_subtrees if isinstance(x, x509.IPAddress)}


# ------------------------------------------------------------------ root-Helfer
@pytest.fixture
def pi(tmp_path):
    r, boot = tmp_path / "root", tmp_path / "boot"
    (boot / "festpos-tls").mkdir(parents=True)
    old = zertifikate.make_all(str(OLD), ["192.168.55.10", "192.168.55.11", "192.168.55.12", "192.168.55.13"])
    for n in ("ca.crt", "server.crt", "server.key", "device-ca.crt"):
        (boot / "festpos-tls" / n).write_bytes(old[n])
    (boot / "festpos.conf").write_text(CONF)
    (r / "var/lib/festpos/rqlite/raft").mkdir(parents=True)
    log = tmp_path / "log"
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("systemctl", "systemd-run"):
        (tools / name).write_text(LOGGER)
        (tools / name).chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "FESTPOS_RELEASE_LIB": str(ROOT), "FESTPOS_TEST_ROOT": str(r),
           "FESTPOS_BOOT": str(boot), "FESTPOS_SYSTEMCTL": str(tools / "systemctl"),
           "FESTPOS_SYSTEMD_RUN": str(tools / "systemd-run"), "FAKE_LOG": str(log)}
    new = zertifikate.for_move(str(NEW), ["10.20.0.10", "10.20.0.11", "10.20.0.12", "10.20.0.13"], old["server.crt"])
    now = int(time.time())
    job = {"id": "abc123", "at": now + 60, "deadline": now + 900, "until": now + 90000, "old": OLD_PLAN,
           "new": NEW_PLAN, "ca_crt": new["ca.crt"].decode(), "server_crt": new["server.crt"].decode()}

    def run(*args, stdin=None):
        p = subprocess.run([sys.executable, str(HELPER), *args], env=env, input=json.dumps(stdin) if stdin else None,
                           capture_output=True, text=True, timeout=60)
        out = (p.stdout.strip().splitlines() or ["{}"])[-1]
        return p, json.loads(out) if out.startswith("{") else {}
    return {"r": r, "boot": boot, "run": run, "job": job, "old": old, "new": new, "log": log,
            "state": r / "var/lib/festpos-netz/umzug.json", "peers": r / "var/lib/festpos/rqlite/raft/peers.json"}


def conf(pi, name="festpos.conf"):
    text = (pi["boot"] / name).read_text()
    return lambda k: backup.conf_value(text, k)  # noqa: E731


def test_prepare_writes_new_conf_and_certs_and_plans_switch(pi):
    p, res = pi["run"]("--vorbereiten", stdin=pi["job"])
    assert res == {"ok": True}, p.stderr
    c = conf(pi, "festpos.conf.umzug")
    assert c("NET") == "10.20.0.0/22" and c("NET_CIDR") == "22" and c("VIP") == "10.20.0.10"
    assert c("NODE_B_IP") == "10.20.0.12" and c("DHCP_POOL") == "10.20.1.1-10.20.3.200"
    assert c("INTERNET_GATEWAY") == "10.20.0.1" and c("ADMIN_FROM") == "10.20.0.200-10.20.0.220"   # umgerechnet
    assert c("CLUSTER_SECRET") == "im-container" and c("IFACE") == "eth0"          # Rest bleibt, auch verschlüsselt
    assert c("NET_ALT") == "192.168.55.0/24" and c("VIP_ALT") == "192.168.55.10" and c("NODE_C_IP_ALT") == "192.168.55.13"
    assert c("NET_ALT_BIS") == str(pi["job"]["until"])
    assert (pi["boot"] / "festpos.conf").read_text() == CONF                       # noch nichts umgestellt
    assert (pi["boot"] / "festpos-tls.umzug/ca.crt").read_bytes() == pi["new"]["ca.crt"]
    assert (pi["boot"] / "festpos-tls.umzug/server.key").read_bytes() == pi["old"]["server.key"]
    assert "systemd-run --unit=festpos-netz-umstellen" in pi["log"].read_text()


@pytest.mark.parametrize("change, code", [
    (lambda j: j.update(server_crt=zertifikate.for_move(str(NEW), ["10.20.0.10"], zertifikate.make_all(
        str(NEW), ["10.20.0.10"])["server.crt"])["server.crt"].decode()), "tls"),             # anderer Schlüssel
    (lambda j: j.update(new={**NEW_PLAN, "net": "192.168.55.0/25"}), "net"),             # überschneidet das alte
    (lambda j: j.pop("at"), "job"),
])
def test_prepare_refuses_bad_jobs(pi, change, code):
    change(pi["job"])
    p, res = pi["run"]("--vorbereiten", stdin=pi["job"])
    assert res == {"ok": False, "code": code}, p.stderr
    assert not (pi["boot"] / "festpos.conf.umzug").exists()


def test_switch_then_confirm(pi):
    pi["run"]("--vorbereiten", stdin={**pi["job"], "at": int(time.time())})
    pi["run"]("--umstellen")
    assert conf(pi)("NET") == "10.20.0.0/22" and (pi["boot"] / "festpos.conf.vor-umzug").read_text() == CONF
    assert (pi["boot"] / "festpos-tls/ca.crt").read_bytes() == pi["new"]["ca.crt"]
    assert (pi["boot"] / "festpos-tls-alt/ca.crt").read_bytes() == pi["old"]["ca.crt"]          # Übergangszeit
    assert not (pi["boot"] / "festpos-tls-alt/server.key").exists()                          # gleicher Schlüssel
    assert json.loads(pi["peers"].read_text()) == [
        {"id": k, "address": f"10.20.0.1{i}:4002", "non_voter": False} for i, k in ((1, "a"), (2, "b"), (3, "c"))]
    assert pi["log"].read_text().rstrip().endswith("systemctl reboot --no-block")
    assert json.loads(pi["state"].read_text())["phase"] == "umgestellt"
    pi["run"]("--wache")                                                          # Frist noch nicht vorbei
    assert pi["state"].exists()
    p, res = pi["run"]("--bestaetigt", stdin={"id": "abc123"})
    assert res == {"ok": True} and not pi["state"].exists()
    assert not (pi["boot"] / "festpos.conf.vor-umzug").exists() and (pi["boot"] / "festpos-tls-alt").is_dir()


def test_no_confirmation_falls_back(pi):
    pi["run"]("--vorbereiten", stdin={**pi["job"], "at": int(time.time()), "deadline": int(time.time()) + 1})
    pi["run"]("--umstellen")
    time.sleep(1.2)
    pi["run"]("--wache")
    assert (pi["boot"] / "festpos.conf").read_text() == CONF and not pi["state"].exists()
    assert (pi["boot"] / "festpos-tls/ca.crt").read_bytes() == pi["old"]["ca.crt"]
    assert not (pi["boot"] / "festpos-tls-alt").exists()
    assert json.loads(pi["peers"].read_text())[0]["address"] == "192.168.55.11:4002"           # alte Adressen
    assert pi["log"].read_text().count("reboot") == 2


@pytest.mark.parametrize("deadline_s, expect_net", [(900, "10.20.0.0/22"), (-1, "192.168.55.0/24")])
def test_interrupted_switch_resumes_or_restores_original(pi, deadline_s, expect_net):
    """Strom weg mitten im Umstellen (Phase «wechsel», halb neue Dateien): die Wache macht innert Frist fertig, danach
    zurück – immer mit der Sicherung der ursprünglichen Dateien, nie mit einer der halb neuen."""
    now = int(time.time())
    pi["run"]("--vorbereiten", stdin={**pi["job"], "at": now, "deadline": now + deadline_s})
    st = json.loads(pi["state"].read_text())
    (pi["boot"] / "festpos.conf.vor-umzug").write_text(CONF)                      # Sicherung schon geschrieben …
    (pi["boot"] / "festpos-tls.vor-umzug").mkdir()
    for n in ("ca.crt", "server.crt", "server.key"):
        (pi["boot"] / "festpos-tls.vor-umzug" / n).write_bytes((pi["boot"] / "festpos-tls" / n).read_bytes())
    (pi["boot"] / "festpos.conf").write_text((pi["boot"] / "festpos.conf.umzug").read_text())   # … halb neu
    (pi["boot"] / "festpos-tls/ca.crt").write_bytes(pi["new"]["ca.crt"])
    pi["state"].write_text(json.dumps({**st, "phase": "wechsel"}))
    p, res = pi["run"]("--vorbereiten", stdin=pi["job"])
    assert res == {"ok": False, "code": "running"}, p.stderr                       # kein neuer Umzug dazwischen
    pi["run"]("--wache")
    assert conf(pi)("NET") == expect_net
    if deadline_s > 0:
        assert json.loads(pi["state"].read_text())["phase"] == "umgestellt"
        assert (pi["boot"] / "festpos.conf.vor-umzug").read_text() == CONF      # Sicherung nicht überschrieben
        pi["run"]("--zurueck")
        assert (pi["boot"] / "festpos.conf").read_text() == CONF
    assert (pi["boot"] / "festpos-tls/ca.crt").read_bytes() == pi["old"]["ca.crt"]     # am Ende wieder das alte Netz
    assert (pi["boot"] / "festpos.conf").read_text() == CONF and not pi["state"].exists()


def test_cancel_before_switch_and_missed_switch(pi):
    pi["run"]("--vorbereiten", stdin=pi["job"])
    p, res = pi["run"]("--zurueck")
    assert res == {"ok": True} and not pi["state"].exists() and not (pi["boot"] / "festpos.conf.umzug").exists()
    assert "reboot" not in pi["log"].read_text()
    # Pi war zum Zeitpunkt aus: innert 5 Minuten nachholen, später verwerfen
    pi["run"]("--vorbereiten", stdin={**pi["job"], "at": int(time.time()) - 400})
    pi["run"]("--wache")
    assert not pi["state"].exists() and (pi["boot"] / "festpos.conf").read_text() == CONF
    pi["run"]("--vorbereiten", stdin={**pi["job"], "at": int(time.time()) - 10})
    pi["run"]("--wache")
    assert conf(pi)("NET") == "10.20.0.0/22"


def test_node_script_has_transition():
    node = (ROOT / "deploy/festpos-node.sh").read_text()
    assert 'WANT="$WANT,$ALT_NODE_IP/$ALT_CIDR"' in node and "${ALT_VIP:+        $ALT_VIP/$ALT_CIDR dev $IFACE}" in node
    assert 'ip saddr $SRC_SET tcp dport { 80, 443, 8443 } accept' in node and "festpos_env_line FESTPOS_LISTEN_ALT" in node
    assert "NET_ALT_BIS" in node and 'rm -rf "$BOOT/festpos-tls-alt"' in node
    assert "festpos-netz festpos-netz 0755" in (ROOT / "deploy/install-lib.sh").read_text()


# ------------------------------------------------------------------ App
@pytest.fixture
def move(client, monkeypatch, tmp_path):
    import types

    from app import cards, core, ledger, main, umzug
    monkeypatch.setattr(main, "umzug_mod", types.SimpleNamespace(tick=lambda: None))   # Takt im Hintergrund: nur der Test
    monkeypatch.setattr(core.Config, "peers", [f"https://192.168.55.1{i}:8443" for i in (1, 2, 3)])
    monkeypatch.setattr(core.Config, "node", "a")
    monkeypatch.setattr(core.Config, "node_ip", "192.168.55.11")
    monkeypatch.setenv("FESTPOS_NET", "192.168.55.0/24")
    monkeypatch.setenv("FESTPOS_VIP", "192.168.55.10")
    old = zertifikate.make_all(str(OLD), ["192.168.55.10"])
    (tmp_path / "server.crt").write_bytes(old["server.crt"])
    monkeypatch.setattr(core.Config, "server_cert", str(tmp_path / "server.crt"))
    monkeypatch.setattr(umzug, "_fresh_backup", lambda: (True, time.time()))
    monkeypatch.setattr(ledger, "open_orders", lambda store: 0)
    from app import system
    monkeypatch.setattr(system, "nodes", lambda info=None: [{"id": k, "reachable": True} for k in "abc"])
    jobs = []
    monkeypatch.setattr(cards, "available", lambda: True)
    monkeypatch.setattr(cards, "submit", lambda kind, payload=None: jobs.append((kind, payload)) or str(len(jobs)))
    monkeypatch.setattr(cards, "wait", lambda job, timeout=30.0: {"ok": True})
    store = umzug.rt.store
    store.execute(umzug.UPSERT, ("monitor_hosts", "Switch=192.168.55.30; Access Point=192.168.55.40"))
    store.execute(umzug.UPSERT, ("monitor_devices", json.dumps([{"id": "p1", "name": "Kasse 1", "kind": "phone",
                                                                 "ip": "192.168.55.101", "active": True}])))
    store.execute("INSERT INTO printers(name, host, port) VALUES ('Küche', '192.168.55.51', 9100)")
    umzug._local.update(prepared=None, back=None, confirmed=None, reported=0.0, phones=0.0)
    return {"jobs": jobs, "store": store, "umzug": umzug}


def test_plan_check_lists_manual_steps_and_blockers(client, move, monkeypatch):
    r = client.post("/api/admin/umzug/pruefen", auth=AUTH, json=NEW_PLAN).json()
    assert r["errors"] == [] and r["blockers"] == [] and r["range"]["hosts"] == 1022
    manual = {(x["kind"], x["old"], x["new"]) for x in r["manual"]}
    assert ("printer", "192.168.55.51", "10.20.0.51") in manual and ("device", "192.168.55.30", "10.20.0.30") in manual
    assert r["auto"]["pis"] == 3
    bad = client.post("/api/admin/umzug/pruefen", auth=AUTH, json={**NEW_PLAN, "net": "192.168.55.0/25",
                                                                    "vip": "192.168.55.10"}).json()
    assert bad["errors"]
    from app import ledger
    monkeypatch.setattr(ledger, "open_orders", lambda store: 3)
    assert "open_orders" in client.post("/api/admin/umzug/pruefen", auth=AUTH, json=NEW_PLAN).json()["blockers"]
    assert client.post("/api/admin/umzug/start", auth=AUTH, json=NEW_PLAN).status_code == 409


def test_start_switch_confirm_and_fallback(client, move, monkeypatch):
    u, store = move["umzug"], move["store"]
    tok = client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).json()["token"]
    assert client.post("/api/admin/umzug/start", headers={"Authorization": f"Bearer {tok}"},
                       json=NEW_PLAN).status_code == 428                         # kritisch
    m = client.post("/api/admin/umzug/start", auth=AUTH, json=NEW_PLAN).json()
    assert m["phase"] == "prepared" and "ca_key" not in m and m["new"]["net"] == "10.20.0.0/22"
    assert u.load()["ca_key"].startswith("enc1:")                                # verschlüsselt in der Datenbank
    u.tick()                                                                      # dieser Pi: vorbereiten
    kind, payload = move["jobs"][-1]
    assert kind == "netz_vorbereiten" and payload["ca_crt"].startswith("-----BEGIN CERTIFICATE")
    # B und C melden sich vorbereitet; kurz vor «at» rechnet der druckende Pi die Datenbank um
    for n in "bc":
        store.execute(u.UPSERT, (u.NODE_KEY + n, json.dumps({"id": m["id"], "prepared": 1, "net": str(OLD),
                                                             "at": int(time.time())})))
    monkeypatch.setattr(u.rt.spooler, "is_leader", True, raising=False)
    store.execute(u.UPSERT, (u.KEY, json.dumps({**u.load(), "at": int(time.time()) + 5})))
    u.tick()
    assert u.load()["phase"] == "switching"
    assert "10.20.0.30" in store.setting("monitor_hosts") and "10.20.0.101" in store.setting("monitor_devices")
    assert store.scalar("SELECT host FROM printers WHERE name='Küche'") == "10.20.0.51"
    # Bestätigen nur über die neue Adresse und erst, wenn alle Pi im neuen Netz sind
    r = client.post("/api/admin/umzug/bestaetigen", auth=AUTH)
    assert r.status_code == 409 and "10.20.0.10" in r.json()["detail"]
    # kein Bestätigen: Pi stellen selbst zurück -> wieder im alten Netz, Datenbank zurückgerechnet
    store.execute(u.UPSERT, (u.KEY, json.dumps({**u.load(), "at": int(time.time()) - 100,
                                                "deadline": int(time.time()) - 1})))
    u.tick()
    assert u.load()["phase"] == "back" and store.scalar("SELECT host FROM printers WHERE name='Küche'") == "192.168.55.51"
    assert "192.168.55.30" in store.setting("monitor_hosts")
    acts = [r["action"] for r in store.query("SELECT action FROM audit_log WHERE action LIKE 'net_move%' ORDER BY id")]
    assert acts == ["net_move_start", "net_move_switch", "net_move_back"]


def test_confirm_over_new_address(client, move, monkeypatch):
    u, store = move["umzug"], move["store"]
    m = client.post("/api/admin/umzug/start", auth=AUTH, json=NEW_PLAN).json()
    store.execute(u.UPSERT, (u.KEY, json.dumps({**u.load(), "phase": "switching"})))
    monkeypatch.setenv("FESTPOS_NET", "10.20.0.0/22")                           # nach dem Neustart
    for n in "abc":
        store.execute(u.UPSERT, (u.NODE_KEY + n, json.dumps({"id": m["id"], "net": "10.20.0.0/22",
                                                             "at": int(time.time())})))
    from fastapi.testclient import TestClient
    from app import main
    new = TestClient(main.app, base_url="https://10.20.0.10:8443")
    r = new.post("/api/admin/umzug/bestaetigen", auth=AUTH)
    assert r.status_code == 200 and r.json()["phase"] == "done", r.text
    u.tick()
    assert ("netz_bestaetigt", {"id": m["id"]}) in move["jobs"]
    short = client.post("/api/admin/umzug/ca-key", auth=AUTH, json={"password": "kurz"})
    assert short.status_code == 422
    key = client.post("/api/admin/umzug/ca-key", auth=AUTH, json={"password": "Neuer-CA-Schluessel-1"})
    assert key.status_code == 200 and key.content.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY")
    assert "ca_key" not in u.load() and client.post("/api/admin/umzug/ca-key", auth=AUTH,
                                                    json={"password": "Neuer-CA-Schluessel-1"}).status_code == 409


def test_manual_back_before_switch(client, move):
    u = move["umzug"]
    client.post("/api/admin/umzug/start", auth=AUTH, json=NEW_PLAN)
    assert client.post("/api/admin/umzug/zurueck", auth=AUTH).json()["phase"] == "aborted"
    u.tick()
    assert move["jobs"][-1][0] == "netz_zurueck"


def test_late_phone_result_never_undoes_back(client, move):
    """Die CA geht im Hintergrund auf die Handhelds; kommt ihr Ergebnis erst nach «Zurück», bleibt es zurück."""
    u = move["umzug"]
    started = client.post("/api/admin/umzug/start", auth=AUTH, json=NEW_PLAN).json()
    stale = u.load()
    assert client.post("/api/admin/umzug/zurueck", auth=AUTH).json()["phase"] == "aborted"
    u._set_phones(started["id"], {"Kasse 1": "ok"})
    assert u.load()["phase"] == "aborted" and u.load()["phones"] == {"Kasse 1": "ok"}
    assert not u._change(stale, {"phase": "switching"}) and u.load()["phase"] == "aborted"


# ------------------------------------------------------------------ Übergangszeit: zwei Zertifikate
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_serve_uses_old_certificate_on_old_address(tmp_path):
    new = zertifikate.make_all("127.0.0.0/24", ["127.0.0.1"])
    alt = zertifikate.for_move("127.0.1.0/24", ["127.0.1.1"], new["server.crt"])
    for name, data in (("server.crt", new["server.crt"]), ("server.key", new["server.key"]),
                       ("alt-server.crt", alt["server.crt"])):
        (tmp_path / name).write_bytes(data)
    admin, phone = _free_port(), _free_port()
    env = {**os.environ, "FESTPOS_DB": f"sqlite://{tmp_path}/b.db", "FESTPOS_ADMIN_PASSWORD": "geheim",
           "FESTPOS_STATIC": str(ROOT / "static"), "FESTPOS_NODE": "a", "FESTPOS_PHONE_CERTS": "0",
           "FESTPOS_ADMIN_LISTEN": str(admin), "FESTPOS_PHONE_LISTEN": str(phone),
           "FESTPOS_TLS_CERT": str(tmp_path / "server.crt"), "FESTPOS_TLS_KEY": str(tmp_path / "server.key"),
           "FESTPOS_LISTEN": "127.0.0.1", "FESTPOS_LISTEN_ALT": "127.0.1.1",
           "FESTPOS_TLS_ALT_CERT": str(tmp_path / "alt-server.crt")}
    proc = subprocess.Popen([sys.executable, "-m", "app.serve"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        def issuer(host: str, port: int) -> bytes | None:
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            try:
                with socket.create_connection((host, port), timeout=1) as s, ctx.wrap_socket(s) as t:
                    der = t.getpeercert(binary_form=True)
            except OSError:
                return None
            return der
        for _ in range(100):
            if issuer("127.0.1.1", phone):
                break
            time.sleep(0.1)
        from cryptography.hazmat.primitives import serialization
        ca_new = x509.load_pem_x509_certificate(new["server.crt"]).public_bytes(serialization.Encoding.DER)
        ca_alt = x509.load_pem_x509_certificate(alt["server.crt"]).public_bytes(serialization.Encoding.DER)
        assert ca_new != ca_alt
        assert issuer("127.0.0.1", admin) == ca_new and issuer("127.0.0.1", phone) == ca_new
        assert issuer("127.0.1.1", admin) == ca_alt and issuer("127.0.1.1", phone) == ca_alt
    finally:
        proc.terminate()
        proc.wait(10)


def test_backup_from_old_net_is_moved_and_keys_refused(client, move, monkeypatch):
    """Sicherung von vor dem Umzug: Adressen in der Datenbank werden umgerechnet, ihre Schlüssel nie eingespielt."""
    u, store = move["umzug"], move["store"]
    monkeypatch.setattr(u.rt.spooler, "is_leader", True, raising=False)
    u.tick()
    assert store.setting("net_current") == "192.168.55.0/24"                     # Netz der Datenbank gemerkt
    monkeypatch.setenv("FESTPOS_NET", "10.20.0.0/22")                           # Pi im neuen Netz, Daten alt
    u.tick()
    assert store.setting("net_current") == "10.20.0.0/22" and "10.20.0.30" in store.setting("monitor_hosts")
    assert store.scalar("SELECT host FROM printers WHERE name='Küche'") == "10.20.0.51"
    assert store.scalar("SELECT COUNT(*) FROM audit_log WHERE action='net_restore_moved'") == 1
    from app import maintenance
    old = zertifikate.make_all(str(OLD), ["192.168.55.10"])
    assert maintenance._other_net({"ca.crt": old["ca.crt"]})
    assert not maintenance._other_net({"ca.crt": zertifikate.make_all(str(NEW), ["10.20.0.10"])["ca.crt"]})


def test_phone_gets_new_address_only_with_new_ca(monkeypatch):
    """Ein Handheld ohne neue CA bekommt die neue Adresse nie (es vertraute ihr nicht) und bleibt offen bis zu beidem."""
    import asyncio

    from app import fully, umzug
    cfg = {"port": 2323, "password": "pw", "targets": [{"name": "Kasse 1", "ip": "10.20.0.101", "pin": None}]}
    monkeypatch.setattr(fully, "config", lambda: cfg)
    sent, ca_state = [], ["unreachable"]

    async def deliver(args, host, files):
        return {"state": ca_state[0]}

    async def command(ip, port, pin, password, cmd, **kw):
        sent.append(cmd)
        return {"state": "ok"}
    monkeypatch.setattr(fully, "deliver", deliver)
    monkeypatch.setattr(fully, "setup_command", command)
    m = {"old": OLD_PLAN, "new": NEW_PLAN, "ca_crt": "x", "phones": {}}
    phones = asyncio.run(umzug.phones_url(m))
    assert phones["Kasse 1"] == {"ca": "unreachable", "url": "unreachable"} and sent == []
    ca_state[0] = "ok"
    phones = asyncio.run(umzug.phones_url({**m, "phones": phones}))
    assert phones["Kasse 1"] == {"ca": "ok", "url": "ok"} and sent[0] == "installUserCa" and "restartApp" in sent
