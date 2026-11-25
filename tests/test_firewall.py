"""Firewall für Verbindungen der Pi ins Internet (T-0-N/FestPOS#10): Regeln, root-Helfer, Modus in der Verwaltung."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "festpos-firewall"


# ------------------------------------------------------------------ Regeln
def test_rules_per_mode():
    from app import ausgehend
    strict = ausgehend.rules("strict", ["140.82.121.6", "140.82.121.7", "2606:50c0:8000::153", "kaputt"], ["192.168.55.1"])
    assert strict.startswith(f"table inet {ausgehend.TABLE}\ndelete table inet {ausgehend.TABLE}\n")
    assert "140.82.121.6/31" in strict and "2606:50c0:8000::153/128" in strict and "kaputt" not in strict
    assert "104.16.0.0/13" in strict                                    # Cloudflare-Netze für den Tunnel
    assert "set dns4 { type ipv4_addr; flags interval; elements = { 192.168.55.1/32 }" in strict
    assert "192.168.0.0/16" in strict and "udp dport 123 accept" in strict
    assert strict.rstrip().endswith("drop\n    }\n}") and "counter name unknown" in strict
    assert ausgehend.rules("watch", [], []).rstrip().endswith("accept\n    }\n}")
    assert ausgehend.rules("open", ["1.2.3.4"], []) == f"table inet {ausgehend.TABLE}\ndelete table inet {ausgehend.TABLE}\n"
    assert ausgehend.rules("unsinn", [], []) == ausgehend.rules("open", [], [])
    assert set(ausgehend.HOSTS) >= {"api.payrexx.com", "api.github.com", "pkg.cloudflare.com"}


def test_rules_are_valid_nft():
    """Mit nft (falls vorhanden und erlaubt) im Prüfmodus laden."""
    from app import ausgehend
    try:
        p = subprocess.run(["nft", "-c", "-f", "-"], input=ausgehend.rules("strict", ["1.2.3.4", "::2"], ["1.1.1.1"]),
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("nft fehlt")
    if p.returncode and ("Operation not permitted" in p.stderr or "Permission denied" in p.stderr):
        pytest.skip("nft ohne Rechte")
    assert p.returncode == 0, p.stderr


# ------------------------------------------------------------------ root-Helfer
LISTING = {"nftables": [
    {"counter": {"family": "inet", "name": "unknown", "table": "festpos_out", "packets": 17, "bytes": 1020}},
    {"set": {"family": "inet", "name": "seen4", "table": "festpos_out",
             "elem": [{"elem": {"val": {"concat": ["142.250.203.99", "tcp", 443]}, "expires": 86000}},
                      {"elem": {"val": {"concat": ["8.8.8.8", "udp", 53]}, "expires": 400}}]}},
    {"set": {"family": "inet", "name": "seen6", "table": "festpos_out"}}]}


@pytest.fixture
def fw(tmp_path):
    r = tmp_path / "root"
    (r / "etc").mkdir(parents=True)
    (r / "etc" / "resolv.conf").write_text("# vom Router\nnameserver 192.168.55.1\nnameserver 1.1.1.1\n")
    (r / "run" / "festpos" / "firewall").mkdir(parents=True)
    names = tmp_path / "dns.json"
    names.write_text(json.dumps({"api.payrexx.com": ["185.8.12.1"], "api.github.com": ["140.82.121.6"]}))
    fake = tmp_path / "nft"
    fake.write_text(f"""#!/bin/sh
echo "$@" >> {tmp_path}/nft-args
da() {{ grep -q "chain output" {tmp_path}/geladen.nft 2>/dev/null; }}      # Tabelle geladen (nicht «open»)
if [ "$1" = "-j" ]; then da || exit 1; cat {tmp_path}/listing.json 2>/dev/null || echo '{{"nftables": [{{"counter": {{"name": "unknown", "packets": 0}}}}]}}'; exit 0; fi
if [ "$1" = "list" ]; then da; exit $?; fi
cat > {tmp_path}/geladen.nft
[ -f {tmp_path}/nft-fehler ] && {{ echo "Error: kaputt" >&2; exit 1; }}   # wie nft -f: alles oder nichts
rm -f {tmp_path}/listing.json                                          # neu geladen: Zähler und Sets leer
exit 0
""")
    fake.chmod(0o755)
    env = {**os.environ, "FESTPOS_TEST_ROOT": str(r), "FESTPOS_NFT": str(fake), "FESTPOS_FW_RESOLVE": str(names),
           "FESTPOS_DEST": str(ROOT)}

    def run(*args, wish=None):
        if wish is not None:
            (r / "run" / "festpos" / "firewall" / "modus.json").write_text(wish if isinstance(wish, str) else json.dumps(wish))
        p = subprocess.run([sys.executable, "-I", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=60)
        stand = json.loads((r / "run" / "festpos" / "firewall-stand" / "stand.json").read_text())
        loaded = (tmp_path / "geladen.nft").read_text() if (tmp_path / "geladen.nft").exists() else ""
        return p, stand, loaded
    def traffic():                       # seit dem letzten Laden verworfen
        (tmp_path / "listing.json").write_text(json.dumps(LISTING))
    return {"run": run, "tmp": tmp_path, "r": r, "names": names, "traffic": traffic}


def test_helper_strict_by_default_with_counts(fw):
    p, stand, loaded = fw["run"]()                                    # noch kein Wunsch der App: strict
    assert p.returncode == 0, p.stderr
    assert stand["mode"] == "strict" and stand["unknown"] == 0 and stand["error"] == ""
    fw["traffic"]()
    p, stand, loaded = fw["run"]()
    assert stand["unknown"] == 17
    assert stand["seen"][0] == {"ip": "142.250.203.99", "proto": "tcp", "port": 443, "left": 86000}
    assert "185.8.12.1/32" in loaded and "140.82.121.6/32" in loaded and loaded.rstrip().endswith("drop\n    }\n}")
    assert "elements = { 1.1.1.1/32, 192.168.55.1/32 }" in loaded     # DNS nur über die eingestellten Server
    assert stand["hosts"] == 2 and stand["addrs"] == 2
    cache = fw["r"] / "var" / "lib" / "festpos-firewall"
    assert (cache.stat().st_mode & 0o777) == 0o700


def test_helper_modes_expiry_and_bad_wishes(fw):
    later = int(time.time()) + 3600
    _, stand, loaded = fw["run"](wish={"mode": "watch", "until": later})
    assert stand["mode"] == "watch" and stand["until"] == later and loaded.rstrip().endswith("accept\n    }\n}")
    _, stand, loaded = fw["run"](wish={"mode": "open", "until": later})
    assert stand["mode"] == "open" and "chain output" not in loaded and stand["unknown"] is None
    for bad in ({"mode": "open", "until": int(time.time()) - 5},          # abgelaufen
                {"mode": "open", "until": int(time.time()) + 30 * 86400},  # zu lange
                {"mode": "alles"}, "{kaputt"):
        _, stand, loaded = fw["run"](wish=bad)
        assert stand["mode"] == "strict" and loaded.rstrip().endswith("drop\n    }\n}"), bad
    # Verknüpfung statt Datei: nicht folgen
    w = fw["r"] / "run" / "festpos" / "firewall" / "modus.json"
    w.unlink()
    (fw["tmp"] / "fremd.json").write_text(json.dumps({"mode": "open", "until": later}))
    w.symlink_to(fw["tmp"] / "fremd.json")
    _, stand, _ = fw["run"]()
    assert stand["mode"] == "strict"


def test_helper_reloads_only_on_change_and_keeps_observations(fw):
    """Unveränderte Regeln: nicht neu laden (Zähler und Sets bleiben). Neue Adressen: neu laden, die Beobachtungen
    der alten Tabelle zählen weiter."""
    fw["run"]()
    loads = lambda: (fw["tmp"] / "nft-args").read_text().splitlines().count("-f -")  # noqa: E731
    n = loads()
    fw["traffic"]()
    _, stand, _ = fw["run"]()
    assert loads() == n and stand["unknown"] == 17                     # nichts neu geladen
    cache = fw["r"] / "var/lib/festpos-firewall/adressen.json"
    c = json.loads(cache.read_text())
    cache.write_text(json.dumps({**c, "at": 0}))                       # DNS fällig, neue Adresse
    fw["names"].write_text(json.dumps({"api.payrexx.com": ["185.8.12.1"], "api.github.com": ["140.82.121.7"]}))
    _, stand, loaded = fw["run"]()
    assert loads() == n + 1 and "140.82.121.6/31" in loaded            # alte Adresse bleibt einen Tag
    assert stand["unknown"] == 17 and stand["seen"][0]["ip"] == "142.250.203.99"    # aus der alten Tabelle
    fw["traffic"]()
    _, stand, _ = fw["run"]()
    assert stand["unknown"] == 34                                      # 17 vorher + 17 in der neuen
    c = json.loads(cache.read_text())                                  # Laden scheitert: alte Tabelle bleibt, …
    cache.write_text(json.dumps({**c, "at": 0}))
    fw["names"].write_text(json.dumps({"api.payrexx.com": ["185.8.12.2"], "api.github.com": ["140.82.121.7"]}))
    (fw["tmp"] / "nft-fehler").write_text("1")
    fw["run"]()
    (fw["tmp"] / "nft-fehler").unlink()
    c = json.loads(cache.read_text())
    cache.write_text(json.dumps({**c, "at": 0}))
    _, stand, _ = fw["run"]()
    assert stand["unknown"] == 34                                      # … ihre Zähler nicht doppelt gezählt
    _, stand, _ = fw["run"](wish={"mode": "watch", "until": int(time.time()) + 3600})
    assert stand["unknown"] == 0 and stand["seen"] == []               # neuer Modus: neu gezählt


def test_expired_open_is_strict_before_dns(fw):
    """Abgelaufenes «open»: die strengen Regeln sind geladen, bevor DNS gefragt wird (DNS hängt hier)."""
    fw["run"](wish={"mode": "open", "until": int(time.time()) + 3600})
    w = fw["r"] / "run" / "festpos" / "firewall" / "modus.json"
    w.write_text(json.dumps({"mode": "open", "until": int(time.time()) - 1}))
    order = []
    args = fw["tmp"] / "nft-args"
    args.write_text("")
    p, stand, loaded = fw["run"]()
    order = args.read_text().splitlines()
    assert stand["mode"] == "strict" and loaded.rstrip().endswith("drop\n    }\n}")
    assert order[0] == "-f -"                                          # zuerst geladen, mit den bekannten Adressen


def test_helper_keeps_addresses_without_dns_and_reports_errors(fw):
    fw["run"]()
    fw["names"].write_text("{}")                                       # DNS weg / anderer Eintrag
    _, stand, loaded = fw["run"]("--ohne-dns")
    assert "185.8.12.1/32" in loaded and stand["addrs"] == 2           # die bisherigen Adressen bleiben
    (fw["tmp"] / "nft-fehler").write_text("1")
    (fw["tmp"] / "geladen.nft").unlink()                               # Tabelle weg (z.B. festpos-node): neu laden
    p, stand, _ = fw["run"]()
    assert p.returncode == 1 and stand["error"] == "Error: kaputt" and stand["unknown"] is None


# ------------------------------------------------------------------ Verwaltung
def test_mode_in_admin_critical_and_expires(client, monkeypatch):
    from app import core, firewall
    assert client.get("/api/admin/firewall", auth=AUTH).json()["mode"] == "strict"
    tok = client.post("/api/admin/login", json={"name": "admin", "password": "geheim"}).json()["token"]
    r = client.put("/api/admin/firewall", headers={"Authorization": f"Bearer {tok}"}, json={"mode": "open"})
    assert r.status_code == 428                                          # kritisch: bestätigen
    assert client.put("/api/admin/firewall", auth=AUTH, json={"mode": "alles"}).status_code == 422
    r = client.put("/api/admin/firewall", auth=AUTH, json={"mode": "watch"})
    assert r.status_code == 200 and r.json()["until"] > time.time() + 23 * 3600
    assert json.loads(firewall.WISH.read_text()) == {"mode": "watch", "until": r.json()["until"]}
    g = client.get("/api/admin/firewall", auth=AUTH).json()
    assert g["mode"] == "watch" and any(h["name"] == "api.payrexx.com" for h in g["hosts"])
    # nach 24 Stunden: zurück auf strict, im Protokoll
    real = time.time
    monkeypatch.setattr(firewall.time, "time", lambda: real() + 25 * 3600)
    firewall.sync()
    assert core.rt.store.setting("fw_mode") == "strict"
    assert json.loads(firewall.WISH.read_text())["mode"] == "strict"
    audit = json.dumps(client.get("/api/admin/audit", auth=AUTH).json())
    assert '"expired": "watch"' in audit
    lea = client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "sicher-123"})
    assert lea.status_code == 200
    assert client.put("/api/admin/firewall", auth=("Lea", "sicher-123"), json={"mode": "open"}).status_code == 403


def test_local_status_for_the_pi_page(tmp_path, monkeypatch):
    from app import firewall
    f = tmp_path / "stand.json"
    monkeypatch.setattr(firewall, "STAND", f)
    assert firewall.local_status() is None
    f.write_text(json.dumps({"mode": "strict", "at": 5, "unknown": 3, "error": "",
                             "seen": [{"ip": "1.2.3.4", "proto": "tcp", "port": 443, "left": 9}], "addrs": 20}))
    s = firewall.local_status()
    assert s["mode"] == "strict" and s["unknown"] == 3 and s["seen"] == [{"ip": "1.2.3.4", "proto": "tcp", "port": 443}]
    f.write_text(json.dumps({"mode": "irgendwas"}))
    assert firewall.local_status()["mode"] is None
