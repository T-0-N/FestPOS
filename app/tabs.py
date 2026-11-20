"""Offene Tische (Einstellung «Tisch offen lassen», Verwaltung › Einstellungen › Kasse).

Statt nach jeder Bestellung zu kassieren, sammeln sich die Bestellungen eines Tisches in einem offenen Tisch (tabs);
abgerechnet wird am Schluss – auch in mehreren Zahlungen (Rückgeld, Teilen, Einzeln). Die Zahlungen liegen auf dem
Server (tab_payments), damit ein anderes Handy, die Festleitung und der Tagesabschluss denselben Stand sehen.

  * Pro Tisch höchstens ein offener Tisch (open_key «0:12», Übungen getrennt «1:12»). Die erste Bestellung eröffnet
    ihn; Eigentümer ist das Gerät, das ihn eröffnet hat (Geräte-ID aus dem Gerätezertifikat, T1).
  * Bestellen darf jedes Handy an jedem Tisch – die Bestellung kommt auf den offenen Tisch.
  * Abrechnen (Einstellung tab_who): own = nur das Handy, dem der Tisch gehört; own_lead = dazu Handys mit der Rolle
    Festleitung; any = jedes Handy. Die Verwaltung (ab Festleitung) darf immer übergeben, abrechnen und ausbuchen.
  * Umsatz zählt wie bisher bei der Bestellung; im Tagesabschluss wird die Zahlart der Bestellungen eines Tisches nach
    seinen Zahlungen aufgeteilt (ledger.summarize). Der Tagesabschluss geht erst, wenn kein Tisch mehr offen ist.
"""
from __future__ import annotations

import json
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import devices
from . import ledger_base as lb
from .core import admin, db, rt
from .i18n import t
from .store import Stmt, Store, StoreError, now_iso

router = APIRouter()
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])

MODES = ("now", "open")
WHO = ("own", "own_lead", "any")
PAY_KINDS = ("cash", "card", "twint", "other")
MAX_CENTS = 10_000_000


class TabError(Exception):
    def __init__(self, msg: str, status: int = 422):
        super().__init__(msg)
        self.status = status


def _http(e: TabError) -> HTTPException:
    return HTTPException(e.status, str(e))


# -- Einstellungen --------------------------------------------------------------------------------------
def mode(store: Store) -> str:
    v = store.setting("tab_mode", "now")
    return v if v in MODES else "now"


def who(store: Store) -> str:
    v = store.setting("tab_who", "own_lead")
    return v if v in WHO else "own_lead"


def idle_minutes(store: Store) -> int:
    try:
        return max(0, min(600, int(store.setting("tab_idle", "45") or 45)))
    except ValueError:
        return 45


def config(store: Store) -> dict:
    """Für /api/bootstrap und die Verwaltung."""
    return {"mode": mode(store), "who": who(store), "idle": idle_minutes(store)}


# -- Eigentümer -----------------------------------------------------------------------------------------
def owner_of(store: Store, cert: dict | None) -> str | None:
    """Gerät des Zertifikats: Geräte-ID (T1) – bleibt beim Erneuern gleich; unbekanntes Zertifikat: nach dem Namen."""
    if not cert:
        return None
    from . import device_ids
    did = device_ids.device_id_of(store, cert.get("serial") or "")
    return did or f"name:{cert.get('name') or ''}"


def open_key(table_label: str, test: bool) -> str:
    return f"{int(bool(test))}:{(table_label or '').strip().casefold()}"


def order_stmts(store: Store, *, table_label: str, test: bool, owner: str | None, owner_name: str | None,
                ts: str) -> tuple[list[Stmt], str] | None:
    """Für orders.create_order: offenen Tisch anlegen, falls es keinen gibt (im selben Batch wie die Bestellung).
    -> (Statements, Unterabfrage für orders.tab_id) oder None (Einstellung «sofort»)."""
    if mode(store) != "open":
        return None
    key = open_key(table_label, test)
    return ([("INSERT INTO tabs(table_label, owner, owner_name, opened_at, test, open_key, uuid) VALUES (?,?,?,?,?,?,?) "
              "ON CONFLICT(open_key) DO NOTHING", (table_label, owner, owner_name, ts, int(bool(test)), key,
                                                   str(uuid_mod.uuid4())))],
            key)


# -- Lesen ----------------------------------------------------------------------------------------------
def _round5(c: int) -> int:
    return int((c + 2) // 5 * 5)


def _sums(store: Store, tab_ids: list[int]) -> dict[int, dict]:
    if not tab_ids:
        return {}
    marks = ",".join("?" * len(tab_ids))
    out = {tid: {"orders": 0, "total": 0, "storno": 0, "paid": 0, "tip": 0, "last_at": None, "order_ids": []}
           for tid in tab_ids}
    for r in store.query(f"SELECT id, tab_id, total_cents, created_at FROM orders WHERE tab_id IN ({marks}) ORDER BY id",
                         tuple(tab_ids)):
        s = out[r["tab_id"]]
        s["orders"] += 1
        s["total"] += r["total_cents"]
        s["order_ids"].append(r["id"])
        s["last_at"] = max(s["last_at"] or "", r["created_at"])
    for r in store.query(f"SELECT o.tab_id, SUM(c.amount_cents) AS a FROM cancellations c JOIN orders o ON o.id=c.order_id "
                         f"WHERE o.tab_id IN ({marks}) GROUP BY o.tab_id", tuple(tab_ids)):
        out[r["tab_id"]]["storno"] = int(r["a"] or 0)
    for r in store.query(f"SELECT tab_id, SUM(amount_cents) AS a, SUM(tip_cents) AS tip FROM tab_payments "
                         f"WHERE tab_id IN ({marks}) AND undone_at IS NULL GROUP BY tab_id", tuple(tab_ids)):
        out[r["tab_id"]]["paid"] = int(r["a"] or 0)
        out[r["tab_id"]]["tip"] = int(r["tip"] or 0)
    for s in out.values():
        s["net"] = s["total"] - s["storno"]
        s["open"] = max(0, s["net"] - s["paid"])
        s["open_cash"] = max(0, _round5(s["net"]) - s["paid"])       # bar auf 5 Rappen gerundet
    return out


# T39/T40: dasselbe wie _sums, aber als SQL im Schreibbefehl – geprüft wird beim Schreiben, nicht vorher
_NET = ("(COALESCE((SELECT SUM(total_cents) FROM orders WHERE tab_id=?), 0) - COALESCE((SELECT SUM(c.amount_cents) "
        "FROM cancellations c JOIN orders o ON o.id=c.order_id WHERE o.tab_id=?), 0))")
_PAID = "COALESCE((SELECT SUM(amount_cents) FROM tab_payments WHERE tab_id=? AND undone_at IS NULL), 0)"
LIMIT_SQL = f"MAX({_NET} - {_PAID}, ({_NET} + 2) / 5 * 5 - {_PAID})"      # wie max(open, open_cash)


def limit_args(tab_id: int) -> tuple:
    """Parameter für LIMIT_SQL (sechsmal der Tisch)."""
    return (tab_id,) * 6


def _public(tab: dict, sums: dict, me: str | None = None) -> dict:
    return {"id": tab["id"], "table": tab["table_label"], "owner_name": tab.get("owner_name") or "",
            "mine": bool(me) and tab.get("owner") == me, "opened_at": tab["opened_at"], "closed_at": tab.get("closed_at"),
            "close_reason": tab.get("close_reason"), "test": bool(tab["test"]),
            **{k: sums[k] for k in ("orders", "total", "storno", "net", "paid", "tip", "open", "open_cash", "last_at")}}


def open_tabs(store: Store, *, test: bool | None = None, me: str | None = None) -> list[dict]:
    where, args = "closed_at IS NULL", ()
    if test is not None:
        where += " AND test=?"
        args = (int(test),)
    tabs = store.query(f"SELECT * FROM tabs WHERE {where} ORDER BY opened_at, id", args)
    sums = _sums(store, [x["id"] for x in tabs])
    return [_public(x, sums[x["id"]], me) for x in tabs if sums[x["id"]]["orders"]]


def count_open(store: Store) -> int:
    return int(store.scalar("SELECT COUNT(*) FROM tabs WHERE closed_at IS NULL AND test=0 AND id IN "
                            "(SELECT tab_id FROM orders WHERE tab_id IS NOT NULL)") or 0)


def _merged(store: Store, orders: list[dict]) -> tuple[list[dict], dict[int, list[dict]]]:
    """Positionen aller Bestellungen zusammengefasst (Produkt, Optionen, Preis). Ganz stornierte bleiben mit qty 0 in der
    Liste: die Nummern (id) ändern sich so nie – «Einzeln» bezahlte Stück zeigen auf die Nummer. cancelled = storniert.
    Zweiter Wert: pro Nummer die Bestellpositionen dahinter (order_id, line_no, left = noch nicht storniert)."""
    oids = [o["id"] for o in orders]
    if not oids:
        return [], {}
    marks = ",".join("?" * len(oids))
    cancelled: dict[int, int] = {}
    for r in store.query(f"SELECT ci.item_id, SUM(ci.qty) AS q FROM cancellation_items ci JOIN cancellations c "
                         f"ON c.id=ci.cancellation_id WHERE c.order_id IN ({marks}) GROUP BY ci.item_id", tuple(oids)):
        cancelled[r["item_id"]] = int(r["q"] or 0)
    merged: dict[tuple, dict] = {}
    parts: dict[int, list[dict]] = {}
    nb = {o["id"] for o in orders if o.get("no_print")}
    for i in store.query(f"SELECT id, order_id, line_no, product_id, name, price_cents, qty, options FROM order_items "
                         f"WHERE order_id IN ({marks}) ORDER BY order_id, line_no, id", tuple(oids)):
        gone = min(i["qty"], cancelled.get(i["id"], 0))
        # nachgebucht ohne Bon: eigene Position (in der Kasse markiert); neue Positionen kommen immer hinten dazu,
        # die Nummern der bisherigen (Einzeln bezahlt) bleiben
        k = (i["product_id"], i["name"], i["options"] or "", i["price_cents"], i["order_id"] in nb)
        m = merged.setdefault(k, {"id": len(merged) + 1, "pid": i["product_id"], "name": i["name"],
                                  "opt": i["options"] or "", "price": i["price_cents"], "qty": 0, "cancelled": 0,
                                  **({"nb": True} if i["order_id"] in nb else {})})
        m["qty"] += i["qty"] - gone
        m["cancelled"] += gone
        parts.setdefault(m["id"], []).append({"order_id": i["order_id"], "line_no": i["line_no"], "left": i["qty"] - gone})
    return list(merged.values()), parts


def detail(store: Store, tab_id: int, me: str | None = None) -> dict:
    """Ein Tisch mit allen Bestellungen, den Positionen (zusammengefasst: Produkt, Optionen, Preis) und den Zahlungen."""
    tab = store.query_one("SELECT * FROM tabs WHERE id=?", (tab_id,))
    if not tab:
        raise TabError(t("err.tab.unknown"), 404)
    sums = _sums(store, [tab_id])[tab_id]
    orders = store.query("SELECT id, client_uuid, created_at, total_cents, device, device_cert, no_print FROM orders "
                         "WHERE tab_id=? ORDER BY id", (tab_id,))
    items, _ = _merged(store, orders)
    pays = store.query("SELECT key, amount_cents, payment, tip_cents, part, items, by_device, by_user, created_at, undone_at "
                       "FROM tab_payments WHERE tab_id=? ORDER BY id", (tab_id,))
    for p in pays:
        try:
            p["items"] = json.loads(p["items"]) if p["items"] else {}
        except ValueError:
            p["items"] = {}
    return {**_public(tab, sums, me), "items": items, "payments": pays,
            "orders_list": [{"id": o["id"], "uuid": o["client_uuid"], "at": o["created_at"], "total": o["total_cents"],
                             "device": lb.device_label(o.get("device_cert"), o.get("device"))} for o in orders]}


# -- Rechte ---------------------------------------------------------------------------------------------
def may_settle(store: Store, tab: dict, cert: dict | None) -> bool:
    """Darf dieses Handy den Tisch abrechnen? (Verwaltung: immer, siehe admin_router)"""
    me = owner_of(store, cert)
    if who(store) == "any":
        return True
    if me and tab.get("owner") == me:
        return True
    return who(store) == "own_lead" and lb.device_role(store, cert) == "lead"


# -- Ändern ---------------------------------------------------------------------------------------------
def _open_tab(store: Store, tab_id: int) -> dict:
    tab = store.query_one("SELECT * FROM tabs WHERE id=?", (tab_id,))
    if not tab:
        raise TabError(t("err.tab.unknown"), 404)
    if tab["closed_at"]:
        raise TabError(t("err.tab.closed"), 409)
    return tab


def pay(store: Store, *, tab_id: int, key: str, amount: int, payment: str | None, tip: int = 0, part: int | None = None,
        items: dict | None = None, by_device: str | None = None, by_user: str | None = None) -> None:
    """Zahlung an einen offenen Tisch (idempotent über key). Mehr als offen ist nicht möglich (bar: auf 5 Rappen)."""
    if store.query_one("SELECT id FROM tab_payments WHERE key=?", (key,)):
        return
    tab = _open_tab(store, tab_id)
    s = _sums(store, [tab_id])[tab_id]
    limit = max(s["open"], s["open_cash"])
    if amount <= 0 or amount > limit:
        raise TabError(t("err.tab.amount", open=lb_money(s["open"])), 409)
    payment = payment if payment in PAY_KINDS else None
    # T40: offen und Betrag im Schreibbefehl geprüft – zwei Handys zugleich können nie zusammen mehr als offen buchen
    stmts = [("INSERT INTO tab_payments(key, tab_id, amount_cents, payment, tip_cents, part, items, by_device, by_user, "
              "created_at) SELECT ?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM tabs WHERE id=? AND closed_at IS NULL) "
              f"AND ? <= {LIMIT_SQL}",
              (key, tab_id, amount, payment, max(0, tip), part, json.dumps(items) if items else None, by_device, by_user,
               now_iso(), tab_id, amount, *limit_args(tab_id))),
             _if_changed(lb.audit_stmt(by_user or f"device:{by_device}", "tab_pay", tab=tab_id, table=tab["table_label"],
                                       amount=amount, payment=payment, tip=tip, test=bool(tab["test"])))]
    try:
        res = store.batch(stmts, transaction=True)
    except StoreError as e:
        if e.is_unique_violation:
            return                                         # dieselbe Zahlung war schneller
        raise
    if not res[0].rows_affected:
        _open_tab(store, tab_id)                           # inzwischen geschlossen -> 409 «geschlossen»
        s = _sums(store, [tab_id])[tab_id]                 # sonst: inzwischen anders bezahlt
        raise TabError(t("err.tab.amount", open=lb_money(s["open"])), 409)


def undo_payment(store: Store, *, tab_id: int, key: str, by: str) -> None:
    _open_tab(store, tab_id)
    res = store.batch([("UPDATE tab_payments SET undone_at=? WHERE key=? AND tab_id=? AND undone_at IS NULL AND "
                        "EXISTS (SELECT 1 FROM tabs WHERE id=? AND closed_at IS NULL)", (now_iso(), key, tab_id, tab_id)),
                       _if_changed(lb.audit_stmt(by, "tab_pay_undo", tab=tab_id, key=key))], transaction=True)
    if not res[0].rows_affected:
        _open_tab(store, tab_id)                           # inzwischen geschlossen (T40)


def _if_changed(audit: tuple) -> tuple:
    """Protokolleintrag nur, wenn der vorherige Befehl im Batch eine Zeile geändert hat (changes(), T40)."""
    return audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), audit[1]


def _same_state(tab_id: int, s: dict) -> tuple[str, tuple]:
    """T40: Bedingung «am Tisch hat sich seit dem Lesen nichts geändert» (Bestellungen, Storno, Zahlungen)."""
    return ("(SELECT COUNT(*) FROM orders WHERE tab_id=?)=? AND COALESCE((SELECT SUM(c.amount_cents) FROM "
            "cancellations c JOIN orders o ON o.id=c.order_id WHERE o.tab_id=?), 0)=? AND COALESCE((SELECT "
            "SUM(amount_cents) FROM tab_payments WHERE tab_id=? AND undone_at IS NULL), 0)=?",
            (tab_id, s["orders"], tab_id, s["storno"], tab_id, s["paid"]))


def close(store: Store, *, tab_id: int, by: str, force: bool = False) -> dict:
    """Tisch abschliessen, wenn bezahlt (bar gerundet genügt). Zahlart einer einzigen Zahlart geht an die Bestellungen."""
    tab = store.query_one("SELECT * FROM tabs WHERE id=?", (tab_id,))
    if not tab:
        raise TabError(t("err.tab.unknown"), 404)
    if tab["closed_at"]:
        return detail(store, tab_id)
    s = _sums(store, [tab_id])[tab_id]
    if s["open"] > 0 and s["open_cash"] > 0 and not force:
        raise TabError(t("err.tab.not_paid", open=lb_money(s["open"])), 409)
    kinds = {r["payment"] for r in store.query("SELECT DISTINCT payment FROM tab_payments WHERE tab_id=? AND undone_at IS NULL",
                                                (tab_id,))}
    ts = now_iso()
    same, args = _same_state(tab_id, s)
    # T40: nur schliessen, wenn seither nichts dazukam (neue Bestellung, Zahlung, Storno); die Zahlart der Bestellungen
    # nur setzen, wenn der Tisch in DIESEM Batch geschlossen wurde
    mine = ("EXISTS (SELECT 1 FROM tabs WHERE id=? AND closed_at=? AND closed_by=? AND close_reason='paid')",
            (tab_id, ts, by))
    stmts = [("UPDATE tabs SET closed_at=?, closed_by=?, close_reason='paid', open_key=NULL WHERE id=? AND closed_at IS NULL "
              f"AND {same}", (ts, by, tab_id, *args))]
    if len(kinds) == 1 and next(iter(kinds)) in lb.PAYMENTS:
        stmts.append((f"UPDATE orders SET payment=? WHERE tab_id=? AND closing_id IS NULL AND {mine[0]}",
                      (next(iter(kinds)), tab_id, *mine[1])))
    audit = lb.audit_stmt(by, "tab_close", tab=tab_id, table=tab["table_label"], net=s["net"], paid=s["paid"],
                          test=bool(tab["test"]))
    stmts.append((audit[0].replace(" VALUES (?,?,?,?)", f" SELECT ?,?,?,? WHERE {mine[0]}"), (*audit[1], *mine[1])))
    if not store.batch(stmts, transaction=True)[0].rows_affected:
        if (store.query_one("SELECT closed_at FROM tabs WHERE id=?", (tab_id,)) or {}).get("closed_at"):
            return detail(store, tab_id)                   # ein anderes Gerät hat ihn eben geschlossen
        raise TabError(t("err.tab.changed"), 409)
    return detail(store, tab_id)


def transfer(store: Store, *, tab_id: int, owner: str, owner_name: str, by: str) -> None:
    tab = _open_tab(store, tab_id)
    store.batch([("UPDATE tabs SET owner=?, owner_name=? WHERE id=? AND closed_at IS NULL", (owner, owner_name, tab_id)),
                 lb.audit_stmt(by, "tab_transfer", tab=tab_id, table=tab["table_label"], to=owner_name,
                               before=tab.get("owner_name"))], transaction=True)


def unpaid(store: Store, *, tab_id: int, by_user: str, note: str | None) -> list[int]:
    """Gäste sind ohne zu bezahlen gegangen: alles Offene als Storno «Nicht bezahlt» (ohne Storno-Bons), dann zu.
    Nur ohne Zahlung – sonst einzelne Positionen in den Bestellungen stornieren und den Rest abrechnen."""
    from . import ledger
    tab = _open_tab(store, tab_id)
    s0 = _sums(store, [tab_id])[tab_id]                    # ein Stand: Anzahl, bezahlt und welche Bestellungen
    if s0["paid"]:
        raise TabError(t("err.tab.unpaid_partly"), 409)
    # T43: alle Stornos und das Schliessen in EINEM Batch. Jedes Storno prüft beim Schreiben, dass am Tisch keine
    # Zahlung steht und keine Bestellung dazukam – sonst scheitert alles und nichts bleibt storniert.
    guard = ("NOT EXISTS (SELECT 1 FROM tab_payments WHERE tab_id=? AND undone_at IS NULL) AND "
             "(SELECT COUNT(*) FROM orders WHERE tab_id=?)=?", (tab_id, tab_id, s0["orders"]))
    stmts: list = []
    for oid in s0["order_ids"]:                            # nur, was gelesen wurde – nie eine eben dazugekommene
        try:
            ledger.cancel(store, order_id=oid, lines=None, reason="unpaid", note=note, by_user=by_user,
                          client_uuid=f"tab-{tab_id}-unpaid-{oid}", bons=False, guard=guard, collect=stmts)
        except ledger.LedgerError as e:
            if str(e) != t("err.ledger.nothing_left"):
                raise TabError(str(e), 409) from None
    stmts += [("UPDATE tabs SET closed_at=?, closed_by=?, close_reason='unpaid', open_key=NULL WHERE id=? AND "
               f"closed_at IS NULL AND {guard[0]}", (now_iso(), by_user, tab_id, *guard[1])),
              _if_changed(lb.audit_stmt(by_user, "tab_unpaid", tab=tab_id, table=tab["table_label"], note=note))]
    try:
        res = store.batch(stmts, transaction=True)
    except StoreError as e:
        if ledger.GUARD_FAILED in str(e) or e.is_unique_violation:     # Zahlung/Bestellung/anderes Storno zugleich
            raise TabError(t("err.tab.changed"), 409) from None
        raise
    if not res[-2].rows_affected:
        _open_tab(store, tab_id)
        raise TabError(t("err.tab.changed"), 409)
    return []


def storno(store: Store, *, tab_id: int, lines: dict[int, int], reason: str, note: str | None, by_device: str,
           client_uuid: str) -> dict:
    """Storno am offenen Tisch (Handy mit Storno-Recht): lines = {Nummer der zusammengefassten Position: Menge}. Gebucht
    wird auf die Bestellungen dahinter, die neueste zuerst – je Bestellung ein Storno mit Storno-Bon wie gewohnt, alles
    in EINEM Batch. Nicht stornierbar: Stück, die schon eine Person «Einzeln» bezahlt hat, und mehr als noch offen ist
    (sonst wäre zu viel bezahlt). Gleiche client_uuid nochmals: nichts Neues, Betrag wie beim ersten Mal."""
    from . import ledger
    # nur Stornos dieses Tisches mit genau diesem Präfix (kein LIKE: «%» oder «_» in der Nummer zählen nicht als Muster)
    pre = f"{client_uuid}:"
    dup = store.query("SELECT c.amount_cents FROM cancellations c JOIN orders o ON o.id=c.order_id WHERE o.tab_id=? AND "
                      "substr(c.client_uuid, 1, ?)=?", (tab_id, len(pre), pre))
    if dup:
        return {"amount_cents": sum(d["amount_cents"] for d in dup), "duplicate": True, "printer_ids": [],
                "order_ids": []}
    _open_tab(store, tab_id)
    # Zahlungen ZUERST und in EINER Abfrage lesen: Zähler, Summe und die «Einzeln» bezahlten Stück stammen so sicher
    # aus demselben Stand (auch bei einem Wechsel des Leaders zwischen zwei Abfragen). Ändert sich danach etwas – auch
    # zurückgenommen und gleich hoch neu gebucht –, fällt es im Schreibbefehl auf
    pays = store.query("SELECT amount_cents, items, undone_at FROM tab_payments WHERE tab_id=?", (tab_id,))
    live = [p for p in pays if p["undone_at"] is None]
    n_pay = {"n": len(pays), "a": len(live)}
    paid_qty: dict[int, int] = {}
    for p in live:
        try:
            for k, q in (json.loads(p["items"]) if p["items"] else {}).items():
                paid_qty[int(k)] = paid_qty.get(int(k), 0) + int(q)
        except (ValueError, TypeError):
            continue
    s = _sums(store, [tab_id])[tab_id]
    s["paid"] = sum(int(p["amount_cents"]) for p in live)  # aus demselben Stand wie oben
    s["open"] = max(0, s["net"] - s["paid"])
    orders = store.query("SELECT id, no_print FROM orders WHERE tab_id=? ORDER BY id", (tab_id,))
    items, parts = _merged(store, orders)
    by_id = {i["id"]: i for i in items}
    plan: dict[int, dict[int, int]] = {}                   # order_id -> {line_no: Menge}
    amount = 0
    for mid, qty in lines.items():
        it = by_id.get(mid)
        if not it:
            raise TabError(t("err.tab.storno_line"), 422)
        free = max(0, it["qty"] - paid_qty.get(mid, 0))
        if qty > free:
            raise TabError(t("err.tab.storno_too_many", name=it["name"], count=free), 409)
        amount += it["price"] * qty
        for part in reversed(parts[mid]):                  # die neueste Bestellung zuerst
            take = min(part["left"], qty)
            if take > 0:
                plan.setdefault(part["order_id"], {})
                plan[part["order_id"]][part["line_no"]] = plan[part["order_id"]].get(part["line_no"], 0) + take
                qty -= take
    if not plan:
        raise TabError(t("err.ledger.nothing_left"), 422)
    if amount > s["open"]:
        raise TabError(t("err.tab.storno_paid", open=lb_money(s["open"])), 409)
    # Beim Schreiben geprüft (T40), für jedes Storno im Batch: Tisch offen, keine Bestellung dazu, Zahlungen unverändert
    # (Anzahl aller und der gültigen – auch zurückgenommen und gleich hoch neu gebucht fällt auf) und kein anderes
    # Storno am Tisch seit dem Lesen (Summe ohne die eigenen dieses Batches). Sonst bleibt nichts storniert.
    guard = ("EXISTS (SELECT 1 FROM tabs WHERE id=? AND closed_at IS NULL) AND (SELECT COUNT(*) FROM orders WHERE "
             f"tab_id=?)=? AND {_PAID}=? AND (SELECT COUNT(*) FROM tab_payments WHERE tab_id=?)=? AND "
             "(SELECT COUNT(*) FROM tab_payments WHERE tab_id=? AND undone_at IS NULL)=? AND "
             "COALESCE((SELECT SUM(c.amount_cents) FROM cancellations c JOIN orders o ON o.id=c.order_id WHERE o.tab_id=? "
             "AND substr(c.client_uuid, 1, ?)<>?), 0)=?",
             (tab_id, tab_id, s["orders"], tab_id, s["paid"], tab_id, int(n_pay["n"]), tab_id, int(n_pay["a"]),
              tab_id, len(pre), pre, s["storno"]))
    stmts: list = []
    printers: set[int] = set()
    for oid in sorted(plan):
        try:
            r = ledger.cancel(store, order_id=oid, lines=plan[oid], reason=reason, note=note, by_device=by_device,
                              client_uuid=f"{client_uuid}:{oid}", guard=guard, collect=stmts)
        except ledger.LedgerError as e:
            raise TabError(str(e), 409) from None
        printers.update(r["printer_ids"])
    try:
        store.batch(stmts, transaction=True)
    except StoreError as e:
        if ledger.GUARD_FAILED in str(e) or e.is_unique_violation:
            raise TabError(t("err.tab.changed"), 409) from None
        if ledger.CLOSED_MEANWHILE in str(e):
            raise TabError(t("err.ledger.closed"), 409) from None
        raise
    return {"amount_cents": amount, "duplicate": False, "printer_ids": sorted(printers), "order_ids": sorted(plan)}


def lb_money(c: int) -> str:
    return f"{c / 100:.2f}"


# -- Handy ----------------------------------------------------------------------------------------------
async def _phone(request: Request) -> tuple[dict | None, str | None]:
    info = devices.client_cert(request)
    return info, await db(owner_of, rt.store, info)


def _app_name(request: Request) -> str:
    from urllib.parse import unquote
    return " ".join(unquote(request.headers.get("X-Festpos-Device", "")).split())[:40]


@router.get("/api/tabs", dependencies=[Depends(devices.phone_only)])
async def phone_tabs(request: Request, test: bool = False):
    """Offene Tische (für Tischplan und Tischwahl): wem sie gehören, wie viel offen ist, darf ich abrechnen."""
    info, me = await _phone(request)

    def run():
        out = open_tabs(rt.store, test=test, me=me)
        owners = {r["id"]: r for r in rt.store.query("SELECT id, owner FROM tabs WHERE closed_at IS NULL")}
        for x in out:
            x["can_pay"] = may_settle(rt.store, owners.get(x["id"]) or {}, info)
        return {"tabs": out, **config(rt.store)}
    return await db(run)


@router.get("/api/tabs/{tab_id}", dependencies=[Depends(devices.phone_only)])
async def phone_tab(tab_id: int, request: Request):
    info, me = await _phone(request)

    def run():
        d = detail(rt.store, tab_id, me)
        d["can_pay"] = may_settle(rt.store, rt.store.query_one("SELECT owner FROM tabs WHERE id=?", (tab_id,)), info)
        return d
    try:
        return await db(run)
    except TabError as e:
        raise _http(e) from None


class PayIn(BaseModel):
    key: str = Field(min_length=8, max_length=64)
    amount_cents: int = Field(gt=0, le=MAX_CENTS)
    payment: str | None = Field(default=None, pattern="^(cash|card|twint|other)$")
    tip_cents: int = Field(default=0, ge=0, le=100_000)
    part: int | None = Field(default=None, ge=1, le=99)
    items: dict[str, int] | None = Field(default=None, max_length=500)     # «Einzeln»: {Nummer der Position: Stück}

    @field_validator("items")
    @classmethod
    def _items(cls, v):
        if v is not None and not all(k.isascii() and k.isdigit() and len(k) <= 6 and 1 <= q <= 999 for k, q in v.items()):
            raise ValueError("items")
        return v


async def _check_right(tab_id: int, info: dict | None) -> None:
    tab = await db(rt.store.query_one, "SELECT * FROM tabs WHERE id=?", (tab_id,))
    if not tab:
        raise HTTPException(404, t("err.tab.unknown"))
    if not await db(may_settle, rt.store, tab, info):
        raise HTTPException(403, t("err.tab.no_right", owner=tab.get("owner_name") or "?"))


@router.post("/api/tabs/{tab_id}/pay", dependencies=[Depends(devices.phone_only)])
async def phone_pay(tab_id: int, p: PayIn, request: Request):
    info, me = await _phone(request)
    await _check_right(tab_id, info)
    by = lb.device_label(info["name"] if info else None, _app_name(request)) or "?"
    try:
        await db(pay, rt.store, tab_id=tab_id, key=p.key, amount=p.amount_cents, payment=p.payment, tip=p.tip_cents,
                 part=p.part, items=p.items, by_device=by)
        return await db(detail, rt.store, tab_id, me)
    except TabError as e:
        raise _http(e) from None


class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=64)


@router.post("/api/tabs/{tab_id}/undo", dependencies=[Depends(devices.phone_only)])
async def phone_undo(tab_id: int, k: KeyIn, request: Request):
    info, me = await _phone(request)
    await _check_right(tab_id, info)
    try:
        await db(undo_payment, rt.store, tab_id=tab_id, key=k.key, by=f"device:{info['name'] if info else '?'}")
        return await db(detail, rt.store, tab_id, me)
    except TabError as e:
        raise _http(e) from None


@router.post("/api/tabs/{tab_id}/close", dependencies=[Depends(devices.phone_only)])
async def phone_close(tab_id: int, request: Request):
    info, me = await _phone(request)
    await _check_right(tab_id, info)
    try:
        return await db(close, rt.store, tab_id=tab_id, by=f"device:{info['name'] if info else '?'}")
    except TabError as e:
        raise _http(e) from None


class TabStornoLine(BaseModel):
    id: int = Field(ge=1)
    qty: int = Field(ge=1, le=999)


class TabStornoIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64)
    lines: list[TabStornoLine] = Field(min_length=1, max_length=200)
    reason: str = Field(pattern="^(" + "|".join(lb.REASONS) + ")$")
    note: str = Field(default="", max_length=80)


@router.post("/api/tabs/{tab_id}/storno", dependencies=[Depends(devices.phone_only)])
async def phone_storno(tab_id: int, x: TabStornoIn, request: Request):
    """Storno beim Abrechnen am offenen Tisch – nur mit Storno-Recht (wie /api/orders/storno) und Recht am Tisch."""
    from .ledger import _gast_refund, _storno_device
    info = await _storno_device(request)
    await _check_right(tab_id, info)
    me = await db(owner_of, rt.store, info)
    lines: dict[int, int] = {}
    for ln in x.lines:
        lines[ln.id] = lines.get(ln.id, 0) + ln.qty
    try:
        res = await db(storno, rt.store, tab_id=tab_id, lines=lines, reason=x.reason, note=x.note or None,
                       by_device=lb.device_label(info["name"], _app_name(request)), client_uuid=x.client_uuid)
    except TabError as e:
        raise _http(e) from None
    for pid in res["printer_ids"]:
        rt.spooler.notify(pid)
    for oid in res["order_ids"]:
        await db(_gast_refund, oid)
    return {**res, "tab": await db(detail, rt.store, tab_id, me)}


# -- Verwaltung -----------------------------------------------------------------------------------------
def _actor(request: Request) -> str:
    from .ledger import actor_of
    return actor_of(request)


@admin_router.get("/tabs")
async def admin_tabs():
    def run():
        out = open_tabs(rt.store, test=False)
        idle = idle_minutes(rt.store)
        return {"tabs": out, "idle": idle, **config(rt.store)}
    return await db(run)


@admin_router.get("/tabs/{tab_id}")
async def admin_tab(tab_id: int):
    try:
        return await db(detail, rt.store, tab_id)
    except TabError as e:
        raise _http(e) from None


class AdminPayIn(BaseModel):
    payment: str | None = Field(default=None, pattern="^(cash|card|twint|other)$")


@admin_router.post("/tabs/{tab_id}/settle")
async def admin_settle(tab_id: int, p: AdminPayIn, request: Request):
    """Rest bezahlt (mit dieser Zahlart) und Tisch abschliessen."""
    actor = _actor(request)

    def run():
        s = _sums(rt.store, [tab_id]).get(tab_id)
        if s and s["open"] > 0:
            pay(rt.store, tab_id=tab_id, key=f"admin-{uuid_mod.uuid4()}", amount=s["open"], payment=p.payment, by_user=actor)
        return close(rt.store, tab_id=tab_id, by=actor)
    try:
        return await db(run)
    except TabError as e:
        raise _http(e) from None


class TransferIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)


@admin_router.post("/tabs/{tab_id}/transfer")
async def admin_transfer(tab_id: int, x: TransferIn, request: Request):
    """Tisch einem anderen Handy übergeben (Akku leer, Schichtwechsel)."""
    from . import device_ids

    def run():
        ph = next((d for d in device_ids.phones_raw(rt.store) if d.get("cert") == x.device_id), None)
        if not ph:
            raise TabError(t("err.tab.device_unknown"), 404)
        transfer(rt.store, tab_id=tab_id, owner=x.device_id, owner_name=ph.get("name") or x.device_id, by=_actor(request))
        return detail(rt.store, tab_id)
    try:
        return await db(run)
    except TabError as e:
        raise _http(e) from None


class UnpaidIn(BaseModel):
    note: str = Field(default="", max_length=80)


@admin_router.post("/tabs/{tab_id}/unpaid")
async def admin_unpaid(tab_id: int, x: UnpaidIn, request: Request):
    try:
        await db(unpaid, rt.store, tab_id=tab_id, by_user=_actor(request), note=x.note or None)
        return await db(detail, rt.store, tab_id)
    except TabError as e:
        raise _http(e) from None

