"""Alle gespeicherten Geheimnisse verschlüsselt (T-0-N/FestPOS#8): AES-GCM, ein Schlüssel pro Zweck (aus
CLUSTER_SECRET), bestehende Klartext-Werte werden umgeschrieben – erst wenn alle Pi die neue Version haben."""
from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import json
import sqlite3
from pathlib import Path

from tests.test_api import AUTH

ROOT = Path(__file__).resolve().parent.parent


def _raw(store, key):
    return store.scalar("SELECT value FROM settings WHERE key=?", (key,))


def test_seal_unseal_per_purpose():
    from app import secretbox
    v = secretbox.seal("fully_password", "Geheim-123", secret="s" * 32)
    assert v.startswith("enc1:") and "Geheim" not in v
    assert secretbox.unseal("fully_password", v, secret="s" * 32) == "Geheim-123"
    assert secretbox.unseal("uplink_password", v, secret="s" * 32) is None           # anderer Zweck: nicht lesbar
    assert secretbox.unseal("fully_password", v, secret="t" * 32) is None            # anderer Hauptschlüssel
    assert secretbox.unseal("fully_password", "alt-im-klartext") == "alt-im-klartext"   # älterer Stand
    assert secretbox.seal("fully_password", "") == ""
    assert secretbox.seal("x", "a", secret="s" * 32) != secretbox.seal("x", "a", secret="s" * 32)   # zufällige Nonce


def test_no_secret_in_plain_text_after_saving_and_migration(client, monkeypatch):
    """Über alle Geheimnisse: beim Speichern verschlüsselt, ältere Klartext-Werte nach migrate(); gelesen wird
    immer der Klartext."""
    from app import main, secretbox
    store = main.store
    monkeypatch.setattr(main, "_secrets_last", [1e12])      # der Takt im Hintergrund schreibt hier nicht dazwischen
    assert client.put("/api/admin/settings", auth=AUTH, json={"fully_password": "Fully-Pass-1"}).status_code == 200
    assert secretbox.sealed(_raw(store, "fully_password")) and store.setting("fully_password") == "Fully-Pass-1"
    r = client.put("/api/admin/update/net", auth=AUTH, json={"repo": "o/r", "via": "wlan", "ssid": "Fest",
                                                             "psk": "wlan-pass-123", "token": "ghp_" + "a" * 36})
    assert r.status_code == 200, r.text
    for k in ("update_net_psk", "update_net_token"):
        assert secretbox.sealed(_raw(store, k)), k
    assert store.setting("update_net_psk") == "wlan-pass-123"
    # Klartext wie aus einer älteren Version (oder aus einer alten Sicherung)
    plain = {k: f"klartext-{k}" for k in secretbox.AUTO}
    store.batch([("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (k, v)) for k, v in plain.items()])
    assert {k: store.setting(k) for k in plain} == plain                             # liest auch Klartext
    assert secretbox.migrate(store) == len(plain)
    for k, v in plain.items():
        raw = _raw(store, k)
        assert secretbox.sealed(raw) and v not in raw, k
        assert store.setting(k) == v, k
    assert secretbox.migrate(store) == 0                                             # mehrfach: nichts mehr
    rows = store.query("SELECT key, value FROM settings")
    leaks = [r["key"] for r in rows if any(v in (r["value"] or "") for v in plain.values())]
    assert not leaks, leaks


def test_payrexx_key_and_tunnel_token_old_format_migrated(client, monkeypatch):
    from app import core, main, payrexx_api, secretbox, tunnel
    store = main.store
    monkeypatch.setattr(core.Config, "cluster_secret", "c" * 40)
    old = payrexx_api.unseal_legacy
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = b"\0" * 12
    legacy = base64.b64encode(nonce + AESGCM(payrexx_api._box()).encrypt(nonce, b"px-key-123", b"payrexx")).decode()
    assert old(legacy) == "px-key-123"
    store.batch([(secretbox.UPSERT, ("gast_key_enc", legacy))])
    assert payrexx_api.unseal(_raw(store, "gast_key_enc")) == "px-key-123"
    assert secretbox.migrate(store) >= 1
    raw = _raw(store, "gast_key_enc")
    assert secretbox.sealed(raw) and payrexx_api.unseal(raw) == "px-key-123"
    assert payrexx_api.unseal(raw, tunnel.SETTING) is None                           # nur für seinen Zweck lesbar


def test_migration_waits_for_all_pis(client, monkeypatch):
    from app import core, main
    store = main.store
    # FESTPOS_PEERS nennt alle Pi, auch diesen (festpos-node)
    monkeypatch.setattr(core.Config, "peers", [f"https://192.168.55.1{i}:8443" for i in (1, 2, 3)])
    up = "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    store.batch([(up, (f"node_info.{n}", json.dumps({"version": v})))
                 for n, v in (("a", main.APP_VERSION), ("b", "0.9.0"), ("x", main.APP_VERSION))])
    assert main.all_pis_current() is False                                            # Pi B noch alt: warten
    store.batch([(up, ("node_info.b", json.dumps({"version": main.APP_VERSION})))])
    assert main.all_pis_current() is False                                            # Pi C fehlt («x» zählt nicht)
    store.batch([(up, ("node_info.c", json.dumps({"version": main.APP_VERSION})))])
    assert main.all_pis_current() is True
    monkeypatch.setattr(main, "_current", {"at": 0.0, "ok": False})
    assert main._pis_current_cached() is True
    store.batch([(up, ("node_info.c", json.dumps({"version": "0.9.0"})))])          # Pi C zurück auf eine alte Version
    assert main._pis_current_cached() is False                                        # sofort, nicht erst in einer Minute
    store.batch([(up, ("node_info.c", json.dumps({"version": main.APP_VERSION})))])
    assert main._pis_current_cached() is False                                        # «nein» gilt eine Minute weiter
    monkeypatch.setattr(core.Config, "peers", ["https://192.168.55.11:8443"])
    assert main.all_pis_current() is True                                             # Einzelbetrieb


def test_backup_helper_reads_sealed_password(tmp_path):
    from app import secretbox
    loader = importlib.machinery.SourceFileLoader("sicherung", str(ROOT / "deploy" / "festpos-sicherung"))
    spec = importlib.util.spec_from_loader("sicherung", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO settings VALUES ('backup_password', ?)",
                (secretbox.seal("backup_password", "K7QM-3XRA", secret="g" * 40),))
    con.commit()
    con.close()
    assert mod.password_of(db.read_bytes(), "g" * 40) == "K7QM-3XRA"
    try:
        mod.password_of(db.read_bytes(), "h" * 40)
        raise AssertionError("falscher Schlüssel")
    except mod.Fehler as e:
        assert e.code == "password"


def test_plaintext_starting_with_prefix_and_writes_during_update(client, monkeypatch):
    """Ein altes Passwort «enc1:…» bleibt Klartext; solange ein Pi noch alt ist, werden Geheimnisse im Klartext
    geschrieben (er könnte sie sonst nicht lesen), danach verschlüsselt."""
    from app import payrexx_api, secretbox
    for plain in ("enc1:Passwort", "enc1:AAAA"):
        assert not secretbox.sealed(plain) and secretbox.unseal("uplink_password", plain) == plain
    assert secretbox.sealed(secretbox.seal("uplink_password", "x"))
    monkeypatch.setattr(secretbox, "_may_seal", lambda: False)
    assert secretbox.put_stmt("uplink_password", "geheim-1")[1][1] == "geheim-1"
    legacy = secretbox.put_stmt("gast_key_enc", "px")[1][1]                          # bisheriges Verfahren:
    assert not secretbox.sealed(legacy) and payrexx_api.unseal_legacy(legacy) == "px"   # ältere Pi lesen es
    monkeypatch.setattr(secretbox, "_may_seal", lambda: True)
    assert secretbox.sealed(secretbox.put_stmt("uplink_password", "geheim-1")[1][1])
