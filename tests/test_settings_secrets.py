"""Review 2026-11, S1: GET /api/admin/settings ist für jede Rolle lesbar (auch «Anzeige») – darüber darf nie ein Geheimnis
hinausgehen. Vorher stand dort das Sicherungs-Passwort im Klartext: mit einer Sicherung vom USB-Stick kam man so an
alle Schlüssel und an das Notfall-Passwort."""
from __future__ import annotations

from app import backup
from tests.test_api import AUTH


def test_no_secret_in_settings_for_any_role(client):
    c = client
    from app.core import rt
    marker = "GEHEIM-nur-fuer-diesen-Test"
    extra = ("pin_hash", "test_pin_hash", "gast_key_enc", "gast_tunnel_enc", "fully_kiosk_pin_enc", "uplink",
             "device_pin.abc", "node_info.a", "system_ids", "restore_check")
    for k in (*backup.SECRET_KEYS, *extra):
        rt.store.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (k, f"{marker}-{k}"))
    assert rt.store.setting("backup_password").startswith(marker)
    for name, role in (("Vreni", "view"), ("Lea", "lead"), ("Andrea", "admin")):
        assert c.post("/api/admin/users", auth=AUTH, json={"name": name, "role": role, "password": "geheim123"}).status_code == 200
        r = c.get("/api/admin/settings", auth=(name, "geheim123"))
        assert r.status_code == 200, role
        assert marker not in r.text, f"Geheimnis in /api/admin/settings für Rolle {role}"
        s = r.json()
        assert not [k for k in s if k in backup.SECRET_KEYS or k.startswith(("backup_password", "device_pin.", "node_info."))]
    # das Sicherungs-Passwort gibt es nur über die eigene, protokollierte Route – und nur für Admin
    assert c.get("/api/admin/backup/password?show=1", auth=("Vreni", "geheim123")).status_code == 403
    assert c.get("/api/admin/backup/password?show=1", auth=("Lea", "geheim123")).status_code == 403
    assert c.get("/api/admin/backup/password?show=1", auth=AUTH).json()["password"].startswith(marker)
