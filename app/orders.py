"""Bestellung anlegen: Validierung, Snapshot und Druckjobs in EINEM atomaren Batch.

Kein interaktiver Transaktionsablauf nötig (rqlite-tauglich): alle Fremdschlüssel werden per
Subquery über die eindeutige client_uuid aufgelöst.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ledger_base as lb, stock
from .i18n import t
from .options import clean_choice_list, clean_note, label_choices, loads as load_options
from .print_service import plan_jobs
from .store import Stmt, Store, StoreError, now_iso


class OrderError(ValueError):
    def __init__(self, msg: str, stock: list[dict] | None = None, code: str | None = None):
        super().__init__(msg)
        self.stock = stock or []                    # ausverkauft / nicht genug: [{product_id, name, left}]
        self.code = code                            # price: Summe stimmt nicht (expect_total)


@dataclass
class OrderResult:
    order_id: int
    table_label: str
    total_cents: int
    created_at: str
    duplicate: bool = False                       # client_uuid schon bekannt (Retry vom Handy)
    printer_ids: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


_OID = "(SELECT id FROM orders WHERE client_uuid=?)"


def delivery_times(store: Store) -> list[str]:
    """Lieferzeitpunkte aus der Einstellung, z.B. ["Sofort", "Pause", "Schluss"]. Der erste ist der Standard."""
    raw = store.setting("delivery_times", "Sofort, Pause, Schluss")
    out = []
    for part in raw.replace(";", ",").split(","):
        part = " ".join(part.split())[:20]
        if part and part.lower() not in (x.lower() for x in out):
            out.append(part)
    return out or ["Sofort"]


def allowed_times(product: dict, times: list[str]) -> list[str]:
    """Erlaubte Lieferzeitpunkte eines Produkts (Reihenfolge wie in der Einstellung). [] = keine Lieferzeit."""
    if not product.get("delivery"):
        return []
    raw = product.get("delivery_allowed")
    if not raw:
        return list(times)
    want = {t.strip().lower() for t in raw.split(",") if t.strip()}
    return [t for t in times if t.lower() in want] or list(times)


def resolve_when(product: dict, when: str, times: list[str]) -> str | None:
    """Gewünschte Lieferzeit -> gespeicherter Wert (None = Standard/sofort). Unbekannt oder nicht erlaubt:
    Vorgabe des Produkts (Standard, falls erlaubt, sonst der erste erlaubte Zeitpunkt)."""
    allowed = allowed_times(product, times)
    if not allowed:
        return None
    match = next((t for t in allowed if t.lower() == (when or "").lower()), None)
    if match is None:
        match = times[0] if times[0] in allowed else allowed[0]
    return None if match == times[0] else match


def _norm_line(line) -> tuple[int, int, tuple[str, ...], str, str]:
    """(pid, qty) oder {"product_id", "qty", "options": [...], "note": "", "when": ""}
    -> (pid, qty, options, note, when)"""
    if isinstance(line, dict):
        return (int(line["product_id"]), int(line["qty"]),
                tuple(clean_choice_list(line.get("options"), dedupe=False)), clean_note(line.get("note")),
                " ".join(str(line.get("when") or "").split())[:20])
    pid, qty = line[0], line[1]
    return int(pid), int(qty), (), "", ""


def create_order(store: Store, *, client_uuid: str, table_label: str,
                 lines: list, device: str | None = None, test: bool = False, print_test: bool = True,
                 payment: str | None = None, device_cert: str | None = None, tab_owner: str | None = None,
                 no_print: bool = False, open_tab: bool = True, prices: dict[int, int] | None = None,
                 expect_total: int | None = None, extra: list[Stmt] | None = None) -> OrderResult:
    """lines: Positionen. Gleiches Produkt mit unterschiedlichen Optionen = eigene Positionen
    (z.B. 2x Kaffee + 1x Kaffee "2 Zucker"). Identische Positionen werden zusammengezählt.
    test: Übungsbestellung aus dem Testmodus der App – zählt nirgends mit; Bons nur mit print_test
    (Einstellung «Testbestellungen drucken»), dann gross als TEST gekennzeichnet.
    payment: Zahlart, falls an der Kasse schon gewählt, bevor die Bestellung beim Server ankam.
    tab_owner: Gerät (app/tabs.py) – mit «Tisch offen lassen» kommt die Bestellung auf den offenen Tisch.
    no_print: «Nachbuchen ohne Bon» – war schon geliefert: keine Bons, sonst wie jede Bestellung (Umsatz, MWST,
    Restmenge, offener Tisch).
    open_tab=False: nie auf einen offenen Tisch – schon bezahlt (QR-Bestellung, T38).
    prices: {Produkt: Rappen} – Preise zum Zeitpunkt, als der Gast bestellt hat (QR-Bestellung: er hat sie bezahlt).
    expect_total: Summe muss genau stimmen, sonst OrderError (nichts angelegt).
    extra: weitere Schreibbefehle im SELBEN Batch (T43: QR-Bestellung – Verknüpfung und Payrexx-Buchung entstehen
    zusammen mit der Bestellung). Die Bestellung finden sie über (SELECT id FROM orders WHERE client_uuid=?)."""
    table_label = (table_label or "").strip()
    client_uuid = (client_uuid or "").strip()
    if not table_label:
        raise OrderError(t("err.order.table_missing"))
    if not client_uuid:
        raise OrderError(t("err.order.uuid_missing"))
    qty_by_key: dict[tuple[int, tuple[str, ...], str, str], int] = {}   # Reihenfolge wie im Warenkorb
    for line in lines:
        key = _norm_line(line)
        if key[1] > 0:
            k = (key[0], key[2], key[3], key[4])
            qty_by_key[k] = qty_by_key.get(k, 0) + key[1]
    if not qty_by_key:
        raise OrderError(t("err.order.empty"))
    if any(q > 999 for q in qty_by_key.values()):
        raise OrderError(t("err.order.qty"))

    existing = _existing(store, client_uuid)
    if existing:
        return existing
    table_label = _check_table(store, table_label)

    pids = {k[0] for k in qty_by_key}
    times = delivery_times(store)
    marks = ",".join("?" * len(pids))
    products = {p["id"]: p for p in store.query(
        f"SELECT p.*, c.name AS category, c.sort AS cat_sort, c.vat_code AS cat_vat_code FROM products p "
        f"LEFT JOIN categories c ON c.id = p.category_id WHERE p.id IN ({marks}) AND p.active=1", tuple(pids))}
    missing = pids - set(products)
    if missing:
        raise OrderError(t("err.order.unknown_products", ids=", ".join(str(x) for x in sorted(missing))))
    need: dict[int, int] = {}
    for k, qty in qty_by_key.items():
        need[k[0]] = need.get(k[0], 0) + qty
    short = stock.problems(products, need)             # ausverkauft / nicht mehr genug (auch im Testmodus): alle melden
    if short:
        raise OrderError(stock.message(short), short)

    prows = store.query("SELECT id, enabled FROM printers")
    printer_ids = {r["id"] for r in prows}
    fallback = default_printer(store, printer_ids, {r["id"] for r in prows if r["enabled"]})
    merged: dict[tuple, int] = {}
    for (pid, opts, note, when), qty in qty_by_key.items():
        when = resolve_when(products[pid], when, times)       # nur erlaubte Zeitpunkte, sonst Vorgabe
        opts = tuple(label_choices(load_options(products[pid].get("options")), list(opts)))
        merged[(pid, opts, note, when)] = merged.get((pid, opts, note, when), 0) + qty
    if any(q > 999 for q in merged.values()):          # auch nach dem Zusammenfassen gleicher Positionen (REV-038)
        raise OrderError(t("err.order.qty"))
    # MWST zum Bestellzeitpunkt mitspeichern (ändert sich ein Satz, bleiben alte Bestellungen richtig)
    rates = lb.vat_rates_on(store) if lb.vat_enabled(store) else {}
    items = []
    for (pid, opts, note, when), qty in merged.items():
        p = products[pid]
        vat_code = (p.get("vat_code") or p.get("cat_vat_code") or "normal") if rates else None
        price = prices.get(pid, p["price_cents"]) if prices else p["price_cents"]
        items.append(dict(delivery=when, delivery_rank=times.index(when) if when else 0, product_id=pid, name=p["name"], price_cents=price, qty=qty,
                          printer_id=p["printer_id"] if p["printer_id"] in printer_ids else fallback,
                          single_ticket=bool(p["single_ticket"]), sort=p["sort"],
                          options=", ".join(opts) or None, note=note or None,
                          category=p["category"], cat_sort=p["cat_sort"] if p["cat_sort"] is not None else 9999,
                          vat_code=vat_code, vat_bp=rates.get(vat_code) if vat_code else None))
    # nach Lieferzeit, Kategorie, Produkt; gleiches Produkt mit verschiedenen Optionen in Warenkorb-Reihenfolge
    items.sort(key=lambda i: (i["delivery_rank"], i["cat_sort"], (i["category"] or "").lower(), i["sort"],
                              i["name"].lower()))
    for n, it in enumerate(items, start=1):
        it["line_no"] = n
    total = sum(i["price_cents"] * i["qty"] for i in items)
    if expect_total is not None and total != expect_total:
        raise OrderError(t("err.order.total_changed"), code="price")
    jobs, warnings = plan_jobs(items, printer_ids)
    if (test and not print_test) or no_print:          # Übung ohne Druck, nachgebucht ohne Bon
        jobs, warnings = [], []
    ts = now_iso()
    if payment not in lb.PAYMENTS:
        payment = None
    device_cert = (device_cert or "").strip()[:64] or None
    order_row = dict(client_uuid=client_uuid, table_label=table_label, device=device, total_cents=total,
                     created_at=ts, test=int(bool(test)), device_cert=device_cert)
    digest = lb.order_digest(order_row, items)

    from . import tabs                                 # offener Tisch: anlegen, falls es keinen gibt (gleicher Batch)
    tab = tabs.order_stmts(store, table_label=table_label, test=test, owner=tab_owner,
                           owner_name=lb.device_label(device_cert, device), ts=ts) if open_tab else None
    stmts = tab[0] if tab else []
    at_order = len(stmts)                              # Index der Bestellung im Batch (doppelt gesendet: siehe unten)
    stmts += [("INSERT INTO orders(client_uuid, table_label, device, device_cert, total_cents, created_at, test, payment, "
               "digest, tab_id, no_print) VALUES (?,?,?,?,?,?,?,?,?,(SELECT id FROM tabs WHERE open_key=?),?) RETURNING id",
               (client_uuid, table_label, device, device_cert, total, ts, int(bool(test)), payment, digest,
                tab[1] if tab else None, int(bool(no_print))))]
    stmts += [(f"INSERT INTO order_items(order_id, product_id, name, price_cents, qty, printer_id, single_ticket, sort, "
               f"line_no, options, note, category, cat_sort, delivery, vat_code, vat_bp) "
               f"VALUES ({_OID},?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (client_uuid, i["product_id"], i["name"], i["price_cents"], i["qty"], i["printer_id"],
                int(i["single_ticket"]), i["sort"], i["line_no"], i["options"], i["note"], i["category"],
                i["cat_sort"], i["delivery"], i["vat_code"], i["vat_bp"])) for i in items]
    # Restmenge herunterzählen (nicht bei Übungen). Reicht sie inzwischen nicht mehr (anderes Handy war schneller),
    # schlägt der CHECK (stock >= 0) fehl und die ganze Bestellung wird nicht angelegt – keine Bons.
    counted = [] if test else [pid for pid in need if products[pid].get("stock") is not None]
    stmts += [("UPDATE products SET stock = stock - ? WHERE id=? AND stock IS NOT NULL", (need[pid], pid))
              for pid in counted]
    for j in jobs:
        if j.kind == "table":
            stmts.append((f"INSERT INTO print_jobs(order_id, station_id, printer_id, kind, n, of_n, delivery, created_at) "
                          f"VALUES ({_OID},?,?,'table',?,?,?,?)",
                          (client_uuid, j.station_id, j.station_id, j.n, j.of_n, j.delivery, ts)))
        else:
            stmts.append((f"INSERT INTO print_jobs(order_id, station_id, printer_id, kind, item_id, n, of_n, delivery, "
                          f"created_at) VALUES ({_OID},?,?,'single',"
                          f"(SELECT id FROM order_items WHERE order_id={_OID} AND line_no=?),?,?,?,?)",
                          (client_uuid, j.station_id, j.station_id, client_uuid, j.line_no, j.n, j.of_n,
                           j.delivery, ts)))
    stmts += list(extra or [])
    try:
        res = store.batch(stmts, transaction=True)
    except StoreError as e:
        if e.is_unique_violation and e.index == at_order:  # paralleler Retry hat gewonnen
            dup = _existing(store, client_uuid)
            if dup:
                return dup
        if counted and "CHECK constraint failed" in str(e):  # Restmenge reicht nicht mehr: aktuellen Stand melden
            fresh = {r["id"]: {**products[r["id"]], **r} for r in store.query(
                f"SELECT id, soldout, stock FROM products WHERE id IN ({marks})", tuple(pids))}
            short = stock.problems(fresh, need) or [{"product_id": p, "name": products[p]["name"], "left": 0}
                                                    for p in counted]
            raise OrderError(stock.message(short), short) from None
        raise

    return OrderResult(res[at_order].rows[0]["id"], table_label, total, ts,
                       printer_ids={j.station_id for j in jobs}, warnings=warnings)


NACHBUCHEN = ("off", "all", "storno")


def nachbuchen_mode(store: Store) -> str:
    """Verwaltung › Einstellungen › Kasse: «Nachbuchen ohne Bon» aus | alle Bedienungen | nur mit Storno-Recht."""
    v = store.setting("nachbuchen", "all")
    return v if v in NACHBUCHEN else "all"


def may_nachbuchen(store: Store, cert: dict | None) -> bool:
    mode = nachbuchen_mode(store)
    if mode == "off":
        return False
    return mode == "all" or (bool(cert) and lb.device_role(store, cert) in lb.STORNO_ROLES)


def tables_mode(store: Store) -> str:
    """free = jede Eingabe | warn = App fragt nach, Server nimmt an | strict = nur erfasste Tische."""
    mode = store.setting("tables_mode", "")
    if mode not in ("free", "warn", "strict"):
        mode = "strict" if store.setting("tables_strict", "0") == "1" else "free"   # ältere Einstellung
    return mode


def _check_table(store: Store, label: str) -> str:
    """Erzwingen: nur erfasste Tische (Schreibweise wie erfasst). Ohne erfasste Tische greift die
    Prüfung nicht – sonst könnte gar nicht mehr bestellt werden. Sonst: erfasste Schreibweise übernehmen."""
    row = store.query_one("SELECT label FROM tables WHERE label=? COLLATE NOCASE", (label,))
    if row:
        return row["label"]
    if tables_mode(store) != "strict":
        return label
    if store.scalar("SELECT COUNT(*) FROM tables"):
        word = t("common.word.seat" if store.setting("table_word", "Tisch") == "Platz" else "common.word.table")
        raise OrderError(t("err.order.table_unknown", word=word, table=label))
    return label


def default_printer(store: Store, printer_ids: set[int] | None = None, enabled_ids: set[int] | None = None) -> int | None:
    """Drucker für Produkte ohne eigenen Drucker: Einstellung 'default_printer_id', sonst der erste angelegte
    Drucker (kleinste ID) – jeweils bevorzugt ein EINGESCHALTETER (REV-045)."""
    if printer_ids is None or enabled_ids is None:
        rows = store.query("SELECT id, enabled FROM printers")
        printer_ids = {r["id"] for r in rows}
        enabled_ids = {r["id"] for r in rows if r["enabled"]}
    if not printer_ids:
        return None
    try:
        chosen = int(store.setting("default_printer_id", "") or 0)
    except ValueError:
        chosen = 0
    usable = enabled_ids or printer_ids                   # alle aus: lieber warten als Bons verlieren
    if chosen in usable:
        return chosen
    return min(usable)


def _existing(store: Store, client_uuid: str) -> OrderResult | None:
    row = store.query_one("SELECT id, table_label, total_cents, created_at FROM orders WHERE client_uuid=?",
                          (client_uuid,))
    if not row:
        return None
    return OrderResult(row["id"], row["table_label"], row["total_cents"], row["created_at"], duplicate=True)
