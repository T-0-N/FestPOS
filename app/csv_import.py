"""Preisliste als CSV importieren/exportieren (Excel-tauglich).

Format (Kopfzeile Pflicht, Reihenfolge egal, Gross-/Kleinschreibung egal):
    name;preis;kategorie;drucker;einzelbon;sort;aktiv;optionen;freitext;farbe
    Bier 5dl;6.50;Getränke;Getränkeausgabe;0;1;1;;
    Kaffee;3,50;Getränke;Getränkeausgabe;0;2;1;"Zucker*: ohne|1 Zucker|2 Zucker; Milch: mit|ohne";ja

optionen: Gruppen mit ";" getrennt, Auswahl mit "|", "*" = Pflicht, "+" = Mehrfachauswahl
(Excel setzt die Zelle automatisch in Anführungszeichen). freitext: ja = Bemerkung erlaubt.
farbe: eigene Kachelfarbe "#e4002b", leer = Farbe der Kategorie.
lieferzeit: leer = immer sofort, ja = wählbar, Pflicht = muss gewählt werden, "Pause|Schluss" = nur diese
(einer allein = erzwungen), angehängtes * = Pflicht, z.B. "Pause|Schluss*".

Pflicht: name, preis. Rest optional. Abgleich über den Namen (ohne Gross/Klein).
Ablauf: preview() zeigt, was passieren würde -> apply() führt es atomar aus.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from . import i18n, options as options_mod
from . import ledger_base as lb
from .orders import delivery_times
from .store import Store, Stmt

ALIASES = {
    "name": "name", "produkt": "name", "artikel": "name", "bezeichnung": "name",
    "preis": "price", "price": "price", "betrag": "price",
    "kategorie": "category", "category": "category", "gruppe": "category",
    "drucker": "printer", "printer": "printer", "station": "printer",
    "einzelbon": "single", "single": "single", "einzel": "single",
    "sort": "sort", "sortierung": "sort", "reihenfolge": "sort",
    "aktiv": "active", "active": "active",
    "optionen": "options", "options": "options", "varianten": "options",
    "freitext": "free", "bemerkung": "free", "notiz": "free",
    "farbe": "color", "color": "color",
    "lieferzeit": "delivery", "lieferung": "delivery", "spaeter": "delivery", "später": "delivery",
    "bestand": "stock", "restmenge": "stock", "stock": "stock",
    "warnen_ab": "stock_warn", "warnen ab": "stock_warn", "warnschwelle": "stock_warn", "stock_warn": "stock_warn",
    "ausverkauft": "soldout", "soldout": "soldout",
}
STOCK_MAX = 99_999
# Grenzen wie in der Verwaltung (catalog.ProductIn / CategoryIn) – der Import darf nicht mehr zulassen (Review 2026-11, N2)
NAME_MAX = 40
CATEGORY_MAX = 32
PRICE_MAX = 1_000_000                             # Rappen (CHF 10'000)
SORT_MAX = 1_000_000
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def parse_count(s: str) -> int | str:
    """Bestand / Warnschwelle: Zahl 0 … 99999, leer = "" (ohne Bestand bzw. keine Warnung)."""
    t = s.strip().replace("'", "").replace("’", "")
    if t == "":
        return ""
    if not t.isdigit() or int(t) > STOCK_MAX:
        raise ValueError(i18n.t("err.csv.count", value=s))
    return int(t)
TRUE = {"1", "ja", "j", "x", "true", "yes", "y", "wahr"}
FALSE = {"0", "nein", "n", "", "false", "no", "falsch", "-"}


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):          # Excel CH speichert "CSV (Trennzeichen-getrennt)" als cp1252
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_price(s: str) -> int:
    t = re.sub(r"(?i)chf|fr\.?|\s|'", "", s)
    if t[:1] in ("-", "–", "−"):                 # «4.–» (Schweizer Schreibweise) ja, «-2.00» nein (REV-046)
        raise ValueError(i18n.t("err.csv.price_negative", value=s))
    t = t.replace("–", "").replace("-", "")
    t = t.replace(",", ".")
    if t.endswith("."):
        t = t[:-1]
    if not re.fullmatch(r"\d+(\.\d{1,2})?", t):
        raise ValueError(i18n.t("err.csv.price_unreadable", value=s))
    franken, _, rappen = t.partition(".")
    cents = int(franken) * 100 + int((rappen + "00")[:2])
    if cents > PRICE_MAX:                        # Tippfehler («6500» statt «6.50») – wie in der Verwaltung
        raise ValueError(i18n.t("err.csv.price_high", value=s, max=f"{PRICE_MAX // 100}.00"))
    return cents


def parse_sort(s: str) -> int:
    v = int(s) if s else 0
    if abs(v) > SORT_MAX:
        raise ValueError(s)
    return v


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(v):
    """Für Excel: Text, der mit = + - @ beginnt, würde als Formel ausgeführt -> ein ' davor (REV-046)."""
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return v


def uncell(v: str) -> str:
    """Gegenstück beim Import: das ' vor = + - @ wieder entfernen (Export -> Excel -> Import bleibt gleich)."""
    return v[1:] if v.startswith("'") and v[1:2] in ("=", "+", "-", "@") else v


def parse_bool(s: str, default: bool) -> bool:
    t = s.strip().lower()
    if t == "":
        return default
    if t in TRUE:
        return True
    if t in FALSE:
        return False
    raise ValueError(i18n.t("err.csv.not_bool", value=s))


@dataclass
class Row:
    line: int
    name: str = ""
    price_cents: int | None = None
    category: str | None = None
    printer: str | None = None
    single: bool | None = None
    sort: int | None = None
    active: bool | None = None
    options: str | None = None                    # Optionen als JSON (None = Spalte fehlt -> unverändert)
    color: str | None = None                      # "#rrggbb", "" = Farbe der Kategorie, None = unverändert
    delivery: str | None = None                   # Lieferzeit als Text (siehe parse_delivery), None = unverändert
    stock: int | str | None = None                # Restmenge, "" = ohne Bestand, None = Spalte fehlt -> unverändert
    stock_warn: int | str | None = None           # Warnschwelle, "" = keine Warnung, None = unverändert
    soldout: bool | None = None                   # ausverkauft, None = unverändert
    action: str = ""                              # new | update | unchanged | error | deactivate
    changes: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    product_id: int | None = None


@dataclass
class ImportPlan:
    rows: list[Row]
    deactivate: list[Row]
    new_categories: list[str]
    file_errors: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.file_errors and not any(r.errors for r in self.rows)

    def summary(self) -> dict:
        c = {k: 0 for k in ("new", "update", "unchanged", "error")}
        for r in self.rows:
            c[r.action] = c.get(r.action, 0) + 1
        return {**c, "deactivate": len(self.deactivate), "new_categories": self.new_categories,
                "file_errors": self.file_errors, "notes": self.notes, "ok": self.ok}


def parse_delivery(text: str, times: list[str]) -> str:
    """CSV-Spalte lieferzeit -> normierter Text: "" (aus), "alle", "alle*", "Pause|Schluss", "Pause|Schluss*".
    Erlaubt: leer/nein/0 = aus, ja/1 = wählbar, pflicht = muss gewählt werden,
    "Pause|Schluss" = nur diese (ein einzelner = erzwungen), angehängtes * = Pflicht."""
    t = (text or "").strip()
    low = t.lower()
    if low in FALSE:
        return ""
    if low in TRUE or low == "alle":              # "alle" = so exportiert delivery_text()
        return "alle"
    if low in ("pflicht", "muss", "alle*"):
        return "alle*"
    req = t.endswith("*")
    names = [x.strip() for x in t.rstrip("*").split("|") if x.strip()]
    bad = [n for n in names if n.lower() not in (x.lower() for x in times)]
    if bad or not names:
        raise ValueError(i18n.t("err.csv.delivery_unknown", value=text, known=", ".join(times)))
    chosen = [x for x in times if x.lower() in {n.lower() for n in names}]
    out = "alle" if len(chosen) == len(times) else "|".join(chosen)
    return out + ("*" if req and len(chosen) > 1 else "")


def delivery_text(row: dict, times: list[str]) -> str:
    """Produkt-Zeile -> Text wie parse_delivery ihn liefert (für Export und Vergleich)."""
    if not row.get("delivery"):
        return ""
    allowed = row.get("delivery_allowed")
    names = [x for x in times if allowed and x.lower() in {a.strip().lower() for a in allowed.split(",")}]
    out = "|".join(names) if names and len(names) < len(times) else "alle"
    return out + ("*" if row.get("delivery_required") and (out == "alle" or len(names) > 1) else "")


def delivery_cols(text: str, times: list[str]) -> tuple[int, str | None, int]:
    """Text -> (delivery, delivery_allowed, delivery_required)"""
    if not text:
        return 0, None, 0
    req = text.endswith("*")
    body = text.rstrip("*")
    allowed = None if body == "alle" else ", ".join(body.split("|"))
    return 1, allowed, int(req)


def preview(store: Store, raw: bytes, deactivate_missing: bool = False) -> ImportPlan:
    text = decode(raw)
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        return ImportPlan([], [], [], [i18n.t("err.csv.empty")])

    cols: dict[int, str] = {}
    unknown = []
    for i, h in enumerate(header):
        key = ALIASES.get(h.strip().lower())
        if key:
            cols[i] = key
        elif h.strip():
            unknown.append(h.strip())
    file_errors = [i18n.t("err.csv.column_missing", column=c) for c in ("name", "price") if c not in cols.values()]
    if file_errors:
        return ImportPlan([], [], [], file_errors + ([i18n.t("err.csv.columns_unknown", columns=", ".join(unknown))]
                                                     if unknown else []))

    products = {p["name"].lower(): p for p in store.query("SELECT * FROM products")}
    times = delivery_times(store)
    categories = {c["name"].lower(): c for c in store.query("SELECT * FROM categories")}
    printers = {p["name"].lower(): p for p in store.query("SELECT * FROM printers")}
    cat_names = {c["id"]: c["name"] for c in categories.values()}
    prn_names = {p["id"]: p["name"] for p in printers.values()}

    rows: list[Row] = []
    seen: set[str] = set()
    new_cats: dict[str, str] = {}
    for lineno, rec in enumerate(reader, start=2):
        if not any(c.strip() for c in rec):
            continue
        vals = {cols[i]: uncell(v.strip()) for i, v in enumerate(rec) if i in cols}
        # Zeilenumbruch/Tabulator in der Zelle: gehört nie in einen Namen. Gibt es das Produkt bzw. die Kategorie genau
        # so schon (früherer Import), bleibt der Name – sonst träfe die Zeile ein anderes Produkt
        for key, known in (("name", products), ("category", categories)):
            if key in vals and vals[key].lower() not in known:
                vals[key] = _CTRL.sub(" ", vals[key]).strip()
        r = Row(lineno, name=vals.get("name", ""))
        try:
            if not r.name:
                raise ValueError(i18n.t("err.csv.name_missing"))
            if r.name.lower() in seen:
                raise ValueError(i18n.t("err.csv.name_duplicate"))
            seen.add(r.name.lower())
            if len(r.name) > NAME_MAX and r.name.lower() not in products:     # bestehende bleiben, wie sie sind
                raise ValueError(i18n.t("err.csv.name_long", max=NAME_MAX))
        except ValueError as e:
            r.errors.append(str(e))
        for key, fn in (("price", parse_price),
                        ("single", lambda s: parse_bool(s, False)),
                        ("active", lambda s: parse_bool(s, True)),
                        ("sort", parse_sort)):
            if key in vals:
                try:
                    setattr(r, {"price": "price_cents"}.get(key, key), fn(vals[key]))
                except ValueError as e:
                    r.errors.append(str(e) if key != "sort" else i18n.t("err.csv.sort_nan", value=vals[key]))
        if vals.get("category"):
            # in Python vergleichen (auch «GETRÄNKE» = «Getränke»; SQLite kennt Gross/Klein nur für A–Z)
            # und die vorhandene Schreibweise übernehmen
            low = vals["category"].lower()
            if low in categories:
                r.category = categories[low]["name"]
            elif len(vals["category"]) > CATEGORY_MAX:
                r.errors.append(i18n.t("err.csv.category_long", value=vals["category"][:CATEGORY_MAX] + "…", max=CATEGORY_MAX))
            else:
                r.category = new_cats.setdefault(low, vals["category"])
        if vals.get("printer"):
            p = printers.get(vals["printer"].lower())
            if p is None:
                r.errors.append(i18n.t("err.csv.printer_unknown", value=vals["printer"],
                                       known=", ".join(prn_names.values()) or "–"))
            else:
                r.printer = p["name"]

        if "options" in vals or "free" in vals:
            try:
                free = parse_bool(vals.get("free", ""), False)
                r.options = options_mod.dumps(options_mod.parse_text(vals.get("options", ""), free)) or ""
            except ValueError as e:
                r.errors.append(i18n.t("err.csv.options", error=e))

        if "color" in vals:
            c = vals["color"].strip().lower()
            if c and not re.fullmatch(r"#?[0-9a-f]{6}", c):
                r.errors.append(i18n.t("err.csv.color", value=vals["color"]))
            else:
                r.color = ("#" + c.lstrip("#")) if c else ""

        if "delivery" in vals:
            try:
                r.delivery = parse_delivery(vals["delivery"], times)
            except ValueError as e:
                r.errors.append(str(e))

        # Bestand (app/stock.py): Spalte vorhanden = überschreiben, auch mit leerer Zelle (= ohne Bestand)
        for key in ("stock", "stock_warn"):
            if key in vals:
                try:
                    setattr(r, key, parse_count(vals[key]))
                except ValueError as e:
                    r.errors.append(str(e))
        if r.stock == "":
            r.stock_warn = ""                          # ohne Bestand: keine Warnschwelle (wie in der Verwaltung)
        if "soldout" in vals:
            try:
                r.soldout = parse_bool(vals["soldout"], False)
            except ValueError as e:
                r.errors.append(str(e))

        existing = products.get(r.name.lower())
        if existing is None and r.price_cents is None and not r.errors:
            r.errors.append(i18n.t("err.csv.price_missing"))
        if r.errors:
            r.action = "error"
        elif existing is None:
            r.action = "new"
        else:
            r.product_id = existing["id"]
            cur = {"price_cents": existing["price_cents"], "category": cat_names.get(existing["category_id"]),
                   "printer": prn_names.get(existing["printer_id"]), "single": bool(existing["single_ticket"]),
                   "sort": existing["sort"], "active": bool(existing["active"]),
                   "options": options_mod.dumps(options_mod.loads(existing["options"])) or "",
                   "color": existing["color"] or "", "delivery": delivery_text(existing, times),
                   "stock": "" if existing.get("stock") is None else existing["stock"],
                   "stock_warn": "" if existing.get("stock_warn") is None else existing["stock_warn"],
                   "soldout": bool(existing.get("soldout"))}
            new = {"price_cents": r.price_cents, "category": r.category, "printer": r.printer,
                   "single": r.single, "sort": r.sort, "active": r.active, "options": r.options,
                   "color": r.color, "delivery": r.delivery, "stock": r.stock, "stock_warn": r.stock_warn,
                   "soldout": r.soldout}
            r.changes = {k: [cur[k], v] for k, v in new.items()
                         if v is not None and (cur[k] != v if k == "options" else
                                               (str(cur[k]).lower() if isinstance(v, str) else cur[k]) !=
                                               (v.lower() if isinstance(v, str) else v))}
            if "options" in r.changes:                 # in der Vorschau lesbar statt JSON
                r.changes["options"] = [options_mod.to_text(options_mod.loads(cur["options"])) or "–",
                                        options_mod.to_text(options_mod.loads(r.options)) or "–"]
                r.changes["options"].append(r.options)
            r.action = "update" if r.changes else "unchanged"
        rows.append(r)

    deactivate = []
    if deactivate_missing:
        deactivate = [Row(0, name=p["name"], product_id=p["id"], action="deactivate")
                      for key, p in products.items() if key not in seen and p["active"]]
    notes = [i18n.t("err.csv.columns_ignored", columns=", ".join(unknown))] if unknown else []
    return ImportPlan(rows, deactivate, list(new_cats.values()), [], notes)


def _count(v: int | str | None) -> int | None:
    return None if v in ("", None) else int(v)


def apply(store: Store, plan: ImportPlan) -> dict:
    if not plan.ok:
        raise ValueError(i18n.t("err.csv.has_errors"))
    times = delivery_times(store)
    # Namen sind aus der Vorschau schon in der gespeicherten Schreibweise – exakt vergleichen
    cat = "(SELECT id FROM categories WHERE name=?)"
    prn = "(SELECT id FROM printers WHERE name=?)"
    # Neue Kategorien in der Reihenfolge, in der sie in der CSV zuerst vorkommen (hinter den bestehenden)
    base = store.scalar("SELECT COALESCE(MAX(sort), 0) FROM categories") or 0
    stmts: list[Stmt] = [("INSERT OR IGNORE INTO categories(name, sort) VALUES (?,?)", (c, base + (i + 1) * 10))
                         for i, c in enumerate(plan.new_categories)]
    for r in plan.rows:
        if r.action == "new":
            stmts.append((f"INSERT INTO products(name, price_cents, category_id, printer_id, single_ticket, sort, active, "
                          f"options, color, delivery, delivery_allowed, delivery_required, stock, stock_warn, soldout) "
                          f"VALUES (?,?,{cat},{prn},?,?,?,?,?,?,?,?,?,?,?)",
                          (r.name, r.price_cents, r.category, r.printer, int(bool(r.single)), r.sort or 0,
                           int(r.active if r.active is not None else True), r.options or None, r.color or None,
                           *delivery_cols(r.delivery or "", times), _count(r.stock),
                           _count(r.stock_warn) if _count(r.stock) is not None else None, int(bool(r.soldout)))))
        elif r.action == "update":
            sets, params = [], []
            for k, (_, v, *raw) in r.changes.items():
                if k == "delivery":
                    sets.append("delivery=?, delivery_allowed=?, delivery_required=?")
                    params.extend(delivery_cols(v, times))
                    continue
                if k == "options":
                    v = raw[0] or None
                if k == "color":
                    v = v or None
                if k in ("stock", "stock_warn"):
                    v = _count(v)
                col, expr = {"price_cents": ("price_cents", "?"), "category": ("category_id", cat),
                             "printer": ("printer_id", prn), "single": ("single_ticket", "?"),
                             "sort": ("sort", "?"), "active": ("active", "?"), "options": ("options", "?"),
                             "color": ("color", "?"), "stock": ("stock", "?"), "stock_warn": ("stock_warn", "?"),
                             "soldout": ("soldout", "?")}[k]
                sets.append(f"{col}={expr}")
                params.append(int(v) if isinstance(v, bool) else v)
            stmts.append((f"UPDATE products SET {', '.join(sets)} WHERE id=?", (*params, r.product_id)))
    stmts += [("UPDATE products SET active=0 WHERE id=?", (r.product_id,)) for r in plan.deactivate]
    store.batch(stmts, transaction=True)
    return plan.summary()


EXPORT_HEADER = ["name", "preis", "kategorie", "drucker", "einzelbon", "sort", "aktiv", "optionen", "freitext", "farbe",
                 "lieferzeit", "bestand", "warnen_ab", "ausverkauft"]


def export_products(store: Store) -> bytes:
    times = delivery_times(store)
    rows = store.query("""
        SELECT p.name, p.price_cents, c.name AS category, pr.name AS printer, p.single_ticket, p.sort, p.active,
               p.options, p.color, p.delivery, p.delivery_allowed, p.delivery_required, p.stock, p.stock_warn, p.soldout
        FROM products p LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN printers pr ON pr.id=p.printer_id
        ORDER BY c.sort, c.name, p.sort, p.name""")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    for r in rows:
        o = options_mod.loads(r["options"])
        w.writerow([csv_cell(x) for x in (
            r["name"], f"{r['price_cents'] // 100}.{r['price_cents'] % 100:02d}", r["category"] or "",
            r["printer"] or "", r["single_ticket"], r["sort"], r["active"], options_mod.to_text(o),
            1 if o and o["free"] else 0, r["color"] or "", delivery_text(r, times),
            "" if r["stock"] is None else r["stock"], "" if r["stock_warn"] is None else r["stock_warn"],
            "ja" if r["soldout"] else "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")      # BOM: Excel erkennt UTF-8 + Umlaute


def export_orders(store: Store) -> bytes:
    rows = store.query("""
        SELECT o.id, o.created_at, o.table_label, o.device, o.device_cert, o.payment, o.closing_id, i.name, i.qty, i.price_cents,
               i.options, i.note, i.delivery, i.vat_bp,
               COALESCE((SELECT SUM(ci.qty) FROM cancellation_items ci WHERE ci.item_id=i.id), 0) AS storno_qty
        FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.test=0
        ORDER BY o.id, COALESCE(i.line_no, 0), i.sort, i.name""")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    # die ersten Spalten wie bisher (Auswertungen in Excel laufen weiter), neue Spalten hinten angehängt
    w.writerow(["bestellung", "zeit", "tisch", "geraet", "produkt", "optionen", "menge", "einzelpreis", "total",
                "storniert", "total_nach_storno", "zahlart", "mwst_satz", "abschluss"])
    for r in rows:
        p = r["price_cents"]
        net = p * (r["qty"] - r["storno_qty"])
        w.writerow([csv_cell(x) for x in (
            r["id"], r["created_at"].replace("T", " "), r["table_label"], lb.device_label(r["device_cert"], r["device"]), r["name"],
            ", ".join(x for x in (r["options"], r["note"], r["delivery"]) if x), r["qty"],
            f"{p // 100}.{p % 100:02d}", f"{p * r['qty'] // 100}.{p * r['qty'] % 100:02d}",
            r["storno_qty"] or "", f"{net // 100}.{net % 100:02d}", r["payment"] or "",
            "" if r["vat_bp"] is None else f"{r['vat_bp'] / 100:.2f}", r["closing_id"] or "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def template(store: Store) -> bytes:
    """Vorlage mit Beispielzeilen, die die echten Drucker-/Kategorienamen dieser Installation verwenden."""
    printers = [r["name"] for r in store.query("SELECT name FROM printers ORDER BY id")] or ["Getränkeausgabe", "Küche"]
    cats = [r["name"] for r in store.query("SELECT name FROM categories ORDER BY sort, name")] or ["Getränke", "Essen"]
    bar, kitchen = printers[0], printers[-1]
    drinks, food = cats[0], cats[-1] if len(cats) > 1 else cats[0]
    rows = [
        ["Bier 5dl", "6.50", drinks, bar, 0, 1, 1, "", 0, "#e0a800", "", "", "", ""],
        ["Mineral 5dl", "4.00", drinks, bar, 0, 2, 1, "", 0, "", "", "", "", ""],
        ["Kaffee", "3.50", drinks, bar, 0, 3, 1, "Zucker*: ohne|1 Zucker|2 Zucker; Milch: mit Milch|ohne Milch", 1, "#8a6d3b", "ja",
         "", "", ""],
        ["Bratwurst mit Bürli", "8.00", food, kitchen, 1, 1, 1, "Sauce+: Senf|Ketchup|ohne Sauce", 0, "", "Pflicht", 120, 15, ""],
        ["Pommes", "5.00", food, kitchen, 0, 2, 1, "", 1, "", "Pause|Schluss", "", "", ""],
    ]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADER)
    w.writerows(rows)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
