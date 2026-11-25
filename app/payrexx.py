"""Kartenzahlung über Payrexx mit NFC am Handy (T13: direkt über NFC auf dem Smartphone).

PAYREXX-UNGETESTET: Noch nie gegen ein echtes Payrexx-Konto gelaufen (Stand Version 1.0), siehe PAYREXX_GEPRUEFT
in app/payrexx_api.py und docs/testplan-payrexx.md.


Payrexx «Tap to Pay» läuft in der Payrexx-App auf dem XCover. Andere Apps sprechen sie über einen Android-Intent an
(App-zu-App, `com.payrexx.taptopay.SOFTPOS`) und bekommen das Ergebnis als Activity-Result – das kann die Bestell-App
im Browser (Fully) nicht empfangen. Darum gibt es die kleine Begleit-App «FestPOS Tap» (android/festpos-tap):

  1. Bestell-App: POST /api/phone/payrexx -> Zahlung mit Einmal-Schlüssel (token) und Startadresse (intent://)
  2. Bestell-App startet «FestPOS Tap» (fully.startIntent), die ruft die Payrexx-App auf: Karte oder Handy (TWINT)
     an das XCover halten
  3. «FestPOS Tap» meldet das Ergebnis an den Pi: POST /api/tap/result (Port 8443, mit dem Einmal-Schlüssel) und kehrt
     zu Fully zurück; die Bestell-App fragt den Stand ab und zeigt «Bezahlt ✓»

Der Pi braucht kein Internet (nur das Handy, für die Payrexx-App). Ohne «FestPOS Tap» oder ohne Payrexx-App wird bar
oder mit dem eigenen Terminal kassiert. Kam das Ergebnis nicht an (Verbindung weg), bleibt die Zahlung «offen»; zeigte die Payrexx-App «bezahlt»,
bestätigt die Bedienung von Hand (mit Protokoll). Nie doppelt: ein offener Auftrag pro Bestellung bzw. Person, bezahlt
ist bezahlt; ein Erfolg nach «Abbrechen» wird trotzdem verbucht und im Protokoll gemeldet.
Übungsbestellungen (Testmodus): simuliert, die Payrexx-App wird nie gestartet.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

from . import devices, ledger
from . import payrexx_api as px
from . import ledger_base as lb
from .core import Config, admin, db, log, rt
from .i18n import t
from .store import StoreError, now_iso

APP_PACKAGE = "ch.festpos.tap"                       # Begleit-App (android/festpos-tap)
PAYREXX_PACKAGE = "io.github.payrexx.taptopay"       # Payrexx Tap to Pay
SIM_S = 3.0                                          # Übung: «bezahlt» nach so vielen Sekunden
OPEN_S = 24 * 3600

router = APIRouter(dependencies=[Depends(devices.phone_only)])
result_router = APIRouter()                          # Begleit-App: ohne Gerätezertifikat, mit Einmal-Schlüssel
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Einstellungen ------------------------------------------------------------------------------------------------
def config() -> dict:
    s = rt.store.setting
    return {"enabled": s("payrexx_enabled", "0") == "1", "method": s("payrexx_method", "") or "",
            "show_result": s("payrexx_show_result", "0") == "1"}


def ready(cfg: dict | None = None) -> bool:
    return (cfg or config())["enabled"]


# -- Zahlungen ------------------------------------------------------------------------------------------------------
def _row(pid: int) -> dict | None:
    return rt.store.query_one("SELECT * FROM payrexx_payments WHERE id=?", (pid,))


def _view(r: dict) -> dict:
    return {"id": r["id"], "status": r["status"], "amount": r["amount_cents"], "tip": r.get("tip_cents") or 0,
            "test": bool(r["test"]),
            "transaction": r["transaction_id"], "part": r["part"], "error": r["error"],
            "double": bool(r.get("extra_tx")),                 # T43: zweite Belastung gemeldet
            "late": r.get("late")}                             # T46: nach dem Abschluss bestätigt, zu entscheiden


def launch_url(r: dict, server: str) -> str:
    """intent://-Adresse für «FestPOS Tap» (Fully: fully.startIntent). Betrag in Franken wie die Payrexx-App."""
    cfg = config()
    q = {"id": str(r["id"]), "token": r["link"], "amount": f"{r['amount_cents'] / 100:.2f}",
         "ref": f"FestPOS-{r['order_id']:04d}-{r['id']}", "server": server, "method": cfg["method"],
         "show": "1" if cfg["show_result"] else "0"}
    extras = ";".join(f"S.{k}={quote(v, safe='')}" for k, v in q.items())
    return f"intent://pay#Intent;scheme=festpostap;package={APP_PACKAGE};{extras};end"


def start(client_uuid: str | None, amount: int, part: str, key: str, device: str, tip: int = 0,
          tab_id: int | None = None, device_id: str | None = None) -> dict:
    """Zahlung anlegen (oder die mit diesem Schlüssel / die offene dieser Bestellung bzw. Person zurückgeben).
    tip: davon Trinkgeld (app/tips.py) – steht im Betrag, den der Gast bei Payrexx zahlt.
    tab_id: offener Tisch (app/tabs.py) – bezahlt wird an den Tisch (höchstens, was offen ist), bezahlt = Zahlung des
    Tisches mit Zahlart «payrexx»."""
    old = rt.store.query_one("SELECT * FROM payrexx_payments WHERE key=?", (key,))
    if old:
        return old
    if tab_id:
        from . import tabs as tabs_mod
        tab = rt.store.query_one("SELECT * FROM tabs WHERE id=?", (tab_id,))
        if not tab or tab["closed_at"]:
            raise HTTPException(409, t("err.tab.closed"))
        s = tabs_mod._sums(rt.store, [tab_id])[tab_id]
        o = rt.store.query_one("SELECT id, test FROM orders WHERE tab_id=? ORDER BY id LIMIT 1", (tab_id,))
        if not o:
            raise HTTPException(404, t("err.payrexx.order"))
        o = {**o, "total_cents": s["open"]}            # höchstens, was am Tisch noch offen ist
        where, arg = "tab_id=?", tab_id
    else:
        o = rt.store.query_one("SELECT id, total_cents, test FROM orders WHERE client_uuid=?", (client_uuid or "",))
        if not o:
            raise HTTPException(404, t("err.payrexx.order"))
        where, arg = "order_id=?", o["id"]
    def existing() -> dict | None:
        same = rt.store.query_one(f"SELECT * FROM payrexx_payments WHERE {where} AND part=? AND status IN "
                                  "('waiting','confirmed') ORDER BY id DESC LIMIT 1", (arg, part))
        if same and not part.startswith("s"):
            if same["status"] == "confirmed":
                raise HTTPException(409, t("err.payrexx.paid"))
            if same["amount_cents"] == amount:
                return same
            raise HTTPException(409, t("err.payrexx.waiting"))
        return None
    if (same := existing()) is not None:
        return same
    if amount <= 0 or tip < 0 or tip >= amount or amount - tip > o["total_cents"]:
        raise HTTPException(409, t("err.payrexx.amount"))
    if not o["test"] and not ready():
        raise HTTPException(409, t("err.payrexx.off"))
    try:
        res = rt.store.execute("INSERT INTO payrexx_payments(key, order_id, part, amount_cents, tip_cents, status, "
                               "test, device, link, created_at, tab_id, device_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                               "RETURNING id",
                               (key, o["id"], part, amount, tip, "waiting", int(bool(o["test"])), device[:60],
                                secrets.token_urlsafe(24), now_iso(), tab_id, device_id))  # link: Einmal-Schlüssel
    except StoreError as e:
        if not e.is_unique_violation:
            raise
        # T39: anderes Handy (oder derselbe Knopf doppelt) war gleichzeitig schneller – eindeutiger Index
        old = rt.store.query_one("SELECT * FROM payrexx_payments WHERE key=?", (key,))
        if old:
            return old
        if (same := existing()) is not None:
            return same
        raise HTTPException(409, t("err.payrexx.waiting")) from None
    return _row(res.rows[0]["id"])


def refresh(pid: int) -> dict:
    r = _row(pid)
    if not r:
        raise HTTPException(404, t("err.payrexx.unknown"))
    if r["status"] == "waiting" and r["test"] and time.time() - (lb_epoch(r["created_at"]) or time.time()) >= SIM_S:
        _confirm(r, f"UEBUNG-{r['id']}", how="test")
        r = _row(pid)
    _repair(r)
    return _view(_row(pid))


def _tab_stmt(r: dict) -> tuple:
    """Zahlung an den offenen Tisch – nur wenn diese Zahlung bestätigt ist, der Tisch noch offen und der Betrag
    hineinpasst (T39/T40: geprüft im Schreibbefehl). Wiederholbar (Schlüssel payrexx-<id>): repariert auch Fälle, in
    denen die Bestätigung gespeichert wurde, die Tischzahlung aber nicht."""
    from . import tabs as tabs_mod
    part = int(r["part"][1:]) if r["part"].startswith("p") else None
    net = r["amount_cents"] - (r.get("tip_cents") or 0)             # Trinkgeld zählt über Payrexx, nicht am Tisch
    return ("INSERT OR IGNORE INTO tab_payments(key, tab_id, amount_cents, payment, tip_cents, part, by_device, "
            "created_at) SELECT ?,?,?,'payrexx',0,?,?,? WHERE EXISTS (SELECT 1 FROM payrexx_payments WHERE id=? AND "
            "status='confirmed') AND EXISTS (SELECT 1 FROM tabs WHERE id=? AND closed_at IS NULL) "
            f"AND ? <= {tabs_mod.LIMIT_SQL}",
            (f"payrexx-{r['id']}", r["tab_id"], net, part, r.get("device"), now_iso(), r["id"], r["tab_id"], net,
             *tabs_mod.limit_args(r["tab_id"])))


def _tab_booked(r: dict) -> bool:
    return bool(rt.store.scalar("SELECT 1 FROM tab_payments WHERE key=?", (f"payrexx-{r['id']}",)))


def _confirm(r: dict, tx: str | None, how: str = "nfc") -> bool:
    """Einmal «bezahlt» (aus «waiting» oder «cancelled» – Geld ist geflossen) – mit Protokoll; ganze Bestellung:
    Zahlart setzen. T39: Bestätigung, Tischzahlung und Protokoll in EINEM Batch. Passt der Betrag nicht mehr an den
    Tisch (inzwischen bar bezahlt, Tisch zu), bleibt die Zahlung bezahlt, wird aber als Zuvielzahlung gemeldet."""
    audit = lb.audit_stmt("payrexx", "payrexx_paid", order=r["order_id"], amount=r["amount_cents"], part=r["part"],
                          tip=r.get("tip_cents") or 0, transaction=tx, how=how, after_cancel=r["status"] == "cancelled",
                          test=bool(r["test"]))
    stmts = [("UPDATE payrexx_payments SET status='confirmed', transaction_id=?, paid_at=?, checked_at=?, error=NULL "
              "WHERE id=? AND status IN ('waiting','cancelled','failed')", (tx, now_iso(), now_iso(), r["id"])),
             # Protokoll nur, wenn die Bestätigung eben eine Zeile geändert hat (changes() = vorheriger Befehl)
             (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1 AND ?=0"),
              (*audit[1], int(bool(r["test"]))))]
    if r.get("tab_id"):
        stmts.append(_tab_stmt(r))
    elif not r["test"]:
        if r["part"] == "all":
            stmts.append(_payment_stmt(r))                 # T43: Zahlart der ganzen Bestellung im selben Batch
        stmts += _late_stmts(r)                            # T45: Bestellung schon abgeschlossen -> Korrektur
    res = rt.store.batch(stmts, transaction=True)
    if res[0].rows_affected != 1:
        return False
    if r.get("tab_id"):
        if not res[2].rows_affected and not _tab_booked(r):
            _overpaid(r)
    return True


def _payment_stmt(r: dict) -> tuple:
    """Zahlart «payrexx» für die ganze Bestellung – nur, wenn diese Zahlung bestätigt ist und die Bestellung offen
    (abgeschlossen: die Zahlung steht trotzdem in payrexx_payments). Wiederholbar: repariert auch verlorene Antworten."""
    return ("UPDATE orders SET payment='payrexx' WHERE id=? AND closing_id IS NULL AND COALESCE(payment,'')<>'payrexx' "
            "AND EXISTS (SELECT 1 FROM payrexx_payments WHERE id=? AND status='confirmed')", (r["order_id"], r["id"]))


def _overpaid(r: dict) -> None:
    """Bezahlt, aber am Tisch nicht mehr offen (oder Tisch zu): nicht an den Tisch buchen, melden (Verwaltung klärt:
    im Payrexx-Konto zurückerstatten oder bar herausgeben)."""
    rt.store.batch([("UPDATE payrexx_payments SET error='overpaid' WHERE id=?", (r["id"],)),
                    lb.audit_stmt("payrexx", "payrexx_overpaid", order=r["order_id"], tab=r["tab_id"],
                                  amount=r["amount_cents"], part=r["part"])])


def _repair(r: dict) -> None:
    """Bestätigt, aber Tischzahlung bzw. Zahlart fehlen (Antwort auf die Bestätigung verloren, Stand vor T39/T43):
    nachtragen."""
    if r["status"] != "confirmed":
        return
    if r.get("tab_id"):
        if r.get("error") != "overpaid" and not _tab_booked(r):
            if not rt.store.batch([_tab_stmt(r)])[0].rows_affected and not _tab_booked(r):
                _overpaid(r)
    elif r["part"] == "all" and not r["test"]:
        rt.store.batch([_payment_stmt(r)])                 # Korrekturen entstehen nur im Batch der Bestätigung (T45)


# Zurückerstattet für den Tagesabschluss (T44/T45): ausgeführt oder zugesagt (Aufträge einer Online-Bestellung) – für
# Payrexx und Trinkgeld dieselbe Sicht. Tabellenalias p = payrexx_payments.
REFUNDED_SQL = ("MAX(p.refunded_cents, COALESCE((SELECT SUM(r.amount_cents) FROM gast_refunds r "
                "WHERE p.key = 'gast-' || r.gast_id), 0))")

EXTERNAL = ("card", "twint", "other", "payrexx")   # Zahlarten ohne Bargeld: dort hat der Gast sicher zweimal bezahlt
_LATE_WHEN = ("EXISTS (SELECT 1 FROM payrexx_payments WHERE id=? AND status='confirmed' AND closing_id IS NULL) "
              "AND EXISTS (SELECT 1 FROM orders WHERE id=? AND closing_id IS NOT NULL)")   # in keinem Abschluss gezählt
# Umsatz einer Bestellung, den ANDERE Kartenzahlungen decken: gezählte voll (ohne Trinkgeld, abzüglich zurück),
# spät bestätigte nur, was sie wirklich decken – nachgebucht: der Umsatz ihrer Korrektur; offen: voll (noch zu
# entscheiden, die spätere ist dann zu viel); «Gast hat bar bezahlt» oder ganz zu viel bezahlt: nichts.
_COVERED_SQL = ("SELECT COALESCE(SUM(CASE WHEN p.late='refund' THEN 0 "
                "WHEN p.late='booked' THEN COALESCE((SELECT c.goods_cents FROM corrections c "
                "WHERE c.key = 'late:' || p.id), 0) "
                "WHEN p.late IS NULL AND p.error='overpaid' AND p.tab_id IS NULL AND p.closing_id IS NULL THEN 0 "
                f"ELSE MAX(0, p.amount_cents - {REFUNDED_SQL} - p.tip_cents) END), 0) FROM "
                "payrexx_payments p WHERE p.order_id=? AND p.id<>? AND p.status='confirmed' AND p.test=0")


def _late_plan(r: dict) -> dict:
    """T45/T46: was eine nach dem Abschluss bestätigte Kartenzahlung bedeutet. Umsatz, den andere Kartenzahlungen der
    Bestellung schon decken, oder eine Bestellung mit Zahlart ohne Bargeld (Karte, TWINT, Andere, Payrexx): zu viel
    bezahlt. Der Rest (Bestellung bar oder ohne Angabe) ist offen – nur die Kasse weiss, ob der Gast bar bezahlt hat."""
    tip = r.get("tip_cents") or 0
    o = rt.store.query_one("SELECT COALESCE(payment, 'none') AS payment, total_cents - COALESCE((SELECT "
                           "SUM(c.amount_cents) FROM cancellations c WHERE c.order_id=orders.id), 0) AS net "
                           "FROM orders WHERE id=?", (r["order_id"],)) or {"payment": "none", "net": 0}
    covered = int(rt.store.scalar(_COVERED_SQL, (r["order_id"], r["id"])) or 0)
    goods = 0 if o["payment"] in EXTERNAL else max(0, min(r["amount_cents"] - tip, o["net"] - covered))
    return {"old": o["payment"], "covered": covered, "goods": goods, "tip": tip,
            "excess": r["amount_cents"] - tip - goods}


def _late_stmts(r: dict) -> list:
    """T45/T46: Kartenzahlung zu einer schon abgeschlossenen Bestellung (abgebrochen, abgeschlossen, dann kam doch
    «bezahlt»). Nichts wird automatisch gebucht (Entscheid: C):
    - zu viel bezahlt (siehe _late_plan): melden, im Payrexx-Konto zurückerstatten – die Kasse bleibt;
    - Bestellung bar oder ohne Angabe: offen (late='open'). Die Seite Tagesabschluss fragt: Gast hat bar bezahlt
      (zurückerstatten) oder nicht (nachbuchen, admin_late). Nur bei der ersten Bestätigung (Bedingungen im SQL)."""
    p = _late_plan(r)
    ids = (r["id"], r["order_id"])
    a_open = lb.audit_stmt("payrexx", "payrexx_late_open", order=r["order_id"], amount=r["amount_cents"],
                           part=r["part"], payment=p["old"])
    a_over = lb.audit_stmt("payrexx", "payrexx_overpaid_late", order=r["order_id"], amount=r["amount_cents"],
                           part=r["part"], excess=p["excess"], payment=p["old"])
    return [("UPDATE payrexx_payments SET late='open' WHERE id=? AND late IS NULL AND ?>0 AND " + _LATE_WHEN,
             (r["id"], p["goods"], *ids)),
            (a_open[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), a_open[1]),
            ("UPDATE payrexx_payments SET error='overpaid' WHERE id=? AND COALESCE(error, '')<>'overpaid' AND ?>0 AND "
             + _LATE_WHEN, (r["id"], p["excess"], *ids)),
            (a_over[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), a_over[1])]


def _late_book_stmts(r: dict, actor: str) -> list:
    """T46: «Gast hat nicht bar bezahlt – nachbuchen»: Korrektur für den nächsten Abschluss (Payrexx und Trinkgeld
    plus, Zahlart des Umsatzes wechselt zu Payrexx, erwartete Kasse kleiner – gleicht den Fehlbetrag des alten
    Abschlusses aus). Nur aus «offen», nur einmal; fehlt die Korrektur (gleichzeitig geändert), scheitert der Batch."""
    p = _late_plan(r)
    if p["goods"] <= 0:
        return []
    key = f"late:{r['id']}"
    c = {"key": key, "kind": "late_payment", "order_id": r["order_id"], "amount_cents": p["goods"] + p["tip"],
         "goods_cents": p["goods"], "tip_cents": p["tip"], "vat": [], "created_at": now_iso(), "why": f"von:{p['old']}"}
    audit = lb.audit_stmt(actor, "payrexx_late_book", order=r["order_id"], amount=c["amount_cents"], payment=p["old"])
    return [("INSERT OR IGNORE INTO corrections(key, kind, order_id, amount_cents, goods_cents, tip_cents, vat, why, "
             "by_user, created_at, digest) SELECT ?,?,?,?,?,?,'[]',?,?,?,? WHERE " + _LATE_WHEN +
             f" AND EXISTS (SELECT 1 FROM payrexx_payments WHERE id=? AND late='open') AND ({_COVERED_SQL})=?",
             (key, c["kind"], c["order_id"], c["amount_cents"], p["goods"], p["tip"], c["why"], actor[:60],
              c["created_at"], lb.correction_digest(c), r["id"], r["order_id"], r["id"], r["order_id"], r["id"],
              p["covered"])),
            ("UPDATE payrexx_payments SET late='booked' WHERE id=? AND late='open' AND EXISTS (SELECT 1 FROM "
             "corrections WHERE key=?)", (r["id"], key)),
            (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), audit[1]),
            ("UPDATE payrexx_payments SET status=NULL WHERE id=? AND NOT EXISTS (SELECT 1 FROM corrections WHERE key=?)",
             (r["id"], key))]


def _double(r: dict, tx: str | None) -> None:
    """T43: schon bezahlt, und die Payrexx-App meldet eine ANDERE erfolgreiche Transaktion – der Gast wurde zweimal
    belastet. Nie still verwerfen: an der Zahlung vermerken und ins Protokoll (zurückerstatten in der Payrexx-App)."""
    if r["status"] != "confirmed" or not tx or tx == r["transaction_id"]:
        return                                             # dieselbe Meldung nochmals: nichts Neues
    if not r["transaction_id"]:                            # von Hand bestätigt («Payrexx zeigte bezahlt»): das ist
        if rt.store.execute("UPDATE payrexx_payments SET transaction_id=? WHERE id=? AND transaction_id IS NULL",
                            (tx, r["id"])).rows_affected:  # die erste Transaktion, keine zweite Belastung
            return
        r = _row(r["id"])                                  # ein anderes Ergebnis war eben schneller: neu vergleichen
        if tx == r["transaction_id"]:
            return
    # T44: Vermerk und Protokoll in EINEM Batch (geht die Antwort verloren, fehlt keines von beiden); Nummern exakt
    # vergleichen (durch Leerzeichen getrennt), nicht als Teil einer anderen Nummer
    audit = lb.audit_stmt("payrexx", "payrexx_double", order=r["order_id"], amount=r["amount_cents"], part=r["part"],
                          transaction=r["transaction_id"], second=tx)
    rt.store.batch([("UPDATE payrexx_payments SET extra_tx=COALESCE(extra_tx || ' ', '') || ?, double_at=? WHERE id=? AND "
                     "(extra_tx IS NULL OR instr(' ' || extra_tx || ' ', ' ' || ? || ' ')=0)",
                     (tx, now_iso(), r["id"], tx)),
                    (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), audit[1])])


def doubles_for(store, cert: dict | None) -> list[dict]:
    """T44: Doppelbelastungen der letzten 24 h, die dieses Handy gestartet hat – für /api/phone/status, damit die
    Bedienung sie auch sieht, wenn die zweite Meldung erst nach «Bezahlt ✓» kam (die App zeigt jede einmal)."""
    from datetime import datetime, timedelta
    from . import tabs as tabs_mod
    me = tabs_mod.owner_of(store, cert)
    if not me:
        return []
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    return [{"id": r["id"], "order": r["order_id"], "amount": r["amount_cents"], "tx": r["extra_tx"]}
            for r in store.query("SELECT id, order_id, amount_cents, extra_tx FROM payrexx_payments WHERE "
                                 "extra_tx IS NOT NULL AND device_id=? AND COALESCE(double_at, paid_at) >= ? ORDER BY id",
                                 (me, since))]                     # ab der Meldung der zweiten Belastung


def parse_result(raw: str) -> dict:
    """Antwort der Payrexx-App (com.payrexx.taptopay.RESULT): {"payload": "{\\"responseBody\\": {…}}"}.
    -> {ok, transaction, amount (Rappen), reference, message}."""
    try:
        outer = json.loads(raw)
        payload = json.loads(outer["payload"]) if isinstance(outer.get("payload"), str) else outer.get("payload") or {}
        body = payload.get("responseBody")
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"ok": False, "message": "format"}
    if not isinstance(body, dict):
        return {"ok": False, "message": str(body or "")[:200]}
    tr = body.get("transaction") or {}
    status = str(tr.get("status") or "").lower()
    code = str(tr.get("action_code") if tr.get("action_code") is not None else "")
    try:
        amount = round(float(tr.get("amount")) * 100)
    except (TypeError, ValueError):
        amount = None
    ok = status in ("successful", "1") and code in ("0", "")
    return {"ok": ok, "transaction": tr.get("transaction_key"), "amount": amount,
            "reference": tr.get("order_reference") or (body.get("extras") or {}).get("order_reference"),
            "message": body.get("message") or (None if ok else f"{status or '?'} {code}".strip())}


def tap_result(pid: int, token: str, ok: bool, raw: str | None, message: str | None) -> dict:
    """Ergebnis von «FestPOS Tap». Bezahlt nur mit gültigem Schlüssel, Erfolg der Payrexx-App und gleichem Betrag."""
    r = _row(pid)
    if not r or not r["link"] or not hmac.compare_digest(str(r["link"]), str(token)):
        raise HTTPException(403, t("err.payrexx.token"))
    res = parse_result(raw) if raw else {"ok": False, "message": message or "cancelled"}
    ref = f"FestPOS-{r['order_id']:04d}-{r['id']}"
    if ok and res["ok"] and res["amount"] == r["amount_cents"] and (res.get("reference") in (None, "", ref)):
        if not _confirm(r, res.get("transaction")):
            _double(_row(pid), res.get("transaction"))
    elif ok and res["ok"] and res["amount"] is None and r["status"] == "waiting":
        # T39: Erfolg ohne lesbaren Betrag – nicht als bezahlt werten; die Bedienung prüft in der Payrexx-App und
        # bestätigt von Hand («Payrexx zeigte bezahlt»)
        rt.store.batch([("UPDATE payrexx_payments SET error='unclear', checked_at=? WHERE id=? AND status='waiting'",
                         (now_iso(), pid)),
                        lb.audit_stmt("payrexx", "payrexx_unclear", order=r["order_id"], expected=r["amount_cents"],
                                      transaction=res.get("transaction"))])
    elif r["status"] == "waiting":
        rt.store.execute("UPDATE payrexx_payments SET status='failed', error=?, checked_at=? WHERE id=? AND "
                         "status='waiting'", ((res.get("message") or "failed")[:200], now_iso(), pid))
        if res["ok"]:                                          # Erfolg, aber anderer Betrag/Bezug: auffällig
            lb.audit(rt.store, "payrexx", "payrexx_mismatch", order=r["order_id"], expected=r["amount_cents"],
                     got=res.get("amount"), transaction=res.get("transaction"))
    return _view(_row(pid))


def may_touch(r: dict, cert: dict | None) -> bool:
    """T39: Bestätigen von Hand, Abbrechen und Abfragen – offener Tisch: wer ihn abrechnen darf; sonst das Handy, das
    die Zahlung gestartet hat, oder die Festleitung."""
    from . import tabs as tabs_mod
    if r.get("tab_id"):
        tab = rt.store.query_one("SELECT * FROM tabs WHERE id=?", (r["tab_id"],)) or {}
        return tabs_mod.may_settle(rt.store, tab, cert)
    if r.get("device_id") == tabs_mod.owner_of(rt.store, cert):     # ohne Zertifikat (nur Tests): beide leer
        return True
    return lb.device_role(rt.store, cert) == "lead"


def _guard(pid: int, cert: dict | None) -> None:
    r = _row(pid)
    if not r:
        raise HTTPException(404, t("err.payrexx.unknown"))
    if not may_touch(r, cert):
        raise HTTPException(403, t("err.payrexx.not_yours"))


def manual(pid: int, actor: str) -> dict:
    """Die Payrexx-App zeigte «bezahlt», das Ergebnis kam aber nicht an: Bedienung bestätigt (Protokoll)."""
    r = _row(pid)
    if not r:
        raise HTTPException(404, t("err.payrexx.unknown"))
    _confirm(r, None, how=f"manual:{actor}"[:60])
    return _view(_row(pid))


def cancel(pid: int) -> dict:
    r = _row(pid)
    if not r:
        raise HTTPException(404, t("err.payrexx.unknown"))
    rt.store.execute("UPDATE payrexx_payments SET status='cancelled' WHERE id=? AND status='waiting'", (pid,))
    return _view(_row(pid))


def lb_epoch(iso: str | None) -> float | None:
    from .timesync import local_epoch
    return local_epoch(iso)


def expire_open() -> int:
    """Offene Zahlungen nach 24 h als gescheitert markieren (Hintergrund)."""
    n = 0
    for r in rt.store.query("SELECT id, created_at FROM payrexx_payments WHERE status='waiting'"):
        at = lb_epoch(r["created_at"])
        if at and time.time() - at > OPEN_S:
            n += rt.store.execute("UPDATE payrexx_payments SET status='failed', error='expired' WHERE id=? AND "
                                  "status='waiting'", (r["id"],)).rows_affected
    return n




def period(store, order_ids: list[int]) -> dict:
    """Payrexx im Tagesabschluss: Summe, Anzahl und Liste (mit Transaktionsnummer) für diese Bestellungen."""
    if not order_ids:
        return {"count": 0, "cents": 0, "manual": {"count": 0, "cents": 0}, "list": []}
    marks = ",".join("?" * len(order_ids))
    # T22: Zurückerstattetes zählt nicht mehr. T44: auch zugesagte Rückerstattungen einer Online-Bestellung (offen,
    # unklar, gescheitert) – jeder Auftrag endet als «zurück» (automatisch, «Nochmals senden» oder «Ist erledigt»); so
    # stimmt ein Abschluss auch, wenn Payrexx beim Abschliessen gerade nicht erreichbar war
    rows = store.query(f"SELECT p.id, p.key, p.order_id, p.part, p.amount_cents - {REFUNDED_SQL} AS amount_cents, "
                       f"p.transaction_id, p.paid_at FROM payrexx_payments p WHERE p.status='confirmed' AND p.test=0 "
                       f"AND p.order_id IN ({marks}) ORDER BY p.id", tuple(order_ids))
    # Review 2026-11, F1: «bezahlt» ohne Transaktionsnummer hat die Bedienung von Hand bestätigt («Payrexx zeigte
    # bezahlt») – der Pi hat es nie von Payrexx gehört. Im Kassensturz zählt es trotzdem als nicht in der Kasse; darum
    # steht es im Abschluss eigens da, zum Vergleichen mit der Abrechnung von Payrexx. (Online-Bestellungen «gast-…»
    # hat der Server selbst bei Payrexx nachgefragt.)
    manual = [r for r in rows if not r["transaction_id"] and not str(r["key"] or "").startswith("gast-")]
    return {"count": len(rows), "cents": sum(r["amount_cents"] for r in rows),
            "manual": {"count": len(manual), "cents": sum(r["amount_cents"] for r in manual)},
            "list": [{"order": r["order_id"], "part": r["part"], "amount": r["amount_cents"],
                      "transaction": r["transaction_id"], "at": r["paid_at"]} for r in rows]}


# -- Handy -------------------------------------------------------------------------------------------------------------
class StartIn(BaseModel):
    client_uuid: str | None = Field(default=None, min_length=8, max_length=64)
    tab_id: int | None = Field(default=None, ge=1)                         # offener Tisch (app/tabs.py)
    amount_cents: int = Field(gt=0, le=10_000_000)
    tip_cents: int = Field(default=0, ge=0, le=100_000)                 # davon Trinkgeld (app/tips.py)
    part: str = Field(default="all", pattern=r"^(all|p\d{1,2}|s\d{1,2})$")
    key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("key")
    @classmethod
    def _not_reserved(cls, v: str) -> str:
        if v.lower().startswith("gast-"):              # «gast-…» vergibt nur der Server (QR-Bestellung, app/gast.py)
            raise ValueError("reserved")
        return v


def _server() -> str:
    """Adresse, unter der «FestPOS Tap» den Pi erreicht: Verwaltungs-Port der VIP (ohne Gerätezertifikat)."""
    from . import netz
    host = netz.vip()
    port = Config.admin_port or 8443
    return f"https://{host}:{port}"


@router.post("/api/phone/payrexx")
async def phone_start(b: StartIn, request: Request):
    from urllib.parse import unquote
    device = unquote(request.headers.get("X-Festpos-Device") or "")
    if b.tab_id:                                       # offener Tisch: nur wer ihn abrechnen darf
        from . import tabs as tabs_mod
        tab = await db(rt.store.query_one, "SELECT * FROM tabs WHERE id=?", (b.tab_id,))
        if not tab:
            raise HTTPException(404, t("err.tab.unknown"))
        if not await db(tabs_mod.may_settle, rt.store, tab, devices.client_cert(request)):
            raise HTTPException(403, t("err.tab.no_right", owner=tab.get("owner_name") or "?"))
    from . import tabs as tabs_mod
    me = await db(tabs_mod.owner_of, rt.store, devices.client_cert(request))
    r = await db(start, b.client_uuid, b.amount_cents, b.part, b.key, device, b.tip_cents, b.tab_id, me)
    # T39: eine offene Zahlung eines anderen Handys nie zurückgeben – dieses könnte sie weder abfragen noch abbrechen
    if r["status"] == "waiting" and not await db(may_touch, r, devices.client_cert(request)):
        raise HTTPException(409, t("err.payrexx.waiting"))
    # T44: von selbst starten nur eine eben angelegte Zahlung (dieser Schlüssel, noch kein Ergebnis). Eine bestehende
    # wird nur angezeigt («Nochmals an das Handheld halten» bleibt ein bewusster Tipp); eine unklare bekommt gar keine
    # Startadresse – sie kann schon bezahlt sein
    fresh = r["key"] == b.key and r["status"] == "waiting" and not r.get("error") and not r.get("checked_at")
    launch = None if r["test"] or r.get("error") == "unclear" else launch_url(r, _server())
    return {**_view(r), "launch": launch, "autostart": bool(fresh and launch)}


@router.get("/api/phone/payrexx/{pid}")
async def phone_status(pid: int, request: Request):
    await db(_guard, pid, devices.client_cert(request))
    return await db(refresh, pid)


@router.post("/api/phone/payrexx/{pid}/cancel")
async def phone_cancel(pid: int, request: Request):
    await db(_guard, pid, devices.client_cert(request))
    return await db(cancel, pid)


@router.post("/api/phone/payrexx/{pid}/manual")
async def phone_manual(pid: int, request: Request):
    from urllib.parse import unquote
    await db(_guard, pid, devices.client_cert(request))
    return await db(manual, pid, unquote(request.headers.get("X-Festpos-Device") or "") or "?")


class ResultIn(BaseModel):
    id: int
    token: str = Field(min_length=10, max_length=80)
    ok: bool = False
    result: str | None = Field(default=None, max_length=20000)     # Antwort der Payrexx-App, unverändert
    message: str | None = Field(default=None, max_length=300)


@result_router.post("/api/tap/result")
async def tap_result_route(b: ResultIn):
    return await db(tap_result, b.id, b.token, b.ok, b.result, b.message)


# -- Verwaltung (nur Admin) -------------------------------------------------------------------------------------------
class SettingsIn(BaseModel):
    enabled: bool = False
    method: str = Field(default="", pattern="^(|CONTACTLESS|TWINT)$")     # leer: die Payrexx-App fragt
    show_result: bool = False


def admin_view() -> dict:
    return {**config(), "tested": px.PAYREXX_GEPRUEFT,
            "app_package": APP_PACKAGE, "payrexx_package": PAYREXX_PACKAGE, "apk": tap_apk_meta(),
            "apk_here": tap_apk_path().is_file(), "sent": tap_sent()}


@admin_router.get("/payrexx")
async def get_settings():
    return await db(admin_view)


@admin_router.put("/payrexx")
async def put_settings(b: SettingsIn, request: Request):
    vals = {"payrexx_enabled": "1" if b.enabled else "0", "payrexx_method": b.method,
            "payrexx_show_result": "1" if b.show_result else "0"}

    def save():
        stmts = [("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (k, v)) for k, v in vals.items()]
        stmts.append(lb.audit_stmt(ledger.actor_of(request), "payrexx_settings", enabled=b.enabled, method=b.method))
        rt.store.batch(stmts)
    await db(save)
    return await db(admin_view)


# -- Begleit-App «FestPOS Tap» auf die Handys (über Fully, wie die Fully-APK in T6) --------------------------------------
def tap_apk_path():
    from . import fully
    return fully.APK_DIR / "festpos-tap.apk"


def tap_apk_meta() -> dict | None:
    try:
        return json.loads(rt.store.setting("payrexx_tap_apk") or "null")
    except ValueError:
        return None


@admin_router.post("/payrexx/apk")
async def upload_tap_apk(request: Request, file: UploadFile = File(...)):
    """APK von «FestPOS Tap» (Release-Anhang festpos-tap.apk) auf diesen Pi; nur dieses Paket wird angenommen."""
    import hashlib
    import os
    from . import fully_apk
    data = await file.read(30 * 1024 * 1024 + 1)
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, t("err.payrexx.apk"))
    dst = tap_apk_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".festpos-tap.{secrets.token_hex(3)}")
    tmp.write_bytes(data)
    info = await db(fully_apk.apk_manifest, tmp, file.filename or "")
    if info.get("package") != APP_PACKAGE:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, t("err.payrexx.apk"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, dst)
    meta = {"version": info.get("version_name"), "code": info.get("version_code"), "node": Config.node,
            "checksum": hashlib.sha256(data).hexdigest(), "at": int(time.time())}
    await db(rt.store.execute, "INSERT INTO settings(key,value) VALUES ('payrexx_tap_apk',?) ON CONFLICT(key) DO UPDATE "
                               "SET value=excluded.value", (json.dumps(meta),))
    await db(lb.audit, rt.store, ledger.actor_of(request), "payrexx_tap_apk", version=meta["version"])
    return meta


def tap_sent() -> dict:
    """Pro Handheld (IP): welche Version von «FestPOS Tap» zuletzt an Fully ging – Fully meldet die installierten
    Apps nicht, darum merkt sich der Pi, was er geschickt hat. {ip: {"version", "at", "state"}}"""
    try:
        d = json.loads(rt.store.setting("payrexx_tap_sent") or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


class TapInstallIn(BaseModel):
    ip: str | None = Field(default=None, max_length=64)       # nur dieses Handheld (Geräteansicht); leer = alle


_tap_lock = threading.Lock()                    # payrexx_tap_sent: mehrere Handhelds melden gleichzeitig
_tap_tasks: set = set()


def _tap_note(ip: str, change) -> dict:
    with _tap_lock:
        sent = tap_sent()
        sent[ip] = change(dict(sent.get(ip) or {}))
        rt.store.execute("INSERT INTO settings(key,value) VALUES ('payrexx_tap_sent',?) ON CONFLICT(key) DO UPDATE "
                         "SET value=excluded.value", (json.dumps(sent),))
        return sent


async def _tap_install(x: dict, cfg: dict, url: str, version: str, actor: str) -> None:
    """Im Hintergrund (T30): Sperren aus, installieren, auf Fully warten (getInstallApkState), Sperren wieder ein."""
    from . import fully
    try:
        r = await fully.install_apk(x["ip"], cfg["port"], x["pin"], cfg["password"], url, wait=True)
    except Exception as e:  # noqa: BLE001 – der Stand darf nicht auf «wird installiert» hängen bleiben
        log.warning("FestPOS Tap auf %s: %s", x["ip"], e)
        r = {"state": "bad"}
    now, st = int(time.time()), r.get("state")

    def change(old: dict) -> dict:
        old.pop("installing", None)
        old.pop("installing_at", None)
        if st in ("installed", "sent", "ok"):
            return {"version": version, "at": now, "state": "installed" if st == "installed" else "sent",
                    "locks": r.get("locks") or []}
        return {**old, "failed_at": now, "failed": str(st or "")[:40], "locks": r.get("locks") or []}
    await db(_tap_note, x["ip"], change)
    await db(lb.audit, rt.store, actor, "payrexx_tap_install", version=version, ip=x["ip"], state=st,
             locks=", ".join(r.get("locks") or []) or None)
    log.info("FestPOS Tap %s auf %s (%s): %s", version, x.get("name"), x["ip"], st)


@admin_router.post("/payrexx/apk/install")
async def install_tap_apk(request: Request, b: TapInstallIn | None = None):
    """Fully installiert «FestPOS Tap» auf allen Handhelds mit Fully-Fernverwaltung oder auf einem (loadApkFile, ohne
    Rückfrage; dieselbe Datei installiert neu oder aktualisiert). T30: im Hintergrund – Installationssperren kurz aus,
    Ergebnis von Fully abwarten; der Stand steht in GET /payrexx › sent (installing, dann installed | sent | failed)."""
    from . import fully
    meta = await db(tap_apk_meta)
    if not meta or not tap_apk_path().is_file():
        raise HTTPException(409, t("err.payrexx.no_apk"))
    cfg = await db(fully.config)
    if not cfg["password"]:
        raise HTTPException(422, t("err.fully.no_password"))
    url = f"http://{fully._pi_host(request)}/fully/festpos-tap.apk"
    one_ip = b.ip if b else None
    targets = [x for x in cfg["targets"] if x.get("pin") and (x["ip"] == one_ip if one_ip else x.get("active", True))]
    if one_ip and not targets:
        raise HTTPException(404, t("err.payrexx.no_fully"))

    import asyncio
    version, actor, now = meta.get("version") or "", ledger.actor_of(request), int(time.time())
    for x in targets:
        await db(_tap_note, x["ip"], lambda old: {**old, "installing": version, "installing_at": now})
        task = asyncio.create_task(_tap_install(x, cfg, url, version, actor))
        _tap_tasks.add(task)
        task.add_done_callback(_tap_tasks.discard)
    return {"results": [{"name": x.get("name") or x["ip"], "ip": x["ip"], "state": "installing"} for x in targets],
            "sent": await db(tap_sent)}


@admin_router.post("/payrexx/payments/{pid}/cancel")
async def admin_cancel_payment(pid: int, request: Request):
    """T45 (B1): offene Kartenzahlung in der Verwaltung abbrechen – sie sperrt sonst den Tagesabschluss. Kommt danach
    doch noch «bezahlt», wird sie trotzdem verbucht (Geld ist geflossen, wie am Handy)."""
    actor = ledger.actor_of(request)

    def go():
        r = _row(pid)
        if not r:
            raise HTTPException(404, t("err.payrexx.unknown"))
        audit = lb.audit_stmt(actor, "payrexx_cancel_admin", order=r["order_id"], amount=r["amount_cents"],
                              device=r.get("device"))
        res = rt.store.batch([("UPDATE payrexx_payments SET status='cancelled' WHERE id=? AND status='waiting'", (pid,)),
                              (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"), audit[1])])
        if not res[0].rows_affected:
            raise HTTPException(409, t("err.payrexx.not_waiting"))
        return _view(_row(pid))
    return await db(go)


@admin_router.post("/payrexx/payments/{pid}/late/{action}")
async def admin_late(pid: int, action: str, request: Request):
    """T46 (Entscheid: C): Kartenzahlung, nach dem Abschluss bestätigt, Bestellung bar abgeschlossen.
    refund = Gast hat bar bezahlt, im Payrexx-Konto zurückerstattet (die Kasse bleibt); book = Gast hat nicht bar
    bezahlt, nachbuchen (Korrektur im nächsten Abschluss). Beides im Protokoll, nur einmal."""
    if action not in ("refund", "book"):
        raise HTTPException(404, t("err.payrexx.unknown"))
    actor = ledger.actor_of(request)

    def go():
        r = _row(pid)
        if not r:
            raise HTTPException(404, t("err.payrexx.unknown"))
        if r.get("late") != "open":
            raise HTTPException(409, t("err.payrexx.late_state"))
        if action == "refund":
            audit = lb.audit_stmt(actor, "payrexx_late_refund", order=r["order_id"], amount=r["amount_cents"])
            res = rt.store.batch([("UPDATE payrexx_payments SET late='refund' WHERE id=? AND late='open'", (pid,)),
                                  (audit[0].replace(" VALUES (?,?,?,?)", " SELECT ?,?,?,? WHERE changes()=1"),
                                   audit[1])])
            if not res[0].rows_affected:
                raise HTTPException(409, t("err.payrexx.late_state"))
        else:
            stmts = _late_book_stmts(r, actor)
            if not stmts:
                raise HTTPException(409, t("err.payrexx.late_state"))
            try:
                rt.store.batch(stmts)
            except StoreError as e:                          # gleichzeitig entschieden oder anders bezahlt
                if "NOT NULL constraint failed: payrexx_payments.status" not in str(e):
                    raise
                raise HTTPException(409, t("err.payrexx.late_state")) from None
        return _view(_row(pid))
    return await db(go)


@admin_router.get("/payrexx/payments")
async def list_payments(limit: int = 200):
    rows = await db(rt.store.query, "SELECT p.id, p.order_id, p.part, p.amount_cents, p.status, p.transaction_id, p.extra_tx, "
                                    "p.created_at, p.paid_at, p.test, p.error, o.closing_id FROM payrexx_payments p "
                                    "LEFT JOIN orders o ON o.id=p.order_id ORDER BY p.id DESC LIMIT ?", (min(limit, 1000),))
    return {"payments": rows}
