"""Kurzauskunft über den Raft-Cluster für die anderen Knoten.

festpos-rqlited fragt vor dem Start die anderen Pi (GET /api/health/cluster), ob seine Daten noch zum
laufenden Cluster gehören. Beispiel: Pi A lief im Notfall-Einzelbetrieb weiter, B und C kommen mit ihrem
alten Stand zurück. Sie dürfen dann nicht mit diesem alten Stand eine Mehrheit bilden (REV-029).
"""
from __future__ import annotations

from pathlib import Path

EINZELBETRIEB_FLAG = ".festpos-einzelbetrieb"     # schreibt «festpos-notfall einzelbetrieb» ins Datenverzeichnis


def summarize(status: dict | None, node: str, data_dir: str | Path | None = None) -> dict:
    """Aus rqlite /status: Zustand, Leader und stimmberechtigte Mitglieder dieses Knotens."""
    out: dict = {"node": node, "state": None, "leader": None, "members": [], "einzelbetrieb": False}
    if data_dir:
        out["einzelbetrieb"] = (Path(data_dir) / EINZELBETRIEB_FLAG).exists()
    try:
        s = status["store"]                                  # type: ignore[index]
        out["state"] = s["raft"]["state"]
        out["leader"] = (s.get("leader") or {}).get("node_id") or None
        out["members"] = sorted(str(n["id"]) for n in (s.get("nodes") or [])
                                if n.get("suffrage", "voter") == "voter")
    except (KeyError, TypeError, AttributeError):
        pass
    return out
