"""Trinkgeld: an der Kasse erfassen – Verwaltung › Einstellungen › Kasse, Standard aus.

Trinkgeld ist kein Umsatz und hat keine MWST. Es steht getrennt im Tagesabschluss (Bildschirm, PDF, CSV und damit in
der Prüfsumme), nie auf der Quittung.
  * Bar, Karte, TWINT: das Handy meldet pro Bestellung die Summe (Tabelle tips, auch nachgereicht wie die Zahlart).
    Zahlart des Trinkgelds = Zahlart der Bestellung (nicht angegeben = bar).
  * Payrexx (NFC am Handy, QR am Tisch): im Payrexx-Betrag enthalten und genau bekannt (payrexx_payments.tip_cents);
    zurückerstattet wird es nur, wenn die ganze Bestellung zurückgeht.
  * Storno ändert das Trinkgeld nicht. Übungsbestellungen zählen nicht.
Kassensturz: Bargeld laut Kasse = Wechselgeld + Umsatz + Trinkgeld − Karte − TWINT − Andere − Payrexx − Trinkgeld
ausbezahlt (Karte, TWINT und Payrexx enthalten ihr Trinkgeld, so wie es auf deren Abrechnung steht).
"""
from __future__ import annotations

from collections import defaultdict

from . import ledger_base as lb
from .store import Store, now_iso

MAX_CENTS = 100_000                          # CHF 1000 pro Bestellung: mehr ist ein Tippfehler
KINDS = ("cash", "card", "twint", "other")   # Zahlart der Bestellung -> Zahlart des Trinkgelds, sonst «none» (bar)


def enabled(store: Store) -> bool:
    return store.setting("tips_enabled", "0") == "1"


def set_tip(store: Store, *, client_uuid: str, amount: int, device: str | None) -> str:
    """Trinkgeld einer Bestellung setzen (Summe, ersetzt den alten Wert). -> ok | unknown | closed.
    Neue Zeile bei jeder Änderung: der Tagesabschluss erkennt so ein Trinkgeld, das während der Rechnung kam."""
    res = store.execute("INSERT OR REPLACE INTO tips(order_id, amount_cents, device, updated_at) SELECT id, ?, ?, ? "
                        "FROM orders WHERE client_uuid=? AND closing_id IS NULL",
                        (int(amount), (device or "")[:60] or None, now_iso(), client_uuid))
    if res.rows_affected:
        return "ok"
    return "closed" if store.scalar("SELECT 1 FROM orders WHERE client_uuid=?", (client_uuid,)) else "unknown"


def max_id(store: Store) -> int:
    return int(store.scalar("SELECT MAX(id) FROM tips") or 0)


def period(store: Store, orders: list[dict]) -> dict:
    """Trinkgeld der Bestellungen eines Zeitraums: Total, pro Zahlart, pro Handy und als Liste (für die CSV)."""
    empty = {"total": 0, "by_payment": {}, "by_device": [], "list": []}
    if not orders:
        return empty
    by_id = {o["id"]: o for o in orders}
    marks = ",".join("?" * len(by_id))
    rows = [(r["order_id"], None, r["amount_cents"]) for r in store.query(
        f"SELECT order_id, amount_cents FROM tips WHERE amount_cents>0 AND order_id IN ({marks}) ORDER BY order_id",
        tuple(by_id))]
    # Payrexx: Trinkgeld ist im Betrag; zurückerstattet wird zuerst der Rest, das Trinkgeld nur mit allem. T45: wie
    # bei Payrexx zählt auch eine zugesagte (noch nicht ausgeführte) Rückerstattung schon als zurück
    from .payrexx import REFUNDED_SQL
    rows += [(r["order_id"], "payrexx", r["tip"]) for r in store.query(
        f"SELECT p.order_id, MIN(p.tip_cents, p.amount_cents - {REFUNDED_SQL}) AS tip FROM payrexx_payments p WHERE "
        f"p.status='confirmed' AND p.test=0 AND p.tip_cents>0 AND p.order_id IN ({marks}) ORDER BY p.id", tuple(by_id))
        if r["tip"] > 0]
    # offene Tische (app/tabs.py): Trinkgeld pro Zahlung, Zahlart der Zahlung, bei der ersten Bestellung des Tisches
    first: dict[int, int] = {}
    for o in sorted(orders, key=lambda o: o["id"]):
        if o.get("tab_id"):
            first.setdefault(o["tab_id"], o["id"])
    if first:
        tmarks = ",".join("?" * len(first))
        rows += [(first[r["tab_id"]], r["payment"] if r["payment"] in KINDS else "none", r["tip_cents"]) for r in store.query(
            f"SELECT tab_id, payment, tip_cents FROM tab_payments WHERE undone_at IS NULL AND tip_cents>0 "
            f"AND tab_id IN ({tmarks}) ORDER BY id", tuple(first))]
    if not rows:
        return empty
    by_payment: dict[str, int] = defaultdict(int)
    by_device: dict[str, int] = defaultdict(int)
    out = []
    for oid, kind, cents in sorted(rows, key=lambda x: x[0]):
        o = by_id[oid]
        pay = kind or (o.get("payment") if o.get("payment") in KINDS else "none")
        who = lb.device_label(o.get("device_cert"), o.get("device")) or "?"
        by_payment[pay] += cents
        by_device[who] += cents
        out.append({"order": oid, "payment": pay, "amount": cents, "device": who, "at": o["created_at"],
                    "table": o["table_label"]})
    return {"total": sum(by_payment.values()),
            "by_payment": {k: by_payment[k] for k in (*KINDS, "none", "payrexx") if k in by_payment},
            "by_device": [{"device": k, "amount": v} for k, v in sorted(by_device.items(), key=lambda kv: kv[0].lower())],
            "list": out}
