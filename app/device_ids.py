"""Geräte-ID (T1): ein Gerät erkennt man nie am Namen.

- Gerät mit Zertifikat: `device_certs.device_id` = Seriennummer des ersten Zertifikats. Eine Erneuerung (`replaces`)
  erbt die ID ihres Vorgängers – so bleibt sie über alle Erneuerungen gleich (ein Fingerprint wechselte jedes Mal).
- Handy mit fester IP (Einstellung `monitor_devices`): eigenes Feld `id` (p + 12 Hex-Zeichen) und – wenn es zu einem
  Zertifikat gehört – `cert` = dessen device_id.

Der Gerätename muss unter den gültigen Geräten eindeutig sein (name_conflict). Gesperrte und abgelaufene Geräte zählen
nicht: ihr Name ist sofort wieder frei, z. B. für ein Ersatzhandy. Der Name in der Bestell-App («Name dieses Geräts»)
ist nur Anzeige und darf doppelt vorkommen.

Nur die Datenbank (Store), kein Web-Teil: auch store.init_schema benutzt das Modul (backfill).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets

PHONE_ID_RE = re.compile(r"p[0-9a-f]{12}")
CERT_COLS = "id, name, serial, created_at, expires_at, revoked_at, replaces, removed_at, device_id"


def new_phone_id() -> str:
    return "p" + secrets.token_hex(6)


def norm(name: str | None) -> str:
    """Vergleichsform eines Namens: ohne Gross-/Kleinschreibung, Leerzeichen zusammengefasst."""
    return " ".join(str(name or "").split()).casefold()


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def cert_valid(row: dict, day: str | None = None) -> bool:
    """Zertifikat gilt: nicht gesperrt, nicht gelöscht, nicht abgelaufen."""
    return not row.get("revoked_at") and not row.get("removed_at") and \
        (not row.get("expires_at") or row["expires_at"] >= (day or today()))


def assign(rows: list[dict], day: str | None = None) -> dict[int, str]:
    """Zeilen-ID -> device_id für alle Zertifikate. Fehlt device_id (Datenbank vor T1, oder ein Pi mit alter Version
    hat ausgestellt), gilt die Kette über `replaces`. Alte Ketten ohne Verknüpfung mit gleichem Namen (Erneuerungen
    aus der Zeit vor `replaces`) sind EIN Gerät – ausser mehrere davon gelten gleichzeitig: dann sind es verschiedene
    Geräte (und der Name ist doppelt, siehe Verwaltung)."""
    day = day or today()
    by_serial = {r["serial"]: r for r in rows}
    found: dict[int, str] = {}
    root_of: dict[int, dict] = {}
    for r in rows:
        x, seen = r, set()
        while not x.get("device_id"):
            p = by_serial.get(x.get("replaces") or "")
            if p is None or p["id"] in seen or p is x:
                break
            seen.add(x["id"])
            x = p
        if x.get("device_id"):
            found[r["id"]] = x["device_id"]
        else:
            root_of[r["id"]] = x
    if root_of:                                            # Ketten ohne ID: nach Namen der Wurzel gruppieren
        members: dict[int, list[dict]] = {}
        for r in rows:
            if r["id"] in root_of:
                members.setdefault(root_of[r["id"]]["id"], []).append(r)
        roots = sorted({x["id"]: x for x in root_of.values()}.values(), key=lambda x: (x["created_at"] or "", x["id"]))
        groups: dict[str, list[dict]] = {}
        for x in roots:
            groups.setdefault(norm(x["name"]), []).append(x)
        root_id: dict[int, str] = {}
        for group in groups.values():
            valid = [x for x in group if any(cert_valid(m, day) for m in members[x["id"]])]
            for x in group:
                root_id[x["id"]] = x["serial"] if len(valid) > 1 else group[0]["serial"]
        for rid, x in root_of.items():
            found[rid] = root_id[x["id"]]
    return found


def cert_rows(store) -> list[dict]:
    return store.query(f"SELECT {CERT_COLS} FROM device_certs ORDER BY created_at, id")


def lineages(store, rows: list[dict] | None = None, day: str | None = None) -> dict[str, dict]:
    """device_id -> {device_id, name (neuestes Zertifikat), certs (neuestes zuerst), valid, valid_names, removed}."""
    day = day or today()
    rows = cert_rows(store) if rows is None else rows
    ids = assign(rows, day)
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(ids[r["id"]], {"device_id": ids[r["id"]], "certs": []})["certs"].append(r)
    for x in out.values():
        x["certs"].sort(key=lambda c: (c["expires_at"] or "", c["id"]), reverse=True)
        live = [c for c in x["certs"] if not c["removed_at"]]
        x["name"] = (live or x["certs"])[0]["name"]
        x["valid_names"] = {norm(c["name"]) for c in x["certs"] if cert_valid(c, day)}
        x["valid"] = bool(x["valid_names"])
        x["removed"] = not live
    return out


def blocked(lins: dict[str, dict]) -> set[str]:
    """Geräte, deren Zertifikate alle gesperrt sind (T3): stehen unter «Gesperrt», auch mit Handy-Eintrag – der bleibt
    fürs Entsperren, fehlt aber auf der Statusanzeige und im DHCP."""
    return {k for k, x in lins.items() if not x["removed"] and all(c["revoked_at"] for c in x["certs"] if not c["removed_at"])}


def device_id_of(store, serial: str) -> str | None:
    """device_id zum Zertifikat mit dieser Seriennummer (None: nicht von diesem System ausgestellt)."""
    row = store.query_one("SELECT device_id FROM device_certs WHERE serial=?", (serial,))
    if not row:
        return None
    if row["device_id"]:
        return row["device_id"]
    rows = cert_rows(store)                                  # selten: von einem Pi mit alter Version ausgestellt
    hit = next((r for r in rows if r["serial"] == serial), None)
    return assign(rows).get(hit["id"]) if hit else None


def phones_raw(store) -> list[dict]:
    """Alle Einträge aus monitor_devices (Handys und Netzwerkgeräte), roh."""
    try:
        items = json.loads(store.setting("monitor_devices") or "[]")
    except ValueError:
        return []
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def normalize(items: list[dict], lins: dict[str, dict], legacy: bool = False) -> bool:
    """Jedes Handy bekommt eine ID; ein Handy ohne Zertifikat wird mit dem gültigen Gerät gleichen Namens verknüpft,
    wenn es genau eines gibt und kein anderes Handy dazu gehört. legacy (erste Migration): gibt es kein gültiges, auch
    mit einem gesperrten/abgelaufenen – vor T1 gehörten sie über den Namen zusammen. -> True, wenn geändert."""
    changed = False
    phones = [d for d in items if d.get("kind") == "phone"]
    for d in phones:
        if not PHONE_ID_RE.fullmatch(str(d.get("id") or "")):
            d["id"] = new_phone_id()
            changed = True
        if d.get("cert") and (d["cert"] not in lins or lins[d["cert"]]["removed"]):   # Zertifikat endgültig weg
            d.pop("cert")
            changed = True
    taken = {d["cert"] for d in phones if d.get("cert")}
    for d in phones:
        if d.get("cert"):
            continue
        n = norm(d.get("name"))
        hits = [k for k, x in lins.items() if not x["removed"] and x["valid"] and n in x["valid_names"] and k not in taken]
        if legacy and not hits:
            hits = [k for k, x in lins.items() if not x["removed"] and norm(x["name"]) == n and k not in taken]
        if len(hits) == 1:
            d["cert"] = hits[0]
            taken.add(hits[0])
            changed = True
    return changed


def relink(store) -> None:
    """Nach Sperren, Entsperren, Löschen, Ausstellen: Handys neu mit ihren Zertifikaten verknüpfen (normalize)."""
    items = phones_raw(store)
    if normalize(items, lineages(store)):
        store.execute(*save_phones(items))


def holds_name(d: dict, lins: dict[str, dict]) -> bool:
    """Belegt dieser Handy-Eintrag seinen Namen? Nein, wenn er zu einem gesperrten/abgelaufenen Gerät gehört (E3)."""
    if d.get("kind") != "phone":
        return False
    x = lins.get(d.get("cert") or "")
    return not x or x["valid"]


def save_phones(items: list[dict]) -> tuple[str, tuple]:
    return ("INSERT INTO settings(key,value) VALUES ('monitor_devices', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(items, ensure_ascii=False),))


def name_conflict(store, name: str, own_cert: str | None = None, own_phone: str | None = None,
                  items: list[dict] | None = None, lins: dict[str, dict] | None = None) -> str | None:
    """Heisst schon ein ANDERES gültiges Gerät so? -> dessen Name, sonst None. Zählt: Geräte mit gültigem Zertifikat
    und Handys mit fester IP. Nicht: gesperrte, abgelaufene, gelöschte (auch nicht ihr Handy-Eintrag) – und das eigene
    Gerät (own_cert/own_phone)."""
    n = norm(name)
    if not n:
        return None
    items = phones_raw(store) if items is None else items
    lins = lineages(store) if lins is None else lins
    phones = [d for d in items if d.get("kind") == "phone"]
    if own_phone and not own_cert:
        own_cert = next((d.get("cert") for d in phones if d.get("id") == own_phone), None)
    for k, x in lins.items():
        if k != own_cert and not x["removed"] and n in x["valid_names"]:
            return x["name"]
    for d in phones:
        if (own_phone and d.get("id") == own_phone) or (own_cert and d.get("cert") == own_cert) or not holds_name(d, lins):
            continue
        if norm(d.get("name")) == n:
            return str(d.get("name"))
    return None


def conflicts(store) -> list[str]:
    """Namen, die mehrere gültige Geräte tragen (Altbestand vor T1) – für die Warnung in der Übersicht."""
    lins = lineages(store)
    items = phones_raw(store)
    normalize(items, lins)                                   # nur zum Zählen: Handy und Zertifikat gleichen Namens
    seen: dict[str, set[str]] = {}
    shown: dict[str, str] = {}
    for k, x in lins.items():
        if x["valid"] and not x["removed"]:
            for n in x["valid_names"]:
                seen.setdefault(n, set()).add(k)
                shown.setdefault(n, x["name"])
    for d in items:
        if holds_name(d, lins):
            n = norm(d.get("name"))
            seen.setdefault(n, set()).add(d.get("cert") or d.get("id") or d.get("ip") or "")
            shown.setdefault(n, str(d.get("name")))
    return sorted(shown[n] for n, keys in seen.items() if len(keys) > 1)


def backfill(store) -> None:
    """Migration (beim Start und nach «Sicherung einspielen», wiederholbar): fehlende device_id eintragen, Rollen der
    Altgeräte (nach Namen) übernehmen, Handys mit ID und Verknüpfung zum Zertifikat."""
    rows = cert_rows(store)
    stmts: list[tuple[str, tuple]] = []
    missing = [r for r in rows if not r["device_id"]]
    if missing:
        ids = assign(rows)
        for r in missing:
            stmts.append(("UPDATE device_certs SET device_id=? WHERE id=? AND device_id IS NULL", (ids[r["id"]], r["id"])))
        fresh = {ids[r["id"]] for r in missing} - {r["device_id"] for r in rows if r["device_id"]}
        roles = {norm(r["name"]): r["role"] for r in store.query("SELECT name, role FROM device_roles")}
        have = {r["device_id"] for r in store.query("SELECT device_id FROM device_role_ids")}
        for r in rows:
            r["device_id"] = ids[r["id"]]
        lins = lineages(store, rows)
        for did in sorted(fresh - have):
            role = roles.get(norm(lins[did]["name"]))
            if role:
                stmts.append(("INSERT OR IGNORE INTO device_role_ids(device_id, role) VALUES (?,?)", (did, role)))
    else:
        lins = lineages(store, rows)
    items = phones_raw(store)
    if normalize(items, lins, legacy=bool(missing)):
        stmts.append(save_phones(items))
    if stmts:
        store.batch(stmts, transaction=True)
