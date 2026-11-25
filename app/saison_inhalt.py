"""Aufbau eines Saison-Archivs prüfen (T-0-N/FestPOS#12) – ohne FastAPI, damit tools/archiv.py am PC dieselben Regeln
nutzt wie die Prüfung auf dem Pi (app/saison.py)."""
from __future__ import annotations

import hashlib
import json

NEED = ("abschluesse.json", "quittungen.json", "kopien.json")


def records(members: dict[str, bytes]) -> tuple[dict, list, list] | None:
    """(Inhaltsangabe, Abschlüsse, Quittungen) – None, wenn eine davon fehlt oder nicht die erwartete Form hat."""
    try:
        info = json.loads(members["archiv.json"])
        closings = json.loads(members["abschluesse.json"])
        recs = json.loads(members["quittungen.json"])
    except (KeyError, ValueError):
        return None
    if not (isinstance(info, dict) and isinstance(closings, list) and isinstance(recs, list)
            and all(isinstance(r, dict) for r in closings + recs)):        # leere Liste Quittungen ist in Ordnung
        return None
    return info, closings, recs


def complete(members: dict[str, bytes], info: dict, closings: list, recs: list) -> bool:
    """Aufbau stimmt: Inhaltsangabe nennt alle Dateien, die Anzahlen passen, jeder Abschluss hat sein PDF."""
    names = info.get("files")
    if not isinstance(names, dict) or not names or any(not isinstance(h, str) for h in names.values()) or not closings:
        return False
    try:
        need = set(NEED) | {f"pdf/abschluss-{int(r['id']):05d}.pdf" for r in closings}
        copies = json.loads(members["kopien.json"])
        counts = (info["closings"]["count"], info["receipts"]["count"], info["receipts"]["copies"])
    except (KeyError, TypeError, ValueError):
        return False
    return (need <= set(names) <= set(members) and isinstance(copies, list)
            and counts == (len(closings), len(recs), len(copies)))


def bad_digests(members: dict[str, bytes], info: dict) -> list[str]:
    """Dateien, deren SHA-256 nicht zur Inhaltsangabe passt (oder die fehlen)."""
    return [n for n, h in info.get("files", {}).items() if hashlib.sha256(members.get(n, b"\0missing")).hexdigest() != h]
