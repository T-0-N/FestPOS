"""deploy/festpos-rqlited mit echtem rqlited.

REV-009: ein Knoten, dessen Beitritt scheitert, darf beim nächsten Start NIE einen eigenen Cluster gründen.
REV-029: nach dem Notfall-Einzelbetrieb dürfen die zurückkehrenden Pi die neuen Daten nicht überschreiben.
Ausbau 1 -> 3 Pi wird dabei einmal komplett durchgespielt.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import cluster
from tests.conftest import RQLITED, free_port, wait_for

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
WRAPPER = DEPLOY / "festpos-rqlited"
pytestmark = pytest.mark.skipif(not Path(RQLITED).exists(), reason="rqlited nicht gefunden")


class Node:
    def __init__(self, tmp: Path, name: str, size: int):
        self.name, self.size = name, size
        self.http, self.raft = free_port(), free_port()
        self.data = tmp / name
        self.flag = tmp / f"{name}.gruenden"
        self.envfile = tmp / f"{name}.env"
        self.join = ""
        self.peers = ""                     # Basis-URLs der «App» der anderen Pi (FakeApp)
        self.extra: dict[str, str] = {}     # weitere Zeilen für /etc/festpos/env (z.B. RAFT_TLS)
        self.proc: subprocess.Popen | None = None
        self.out = b""

    def start(self, attempts: int = 30):
        self.data.mkdir(exist_ok=True)
        self.envfile.write_text(f'FESTPOS_NODE="{self.name}"\nNODE_IP="127.0.0.1"\nJOIN_OTHERS="{self.join}"\n'
                                f'CLUSTER_SIZE="{self.size}"\n' + "".join(f'{k}="{v}"\n' for k, v in self.extra.items()))
        env = {**os.environ, "FESTPOS_RQ_ENVFILE": str(self.envfile), "FESTPOS_RQ_DATA": str(self.data),
               "FESTPOS_RQ_FLAG": str(self.flag), "FESTPOS_RQ_BIN": RQLITED,
               "FESTPOS_RQ_HTTP": f"127.0.0.1:{self.http}", "FESTPOS_RQ_RAFT_PORT": str(self.raft),
               "FESTPOS_RQ_JOIN_ATTEMPTS": str(attempts), "FESTPOS_RQ_JOIN_INTERVAL": "500ms",
               "FESTPOS_RQ_EXTRA": "-raft-election-timeout 500ms -raft-heartbeat-timeout 500ms",
               "FESTPOS_RQ_PEERS": self.peers, "FESTPOS_RQ_PEER_WAIT": "2", "FESTPOS_RQ_REFUSE_SLEEP": "0"}
        self.proc = subprocess.Popen(["bash", str(WRAPPER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return self

    def wait_exit(self, timeout=30) -> int:
        out, _ = self.proc.communicate(timeout=timeout)
        self.out = out
        return self.proc.returncode

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            out, _ = self.proc.communicate(timeout=10)
            self.out = out

    def status(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.http}/status", timeout=2) as r:
            return json.load(r)["store"]

    def state(self) -> str | None:
        try:
            return self.status()["raft"]["state"]
        except Exception:  # noqa: BLE001
            return None

    def execute(self, sql: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.http}/db/execute", data=json.dumps([sql]).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def nodes(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.http}/nodes", timeout=2) as r:
            return json.load(r)

    def rows(self) -> list[str]:
        """Lokal gelesen (ohne Leader) – zeigt, was auf DIESEM Knoten liegt."""
        q = urllib.parse.quote("SELECT v FROM probe ORDER BY v")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.http}/db/query?level=none&q={q}", timeout=2) as r:
            res = json.load(r)["results"][0]
        return [v[0] for v in res.get("values") or []]

    def leader(self) -> str | None:
        try:
            return (self.status().get("leader") or {}).get("node_id") or None
        except Exception:  # noqa: BLE001
            return None


class FakeApp:
    """Steht für die FestPOS-App eines Pi: beantwortet /api/health/cluster wie app.main (gleiche Funktion)."""

    def __init__(self, node: Node):
        self.node, self.online = node, True
        app = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if not app.online or self.path != "/api/health/cluster":
                    self.send_response(503); self.end_headers(); return
                try:
                    status = {"store": app.node.status()}
                except Exception:  # noqa: BLE001
                    status = None
                body = json.dumps(cluster.summarize(status, app.node.name, app.node.data)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def shell(fn: str, *args: str) -> str:
    r = subprocess.run(["bash", "-c", f'source "{DEPLOY}/festpos-conf.sh"; {fn} "$@"', "_", *args],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def nodes(tmp_path):
    made: list[Node] = []

    def make(name, size):
        n = Node(tmp_path, name, size)
        made.append(n)
        return n
    yield make
    for n in made:
        n.stop()


@pytest.fixture
def three(tmp_path):
    """Ausbau wie in der Anleitung: A gründet im Einzelbetrieb, wird auf 3 umgestellt, B und C treten bei.
    Danach fallen B und C aus, A läuft mit «festpos-notfall einzelbetrieb» allein weiter und erfasst Neues."""
    ns = {k: Node(tmp_path, k, 3) for k in "abc"}
    apps = {k: FakeApp(n) for k, n in ns.items()}
    for k, n in ns.items():
        others = [o for o in "abc" if o != k]
        n.join = ",".join(f"127.0.0.1:{ns[o].raft}" for o in others)
        n.peers = ",".join(apps[o].url for o in others)
    a, b, c = ns["a"], ns["b"], ns["c"]
    try:
        # 1. Einzelbetrieb mit Pi A
        a.size = 1
        a.start()
        wait_for(lambda: a.state() == "Leader", 15, "A wird Leader")
        a.execute("CREATE TABLE probe (v TEXT)")
        a.execute("INSERT INTO probe VALUES ('vorher')")
        wait_for(lambda: (a.data / ".festpos-member").exists(), 15, "Markierung A")
        # 2. auf 3 Pi umstellen: A startet als Mitglied neu, B und C treten bei
        a.stop()
        a.size = 3
        a.start()
        wait_for(lambda: a.state() == "Leader", 15, "A nach Umstellung wieder Leader")
        b.start(); c.start()
        wait_for(lambda: len(a.nodes()) == 3, 30, "B und C im Cluster")
        wait_for(lambda: (b.data / ".festpos-member").exists() and (c.data / ".festpos-member").exists(), 15,
                 "Markierungen B und C")
        wait_for(lambda: b.rows() == ["vorher"] and c.rows() == ["vorher"], 15, "Daten auf B und C")
        # 3. B und C fallen aus, A allein ohne Mehrheit -> Notfall-Einzelbetrieb
        b.proc.kill(); c.proc.kill()
        b.stop(); c.stop()
        a.stop()
        shell("festpos_rq_einzelbetrieb", str(a.data), "a", f"127.0.0.1:{a.raft}")
        a.start()
        wait_for(lambda: a.state() == "Leader", 15, "A im Einzelbetrieb Leader")
        assert list(a.nodes()) == ["a"]
        a.execute("INSERT INTO probe VALUES ('notfall')")
        assert (a.data / ".festpos-einzelbetrieb").exists()
        yield a, b, c, apps
    finally:
        for n in ns.values():
            n.stop()
        for ap in apps.values():
            ap.close()


def test_failed_join_never_founds_own_cluster(nodes):
    a = nodes("a", 1)
    b = nodes("b", 3)
    b.join = f"127.0.0.1:{a.raft}"

    # 1. B startet, A läuft noch nicht: Beitritt scheitert, rqlited beendet sich
    assert b.start(attempts=2).wait_exit() != 0
    assert (b.data / "raft.db").exists()                    # rqlite legt raft.db schon vor dem Beitritt an

    # 2. systemd startet B neu: früher "Start ohne Join" -> eigener Cluster. Jetzt: wieder nur beitreten.
    assert b.start(attempts=2).wait_exit() != 0
    out = b.out.decode()
    assert "trete bei" in out and "bootstrapping single new node" not in out
    assert not (b.data / ".festpos-member").exists()

    # 3. A gründet (Einzelbetrieb), legt eine Tabelle an; dann tritt B wirklich bei
    a.start()
    wait_for(lambda: a.state() == "Leader", 15, "A wird Leader")
    a.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    b.start()
    wait_for(lambda: len(a.nodes()) == 2, 20, "B im Cluster")
    wait_for(lambda: (b.data / ".festpos-member").exists(), 15, "Mitglieds-Markierung bei B")
    wait_for(lambda: (a.data / ".festpos-member").exists(), 15, "Mitglieds-Markierung bei A")

    # 4. B startet neu: jetzt Mitglied -> ohne Join, wird wieder Follower
    b.stop()
    b.start()
    wait_for(lambda: b.state() == "Follower", 15, "B wieder Follower")
    b.stop()
    assert "Mitglied eines Clusters" in b.out.decode()


def test_member_from_older_version_without_marker(nodes):
    """Knoten aus einer älteren Version (keine Markierung, aber Daten) gilt als Mitglied."""
    a = nodes("a", 1)
    a.start()
    wait_for(lambda: a.state() == "Leader", 15, "A wird Leader")
    a.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    time.sleep(0.5)
    a.stop()
    (a.data / ".festpos-member").unlink(missing_ok=True)
    a.size = 3                                              # z.B. schon auf 3 Pi umgestellt
    a.join = "127.0.0.1:1"
    a.start()
    wait_for(lambda: a.state() == "Leader", 15, "A startet ohne Join wieder als Leader")
    a.stop()
    out = a.out.decode()
    assert "Mitglied eines Clusters" in out and "trete bei" not in out


def test_return_after_einzelbetrieb_rejoins_instead_of_overwriting(three):
    """B und C kommen zurück, während A im Einzelbetrieb läuft: sie erkennen den veralteten Stand,
    legen ihn beiseite und treten neu bei. Die im Einzelbetrieb erfassten Daten bleiben."""
    a, b, c, _apps = three
    b.start(); c.start()
    wait_for(lambda: len(a.nodes()) == 3, 40, "B und C neu beigetreten")
    wait_for(lambda: b.rows() == ["notfall", "vorher"] and c.rows() == ["notfall", "vorher"], 20,
             "Daten aus dem Einzelbetrieb auf B und C")
    assert a.state() == "Leader" and a.rows() == ["notfall", "vorher"]
    wait_for(lambda: not (a.data / ".festpos-einzelbetrieb").exists(), 15, "Einzelbetrieb-Markierung weg")
    assert list(b.data.parent.glob("b.verworfen-*")), "alter Stand von B beiseite gelegt, nicht gelöscht"
    b.stop(); c.stop()
    for n in (b, c):
        out = n.out.decode()
        assert "Einzelbetrieb und hat die aktuellen Daten" in out or "führt diesen Pi nicht mehr" in out


def test_einzelbetrieb_data_survive_stale_majority(three):
    """Schlimmster Bedienfehler: A ist aus, B und C werden mit altem Stand eingeschaltet und bilden eine
    Mehrheit. Kommt A danach zurück, startet seine Datenbank NICHT (sonst würde B/C sie überschreiben)."""
    a, b, c, apps = three
    a.stop()
    apps["a"].online = False
    b.start(); c.start()
    wait_for(lambda: b.leader() in ("b", "c"), 30, "B/C bilden mit altem Stand eine Mehrheit")
    apps["a"].online = True
    assert a.start().wait_exit() != 0
    out = a.out.decode()
    assert "ÄLTEREM Stand" in out and "NICHT gestartet" in out
    con = sqlite3.connect(a.data / "db.sqlite")
    assert sorted(r[0] for r in con.execute("SELECT v FROM probe")) == ["notfall", "vorher"]
    con.close()
    # Rettung laut Anleitung: auf B und C «festpos-notfall neu-beitreten», dann A starten
    b.stop(); c.stop()
    for n in (b, c):
        shell("festpos_rq_verwerfen", str(n.data))
    a.start()
    wait_for(lambda: a.state() == "Leader", 20, "A startet wieder")
    b.start(); c.start()
    wait_for(lambda: len(a.nodes()) == 3, 40, "B und C neu beigetreten")
    wait_for(lambda: b.rows() == ["notfall", "vorher"], 20, "Daten aus dem Einzelbetrieb auf B")


def test_verwerfen_keeps_three_newest(tmp_path):
    d = tmp_path / "rqlite"
    for i in range(1, 6):
        (tmp_path / f"rqlite.verworfen-nr{i}-2026100{i}-000000").mkdir()
    d.mkdir()
    (d / "raft.db").write_text("x")
    (d / ".festpos-node").write_text("b\n")
    ziel = Path(shell("festpos_rq_verwerfen", str(d)))
    assert ziel.name.startswith("rqlite.verworfen-nr6-")
    assert (ziel / "raft.db").exists() and not (d / "raft.db").exists()
    assert (d / ".festpos-node").read_text() == "b\n"                 # Rolle der Karte bleibt
    left = sorted(p.name.split("-")[1] for p in tmp_path.glob("rqlite.verworfen-*"))
    assert left == ["nr4", "nr5", "nr6"]


def test_verwerfen_with_clock_behind_keeps_new_copy(tmp_path):
    """Pi ohne Uhr-Batterie: Datum in der Vergangenheit. Die neue Kopie darf trotzdem nicht weggeräumt werden."""
    d = tmp_path / "rqlite"
    for i in range(1, 4):
        (tmp_path / f"rqlite.verworfen-nr{i}-2026101{i}-000000").mkdir()
    d.mkdir()
    (d / "raft.db").write_text("neu")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "date").write_text("#!/bin/sh\necho 20250101-000000\n")
    (stub / "date").chmod(0o755)
    r = subprocess.run(["bash", "-c", f'PATH="{stub}:$PATH"; source "{DEPLOY}/festpos-conf.sh"; festpos_rq_verwerfen "$1"',
                        "_", str(d)], capture_output=True, text=True, check=True)
    ziel = Path(r.stdout.strip())
    assert (ziel / "raft.db").read_text() == "neu"
    assert len(list(tmp_path.glob("rqlite.verworfen-*"))) == 3


def test_simultaneous_power_on_after_einzelbetrieb(three):
    """Nach dem Notfall-Abend alle drei Pi an einer Steckerleiste einschalten: die App antwortet schon,
    bevor ihre Datenbank läuft, und meldet den Einzelbetrieb – B und C treten neu bei, A behält die Daten."""
    a, b, c, _apps = three
    a.stop()
    a.start(); b.start(); c.start()
    wait_for(lambda: len(a.nodes()) == 3 and a.state() == "Leader", 60, "B und C bei A neu beigetreten")
    wait_for(lambda: b.rows() == ["notfall", "vorher"] and c.rows() == ["notfall", "vorher"], 20, "Daten auf B und C")
    assert a.rows() == ["notfall", "vorher"]
    b.stop(); c.stop()
    for n in (b, c):
        assert "Einzelbetrieb" in n.out.decode()


def test_einzelbetrieb_node_stops_when_stale_majority_takes_over(three):
    """Laufzeit-Schutz: A läuft im Einzelbetrieb, B und C kommen mit altem Stand, ohne A zu sehen (z.B. Kabel),
    und bilden eine Mehrheit. Folgt A plötzlich deren Leader, stoppt A seine Datenbank sofort."""
    a, b, c, apps = three
    apps["a"].online = False                      # B und C können A nicht fragen
    b.start(); c.start()
    assert a.wait_exit(timeout=60) is not None
    out = a.out.decode()
    assert "ÄLTEREN Stand" in out and "gestoppt" in out
def _rqcheck():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rqcheck", DEPLOY / "festpos-rqcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("answers,einzel,expected", [
    ({}, False, "ok"),
    ({"x": {"state": "Leader", "leader": "a", "members": ["a", "b", "c"]}}, False, "ok"),
    ({"x": {"state": None, "leader": None, "members": []}}, False, "ok"),
    ({"x": {"state": "Leader", "leader": "a", "members": ["a"]}}, False, "veraltet"),
    ({"x": {"state": "Leader", "leader": "a", "members": ["a"]}}, True, "konflikt"),
    ({"x": {"state": "Leader", "leader": "c", "members": ["a", "b", "c"]}}, True, "fremd"),
    ({"x": {"state": "Leader", "leader": "a", "members": ["a"]},
      "y": {"state": "Leader", "leader": "c", "members": ["c"]}}, False, "konflikt"),
    ({"x": {"state": "Candidate", "leader": None, "members": ["a"]}}, False, "ok"),
    ({"x": {"node": "a", "state": None, "leader": None, "members": [], "einzelbetrieb": True}}, False, "veraltet"),
    ({"x": {"node": "a", "state": None, "leader": None, "members": [], "einzelbetrieb": True}}, True, "konflikt"),
])
def test_rqcheck_decide(answers, einzel, expected):
    assert _rqcheck().decide("b", answers, einzel)[0] == expected


def test_cluster_summary():
    st = {"store": {"raft": {"state": "Follower"}, "leader": {"node_id": "a"},
                    "nodes": [{"id": "a", "suffrage": "voter"}, {"id": "b", "suffrage": "voter"},
                              {"id": "x", "suffrage": "nonvoter"}]}}
    assert cluster.summarize(st, "b") == {"node": "b", "state": "Follower", "leader": "a",
                                          "members": ["a", "b"], "einzelbetrieb": False}
    assert cluster.summarize(None, "b")["state"] is None


def test_flag_does_not_override_running_majority():
    """Ein Pi im Einzelbetrieb, der den Start verweigert, darf einen laufenden Cluster nicht kippen:
    C startet neu und bleibt im Cluster von B (REV-029, Prüfung Runde 2)."""
    answers = {"a": {"node": "a", "state": None, "leader": None, "members": [], "einzelbetrieb": True},
               "b": {"node": "b", "state": "Leader", "leader": "b", "members": ["a", "b", "c"], "einzelbetrieb": False}}
    assert _rqcheck().decide("c", answers, False)[0] == "ok"
    # A ist selbst Leader (gerade im Einzelbetrieb gestartet): dann ist C veraltet
    answers["a"].update(state="Leader", leader="a", members=["a"])
    del answers["b"]
    assert _rqcheck().decide("c", answers, False)[0] == "veraltet"


def _tls_files(d: Path, name: str, client_auth: bool = True):
    """CA + Server-Zertifikat (127.0.0.1) wie make-certs.sh: serverAuth + clientAuth."""
    import datetime as dt
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Test-CA {name}")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    eku = [ExtendedKeyUsageOID.SERVER_AUTH] + ([ExtendedKeyUsageOID.CLIENT_AUTH] if client_auth else [])
    crt = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "festpos.local")]))
           .issuer_name(ca_name).public_key(key.public_key()).serial_number(2).not_valid_before(now)
           .not_valid_after(now + dt.timedelta(days=30))
           .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
           .add_extension(x509.ExtendedKeyUsage(eku), critical=False).sign(ca_key, hashes.SHA256()))
    d.mkdir(parents=True, exist_ok=True)
    (d / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (d / "server.crt").write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    (d / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption()))
    return {"FESTPOS_RAFT_TLS": "1", "FESTPOS_CA": str(d / "ca.crt"), "FESTPOS_TLS_CERT": str(d / "server.crt"),
            "FESTPOS_TLS_KEY": str(d / "server.key")}


def test_raft_tls_between_nodes(nodes, tmp_path):
    """RAFT_TLS=1: die Pi sprechen verschlüsselt und nehmen nur Pi mit einem Zertifikat derselben CA an."""
    tls = _tls_files(tmp_path / "tls", "festpos")
    a, b, fremd = nodes("a", 1), nodes("b", 3), nodes("x", 3)
    a.extra = b.extra = tls
    a.start()
    wait_for(lambda: a.state() == "Leader", 15, "A wird Leader (mit TLS)")
    a.execute("CREATE TABLE probe (v TEXT)")
    a.execute("INSERT INTO probe VALUES ('geheim')")
    b.join = fremd.join = f"127.0.0.1:{a.raft}"
    b.start()
    wait_for(lambda: len(a.nodes()) == 2 and b.rows() == ["geheim"], 30, "B tritt über TLS bei")
    # Port 4002 spricht nur noch TLS und verlangt ein Client-Zertifikat
    import socket
    import ssl
    ctx = ssl.create_default_context(cafile=tls["FESTPOS_CA"])
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    with pytest.raises(ssl.SSLError):
        with socket.create_connection(("127.0.0.1", a.raft), timeout=3) as sock:
            with ctx.wrap_socket(sock, server_hostname="127.0.0.1") as t:
                t.settimeout(3)
                t.send(b"x")
                t.recv(10)
    # Pi ohne TLS bzw. mit Zertifikat einer anderen CA: kommt nicht rein
    fremd.extra = _tls_files(tmp_path / "andere", "andere")
    assert fremd.start(attempts=3).wait_exit(timeout=60) != 0
    assert len(a.nodes()) == 2
