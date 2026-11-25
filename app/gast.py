"""QR-Bestellung am Tisch (T22, Variante A: Tunnel, Payrexx, Lieferung an den Tisch, automatische Rückerstattung plus
Knopf, digitaler Beleg).

PAYREXX-UNGETESTET: Noch nie gegen ein echtes Payrexx-Konto gelaufen (Stand Version 1.0), siehe PAYREXX_GEPRUEFT
in app/payrexx_api.py und docs/testplan-payrexx.md.


Der Gast scannt den QR-Code am Tisch, bestellt auf seinem Handy und zahlt bei Payrexx (TWINT, Karte, Apple/Google Pay).
Erst wenn Payrexx die Zahlung bestätigt hat, entsteht die Bestellung in FestPOS und die Bons werden gedruckt – unbezahlte
Bestellungen gibt es nicht. Geht danach etwas schief (inzwischen ausverkauft, Drucker gestört), erstattet FestPOS
automatisch zurück; jedes Storno einer Online-Bestellung (Verwaltung oder Handy mit Storno-Rolle) erstattet den Betrag
ebenfalls zurück.

Sicherheit (deploy/ANLEITUNG.md, «QR-Bestellung am Tisch»):
- Gäste erreichen nie diesen Dienst direkt: Cloudflare Tunnel -> Gästedienst (app/gast_proxy.py, eigener Benutzer,
  Sandbox, nur 127.0.0.1) -> hier, nur /api/gast/…, nur mit dem Gäste-Schlüssel (HMAC aus CLUSTER_SECRET).
- QR-Code pro Tisch signiert: niemand bestellt für einen fremden oder erfundenen Tisch.
- Preise, Summe und Verfügbarkeit rechnet nur der Server; Höchstbetrag und Höchstmenge; wenige offene Zahlungen pro Tisch.
- Bezahlt ist nur, was Payrexx auf Nachfrage als «confirmed» meldet (Webhook ist nur ein Anstoss, nie die Wahrheit).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import ledger
from . import ledger_base as lb
from . import options as options_mod
from . import tips
from . import tunnel
from . import web
from . import payrexx_api as px
from .core import Config, admin, db, log, rt
from .i18n import t
from .orders import OrderError, create_order
from .store import StoreError, now_iso

WAIT_S = 30 * 60                # so lange gilt eine offene Zahlung (Payrexx-Seite «validity» 15 min + Reserve)
RECHECK_S = 3.0                 # höchstens so oft pro Zahlung bei Payrexx nachfragen
MAX_QTY = 20                    # pro Position
MAX_LINES = 30
MAX_WAITING_PER_TABLE = 5
LANGS = ("de", "fr", "it", "en", "rm")
SETTINGS = ("gast_open", "gast_domain", "gast_max_cents", "gast_refund_min", "gast_instance", "gast_key_enc",
            "gast_tunnel_enc")
# Schlüssel des Gästedienstes: pro Pi zufällig (festpos-node), lesbar für festpos-gast und festpos
TOKEN_FILE = Path(os.environ.get("FESTPOS_GAST_TOKEN_FILE", "/run/festpos-gast/token"))

router = APIRouter(prefix="/api/gast")
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Schlüssel: Gästedienst und Tische -------------------------------------------------------------------------
def _mac(purpose: bytes, msg: str) -> bytes:
    return hmac.new(hashlib.sha256(b"festpos-gast\0" + purpose + (Config.cluster_secret or "").encode()).digest(),
                    msg.encode(), hashlib.sha256).digest()


def proxy_token() -> str:
    """Schlüssel, mit dem sich der Gästedienst ausweist (leer: QR-Bestellung auf diesem Pi nicht eingerichtet)."""
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def table_sig(table: str) -> str:
    return base64.urlsafe_b64encode(_mac(b"tisch", table.strip().lower())).decode()[:12]


def table_ok(table: str, sig: str) -> bool:
    return bool(table) and bool(sig) and hmac.compare_digest(table_sig(table), sig)


def gast_only(request: Request) -> None:
    got = request.headers.get("x-festpos-gast") or ""
    want = proxy_token()
    if len(want) < 16 or not Config.cluster_secret or not hmac.compare_digest(got, want):
        raise HTTPException(403, t("err.gast.forbidden"))


# -- Einstellungen -------------------------------------------------------------------------------------------------
def config() -> dict:
    s = rt.store.setting
    key_enc = s("gast_key_enc", "") or ""
    key = px.unseal(key_enc) if key_enc else None
    return {"open": s("gast_open", "0") == "1", "domain": (s("gast_domain", "") or "").strip().lower(),
            "max_cents": int(s("gast_max_cents", "30000") or 30000), "refund_min": int(s("gast_refund_min", "10") or 10),
            "instance": s("gast_instance", "") or "", "key": key, "key_set": bool(key_enc), "key_ok": key is not None}


def base_url(cfg: dict) -> str:
    return f"https://{cfg['domain']}" if cfg["domain"] else ""


def table_url(cfg: dict, table: str) -> str:
    return f"{base_url(cfg)}/?{urlencode({'t': table, 's': table_sig(table)})}"


def closed_reason(cfg: dict | None = None) -> str | None:
    """Warum gerade nicht bestellt werden kann (None = offen)."""
    cfg = cfg or config()
    if not cfg["open"]:
        return "off"
    if not (cfg["instance"] and cfg["key"] and cfg["domain"]):
        return "setup"
    printers = rt.store.query("SELECT id FROM printers WHERE enabled=1")
    if not printers or all(rt.spooler and rt.spooler.is_down(p["id"]) for p in printers):
        return "printer"
    return None


# -- Speisekarte ---------------------------------------------------------------------------------------------------
def _check_table(table: str, sig: str) -> str:
    table = " ".join((table or "").split())[:20]
    if not table_ok(table, sig):
        raise HTTPException(403, t("err.gast.table"))
    row = rt.store.query_one("SELECT label FROM tables WHERE label=? COLLATE NOCASE", (table,))
    if rt.store.scalar("SELECT COUNT(*) FROM tables") and not row:
        raise HTTPException(403, t("err.gast.table"))          # Tisch inzwischen gelöscht
    return row["label"] if row else table


def menu(table: str, sig: str) -> dict:
    table = _check_table(table, sig)
    cfg = config()
    cats = rt.store.query("SELECT id, name, color, sort FROM categories ORDER BY sort, name")
    prods = []
    for p in rt.store.query("SELECT id, name, price_cents, category_id, image_id, sort, options, soldout, stock "
                            "FROM products WHERE active=1 ORDER BY sort, name"):
        opts = options_mod.loads(p["options"])
        prods.append({"id": p["id"], "name": p["name"], "price": p["price_cents"], "category_id": p["category_id"],
                      "image": f"/media/{p['image_id']}" if p["image_id"] else None,
                      "soldout": bool(p["soldout"]) or (p["stock"] is not None and p["stock"] <= 0),
                      "left": p["stock"] if p["stock"] is not None and p["stock"] < 10 else None,
                      "groups": [{"name": g["name"], "choices": g["choices"], "required": g["required"],
                                  "multi": g["multi"]} for g in (opts or {}).get("groups") or []]})
    used = {p["category_id"] for p in prods}
    return {"table": table, "closed": closed_reason(cfg), "name": rt.store.setting("event_name", "") or "",
            "logo": bool(rt.store.setting("logo_image", "")), "max_cents": cfg["max_cents"], "max_qty": MAX_QTY,
            "table_word": rt.store.setting("table_word", "Tisch") or "Tisch",
            "tips": tips.enabled(rt.store),                     # Trinkgeld anbieten (Verwaltung › Kasse)
            "legal": web.legal_links(), "terms": web.terms_required(),   # Rechtsseiten, AGB akzeptieren (web.py)
            "categories": [c for c in cats if c["id"] in used], "products": prods}


# -- Bestellen ---------------------------------------------------------------------------------------------------
class LineIn(BaseModel):
    product_id: int = Field(ge=1)
    qty: int = Field(ge=1, le=MAX_QTY)
    options: list[str] = Field(default_factory=list, max_length=20)
    # Auswahl eindeutig pro Gruppe: [[Gruppe (Nummer), Auswahl (Text)], …] – gleichnamige Auswahl in zwei Gruppen
    # («Beilage: Pommes», «Extra: Pommes») ist so nie verwechselt. Ohne picks: ältere Seite mit «options».
    # [Gruppe, Gruppenname, Auswahl] – der Name sichert gegen umsortierte Gruppen (ältere Seite: [Gruppe, Auswahl])
    picks: list[tuple[int, str] | tuple[int, str, str]] | None = Field(
        default=None, max_length=options_mod.MAX_GROUPS * options_mod.MAX_CHOICES)


class OrderIn(BaseModel):
    t: str = Field(min_length=1, max_length=20)
    s: str = Field(min_length=8, max_length=20)
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)
    key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")    # nochmals senden = gleiche Bestellung
    lang: str = Field(default="de", pattern="^(de|fr|it|en|rm)$")
    tip_cents: int = Field(default=0, ge=0, le=tips.MAX_CENTS)       # Trinkgeld (nur wenn eingeschaltet)
    terms: bool = False                                              # AGB akzeptiert (Pflicht, wenn es AGB gibt)


def _validate_lines(lines: list[LineIn]) -> tuple[list[dict], int]:
    """Nur aktive, verfügbare Produkte; Optionen nur aus der Liste, Pflichtgruppen gewählt. -> (Positionen, Rappen)."""
    pids = {x.product_id for x in lines}
    marks = ",".join("?" * len(pids))
    prods = {p["id"]: p for p in rt.store.query(
        f"SELECT id, name, price_cents, options, soldout, stock FROM products WHERE active=1 AND id IN ({marks})",
        tuple(pids))}
    out, total, need = [], 0, {}
    for x in lines:
        p = prods.get(x.product_id)
        if not p:
            raise HTTPException(409, t("err.gast.product"))
        need[p["id"]] = need.get(p["id"], 0) + x.qty
        if p["soldout"] or (p["stock"] is not None and p["stock"] < need[p["id"]]):
            raise HTTPException(409, t("err.gast.soldout", name=p["name"]))
        defn = options_mod.loads(p["options"]) or {}
        groups = defn.get("groups") or []
        chosen = []
        if x.picks is not None:
            want: dict[int, list[str]] = {}
            for pick in x.picks:
                gi, c = pick[0], pick[-1]
                if not 0 <= gi < len(groups) or c not in groups[gi]["choices"] or \
                        (len(pick) == 3 and pick[1] != groups[gi]["name"]):
                    raise HTTPException(409, t("err.gast.option_unknown", name=p["name"]))
                want.setdefault(gi, []).append(c)
            for gi, g in enumerate(groups):
                picked = [c for c in g["choices"] if c in want.get(gi, [])]
                if g["required"] and not picked:
                    raise HTTPException(409, t("err.gast.option", name=p["name"], group=g["name"]))
                if not g["multi"]:
                    picked = picked[:1]
                chosen += [options_mod.group_label(defn, gi, c) for c in picked]
        else:
            for g in groups:
                picked = [c for c in g["choices"] if c in x.options]
                if g["required"] and not picked:
                    raise HTTPException(409, t("err.gast.option", name=p["name"], group=g["name"]))
                if not g["multi"]:
                    picked = picked[:1]
                chosen += picked
            if any(o not in chosen for o in x.options):
                raise HTTPException(409, t("err.gast.option_unknown", name=p["name"]))
            chosen = options_mod.label_choices(defn, chosen)
        out.append({"product_id": p["id"], "qty": x.qty, "options": chosen, "name": p["name"],
                    "price": p["price_cents"]})
        total += p["price_cents"] * x.qty
    return out, total


def start(b: OrderIn, ip: str) -> dict:
    old = rt.store.query_one("SELECT * FROM gast_orders WHERE key=?", (b.key,))
    if old:
        if old["table_label"].lower() != _check_table(b.t, b.s).lower():   # nur derselbe (signierte) Tisch
            raise HTTPException(409, t("err.gast.busy"))
        return old
    cfg = config()
    why = closed_reason(cfg)
    if why:
        raise HTTPException(409, t("err.gast.closed"))
    table = _check_table(b.t, b.s)
    if not b.terms and web.terms_required():
        raise HTTPException(422, t("err.gast.terms"))
    lines, total = _validate_lines(b.lines)
    if total <= 0 or total > cfg["max_cents"]:
        raise HTTPException(409, t("err.gast.max", amount=ledger.money(cfg["max_cents"])))
    tip = b.tip_cents if tips.enabled(rt.store) else 0
    if tip > total:                                      # mehr Trinkgeld als Bestellung: verschrieben
        raise HTTPException(409, t("err.gast.tip"))
    waiting = [r for r in rt.store.query("SELECT created_at FROM gast_orders WHERE table_label=? AND status='waiting'",
                                         (table,)) if time.time() - (_epoch(r["created_at"]) or 0) < WAIT_S]
    if len(waiting) >= MAX_WAITING_PER_TABLE:
        raise HTTPException(429, t("err.gast.busy"))
    token = secrets.token_urlsafe(18)
    res = rt.store.execute("INSERT INTO gast_orders(token, key, table_label, lines, amount_cents, tip_cents, lang, "
                           "status, ip, created_at) VALUES (?,?,?,?,?,?,?,'new',?,?) RETURNING id",
                           (token, b.key, table, json.dumps(lines, ensure_ascii=False), total + tip, tip, b.lang,
                            hashlib.sha256(ip.encode()).hexdigest()[:16], now_iso()))
    gid = res.rows[0]["id"]
    back = f"{base_url(cfg)}/?b={quote(token)}"
    try:
        g = px.create_gateway(cfg["instance"], cfg["key"], amount=total + tip, reference=f"FestPOS-online-{gid}",
                              purpose=t("srv.gast.purpose", table=table), success_url=back, failed_url=back,
                              cancel_url=back, lang=b.lang)
    except px.PayrexxError as e:
        rt.store.execute("UPDATE gast_orders SET status='error', error=? WHERE id=?", (e.code, gid))
        raise HTTPException(503, t("err.gast.payrexx")) from None
    rt.store.execute("UPDATE gast_orders SET status='waiting', gateway_id=?, link=? WHERE id=? AND status='new'",
                     (g["id"], g["link"], gid))
    return _row_token(token)


def _row_token(token: str) -> dict | None:
    return rt.store.query_one("SELECT * FROM gast_orders WHERE token=?", (token,))


# -- Bezahlt -> Bestellung, sonst zurück --------------------------------------------------------------------------
def _ask(row: dict) -> dict | None:
    """Stand bei Payrexx nachfragen. None: keine Antwort (der Stand ist unbekannt – nie als «nicht bezahlt» werten)."""
    cfg = config()
    rt.store.execute("UPDATE gast_orders SET checked_at=? WHERE id=?", (now_iso(), row["id"]))
    try:
        return px.gateway_status(cfg["instance"], cfg["key"], row["gateway_id"])
    except px.PayrexxError as e:
        log.info("Online-Bestellung %s: Payrexx nicht erreichbar (%s)", row["id"], e.code)
        return None


def check(row: dict, force: bool = False) -> dict:
    """Offene Zahlung bei Payrexx nachfragen (höchstens alle RECHECK_S Sekunden) und weiterverarbeiten.
    Hat der Gast abbrechen lassen (reason «guest», T38), wird eine doch bezahlte Zahlung zurückerstattet, sonst
    abgebrochen – aber nur, wenn Payrexx geantwortet hat."""
    if row["status"] != "waiting" or not row["gateway_id"]:
        return row
    last = _epoch(row["checked_at"])
    if not force and last and time.time() - last < RECHECK_S:
        return row
    st = _ask(row)
    if st is None:
        return _row_token(row["token"])
    if st["status"] in px.PAID:
        if row["reason"] == "guest":
            _late_paid(row, st["transaction"], st["method"])
        else:
            paid(row, st["transaction"], st["method"])
    elif st["status"] in px.FAILED:
        rt.store.execute("UPDATE gast_orders SET status='cancelled', reason=? WHERE id=? AND status='waiting'",
                         (st["status"], row["id"]))
    elif row["reason"] == "guest":
        _cancel_now(row, "guest")
    return _row_token(row["token"])


def _cancel_now(row: dict, reason: str) -> None:
    """Payrexx hat geantwortet: noch nicht bezahlt -> abbrechen und die Zahlungsseite löschen. Zahlt der Gast genau
    jetzt noch, findet die Nachkontrolle in tick() das (2 h ab jetzt, T43) und erstattet zurück."""
    rt.store.execute("UPDATE gast_orders SET status='cancelled', reason=?, cancelled_at=? WHERE id=? AND status='waiting'",
                     (reason, now_iso(), row["id"]))
    try:
        cfg = config()
        px.delete_gateway(cfg["instance"], cfg["key"], row["gateway_id"])
    except px.PayrexxError:
        pass


def _late_paid(row: dict, tx: str | None, method: str | None) -> None:
    """Bezahlt, obwohl abgebrochen (T38, Entscheid: Geld automatisch zurück, nichts drucken)."""
    res = rt.store.batch([
        ("UPDATE gast_orders SET status='rejected', reason='late', transaction_id=?, method=?, paid_at=? WHERE id=? "
         "AND (status='cancelled' OR (status='waiting' AND reason='guest'))", (tx, method, now_iso(), row["id"])),
        *refund_stmts(row["id"], row["amount_cents"], f"late:{row['id']}", "auto:late"),
        lb.audit_stmt("online", "gast_paid_late", gast=row["id"], table=row["table_label"], amount=row["amount_cents"],
                      transaction=tx)])
    if res[0].rows_affected:
        process_refunds(row["id"])


def _epoch(iso: str | None) -> float | None:
    from .timesync import local_epoch
    return local_epoch(iso) if iso else None


def paid(row: dict, tx: str | None, method: str | None) -> None:
    """Einmal: Zahlung bestätigt -> Bestellung anlegen und drucken; geht das nicht, ganz zurückerstatten."""
    claim = rt.store.execute("UPDATE gast_orders SET status='paid', transaction_id=?, method=?, paid_at=? "
                             "WHERE id=? AND status='waiting' AND COALESCE(reason,'')<>'guest'",
                             (tx, method, now_iso(), row["id"]))
    if claim.rows_affected != 1:
        return
    lb.audit(rt.store, "online", "gast_paid", gast=row["id"], table=row["table_label"], amount=row["amount_cents"],
             transaction=tx)
    row = {**row, "status": "paid", "transaction_id": tx}
    lines = json.loads(row["lines"])
    if closed_reason() == "printer":
        return _reject(row, tx, "printer")
    try:
        # T38: nie auf den offenen Tisch (schon bezahlt), Preise wie bezahlt, Summe muss stimmen
        res = create_order(rt.store, client_uuid=f"gast-{row['token']}", table_label=row["table_label"],
                           lines=[{"product_id": x["product_id"], "qty": x["qty"], "options": x["options"]}
                                  for x in lines], device=t("srv.gast.device"), payment="payrexx", open_tab=False,
                           prices={x["product_id"]: x["price"] for x in lines},
                           expect_total=row["amount_cents"] - (row.get("tip_cents") or 0),
                           extra=_finish_stmts(row, claim=True))   # T43/T44: Verknüpfung und Zahlung im selben Batch
    except OrderError as e:
        log.info("Online-Bestellung %s nicht angenommen: %s", row["id"], e)
        return _reject(row, tx, "soldout" if e.stock else e.code or "order", str(e))
    except Exception:  # noqa: BLE001 – Antwort unklar: tick() holt den Abschluss nach oder erstattet zurück
        log.exception("Online-Bestellung %s: bezahlt, Anlegen unklar", row["id"])
        return
    if res.duplicate:                                  # Bestellung stand schon (Wiederholung): Abschluss nachholen
        rt.store.batch(_finish_stmts(row))
    if rt.spooler:
        for pid in res.printer_ids:
            rt.spooler.notify(pid)


def _finish_stmts(row: dict, claim: bool = False) -> list:
    """Online-Bestellung «ordered» mit Verweis auf die Bestellung und die Zahlung für den Tagesabschluss. Läuft im Batch
    der Bestellung (T43) oder später zum Nachholen; wiederholbar. Die Bestellung über ihre client_uuid.
    claim (T44, im Batch der Bestellung): gilt die Online-Bestellung nicht mehr als «bezahlt» (z.B. inzwischen von tick()
    abgelehnt und zurückerstattet, weil dieser Aufruf hing), scheitert der ganze Batch – keine Bestellung, keine Bons."""
    oid = "(SELECT id FROM orders WHERE client_uuid=?)"
    cu = f"gast-{row['token']}"
    guard = [("UPDATE gast_orders SET status=NULL WHERE id=? AND status<>'ordered'", (row["id"],))] if claim else []
    return [(f"UPDATE gast_orders SET status='ordered', order_id={oid} WHERE id=? AND status='paid'", (cu, row["id"])),
            *guard,                                    # NOT NULL scheitert: der Batch wird nicht geschrieben
            ("INSERT OR IGNORE INTO payrexx_payments(key, order_id, part, amount_cents, tip_cents, gateway_id, status, "
             f"transaction_id, test, device, created_at, paid_at) VALUES (?,{oid},'all',?,?,?,'confirmed',?,0,?,?,?)",
             (f"gast-{row['id']}", cu, row["amount_cents"], row.get("tip_cents") or 0, row["gateway_id"],
              row["transaction_id"], t("srv.gast.device"), row["created_at"], now_iso()))]


def _finish(row: dict, order_id: int | None = None, printer_ids=()) -> None:
    """Abschluss nachholen (tick: Bestellung steht, Abschluss fehlt – nur bei Daten aus der Zeit vor T43)."""
    rt.store.batch(_finish_stmts(row))
    if rt.spooler:
        for pid in printer_ids:
            rt.spooler.notify(pid)


def _reject(row: dict, tx: str | None, reason: str, detail: str | None = None) -> None:
    """Nicht angenommen: Status und Rückerstattungsauftrag im selben Batch (T37), dann gleich senden. T44: nur, solange
    sie noch «bezahlt» ist und keine Bestellung besteht – sonst (Bestellung kam eben doch) bleibt alles, wie es ist."""
    res = rt.store.batch([("UPDATE gast_orders SET status='rejected', reason=?, error=? WHERE id=? AND status='paid' "
                           "AND NOT EXISTS (SELECT 1 FROM orders WHERE client_uuid=?)",
                           (reason, (detail or "")[:200] or None, row["id"], f"gast-{row['token']}")),
                          *refund_stmts(row["id"], row["amount_cents"], f"reject:{row['id']}", f"auto:{reason}",
                                        when="g.status='rejected'")])
    if res[0].rows_affected:
        process_refunds(row["id"])


# -- Rückerstattungen (T37) ------------------------------------------------------------------------------------------
# Jede Rückerstattung ist ein Auftrag in gast_refunds mit eigenem Schlüssel, angelegt im selben Batch wie das, was sie
# auslöst (Storno, Ablehnung, Knopf). Zusammen nie mehr als bezahlt: reserviert ist jeder Auftrag. Eine unklare Antwort
# von Payrexx (Timeout) wird nie von selbst wiederholt – das Geld kann schon zurück sein.
STUCK_S = 120                   # so lange darf ein Auftrag «sending» sein, sonst gilt er als unklar (Neustart dazwischen)
_RESERVED = "COALESCE((SELECT SUM(r.amount_cents) FROM gast_refunds r WHERE r.gast_id=g.id), 0)"


def _sum(states: str) -> str:
    return (f"COALESCE((SELECT SUM(amount_cents) FROM gast_refunds WHERE gast_id=gast_orders.id AND "
            f"status IN ({states})), 0)")


def sync_stmts(gid: int) -> list:
    """Zurück und offen aus den Aufträgen berechnen (kein +=, T36/T37); alles zurück -> «refunded»."""
    done, left = _sum("'done'"), _sum("'open','sending','unclear','failed'")
    return [(f"UPDATE gast_orders SET refunded_cents={done}, refund_open={left}, "
             f"status=CASE WHEN status IN ('ordered','paid') AND {done}>=amount_cents THEN 'refunded' ELSE status END "
             f"WHERE id=?", (gid,)),
            ("UPDATE payrexx_payments SET refunded_cents=(SELECT refunded_cents FROM gast_orders WHERE id=?) WHERE key=?",
             (gid, f"gast-{gid}"))]


def refund_stmts(gid: int, amount: int, key: str, why: str, when: str = "1") -> list:
    """Auftrag anlegen – höchstens, was noch nicht reserviert ist; derselbe Schlüssel nochmals: nichts Neues.
    when: zusätzliche Bedingung an die Online-Bestellung g (T44: nur wenn sie eben abgelehnt wurde)."""
    want = f"MIN(?, g.amount_cents - {_RESERVED})"
    return [(f"INSERT OR IGNORE INTO gast_refunds(key, gast_id, amount_cents, why, status, created_at) "
             f"SELECT ?, g.id, {want}, ?, 'open', ? FROM gast_orders g WHERE g.id=? AND g.transaction_id IS NOT NULL "
             f"AND {want} > 0 AND {when}", (key, int(amount), why[:60], now_iso(), gid, int(amount))),
            *sync_stmts(gid)]


def late_refund_stmts(store, gid: int, amount: int, key: str, why: str, actor: str) -> list:
    """T45 (Entscheid: A2): Rückerstattung einer Online-Bestellung, die schon in einem Tagesabschluss ist.
    Kein Storno (der Abschluss bleibt, wie er ist), sondern Rückerstattungsauftrag und Korrektur im selben Batch: der
    nächste Abschluss bucht sie als «Rückerstattung früherer Tage» (Umsatz, MWST, Trinkgeld und Payrexx minus).
    Zurück geht zuerst der Umsatz, das Trinkgeld erst mit allem (wie beim Storno)."""
    row = store.query_one("SELECT * FROM gast_orders WHERE id=?", (gid,))
    if not row or not row["order_id"] or not row["transaction_id"]:
        return []
    reserved = int(store.scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM gast_refunds WHERE gast_id=?", (gid,)) or 0)
    want = min(int(amount), row["amount_cents"] - reserved)
    if want <= 0:
        return []
    goods_total = row["amount_cents"] - (row["tip_cents"] or 0)
    goods = max(0, min(want, goods_total - min(reserved, goods_total)))
    vat = _vat_split(store, row["order_id"], goods)
    c = {"key": key, "kind": "refund", "order_id": row["order_id"], "amount_cents": want, "goods_cents": goods,
         "tip_cents": want - goods, "vat": vat, "created_at": now_iso(), "why": why[:60]}
    return [*refund_stmts(gid, want, key, why),
            # nur zusammen mit genau diesem Auftrag (gleicher Schlüssel und Betrag) – nie eine Korrektur ohne Geldfluss
            ("INSERT OR IGNORE INTO corrections(key, kind, order_id, gast_id, amount_cents, goods_cents, tip_cents, vat, "
             "why, by_user, created_at, digest) SELECT ?,?,?,?,?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM gast_refunds "
             "WHERE key=? AND amount_cents=?)",
             (key, "refund", row["order_id"], gid, want, goods, want - goods, json.dumps(vat), why[:60], actor[:60],
              c["created_at"], lb.correction_digest(c), key, want)),
            # fehlt die Korrektur (gleichzeitig anders reserviert) oder hat sich der Stand geändert, aus dem Umsatz und
            # Trinkgeld berechnet sind (T46: zwei Rückerstattungen gleichzeitig), scheitert der ganze Batch – kein Geld
            # ohne Buchung, keine falsche Aufteilung; die Verwaltung bekommt 409 und versucht es nochmals
            ("UPDATE gast_orders SET status=NULL WHERE id=? AND (NOT EXISTS (SELECT 1 FROM corrections WHERE key=?) OR "
             "(SELECT COALESCE(SUM(amount_cents), 0) FROM gast_refunds WHERE gast_id=? AND key<>?) <> ?)",
             (gid, key, gid, key, reserved))]


def _vat_split(store, order_id: int, goods: int) -> list:
    """Umsatz einer Korrektur nach MWST-Satz aufteilen – im Verhältnis der Positionen, die noch nicht storniert sind
    (grösster Rest, Summe exakt). Ohne MWST: []."""
    rows = store.query("SELECT i.vat_code, i.vat_bp, SUM(i.price_cents*i.qty) - COALESCE((SELECT SUM(ci.amount_cents) "
                       "FROM cancellation_items ci JOIN order_items j ON j.id=ci.item_id WHERE j.order_id=i.order_id "
                       "AND j.vat_code IS i.vat_code AND j.vat_bp IS i.vat_bp), 0) AS g FROM order_items i "
                       "WHERE i.order_id=? AND i.vat_bp IS NOT NULL GROUP BY i.vat_code, i.vat_bp", (order_id,))
    rows = [r for r in rows if r["g"] > 0]
    total = sum(r["g"] for r in rows)
    if not total or goods <= 0:
        return []
    q = [(r, *divmod(goods * r["g"], total)) for r in rows]
    parts = {(r["vat_code"] or "normal", r["vat_bp"]): a for r, a, _ in q}
    for r, _, _ in sorted(q, key=lambda x: x[2], reverse=True)[:goods - sum(parts.values())]:
        parts[(r["vat_code"] or "normal", r["vat_bp"])] += 1
    return [[code, bp, cents] for (code, bp), cents in parts.items() if cents]


def storno_refund_stmts(store, order_id: int, amount: int, cancel_uuid: str, actor: str) -> list:
    """Für ledger.cancel (im Batch des Stornos): Storno einer Online-Bestellung erstattet diesen Betrag zurück. Bliebe
    danach nur noch das Trinkgeld, geht es mit zurück (ganze Bestellung storniert)."""
    row = store.query_one("SELECT id, amount_cents, tip_cents, transaction_id FROM gast_orders WHERE order_id=?",
                          (order_id,))
    if not row or amount <= 0 or not row["transaction_id"]:
        return []
    reserved = int(store.scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM gast_refunds WHERE gast_id=?",
                                (row["id"],)) or 0)
    if reserved + amount >= row["amount_cents"] - (row["tip_cents"] or 0):
        amount = row["amount_cents"] - reserved
    return refund_stmts(row["id"], amount, f"storno:{cancel_uuid}", f"storno:{actor}")


def process_refunds(gid: int | None = None) -> None:
    """Offene Aufträge an Payrexx senden. offline (sicher nicht angekommen): bleibt offen, nächste Runde. unclear:
    nicht wiederholen, die Verwaltung prüft im Payrexx-Konto. Payrexx lehnt ab: failed (Knopf «Nochmals senden»)."""
    cfg = config()
    if not (cfg["instance"] and cfg["key"]):
        return
    rows = rt.store.query("SELECT r.*, g.transaction_id FROM gast_refunds r JOIN gast_orders g ON g.id=r.gast_id "
                          "WHERE r.status='open'" + (" AND r.gast_id=?" if gid else "") + " ORDER BY r.id",
                          (gid,) if gid else ())
    for r in rows:
        claim = rt.store.execute("UPDATE gast_refunds SET status='sending', sent_at=?, attempts=attempts+1 "
                                 "WHERE id=? AND status='open'", (now_iso(), r["id"]))
        if claim.rows_affected != 1:
            continue                                            # anderer Pi / anderer Aufruf war schneller
        try:
            px.refund(cfg["instance"], cfg["key"], r["transaction_id"], r["amount_cents"])
        except px.PayrexxError as e:
            st = {"offline": "open", "unclear": "unclear"}.get(e.code, "failed")
            stmts = [("UPDATE gast_refunds SET status=?, error=? WHERE id=? AND status='sending'", (st, e.code, r["id"])),
                     ("UPDATE gast_orders SET error=? WHERE id=?", (f"refund:{e.code}", r["gast_id"])),
                     *sync_stmts(r["gast_id"])]
            if st != "open":
                stmts.append(lb.audit_stmt("online", "gast_refund_failed" if st == "failed" else "gast_refund_unclear",
                                           gast=r["gast_id"], amount=r["amount_cents"], why=r["why"], error=e.code))
            rt.store.batch(stmts)
            log.info("Online-Bestellung %s: Rückerstattung %s (%s)", r["gast_id"], st, e.code)
            continue
        rt.store.batch([("UPDATE gast_refunds SET status='done', done_at=?, error=NULL WHERE id=?", (now_iso(), r["id"])),
                        ("UPDATE gast_orders SET error=NULL WHERE id=? AND error LIKE 'refund:%' AND NOT EXISTS "
                         "(SELECT 1 FROM gast_refunds WHERE gast_id=? AND status IN ('unclear','failed'))",
                         (r["gast_id"], r["gast_id"])),
                        *sync_stmts(r["gast_id"]),
                        lb.audit_stmt("online", "gast_refund", gast=r["gast_id"], amount=r["amount_cents"], why=r["why"],
                                      transaction=r["transaction_id"])])


def process_for_order(order_id: int) -> None:
    """Nach einem Storno: die Aufträge dieser Online-Bestellung gleich senden (der Gast wartet nicht eine Minute)."""
    gid = rt.store.scalar("SELECT id FROM gast_orders WHERE order_id=?", (order_id,))
    if gid:
        process_refunds(int(gid))


def _unstick() -> None:
    """Auftrag hängt in «sending» (Neustart oder Datenbank weg mitten im Senden): unklar, nie blind nochmals."""
    from datetime import datetime, timedelta
    before = (datetime.now() - timedelta(seconds=STUCK_S)).strftime("%Y-%m-%dT%H:%M:%S")
    for r in rt.store.query("SELECT id, gast_id, amount_cents, why FROM gast_refunds WHERE status='sending' "
                            "AND sent_at < ?", (before,)):
        rt.store.batch([("UPDATE gast_refunds SET status='unclear', error='stuck' WHERE id=? AND status='sending'",
                         (r["id"],)),
                        *sync_stmts(r["gast_id"]),
                        lb.audit_stmt("online", "gast_refund_unclear", gast=r["gast_id"], amount=r["amount_cents"],
                                      why=r["why"], error="stuck")])


def do_refund(gid: int, amount: int, why: str, key: str | None = None) -> bool:
    """Auftrag anlegen und gleich senden. False: nichts mehr offen oder hat (noch) nicht geklappt (Verwaltung)."""
    key = key or f"admin:{secrets.token_hex(8)}"
    rt.store.batch(refund_stmts(gid, amount, key, why))
    process_refunds(gid)
    return rt.store.scalar("SELECT status FROM gast_refunds WHERE key=?", (key,)) == "done"


LATE_S = 24 * 3600              # so lange bleibt eine Zahlung ohne Antwort von Payrexx offen (T38)
AFTER_CANCEL_S = 2 * 3600       # so lange nach einem Abbruch durch FestPOS nachfragen (Payrexx-Seite gilt 15 min)
LATE_EVERY_S = 300


CLEAN_EVERY_S = 3600            # Aufräumen höchstens so oft
KEEP_DEAD_S = 24 * 3600         # nie zur Zahlung gekommen (new, error): so lange behalten
KEEP_CANCELLED_S = 14 * 86400   # abgebrochen oder abgelaufen, ohne Zahlung: so lange in der Liste der Verwaltung
_cleaned = [0.0]


def cleanup(now: float | None = None) -> int:
    """Alte Online-Bestellungen löschen, zu denen nie Geld floss (Review 2026-11, S7): sonst wächst die Tabelle mit
    jedem angefangenen Bezahlvorgang – auch mit denen eines Störers. Nie etwas mit Zahlung, Bestellung oder
    Rückerstattung. Zuerst lesen: ein DELETE ohne Treffer wäre trotzdem ein Schreibzugriff auf allen Pi (T9)."""
    from datetime import datetime, timedelta
    t0 = datetime.fromtimestamp(time.time() if now is None else now)
    dead = (t0 - timedelta(seconds=KEEP_DEAD_S)).strftime("%Y-%m-%dT%H:%M:%S")
    cancelled = (t0 - timedelta(seconds=KEEP_CANCELLED_S)).strftime("%Y-%m-%dT%H:%M:%S")
    where = ("transaction_id IS NULL AND order_id IS NULL AND ((status IN ('new','error') AND created_at < ?) OR "
             "(status='cancelled' AND COALESCE(cancelled_at, created_at) < ?)) AND NOT EXISTS (SELECT 1 FROM gast_refunds r WHERE "
             "r.gast_id=gast_orders.id)")
    if not rt.store.scalar(f"SELECT COUNT(*) FROM gast_orders WHERE {where}", (dead, cancelled)):
        return 0
    return rt.store.execute(f"DELETE FROM gast_orders WHERE {where}", (dead, cancelled)).rows_affected


def tick() -> None:
    """Druckender Pi, jede Minute: offene Zahlungen nachfragen, alte schliessen (erst nach einer Antwort von Payrexx),
    abgebrochene 24 h nachkontrollieren, bezahlte ohne Bestellung abschliessen oder zurückerstatten, nicht gedruckte
    zurückerstatten, offene Rückerstattungen senden. Einmal pro Stunde: alte ohne Zahlung löschen."""
    if time.time() - _cleaned[0] >= CLEAN_EVERY_S:
        _cleaned[0] = time.time()
        try:
            n = cleanup()
            if n:
                log.info("Online-Bestellungen: %d alte ohne Zahlung gelöscht", n)
        except StoreError as e:
            log.warning("Online-Bestellungen nicht aufgeräumt: %s", e)
    cfg = config()
    if not (cfg["instance"] and cfg["key"]):
        return
    now = time.time()
    for r in rt.store.query("SELECT * FROM gast_orders WHERE status='waiting'"):
        age = now - (_epoch(r["created_at"]) or now)
        if age < WAIT_S:
            check(r, force=True)
            continue
        st = _ask(r) if r["gateway_id"] else {"status": ""}
        if st is None:                                   # Payrexx antwortet nicht: offen lassen, höchstens 24 h
            if age > LATE_S:
                rt.store.batch([("UPDATE gast_orders SET status='cancelled', reason='expired', cancelled_at=? WHERE "
                                 "id=? AND status='waiting'", (now_iso(), r["id"])),
                                lb.audit_stmt("online", "gast_unresolved", gast=r["id"], amount=r["amount_cents"])])
        elif st["status"] in px.PAID:
            (_late_paid if r["reason"] == "guest" else paid)(r, st["transaction"], st["method"])
        else:
            _cancel_now(r, "guest" if r["reason"] == "guest" else "expired")
    # von FestPOS abgebrochen (Gast, Ablauf), aber doch noch bezahlt? (Gast zahlte im letzten Moment). Hat Payrexx
    # selbst «abgelehnt/abgelaufen» gemeldet, kommt nichts mehr – nicht nachfragen
    for r in rt.store.query("SELECT * FROM gast_orders WHERE status='cancelled' AND gateway_id IS NOT NULL AND "
                            "reason IN ('guest','expired')"):
        since = _epoch(r.get("cancelled_at") or r["created_at"]) or now           # T43: ab dem Abbruch zählen
        if now - since > AFTER_CANCEL_S or now - (_epoch(r["checked_at"]) or 0) < LATE_EVERY_S:
            continue
        st = _ask(r)
        if st and st["status"] in px.PAID:
            _late_paid(r, st["transaction"], st["method"])
    for r in rt.store.query("SELECT * FROM gast_orders WHERE status='paid'"):
        if now - (_epoch(r["paid_at"]) or now) <= 120:
            continue
        oid = rt.store.scalar("SELECT id FROM orders WHERE client_uuid=?", (f"gast-{r['token']}",))
        if oid:                                          # T38: die Bestellung steht – nur der Abschluss fehlte
            _finish(r, int(oid))
        else:
            _reject(r, r["transaction_id"], "order", "paid, aber keine Bestellung")
    limit = cfg["refund_min"] * 60
    # nicht gedruckt: nichts fertig oder unterwegs, und mindestens ein Bon wartet oder ist gescheitert (T38)
    for r in rt.store.query("SELECT g.* FROM gast_orders g WHERE g.status='ordered' AND NOT EXISTS (SELECT 1 FROM "
                            "print_jobs j WHERE j.order_id=g.order_id AND j.status IN ('done','printing','uncertain')) "
                            "AND EXISTS (SELECT 1 FROM print_jobs j WHERE j.order_id=g.order_id AND "
                            "j.status IN ('pending','failed'))"):
        if now - (_epoch(r["paid_at"]) or now) < limit:
            continue
        if rt.store.scalar("SELECT closing_id FROM orders WHERE id=?", (r["order_id"],)):
            # T45: inzwischen im Tagesabschluss – kein Storno mehr möglich: Bons zurückhalten, Korrektur und Geld zurück
            try:
                rt.store.batch([("UPDATE print_jobs SET status='cancelled', error_code='storno' WHERE order_id=? AND "
                                 "status IN ('pending','failed')", (r["order_id"],)),
                                ("UPDATE gast_orders SET reason='printer' WHERE id=?", (r["id"],)),
                                *late_refund_stmts(rt.store, r["id"], r["amount_cents"], f"np:{r['id']}",
                                                   "auto:printer", "online")])
            except Exception as e:  # noqa: BLE001 – gleichzeitig anders reserviert: nächste Minute nochmals
                log.warning("Online-Bestellung %s: Korrektur gescheitert: %s", r["id"], e)
                continue
            process_refunds(r["id"])
            continue
        try:
            ledger.cancel(rt.store, order_id=r["order_id"], lines=None, reason="other", note=t("srv.gast.not_printed"),
                          by_user="online", client_uuid=f"gast-np-{r['id']}")
        except Exception as e:  # noqa: BLE001 – nächste Minute nochmals
            log.warning("Online-Bestellung %s: Storno gescheitert: %s", r["id"], e)
            continue
        rt.store.execute("UPDATE gast_orders SET reason='printer' WHERE id=?", (r["id"],))
        process_refunds(r["id"])                     # den Auftrag hat das Storno angelegt (T37)
    _unstick()
    process_refunds()                                # offen gebliebene (Payrexx war nicht erreichbar)


# -- Ansicht für den Gast ---------------------------------------------------------------------------------------------
def view(row: dict) -> dict:
    lines = json.loads(row["lines"])
    step = None
    if row["status"] == "ordered" and row["order_id"]:
        jobs = rt.store.query("SELECT status FROM print_jobs WHERE order_id=?", (row["order_id"],))
        step = "printed" if jobs and all(j["status"] == "done" for j in jobs) else "queued"
    return {"status": row["status"], "nr": row["order_id"], "table": row["table_label"], "amount": row["amount_cents"],
            "tip": row.get("tip_cents") or 0,
            "refunded": row["refunded_cents"], "reason": row["reason"], "method": row["method"], "step": step,
            "link": row["link"] if row["status"] == "waiting" and row["reason"] != "guest" else None,
            "cancelling": row["status"] == "waiting" and row["reason"] == "guest",     # Abbruch wird geprüft
            "lines": [{"name": x["name"], "qty": x["qty"], "options": x["options"], "sum": x["price"] * x["qty"]}
                      for x in lines]}


def _ip(request: Request) -> str:
    return (request.headers.get("x-festpos-gast-ip") or "")[:64]


@router.get("/menu", dependencies=[Depends(gast_only)])
async def get_menu(table: str = Query("", alias="t", max_length=20), s: str = Query("", max_length=20)):
    return await db(menu, table, s)


@router.post("/order", dependencies=[Depends(gast_only)])
async def post_order(b: OrderIn, request: Request):
    row = await db(start, b, _ip(request))
    return {"token": row["token"], **view(row)}


@router.get("/order/{token}", dependencies=[Depends(gast_only)])
async def get_order(token: str):
    if len(token) > 40:
        raise HTTPException(404, t("err.gast.unknown"))

    def go():
        row = _row_token(token)
        if not row:
            raise HTTPException(404, t("err.gast.unknown"))
        return view(check(row))
    return await db(go)


@router.post("/order/{token}/cancel", dependencies=[Depends(gast_only)])
async def cancel_order(token: str):
    """Gast bricht ab (T38): zuerst merken (reason «guest» – kommt die Zahlung doch noch, geht sie zurück), dann bei
    Payrexx nachfragen. Abgebrochen wird erst, wenn Payrexx geantwortet hat; sonst klärt es tick()."""
    def go():
        row = _row_token(token)
        if not row:
            raise HTTPException(404, t("err.gast.unknown"))
        rt.store.execute("UPDATE gast_orders SET reason='guest' WHERE id=? AND status='waiting'", (row["id"],))
        check(_row_token(token), force=True)
        return view(_row_token(token))
    return await db(go)


@router.post("/webhook", dependencies=[Depends(gast_only)])
async def webhook(request: Request):
    """Payrexx meldet eine Änderung. Nur ein Anstoss: der Stand wird immer bei Payrexx nachgefragt."""
    raw = await request.body()
    ref = None
    try:
        data = json.loads(raw[:65536] or b"{}") if raw.strip().startswith(b"{") else {}
        tr = data.get("transaction") or {}
        ref = tr.get("referenceId") or (tr.get("invoice") or {}).get("referenceId")
    except (ValueError, AttributeError):
        pass
    if isinstance(ref, str) and ref.startswith("FestPOS-online-") and ref[15:].isdigit():
        def go():
            row = rt.store.query_one("SELECT * FROM gast_orders WHERE id=?", (int(ref[15:]),))
            if row:
                check(row)                                   # nicht erzwungen: eine Flut von Meldungen bremst sich selbst
        await db(go)
    return Response(status_code=200)


# -- Verwaltung -------------------------------------------------------------------------------------------------------
class SettingsIn(BaseModel):
    open: bool = False
    domain: str = Field(default="", max_length=120, pattern=r"^([a-z0-9-]+\.)+[a-z]{2,}$|^$")
    max_cents: int = Field(default=30000, ge=500, le=200000)
    refund_min: int = Field(default=10, ge=3, le=120)
    instance: str = Field(default="", max_length=60, pattern=r"^[A-Za-z0-9-]*$")
    key: str | None = Field(default=None, max_length=200)          # nur zum Ändern
    tunnel: str | None = Field(default=None, max_length=2100)      # Token des Cloudflare Tunnels, nur zum Ändern


def admin_view() -> dict:
    cfg = config()
    return {k: cfg[k] for k in ("open", "domain", "max_cents", "refund_min", "instance", "key_set", "key_ok")} | {
        "closed": closed_reason(cfg), "token_ok": bool(Config.cluster_secret),
        "tunnel": tunnel.configured(), "tunnel_pis": tunnel.pis_view(), "tested": px.PAYREXX_GEPRUEFT}


@admin_router.get("/gast")
async def get_settings():
    return await db(admin_view)


@admin_router.put("/gast")
async def put_settings(b: SettingsIn, request: Request):
    vals = {"gast_open": "1" if b.open else "0", "gast_domain": b.domain.strip().lower(),
            "gast_max_cents": str(b.max_cents), "gast_refund_min": str(b.refund_min), "gast_instance": b.instance}
    if b.key:
        vals["gast_key_enc"] = px.seal(b.key.strip())
    if b.tunnel:
        try:
            vals[tunnel.SETTING] = tunnel.seal(b.tunnel)
        except ValueError:
            raise HTTPException(422, t("err.gast.tunnel_bad")) from None

    def save():
        stmts = [("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (k, v)) for k, v in vals.items()]
        stmts.append(lb.audit_stmt(ledger.actor_of(request), "gast_settings", open=b.open, domain=b.domain,
                                   instance=b.instance, key_changed=bool(b.key), tunnel_changed=bool(b.tunnel)))
        rt.store.batch(stmts)
    await db(save)
    return await db(admin_view)


@admin_router.delete("/gast/tunnel")
async def tunnel_off(request: Request):
    """Tunnel aus der Verwaltung entfernen: jeder Pi schaltet ihn innert einer Minute aus (ausser mit festpos.conf)."""
    def save():
        rt.store.batch([("INSERT INTO settings(key,value) VALUES (?,'') ON CONFLICT(key) DO UPDATE SET value=''",
                         (tunnel.SETTING,)),
                        lb.audit_stmt(ledger.actor_of(request), "gast_tunnel_off")])
    await db(save)
    return await db(admin_view)


@admin_router.post("/gast/test")
async def test_connection():
    cfg = await db(config)
    if not (cfg["instance"] and cfg["key"]):
        raise HTTPException(422, t("err.gast.setup"))
    try:
        await db(px.check_connection, cfg["instance"], cfg["key"])
    except px.PayrexxError as e:
        raise HTTPException(502, t("err.gast.payrexx_auth" if e.code == "auth" else "err.gast.payrexx_offline")) from None
    return {"ok": True}


@admin_router.get("/gast/qr.pdf")
async def qr_pdf(labels: str = ""):
    from . import qr

    def build():
        cfg = config()
        if not cfg["domain"]:
            raise HTTPException(422, t("err.gast.domain"))
        want = [x.strip() for x in labels.split(",") if x.strip()] or \
            [r["label"] for r in rt.store.query("SELECT label FROM tables ORDER BY sort, label")]
        if not want:
            raise HTTPException(422, t("err.gast.no_tables"))
        return qr.gast_pdf([(lb_, table_url(cfg, lb_)) for lb_ in want[:200]],
                           rt.store.setting("event_name", "") or "", rt.store.setting("table_word", "Tisch") or "Tisch")
    pdf = await db(build)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="festpos-qr-bestellung.pdf"'})


@admin_router.get("/gast/orders")
async def list_orders(limit: int = 200):
    def go():
        rows = rt.store.query("SELECT id, table_label, amount_cents, tip_cents, refunded_cents, refund_open, status, "
                              "reason, order_id, transaction_id, method, created_at, paid_at, error FROM gast_orders "
                              "WHERE status NOT IN ('new','error') ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
        by: dict[int, list] = {}
        if rows:
            marks = ",".join("?" * len(rows))
            for r in rt.store.query(f"SELECT id, gast_id, amount_cents, why, status, attempts, error, created_at, "
                                    f"done_at FROM gast_refunds WHERE gast_id IN ({marks}) ORDER BY id",
                                    tuple(r["id"] for r in rows)):
                by.setdefault(r["gast_id"], []).append(r)
        for r in rows:
            r["refunds"] = by.get(r["id"], [])
        return {"orders": rows}
    return await db(go)


class RefundIn(BaseModel):
    amount_cents: int | None = Field(default=None, ge=1)             # None = alles, was noch nicht zurück ist


def _reserved(gid: int) -> int:
    return int(rt.store.scalar("SELECT COALESCE(SUM(amount_cents), 0) FROM gast_refunds WHERE gast_id=?", (gid,)) or 0)


@admin_router.post("/gast/orders/{gid}/refund")
async def refund_order(gid: int, b: RefundIn, request: Request):
    """Ganze Online-Bestellung zurück: Storno im Kassenbuch (wenn sie angelegt ist) und Geld zurück. Gescheiterte
    Rückerstattungen dieser Bestellung werden dabei nochmals gesendet; unklare nicht (erst im Payrexx-Konto prüfen)."""
    actor = ledger.actor_of(request)

    def go():
        row = rt.store.query_one("SELECT * FROM gast_orders WHERE id=?", (gid,))
        if not row or not row["transaction_id"]:
            raise HTTPException(404, t("err.gast.unknown"))
        failed = rt.store.scalar("SELECT COUNT(*) FROM gast_refunds WHERE gast_id=? AND status='failed'", (gid,))
        waiting = rt.store.scalar("SELECT COUNT(*) FROM gast_refunds WHERE gast_id=? AND status='open'", (gid,))
        if row["amount_cents"] - _reserved(gid) <= 0 and not failed and not waiting:
            raise HTTPException(409, t("err.gast.refunded"))
        if failed:
            rt.store.batch([("UPDATE gast_refunds SET status='open', error=NULL WHERE gast_id=? AND status='failed'",
                             (gid,)), *sync_stmts(gid),
                            lb.audit_stmt(actor, "gast_refund_retry", gast=gid)])
        closed = row["order_id"] and rt.store.scalar("SELECT closing_id FROM orders WHERE id=?", (row["order_id"],))
        if row["order_id"] and b.amount_cents is None and not closed:
            try:                                                         # legt den Auftrag im selben Batch an (T37)
                ledger.cancel(rt.store, order_id=row["order_id"], lines=None, reason="other",
                              note=t("srv.gast.refund_note"), by_user=actor, client_uuid=f"gast-rf-{gid}")
            except ledger.LedgerError as e:
                if str(e) != t("err.ledger.nothing_left"):              # nur «schon ganz storniert» ist in Ordnung
                    raise HTTPException(409, str(e)) from None
        left = row["amount_cents"] - _reserved(gid)
        want = left if b.amount_cents is None else min(b.amount_cents, left)
        if want > 0:
            key = f"admin:{secrets.token_hex(8)}"
            if not closed:
                try:                                                     # Abschluss im selben Batch geprüft: kam er
                    rt.store.batch([*refund_stmts(gid, want, key, f"admin:{actor}"),  # eben dazwischen, als Korrektur
                                    ("UPDATE gast_orders SET status=NULL WHERE id=? AND EXISTS (SELECT 1 FROM orders "
                                     "WHERE id=? AND closing_id IS NOT NULL)", (gid, row["order_id"]))])
                except StoreError as e:
                    if "NOT NULL constraint failed: gast_orders.status" not in str(e):
                        raise
                    closed = True
            if closed:                                                   # T45: schon im Tagesabschluss -> Korrektur
                try:
                    rt.store.batch(late_refund_stmts(rt.store, gid, want, key, f"admin:{actor}", actor))
                except StoreError as e:                                  # gleichzeitig anders reserviert
                    if "NOT NULL constraint failed: gast_orders.status" not in str(e):
                        raise
                    raise HTTPException(409, t("err.gast.refund_state")) from None
        process_refunds(gid)
        if rt.store.scalar("SELECT COUNT(*) FROM gast_refunds WHERE gast_id=? AND status<>'done'", (gid,)):
            raise HTTPException(502, t("err.gast.refund_failed"))
        return rt.store.query_one("SELECT id, status, refunded_cents, amount_cents FROM gast_orders WHERE id=?", (gid,))
    return await db(go)


@admin_router.post("/gast/refunds/{rid}/{action}")
async def refund_action(rid: int, action: str, request: Request):
    """Unklare oder gescheiterte Rückerstattung: «Nochmals senden» (retry) – nur, wenn sie im Payrexx-Konto nicht
    erscheint – oder «Ist erledigt» (done) – im Payrexx-Konto schon zurück. Beides im Protokoll."""
    if action not in ("retry", "done"):
        raise HTTPException(404, t("err.gast.unknown"))
    actor = ledger.actor_of(request)

    def go():
        r = rt.store.query_one("SELECT * FROM gast_refunds WHERE id=?", (rid,))
        if not r:
            raise HTTPException(404, t("err.gast.unknown"))
        if r["status"] not in ("unclear", "failed"):          # Doppelklick, veraltete Ansicht: nichts protokollieren
            raise HTTPException(409, t("err.gast.refund_state"))
        st, extra = ("open", "") if action == "retry" else ("done", ", done_at=?")
        args = (st, now_iso(), rid) if action == "done" else (st, rid)
        audit = lb.audit_stmt(actor, f"gast_refund_{action}", gast=r["gast_id"], amount=r["amount_cents"],
                              before=r["status"])
        res = rt.store.batch([(f"UPDATE gast_refunds SET status=?{extra}, error=NULL WHERE id=? AND "
                               f"status IN ('unclear','failed')", args),
                              # Protokoll nur, wenn der Befehl davor eben etwas geändert hat (gleichzeitiger Klick)
                              (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), audit[1]),
                              *sync_stmts(r["gast_id"])])
        if not res[0].rows_affected:
            raise HTTPException(409, t("err.gast.refund_state"))
        if action == "retry":
            process_refunds(r["gast_id"])
        return rt.store.query_one("SELECT id, gast_id, amount_cents, status, error FROM gast_refunds WHERE id=?", (rid,))
    return await db(go)
