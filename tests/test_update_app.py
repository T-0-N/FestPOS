"""Version 2026.10.10-26: Updates in der App (app/update.py) – Hochladen mit Vorprüfung, Plan, wer wann dran ist
(nicht druckende Pi zuerst, der druckende zuletzt, immer nur einer, nur mit Mehrheit), Nachziehen, USB-Stick,
Zurück, Abbrechen, Erneut versuchen. Den root-Helfer spielt der Test: er beantwortet die Aufträge."""
from __future__ import annotations

import io
import json
import time
import uuid
from pathlib import Path

import pytest

from app import release
from tests.release_util import build_bundle, keypair
from tests.test_api import AUTH, setup_shop

NEW = "99.0.0"                # weit voraus: immer neuer als die laufende Version (VERSION)


@pytest.fixture
def upd(client, tmp_path, monkeypatch):
    from app import update
    from app.core import Config
    d = {k: tmp_path / k for k in ("pakete", "req", "reqneu", "status", "alt")}
    for p in d.values():
        p.mkdir()
    monkeypatch.setattr(update, "UPDATE_DIR", d["pakete"])
    monkeypatch.setattr(update, "REQ_DIR", d["req"])
    monkeypatch.setattr(update, "REQ_NEU", d["reqneu"])
    monkeypatch.setattr(update, "STATUS_DIR", d["status"])
    monkeypatch.setattr(update, "ALT_DIR", d["alt"])
    monkeypatch.setattr(update, "ENABLED", False)
    monkeypatch.setattr(update, "_started_at", 0.0)
    monkeypatch.setattr(Config, "cluster_secret", "test-geheimnis-für-die-pi")
    pk, line = keypair("Vereins-Update")
    keys(d["status"], line)
    return {"m": update, "pk": pk, "line": line, "c": client, "tmp": tmp_path, **d}


def keys(status: Path, *lines: str) -> None:
    status.joinpath("keys.json").write_text(json.dumps({"keys": [release.parse_key_line(x).public() for x in lines],
                                                        "problems": []}))


def bundle(u, version=NEW, pk=None) -> tuple[bytes, bytes]:
    z = build_bundle(u["tmp"] / f"b-{version}-{uuid.uuid4().hex[:6]}.zip", version)
    return z.read_bytes(), release.sign(z, pk or u["pk"])


def upload(u, version=NEW, pk=None):
    data, sig = bundle(u, version, pk)
    return u["c"].post("/api/admin/update/upload", auth=AUTH,
                       files={"bundle": ("festpos.zip", data), "sig": ("festpos.zip.sig", sig)})


def answer(u, ok=True, after=NEW, code=None, msg="OK: Server läuft") -> dict:
    """Wie festpos-update: Auftrag abholen und beantworten."""
    files = list(u["req"].iterdir())
    assert len(files) == 1, files
    req = json.loads(files[0].read_text())
    files[0].unlink()
    (u["status"] / f"{req['id']}.json").write_text(json.dumps(
        {"id": req["id"], "ok": ok, "code": code, "msg": msg, "after": after, "log": ["==> …", msg]}))
    return req


def setting(key: str) -> dict:
    from app.core import rt
    return json.loads(rt.store.setting(key) or "{}")


def put(key: str, value: dict) -> None:
    from app.core import rt
    rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))


def test_upload_checks_signature_and_single_pi_update(upd):
    u, m = upd, upd["m"]
    other, _ = keypair("Fremd")
    r = upload(u, pk=other)
    assert r.status_code == 422 and "unbekannten Schlüssel" in r.json()["detail"]
    data, sig = bundle(u)
    r = u["c"].post("/api/admin/update/upload", auth=AUTH, files={"bundle": ("f.zip", data + b"x"), "sig": ("f.sig", sig)})
    assert r.status_code == 422 and "passt nicht" in r.json()["detail"]
    assert not [p for p in u["pakete"].iterdir() if p.suffix != ".part"]
    r = upload(u)
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["version"] == NEW and up["signer"]["comment"] == "Vereins-Update" and up["node"] == "local"
    assert (u["pakete"] / f"{up['sha']}.zip").exists() and (u["pakete"] / f"{up['sha']}.sig").exists()
    ov = u["c"].get("/api/admin/update", auth=AUTH).json()
    assert ov["upload"]["due"] == ["local"] and ov["helper"]["keys"][0]["comment"] == "Vereins-Update"
    assert ov["plan"] is None and ov["nodes"][0]["key_ok"] is True
    assert "update_upload" not in u["c"].get("/api/admin/settings", auth=AUTH).json()

    r = u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]}).json()
    assert r["started"] is True and r["plan"]["version"] == NEW
    m.step()
    st = setting("update_status.local")
    assert st["state"] == "installing" and st["to"] == NEW and setting("update_lock")["node"] == "local"
    req = answer(u)
    assert req["action"] == "install" and req["sha"] == up["sha"] and req["version"] == NEW
    m.step()                                                   # (nach dem Neustart der App) Ergebnis übernehmen
    st = setting("update_status.local")
    assert st["state"] == "ok" and st["version"] == NEW and st["log"][-1] == "OK: Server läuft"
    assert setting("update_lock") == {}
    m.step()
    assert not list(u["req"].iterdir())                         # nicht nochmals
    acts = [a["action"] for a in u["c"].get("/api/admin/audit", auth=AUTH).json()["entries"]]
    assert {"update_upload", "update_start", "update_pi"} <= set(acts)


def test_upload_needs_the_helper_and_a_key(upd):
    u = upd
    (u["status"] / "keys.json").unlink()
    r = upload(u)
    assert r.status_code == 409 and "einmal von Hand" in r.json()["detail"]
    keys(u["status"])
    assert "Kein Update-Schlüssel" in upload(u).json()["detail"]


def test_nothing_to_do_for_older_version_and_warnings_need_confirmation(upd):
    u = upd
    up = upload(u, "2026.10.10-2").json()
    r = u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]})
    assert r.status_code == 409 and "haben schon" in r.json()["detail"]
    setup_shop(u["c"])
    boot = u["c"].get("/api/bootstrap").json()
    u["c"].post("/api/orders", json={"client_uuid": str(uuid.uuid4()), "table": "3", "device": "X",
                                     "lines": [{"product_id": boot["products"][0]["id"], "qty": 1}]})
    up = upload(u).json()
    r = u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]}).json()
    assert r["started"] is False and {w["code"] for w in r["warnings"]} == {"jobs", "orders"}
    assert setting("update_plan") == {}
    assert u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"], "confirm": True}).json()["started"]


# -- Drei Pi ------------------------------------------------------------------------------------------------------
def cluster(monkeypatch, u, me: str, leader: str = "a", reachable=("a", "b", "c"), versions=None, nokey=(), old=(),
            members=("a", "b", "c")):
    """Dieser Pi ist «me»; die anderen melden ihre Infos über die Datenbank (node_info.<pi>)."""
    from app import update
    from app.core import APP_VERSION, Config
    monkeypatch.setattr(Config, "node", me)
    nodes = {n: {"id": n, "reachable": n in reachable, "leader": n == leader, "voter": True} for n in members}
    monkeypatch.setattr(update, "cluster_info", lambda: {"mode": "rqlite", "nodes": nodes})
    fp = release.parse_key_line(u["line"]).fp
    for n in members:
        if n == me:
            continue
        upd_ = None if n in old else {"helper": True, "keys": [] if n in nokey else [fp], "backup": "2026.10.10-25"}
        put(f"node_info.{n}", {"version": (versions or {}).get(n, APP_VERSION), "ip": f"10.0.0.{ord(n)}", "upd": upd_,
                               "seen": int(time.time())})


def test_order_followers_first_printing_pi_last(upd, monkeypatch):
    u, m = upd, upd["m"]
    up = upload(u).json()
    cluster(monkeypatch, u, "b")
    assert u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]}).json()["started"]
    plan = setting("update_plan")
    view = m.cluster_view()
    assert m.decide(plan, "b", view, {}, time.time())["action"] == "go"
    for other in ("a", "c"):
        d = m.decide(plan, other, view, {}, time.time())
        assert (d["action"], d["reason"], d["node"]) == ("wait", "turn", "b")
    m.step()
    assert setting("update_status.b")["state"] == "installing"
    view = m.cluster_view()
    d = m.decide(plan, "c", view, m._lock(), time.time())
    assert (d["action"], d["reason"], d["node"]) == ("wait", "running", "b")       # nie zwei gleichzeitig
    answer(u)
    m.step()
    assert setting("update_status.b")["state"] == "ok"
    put("node_info.b", {**setting("node_info.b"), "version": NEW})                # b läuft jetzt mit der neuen Version
    view = m.cluster_view()
    view["infos"]["b"]["version"] = NEW
    assert m.decide(plan, "c", view, {}, time.time())["action"] == "go"
    view["infos"]["c"]["version"] = NEW
    d = m.decide(plan, "a", view, {}, time.time())
    assert d["action"] == "go"                                                      # der druckende zuletzt
    view["infos"]["a"]["version"] = NEW
    assert m.decide(plan, "a", view, {}, time.time())["action"] == "done"


def test_waits_for_unreachable_pi_and_lock(upd, monkeypatch):
    u, m = upd, upd["m"]
    up = upload(u).json()
    cluster(monkeypatch, u, "b", reachable=("a", "b"))
    r = u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]})
    assert r.status_code == 409 and "C" in r.json()["detail"]                      # Start: alle Pi müssen da sein
    put("update_plan", {"id": "p1", "kind": "install", "version": NEW, "sha": up["sha"], "size": up["size"],
                        "signer": up["signer"], "source": "b"})
    m.step()
    assert setting("update_status.b") == {}                                        # ohne c: keine Mehrheit beim Neustart
    d = m.decide(setting("update_plan"), "b", m.cluster_view(), {}, time.time())
    assert (d["reason"], d["node"]) == ("unreachable", "c")
    cluster(monkeypatch, u, "b")
    put("update_lock", {"node": "a", "plan": "p0", "at": int(time.time())})
    m.step()
    assert setting("update_status.b") == {}                                        # a hat die Sperre
    put("update_lock", {"node": "a", "plan": "p0", "at": int(time.time()) - m.LOCK_STALE - 1})
    m.step()
    assert setting("update_status.b")["state"] == "installing"                     # alte Sperre zählt nicht


def test_failure_stops_the_plan_until_retry(upd, monkeypatch):
    u, m = upd, upd["m"]
    up = upload(u).json()
    cluster(monkeypatch, u, "b")
    u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]})
    m.step()
    answer(u, ok=False, after="2026.10.10-25", code="update_failed", msg="FEHLER: Server meldet nach 90 s nicht die Version")
    m.step()
    st = setting("update_status.b")
    assert st["state"] == "failed" and st["code"] == "update_failed" and "FEHLER" in st["msg"]
    d = m.decide(setting("update_plan"), "c", m.cluster_view(), {}, time.time())
    assert (d["action"], d["node"]) == ("blocked", "b")
    sv = m.status_view()
    assert sv["plan"]["phase"] == "blocked" and sv["plan"]["node"] == "b"
    ov = u["c"].get("/api/admin/update", auth=AUTH).json()
    assert [n["state"] for n in ov["nodes"]] == ["queued", "failed", "queued"] and ov["plan"]["phase"] == "blocked"
    assert u["c"].post("/api/admin/update/retry", auth=AUTH).json()["pis"] == ["b"]
    m.step()
    assert setting("update_status.b")["state"] == "installing"
    assert u["c"].post("/api/admin/update/cancel", auth=AUTH).json()["ok"]
    assert m.decide(setting("update_plan"), "c", m.cluster_view(), {}, time.time())["action"] == "idle"


def test_start_refuses_pi_without_key_or_helper(upd, monkeypatch):
    u = upd
    up = upload(u).json()
    cluster(monkeypatch, u, "b", nokey=("c",))
    r = u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]})
    assert r.status_code == 409 and "Schlüssel" in r.json()["detail"] and "C" in r.json()["detail"]
    cluster(monkeypatch, u, "b", old=("a",))
    r = u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]})
    assert r.status_code == 409 and "von Hand" in r.json()["detail"] and "A" in r.json()["detail"]


def test_late_pi_catches_up(upd, monkeypatch):
    """Nachziehen: Plan fertig für a, b, c – dann kommt d mit der alten Version dazu und ist an der Reihe."""
    u, m = upd, upd["m"]
    up = upload(u).json()
    members = ("a", "b", "c", "d")
    cluster(monkeypatch, u, "d", versions={"a": NEW, "b": NEW, "c": NEW}, members=members, reachable=members)
    plan = {"id": "p1", "kind": "install", "version": NEW, "sha": up["sha"], "size": up["size"], "signer": up["signer"],
            "source": "d"}
    put("update_plan", plan)
    view = m.cluster_view()
    assert m.decide(plan, "d", view, {}, time.time())["action"] == "go"
    view["infos"]["d"]["version"] = NEW
    assert m.decide(plan, "d", view, {}, time.time())["action"] == "done"


def test_bundle_is_fetched_from_another_pi(upd, monkeypatch):
    """Das Paket liegt nur auf dem Pi, auf dem es hochgeladen wurde; die anderen holen es mit CLUSTER_SECRET."""
    u, m = upd, upd["m"]
    up = upload(u).json()
    sha = up["sha"]
    c = u["c"]
    assert c.get(f"/api/internal/update/{sha}.zip").status_code == 403
    assert c.get(f"/api/internal/update/{sha}.zip", headers={"X-Festpos-Secret": "falsch"}).status_code == 403
    hdr = {"X-Festpos-Secret": "test-geheimnis-für-die-pi".encode()}                # UTF-8-Bytes, wie timesync
    assert c.get("/api/internal/update/..%2Fkeys.json", headers=hdr).status_code == 404
    assert c.get(f"/api/internal/update/{sha}.zip", headers=hdr).content == (u["pakete"] / f"{sha}.zip").read_bytes()
    # jetzt so tun, als läge es nicht hier: in einen «anderen Pi» verschieben und über die Schnittstelle holen
    other = u["tmp"] / "anderer-pi"
    other.mkdir()
    for p in u["pakete"].iterdir():
        p.rename(other / p.name)

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    seen = []

    def urlopen(req, timeout, context):
        seen.append((req.full_url, req.headers.get("X-festpos-secret")))
        name = req.full_url.rsplit("/", 1)[1]
        if "10.0.0.97" in req.full_url:                                             # Pi a ist gerade weg
            raise OSError("keine Verbindung")
        return Resp((other / name).read_bytes())

    monkeypatch.setattr(m.urllib.request, "urlopen", urlopen)
    from app.core import Config
    monkeypatch.setattr(Config, "peers", ["https://10.0.0.97:8443", "https://10.0.0.98:8443", "https://10.0.0.99:8443"])
    monkeypatch.setattr(Config, "node_ip", "10.0.0.99")
    cluster(monkeypatch, u, "c")
    put("node_info.b", {**setting("node_info.b"), "ip": "10.0.0.98"})
    plan = {"sha": sha, "size": up["size"], "source": "b"}
    # ohne CA geht das Geheimnis nicht über eine ungeprüfte Verbindung
    monkeypatch.setattr(Config, "cafile", None)
    with pytest.raises(m.FetchError, match="FESTPOS_CA"):
        m.fetch(plan)
    assert not seen
    monkeypatch.setattr(Config, "cafile", "ca.crt")
    monkeypatch.setattr(m.ssl, "create_default_context", lambda cafile: ("geprüft", cafile))
    assert m.fetch(plan) == "https://10.0.0.98:8443"                              # zuerst der mit dem Paket
    assert seen[0][0].startswith("https://10.0.0.98") and seen[0][1]
    assert release.sha256_file(u["pakete"] / f"{sha}.zip") == sha
    # verfälscht unterwegs: abgelehnt
    (u["pakete"] / f"{sha}.zip").unlink()
    (other / f"{sha}.zip").write_bytes(b"x" + (other / f"{sha}.zip").read_bytes()[1:])
    with pytest.raises(m.FetchError, match="Prüfsumme"):
        m.fetch({**plan, "source": "a"})
    assert not (u["pakete"] / f"{sha}.zip").exists() and not list(u["pakete"].glob("*.part"))


def test_own_progress_after_restart(upd, monkeypatch):
    u, m = upd, upd["m"]
    from app.core import APP_VERSION
    now = time.time()
    put("update_status.local", {"plan": "p", "state": "fetching", "to": NEW, "at": int(now)})
    m.step(now)
    assert setting("update_status.local")["state"] == "wait"                      # Übertragung unterbrochen
    # Auftrag liegt noch da, Helfer arbeitet nicht daran: zuerst warten, dann aufgeben
    (u["req"] / "anfrage-r1").write_text("{}")
    put("update_status.local", {"plan": "p", "state": "installing", "req": "r1", "to": NEW, "at": int(now)})
    m.step(now + 10)
    assert setting("update_status.local")["state"] == "installing"
    m.step(now + m.NO_ANSWER + 1)
    st = setting("update_status.local")
    assert st["state"] == "failed" and st["code"] == "no_answer" and not (u["req"] / "anfrage-r1").exists()
    # Helfer arbeitet daran (läuft als root): warten, so lange es dauert
    (u["status"] / "laeuft.json").write_text(json.dumps({"id": "r2", "pid": 1}))
    put("update_status.local", {"plan": "p", "state": "installing", "req": "r2", "to": NEW, "at": int(now)})
    m.step(now + 3000)
    assert setting("update_status.local")["state"] == "installing"
    # Antwort weg (Pi neu gestartet), aber die gewünschte Version läuft: fertig
    (u["status"] / "laeuft.json").unlink()
    put("update_status.local", {"plan": "p", "state": "installing", "req": "r3", "to": APP_VERSION, "at": int(now)})
    m.step(now + 5)
    assert setting("update_status.local")["state"] == "ok"


def test_usb_stick_creates_the_plan(upd):
    u, m = upd, upd["m"]
    data, sig = bundle(u)
    sha = __import__("hashlib").sha256(data).hexdigest()
    (u["status"] / "paket").mkdir()
    (u["status"] / "paket" / f"{sha}.zip").write_bytes(data)
    (u["status"] / "paket" / f"{sha}.sig").write_bytes(sig)
    usb = {"at": int(time.time()), "state": "handed", "source": "usb:sda1", "version": NEW, "sha": sha}
    (u["status"] / "usb.json").write_text(json.dumps(usb))
    m.step()
    plan = setting("update_plan")
    assert plan["version"] == NEW and plan["by"] == "usb" and plan["source"] == "local"
    assert setting("update_status.local")["state"] == "installing"                # gleich weiter (einzelner Pi)
    assert (u["pakete"] / f"{sha}.zip").exists()
    sv = m.status_view()
    assert sv["usb"]["state"] == "handed" and sv["plan"]["version"] == NEW
    m.step()
    assert setting("update_plan")["id"] == plan["id"]                             # derselbe Stick: nur einmal
    assert "update_usb" in [a["action"] for a in u["c"].get("/api/admin/audit", auth=AUTH).json()["entries"]]
    # fremd signiert (kann nur passieren, wenn auf diesem Pi andere Schlüssel liegen als beim Helfer): nichts
    other, _ = keypair("Fremd")
    data2, sig2 = bundle(u, "99.0.1", pk=other)
    sha2 = __import__("hashlib").sha256(data2).hexdigest()
    (u["status"] / "paket" / f"{sha2}.zip").write_bytes(data2)
    (u["status"] / "paket" / f"{sha2}.sig").write_bytes(sig2)
    (u["status"] / "usb.json").write_text(json.dumps({**usb, "at": usb["at"] + 1, "sha": sha2}))
    m.usb_pickup(time.time())
    assert setting("update_plan")["id"] == plan["id"]


def test_rollback_all(upd, monkeypatch):
    u, m = upd, upd["m"]
    from app.core import APP_VERSION
    (u["alt"] / "VERSION").write_text("2026.10.10-25\n")
    ov = u["c"].get("/api/admin/update", auth=AUTH).json()
    assert ov["rollback"] == {"from": APP_VERSION, "to": "2026.10.10-25"}
    r = u["c"].post("/api/admin/update/rollback", auth=AUTH, json={"confirm": True}).json()
    assert r["started"] and r["plan"]["kind"] == "rollback"
    m.step()
    req = answer(u, after="2026.10.10-25")
    assert req == {"action": "rollback", "from": APP_VERSION, "to": "2026.10.10-25", "id": req["id"]}
    m.step()
    assert setting("update_status.local")["state"] == "ok"
    # verschiedene Stände: kein gemeinsames Zurück
    cluster(monkeypatch, u, "b", versions={"a": NEW})
    assert u["c"].post("/api/admin/update/rollback", auth=AUTH, json={"confirm": True}).status_code == 409


def test_status_page_shows_running_update(upd, monkeypatch):
    u, m = upd, upd["m"]
    up = upload(u).json()
    cluster(monkeypatch, u, "b")
    u["c"].post("/api/admin/update/start", auth=AUTH, json={"sha": up["sha"]})
    m.step()
    s = u["c"].get("/api/status", auth=AUTH).json()
    assert s["update"]["plan"] == {"kind": "install", "version": NEW, "phase": "wait", "reason": "running", "node": "b",
                                   "done": 0, "total": 3, "back": None}
    assert "upd" in s["pis"][0]["info"]                     # dieser Pi: immer live (T9)
