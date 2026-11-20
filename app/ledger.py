"""Kassenbuch: Zahlart, Storno (als Gegenbuchung), Tagesabschluss mit Kassensturz und Abrechnung pro Handy,
MWST-Auswertung, Prüfung der Prüfsummen, Protokoll und Rollen der Handys.

Grundsätze
  * Eine gesendete Bestellung ist ein Verkauf. Sie wird nie geändert oder gelöscht, Korrekturen sind Stornos
    (wer, wann, warum, welche Positionen). Das Bedienungspersonal muss dafür nichts tun.
  * Die Zahlart ist freiwillig (Einstellung «Zahlart an der Kasse»: aus / freiwillig / Pflicht). Beim Abschluss
    trägt die Festleitung die Summen von Karte (SumUp) und TWINT aus deren Abrechnung ein, der Rest ist Bargeld.
  * Der Tagesabschluss fasst alle offenen Bestellungen zusammen, bekommt eine fortlaufende Nummer und eine
    Prüfsumme, die mit dem vorherigen Abschluss verkettet ist. Er enthält alle Bestellungen als CSV und bleibt
    auch nach «Alle Bestellungen löschen» erhalten (10 Jahre aufbewahren: PDF und CSV herunterladen).
Grundlagen (Prüfsummen, MWST, Protokolleintrag): ledger_base.py
"""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import hmac
import io
import json
import re
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import devices, i18n, receipts, tips
from . import ledger_base as lb
from .core import admin, current_user, db, log, rt
from .csv_import import csv_cell
from .i18n import t
from .store import Store, StoreError, now_iso

router = APIRouter()                                     # Handy (Gerätezertifikat)
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


class LedgerError(Exception):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def _http(e: LedgerError) -> HTTPException:
    return HTTPException(e.status, str(e))


def actor_of(request: Request) -> str:
    """Wer handelt: Benutzer der Verwaltung, sonst das Handy (Name aus dem Gerätezertifikat)."""
    u = current_user(request)
    if u:
        return u.get("name") or "?"
    info = devices.client_cert(request)
    return f"device:{info['name']}" if info else "device:?"


def money(c: int | None) -> str:
    c = int(c or 0)
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}{c // 100}.{c % 100:02d}"


# -- Zahlart ----------------------------------------------------------------------------------------------
def payment_mode(store: Store) -> str:
    m = store.setting("payment_mode", "off")
    return m if m in lb.PAYMENT_MODES else "off"


def set_payment(store: Store, *, payment: str | None, client_uuid: str | None = None, order_id: int | None = None,
                actor: str | None = None) -> int:
    """Zahlart setzen (nur offene Bestellungen). Mit actor: Änderung durch die Verwaltung -> Protokoll."""
    if payment is not None and payment not in lb.PAYMENTS:
        raise LedgerError(t("err.ledger.payment"))
    where, arg = ("client_uuid=?", client_uuid) if client_uuid else ("id=?", order_id)
    row = store.query_one(f"SELECT id, payment, closing_id FROM orders WHERE {where}", (arg,))
    if not row:
        raise LedgerError(t("err.ledger.order_unknown"), 404)
    if row["closing_id"]:
        raise LedgerError(t("err.ledger.closed"), 409)
    if actor is None and row["payment"] == "payrexx" and payment != "payrexx":
        return row["id"]          # vom Handheld: eine bestätigte Kartenzahlung bleibt (versehentlich «Bar» getippt)
    stmts = [("UPDATE orders SET payment=? WHERE id=? AND closing_id IS NULL", (payment, row["id"]))]
    if actor and row["payment"] != payment:
        stmts.append(lb.audit_stmt(actor, "payment", order=row["id"], old=row["payment"], new=payment))
    store.batch(stmts, transaction=True)
    return row["id"]


# -- Storno -----------------------------------------------------------------------------------------------
def cancelled_qty(store: Store, order_id: int) -> dict[int, int]:
    rows = store.query("SELECT ci.item_id, SUM(ci.qty) AS q FROM cancellation_items ci "
                       "JOIN cancellations c ON c.id=ci.cancellation_id WHERE c.order_id=? GROUP BY ci.item_id",
                       (order_id,))
    return {r["item_id"]: int(r["q"] or 0) for r in rows}


def reason_text(code: str, note: str | None) -> str:
    """Grund in der Standardsprache (steht so im Abschluss und auf dem Storno-Bon)."""
    base = i18n.t(f"ledger.reason.{code}" if code in lb.REASONS else "ledger.reason.other", i18n.default_lang())
    note = " ".join((note or "").split())[:80]
    return f"{base}: {note}" if note else base


CLOSED_MEANWHILE = "NOT NULL constraint failed: cancellations.order_id"   # siehe cancel()
GUARD_FAILED = "NOT NULL constraint failed: cancellations.created_at"      # siehe cancel(guard=…)


class GuardFailed(LedgerError):
    """T43: die Bedingung des Aufrufers (guard) galt beim Schreiben nicht mehr – nichts storniert."""


def cancel(store: Store, *, order_id: int, lines: dict[int, int] | None, reason: str, note: str | None = None,
           by_user: str | None = None, by_device: str | None = None, client_uuid: str, bons: bool = True,
           guard: tuple[str, tuple] | None = None, collect: list | None = None, _attempt: int = 0) -> dict:
    """Storno als Gegenbuchung. lines: {Position (line_no): Menge}, None = alles, was noch offen ist.
    Druckt an jeder betroffenen Station einen Storno-Bon. Ist die ganze Bestellung storniert und ein Bon noch
    gar nicht gedruckt, wird dieser stattdessen zurückgehalten (kein Bon und Storno-Bon für nichts).
    Zwei Stornos derselben Bestellung zugleich (Handy und Verwaltung): «seq» ist pro Bestellung eindeutig – nur der
    erste gilt, der zweite rechnet mit dem neuen Stand nochmals (nie mehr storniert als bestellt).
    bons=False: keine Storno-Bons und nichts zurückhalten (offener Tisch «nicht bezahlt»: die Gäste haben gegessen)."""
    dup = store.query_one("SELECT id, order_id, amount_cents FROM cancellations WHERE client_uuid=?", (client_uuid,))
    if dup:
        return {"id": dup["id"], "order_id": dup["order_id"], "amount_cents": dup["amount_cents"], "duplicate": True,
                "printer_ids": []}
    order = store.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
    if not order:
        raise LedgerError(t("err.ledger.order_unknown"), 404)
    if order["closing_id"]:
        raise LedgerError(t("err.ledger.closed"), 409)
    # zuerst die laufende Nummer, DANN den Stand lesen: wer danach storniert, hat diese Nummer schon belegt
    seq = int(store.scalar("SELECT MAX(COALESCE(MAX(seq), 0), COUNT(*)) FROM cancellations WHERE order_id=?",
                           (order_id,)) or 0) + 1
    items = store.query("SELECT * FROM order_items WHERE order_id=? ORDER BY line_no, id", (order_id,))
    done = cancelled_qty(store, order_id)
    by_line = {i["line_no"]: i for i in items}
    want: dict[int, int] = {}                             # item_id -> Menge
    if lines is None:
        for i in items:
            left = i["qty"] - done.get(i["id"], 0)
            if left > 0:
                want[i["id"]] = left
    else:
        for line_no, qty in lines.items():
            it = by_line.get(line_no)
            if not it:
                raise LedgerError(t("err.ledger.line_unknown", line=line_no))
            left = it["qty"] - done.get(it["id"], 0)
            if qty > left:
                raise LedgerError(t("err.ledger.too_many", name=it["name"], count=left))
            if qty > 0:
                want[it["id"]] = qty
    if not want:
        raise LedgerError(t("err.ledger.nothing_left"))
    by_id = {i["id"]: i for i in items}
    amount = sum(by_id[iid]["price_cents"] * q for iid, q in want.items())
    full = all(done.get(i["id"], 0) + want.get(i["id"], 0) >= i["qty"] for i in items)
    ts = now_iso()
    rtext = reason_text(reason, note)
    row = {"client_uuid": client_uuid, "order_uuid": order["client_uuid"], "created_at": ts, "reason": rtext,
           "by_user": by_user, "by_device": by_device, "amount_cents": amount}
    clines = [{"line_no": by_id[iid]["line_no"], "qty": q, "amount_cents": by_id[iid]["price_cents"] * q}
              for iid, q in want.items()]
    digest = lb.cancel_digest(row, clines)
    # order_id nur, solange die Bestellung offen ist – sonst NULL und der ganze Batch scheitert: ein Abschluss
    # zwischen der Prüfung oben und hier lässt kein Storno zu einer abgeschlossenen Bestellung zurück
    # guard (T43): SQL-Bedingung des Aufrufers, geprüft im Schreibbefehl – gilt sie nicht, bleibt created_at leer und
    # der ganze Batch scheitert (z.B. «Nicht bezahlt»: nur, solange am Tisch keine Zahlung steht)
    g_sql, g_args = guard or ("1", ())
    stmts = [("INSERT INTO cancellations(client_uuid, order_id, reason, by_user, by_device, amount_cents, created_at, "
              "digest, seq) VALUES (?,(SELECT id FROM orders WHERE id=? AND closing_id IS NULL),?,?,?,?,"
              f"CASE WHEN {g_sql} THEN ? END,?,?) RETURNING id",
              (client_uuid, order_id, rtext, by_user, by_device, amount, *g_args, ts, digest, seq))]
    stmts += [("INSERT INTO cancellation_items(cancellation_id, item_id, qty, amount_cents) VALUES "
               "((SELECT id FROM cancellations WHERE client_uuid=?),?,?,?)",
               (client_uuid, iid, q, by_id[iid]["price_cents"] * q)) for iid, q in want.items()]
    # Storno-Bons: an die Stationen der stornierten Positionen – nur wo für diese Bestellung etwas gedruckt
    # wird oder wurde. Ganz storniert: noch nicht gedruckte Bons zurückhalten, dort braucht es keinen Storno-Bon.
    jobs = store.query("SELECT station_id, status FROM print_jobs WHERE order_id=? AND kind IN ('table','single')",
                       (order_id,))
    had = {j["station_id"] for j in jobs if j["status"] != "cancelled"}
    # failed = Renderfehler, nichts gedruckt (T38): wie pending – kein Storno-Bon, beim ganzen Storno zurückhalten
    printed = {j["station_id"] for j in jobs if j["status"] not in ("pending", "failed", "cancelled")}
    affected = {by_id[iid]["printer_id"] for iid in want if by_id[iid]["printer_id"]}
    targets = affected & (printed if full else had) if bons else set()
    if full and bons:
        stmts.append(("UPDATE print_jobs SET status='cancelled', last_error=?, error_code='storno' "
                      "WHERE order_id=? AND status IN ('pending','failed') AND kind IN ('table','single')",
                      (i18n.t("srv.job.storno", i18n.default_lang()), order_id)))
    for st in sorted(targets):
        stmts.append(("INSERT INTO print_jobs(order_id, station_id, printer_id, kind, note, created_at) "
                      "VALUES (?,?,?,'storno',?,?)", (order_id, st, st, client_uuid, ts)))
    stmts.append(lb.audit_stmt(by_user or f"device:{by_device}", "storno", order=order_id, amount=amount,
                               reason=rtext, full=full, test=bool(order["test"])))
    # T37: Online-Bestellung (QR am Tisch) -> Rückerstattungsauftrag im selben Batch; ein doppelt gesendetes Storno
    # findet ihn schon vor, nichts geht zwischen Storno und Rückerstattung verloren
    from . import gast
    stmts += gast.storno_refund_stmts(store, order_id, amount, client_uuid, by_user or f"device:{by_device}")
    if collect is not None:                            # T43: nur sammeln – der Aufrufer schreibt alles in einem Batch
        collect.extend(stmts)
        return {"id": None, "order_id": order_id, "amount_cents": amount, "full": full, "printer_ids": sorted(targets)}
    try:
        res = store.batch(stmts, transaction=True)
    except StoreError as e:
        if e.is_unique_violation and e.index == 0 and _attempt < 5:      # gleiche Anfrage oder anderer Storno zugleich
            return cancel(store, order_id=order_id, lines=lines, reason=reason, note=note, by_user=by_user,
                          by_device=by_device, client_uuid=client_uuid, bons=bons, guard=guard, _attempt=_attempt + 1)
        if CLOSED_MEANWHILE in str(e):
            raise LedgerError(t("err.ledger.closed"), 409) from None
        if GUARD_FAILED in str(e):
            raise GuardFailed(t("err.tab.changed"), 409) from None
        raise
    return {"id": res[0].rows[0]["id"], "order_id": order_id, "amount_cents": amount, "full": full,
            "printer_ids": sorted(targets)}


def order_detail(store: Store, order_id: int) -> dict | None:
    o = store.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
    if not o:
        return None
    items = store.query("SELECT id, line_no, product_id, name, price_cents, qty, options, note, delivery, vat_code, vat_bp "
                        "FROM order_items WHERE order_id=? ORDER BY line_no, id", (order_id,))
    done = cancelled_qty(store, order_id)
    for i in items:
        i["cancelled"] = done.get(i["id"], 0)
    cancels = store.query("SELECT id, reason, by_user, by_device, amount_cents, created_at FROM cancellations "
                          "WHERE order_id=? ORDER BY id", (order_id,))
    storno = sum(c["amount_cents"] for c in cancels)
    o.pop("digest", None)
    o["device"] = lb.device_label(o.pop("device_cert", None), o.get("device"))
    return {**o, "items": items, "cancellations": cancels, "storno_cents": storno,
            "net_cents": o["total_cents"] - storno, "receipts": receipts.of_order(store, o["client_uuid"])}


# -- Offener Zeitraum, Auswertung -------------------------------------------------------------------------
def _open_rows(store: Store) -> dict:
    return {
        "orders": store.query("SELECT * FROM orders WHERE closing_id IS NULL AND test=0 ORDER BY id"),
        "items": store.query("SELECT i.* FROM order_items i JOIN orders o ON o.id=i.order_id "
                             "WHERE o.closing_id IS NULL AND o.test=0 ORDER BY i.order_id, i.line_no, i.id"),
        "cancels": store.query("SELECT c.*, o.client_uuid AS order_uuid FROM cancellations c "
                               "JOIN orders o ON o.id=c.order_id "
                               "WHERE c.closing_id IS NULL AND o.test=0 ORDER BY c.id"),
        "citems": store.query("SELECT ci.*, i.line_no, i.name, i.price_cents, i.vat_code, i.vat_bp, i.order_id "
                              "FROM cancellation_items ci JOIN cancellations c ON c.id=ci.cancellation_id "
                              "JOIN order_items i ON i.id=ci.item_id JOIN orders o ON o.id=c.order_id "
                              "WHERE c.closing_id IS NULL AND o.test=0 ORDER BY ci.id"),
        "receipts": store.query("SELECT * FROM receipts WHERE closing_id IS NULL AND test=0 ORDER BY id"),
        "rcopies": store.query("SELECT c.id, c.receipt_id FROM receipt_copies c JOIN receipts r ON r.id=c.receipt_id "
                               "WHERE c.closing_id IS NULL AND r.test=0 ORDER BY c.id"),
        # offene Tische (app/tabs.py): Zahlungen der Tische, deren Bestellungen in diesem Zeitraum liegen
        "tab_pay": store.query("SELECT p.*, t.table_label FROM tab_payments p JOIN tabs t ON t.id=p.tab_id "
                               "WHERE p.undone_at IS NULL AND t.test=0 AND p.tab_id IN (SELECT tab_id FROM orders "
                               "WHERE closing_id IS NULL AND test=0 AND tab_id IS NOT NULL) ORDER BY p.id"),
        # Teilzahlungen sofort kassierter Bestellungen (Teilen/Einzeln mit Zahlart pro Person)
        "order_pay": store.query("SELECT p.* FROM order_payments p JOIN orders o ON o.id=p.order_id "
                                 "WHERE p.undone_at IS NULL AND o.closing_id IS NULL AND o.test=0 ORDER BY p.id"),
        # T45: Korrekturen früherer Tage (Rückerstattung einer schon abgeschlossenen Bestellung)
        "corrections": store.query("SELECT * FROM corrections WHERE closing_id IS NULL ORDER BY id"),
    }


# T45: alles, was der Abschluss liest und was sich während des Rechnens ändern kann, als ein Fingerabdruck. Steht er
# beim Schreiben nicht mehr gleich, wird neu gerechnet (wie bei Stornos und Trinkgeld): bestätigte Kartenzahlungen,
# Zahlarten offener Bestellungen, Tischzahlungen, Rückerstattungsaufträge, offene Korrekturen.
FINGERPRINT_SQL = (
    "(SELECT COUNT(*) || '/' || COALESCE(SUM(id * 7 + amount_cents), 0) || '/' || COALESCE(SUM(refunded_cents), 0) "
    "FROM payrexx_payments WHERE status='confirmed' AND test=0) || '|' || "
    "(SELECT COALESCE(SUM(id * (CASE payment WHEN 'cash' THEN 1 WHEN 'card' THEN 2 WHEN 'twint' THEN 3 WHEN 'other' "
    "THEN 4 WHEN 'payrexx' THEN 5 ELSE 0 END)), 0) FROM orders WHERE closing_id IS NULL AND test=0) || '|' || "
    "(SELECT COUNT(*) || '/' || COALESCE(SUM(amount_cents), 0) || '/' || COALESCE(SUM(tip_cents), 0) "
    "FROM tab_payments WHERE undone_at IS NULL) || '|' || "
    "(SELECT COUNT(*) || '/' || COALESCE(SUM(amount_cents), 0) || '/' || COALESCE(SUM(id * (CASE payment WHEN 'cash' "
    "THEN 1 WHEN 'card' THEN 2 WHEN 'twint' THEN 3 WHEN 'other' THEN 4 WHEN 'payrexx' THEN 5 ELSE 0 END)), 0) "
    "FROM order_payments WHERE undone_at IS NULL) || '|' || "
    "(SELECT COUNT(*) || '/' || COALESCE(SUM(amount_cents), 0) FROM gast_refunds) || '|' || "
    "(SELECT COUNT(*) || '/' || COALESCE(MAX(id), 0) FROM corrections WHERE closing_id IS NULL)")


def payrexx_waiting(store: Store) -> list[dict]:
    """T45 (Entscheid: B1): offene Kartenzahlungen sperren den Tagesabschluss – erst bestätigen oder
    abbrechen (am Handy oder hier in der Verwaltung)."""
    return store.query("SELECT p.id, p.order_id, p.amount_cents, p.device, p.created_at, o.table_label "
                       "FROM payrexx_payments p LEFT JOIN orders o ON o.id=p.order_id "
                       "WHERE p.status='waiting' AND p.test=0 ORDER BY p.id")


def payrexx_late_open(store: Store) -> list[dict]:
    """T46 (Entscheid: C): Kartenzahlungen, nach dem Abschluss bestätigt, zu einer bar abgeschlossenen
    Bestellung – die Seite Tagesabschluss fragt, ob der Gast bar bezahlt hat. Sperrt den Abschluss nicht."""
    return store.query("SELECT p.id, p.order_id, p.amount_cents, p.tip_cents, p.paid_at, o.table_label, o.closing_id "
                       "FROM payrexx_payments p JOIN orders o ON o.id=p.order_id WHERE p.late='open' ORDER BY p.id")


def apply_corrections(s: dict, rows: dict) -> None:
    """T45: Korrekturen früherer Tage in die Zahlen dieses Zeitraums – Umsatz (und MWST) um den Umsatzteil, Trinkgeld
    um den Trinkgeldteil, Payrexx um den ganzen Betrag. Die Kasse ändert sich dadurch nicht (Geld ging über Payrexx)."""
    all_cs = rows.get("corrections") or []

    def part(cs):
        return {"count": len(cs), "cents": sum(c["amount_cents"] for c in cs),
                "goods": sum(c["goods_cents"] for c in cs), "tip": sum(c["tip_cents"] for c in cs),
                "list": [{"id": c["id"], "order": c["order_id"], "at": c["created_at"], "amount": c["amount_cents"],
                          "goods": c["goods_cents"], "tip": c["tip_cents"], "why": c.get("why")} for c in cs]}
    s["corrections"] = part([c for c in all_cs if c["kind"] == "refund"])          # Rückerstattungen: minus
    s["late_payments"] = part([c for c in all_cs if c["kind"] == "late_payment"])  # spät bestätigt: plus
    delta = s["late_payments"]["cents"] - s["corrections"]["cents"]
    dtip = s["late_payments"]["tip"] - s["corrections"]["tip"]
    if "payrexx" in s and delta:
        s["payrexx"]["cents"] += delta
    if "tips" in s and dtip:
        s["tips"]["total"] += dtip
        bp = s["tips"].setdefault("by_payment", {})
        bp["payrexx"] = bp.get("payrexx", 0) + dtip


def _alloc_orders(nets: list[int], alloc: dict[str, int]) -> list[list[tuple[str, int]]]:
    """Betrag eines Tisches (pro Zahlart, Summe = Summe der Bestellungen) auf seine Bestellungen verteilen (T40): jede
    Bestellung bekommt genau ihren Betrag, jede Zahlart genau ihre Summe, kein Teil ist negativ. Der Reihe nach je den
    Anteil am noch Offenen, abgerundet; die Rappen-Reste nach grösstem Rest – nie mehr, als die Zahlart noch hat."""
    left = {k: v for k, v in alloc.items() if v > 0}
    remaining = sum(nets)
    out: list[list[tuple[str, int]]] = []
    for net in nets:
        if net <= 0 or remaining <= 0:
            out.append([])
            continue
        if net >= remaining:
            share = dict(left)
        else:
            q = {k: divmod(v * net, remaining) for k, v in left.items()}
            share = {k: a for k, (a, _) in q.items()}
            for k in sorted(q, key=lambda k: q[k][1], reverse=True)[:net - sum(share.values())]:
                share[k] += 1
        for k, v in share.items():
            left[k] -= v
        remaining -= net
        out.append([(k, v) for k, v in share.items() if v])
    return out


def _tab_split(net: int, pays: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Betrag einer Bestellung eines offenen Tisches nach den Zahlungen des Tisches aufteilen (Zahlart, Rappen).
    Summe stimmt exakt (grösster Rest); was noch nicht bezahlt ist, bleibt «none»."""
    total = sum(a for _, a in pays)
    if total <= 0 or net == 0:
        return [("none", net)]
    by: dict[str, int] = defaultdict(int)
    for k, a in pays:
        by[k] += a
    base = min(net, total) if net > 0 else net
    raw = {k: base * a / total for k, a in by.items()}
    out = {k: int(v) for k, v in raw.items()}
    rest = base - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:abs(rest)]:
        out[k] += 1 if rest > 0 else -1
    res = [(k, v) for k, v in out.items() if v]
    if net > base:
        res.append(("none", net - base))
    return res or [("none", 0)]


def _ranges(ids: list[int]) -> list[str]:
    out: list[str] = []
    for i in sorted(ids):
        if out and isinstance(out[-1], list) and out[-1][1] == i - 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [f"{a}" if a == b else f"{a}–{b}" for a, b in out]


CORRECTION_DEVICE = "\0korrektur"                       # Schlüssel der Zeile «Rückerstattungen früherer Tage»


def summarize(rows: dict, test_ids: set[int] | None = None) -> dict:
    """Auswertung eines Zeitraums (reine Rechnung, keine Datenbank): Summen, pro Zahlart, Handy, Produkt,
    MWST-Satz, dazu die Prüfung der Prüfsummen und Lücken in der Nummernfolge."""
    orders, items, cancels, citems = rows["orders"], rows["items"], rows["cancels"], rows["citems"]
    items_of: dict[int, list[dict]] = defaultdict(list)
    for i in items:
        items_of[i["order_id"]].append(i)
    storno_of: dict[int, int] = defaultdict(int)
    for c in cancels:
        storno_of[c["order_id"]] += c["amount_cents"]
    lines_of: dict[int, list[dict]] = defaultdict(list)
    for ci in citems:
        lines_of[ci["cancellation_id"]].append(ci)

    gross = sum(o["total_cents"] for o in orders)
    storno = sum(c["amount_cents"] for c in cancels)
    by_payment: dict[str, int] = defaultdict(int)
    dev: dict[str, dict] = {}
    # offene Tische: Betrag des Tisches nach seinen Zahlungen aufteilen (pro Zahlart), dann auf seine Bestellungen
    tab_pays: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for p in rows.get("tab_pay") or []:
        tab_pays[p["tab_id"]].append((p["payment"] if p.get("payment") in lb.PAYMENTS else "none", p["amount_cents"]))
    tab_orders: dict[int, list[dict]] = defaultdict(list)
    for o in orders:
        if o.get("tab_id"):
            tab_orders[o["tab_id"]].append(o)
    parts_of: dict[int, list[tuple[str, int]]] = {}
    for tid, olist in tab_orders.items():
        nets = [o["total_cents"] - storno_of[o["id"]] for o in olist]
        alloc = dict(_tab_split(sum(nets), tab_pays.get(tid, [])))
        for o, parts in zip(olist, _alloc_orders(nets, alloc)):
            parts_of[o["id"]] = parts
    # Teilzahlungen einer sofort kassierten Bestellung: der Betrag nach ihren Zahlarten (ohne eigene Zahlart: wie die
    # Bestellung); was nicht über Teilzahlungen bezahlt ist, zählt wie bisher zur Zahlart der Bestellung
    order_pays: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for p in rows.get("order_pay") or []:
        order_pays[p["order_id"]].append((p.get("payment"), p["amount_cents"]))
    for o in orders:
        if o.get("tab_id") or o["id"] not in order_pays:
            continue
        own = o["payment"] if o.get("payment") in lb.PAYMENTS else "none"
        pays = [(k if k in lb.PAYMENTS else own, a) for k, a in order_pays[o["id"]]]
        split = _tab_split(o["total_cents"] - storno_of[o["id"]], pays)
        merged: dict[str, int] = defaultdict(int)
        for k, v in split:
            merged[own if k == "none" else k] += v
        parts_of[o["id"]] = [(k, v) for k, v in merged.items() if v] or [(own, 0)]
    for o in orders:
        net = o["total_cents"] - storno_of[o["id"]]
        parts = parts_of.get(o["id"]) if o["id"] in parts_of else \
            [(o["payment"] if o.get("payment") in lb.PAYMENTS else "none", net)]
        who = lb.device_label(o.get("device_cert"), o.get("device")) or "?"       # «XCover101 (Anna)»
        d = dev.setdefault(who, {"device": who, "orders": 0, "gross": 0,
                                                     "storno": 0, "net": 0, "by_payment": defaultdict(int)})
        d["orders"] += 1
        d["gross"] += o["total_cents"]
        d["storno"] += storno_of[o["id"]]
        d["net"] += net
        for pay, cents in parts:
            by_payment[pay] += cents
            d["by_payment"][pay] += cents

    prod: dict[tuple, dict] = {}
    vat: dict[tuple, int] = defaultdict(int)
    for i in items:
        p = prod.setdefault((i["name"], i["price_cents"]), {"name": i["name"], "price": i["price_cents"], "qty": 0,
                                                            "storno_qty": 0, "amount": 0})
        p["qty"] += i["qty"]
        p["amount"] += i["price_cents"] * i["qty"]
        if i.get("vat_bp") is not None:
            vat[(i.get("vat_code") or "normal", i["vat_bp"])] += i["price_cents"] * i["qty"]
    for ci in citems:
        p = prod.setdefault((ci["name"], ci["price_cents"]), {"name": ci["name"], "price": ci["price_cents"],
                                                              "qty": 0, "storno_qty": 0, "amount": 0})
        p["storno_qty"] += ci["qty"]
        p["amount"] -= ci["amount_cents"]
        if ci.get("vat_bp") is not None:
            vat[(ci.get("vat_code") or "normal", ci["vat_bp"])] -= ci["amount_cents"]
    corr = rows.get("corrections") or []                # T45: Korrekturen früherer Tage
    corr_goods = sum(c["goods_cents"] for c in corr if c["kind"] == "refund")
    for c in corr:
        if c["kind"] == "late_payment":                 # Zahlart wechselt zu Payrexx, der Umsatz bleibt
            frm = (c.get("why") or "von:none").split(":", 1)[1]
            by_payment[frm if frm in lb.PAYMENTS else "none"] -= c["goods_cents"]
            by_payment["payrexx"] += c["goods_cents"]
            continue
        cv = c.get("vat")
        for code, bp, cents in (json.loads(cv) if isinstance(cv, str) else (cv or [])):
            vat[(code, bp)] -= cents
    if corr_goods:
        by_payment["payrexx"] -= corr_goods
        dev[CORRECTION_DEVICE] = {"device": t("ledger.correction.device"), "orders": 0, "gross": 0, "storno": 0,
                                  "net": -corr_goods, "by_payment": defaultdict(int, {"payrexx": -corr_goods})}
    by_vat = []
    for (code, bp), g in sorted(vat.items(), key=lambda kv: (-kv[0][1], kv[0][0])):
        v, n = lb.vat_split(g, bp)
        by_vat.append({"code": code, "bp": bp, "gross": g, "vat": v, "net": n})

    # Prüfung: Prüfsummen und Nummernfolge
    check = {"ok": 0, "bad": [], "other_key": 0, "missing": 0}
    for o in orders:
        r = lb.check_digest(o.get("digest"), lb.order_body(o, items_of[o["id"]]))
        if r == "bad":
            check["bad"].append(f"#{o['id']:04d}")
        else:
            check[r] += 1
    for c in cancels:
        body = lb.cancel_body(c, [{"line_no": x["line_no"], "qty": x["qty"], "amount_cents": x["amount_cents"]}
                                  for x in lines_of[c["id"]]])
        r = lb.check_digest(c.get("digest"), body)
        if r == "bad":
            check["bad"].append(f"S{c['id']}")
        else:
            check[r] += 1
    # Quittungen: Anzahl, Nummern, Kopien, Prüfsummen, Lücken in der Nummernfolge
    recs = rows.get("receipts") or []
    rnos = [r["no"] for r in recs if r.get("no")]
    rbad = []
    for r in recs:
        res = lb.check_digest(r.get("digest"), lb.receipt_body(r))
        if res == "bad":
            rbad.append(f"Q{r['no'] or r['id']}")
        else:
            check[res] += 1
    check["bad"] += rbad
    receipt_info = {"count": len(recs), "first": min(rnos) if rnos else None, "last": max(rnos) if rnos else None,
                    "amount": sum(r["total_cents"] for r in recs), "copies": len(rows.get("rcopies") or []),
                    "gaps": _ranges([n for n in range(min(rnos), max(rnos) + 1) if n not in set(rnos)]) if rnos else [],
                    "list": [{"no": r["no"], "order": r["order_id"], "at": r["created_at"], "part": r.get("part"),
                              "amount": r["total_cents"]} for r in recs]}
    ids = [o["id"] for o in orders]
    gaps: list[str] = []
    if ids:
        present = set(ids) | set(test_ids or ())
        gaps = _ranges([i for i in range(min(ids), max(ids) + 1) if i not in present])

    return {
        "orders": len(orders), "first_order": min(ids) if ids else None, "last_order": max(ids) if ids else None,
        "period_from": orders[0]["created_at"] if orders else None,
        "period_to": orders[-1]["created_at"] if orders else None,
        "gross": gross, "storno": storno, "net": gross - storno - corr_goods, "stornos": len(cancels),
        "by_payment": {k: by_payment.get(k, 0) for k in (*lb.PAYMENTS, "none") if k in by_payment},
        "by_device": sorted(({**d, "by_payment": dict(d["by_payment"])} for d in dev.values()),
                            key=lambda d: d["device"].lower()),
        "by_product": sorted(prod.values(), key=lambda p: (-p["amount"], p["name"].lower())),
        "by_vat": by_vat,
        "storno_list": [{"id": c["id"], "order": c["order_id"], "at": c["created_at"], "reason": c["reason"],
                         "by": c.get("by_user") or (f"device:{c['by_device']}" if c.get("by_device") else "?"),
                         "amount": c["amount_cents"]} for c in cancels],
        "check": {**check, "gaps": gaps,
                  "bad": check["bad"] + [f"K{c['id']}" for c in corr
                                         if lb.check_digest(c.get("digest"), lb.correction_body(c)) == "bad"]},
        "receipts": receipt_info,
    }


def open_summary(store: Store) -> dict:
    rows = _open_rows(store)
    s = summarize(rows, _test_ids(store, rows))
    from . import payrexx as payrexx_mod
    s["payrexx"] = payrexx_mod.period(store, [o["id"] for o in rows["orders"]])    # T13
    s["tips"] = tips.period(store, rows["orders"])                                  # Trinkgeld (app/tips.py)
    apply_corrections(s, rows)                                                      # T45: frühere Tage
    s["tips_enabled"] = tips.enabled(store)
    s["payrexx_waiting"] = payrexx_waiting(store)                                   # T45: sperrt den Abschluss
    s["payrexx_late"] = payrexx_late_open(store)                                    # T46: zu entscheiden
    s["max_cancel"] = max((c["id"] for c in rows["cancels"]), default=0)
    s["receipts_enabled"] = receipts.enabled(store)
    from . import tabs as tabs_mod
    s["open_tabs"] = tabs_mod.count_open(store)                                     # offene Tische: Abschluss gesperrt
    return s


def _test_ids(store: Store, rows: dict) -> set[int]:
    ids = [o["id"] for o in rows["orders"]]
    if not ids:
        return set()
    return {r["id"] for r in store.query("SELECT id FROM orders WHERE id BETWEEN ? AND ? AND (test=1 OR closing_id "
                                          "IS NOT NULL)", (min(ids), max(ids)))}


def cash_check(net: int, *, float_cents: int | None, counted: int | None, card: int | None, twint: int | None,
               other: int | None, payrexx: int = 0, tip: int = 0, tip_out: int | None = None) -> tuple[int, int | None]:
    """Bargeld laut Kasse = Wechselgeld + Umsatz + Trinkgeld − Karte − TWINT − Andere − Payrexx (T13, genau bekannt)
    − Trinkgeld ausbezahlt. Karte, TWINT und Payrexx enthalten ihr Trinkgeld (wie auf deren Abrechnung), darum zählt
    hier das ganze Trinkgeld. Differenz = gezählt − erwartet."""
    expected = ((float_cents or 0) + net + (tip or 0) - (card or 0) - (twint or 0) - (other or 0) - (payrexx or 0)
                - (tip_out or 0))
    return expected, (None if counted is None else counted - expected)


# -- CSV / Tagesabschluss ---------------------------------------------------------------------------------
CSV_HEADER = ["typ", "nummer", "zeit", "tisch", "geraet_benutzer", "zahlart", "position", "produkt", "optionen",
              "menge", "einzelpreis", "betrag", "mwst_satz", "grund", "pruefsumme"]


def period_csv(rows: dict, tip_list: list[dict] | None = None) -> bytes:
    """Alle Bestellungen und Stornos des Zeitraums, Stornos als Gegenbuchung (negative Menge und Betrag), am Schluss
    das Trinkgeld (typ «trinkgeld», kein Umsatz, ohne MWST)."""
    items_of: dict[int, list[dict]] = defaultdict(list)
    for i in rows["items"]:
        items_of[i["order_id"]].append(i)
    lines_of: dict[int, list[dict]] = defaultdict(list)
    for ci in rows["citems"]:
        lines_of[ci["cancellation_id"]].append(ci)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    bp = lambda v: "" if v is None else f"{v / 100:.2f}"            # noqa: E731
    for o in rows["orders"]:
        for i in items_of[o["id"]]:
            w.writerow([csv_cell(x) for x in (
                "bestellung", o["id"], o["created_at"].replace("T", " "), o["table_label"],
                lb.device_label(o.get("device_cert"), o.get("device")),
                o.get("payment") or "", i.get("line_no") or "", i["name"],
                ", ".join(x for x in (i.get("options"), i.get("note"), i.get("delivery")) if x), i["qty"],
                money(i["price_cents"]), money(i["price_cents"] * i["qty"]), bp(i.get("vat_bp")), "",
                o.get("digest") or "")])
    for c in rows["cancels"]:
        for ci in lines_of[c["id"]]:
            w.writerow([csv_cell(x) for x in (
                "storno", c["order_id"], c["created_at"].replace("T", " "), "",
                c.get("by_user") or c.get("by_device") or "", "", ci["line_no"], ci["name"], "", -ci["qty"],
                money(ci["price_cents"]), money(-ci["amount_cents"]), bp(ci.get("vat_bp")), c["reason"],
                c.get("digest") or "")])
    for p in rows.get("tab_pay") or []:              # offene Tische: jede Zahlung (Betrag an die Rechnung)
        w.writerow([csv_cell(v) for v in (
            "tischzahlung", f"T{p['tab_id']}", p["created_at"].replace("T", " "), p.get("table_label") or "",
            p.get("by_user") or p.get("by_device") or "", p.get("payment") or "", "", "", "", "", "",
            money(p["amount_cents"]), "", "", "")])
    for x in tip_list or []:
        w.writerow([csv_cell(v) for v in (
            "trinkgeld", x["order"], x["at"].replace("T", " "), x["table"], x["device"], x["payment"], "", "", "", "",
            "", money(x["amount"]), "", "", "")])
    for c in rows.get("corrections") or []:          # T45: Rückerstattung früherer Tage (Umsatz und Trinkgeld minus)
        if c["kind"] == "late_payment":              # Kartenzahlung früherer Tage, nachträglich bestätigt
            w.writerow([csv_cell(v) for v in (
                "nachzahlung", c["order_id"], c["created_at"].replace("T", " "), "", "", "payrexx", "", "", "", "", "",
                money(c["amount_cents"]), "", c.get("why") or "", c.get("digest") or "")])
            continue
        cv = json.loads(c.get("vat") or "[]")
        for _code, bp_, cents in cv or [[None, None, c["goods_cents"]]]:
            if cents:
                w.writerow([csv_cell(v) for v in (
                    "korrektur", c["order_id"], c["created_at"].replace("T", " "), "", c.get("by_user") or "",
                    "payrexx", "", "", "", "", "", money(-cents), bp(bp_), c.get("why") or "", c.get("digest") or "")])
        if c["tip_cents"]:
            w.writerow([csv_cell(v) for v in (
                "korrektur_trinkgeld", c["order_id"], c["created_at"].replace("T", " "), "", c.get("by_user") or "",
                "payrexx", "", "", "", "", "", money(-c["tip_cents"]), "", c.get("why") or "", "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _pack(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data, mtime=0)).decode()


def _unpack(text: str) -> bytes:
    return gzip.decompress(base64.b64decode(text))


_CLOSING_FIELDS = ("id", "created_at", "by_user", "period_from", "period_to", "first_order", "last_order", "orders",
                   "gross_cents", "storno_cents", "net_cents", "float_cents", "counted_cents", "card_cents",
                   "twint_cents", "other_cents", "expected_cents", "difference_cents", "note")


CHAIN_V2 = "h2:"          # Kette mit Schlüssel (HMAC); ältere Abschlüsse haben reines SHA-256 (64 Zeichen)


def chain_hash(record: dict, summary_json: str, csv_bytes: bytes, prev_hash: str | None, legacy: bool = False) -> str:
    """Prüfsumme des Abschlusses: über seine Zahlen, die Auswertung, die CSV und den vorherigen Abschluss.
    Mit dem Kassenbuch-Schlüssel (HMAC, «h2:Kennung:…»): wer die Datenbank ersetzen kann (z.B. eine veränderte
    Sicherung einspielt), kann einen Abschluss samt Kette nicht unbemerkt neu rechnen. legacy: wie vor 2026-10."""
    body = {k: record.get(k) for k in _CLOSING_FIELDS}
    data = ((prev_hash or "").encode() + b"\0" + lb.canon(body) + b"\0" + summary_json.encode() + b"\0" +
            hashlib.sha256(csv_bytes).hexdigest().encode())
    if legacy:
        return hashlib.sha256(data).hexdigest()
    return CHAIN_V2 + lb.keyed_hash(b"festpos-closing\0" + data)


CHAIN_HEAD = "closing_head"   # Einstellung: «Schlüssel-Kennung:HMAC» über Nummer und Prüfsumme des letzten Abschlusses


def chain_head(cid: int, chain: str) -> str:
    return lb.keyed_hash(f"festpos-closing-head\0{int(cid)}\0{chain}".encode())


def check_chain_hash(r: dict, csv_bytes: bytes, keyed_before: bool = False) -> str:
    """ok | legacy (alte Art ohne Schlüssel, stimmt) | other_key (mit einem früheren Schlüssel, nicht prüfbar) | bad.
    keyed_before: ein früherer Abschluss hat schon die Art mit Schlüssel – dann kann dieser nicht von einer alten
    Version stammen (sonst liesse sich ein Abschluss einfach ohne Schlüssel neu rechnen)."""
    stored = r["chain_hash"] or ""
    if stored.startswith(CHAIN_V2):
        if stored[len(CHAIN_V2):].partition(":")[0] != lb.key_id():
            return "other_key"
        good = chain_hash(r, r["summary"], csv_bytes, r["prev_hash"])
        return "ok" if hmac.compare_digest(good, stored) else "bad"
    if keyed_before:
        return "bad"
    return "legacy" if chain_hash(r, r["summary"], csv_bytes, r["prev_hash"], legacy=True) == stored else "bad"


class ClosingIn(BaseModel):
    float_cents: int | None = Field(default=None, ge=0, le=100_000_000)      # Wechselgeld zu Beginn
    counted_cents: int | None = Field(default=None, ge=0, le=100_000_000)    # gezähltes Bargeld
    card_cents: int | None = Field(default=None, ge=0, le=100_000_000)       # Karte laut SumUp-Abrechnung
    twint_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    other_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    tip_out_cents: int | None = Field(default=None, ge=0, le=100_000_000)    # Trinkgeld bar ans Personal ausbezahlt
    note: str = Field(default="", max_length=300)


STORNO_MEANWHILE = "NOT NULL constraint failed: closings.created_at"   # siehe create_closing()


def create_closing(store: Store, actor: str, c: ClosingIn, _attempt: int = 0) -> dict:
    from . import tabs as tabs_mod
    n_open = tabs_mod.count_open(store)
    if n_open:                                         # offene Tische zuerst abrechnen oder ausbuchen (app/tabs.py)
        raise LedgerError(t("err.tab.closing_open", count=n_open), 409)
    waiting = payrexx_waiting(store)
    if waiting:                                        # T45/B1: offene Kartenzahlung zuerst bestätigen oder abbrechen
        raise LedgerError(t("err.ledger.payrexx_waiting", count=len(waiting)), 409)
    max_tip = tips.max_id(store)                       # vor dem Lesen: kommt danach ein Trinkgeld, wird neu gerechnet
    fingerprint = store.scalar(f"SELECT {FINGERPRINT_SQL}")                     # T45: ebenso Zahlungen, Korrekturen
    rows = _open_rows(store)
    if not rows["orders"] and not rows["cancels"] and not rows["corrections"]:
        raise LedgerError(t("err.ledger.nothing_to_close"), 409)
    s = summarize(rows, _test_ids(store, rows))
    from . import payrexx as payrexx_mod
    s["payrexx"] = payrexx_mod.period(store, [o["id"] for o in rows["orders"]])    # T13: eigene Zeile
    tip = tips.period(store, rows["orders"])                                        # Trinkgeld: eigene Zeilen
    tip_list = tip.pop("list")
    s["tips"] = {**tip, "paid_out": c.tip_out_cents}
    apply_corrections(s, rows)                                                      # T45: frühere Tage
    tip = s["tips"]
    expected, diff = cash_check(s["net"], float_cents=c.float_cents, counted=c.counted_cents, card=c.card_cents,
                                twint=c.twint_cents, other=c.other_cents, payrexx=s["payrexx"]["cents"],
                                tip=tip["total"], tip_out=c.tip_out_cents)
    last = store.query_one("SELECT id, chain_hash FROM closings ORDER BY id DESC LIMIT 1")
    no = (last["id"] if last else 0) + 1
    rec = {"id": no, "created_at": now_iso(), "by_user": actor, "period_from": s["period_from"],
           "period_to": s["period_to"], "first_order": s["first_order"], "last_order": s["last_order"],
           "orders": s["orders"], "gross_cents": s["gross"], "storno_cents": s["storno"], "net_cents": s["net"],
           "float_cents": c.float_cents, "counted_cents": c.counted_cents, "card_cents": c.card_cents,
           "twint_cents": c.twint_cents, "other_cents": c.other_cents, "expected_cents": expected,
           "difference_cents": diff, "note": " ".join(c.note.split()) or None}
    s["vat_enabled"] = lb.vat_enabled(store)
    s["vat_number"] = store.setting("vat_number", "") or None
    s["event_name"] = store.setting("event_name", "")
    s["currency"] = store.setting("currency", "CHF")
    summary_json = json.dumps(s, ensure_ascii=False, sort_keys=True)
    csv_bytes = period_csv(rows, tip_list)
    prev = last["chain_hash"] if last else None
    h = chain_hash(rec, summary_json, csv_bytes, prev)
    last_order = s["last_order"] or 0
    max_cancel = max((x["id"] for x in rows["cancels"]), default=0)
    max_receipt = max((x["id"] for x in rows["receipts"]), default=0)
    max_rcopy = max((x["id"] for x in rows["rcopies"]), default=0)
    cols = list(rec) + ["summary", "orders_csv", "prev_hash", "chain_hash"]
    vals = list(rec.values()) + [summary_json, _pack(csv_bytes), prev, h]
    # Kam während der Berechnung ein Storno zu einer der Bestellungen dazu, fehlte es in den Summen, die
    # Bestellung wäre aber abgeschlossen: dann bleibt created_at leer, der Batch scheitert und es wird neu gerechnet
    marks = ["?"] * len(cols)
    at = cols.index("created_at")
    marks[at] = ("CASE WHEN EXISTS(SELECT 1 FROM cancellations x JOIN orders o ON o.id=x.order_id WHERE "
                 "x.closing_id IS NULL AND x.id>? AND o.closing_id IS NULL AND o.test=0 AND o.id<=?) "
                 "OR EXISTS(SELECT 1 FROM tips x JOIN orders o ON o.id=x.order_id WHERE x.id>? AND "
                 "o.closing_id IS NULL AND o.test=0 AND o.id<=?) "
                 # T40: inzwischen ein Tisch eröffnet (Prüfung oben ist vorbei) -> neu rechnen, dann «offene Tische»
                 "OR EXISTS(SELECT 1 FROM orders o JOIN tabs tb ON tb.id=o.tab_id WHERE o.closing_id IS NULL AND "
                 "o.test=0 AND o.id<=? AND tb.closed_at IS NULL) "
                 # T45: Zahlungen, Zahlarten, Rückerstattungen oder Korrekturen geändert, oder eine Kartenzahlung offen
                 f"OR ({FINGERPRINT_SQL}) <> ? "
                 "OR EXISTS(SELECT 1 FROM payrexx_payments WHERE status='waiting' AND test=0) THEN NULL ELSE ? END")
    vals[at:at] = [max_cancel, last_order, max_tip, last_order, last_order, fingerprint]
    max_corr = max((x["id"] for x in rows["corrections"]), default=0)
    stmts = [(f"INSERT INTO closings({', '.join(cols)}) VALUES ({', '.join(marks)})", tuple(vals)),
             ("UPDATE orders SET closing_id=? WHERE closing_id IS NULL AND test=0 AND id<=?", (no, last_order)),
             ("UPDATE cancellations SET closing_id=? WHERE closing_id IS NULL AND id<=? AND order_id IN "
              "(SELECT id FROM orders WHERE test=0)", (no, max_cancel)),
             ("UPDATE receipts SET closing_id=? WHERE closing_id IS NULL AND test=0 AND id<=?", (no, max_receipt)),
             ("UPDATE receipt_copies SET closing_id=? WHERE closing_id IS NULL AND id<=? AND receipt_id IN "
              "(SELECT id FROM receipts WHERE test=0)", (no, max_rcopy)),
             ("UPDATE corrections SET closing_id=? WHERE closing_id IS NULL AND id<=?", (no, max_corr)),   # T45
             # T45: diese Kartenzahlungen sind in diesem Abschluss gezählt – nie später nochmals «früherer Tage»
             ("UPDATE payrexx_payments SET closing_id=? WHERE closing_id IS NULL AND status='confirmed' AND test=0 "
              "AND order_id IN (SELECT id FROM orders WHERE closing_id=?)", (no, no)),
             # … und nachträglich bestätigte (Korrektur late_payment, eben in diesem Abschluss gebucht)
             ("UPDATE payrexx_payments SET closing_id=? WHERE closing_id IS NULL AND EXISTS (SELECT 1 FROM corrections c "
              "WHERE c.kind='late_payment' AND c.key = 'late:' || payrexx_payments.id AND c.closing_id=?)", (no, no)),
             # Ende der Kette mit Schlüssel: auch der letzte Abschluss lässt sich nicht ohne Schlüssel neu rechnen
             ("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (CHAIN_HEAD, chain_head(no, h))),
             lb.audit_stmt(actor, "closing", closing=no, orders=s["orders"], net=s["net"], difference=diff)]
    try:
        store.batch(stmts, transaction=True)
    except StoreError as e:
        if STORNO_MEANWHILE in str(e):
            if _attempt < 3:
                return create_closing(store, actor, c, _attempt + 1)
            raise LedgerError(t("err.ledger.closing_busy"), 409) from None
        if e.is_unique_violation:
            raise LedgerError(t("err.ledger.closing_busy"), 409) from None
        raise
    log.info("Tagesabschluss %d: %d Bestellungen, netto %s", no, s["orders"], money(s["net"]))
    return closing_public(store.query_one("SELECT * FROM closings WHERE id=?", (no,)))


def closing_public(r: dict) -> dict:
    out = {k: r.get(k) for k in _CLOSING_FIELDS}
    out["chain_hash"] = r["chain_hash"]
    out["summary"] = json.loads(r["summary"])
    return out


def verify_closing(store: Store, cid: int) -> dict:
    """Prüfsumme neu rechnen (aus den gespeicherten Zahlen, der Auswertung und der CSV) und die Kette prüfen."""
    r = store.query_one("SELECT * FROM closings WHERE id=?", (cid,))
    if not r:
        raise LedgerError(t("err.ledger.closing_unknown"), 404)
    prev = store.query_one("SELECT chain_hash FROM closings WHERE id<? ORDER BY id DESC LIMIT 1", (cid,))
    try:
        csv_bytes = _unpack(r["orders_csv"])
    except (ValueError, OSError):
        return {"ok": False, "hash_ok": False, "chain_ok": False, "state": "bad"}
    keyed_before = bool(store.scalar("SELECT 1 FROM closings WHERE id<? AND chain_hash LIKE ? LIMIT 1",
                                     (cid, CHAIN_V2 + "%")))
    state = check_chain_hash(r, csv_bytes, keyed_before)
    head = store.setting(CHAIN_HEAD, "")
    last = store.scalar("SELECT MAX(id) FROM closings")
    if head and cid == last and state in ("ok", "legacy"):          # der letzte: passt er zum Ende der Kette?
        if head.partition(":")[0] != lb.key_id():
            state = "other_key"
        elif not hmac.compare_digest(head, chain_head(cid, r["chain_hash"])):
            state = "bad"
    hash_ok = state in ("ok", "legacy")
    chain_ok = (r["prev_hash"] or None) == (prev["chain_hash"] if prev else None)
    return {"ok": hash_ok and chain_ok, "hash_ok": hash_ok, "chain_ok": chain_ok, "state": state,
            "chain_hash": r["chain_hash"]}


# -- PDF des Abschlusses ----------------------------------------------------------------------------------
PAY_KEYS = {"cash": "ledger.pay.cash", "card": "ledger.pay.card", "twint": "ledger.pay.twint",
            "other": "ledger.pay.other", "payrexx": "ledger.pay.payrexx", "none": "ledger.pay.none"}
VAT_KEYS = {"normal": "ledger.vat.normal", "reduced": "ledger.vat.reduced", "lodging": "ledger.vat.lodging"}


def _who(v: str | None) -> str:
    if v and v.startswith("device:"):
        return t("ledger.who.device", name=v[7:])
    return v or "–"


def _dt(v: str | None) -> str:
    return (v or "–").replace("T", " ")[:16]


def closing_pdf(r: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    s = json.loads(r["summary"])
    cur = s.get("currency") or "CHF"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(t("ledger.pdf.title", no=r["id"]))
    W, H = A4
    left, right = 18 * mm, W - 18 * mm
    y = [H - 20 * mm]

    def need(h: float) -> None:
        if y[0] - h < 18 * mm:
            c.showPage()
            y[0] = H - 20 * mm

    def text(s_: str, *, size: float = 10, bold: bool = False, x: float | None = None, gap: float = 1.35) -> None:
        need(size * gap)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(left if x is None else x, y[0], s_)
        y[0] -= size * gap

    def row(cols: list[tuple[str, float, str]], *, bold: bool = False, size: float = 9.5) -> None:
        """cols: (Text, x-Position, l|r)"""
        need(size * 1.4)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        for txt, x, al in cols:
            (c.drawRightString if al == "r" else c.drawString)(x, y[0], txt)
        y[0] -= size * 1.4

    def rule() -> None:
        need(6)
        c.setLineWidth(0.5)
        c.line(left, y[0] + 3, right, y[0] + 3)
        y[0] -= 4

    text(t("ledger.pdf.title", no=r["id"]), size=16, bold=True, gap=1.5)
    text(s.get("event_name") or "", size=11)
    if s.get("vat_enabled") and s.get("vat_number"):
        text(t("ledger.pdf.vat_number", number=s["vat_number"]), size=9.5)
    y[0] -= 4
    for k, v in ((t("ledger.pdf.created"), f"{_dt(r['created_at'])} – {_who(r['by_user'])}"),
                 (t("ledger.pdf.period"), f"{_dt(r['period_from'])} – {_dt(r['period_to'])}"),
                 (t("ledger.pdf.orders"), t("ledger.pdf.orders_range", count=r["orders"], first=r["first_order"] or "–",
                                           last=r["last_order"] or "–"))):
        row([(k, left, "l"), (v, left + 45 * mm, "l")])
    y[0] -= 4
    text(t("ledger.pdf.turnover"), size=12, bold=True)
    for k, v, b in ((t("ledger.pdf.gross"), r["gross_cents"], False), (t("ledger.pdf.storno"), -r["storno_cents"], False),
                    (t("ledger.pdf.net"), r["net_cents"], True)):
        row([(k, left, "l"), (f"{cur} {money(v)}", left + 110 * mm, "r")], bold=b)
    y[0] -= 4
    text(t("ledger.pdf.by_payment"), size=12, bold=True)
    for k, v in s["by_payment"].items():
        row([(t(PAY_KEYS.get(k, "ledger.pay.none")), left, "l"), (f"{cur} {money(v)}", left + 110 * mm, "r")])
    y[0] -= 4
    cr = s.get("corrections") or {}
    if (s.get("payrexx") or {}).get("count") or cr.get("count") or (s.get("late_payments") or {}).get("count"):
        row([(t("ledger.pdf.payrexx", count=s["payrexx"]["count"]), left, "l"),
             (f"{cur} {money(s['payrexx']['cents'])}", left + 110 * mm, "r")])
    pm = (s.get("payrexx") or {}).get("manual") or {}
    if pm.get("count"):                                       # F1: von Hand bestätigt – mit Payrexx vergleichen
        row([(t("ledger.pdf.payrexx_manual", count=pm["count"]), left + 4 * mm, "l"),
             (f"{cur} {money(pm['cents'])}", left + 110 * mm, "r")], bold=True, size=8.5)
    lp = s.get("late_payments") or {}
    if lp.get("count"):                                       # T45: Kartenzahlungen früherer Tage (im Betrag oben)
        row([(t("ledger.pdf.late_payments", count=lp["count"], tip=money(lp["tip"])), left, "l"),
             (f"{cur} +{money(lp['cents'])}", left + 110 * mm, "r")])
    if cr.get("count"):                                       # T45: Rückerstattungen früherer Tage (im Betrag oben)
        row([(t("ledger.pdf.corrections", count=cr["count"], tip=money(cr["tip"])), left, "l"),
             (f"{cur} −{money(cr['cents'])}", left + 110 * mm, "r")])
        for x in cr.get("list") or []:
            row([(t("ledger.pdf.correction_line", order=x["order"], at=(x["at"] or "").replace("T", " ")[:16],
                    why=x.get("why") or ""), left + 4 * mm, "l"), (f"−{money(x['amount'])}", left + 110 * mm, "r")])
    tp = s.get("tips") or {}
    if tp.get("total") or tp.get("paid_out"):               # Trinkgeld: kein Umsatz, keine MWST
        y[0] -= 4
        text(t("ledger.pdf.tips"), size=12, bold=True)
        for k, v in (tp.get("by_payment") or {}).items():
            row([(t(PAY_KEYS.get(k, "ledger.pay.none")), left, "l"), (f"{cur} {money(v)}", left + 110 * mm, "r")])
        row([(t("ledger.pdf.tips_total"), left, "l"), (f"{cur} {money(tp.get('total'))}", left + 110 * mm, "r")],
            bold=True)
        for d in tp.get("by_device") or []:
            row([(t("ledger.pdf.tips_device", device=d["device"][:40]), left, "l"),
                 (money(d["amount"]), left + 110 * mm, "r")], size=8.5)
        text(t("ledger.pdf.tips_hint"), size=8.5)
    y[0] -= 4
    text(t("ledger.pdf.cash_check"), size=12, bold=True)
    tip_in = ((t("ledger.pdf.tips_total"), tp["total"]),) if tp.get("total") else ()
    tip_out = ((t("ledger.pdf.tip_out"), tp["paid_out"]),) if tp.get("paid_out") is not None else ()
    for k, v in ((t("ledger.pdf.float"), r["float_cents"]), *tip_in, (t("ledger.pdf.card"), r["card_cents"]),
                 (t("ledger.pdf.twint"), r["twint_cents"]), (t("ledger.pdf.other"), r["other_cents"]), *tip_out,
                 (t("ledger.pdf.expected"), r["expected_cents"]), (t("ledger.pdf.counted"), r["counted_cents"]),
                 (t("ledger.pdf.difference"), r["difference_cents"])):
        row([(k, left, "l"), ("–" if v is None else f"{cur} {money(v)}", left + 110 * mm, "r")],
            bold=k == t("ledger.pdf.difference"))
    if r.get("note"):
        text(t("ledger.pdf.note", note=r["note"]), size=9.5)
    if s.get("by_vat"):
        y[0] -= 4
        text(t("ledger.pdf.vat"), size=12, bold=True)
        row([(t("ledger.pdf.vat_rate"), left, "l"), (t("ledger.pdf.vat_gross"), left + 80 * mm, "r"),
             (t("ledger.pdf.vat_amount"), left + 115 * mm, "r"), (t("ledger.pdf.vat_net"), left + 150 * mm, "r")],
            bold=True)
        for v in s["by_vat"]:
            row([(f"{t(VAT_KEYS.get(v['code'], 'ledger.vat.normal'))} {v['bp'] / 100:.1f} %", left, "l"),
                 (money(v["gross"]), left + 80 * mm, "r"), (money(v["vat"]), left + 115 * mm, "r"),
                 (money(v["net"]), left + 150 * mm, "r")])
    y[0] -= 4
    text(t("ledger.pdf.by_device"), size=12, bold=True)
    row([(t("ledger.pdf.device"), left, "l"), (t("ledger.pdf.count"), left + 70 * mm, "r"),
         (t("ledger.pdf.storno"), left + 110 * mm, "r"), (t("ledger.pdf.net"), left + 150 * mm, "r")], bold=True)
    for d in s["by_device"]:
        row([(d["device"][:40], left, "l"), (str(d["orders"]), left + 70 * mm, "r"),
             (money(d["storno"]), left + 110 * mm, "r"), (money(d["net"]), left + 150 * mm, "r")])
    if s.get("storno_list"):
        y[0] -= 4
        text(t("ledger.pdf.stornos"), size=12, bold=True)
        for x in s["storno_list"]:
            row([(f"#{x['order']:04d}", left, "l"), (_dt(x["at"])[11:], left + 18 * mm, "l"),
                 (_who(x["by"])[:22], left + 32 * mm, "l"), (x["reason"][:48], left + 72 * mm, "l"),
                 (money(-x["amount"]), right, "r")], size=8.5)
    y[0] -= 4
    text(t("ledger.pdf.by_product"), size=12, bold=True)
    row([(t("ledger.pdf.product"), left, "l"), (t("ledger.pdf.qty"), left + 95 * mm, "r"),
         (t("ledger.pdf.price"), left + 125 * mm, "r"), (t("ledger.pdf.amount"), left + 160 * mm, "r")], bold=True)
    for p in s["by_product"]:
        q = p["qty"] - p["storno_qty"]
        row([(p["name"][:50], left, "l"), (str(q), left + 95 * mm, "r"), (money(p["price"]), left + 125 * mm, "r"),
             (money(p["amount"]), left + 160 * mm, "r")], size=9)
    y[0] -= 4
    rc = s.get("receipts") or {}
    if rc.get("count") or rc.get("copies"):
        text(t("ledger.pdf.receipts"), size=12, bold=True)
        text(t("ledger.pdf.receipts_line", count=rc.get("count", 0), first=rc.get("first") or "–",
               last=rc.get("last") or "–", amount=money(rc.get("amount", 0)), copies=rc.get("copies", 0),
               gaps=", ".join(rc.get("gaps") or []) or "–"), size=9)
        y[0] -= 4
    text(t("ledger.pdf.check"), size=12, bold=True)
    ch = s["check"]
    text(t("ledger.pdf.check_digests", ok=ch["ok"], bad=len(ch["bad"]), other=ch["other_key"],
           missing=ch["missing"]), size=9)
    if ch["bad"]:
        text(t("ledger.pdf.check_bad", list=", ".join(ch["bad"][:30])), size=9, bold=True)
    text(t("ledger.pdf.check_gaps", list=", ".join(ch["gaps"]) or "–"), size=9)
    text(t("ledger.pdf.hash", hash=r["chain_hash"]), size=8)
    text(t("ledger.pdf.prev_hash", hash=r["prev_hash"] or "–"), size=8)
    y[0] -= 14
    need(30 * mm)
    c.setLineWidth(0.5)
    for i, k in enumerate(("ledger.pdf.sign_counted", "ledger.pdf.sign_checked")):
        x = left + i * 90 * mm
        c.line(x, y[0], x + 75 * mm, y[0])
        c.setFont("Helvetica", 8.5)
        c.drawString(x, y[0] - 11, t(k))
    c.showPage()
    c.save()
    return buf.getvalue()


# -- Handy: Zahlart und Storno ----------------------------------------------------------------------------
class PaymentIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    # nie «payrexx» vom Handy: das setzt nur der Server, wenn eine Kartenzahlung bestätigt ist (Review 2026-11, S9)
    payment: str = Field(pattern="^(cash|card|twint|other)$")


@router.post("/api/orders/payment", dependencies=[Depends(devices.phone_only)])
async def phone_payment(p: PaymentIn):
    try:
        await db(set_payment, rt.store, payment=p.payment, client_uuid=p.client_uuid)
    except LedgerError as e:
        raise _http(e) from None
    return {"ok": True}


# -- Handy: Stand der eigenen Bestellungen für den Verlauf --------------------------------------------
# Der Verlauf liegt auf dem Handy (letzte 40). Tagesabschluss oder «Bestellungen löschen» auf dem Pi kennt er nicht –
# darum fragt die App hier nach: pro Kennung nur der Stand, nie der Inhalt (die Kennungen sind zufällig, 128 Bit).
STATUS_MAX = 60
UUID_RE = r"^[A-Za-z0-9_-]{8,64}$"


class StatusIn(BaseModel):
    uuids: list[str] = Field(default_factory=list, max_length=STATUS_MAX)


def order_states(store: Store, uuids: list[str]) -> dict[str, str]:
    """-> {Kennung: open | storno (ganz storniert) | tab_done (Tisch abgerechnet) | closed (im Tagesabschluss) |
    gone (auf dem Pi nicht mehr da: gelöscht oder nie angekommen)}"""
    out = dict.fromkeys(uuids, "gone")
    if not uuids:
        return out
    ph = ",".join("?" * len(uuids))
    for r in store.query(
            f"SELECT o.client_uuid AS u, o.closing_id, o.total_cents, t.closed_at AS tab_closed, "
            f"COALESCE((SELECT SUM(c.amount_cents) FROM cancellations c WHERE c.order_id=o.id), 0) AS cancelled "
            f"FROM orders o LEFT JOIN tabs t ON t.id=o.tab_id WHERE o.client_uuid IN ({ph})", tuple(uuids)):
        out[r["u"]] = ("closed" if r["closing_id"] else "storno" if r["total_cents"] - r["cancelled"] <= 0
                       else "tab_done" if r["tab_closed"] else "open")
    return out


@router.post("/api/orders/status", dependencies=[Depends(devices.phone_only)])
async def phone_order_states(s: StatusIn):
    uuids = list(dict.fromkeys(u for u in s.uuids if re.match(UUID_RE, u)))
    return {"states": await db(order_states, rt.store, uuids)}


class TipIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    tip_cents: int = Field(ge=0, le=tips.MAX_CENTS)                      # Summe der Bestellung (ohne Payrexx)


class OrderPayIn(BaseModel):
    key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    amount_cents: int = Field(ge=1, le=10_000_000)
    payment: str | None = Field(default=None, pattern="^(cash|card|twint|other|payrexx)$")
    tip_cents: int = Field(default=0, ge=0, le=tips.MAX_CENTS)
    person: int | None = Field(default=None, ge=1, le=99)
    items: dict[str, int] | None = None


class OrderPaysIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    payments: list[OrderPayIn] = Field(default_factory=list, max_length=99)


def set_order_payments(store: Store, uuid: str, pays: list[OrderPayIn], device: str) -> dict:
    """Alle Teilzahlungen einer sofort kassierten Bestellung: neue eintragen, fehlende als zurückgenommen
    markieren – jede Änderung im Protokoll. Abgeschlossene Bestellung: 409. Zusammen höchstens das Total (gerundet)."""
    order = store.query_one("SELECT id, total_cents, closing_id, tab_id, test FROM orders WHERE client_uuid=?", (uuid,))
    if not order:
        raise LedgerError(t("err.ledger.order_unknown"), 404)
    if order["tab_id"]:
        raise LedgerError(t("err.ledger.order_tab"), 409)                # offener Tisch: über /api/tabs/…/pay
    have = {r["key"]: r for r in store.query("SELECT key, amount_cents, payment, undone_at FROM order_payments "
                                              "WHERE order_id=?", (order["id"],))}
    want = {p.key: p for p in pays}
    new = [p for k, p in want.items() if k not in have]
    gone = [k for k, r in have.items() if k not in want and r["undone_at"] is None]
    if not new and not gone:
        return {"ok": True, "changed": 0}
    if order["closing_id"]:
        raise LedgerError(t("err.ledger.closed"), 409)
    net = order["total_cents"] - int(store.scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM cancellations "
                                                  "WHERE order_id=?", (order["id"],)) or 0)
    alive = sum(p.amount_cents for k, p in want.items() if k not in have or have[k]["undone_at"] is None)
    if alive > net + 4:                                # bar auf 5 Rappen gerundet: höchstens das gerundete Total
        raise LedgerError(t("err.ledger.paid_too_much"), 422)
    ts = now_iso()
    stmts = []
    for p in new:
        stmts += [("INSERT OR IGNORE INTO order_payments(key, order_id, amount_cents, payment, tip_cents, person, items, "
                   "by_device, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (p.key, order["id"], p.amount_cents, p.payment, p.tip_cents, p.person,
                    json.dumps(p.items) if p.items else None, device, ts)),
                  lb.audit_stmt(f"device:{device}", "order_part_pay", order=order["id"], amount=p.amount_cents,
                                payment=p.payment, person=p.person)]
    for k in gone:
        stmts += [("UPDATE order_payments SET undone_at=? WHERE key=? AND undone_at IS NULL", (ts, k)),
                  lb.audit_stmt(f"device:{device}", "order_part_undo", order=order["id"],
                                amount=have[k]["amount_cents"], payment=have[k]["payment"])]
    store.batch(stmts, transaction=True)
    return {"ok": True, "changed": len(new) + len(gone)}


@router.post("/api/orders/payments", dependencies=[Depends(devices.phone_only)])
async def phone_order_payments(p: OrderPaysIn, request: Request):
    """Teilzahlungen einer Bestellung (Teilen pro Person, Einzeln, Rückgeld): jeweils alle, die am Handy gelten."""
    info = devices.client_cert(request)
    device = lb.device_label(info["name"] if info else None, _app_name(request)) or "?"
    try:
        return await db(set_order_payments, rt.store, p.client_uuid, p.payments, device)
    except LedgerError as e:
        raise _http(e) from None


@router.post("/api/orders/tip", dependencies=[Depends(devices.phone_only)])
async def phone_tip(p: TipIn, request: Request):
    """Trinkgeld einer Bestellung (bar, Karte, TWINT – mit Payrexx steht es bei der Zahlung). Ersetzt den alten Wert."""
    info = devices.client_cert(request)
    res = await db(tips.set_tip, rt.store, client_uuid=p.client_uuid, amount=p.tip_cents,
                   device=lb.device_label(info["name"] if info else None, _app_name(request)))
    if res == "unknown":
        raise HTTPException(404, t("err.ledger.order_unknown"))
    if res == "closed":
        raise HTTPException(409, t("err.ledger.closed"))
    return {"ok": True}


class StornoLine(BaseModel):
    line_no: int = Field(ge=1, le=10000)
    qty: int = Field(ge=1, le=999)


class PhoneStornoIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    order_uuid: str = Field(min_length=8, max_length=64)
    lines: list[StornoLine] | None = Field(default=None, max_length=200)     # None = alles
    reason: str = Field(pattern="^(" + "|".join(lb.REASONS) + ")$")
    note: str = Field(default="", max_length=80)


def _app_name(request: Request) -> str:
    """Name, den die Bedienung in der App eingetragen hat (Kopf X-Festpos-Device, wie bei der Bestellung)."""
    from urllib.parse import unquote
    return " ".join(unquote(request.headers.get("X-Festpos-Device", "")).split())[:40]


async def _storno_device(request: Request) -> dict:
    """Storno am Handy nur mit der Rolle «Bedienung + Storno» oder «Festleitung» (Verwaltung › Geräte, Name aus dem
    Zertifikat)."""
    info = devices.client_cert(request)
    if not info or await db(lb.device_role, rt.store, info) not in lb.STORNO_ROLES:
        raise HTTPException(403, t("err.ledger.no_storno_right"))
    return info


@router.get("/api/orders/lines", dependencies=[Depends(devices.phone_only)])
async def phone_order_lines(request: Request, uuid: str = Query(min_length=8, max_length=64)):
    """Positionen einer Bestellung mit dem, was schon storniert ist – für das Storno-Fenster am Handy.
    Die Positionen (line_no) sortiert der Server selbst; die Reihenfolge am Handy passt nicht dazu."""
    await _storno_device(request)

    def run():
        oid = rt.store.scalar("SELECT id FROM orders WHERE client_uuid=?", (uuid,))
        return order_detail(rt.store, oid) if oid else None
    d = await db(run)
    if not d:
        raise HTTPException(404, t("err.ledger.order_unknown"))
    return {"order_id": d["id"], "closed": bool(d["closing_id"]), "test": bool(d["test"]), "payment": d["payment"],
            "total_cents": d["total_cents"], "storno_cents": d["storno_cents"], "net_cents": d["net_cents"],
            "items": [{k: i[k] for k in ("line_no", "product_id", "name", "options", "note", "price_cents", "qty", "cancelled")}
                      for i in d["items"]]}


@router.post("/api/orders/storno", dependencies=[Depends(devices.phone_only)])
async def phone_storno(s: PhoneStornoIn, request: Request):
    """Storno am Handy – nur mit der Rolle «Bedienung + Storno» (Verwaltung › Geräte)."""
    info = await _storno_device(request)
    oid = await db(rt.store.scalar, "SELECT id FROM orders WHERE client_uuid=?", (s.order_uuid,))
    if not oid:
        raise HTTPException(404, t("err.ledger.order_unknown"))
    lines = {x.line_no: x.qty for x in s.lines} if s.lines else None
    try:
        res = await db(cancel, rt.store, order_id=oid, lines=lines, reason=s.reason, note=s.note,
                       by_device=lb.device_label(info["name"], _app_name(request)), client_uuid=s.client_uuid)
    except LedgerError as e:
        raise _http(e) from None
    for pid in res["printer_ids"]:
        rt.spooler.notify(pid)
    await db(_gast_refund, oid)
    return res


def _gast_refund(order_id: int) -> None:
    """T22/T37: Storno einer Online-Bestellung – den Rückerstattungsauftrag (im Storno-Batch angelegt) gleich senden.
    Auch bei einem Duplikat: war der erste Versuch unterbrochen, geht das Geld jetzt zurück."""
    from . import gast
    gast.process_for_order(order_id)


# -- Quittung (app/receipts.py) ------------------------------------------------------------------------------
class ReceiptItem(BaseModel):
    product_id: int = Field(ge=1)
    qty: int = Field(ge=1, le=999)
    options: str | None = Field(default=None, max_length=300)


REQ_ID = r"^[A-Za-z0-9-]{8,64}$"


class ShareIn(BaseModel):
    person: int = Field(ge=1, le=99)
    persons: int | None = Field(default=None, ge=1, le=99)
    amount: int = Field(ge=1, le=10_000_000)


class PhoneReceiptIn(BaseModel):
    order_uuid: str | None = Field(default=None, min_length=8, max_length=64)
    tab_id: int | None = Field(default=None, ge=1)                          # offener Tisch: über alle Bestellungen
    payment: str | None = Field(default=None, pattern="^(cash|card|twint|other|payrexx)$")   # Tisch: Zahlart an der Kasse
    request_id: str = Field(pattern=REQ_ID)                                   # gleiche Nummer = gleiche Quittung
    items: list[ReceiptItem] | None = Field(default=None, max_length=200)   # None = ganze Bestellung (was offen ist)
    part: int | None = Field(default=None, ge=1, le=99)                      # Person bei «Einzeln»
    copy_of: int | None = Field(default=None, ge=1)                          # Kopie dieser Quittung (id)
    share: ShareIn | None = None                                             # Teilen: Anteil einer Person


async def _receipt_reply(res: dict) -> dict:
    """Drucker wecken. (Über CHF 400 mit MWST verlangt receipts.issue Name und Adresse – am Handy gar nicht.)"""
    if rt.spooler and res.get("printer_id"):
        rt.spooler.notify(res["printer_id"])
    return res


@router.post("/api/orders/receipt", dependencies=[Depends(devices.phone_only)])
async def phone_receipt(r: PhoneReceiptIn, request: Request):
    """Quittung am Handy: ganze Bestellung, die Positionen einer Person (Einzeln) oder eine Kopie."""
    info = devices.client_cert(request)
    by = lb.device_label(info["name"] if info else None, _app_name(request)) or "?"
    oid = None
    if r.tab_id:                                       # offener Tisch: nur wer ihn abrechnen darf (app/tabs.py)
        from . import tabs as tabs_mod
        tab = await db(rt.store.query_one, "SELECT * FROM tabs WHERE id=?", (r.tab_id,))
        if not tab:
            raise HTTPException(404, t("err.tab.unknown"))
        if not await db(tabs_mod.may_settle, rt.store, tab, info):
            raise HTTPException(403, t("err.tab.no_right", owner=tab.get("owner_name") or "?"))
    else:
        oid = await db(rt.store.scalar, "SELECT id FROM orders WHERE client_uuid=?", (r.order_uuid or "",))
        if not oid:
            raise HTTPException(404, t("err.receipt.order_not_yet"))
    try:
        if r.copy_of:
            src = await db(receipts.source, rt.store, order_id=oid, tab_id=r.tab_id)
            if await db(rt.store.scalar, "SELECT order_uuid FROM receipts WHERE id=?", (r.copy_of,)) != src["key"]:
                raise receipts.ReceiptError(t("err.receipt.unknown"), 404)
            res = await db(receipts.copies, rt.store, [r.copy_of], request_id=r.request_id, by_device=by)
        else:
            res = await db(receipts.issue, rt.store, order_id=oid, tab_id=r.tab_id, request_id=r.request_id, part=r.part,
                           by_device=by, payment=r.payment, share=r.share.model_dump() if r.share else None,
                           items=[i.model_dump() for i in r.items] if r.items is not None else None)
    except receipts.ReceiptError as e:
        raise HTTPException(e.status, str(e)) from None
    return await _receipt_reply(res)


class AdminReceiptIn(BaseModel):
    request_id: str = Field(pattern=REQ_ID)
    copy_of: int | None = Field(default=None, ge=1)
    recipient: str = Field(default="", max_length=300)                       # Name und Adresse (über CHF 400)


@admin_router.post("/orders/{oid}/receipt")
async def admin_receipt(oid: int, r: AdminReceiptIn, request: Request):
    """Quittung in der Verwaltung: was von der Bestellung noch nicht quittiert ist (sonst Kopien) oder eine Kopie."""
    try:
        if r.copy_of:
            order = await db(rt.store.query_one, "SELECT client_uuid, tab_id FROM orders WHERE id=?", (oid,))
            keys = await db(receipts.keys_of_order, rt.store, order) if order else []
            if await db(rt.store.scalar, "SELECT order_uuid FROM receipts WHERE id=?", (r.copy_of,)) not in keys:
                raise receipts.ReceiptError(t("err.receipt.unknown"), 404)
            res = await db(receipts.copies, rt.store, [r.copy_of], request_id=r.request_id, by_user=actor_of(request))
        else:
            res = await db(receipts.issue, rt.store, order_id=oid, request_id=r.request_id, by_user=actor_of(request),
                           recipient=r.recipient)
    except receipts.ReceiptError as e:
        raise HTTPException(e.status, str(e)) from None
    return await _receipt_reply(res)


# -- Verwaltung --------------------------------------------------------------------------------------------
@admin_router.get("/orders")
async def list_orders(scope: str = Query("open", pattern="^(open|all)$"), q: str = Query("", max_length=40),
                      limit: int = Query(300, ge=1, le=2000)):
    where = ["1=1"] if scope == "all" else ["o.closing_id IS NULL"]
    args: list = []
    if q.strip():
        qs = q.strip()
        if qs.lstrip("#").isdigit():
            where.append("(o.id=? OR o.table_label=? COLLATE NOCASE)")
            args += [int(qs.lstrip("#")), qs]
        else:
            where.append("(o.table_label LIKE ? OR o.device LIKE ? OR o.device_cert LIKE ?)")
            args += [f"%{qs}%", f"%{qs}%", f"%{qs}%"]
    rows = await db(rt.store.query, f"""
        SELECT o.id, o.created_at, o.table_label, o.device, o.device_cert, o.total_cents, o.test, o.payment, o.closing_id,
               o.no_print,
               COALESCE((SELECT SUM(amount_cents) FROM cancellations c WHERE c.order_id=o.id), 0) AS storno_cents,
               (SELECT SUM(qty) FROM order_items i WHERE i.order_id=o.id) AS items
        FROM orders o WHERE {' AND '.join(where)} ORDER BY o.id DESC LIMIT ?""", (*args, limit))
    for r in rows:                                   # «XCover101 (Anna)»: Gerät und Name aus der App
        r["device"] = lb.device_label(r.pop("device_cert"), r["device"])
    return {"orders": rows, "payment_mode": await db(payment_mode, rt.store)}


@admin_router.get("/orders/{oid}")
async def get_order(oid: int):
    d = await db(order_detail, rt.store, oid)
    if not d:
        raise HTTPException(404, t("err.ledger.order_unknown"))
    d["receipt_enabled"] = await db(receipts.enabled, rt.store)
    return d


class AdminStornoIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    lines: list[StornoLine] | None = Field(default=None, max_length=200)
    reason: str = Field(pattern="^(" + "|".join(lb.REASONS) + ")$")
    note: str = Field(default="", max_length=80)


@admin_router.post("/orders/{oid}/storno")
async def admin_storno(oid: int, s: AdminStornoIn, request: Request):
    lines = {x.line_no: x.qty for x in s.lines} if s.lines else None
    try:
        res = await db(cancel, rt.store, order_id=oid, lines=lines, reason=s.reason, note=s.note,
                       by_user=actor_of(request), client_uuid=s.client_uuid)
    except LedgerError as e:
        raise _http(e) from None
    for pid in res["printer_ids"]:
        rt.spooler.notify(pid)
    await db(_gast_refund, oid)
    return res


class AdminPaymentIn(BaseModel):
    payment: str | None = Field(default=None, pattern="^(" + "|".join(lb.PAYMENTS) + ")$")


@admin_router.put("/orders/{oid}/payment")
async def admin_payment(oid: int, p: AdminPaymentIn, request: Request):
    try:
        await db(set_payment, rt.store, payment=p.payment, order_id=oid, actor=actor_of(request))
    except LedgerError as e:
        raise _http(e) from None
    return {"ok": True}


@admin_router.get("/closings/open")
async def closing_open():
    s = await db(open_summary, rt.store)
    return {"summary": s, "payment_mode": await db(payment_mode, rt.store),
            "vat_enabled": await db(lb.vat_enabled, rt.store),
            "currency": await db(rt.store.setting, "currency", "CHF")}


@admin_router.post("/closings")
async def closing_create(c: ClosingIn, request: Request):
    try:
        return await db(create_closing, rt.store, actor_of(request), c)
    except LedgerError as e:
        raise _http(e) from None


@admin_router.get("/closings")
async def closing_list():
    rows = await db(rt.store.query, f"SELECT {', '.join(_CLOSING_FIELDS)}, chain_hash FROM closings ORDER BY id DESC")
    return {"closings": rows}


@admin_router.get("/closings/{cid}/pdf")
async def closing_pdf_get(cid: int):
    r = await db(rt.store.query_one, "SELECT * FROM closings WHERE id=?", (cid,))
    if not r:
        raise HTTPException(404, t("err.ledger.closing_unknown"))
    pdf = await db(closing_pdf, r)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="abschluss-{cid:04d}.pdf"'})


@admin_router.get("/closings/{cid}/csv")
async def closing_csv_get(cid: int):
    r = await db(rt.store.query_one, "SELECT orders_csv FROM closings WHERE id=?", (cid,))
    if not r:
        raise HTTPException(404, t("err.ledger.closing_unknown"))
    return Response(_unpack(r["orders_csv"]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="abschluss-{cid:04d}.csv"'})


@admin_router.get("/closings/{cid}/verify")
async def closing_verify(cid: int):
    try:
        return await db(verify_closing, rt.store, cid)
    except LedgerError as e:
        raise _http(e) from None


@admin_router.get("/audit")
async def audit_list(q: str = Query("", max_length=60), limit: int = Query(300, ge=1, le=2000)):
    if q.strip():
        like = f"%{q.strip()}%"
        rows = await db(rt.store.query, "SELECT * FROM audit_log WHERE actor LIKE ? OR action LIKE ? OR details LIKE ? "
                                        "ORDER BY id DESC LIMIT ?", (like, like, like, limit))
    else:
        rows = await db(rt.store.query, "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        try:
            r["details"] = json.loads(r["details"] or "{}")
        except ValueError:
            r["details"] = {}
    return {"entries": rows}


# -- MWST ---------------------------------------------------------------------------------------------------
@admin_router.get("/vat")
async def vat_get():
    def run():
        return {"enabled": lb.vat_enabled(rt.store), "number": rt.store.setting("vat_number", ""),
                "rates": rt.store.query("SELECT id, code, rate_bp, valid_from FROM vat_rates ORDER BY code, valid_from"),
                "current": lb.vat_rates_on(rt.store), "codes": list(lb.VAT_CODES), "today": date.today().isoformat()}
    return await db(run)


class VatIn(BaseModel):
    enabled: bool
    number: str = Field(default="", max_length=40, pattern=r"^[\w .\-]*$")


@admin_router.put("/vat")
async def vat_put(v: VatIn, request: Request):
    number = " ".join(v.number.split())
    await db(rt.store.batch, [
        ("INSERT INTO settings(key,value) VALUES ('vat_enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
         ("1" if v.enabled else "0",)),
        ("INSERT INTO settings(key,value) VALUES ('vat_number', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
         (number,)),
        lb.audit_stmt(actor_of(request), "vat", enabled=v.enabled, number=number)])
    return await vat_get()


class VatRateIn(BaseModel):
    code: str = Field(pattern="^(" + "|".join(lb.VAT_CODES) + ")$")
    rate_bp: int = Field(ge=0, le=9999)
    valid_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@admin_router.post("/vat/rates")
async def vat_rate_add(v: VatRateIn, request: Request):
    try:
        date.fromisoformat(v.valid_from)
    except ValueError:
        raise HTTPException(422, t("err.ledger.date")) from None
    try:
        await db(rt.store.batch, [
            ("INSERT INTO vat_rates(code, rate_bp, valid_from) VALUES (?,?,?)", (v.code, v.rate_bp, v.valid_from)),
            lb.audit_stmt(actor_of(request), "vat_rate", code=v.code, rate_bp=v.rate_bp, valid_from=v.valid_from)])
    except StoreError as e:
        if e.is_unique_violation:
            raise HTTPException(409, t("err.ledger.rate_exists")) from None
        raise
    return await vat_get()


@admin_router.delete("/vat/rates/{rid}")
async def vat_rate_delete(rid: int, request: Request):
    """Nur künftige Sätze lassen sich löschen (vergangene gehören zu gespeicherten Bestellungen)."""
    r = await db(rt.store.query_one, "SELECT * FROM vat_rates WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    if r["valid_from"] <= date.today().isoformat():
        raise HTTPException(409, t("err.ledger.rate_in_use"))
    await db(rt.store.batch, [("DELETE FROM vat_rates WHERE id=?", (rid,)),
                              lb.audit_stmt(actor_of(request), "vat_rate_delete", **r)])
    return await vat_get()


class ProductVatIn(BaseModel):
    vat_code: str | None = Field(default=None, pattern="^(" + "|".join(lb.VAT_CODES) + ")$")


@admin_router.put("/products/{pid}/vat")
async def product_vat(pid: int, v: ProductVatIn, request: Request):
    res = await db(rt.store.batch, [("UPDATE products SET vat_code=? WHERE id=?", (v.vat_code, pid)),
                                    lb.audit_stmt(actor_of(request), "product_vat", product=pid, vat_code=v.vat_code)])
    if not res[0].rows_affected:
        raise HTTPException(404)
    return {"ok": True, "vat_code": v.vat_code}


# -- Rollen der Handys ---------------------------------------------------------------------------------------
class DeviceRoleIn(BaseModel):
    device: str | None = Field(default=None, pattern=r"^[0-9A-Za-z]{1,64}$")   # Geräte-ID (Zertifikat oder Handy, T1)
    name: str | None = Field(default=None, min_length=1, max_length=40)       # alte Form: Gerätename
    role: str = Field(pattern="^(" + "|".join(lb.DEVICE_ROLES) + ")$")


def _set_device_role(d: DeviceRoleIn, actor: str) -> str | None:
    """Rolle speichern: Gerät mit Zertifikat in device_role_ids, Handy ohne Zertifikat beim Handy (geht beim Verknüpfen
    ans Gerät über). Alte Form mit Namen: das gültige Gerät dieses Namens, sonst wie früher device_roles. -> Name"""
    from . import device_ids
    items = device_ids.phones_raw(rt.store)
    phone = next((x for x in items if x.get("kind") == "phone" and d.device and x.get("id") == d.device), None)
    did = phone.get("cert") if phone else d.device
    if d.device and not phone and d.device not in device_ids.lineages(rt.store):
        raise HTTPException(404, t("err.device.not_found"))
    if not d.device:
        did = devices.device_by_name(d.name or "")
        phone = None if did else next((x for x in items if x.get("kind") == "phone"
                                       and device_ids.norm(x.get("name")) == device_ids.norm(d.name)), None)
        if not did and phone and phone.get("cert"):
            did = phone["cert"]
    audit = lambda name: lb.audit_stmt(actor, "device_role", name=name, role=d.role)  # noqa: E731
    if did:
        name = device_ids.lineages(rt.store).get(did, {}).get("name") or d.name
        rt.store.batch([*devices.set_role(did, d.role), audit(name)])
        return name
    if phone:
        phone["role"] = d.role
        rt.store.batch([device_ids.save_phones(items), audit(phone.get("name"))])
        return phone.get("name")
    if not d.name:
        raise HTTPException(422, t("err.device.not_found"))
    rt.store.batch([("INSERT INTO device_roles(name, role) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET role=excluded.role",
                     (d.name, d.role)), audit(d.name)])
    return d.name


@admin_router.put("/devices/role")
async def device_role_put(d: DeviceRoleIn, request: Request):
    name = await db(_set_device_role, d, actor_of(request))
    return {"ok": True, "name": name}


def open_orders(store: Store) -> int:
    return int(store.scalar("SELECT COUNT(*) FROM orders WHERE closing_id IS NULL AND test=0") or 0)

