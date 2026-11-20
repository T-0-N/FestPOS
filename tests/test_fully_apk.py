"""T6: Fully-APK vom Pi verteilen – Version aus der APK, Kopie auf alle Pi, Verteilen an die Handys, Versionsprüfung.
Ohne echtes Handy: Fully ist nachgebaut (tests.test_fully.FakeFully), die anderen Pi über ihre gemeldeten Infos."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import time
import zipfile

import pytest

from tests.test_api import AUTH
from tests.test_fully import _fully_phone


# -- AndroidManifest.xml im Binärformat (AXML), wie es in einer APK steckt ------------------------------------
def _pool(strings: list[str], utf8: bool) -> bytes:
    data, offs = b"", []
    for s in strings:
        offs.append(len(data))
        if utf8:
            raw = s.encode()
            data += bytes([len(s), len(raw)]) + raw + b"\0"
        else:
            data += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\0\0"
    data += b"\0" * (-len(data) % 4)
    start = 28 + 4 * len(strings)
    body = b"".join(struct.pack("<I", o) for o in offs) + data
    return struct.pack("<HHIIIIII", 1, 28, 28 + len(body), len(strings), 0, 0x100 if utf8 else 0, start, 0) + body


def axml(package: str = "com.fullykiosk.emm", name: str = "1.61.2-emm", code: int = 1612, utf8: bool = False,
         names_by_id: bool = False) -> bytes:
    """names_by_id: Namen der Attribute leer (wie in verkleinerten APKs), erkennbar nur über die Ressourcen-ID."""
    strings = ["" if names_by_id else "versionCode", "" if names_by_id else "versionName", "package", "manifest", name, package]
    pool = _pool(strings, utf8)
    resmap = struct.pack("<HHI", 0x0180, 8, 16) + struct.pack("<II", 0x0101021B, 0x0101021C)
    attrs = (struct.pack("<IIIHBBI", 0xFFFFFFFF, 0, 0xFFFFFFFF, 8, 0, 0x10, code) +
             struct.pack("<IIIHBBI", 0xFFFFFFFF, 1, 4, 8, 0, 0x03, 4) +
             struct.pack("<IIIHBBI", 0xFFFFFFFF, 2, 5, 8, 0, 0x03, 5))
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 3, 20, 20, 3, 0, 0, 0)
    elem = struct.pack("<HHIII", 0x0102, 16, 16 + len(ext) + len(attrs), 1, 0xFFFFFFFF) + ext + attrs
    body = pool + resmap + elem
    return struct.pack("<HHI", 3, 8, 8 + len(body)) + body


def apk_bytes(manifest: bytes | None = None, filler: int = 50_000) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if manifest is not None:
            z.writestr("AndroidManifest.xml", manifest)
        z.writestr("classes.dex", b"x" * filler)
    return buf.getvalue()


def _sum(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


@pytest.mark.parametrize("utf8", [False, True])
def test_manifest_version_and_package(utf8):
    from app import fully_apk as fa
    assert fa.parse_axml(axml(utf8=utf8)) == {"package": "com.fullykiosk.emm", "version_name": "1.61.2-emm", "version_code": 1612}
    assert fa.parse_axml(axml(names_by_id=True))["version_code"] == 1612          # nur über die Ressourcen-ID
    for bad in (b"", b"PK\x03\x04", b"\x03\x00\x08\x00" + b"\xff" * 40):
        with pytest.raises((ValueError, struct.error)):
            fa.parse_axml(bad)


def test_manifest_from_apk_or_file_name(tmp_path):
    from app import fully_apk as fa
    good, broken, none = tmp_path / "a.apk", tmp_path / "b.apk", tmp_path / "c.apk"
    good.write_bytes(apk_bytes(axml()))
    broken.write_bytes(apk_bytes(b"kaputt"))
    none.write_bytes(b"PK\x03\x04 keine zip")
    assert fa.apk_manifest(good, "egal.apk")["version_code"] == 1612
    assert fa.apk_manifest(broken, "Fully-Kiosk-Browser-v1.61.2-emm.apk") == {"version_name": "1.61.2-emm"}
    assert fa.apk_manifest(none, "fully.apk") == {}


def test_compare_versions_and_kind_of_fully():
    from app import fully_apk as fa
    apk = {"version_name": "1.61.2-emm", "version_code": 1612}
    assert fa.version_cmp({"version_name": "1.61.2"}, apk) == 0                # Endung zählt nicht
    assert fa.version_cmp({"version_name": "1.60.0-emm"}, apk) == -1
    assert fa.version_cmp({"version_name": "1.62"}, apk) == 1
    assert fa.version_cmp({"version_name": "1.61.2", "version_code": 1611}, apk) == -1   # versionCode geht vor
    assert fa.version_cmp({"version_name": "?"}, apk) is None
    assert fa.variant({"fully_version": "1.60.1-play"}) == "play"
    assert fa.variant({"fully_version": "1.61.2-emm", "device_owner": True}) == "owner"
    assert fa.variant({"fully_version": "1.61.2-emm", "device_owner": False}) == "app"
    assert fa.variant({"fully_version": "1.61.2"}) is None


def test_upload_reads_version_rejects_play_store_variant_and_counts_for_all_pi(client, tmp_path, monkeypatch):
    from app import core, fully
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    play = apk_bytes(axml(package="de.ozerov.fully", name="1.60.1-play"))
    r = client.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("Fully-play.apk", play)})
    assert r.status_code == 422 and "de.ozerov.fully" in r.json()["detail"] and not (tmp_path / "fully" / "fully.apk").exists()
    data = apk_bytes(axml())
    r = client.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("Fully-Kiosk-Browser-v1.61.2-emm.apk", data)}).json()
    assert r["apk"]["version_name"] == "1.61.2-emm" and r["apk"]["version_code"] == 1612 and r["apk"]["checksum"] == _sum(data)
    assert r["meta"]["checksum"] == _sum(data) and r["meta"]["from"] == core.Config.node
    assert r["pis"] == [{"id": core.Config.node, "here": True, "state": "ok"}]
    assert json.loads((tmp_path / "fully" / "fully.json").read_text())["version_code"] == 1612
    audit = client.get("/api/admin/audit?q=fully_apk_upload", auth=AUTH).json()["entries"]
    assert audit and audit[0]["details"]["version"] == "1.61.2-emm"


def test_provision_fields_only_for_the_chosen_source(client):
    base = {"code": "", "ssid": "FestPOS", "security": "WPA"}
    net = {**base, "apk_local": "0", "apk_url": "https://example.org/f.apk", "checksum": "A" * 43}
    assert client.put("/api/admin/fully/provision", auth=AUTH, json=net).status_code == 200
    r = client.put("/api/admin/fully/provision", auth=AUTH, json={**base, "apk_local": "1"})
    assert r.status_code == 200 and r.json()["apk_url"] == net["apk_url"]          # APK vom Pi: Adresse bleibt
    r = client.put("/api/admin/fully/provision", auth=AUTH, json={**base, "apk_local": "0", "apk_url": "", "checksum": ""})
    assert r.status_code == 422 and "Adresse" in r.json()["detail"]


def test_internal_route_only_with_cluster_secret(client, tmp_path, monkeypatch):
    from app import core, fully
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    monkeypatch.setattr(core.Config, "cluster_secret", "S" * 40)
    data = apk_bytes(axml())
    client.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("f.apk", data)})
    assert client.get("/api/internal/fully/apk").status_code == 403
    assert client.get("/api/internal/fully/apk", headers={"X-Festpos-Secret": "falsch"}).status_code == 403
    r = client.get("/api/internal/fully/apk", headers={"X-Festpos-Secret": "S" * 40})
    assert r.status_code == 200 and r.content == data and r.headers["x-festpos-checksum"] == _sum(data)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _meta_for(data: bytes) -> dict:
    return {"checksum": _sum(data), "size": len(data), "name": "f.apk", "at": "2026-10-29T10:00:00", "version_name": "1.61.2-emm",
            "version_code": 1612, "from": "b"}


def test_fetch_from_another_pi_checks_checksum_size_and_space(tmp_path, monkeypatch):
    from app import core, fully, fully_apk as fa
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    monkeypatch.setattr(core.Config, "cluster_secret", "S" * 40)
    monkeypatch.setattr(core.Config, "cafile", str(tmp_path / "ca.crt"))
    monkeypatch.setattr(fa.ssl, "create_default_context", lambda **kw: None)
    data = apk_bytes(axml())
    sent: list = []

    def urlopen(req, timeout, context):
        sent.append((req.full_url, req.headers.get("X-festpos-secret")))
        return _Resp(served)
    monkeypatch.setattr(fa.urllib.request, "urlopen", urlopen)
    served = data[:-1] + b"?"                                                     # verfälscht
    with pytest.raises(fa.FetchError, match="Prüfsumme"):
        fa.fetch("https://192.168.55.12:8443", _meta_for(data))
    assert not (tmp_path / "fully" / "fully.apk").exists() and not list((tmp_path / "fully").glob("*.part"))
    served = data + b"mehr"
    with pytest.raises(fa.FetchError, match="grösser"):
        fa.fetch("https://192.168.55.12:8443", _meta_for(data))
    served = data
    fa.fetch("https://192.168.55.12:8443", _meta_for(data))
    assert (tmp_path / "fully" / "fully.apk").read_bytes() == data
    assert json.loads((tmp_path / "fully" / "fully.json").read_text())["checksum"] == _sum(data)
    assert sent[-1] == ("https://192.168.55.12:8443/api/internal/fully/apk", "S" * 40)
    monkeypatch.setattr(fa.shutil, "disk_usage", lambda p: type("U", (), {"free": len(data)})())
    with pytest.raises(fa.FetchError, match="Platz"):
        fa.fetch("https://192.168.55.12:8443", _meta_for(data))


def _own_tick(monkeypatch, fa):
    """Nur der Test ruft sync_tick: Der Monitor-Takt der App (main._tick, jede Sekunde) ruft es sonst ebenfalls und
    kann dazwischen schon einen Abruf starten (in der CI einmal gesehen: fetch zweimal). Läuft noch ein alter Abruf,
    abwarten."""
    tick = fa.sync_tick
    monkeypatch.setattr(fa, "sync_tick", lambda: None)
    t = fa._sync.get("thread")
    if t:
        t.join(5)
    return tick


def test_sync_fetches_from_a_pi_that_has_it_and_waits_after_failures(client, tmp_path, monkeypatch):
    from app import core, fully, fully_apk as fa
    sync_tick = _own_tick(monkeypatch, fa)
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.11:8443", "https://192.168.55.12:8443"])
    data = apk_bytes(axml())
    fa._put("fully_apk", _meta_for(data))
    have = {"b": _sum(data), "c": "alt"}
    monkeypatch.setattr(fa, "_pis", lambda: [{"id": "a", "here": True, "reachable": True, "info_age": 0,
                                              "info": {"apk": fa.local_checksum()}}] + [
        {"id": n, "here": False, "reachable": True, "info_age": 5, "info": {"ip": f"192.168.55.{11 + i}", "apk": have[n]}}
        for i, n in enumerate(("b", "c"))])
    assert fa.holders(_sum(data)) == [{"id": "b", "ip": "192.168.55.11"}]
    calls = []

    def fetch(base, m):
        calls.append(base)
        if len(calls) == 1:
            raise fa.FetchError("Pi weg")
        fa.write_local_json(m)
        (fully.APK_DIR / fully.APK_FILE).write_bytes(data)
    monkeypatch.setattr(fa, "fetch", fetch)
    fully.APK_DIR.mkdir(parents=True)
    fa._sync.update(thread=None, fails=0, next=0.0, error=None, want=None)
    sync_tick()
    fa._sync["thread"].join(5)
    assert calls == ["https://192.168.55.11:8443"] and fa._sync["fails"] == 1 and fa._sync["error"] == "Pi weg"
    assert [p["state"] for p in fa.pis_view()] == ["failed", "ok", "copying"]
    sync_tick()                                                                   # wartet eine Minute
    assert fa._sync["thread"].is_alive() is False and len(calls) == 1
    fa._sync["next"] = 0.0
    sync_tick()
    fa._sync["thread"].join(5)
    assert len(calls) == 2 and fa.current_here() and fa.pis_view()[0]["state"] == "ok"


def test_apk_from_before_t6_counts_for_all_pi_once_newest_wins(client, tmp_path, monkeypatch):
    from app import core, fully, fully_apk as fa
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    fully.APK_DIR.mkdir(parents=True)
    data = apk_bytes(axml())
    (fully.APK_DIR / "fully.apk").write_bytes(data)
    (fully.APK_DIR / "fully.json").write_text(json.dumps({"checksum": _sum(data), "size": len(data),
                                                          "name": "Fully-v1.61.2-emm.apk", "at": "2026-08-01T10:00:00"}))
    m = fa.adopt_local()
    assert m["checksum"] == _sum(data) and m["version_code"] == 1612 and m["from"] == core.Config.node   # Version nachgelesen
    assert fa.adopt_local() is None                                                     # schon massgeblich
    newer = {**m, "checksum": "andere", "at": "2026-09-01T10:00:00"}
    fa._put("fully_apk", newer)
    assert fa.adopt_local() is None and fa.meta()["checksum"] == "andere"              # ältere als die hochgeladene
    assert fa.adopt_local(force=True)["checksum"] == _sum(data)                         # «Vom Stick übernehmen»


def _phone_setup(client, tmp_path, monkeypatch, version="1.60.0-emm", owner=True):
    """Fully-Handy 127.0.0.1 (nachgebaut), APK 1.61.2 auf dem Pi, Version des Handys gemeldet."""
    from app import fully, fully_apk as fa
    from tests import test_fully
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    srv, _ = _fully_phone(client, tmp_path, monkeypatch)
    monkeypatch.setitem(test_fully.INFO, "appVersionName", version)
    monkeypatch.setitem(test_fully.INFO, "isDeviceOwner", owner)
    client.post("/api/admin/fully/apk", auth=AUTH, files={"apk": ("f.apk", apk_bytes(axml()))})
    client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"})
    fa.note_versions(fully.rt.monitor.fully_view())
    return srv


def test_versions_written_only_on_change_and_outdated_phones(client, tmp_path, monkeypatch):
    from app import fully, fully_apk as fa
    srv = _phone_setup(client, tmp_path, monkeypatch)
    try:
        v = fa.versions()
        assert list(v.values())[0]["name"] == "1.60.0-emm" and list(v.values())[0]["variant"] == "owner"
        writes = []
        orig = fully.rt.store.execute
        monkeypatch.setattr(fully.rt.store, "execute", lambda *a, **k: writes.append(a) or orig(*a, **k))
        fa.note_versions(fully.rt.monitor.fully_view())
        assert writes == []                                                         # nichts geändert: kein Schreibzugriff
        monkeypatch.undo()
        assert [p["name"] for p in fa.outdated()] == ["XCover 9"]
        stats = client.get("/api/admin/stats", auth=AUTH).json()
        assert stats["fully_outdated"] == ["XCover 9"]
        r = client.get("/api/admin/fully/apk", auth=AUTH).json()
        assert r["outdated"][0]["version"] == "1.60.0-emm" and r["phones"][0]["cmp"] == -1
    finally:
        srv.stop()


@pytest.mark.parametrize("version,warn", [("1.60.1-play", False), ("1.62.0-emm", False), ("1.61.2-emm", False)])
def test_no_warning_for_play_store_newer_or_same_version(client, tmp_path, monkeypatch, version, warn):
    from app import fully_apk as fa
    srv = _phone_setup(client, tmp_path, monkeypatch, version, owner=None)
    try:
        assert bool(fa.outdated()) is warn
        assert client.get("/api/admin/stats", auth=AUTH).json()["fully_outdated"] == []
    finally:
        srv.stop()


def test_distribute_sends_the_pi_address_and_waits_for_the_new_version(client, tmp_path, monkeypatch):
    from app import fully, fully_apk as fa
    from tests import test_fully
    srv = _phone_setup(client, tmp_path, monkeypatch)
    try:
        r = client.post("/api/admin/fully/apk/distribute", auth=AUTH, json={}).json()
        assert r["version"] == "1.61.2-emm" and r["results"] == [{"id": r["results"][0]["id"], "name": "XCover 9", "state": "sent"}]
        assert srv.commands[-1] == "loadApkFile" and b"url=http%3A%2F%2Ftestserver%2Ffully%2Ffully.apk" in srv.received
        pid = r["results"][0]["id"]
        assert fa.push_state()["phones"][pid]["state"] == "sent" and fa.outdated()[0]["push"]["state"] == "sent"
        # Fully meldet die neue Version: angekommen, keine Warnung mehr
        monkeypatch.setitem(test_fully.INFO, "appVersionName", "1.61.2-emm")
        client.post("/api/admin/fully/check", auth=AUTH, json={"ip": "127.0.0.1"})
        fa.note_versions(fully.rt.monitor.fully_view())
        fa._push_round(time.time())
        assert fa.push_state()["phones"][pid]["state"] == "ok" and fa.outdated() == []
        assert client.get("/api/admin/audit?q=fully_apk", auth=AUTH).json()["entries"]
    finally:
        srv.stop()


def test_distribute_only_admin_and_play_store_phone_gets_nothing(client, tmp_path, monkeypatch):
    srv = _phone_setup(client, tmp_path, monkeypatch, "1.60.1-play")
    try:
        assert client.post("/api/admin/users", auth=AUTH, json={"name": "Lea", "role": "lead", "password": "Lea-passwort"}).status_code == 200
        assert client.post("/api/admin/fully/apk/distribute", auth=("Lea", "Lea-passwort"), json={}).status_code == 403
        assert client.get("/api/admin/fully/apk", auth=("Lea", "Lea-passwort")).status_code == 200    # Hinweis sehen
        r = client.post("/api/admin/fully/apk/distribute", auth=AUTH, json={}).json()
        assert r["results"] == [] and "loadApkFile" not in srv.commands
    finally:
        srv.stop()


def test_unreachable_or_busy_phone_stays_open_and_is_retried(client, tmp_path, monkeypatch):
    from app import fully, fully_apk as fa
    srv = _phone_setup(client, tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(fa, "_busy", lambda p: True)                         # Bestellung wartet noch
        r = client.post("/api/admin/fully/apk/distribute", auth=AUTH, json={}).json()
        assert r["results"][0]["state"] == "busy" and "loadApkFile" not in srv.commands
        pid = r["results"][0]["id"]
        assert fa.push_state()["phones"][pid]["state"] == "open"
        cfg = fully.config()
        assert fully.asyncio.run(fa.push_pending(cfg)) is None                    # immer noch beschäftigt
        monkeypatch.setattr(fa, "_busy", lambda p: False)
        monkeypatch.setattr(fa, "_idle", lambda p, now, screen: True)
        monkeypatch.setattr(fully, "renew_host", lambda: "192.168.55.10")
        assert fully.asyncio.run(fa.push_pending(cfg)) == pid
        assert srv.commands[-1] == "loadApkFile" and b"url=http%3A%2F%2F192.168.55.10%2Ffully%2Ffully.apk" in srv.received
        job = fa.push_state()["phones"][pid]
        assert job["state"] == "sent" and job["tries"] == 1
        # kommt die neue Version nicht, gilt der Versuch nach PUSH_WAIT als gescheitert – nach 3 am Tag «von Hand»
        fa._push_round(time.time() + fa.PUSH_WAIT + 1)
        assert fa.push_state()["phones"][pid]["state"] == "open"
        st = fa.push_state()
        st["phones"][pid].update(state="sent", tries=fa.PUSH_TRIES, at=0)
        fa._put("fully_apk_push", st)
        fa._push_round(time.time())
        assert fa.push_state()["phones"][pid]["state"] == "failed"
    finally:
        srv.stop()


def test_update_app_uses_a_pi_that_has_the_apk(client, tmp_path, monkeypatch):
    from app import fully_apk as fa
    srv = _phone_setup(client, tmp_path, monkeypatch)
    try:
        fa._put("fully_apk", {**fa.meta(), "checksum": "neuer"})                  # neue APK, hier noch nicht da
        monkeypatch.setattr(fa, "holders", lambda c: [{"id": "b", "ip": "192.168.55.11"}])
        r = client.post("/api/admin/fully/update-app", auth=AUTH, json={"ip": "127.0.0.1"}).json()
        assert r["state"] == "ok" and b"url=http%3A%2F%2F192.168.55.11%2Ffully%2Ffully.apk" in srv.received
        assert list(fa.push_state()["phones"].values())[0]["state"] == "sent"
        monkeypatch.setattr(fa, "holders", lambda c: [])                         # noch nirgends ganz
        r = client.post("/api/admin/fully/update-app", auth=AUTH, json={"ip": "127.0.0.1"})
        assert r.status_code == 409 and "kopiert" in r.json()["detail"]
        assert client.get("/api/admin/fully/provision/qr", auth=AUTH).status_code == 409
    finally:
        srv.stop()


def test_restore_checklist_apk_copied_from_another_pi(client, tmp_path, monkeypatch):
    from app import fully, fully_apk as fa, restore_check
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    data = apk_bytes(axml())
    fa._put("fully_apk", _meta_for(data))
    monkeypatch.setattr(fa, "holders", lambda c: [{"id": "b", "ip": "192.168.55.11"}])
    assert restore_check._apk_item() == {"id": "apk", "state": "wait", "pi": "b"}
    monkeypatch.setattr(fa, "holders", lambda c: [])
    assert restore_check._apk_item()["state"] == "warn"
    fully.APK_DIR.mkdir(parents=True)
    (fully.APK_DIR / "fully.apk").write_bytes(data)
    fa.write_local_json(_meta_for(data))
    assert restore_check._apk_item() == {"id": "apk", "state": "ok"}


def test_single_pi_without_the_apk_says_so(client, tmp_path, monkeypatch):
    """Neue Karte, kein anderer Pi hat die APK: «fehlt» statt «wird kopiert» – kein Versuch ins Leere."""
    from app import fully, fully_apk as fa
    sync_tick = _own_tick(monkeypatch, fa)
    monkeypatch.setattr(fully, "APK_DIR", tmp_path / "fully")
    fa._put("fully_apk", _meta_for(apk_bytes(axml())))
    fa._sync.update(thread=None, fails=0, next=0.0, error=None, want=None)
    sync_tick()
    assert fa._sync["thread"] is None and fa._sync["error"] == "no_source"
    assert [p["state"] for p in fa.pis_view()] == ["missing"]
