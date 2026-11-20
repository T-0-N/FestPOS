"""Quittung für den Gast (Kassenbeleg).

Gedruckt auf Wunsch: an der Kasse (ganze Bestellung, bei «Einzeln» pro Person), im Verlauf am Handy oder in der
Verwaltung (Bestellungen). Gedruckt wird auf dem Drucker, der in der Verwaltung für Quittungen gewählt ist.

Damit die Quittung als Kassenbeleg taugt:
  * fortlaufende Nummer ohne Lücken – nur für echte Bestellungen (Übungen aus dem Testmodus: «TEST», ohne Nummer)
  * der Inhalt wird beim Ausstellen festgehalten (Positionen, Preise, MWST, Kopf, Zahlart …) und mit einer Prüfsumme
    gesichert (wie Bestellungen und Stornos, ledger_base.py). Jeder weitere Druck ist dieselbe Quittung, deutlich
    als KOPIE gekennzeichnet und mit Zeit und Person festgehalten (receipt_copies).
  * dieselben Positionen werden nur einmal im Original quittiert – sonst gibt es eine Kopie
  * alle Quittungen und Kopien stehen im Tagesabschluss (Anzahl, Nummern, Lücken, Prüfung) und im Protokoll
  * «Teilen» pro Person: eine Quittung über den Anteil am Betrag (ohne Positionen; MWST anteilig nach
    den Sätzen der Bestellung). Eine Bestellung wird entweder über Positionen oder über Anteile quittiert, nie beides –
    sonst stünde derselbe Umsatz zweimal auf Quittungen. Pro Person ein Original, danach Kopien.
  * MWST nur, wenn sie in der Verwaltung eingeschaltet ist (nicht jeder Verein ist MWST-pflichtig): dann mit
    MWST-Nummer, Satz pro Position und MWST-Betrag pro Satz (Preise inkl. MWST). Über CHF 400 braucht eine
    MWST-Quittung zusätzlich Name und Adresse des Empfängers (Art. 57 MWSTV) – das geht in der Verwaltung.
"""
from __future__ import annotations

import json
from collections import defaultdict

from . import ledger_base as lb
from .i18n import t
from .store import Store, StoreError, now_iso

RECIPIENT_LIMIT = 40000          # Rappen: darüber braucht eine MWST-Quittung Name und Adresse des Empfängers
MAX_HEADER_LINES = 6
MAX_FOOTER_LINES = 3


class ReceiptError(Exception):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def _lines_of(text: str | None, limit: int) -> list[str]:
    return [" ".join(x.split())[:48] for x in (text or "").splitlines() if x.strip()][:limit]


def config(store: Store) -> dict:
    try:
        pid = int(store.setting("receipt_printer_id", "0") or 0)
    except ValueError:
        pid = 0
    return {"printer_id": pid, "header": store.setting("receipt_header", "") or "",
            "footer": store.setting("receipt_footer", "") or ""}


def printer(store: Store) -> dict | None:
    pid = config(store)["printer_id"]
    return store.query_one("SELECT id, name, enabled FROM printers WHERE id=?", (pid,)) if pid else None


def next_no(store: Store) -> int:
    """Nächste Quittungsnummer: fortlaufend – und nie unter der Untergrenze, die das Einspielen einer Sicherung setzt
    (receipt_no_floor, T8): sonst bekäme ein Gast eine Nummer, die schon auf einer früheren Quittung steht."""
    return int(store.scalar("SELECT MAX(COALESCE((SELECT MAX(no) FROM receipts), 0), COALESCE((SELECT CAST(value AS "
                            "INTEGER) FROM settings WHERE key='receipt_no_floor'), 0))") or 0) + 1


def enabled(store: Store) -> bool:
    """Quittungen sind eingerichtet: ein Drucker ist gewählt und eingeschaltet."""
    p = printer(store)
    return bool(p and p["enabled"])


# -- Wofür die Quittung ist: eine Bestellung oder ein offener Tisch (app/tabs.py) ----------------------------------
# Bestellungen eines Tisches werden immer über den Tisch quittiert (Schlüssel «tab:<uuid>» statt der Bestellnummer):
# so gilt die laufende Nummer «seq» für alle Bestellungen des Tisches, und keine Position wird zweimal im Original
# quittiert – egal ob am Handy beim Abrechnen oder in der Verwaltung bei einer der Bestellungen.
def tab_key(tab: dict) -> str:
    return f"tab:{tab.get('uuid') or tab['id']}"


def source(store: Store, *, order_id: int | None = None, tab_id: int | None = None) -> dict:
    """-> {key, orders, first, tab, test}. Eine Bestellung eines Tisches wird zum Tisch."""
    tab = None
    if order_id is not None:
        order = store.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not order:
            raise ReceiptError(t("err.ledger.order_unknown"), 404)
        if not order.get("tab_id"):
            return {"key": order["client_uuid"], "orders": [order], "first": order, "tab": None, "test": bool(order["test"])}
        tab_id = order["tab_id"]
    tab = store.query_one("SELECT * FROM tabs WHERE id=?", (tab_id,))
    if not tab:
        raise ReceiptError(t("err.tab.unknown"), 404)
    orders = store.query("SELECT * FROM orders WHERE tab_id=? ORDER BY id", (tab_id,))
    if not orders:
        raise ReceiptError(t("err.receipt.nothing"), 409)
    return {"key": tab_key(tab), "orders": orders, "first": orders[0], "tab": tab, "test": bool(tab["test"])}


def keys_of_order(store: Store, order: dict) -> list[str]:
    """Unter welchen Schlüsseln Quittungen dieser Bestellung stehen (eigene und die des Tisches)."""
    out = [order["client_uuid"]]
    if order.get("tab_id"):
        tab = store.query_one("SELECT id, uuid FROM tabs WHERE id=?", (order["tab_id"],))
        if tab:
            out.append(tab_key(tab))
    return out


# -- Was ist noch nicht quittiert -------------------------------------------------------------------------
def _cancelled(store: Store, order_ids: list[int]) -> dict[int, int]:
    marks = ",".join("?" * len(order_ids))
    rows = store.query(f"SELECT ci.item_id, SUM(ci.qty) AS q FROM cancellation_items ci "
                       f"JOIN cancellations c ON c.id=ci.cancellation_id WHERE c.order_id IN ({marks}) GROUP BY ci.item_id",
                       tuple(order_ids))
    return {r["item_id"]: int(r["q"] or 0) for r in rows}


def _line_key(ln: dict, tab: bool):
    """Position einer Quittung: bei einer Bestellung die Positionsnummer, bei einem Tisch Bestellung#Position."""
    return f"{ln.get('order_uuid')}#{ln['line_no']}" if tab else int(ln["line_no"])


def _receipted(store: Store, src: dict) -> dict:
    """Position -> schon im Original quittierte Menge. Über die eindeutige Nummer der Bestellung bzw. des Tisches:
    nach «Alle Bestellungen löschen» beginnen die Bestellnummern wieder bei 1, die Quittungen bleiben."""
    out: dict = defaultdict(int)
    tab = src["tab"] is not None
    for r in store.query("SELECT lines FROM receipts WHERE order_uuid=?", (src["key"],)):
        for ln in json.loads(r["lines"] or "[]"):
            out[_line_key(ln, tab)] += int(ln["qty"])
    return out


def open_lines(store: Store, src: dict) -> list[dict]:
    """Positionen mit der Menge, die noch quittiert werden kann (bestellt − storniert − quittiert)."""
    oids = [o["id"] for o in src["orders"]]
    uuid_of = {o["id"]: o["client_uuid"] for o in src["orders"]}
    marks = ",".join("?" * len(oids))
    items = store.query(f"SELECT * FROM order_items WHERE order_id IN ({marks}) ORDER BY order_id, line_no, id", tuple(oids))
    cancelled, receipted = _cancelled(store, oids), _receipted(store, src)
    tab = src["tab"] is not None
    for i in items:
        i["order_uuid"] = uuid_of[i["order_id"]]
        i["k"] = _line_key(i, tab)
        i["left"] = max(0, i["qty"] - cancelled.get(i["id"], 0) - receipted.get(i["k"], 0))
    return items


def _allocate(items: list[dict], wanted: list[dict]) -> dict:
    """Gewünschte Produkte (product_id, Menge, evtl. Optionen) auf die Positionen verteilen – Positionen mit
    denselben Optionen zuerst. Das Handy kennt die Positionsnummern des Servers nicht (er sortiert selbst)."""
    left = {i["k"]: i["left"] for i in items}
    out: dict = defaultdict(int)
    order = {i["k"]: n for n, i in enumerate(items)}
    for w in wanted:
        need = int(w["qty"])
        cands = [i for i in items if i["product_id"] == w["product_id"]]
        cands.sort(key=lambda i: (0 if (i.get("options") or "") == (w.get("options") or "") else 1, order[i["k"]]))
        for i in cands:
            take = min(need, left[i["k"]])
            if take > 0:
                out[i["k"]] += take
                left[i["k"]] -= take
                need -= take
            if not need:
                break
        if need:
            name = cands[0]["name"] if cands else str(w["product_id"])
            raise ReceiptError(t("err.receipt.not_open", name=name), 409)
    return dict(out)


# -- Ausstellen ---------------------------------------------------------------------------------------------
def _snapshot(store: Store, src: dict, part: int | None, recipient: list[str], by: str, payment: str | None = None) -> dict:
    vat_on = lb.vat_enabled(store)
    order = src["first"]
    pay = order.get("payment") if order.get("payment") in lb.PAYMENTS else None
    if src["tab"] is not None:                       # Tisch: Zahlart wie an der Kasse gewählt (sonst wie die Zahlungen)
        pay = payment if payment in lb.PAYMENTS else None
        if pay is None:
            kinds = {r["payment"] for r in store.query("SELECT DISTINCT payment FROM tab_payments WHERE tab_id=? "
                                                       "AND undone_at IS NULL", (src["tab"]["id"],))}
            pay = next(iter(kinds)) if len(kinds) == 1 and next(iter(kinds)) in lb.PAYMENTS else None
    out = {"header": _lines_of(config(store)["header"], MAX_HEADER_LINES),
           "footer": _lines_of(config(store)["footer"], MAX_FOOTER_LINES),
           "vat": vat_on, "vat_number": (store.setting("vat_number", "") or None) if vat_on else None,
           "event": store.setting("event_name", "") or "", "currency": store.setting("currency", "CHF") or "CHF",
           "table": order["table_label"], "table_word": store.setting("table_word", "Tisch"),
           "order_no": order["id"], "order_at": order["created_at"],
           "device": lb.device_label(order.get("device_cert"), order.get("device")),
           "payment": pay, "part": part, "recipient": recipient or None, "by": by}
    if src["tab"] is not None:
        out["orders"] = [o["id"] for o in src["orders"]]
        out["device"] = src["tab"].get("owner_name") or out["device"]
    from . import bon_design                         # Gestaltung festhalten: eine Kopie sieht aus wie das Original
    out["design"] = bon_design.receipt_snapshot(store)
    return out


def _is_share(r: dict) -> bool:
    return any(ln.get("share") for ln in json.loads(r.get("lines") or "[]"))


def _share_lines(store: Store, src: dict, amount: int) -> tuple[list[dict], int]:
    """Anteil auf die MWST-Sätze der Bestellung verteilen (nach dem, was nach Stornos gilt; grösster Rest).
    -> (Zeilen, Total der Bestellung)"""
    oids = [o["id"] for o in src["orders"]]
    marks = ",".join("?" * len(oids))
    items = store.query(f"SELECT * FROM order_items WHERE order_id IN ({marks})", tuple(oids))
    cancelled = _cancelled(store, oids)
    by: dict[tuple, int] = defaultdict(int)
    for i in items:
        q = i["qty"] - cancelled.get(i["id"], 0)
        if q > 0:
            by[(i.get("vat_code"), i.get("vat_bp"))] += q * i["price_cents"]
    total = sum(by.values())
    if total <= 0:
        raise ReceiptError(t("err.receipt.nothing"), 409)
    raw = {k: amount * v / total for k, v in by.items()}
    cut = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - cut[k], reverse=True)[:amount - sum(cut.values())]:
        cut[k] += 1
    lines = [{"line_no": 0, "share": True, "name": t("bon.receipt.share_line", total=f"{total / 100:.2f}"),
              "options": None, "qty": 1, "price": v, "amount": v, "vat_code": code, "vat_bp": bp}
             for (code, bp), v in sorted(cut.items(), key=lambda kv: -(kv[0][1] or 0)) if v]
    return lines, total


def _issue_share(store: Store, src: dict, p: dict, *, request_id: str, share: dict, by_user: str | None,
                 by_device: str | None, recipient_lines: list[str], payment: str | None) -> dict:
    """Quittung über den Anteil einer Person (Teilen). share = {person, persons, amount}."""
    order, key, test = src["first"], src["key"], src["test"]
    person, amount = int(share["person"]), int(share["amount"])
    who = by_user or (f"device:{by_device}" if by_device else "?")
    for _ in range(5):
        seq = int(store.scalar("SELECT MAX(COALESCE(MAX(seq), 0), COUNT(*)) FROM receipts WHERE order_uuid=?",
                               (key,)) or 0) + 1
        have = store.query("SELECT * FROM receipts WHERE order_uuid=? ORDER BY id", (key,))
        if any(not _is_share(r) for r in have):
            raise ReceiptError(t("err.receipt.has_lines"), 409)
        mine = [r for r in have if r["part"] == person]
        if mine:                                       # diese Person hat schon ihre Quittung: Kopie
            return copies(store, [mine[0]["id"]], request_id=request_id, by_user=by_user, by_device=by_device)
        lines, total = _share_lines(store, src, amount)
        if sum(int(r["total_cents"]) for r in have) + amount > total + 4:
            raise ReceiptError(t("err.receipt.share_too_much"), 409)
        if amount > RECIPIENT_LIMIT and lb.vat_enabled(store) and not recipient_lines:
            if by_user:
                raise ReceiptError(t("err.receipt.recipient_missing"), 422)
            raise ReceiptError(t("err.receipt.recipient_admin"), 409)
        data = _snapshot(store, src, person, recipient_lines, by_user or by_device or "", payment)
        if src["tab"] is None and payment in lb.PAYMENTS:
            data["payment"] = payment                  # Zahlart dieser Person (Teilen: pro Person)
        data["share"] = {"person": person, "persons": int(share.get("persons") or 0) or None, "total": total}
        lines_json, data_json, ts = json.dumps(lines, ensure_ascii=False), json.dumps(data, ensure_ascii=False), now_iso()
        no = None if test else next_no(store)
        rec = {"no": no, "request_id": request_id, "order_uuid": key, "created_at": ts, "part": person,
               "total_cents": amount, "lines": lines_json, "test": int(test), "data": data_json,
               "digest_v": lb.RECEIPT_DIGEST_V}
        stmts = [("INSERT INTO receipts(no, request_id, order_id, order_uuid, part, lines, total_cents, data, by_user, "
                  "by_device, printer_id, test, created_at, digest, seq, digest_v) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (no, request_id, order["id"], key, person, lines_json, amount, data_json, by_user, by_device, p["id"],
                   int(test), ts, lb.receipt_digest(rec), seq, lb.RECEIPT_DIGEST_V)),
                 _job_stmt(request_id, order["id"], p["id"], ts, False),
                 lb.audit_stmt(who, "receipt", no=no, order=order["id"], total=amount, part=person, test=test, share=True,
                               **({"tab": src["tab"]["id"]} if src["tab"] is not None else {}))]
        try:
            store.batch(stmts, transaction=True)
            break
        except StoreError as e:
            if not e.is_unique_violation:
                raise
            if store.query_one("SELECT id FROM receipts WHERE request_id=?", (request_id,)):
                break
    else:
        raise ReceiptError(t("err.receipt.busy"), 409)
    return _result(store, [store.query_one("SELECT * FROM receipts WHERE request_id=?", (request_id,))], copy=False)


def _job_stmt(request_id: str, order_id: int, printer_id: int, ts: str, copy: bool):
    return ("INSERT INTO print_jobs(order_id, station_id, printer_id, kind, note, created_at, reprint) "
            "VALUES (?,?,?,'receipt',?,?,?)", (order_id, printer_id, printer_id, request_id, ts, int(copy)))


def issue(store: Store, *, order_id: int | None = None, tab_id: int | None = None, request_id: str,
          items: list[dict] | None = None, part: int | None = None, by_user: str | None = None,
          by_device: str | None = None, recipient: str | None = None, payment: str | None = None,
          share: dict | None = None) -> dict:
    """Quittung ausstellen und drucken – für eine Bestellung oder einen offenen Tisch (alle seine Bestellungen; eine
    Bestellung eines Tisches wird immer über den Tisch quittiert). items=None: alles, was noch nicht quittiert ist
    (ist schon alles quittiert: Kopien davon). items=[{product_id, qty, options}]: genau diese Produkte (Einzeln).
    request_id: gleiche Anfrage nochmals (Doppeltipp, Antwort verloren) druckt nichts zweimal."""
    dup = store.query_one("SELECT * FROM receipts WHERE request_id=?", (request_id,))
    if dup:
        return _result(store, [dup], copy=False, duplicate=True)
    dup_copy = store.query("SELECT r.* FROM receipt_copies c JOIN receipts r ON r.id=c.receipt_id "
                           "WHERE c.request_id=? OR c.request_id LIKE ?", (request_id, request_id + ":%"))
    if dup_copy:
        return _result(store, dup_copy, copy=True, duplicate=True)
    p = printer(store)
    if not p or not p["enabled"]:
        raise ReceiptError(t("err.receipt.not_set_up"), 409)
    src = source(store, order_id=order_id, tab_id=tab_id)
    order, key, test = src["first"], src["key"], src["test"]
    if test and store.setting("test_print", "0") != "1":
        raise ReceiptError(t("err.receipt.test_no_print"), 409)
    who = by_user or (f"device:{by_device}" if by_device else "?")
    recipient_lines = _lines_of(recipient, 4)
    if share:                                          # Teilen: Anteil einer Person
        return _issue_share(store, src, p, request_id=request_id, share=share, by_user=by_user, by_device=by_device,
                            recipient_lines=recipient_lines, payment=payment)
    shared = [r for r in store.query("SELECT * FROM receipts WHERE order_uuid=? ORDER BY id", (key,)) if _is_share(r)]
    if shared:                                         # schon pro Person (Anteile) quittiert: nur Kopien davon
        if items is None:
            return copies(store, [r["id"] for r in shared], request_id=request_id, by_user=by_user, by_device=by_device)
        raise ReceiptError(t("err.receipt.shared"), 409)
    tab = src["tab"] is not None
    # Mehrere Geräte zugleich: «seq» (laufende Nummer pro Bestellung bzw. Tisch, eindeutig) lässt nur die erste Quittung
    # zu, die anderen lesen neu, was noch offen ist – dieselben Positionen werden so nie zweimal im Original quittiert.
    for _ in range(5):
        # zuerst die laufende Nummer, DANN lesen, was offen ist: wer danach quittiert, hat diese Nummer schon belegt
        seq = int(store.scalar("SELECT MAX(COALESCE(MAX(seq), 0), COUNT(*)) FROM receipts WHERE order_uuid=?",
                               (key,)) or 0) + 1
        lines = open_lines(store, src)
        if items is None:
            take = {i["k"]: i["left"] for i in lines if i["left"] > 0}
            if not take:
                originals = store.query("SELECT * FROM receipts WHERE order_uuid=? ORDER BY id", (key,))
                if not originals:
                    raise ReceiptError(t("err.receipt.nothing"), 409)
                return copies(store, [r["id"] for r in originals], request_id=request_id, by_user=by_user,
                              by_device=by_device)
        else:
            take = _allocate(lines, items)
            if not take:
                raise ReceiptError(t("err.receipt.nothing"), 409)
        by_k = {i["k"]: i for i in lines}
        rlines = []
        for k in [i["k"] for i in lines if i["k"] in take]:
            it, q = by_k[k], take[k]
            ln = {"line_no": it["line_no"], "name": it["name"], "options": it.get("options"), "qty": q,
                  "price": it["price_cents"], "amount": it["price_cents"] * q,
                  "vat_code": it.get("vat_code"), "vat_bp": it.get("vat_bp")}
            if tab:                                        # Tisch: aus welcher Bestellung
                ln["order_uuid"], ln["order_no"] = it["order_uuid"], it["order_id"]
            rlines.append(ln)
        total = sum(x["amount"] for x in rlines)
        # Über CHF 400 mit MWST gehören Name und Adresse des Gastes auf die Quittung (Art. 57 MWSTV): am Handy gar nicht
        # erst drucken (dort gibt es kein Feld dafür), in der Verwaltung nur mit Empfänger
        if total > RECIPIENT_LIMIT and lb.vat_enabled(store) and not recipient_lines:
            if by_user:
                raise ReceiptError(t("err.receipt.recipient_missing"), 422)
            raise ReceiptError(t("err.receipt.recipient_admin"), 409)
        data = _snapshot(store, src, part, recipient_lines, by_user or by_device or "", payment)
        lines_json = json.dumps(rlines, ensure_ascii=False)
        data_json = json.dumps(data, ensure_ascii=False)
        ts = now_iso()
        no = None if test else next_no(store)
        rec = {"no": no, "request_id": request_id, "order_uuid": key, "created_at": ts, "part": part,
               "total_cents": total, "lines": lines_json, "test": int(test), "data": data_json,
               "digest_v": lb.RECEIPT_DIGEST_V}             # T41: geprüft wird genau, was gespeichert und gedruckt wird
        digest = lb.receipt_digest(rec)
        stmts = [("INSERT INTO receipts(no, request_id, order_id, order_uuid, part, lines, total_cents, data, by_user, "
                  "by_device, printer_id, test, created_at, digest, seq, digest_v) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (no, request_id, order["id"], key, part, lines_json, total, data_json, by_user,
                   by_device, p["id"], int(test), ts, digest, seq, lb.RECEIPT_DIGEST_V)),
                 _job_stmt(request_id, order["id"], p["id"], ts, False),
                 lb.audit_stmt(who, "receipt", no=no, order=order["id"], total=total, part=part, test=test,
                               **({"tab": src["tab"]["id"]} if tab else {}))]
        try:
            store.batch(stmts, transaction=True)
            break
        except StoreError as e:
            if not e.is_unique_violation:
                raise
            if store.query_one("SELECT id FROM receipts WHERE request_id=?", (request_id,)):
                break                                  # dieselbe Anfrage war schneller
            # sonst: Nummer vergeben oder anderes Gerät hat diese Bestellung eben quittiert -> neu lesen
    else:
        raise ReceiptError(t("err.receipt.busy"), 409)
    rec = store.query_one("SELECT * FROM receipts WHERE request_id=?", (request_id,))
    return _result(store, [rec], copy=False)


def copies(store: Store, receipt_ids: list[int], *, request_id: str, by_user: str | None = None,
           by_device: str | None = None) -> dict:
    """Kopie(n) bestehender Quittungen drucken – gleicher Inhalt, als KOPIE gekennzeichnet, festgehalten."""
    p = printer(store)
    if not p or not p["enabled"]:
        raise ReceiptError(t("err.receipt.not_set_up"), 409)
    recs = [store.query_one("SELECT * FROM receipts WHERE id=?", (rid,)) for rid in receipt_ids]
    recs = [r for r in recs if r]
    if not recs:
        raise ReceiptError(t("err.receipt.unknown"), 404)
    ts, who = now_iso(), by_user or (f"device:{by_device}" if by_device else "?")
    stmts = []
    for n, r in enumerate(recs):
        rid = request_id if len(recs) == 1 else f"{request_id}:{n + 1}"
        stmts += [("INSERT INTO receipt_copies(request_id, receipt_id, by_user, by_device, created_at) VALUES (?,?,?,?,?)",
                   (rid, r["id"], by_user, by_device, ts)),
                  _job_stmt(r["request_id"], r["order_id"], p["id"], ts, True),
                  lb.audit_stmt(who, "receipt_copy", no=r["no"], order=r["order_id"], test=bool(r["test"]))]
    try:
        store.batch(stmts, transaction=True)
    except StoreError as e:
        if not e.is_unique_violation:
            raise
    return _result(store, recs, copy=True)


def on_reprint(store: Store, src_job: int, new_job: int, actor: str) -> None:
    """Verwaltung › Druckaufträge › Nachdrucken einer Quittung. War der alte Auftrag gedruckt, ist der neue eine
    weitere KOPIE (festgehalten wie jede Kopie). Kam der alte nie aus dem Drucker (Störung, hängengeblieben), ersetzt
    der neue ihn: als Original, solange noch kein Druck dieser Quittung gelungen ist – sonst bleibt es eine Kopie."""
    src = store.query_one("SELECT kind, note, status FROM print_jobs WHERE id=?", (src_job,))
    if not src or src["kind"] != "receipt":
        return
    rec = store.query_one("SELECT id, no, order_id, test FROM receipts WHERE request_id=?", (src["note"],))
    if not rec:
        return
    if src["status"] == "done":                       # schon gedruckt: noch ein Exemplar = Kopie
        store.batch([("UPDATE print_jobs SET reprint=1 WHERE id=?", (new_job,)),
                     ("INSERT INTO receipt_copies(request_id, receipt_id, by_user, by_device, created_at) "
                      "VALUES (?,?,?,?,?)", (f"reprint-{new_job}", rec["id"], actor, None, now_iso())),
                     lb.audit_stmt(actor, "receipt_copy", no=rec["no"], order=rec["order_id"], test=bool(rec["test"]),
                                   reprint=new_job)], transaction=True)
        return
    printed = store.scalar("SELECT COUNT(*) FROM print_jobs WHERE kind='receipt' AND note=? AND status='done' AND id<>?",
                           (src["note"], new_job))
    store.execute("UPDATE print_jobs SET reprint=? WHERE id=?", (1 if printed else 0, new_job))


def _result(store: Store, recs: list[dict], *, copy: bool, duplicate: bool = False) -> dict:
    p = printer(store) or {}
    return {"receipts": [{"id": r["id"], "no": r["no"], "test": bool(r["test"]), "total_cents": r["total_cents"],
                          "part": r["part"]} for r in recs],
            "copy": copy, "duplicate": duplicate, "printer_id": p.get("id"), "printer": p.get("name")}


def of_order(store: Store, order_uuid: str) -> list[dict]:
    """Quittungen einer Bestellung (für die Verwaltung und das Handy) – bei einem Tisch auch die über den Tisch."""
    order = store.query_one("SELECT client_uuid, tab_id FROM orders WHERE client_uuid=?", (order_uuid,))
    keys = keys_of_order(store, order) if order else [order_uuid]
    marks = ",".join("?" * len(keys))
    rows = store.query(f"SELECT r.id, r.no, r.part, r.total_cents, r.created_at, r.by_user, r.by_device, r.test, "
                       f"r.order_uuid LIKE 'tab:%' AS tab, (SELECT COUNT(*) FROM receipt_copies c WHERE c.receipt_id=r.id) "
                       f"AS copies FROM receipts r WHERE r.order_uuid IN ({marks}) ORDER BY r.id", tuple(keys))
    for r in rows:
        r["test"] = bool(r["test"])
        r["tab"] = bool(r["tab"])
    return rows
