"""T22: Cloudflare Tunnel zur QR-Bestellung aus der Verwaltung – Token prüfen, verschlüsselt speichern (nie zurück),
jeder Pi richtet ihn über den root-Helfer ein (Auftrag «tunnel»), festpos.conf hat Vorrang, entfernen schaltet aus;
cloudflared kommt bei Bedarf aus der Paketquelle von Cloudflare (Auftrag «cloudflared»), nicht mit FestPOS."""
from __future__ import annotations

import base64
import grp
import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from app import tunnel_token
from tests.test_api import AUTH
from tests.test_update_app import upd  # noqa: F401  (Fixture)
from tests.test_update_helper import helper, netlog, netz, request  # noqa: F401  (Fixtures)
from tests.test_update import fake  # noqa: F401  (Fixture für helper)

ROOT = Path(__file__).resolve().parent.parent


def make_token(secret: str = "c2VjcmV0LXNlY3JldC1zZWNyZXQtMTIzNDU2Nzg5MA==", tunnel="6ff42ae2-765d-4adf-8112-31c55c1551ef") -> str:
    raw = json.dumps({"a": "0123456789abcdef0123456789abcdef", "t": tunnel, "s": secret}).encode()
    return base64.b64encode(raw).decode()


TOK = make_token()
TOK2 = make_token(tunnel="11111111-2222-4333-8444-555555555555")


def test_token_format():
    assert tunnel_token.parse(TOK) == {"account": "0123456789abcdef0123456789abcdef",
                                       "tunnel": "6ff42ae2-765d-4adf-8112-31c55c1551ef"}
    assert tunnel_token.parse("  " + TOK + "\n") is not None                         # beim Kopieren mitgenommen
    assert tunnel_token.parse(base64.urlsafe_b64encode(base64.b64decode(TOK)).decode().rstrip("=")) is not None
    for bad in (None, "", "abc", TOK + "\nX=1", TOK[:-8], "eyJ" + "A" * 100,
                base64.b64encode(json.dumps({"a": "x", "t": "y", "s": "z" * 20}).encode()).decode(),
                base64.b64encode(json.dumps(["a", "t", "s"] * 20).encode()).decode()):
        assert tunnel_token.parse(bad) is None, bad
    assert tunnel_token.fingerprint(TOK) == tunnel_token.fingerprint(TOK + " ") != tunnel_token.fingerprint(TOK2)
    assert len(tunnel_token.fingerprint(TOK)) == 16


# -- root-Helfer ------------------------------------------------------------------------------------------------
@pytest.fixture
def tun(netz, tmp_path):  # noqa: F811
    n = netz
    etc, gast = tmp_path / "etc-festpos", tmp_path / "etc-festpos-gast"
    etc.mkdir()
    cf = tmp_path / "cloudflared"
    n["env"] = {**n["env"], "FESTPOS_TUNNEL_DIR": str(etc), "FESTPOS_GAST_DIR": str(gast),
                "FESTPOS_TUNNEL_RUN": str(etc), "FESTPOS_KRYPT_GEHEIM": str(tmp_path / "keiner"),
                "FESTPOS_GAST_GROUP": grp.getgrgid(os.getgid()).gr_name, "FESTPOS_CLOUDFLARED": str(cf)}
    return {**n, "etc": etc, "gast": gast, "cf": cf}


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_helper_sets_token_and_starts_services(tun):
    t = tun
    _, out = request(t, "t1", action="tunnel", token=TOK)
    assert out["ok"] and out["cloudflared"] is False, out
    assert (t["etc"] / "tunnel.env").read_text() == f"TUNNEL_TOKEN={TOK}\n" and mode(t["etc"] / "tunnel.env") == 0o600
    assert (t["etc"] / "tunnel-app.env").read_text() == f"TUNNEL_TOKEN={TOK}\n" and mode(t["etc"] / "tunnel-app.env") == 0o600
    assert (t["etc"] / "tunnel-app.fp").read_text().strip() == tunnel_token.fingerprint(TOK)
    key = (t["gast"] / "token").read_text().strip()
    assert len(key) >= 32 and mode(t["gast"] / "token") == 0o640                   # Gästedienst und App lesen
    log = netlog(t)
    assert "systemctl enable festpos-gast.service festpos-tunnel.service" in log
    assert "systemctl restart festpos-tunnel.service" in log
    assert TOK not in json.dumps(out) and TOK not in log                          # Token nie in Antwort oder Protokoll
    t["cf"].write_text("")
    _, out = request(t, "t2", action="tunnel", token=TOK2)                         # neuer Token, Schlüssel bleibt
    assert out["ok"] and out["cloudflared"] is True
    assert TOK2 in (t["etc"] / "tunnel.env").read_text() and (t["gast"] / "token").read_text().strip() == key
    _, out = request(t, "t3", action="tunnel", off=True)
    assert out["ok"]
    assert not any((t["etc"] / f).exists() for f in ("tunnel.env", "tunnel-app.env", "tunnel-app.fp"))
    assert not (t["gast"] / "token").exists()
    assert "systemctl disable --now festpos-tunnel.service festpos-gast.service" in netlog(t)


def test_helper_keeps_admin_token_encrypted_and_runtime_copy_in_ram(tun, tmp_path):
    """Daten verschlüsselt (T-0-N/FestPOS#3): Token der Verwaltung im Container, tunnel.env nur im Arbeitsspeicher."""
    t = tun
    geheim, ram = tmp_path / "krypt-geheim", tmp_path / "run-geheim"
    geheim.mkdir()
    ram.mkdir()
    (t["etc"] / "tunnel-app.env").write_text("TUNNEL_TOKEN=alt\n")          # früher auf der Karte
    t["env"] = {**t["env"], "FESTPOS_KRYPT_GEHEIM": str(geheim), "FESTPOS_TUNNEL_RUN": str(ram)}
    _, out = request(t, "k1", action="tunnel", token=TOK)
    assert out["ok"], out
    assert (geheim / "tunnel-app.env").read_text() == f"TUNNEL_TOKEN={TOK}\n" and not (t["etc"] / "tunnel-app.env").exists()
    assert (ram / "tunnel.env").read_text() == f"TUNNEL_TOKEN={TOK}\n"
    assert (t["etc"] / "tunnel.env").is_symlink() and (t["etc"] / "tunnel.env").read_text() == f"TUNNEL_TOKEN={TOK}\n"
    _, out = request(t, "k2", action="tunnel", off=True)
    assert out["ok"] and not (geheim / "tunnel-app.env").exists() and not (ram / "tunnel.env").exists()


def test_helper_refuses_bad_token_and_respects_festpos_conf(tun):
    t = tun
    for bad in ("x" * 80, TOK + "\nExecStart=/bin/sh", 42):
        _, out = request(t, "b", action="tunnel", token=bad)
        assert not out["ok"] and out["code"] == "tunnel_bad"
    assert list(t["etc"].iterdir()) == [] and "systemctl" not in netlog(t)
    (t["etc"] / "tunnel-conf").write_text("")                                     # TUNNEL_TOKEN in festpos.conf
    (t["etc"] / "tunnel.env").write_text("TUNNEL_TOKEN=aus-festpos-conf\n")
    _, out = request(t, "c1", action="tunnel", token=TOK)
    assert out["ok"] and out["conf"] is True
    assert (t["etc"] / "tunnel.env").read_text() == "TUNNEL_TOKEN=aus-festpos-conf\n"     # bleibt
    assert (t["etc"] / "tunnel-app.fp").exists() and "systemctl" not in netlog(t)         # gespeichert, sonst nichts
    _, out = request(t, "c2", action="tunnel", off=True)
    assert out["ok"] and (t["etc"] / "tunnel.env").exists() and not (t["etc"] / "tunnel-app.env").exists()


def test_node_script_takes_conf_first_then_admin_token():
    node = (ROOT / "deploy/festpos-node.sh").read_text()
    sec = node[node.index("# --- QR-Bestellung am Tisch (T22)"):node.index("# --- Firewall")]
    assert sec.index('if [ -n "${TUNNEL_TOKEN:-}" ]') < sec.index('TUNNEL_APP=$ETC/tunnel-app.env')
    assert 'cp "$TUNNEL_APP" "$GRUN/tunnel.env"' in sec and 'touch "$ETC/tunnel-conf"' in sec
    # Benutzer, Gruppe und Firewall immer – sonst liesse sich der Tunnel nicht ohne Neustart einschalten
    assert sec.index("useradd --system") < sec.index("TUNNEL_SRC=") and "GAST_RULES=" in sec.split("if [ -n \"$TUNNEL_SRC\" ]")[0]


# -- App: Verwaltung und Takt jedes Pi --------------------------------------------------------------------------
class TunnelHelper:
    """festpos-update im Kleinen: beantwortet «tunnel» und legt den Fingerabdruck ab wie der echte Helfer."""

    def __init__(self, u, etc: Path):
        self.u, self.etc, self.reqs, self.code, self.stop = u, etc, [], None, False
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while not self.stop:
            for f in list(self.u["req"].iterdir()):
                req = json.loads(f.read_text())
                f.unlink()
                self.reqs.append(req)
                ok = not self.code
                if req.get("action") == "cloudflared":
                    pass
                elif ok and req.get("off"):
                    (self.etc / "tunnel-app.fp").unlink(missing_ok=True)
                elif ok:
                    (self.etc / "tunnel-app.fp").write_text(tunnel_token.fingerprint(req["token"]) + "\n")
                (self.u["status"] / f"{req['id']}.json").write_text(
                    json.dumps({"id": req["id"], "ok": ok, "code": self.code}))
            time.sleep(0.05)


@pytest.fixture
def app_tun(upd, monkeypatch, tmp_path):  # noqa: F811
    from app import tunnel
    main = sys.modules["app.main"]
    monkeypatch.setattr(main, "_tunnel_last", [time.time() + 10 ** 6])            # Monitor soll nicht dazwischen
    etc = tmp_path / "etc-festpos"
    etc.mkdir()
    monkeypatch.setattr(tunnel, "ETC", etc)
    monkeypatch.setattr(tunnel, "WAIT", 10)
    monkeypatch.setattr(tunnel, "_state", {})
    h = TunnelHelper(upd, etc)
    yield {**upd, "t": tunnel, "etc": etc, "h": h}
    h.stop = True


def test_admin_stores_token_sealed_and_never_returns_it(app_tun):
    c, t = app_tun["c"], app_tun["t"]
    assert c.put("/api/admin/gast", auth=AUTH, json={"tunnel": "kein-token"}).status_code == 422
    r = c.put("/api/admin/gast", auth=AUTH, json={"tunnel": TOK})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["tunnel"] == {"set": True, "ok": True, "id": "6ff42ae2"}
    from app.core import rt
    stored = rt.store.setting(t.SETTING)
    assert stored and TOK not in stored and "6ff42ae2" not in stored                 # verschlüsselt
    assert TOK not in json.dumps(v) and TOK not in c.get("/api/admin/gast", auth=AUTH).text
    assert t.SETTING not in c.get("/api/admin/settings", auth=AUTH).json()
    r = c.put("/api/admin/gast", auth=AUTH, json={"open": False})                    # ohne Token: bleibt
    assert r.json()["tunnel"]["set"] is True
    audit = json.dumps(rt.store.query("SELECT * FROM audit_log"), default=str)
    assert TOK not in audit and "tunnel_changed" in audit


def test_each_pi_applies_admin_token_and_removal(app_tun):
    c, t, h = app_tun["c"], app_tun["t"], app_tun["h"]
    t.tick()
    assert h.reqs == []                                                              # nichts gewünscht, nichts da
    c.put("/api/admin/gast", auth=AUTH, json={"tunnel": TOK})
    v = c.get("/api/admin/gast", auth=AUTH).json()
    assert v["tunnel_pis"][0]["synced"] is False and v["tunnel_pis"][0]["source"] is None
    t.tick()
    assert [r.get("token") for r in h.reqs] == [TOK]
    t.tick()
    assert len(h.reqs) == 1                                                          # eingerichtet: Ruhe
    assert t.local_status()["synced"] is True
    r = c.delete("/api/admin/gast/tunnel", auth=AUTH)
    assert r.status_code == 200 and r.json()["tunnel"] == {"set": False, "ok": False, "id": None}
    t.tick()
    assert h.reqs[-1] == {"action": "tunnel", "off": True, "id": h.reqs[-1]["id"]}
    assert not (app_tun["etc"] / "tunnel-app.fp").exists()


def test_failure_is_shown_and_retried_later(app_tun):
    c, t, h = app_tun["c"], app_tun["t"], app_tun["h"]
    h.code = "tunnel_no_user"
    c.put("/api/admin/gast", auth=AUTH, json={"tunnel": TOK})
    t.tick()
    t.tick()
    assert len(h.reqs) == 1                                                          # nicht jede Minute nochmals
    st = t.local_status()
    assert st["synced"] is False and "neu starten" in st["error"]
    t._state["next"] = 0
    h.code = None
    t.tick()
    assert len(h.reqs) == 2 and "error" not in t.local_status()


# -- cloudflared aus der Paketquelle von Cloudflare ---------------------------------------------------------------
APT = """#!/bin/bash
echo "apt-get $*" >> "$NETLOG"
case " $* " in
  *" update "*) exit "${APT_UPDATE_RC:-0}" ;;
  *" install "*) [ "${APT_INSTALL_RC:-0}" = 0 ] && echo neu >> "$CF"; exit "${APT_INSTALL_RC:-0}" ;;
esac
"""


@pytest.fixture
def cf(tun, tmp_path):
    t = tun
    stub = tmp_path / "aptstub"
    stub.mkdir()
    (stub / "apt-get").write_text(APT)
    (stub / "apt-get").chmod(0o755)
    key = t["dest"] / "deploy" / "cloudflare-main.gpg"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"\x99\x01\x0dSCHLUESSEL")
    t["env"] = {**t["env"], "FESTPOS_APT_GET": str(stub / "apt-get"), "CF": str(t["cf"]),
                "FESTPOS_CF_KEYRING": str(tmp_path / "keyrings" / "festpos-cloudflare.gpg"),
                "FESTPOS_CF_LIST": str(tmp_path / "sources.list.d" / "festpos-cloudflared.list"),
                "FESTPOS_CF_KEY_URL": "http://127.0.0.1:9/nie"}
    return {**t, "key": key, "keyring": tmp_path / "keyrings" / "festpos-cloudflare.gpg",
            "list": tmp_path / "sources.list.d" / "festpos-cloudflared.list"}


def test_cloudflared_installed_from_cloudflare_only(cf):
    t = cf
    _, out = request(t, "c1", action="cloudflared")
    assert out["ok"] and out["cloudflared"] is True, out
    assert t["keyring"].read_bytes() == t["key"].read_bytes() and mode(t["keyring"]) == 0o644
    assert t["list"].read_text() == (f"deb [signed-by={t['keyring']}] https://pkg.cloudflare.com/cloudflared any main\n")
    log = netlog(t)
    only = f"-o Dir::Etc::sourcelist={t['list']} -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0"
    assert f"apt-get {only} update" in log                                       # nur die Quelle von Cloudflare
    assert f"apt-get {only} install -y --no-install-recommends cloudflared" in log
    assert "--only-upgrade" not in log and "restart festpos-tunnel" not in log    # Tunnel war nicht an
    assert mode(t["etc"] / "cloudflared-refresh") == 0o644                         # die App liest, wann zuletzt


def test_cloudflared_present_refresh_and_restart(cf):
    t = cf
    t["cf"].write_text("alt")
    _, out = request(t, "c1", action="cloudflared")                               # da und kein «refresh»: nichts
    assert out["cloudflared"] is True and "apt-get" not in netlog(t)
    (t["etc"] / "tunnel.env").write_text("TUNNEL_TOKEN=x\n")
    _, out = request(t, "c2", action="cloudflared", refresh=True)
    log = netlog(t)
    assert out["cloudflared"] is True and "install -y --no-install-recommends --only-upgrade cloudflared" in log
    assert "systemctl restart festpos-tunnel.service" in log                       # neues Programm: Tunnel neu starten


@pytest.mark.parametrize("env", [{"APT_UPDATE_RC": "100"}, {"APT_INSTALL_RC": "100"}])
def test_cloudflared_without_internet_changes_nothing_else(cf, env):
    t = cf
    t["env"] = {**t["env"], **env}
    _, out = request(t, "c1", action="cloudflared")
    assert out["ok"] and out["cloudflared"] is False
    if "APT_UPDATE_RC" in env:
        assert "install" not in netlog(t)


def test_cloudflared_key_over_https_when_not_bundled(cf):
    """Ohne mitgelieferten Schlüssel: von Cloudflare holen; nicht erreichbar -> nichts installiert, später nochmals."""
    t = cf
    t["key"].unlink()
    _, out = request(t, "c1", action="cloudflared")                               # Adresse antwortet nicht
    assert out["ok"] and out["cloudflared"] is False and "apt-get" not in netlog(t)
    import http.server
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"VON-CLOUDFLARE")

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    t["env"]["FESTPOS_CF_KEY_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/cloudflare-main.gpg"
    _, out = request(t, "c2", action="cloudflared")
    srv.shutdown()
    assert out["cloudflared"] is True and t["keyring"].read_bytes() == b"VON-CLOUDFLARE"


def test_app_asks_helper_for_cloudflared_while_tunnel_is_on(app_tun, monkeypatch):
    t, h, etc = app_tun["t"], app_tun["h"], app_tun["etc"]
    cfb = etc / "cloudflared-bin"
    monkeypatch.setattr(t, "CLOUDFLARED", cfb)
    monkeypatch.setattr(t, "_cf", {"next": 0.0})
    t.cf_tick(wait=True)
    assert h.reqs == []                                                            # Tunnel aus: nichts
    (etc / "tunnel.env").write_text("TUNNEL_TOKEN=x\n")
    t.cf_tick(now=1000, wait=True)
    assert h.reqs[-1]["action"] == "cloudflared" and "refresh" not in h.reqs[-1]
    t.cf_tick(now=1100, wait=True)
    assert len(h.reqs) == 1                                                        # nicht jede Minute
    cfb.write_text("da")
    (etc / "cloudflared-refresh").write_text("1\n")
    os.utime(etc / "cloudflared-refresh", (time.time(), time.time()))
    t.cf_tick(now=time.time() + 700, wait=True)
    assert len(h.reqs) == 1                                                        # eben aktualisiert
    t.cf_tick(now=time.time() + 8 * 86400, wait=True)
    assert h.reqs[-1] == {"action": "cloudflared", "refresh": True, "id": h.reqs[-1]["id"]}
