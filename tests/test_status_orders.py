"""T29: Bestellungen seit dem Tagesabschluss für die Statusanzeige – dieselben Rechte wie /api/status."""
from __future__ import annotations

from tests.test_api import AUTH


def test_orders_need_the_same_rights_as_status(client):
    assert client.get("/api/status/orders").status_code == client.get("/api/status").status_code == 401
    r = client.get("/api/status/orders", auth=AUTH)
    assert r.status_code == 200 and set(r.json()) >= {"orders", "more", "count", "total_cents"}
    assert client.get("/api/status/orders/1").status_code == 401
