"""Grundlagen des Kassenbuchs, ohne Web-Teil (auch von orders.py benutzt): Prüfsummen, MWST, Protokoll.

Prüfsumme: Jede Bestellung und jede Stornierung bekommt beim Anlegen eine Prüfsumme über ihren Inhalt
(HMAC-SHA256 mit einem Schlüssel aus CLUSTER_SECRET – liegt auf den Pi, nicht in der Datenbank). Wer nachträglich
in der Datenbank Beträge oder Mengen ändert, fällt beim Tagesabschluss auf. Der Abschluss selbst wird mit dem
vorherigen verkettet (HMAC) und sichert so den ganzen Zeitraum.
Grenze (Review 2026-11, D1): Seit T8 steht CLUSTER_SECRET verschlüsselt in jeder Sicherung und unverschlüsselt im Kartenpaket –
ein Admin kann es also erhalten. Die Prüfsummen schützen vor Änderungen an der Datenbank durch alle anderen (auch
Festleitung, eingespielte fremde Daten), nicht vor dem Admin selbst; FestPOS ist keine zertifizierte Kasse.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date

from .store import Store, Stmt, now_iso

PAYMENTS = ("cash", "card", "twint", "other", "payrexx")    # payrexx: TWINT/Karte per QR über Payrexx (T13)
PAYMENT_MODES = ("off", "optional", "required")
VAT_CODES = ("normal", "reduced", "lodging")
DEVICE_ROLES = ("service", "storno", "lead", "status")   # lead = Festleitung: Storno + Ausverkauft
# status = «Nur Statusanzeige»: das Handheld zeigt nur die Statusanzeige und darf nicht bestellen
STORNO_ROLES = ("storno", "lead")
REASONS = ("wrong", "unpaid", "complaint", "other")


# -- Prüfsummen -----------------------------------------------------------------------------------------
def _key() -> bytes:
    from .core import Config                      # spät: core lädt die Konfiguration
    return hashlib.sha256(b"festpos-ledger\0" + (Config.cluster_secret or "festpos-dev").encode()).digest()


def key_id() -> str:
    """Kennung des Schlüssels (nicht geheim): zeigt, ob eine Prüfsumme mit dem heutigen Schlüssel prüfbar ist."""
    return hashlib.sha256(b"id\0" + _key()).hexdigest()[:8]


def canon(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _mac(body: dict) -> str:
    return f"{key_id()}:{hmac.new(_key(), canon(body), hashlib.sha256).hexdigest()}"


def keyed_hash(data: bytes) -> str:
    """«Schlüssel-Kennung:HMAC» über beliebige Bytes (Kette der Tagesabschlüsse)."""
    return f"{key_id()}:{hmac.new(_key(), data, hashlib.sha256).hexdigest()}"


def order_body(order: dict, items: list[dict]) -> dict:
    body = {"uuid": order["client_uuid"], "at": order["created_at"], "table": order["table_label"],
            "device": order.get("device"), "test": int(bool(order.get("test"))), "total": order["total_cents"],
            "items": [[i.get("line_no"), i["name"], i["price_cents"], i["qty"], i.get("options"), i.get("note"),
                       i.get("delivery"), i.get("vat_bp")]
                      for i in sorted(items, key=lambda x: (x.get("line_no") or 0, x["name"]))]}
    if order.get("device_cert"):            # erst ab 2026.10.10-21 – ältere Prüfsummen bleiben gültig
        body["cert"] = order["device_cert"]
    return body


def order_digest(order: dict, items: list[dict]) -> str:
    return _mac(order_body(order, items))


def cancel_body(c: dict, lines: list[dict]) -> dict:
    """lines: [{"line_no", "qty", "amount_cents"}] – Positionen der Bestellung, nicht die Datenbank-Nummern."""
    return {"uuid": c["client_uuid"], "order": c["order_uuid"], "at": c["created_at"], "reason": c["reason"],
            "by": c.get("by_user") or c.get("by_device"), "amount": c["amount_cents"],
            "lines": sorted([[x["line_no"], x["qty"], x["amount_cents"]] for x in lines])}


def cancel_digest(c: dict, lines: list[dict]) -> str:
    return _mac(cancel_body(c, lines))


RECEIPT_DIGEST_V = 2                       # T41: Version der Prüfsumme neuer Quittungen


def receipt_body(r: dict) -> dict:
    """Quittung: Nummer, Anfrage, Bestellung, Zeit, Person (Einzeln), Betrag, Positionen [line_no, Name, Preis, Menge,
    MWST-Satz]. lines und data dürfen schon gelesen (Liste/dict) oder noch JSON-Text sein.
    Version 2 (T41, digest_v=2): zusätzlich alles, was gedruckt wird – der ganze Inhalt von data (Empfänger, Zahlart,
    MWST-Nummer, Veranstaltung, Kopf- und Fusszeilen, Gestaltung …) und pro Position Optionen, MWST-Code und Betrag.
    Alte Quittungen (digest_v leer) prüfen weiter mit Version 1. Eine Fälschung der Version nützt nichts: die
    gespeicherte Prüfsumme passt dann nicht mehr."""
    lines = r["lines"] if isinstance(r["lines"], list) else json.loads(r["lines"] or "[]")
    body = {"no": r.get("no"), "req": r["request_id"], "order": r["order_uuid"], "at": r["created_at"],
            "part": r.get("part"), "total": r["total_cents"], "test": int(bool(r.get("test"))),
            # offener Tisch: Positionen aus mehreren Bestellungen – die Bestellung gehört dann mit in die Prüfsumme
            "lines": sorted([[x["line_no"], x["name"], x["price"], x["qty"], x.get("vat_bp")]
                             + ([x["order_uuid"]] if x.get("order_uuid") else []) for x in lines])}
    if int(r.get("digest_v") or 1) < 2:
        return body
    data = r.get("data")
    data = json.loads(data or "{}") if isinstance(data, str) else (data or {})
    body["v"] = 2
    body["data"] = data
    body["lines"] = sorted(([x["line_no"], x["name"], x["price"], x["qty"], x.get("vat_bp"), x.get("vat_code"),
                             x.get("options") or "", x.get("amount")]
                            + ([x["order_uuid"], x.get("order_no")] if x.get("order_uuid") else []) for x in lines),
                           key=lambda v: json.dumps(v, ensure_ascii=False))
    return body


def receipt_digest(r: dict) -> str:
    return _mac(receipt_body(r))


def correction_body(c: dict) -> dict:
    """T45: Korrektur früherer Tage (Rückerstattung einer schon abgeschlossenen Bestellung)."""
    vat = c.get("vat")
    vat = json.loads(vat or "[]") if isinstance(vat, str) else (vat or [])
    return {"key": c["key"], "kind": c["kind"], "order": c["order_id"], "amount": c["amount_cents"],
            "goods": c["goods_cents"], "tip": c["tip_cents"], "vat": vat, "at": c["created_at"], "why": c.get("why")}


def correction_digest(c: dict) -> str:
    return _mac(correction_body(c))


def short_check(digest: str | None) -> str:
    """Kurze Prüfnummer für den Druck («1a2b-3c4d») – die ersten Zeichen der Prüfsumme."""
    h = (digest or "").partition(":")[2]
    return f"{h[:4]}-{h[4:8]}" if len(h) >= 8 else "–"


def check_digest(stored: str | None, body: dict) -> str:
    """ok | bad | other_key (mit einem früheren Schlüssel erstellt) | missing (ältere Version ohne Prüfsumme)."""
    if not stored:
        return "missing"
    kid, _, _ = stored.partition(":")
    if kid != key_id():
        return "other_key"
    return "ok" if hmac.compare_digest(stored, _mac(body)) else "bad"


# -- MWST -----------------------------------------------------------------------------------------------
def vat_enabled(store: Store) -> bool:
    return store.setting("vat_enabled", "0") == "1"


def vat_rates_on(store: Store, day: str | None = None) -> dict[str, int]:
    """Gültige Sätze an einem Tag (YYYY-MM-DD): {"normal": 810, …} in Hundertstel-Prozent."""
    day = day or date.today().isoformat()
    out: dict[str, int] = {}
    for r in store.query("SELECT code, rate_bp FROM vat_rates WHERE valid_from <= ? ORDER BY valid_from", (day,)):
        out[r["code"]] = r["rate_bp"]                 # spätere Zeilen überschreiben frühere
    return out


def vat_split(gross: int, bp: int) -> tuple[int, int]:
    """Bruttobetrag (inkl. MWST) -> (MWST, netto), auf den Rappen gerundet."""
    vat = (gross * bp * 2 + (10000 + bp)) // (2 * (10000 + bp)) if gross >= 0 else -vat_split(-gross, bp)[0]
    return vat, gross - vat


# -- Protokoll ------------------------------------------------------------------------------------------
def audit_stmt(actor: str | None, action: str, **details) -> Stmt:
    return ("INSERT INTO audit_log(at, actor, action, details) VALUES (?,?,?,?)",
            (now_iso(), actor or "?", action, json.dumps(details, ensure_ascii=False, default=str)))


def audit_if_changed_stmt(actor: str | None, action: str, **details) -> Stmt:
    """Protokolleintrag nur, wenn die Anweisung direkt davor etwas geändert hat (SQLite changes(); im selben Batch –
    Änderung und Eintrag gehen zusammen durch oder gar nicht)."""
    sql, params = audit_stmt(actor, action, **details)
    return (sql.replace("VALUES (?,?,?,?)", "SELECT ?,?,?,? WHERE changes() > 0"), params)


def audit(store: Store, actor: str | None, action: str, **details) -> None:
    store.execute(*audit_stmt(actor, action, **details))


def device_label(cert: str | None, name: str | None) -> str:
    """«XCover101 (Anna)»: das ausgerollte Gerät (Name im Gerätezertifikat), in Klammern der Name, den die Bedienung
    in der App eingetragen hat. Ohne Zertifikat nur der Name aus der App; derselbe Name nur einmal."""
    cert, name = (cert or "").strip(), (name or "").strip()
    if cert and name and cert.casefold() != name.casefold():
        return f"{cert} ({name})"
    return cert or name


def device_role(store: Store, cert: dict | None) -> str:
    """Rolle des Handys mit diesem Gerätezertifikat (devices.client_cert) – über die Geräte-ID, nie über den Namen (T1):
    sonst erbte ein neues Gerät die Rolle eines gesperrten gleichen Namens. Nur ein Zertifikat, das dieses System nicht
    kennt (nicht in device_certs), wird noch nach dem Namen gesucht (wie vor T1)."""
    from . import device_ids
    if not cert:
        return "service"
    did = device_ids.device_id_of(store, cert.get("serial") or "")
    if did:
        role = store.scalar("SELECT role FROM device_role_ids WHERE device_id=?", (did,))
    else:
        role = store.scalar("SELECT role FROM device_roles WHERE name=? COLLATE NOCASE", (cert.get("name") or "",))
    return role if role in DEVICE_ROLES else "service"
