"""Datenbank-Abstraktion: gleiche API für lokales SQLite und den rqlite-Cluster.

    store = open_store("sqlite:///var/lib/festpos/festpos.db")   # Einzelbetrieb / Tests
    store = open_store("rqlite://127.0.0.1:4001")                # Cluster

rqlite kennt keine interaktiven Transaktionen. Deshalb gibt es nur:
  query()  – Lesen
  execute()– ein Statement
  batch()  – mehrere Statements atomar (alles oder nichts)
Die Fachlogik ist so geschrieben, dass sie damit auskommt (Subqueries, RETURNING, Compare-and-Set).
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .backup import no_wal as _no_wal

SCHEMA = Path(__file__).with_name("schema.sql")

# Spalten, die nach der ersten Version dazukamen (idempotent, Fehler "duplicate column" = schon da)
MIGRATIONS = [
    "ALTER TABLE print_jobs ADD COLUMN note TEXT",
    "ALTER TABLE device_certs ADD COLUMN expires_at TEXT",
    "ALTER TABLE device_certs ADD COLUMN revoked_at TEXT",
    "ALTER TABLE products ADD COLUMN options TEXT",
    "ALTER TABLE order_items ADD COLUMN line_no INTEGER",
    "ALTER TABLE order_items ADD COLUMN options TEXT",
    "ALTER TABLE order_items ADD COLUMN note TEXT",
    "ALTER TABLE order_items ADD COLUMN category TEXT",
    "ALTER TABLE order_items ADD COLUMN cat_sort INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE printers ADD COLUMN fail_mode TEXT NOT NULL DEFAULT 'wait'",
    "ALTER TABLE printers ADD COLUMN fail_target INTEGER",
    "ALTER TABLE printers ADD COLUMN fail_after INTEGER NOT NULL DEFAULT 60",
    "ALTER TABLE products ADD COLUMN color TEXT",
    "ALTER TABLE products ADD COLUMN delivery INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE order_items ADD COLUMN delivery TEXT",
    "ALTER TABLE print_jobs ADD COLUMN claimed_at TEXT",
    "ALTER TABLE device_certs ADD COLUMN replaces TEXT",
    "ALTER TABLE print_jobs ADD COLUMN reprint INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE print_jobs ADD COLUMN delivery TEXT",
    "ALTER TABLE products ADD COLUMN delivery_allowed TEXT",
    "ALTER TABLE products ADD COLUMN delivery_required INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN test INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE print_jobs ADD COLUMN error_code TEXT",
    "ALTER TABLE printers ADD COLUMN language TEXT",
    # 2026.10.10-20: Kassenbuch (Zahlart, Storno, Tagesabschluss, MWST)
    "ALTER TABLE orders ADD COLUMN payment TEXT",
    "ALTER TABLE orders ADD COLUMN digest TEXT",
    "ALTER TABLE orders ADD COLUMN closing_id INTEGER",
    "ALTER TABLE order_items ADD COLUMN vat_code TEXT",
    "ALTER TABLE order_items ADD COLUMN vat_bp INTEGER",
    "ALTER TABLE categories ADD COLUMN vat_code TEXT",
    "ALTER TABLE products ADD COLUMN vat_code TEXT",
    "ALTER TABLE orders ADD COLUMN device_cert TEXT",
    "CREATE INDEX IF NOT EXISTS idx_orders_open ON orders(closing_id, test)",
    "CREATE INDEX IF NOT EXISTS idx_cancel_order ON cancellations(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_cancel_items ON cancellation_items(cancellation_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at)",
    # Review 2026-10: gleichzeitige Stornos/Quittungen derselben Bestellung – nur die erste gilt, die andere liest neu
    "ALTER TABLE cancellations ADD COLUMN seq INTEGER",
    "ALTER TABLE receipts ADD COLUMN seq INTEGER",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cancel_seq ON cancellations(order_id, seq)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_seq ON receipts(order_uuid, seq)",
    # Ausverkauft und Restmenge (app/stock.py)
    "ALTER TABLE products ADD COLUMN soldout INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE products ADD COLUMN stock INTEGER CHECK (stock IS NULL OR stock >= 0)",
    "ALTER TABLE products ADD COLUMN stock_warn INTEGER",
    # T5: Gerät endgültig löschen – die Zeile bleibt für die Sperrliste, erscheint aber nirgends mehr
    "ALTER TABLE device_certs ADD COLUMN removed_at TEXT",
    # T1: Gerät über eine ID statt über den Namen (Auffüllen: device_ids.backfill in init_schema)
    "ALTER TABLE device_certs ADD COLUMN device_id TEXT",
    # T15: Drucker hat die Statusabfrage schon einmal beantwortet -> schweigt er, nicht blind senden
    "ALTER TABLE printers ADD COLUMN status_seen INTEGER NOT NULL DEFAULT 0",
    # T13: Kartenzahlung über Payrexx (ältere Datenbanken: Tabelle anlegen)
    """CREATE TABLE IF NOT EXISTS payrexx_payments (                 -- Kartenzahlung über Payrexx (T13)
    id             INTEGER PRIMARY KEY,
    key            TEXT    NOT NULL UNIQUE,                  -- Schlüssel vom Handy: nochmals senden = gleiche Zahlung
    order_id       INTEGER NOT NULL,
    part           TEXT    NOT NULL DEFAULT 'all',           -- all | p<n> (Einzeln, Person n) | s<n> (Teilen)
    amount_cents   INTEGER NOT NULL,
    gateway_id     INTEGER,
    link           TEXT,
    status         TEXT    NOT NULL,                         -- new | waiting | confirmed | failed | cancelled | error
    transaction_id TEXT,
    test           INTEGER NOT NULL DEFAULT 0,               -- Übung: simuliert, nie bei Payrexx
    device         TEXT,
    error          TEXT,
    created_at     TEXT    NOT NULL,
    checked_at     TEXT,
    paid_at        TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_payrexx_order ON payrexx_payments(order_id)",
    # T22: QR-Bestellung am Tisch – Rückerstattungen zählen im Tagesabschluss nicht mehr mit
    "ALTER TABLE payrexx_payments ADD COLUMN refunded_cents INTEGER NOT NULL DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS gast_orders (                      -- QR-Bestellung am Tisch (T22)
    id             INTEGER PRIMARY KEY,
    token          TEXT    NOT NULL UNIQUE,                  -- Beleg-Adresse des Gastes (/?b=…)
    key            TEXT    NOT NULL UNIQUE,                  -- Schlüssel vom Handy: nochmals tippen = gleiche Bestellung
    table_label    TEXT    NOT NULL,
    lines          TEXT    NOT NULL,                         -- JSON: [{product_id, qty, options, name, price}]
    amount_cents   INTEGER NOT NULL,
    lang           TEXT,
    status         TEXT    NOT NULL,                         -- new | waiting | paid | ordered | rejected | refunded | cancelled | error
    reason         TEXT,                                     -- soldout | printer | order | guest | expired | …
    gateway_id     INTEGER,
    link           TEXT,
    transaction_id TEXT,
    method         TEXT,
    order_id       INTEGER,                                  -- Bestellung in FestPOS (erst nach der Zahlung)
    refunded_cents INTEGER NOT NULL DEFAULT 0,
    refund_open    INTEGER NOT NULL DEFAULT 0,               -- Rückerstattung gescheitert: von Hand nötig
    ip             TEXT,                                     -- nur Prüfsumme (Bremse), keine Adresse
    error          TEXT,
    created_at     TEXT    NOT NULL,
    checked_at     TEXT,
    paid_at        TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_gast_order ON gast_orders(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_gast_status ON gast_orders(status)",
    # Trinkgeld (app/tips.py): an der Kasse pro Bestellung, mit Payrexx im Betrag enthalten
    """CREATE TABLE IF NOT EXISTS tips (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    device       TEXT,
    updated_at   TEXT    NOT NULL
)""",
    "ALTER TABLE payrexx_payments ADD COLUMN tip_cents INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE gast_orders ADD COLUMN tip_cents INTEGER NOT NULL DEFAULT 0",
    # Tischplan (app/plan.py): Lage der Tische (die Tabelle plan_areas legt schema.sql an)
    "ALTER TABLE tables ADD COLUMN area_id INTEGER",
    "ALTER TABLE tables ADD COLUMN px INTEGER",
    "ALTER TABLE tables ADD COLUMN py INTEGER",
    "ALTER TABLE tables ADD COLUMN pw INTEGER",
    "ALTER TABLE tables ADD COLUMN ph INTEGER",
    "ALTER TABLE tables ADD COLUMN rot INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tables ADD COLUMN shape TEXT",
    # Offene Tische (app/tabs.py; die Tabellen tabs und tab_payments legt schema.sql an)
    "ALTER TABLE orders ADD COLUMN tab_id INTEGER",
    "CREATE INDEX IF NOT EXISTS idx_orders_tab ON orders(tab_id)",
    "CREATE INDEX IF NOT EXISTS idx_tabpay_tab ON tab_payments(tab_id)",
    "ALTER TABLE tabs ADD COLUMN uuid TEXT",
    "ALTER TABLE payrexx_payments ADD COLUMN tab_id INTEGER",
    # Nachbuchen ohne Bon (schon geliefert): keine Bons, sonst wie jede Bestellung
    "ALTER TABLE orders ADD COLUMN no_print INTEGER NOT NULL DEFAULT 0",
    # T37: Rückerstattungen als Aufträge (Tabelle gast_refunds aus schema.sql) – bisherige Beträge übernehmen,
    # sonst rechnet FestPOS sie aus den Aufträgen zu null (wiederholbar: Schlüssel legacy:…)
    # Übernommen wird nur, was noch nicht in Aufträgen steht (Differenz) – so auch eine Rückerstattung, die ein Pi mit
    # der alten Version während des Updates noch gebucht hat; danach ist die Differenz 0 (wiederholbar)
    "INSERT OR IGNORE INTO gast_refunds(key, gast_id, amount_cents, why, status, created_at, done_at) "
    "SELECT 'legacy:' || g.id || ':' || g.refunded_cents, g.id, g.refunded_cents - COALESCE((SELECT SUM(r.amount_cents) "
    "FROM gast_refunds r WHERE r.gast_id=g.id AND r.status='done'), 0), 'vor T37', 'done', "
    "COALESCE(g.paid_at, g.created_at), COALESCE(g.paid_at, g.created_at) FROM gast_orders g WHERE g.refunded_cents > "
    "COALESCE((SELECT SUM(r.amount_cents) FROM gast_refunds r WHERE r.gast_id=g.id AND r.status='done'), 0)",
    "INSERT OR IGNORE INTO gast_refunds(key, gast_id, amount_cents, why, status, error, created_at) "
    "SELECT 'legacy-open:' || g.id || ':' || g.refund_open, g.id, g.refund_open - COALESCE((SELECT SUM(r.amount_cents) "
    "FROM gast_refunds r WHERE r.gast_id=g.id AND r.status IN ('open','sending','unclear','failed')), 0), 'vor T37', "
    "'failed', g.error, COALESCE(g.paid_at, g.created_at) FROM gast_orders g WHERE g.refund_open > "
    "COALESCE((SELECT SUM(r.amount_cents) FROM gast_refunds r WHERE r.gast_id=g.id AND "
    "r.status IN ('open','sending','unclear','failed')), 0)",
    # T39: Kartenzahlung – startendes Gerät (Rechte für Bestätigen/Abbrechen), höchstens eine offene pro Bestellung
    # bzw. Tisch und Teil (Teilen «s…» ausgenommen). Ältere Doppelte zuerst als gescheitert markieren.
    "ALTER TABLE payrexx_payments ADD COLUMN device_id TEXT",
    "UPDATE payrexx_payments SET status='failed', error='duplicate' WHERE status='waiting' AND part NOT LIKE 's%' "
    "AND id NOT IN (SELECT MAX(id) FROM payrexx_payments WHERE status='waiting' AND part NOT LIKE 's%' "
    "GROUP BY CASE WHEN tab_id IS NULL THEN 'o' || order_id ELSE 't' || tab_id END, part)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_payrexx_wait_order ON payrexx_payments(order_id, part) "
    "WHERE status='waiting' AND tab_id IS NULL AND part NOT LIKE 's%'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_payrexx_wait_tab ON payrexx_payments(tab_id, part) "
    "WHERE status='waiting' AND tab_id IS NOT NULL AND part NOT LIKE 's%'",
    # T41: Version der Prüfsumme einer Quittung (leer = 1: ohne data)
    "ALTER TABLE receipts ADD COLUMN digest_v INTEGER",
    # T43: Zeitpunkt des Abbruchs durch FestPOS (Nachkontrolle zählt ab dann) und Doppelbelastung bei Kartenzahlung
    "ALTER TABLE gast_orders ADD COLUMN cancelled_at TEXT",
    "ALTER TABLE payrexx_payments ADD COLUMN extra_tx TEXT",
    "ALTER TABLE payrexx_payments ADD COLUMN double_at TEXT",            # T44: wann die zweite Belastung gemeldet wurde
    # T45: in welchem Abschluss eine Kartenzahlung gezählt wurde – nur eine ungezählte kann «früherer Tage» sein.
    # Bisherige: bestätigt und Bestellung abgeschlossen = gezählt (wiederholbar, ausser es gibt schon eine Korrektur)
    "ALTER TABLE payrexx_payments ADD COLUMN closing_id INTEGER",
    # T46: spät bestätigt zu einer bar abgeschlossenen Bestellung – die Verwaltung entscheidet (zurück oder nachbuchen)
    "ALTER TABLE payrexx_payments ADD COLUMN late TEXT",
    "ALTER TABLE users ADD COLUMN totp TEXT",                 # zweiter Faktor (twofa.py, T-0-N/FestPOS#9)
    "UPDATE payrexx_payments SET closing_id=(SELECT o.closing_id FROM orders o WHERE o.id=payrexx_payments.order_id) "
    "WHERE closing_id IS NULL AND status='confirmed' AND EXISTS (SELECT 1 FROM orders o WHERE "
    "o.id=payrexx_payments.order_id AND o.closing_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM corrections c "
    "WHERE c.key = 'late:' || payrexx_payments.id) "
    # läuft bei jedem Start: spät bestätigte (T46: offen/entschieden, zu viel bezahlt) waren in keinem Abschluss
    "AND late IS NULL AND COALESCE(error, '') <> 'overpaid'",
]

Params = Sequence[Any]
Stmt = tuple[str, Params]


class StoreError(Exception):
    def __init__(self, msg: str, index: int | None = None):
        super().__init__(msg)
        self.index = index

    @property
    def is_unique_violation(self) -> bool:
        return "UNIQUE constraint failed" in str(self)


class StoreUnavailable(StoreError):
    """Keine Mehrheit / Knoten nicht erreichbar – Schreiben aktuell unmöglich."""


class StoreUnclear(StoreUnavailable):
    """T36: Schreibbefehl gesendet, Antwort verloren – er kann ausgeführt sein oder nicht. Nie blind wiederholen:
    nochmals nur mit demselben Schlüssel (client_uuid, key) oder nach erneutem Lesen."""


@dataclass
class Result:
    last_id: int | None = None
    rows_affected: int = 0
    rows: list[dict] = field(default_factory=list)   # bei RETURNING


def now_iso() -> str:
    """Zeitstempel setzt immer die App (deterministisch für die Replikation)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def schema_statements() -> list[str]:
    text = re.sub(r"--[^\n]*", "", SCHEMA.read_text(encoding="utf-8"))
    return [s.strip() for s in re.split(r";\s*\n", text) if s.strip()]


class Store:
    kind = "abstract"

    def query(self, sql: str, params: Params = ()) -> list[dict]: ...
    def batch(self, stmts: list[Stmt], transaction: bool = True) -> list[Result]: ...
    def is_leader(self) -> bool: ...
    def leader_state(self) -> bool | None:
        """True = Leader, False = sicher nicht Leader, None = gerade unbekannt (Fehler/Timeout)."""
        return self.is_leader()
    def cluster_info(self) -> dict: ...
    def backup(self) -> bytes: ...
    def restore(self, data: bytes) -> None: ...

    # -- Komfort ---------------------------------------------------------
    def query_one(self, sql: str, params: Params = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Params = ()) -> Any:
        row = self.query_one(sql, params)
        return next(iter(row.values())) if row else None

    def execute(self, sql: str, params: Params = ()) -> Result:
        return self.batch([(sql, params)], transaction=False)[0]

    def init_schema(self) -> None:
        self.batch([(s, ()) for s in schema_statements()], transaction=True)
        for sql in MIGRATIONS:                       # für Datenbanken aus älteren Versionen
            try:
                self.execute(sql)
            except StoreError as e:
                if "duplicate column" not in str(e):
                    raise
        from . import device_ids                    # Geräte-ID nachtragen (T1) – wiederholbar
        device_ids.backfill(self)

    def setting(self, key: str, default: str = "") -> str:
        v = self.scalar("SELECT value FROM settings WHERE key=?", (key,))
        if v is not None and key in _secret_keys():                     # Geheimnis: verschlüsselt abgelegt (#8)
            from . import secretbox
            v = secretbox.unseal(key, v)
            if v is None:                                              # verschlüsselt, aber nicht lesbar
                logging.getLogger("festpos").warning("Einstellung %s nicht lesbar (anderes CLUSTER_SECRET?)", key)
        return default if v is None else v


def _secret_keys() -> frozenset:
    from .secretbox import AUTO
    return AUTO


# ---------------------------------------------------------------------------
# SQLite (lokal)
# ---------------------------------------------------------------------------

def _sqlite_error(e: sqlite3.Error, index: int | None = None) -> StoreError:
    """Gesperrte Datenbank ist vorübergehend (nochmals versuchen), alles andere ein echter Fehler."""
    msg = str(e)
    if isinstance(e, sqlite3.OperationalError) and ("locked" in msg or "busy" in msg):
        return StoreUnavailable(msg, index)
    return StoreError(msg, index)


class SQLiteStore(Store):
    kind = "sqlite"

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def query(self, sql, params=()):
        c = self._conn()
        try:
            return [dict(r) for r in c.execute(sql, tuple(params))]
        except sqlite3.Error as e:
            raise _sqlite_error(e) from e
        finally:
            c.close()

    def batch(self, stmts, transaction=True):
        c = self._conn()
        out: list[Result] = []
        try:
            if transaction:
                try:
                    c.execute("BEGIN IMMEDIATE")
                except sqlite3.Error as e:
                    raise _sqlite_error(e) from e
            for i, (sql, params) in enumerate(stmts):
                try:
                    cur = c.execute(sql, tuple(params))
                    rows = [dict(r) for r in cur.fetchall()] if cur.description else []
                    out.append(Result(cur.lastrowid, cur.rowcount if cur.rowcount >= 0 else 0, rows))
                except sqlite3.Error as e:
                    if transaction:
                        c.execute("ROLLBACK")
                    raise _sqlite_error(e, i) from e
            if transaction:
                try:
                    c.execute("COMMIT")
                except sqlite3.Error as e:
                    raise _sqlite_error(e) from e
            return out
        finally:
            c.close()

    def is_leader(self) -> bool:
        return True

    def cluster_info(self) -> dict:
        return {"mode": "sqlite", "path": self.path}

    def backup(self) -> bytes:
        src, mem = self._conn(), sqlite3.connect(":memory:")
        try:
            src.backup(mem)
            return _no_wal(mem.serialize())
        finally:
            src.close(); mem.close()

    def restore(self, data: bytes) -> None:
        mem, dst = sqlite3.connect(":memory:"), self._conn()
        try:
            mem.deserialize(_no_wal(data))
            mem.backup(dst)
        finally:
            mem.close(); dst.close()


# ---------------------------------------------------------------------------
# rqlite (Raft-Cluster)
# ---------------------------------------------------------------------------

class RqliteStore(Store):
    """Spricht mit dem LOKALEN rqlited (127.0.0.1). Schreibzugriffe leitet rqlite selbst
    an den Leader weiter. Pro Thread eine Keep-Alive-Verbindung."""
    kind = "rqlite"

    def __init__(self, host: str = "127.0.0.1", port: int = 4001, timeout: float = 5.0,
                 read_level: str = "weak"):
        self.host, self.port, self.timeout = host, port, timeout
        self.read_level = read_level            # weak = vom Leader gelesen, konsistent genug
        self._local = threading.local()

    # -- HTTP --------------------------------------------------------------
    WRITE_IDLE_S = 2.0                         # T36: so lange ruhende Verbindung -> für Schreibbefehle neu verbinden

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        """Ein Lesebefehl wird nach abgerissener Verbindung einmal wiederholt. Ein Schreibbefehl (/db/execute) nie,
        sobald er gesendet sein könnte (T36): hat rqlite ihn ausgeführt und nur die Antwort ging verloren, würde er
        doppelt ausgeführt (z.B. refunded_cents+?). Damit eine abgerissene Keep-Alive-Verbindung gar nicht erst
        vorkommt, bekommt ein Schreibbefehl nach WRITE_IDLE_S Ruhe eine neue Verbindung."""
        write = path.startswith("/db/execute")
        for attempt in (1, 2):
            conn = getattr(self._local, "conn", None)
            if conn is not None and write and time.monotonic() - getattr(self._local, "used", 0.0) > self.WRITE_IDLE_S:
                conn.close()
                conn = None
            if conn is None:
                conn = self._local.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            sent = False
            try:
                if conn.sock is None:
                    conn.connect()                 # scheitert das, ist sicher nichts gesendet
                sent = True
                data = json.dumps(body).encode() if body is not None else None
                conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                raw = resp.read()
                self._local.used = time.monotonic()
                if resp.status >= 500:                 # kein Leader, Timeout, Leaderwechsel: vorübergehend
                    raise StoreUnavailable(f"rqlite HTTP {resp.status}: {raw[:160]!r}")
                if resp.status >= 400:
                    raise StoreError(f"rqlite HTTP {resp.status}: {raw[:200]!r}")
                return json.loads(raw) if raw else {}
            except (OSError, http.client.HTTPException) as e:
                conn.close()
                self._local.conn = None
                if write and sent:
                    raise StoreUnclear(f"rqlite: Antwort auf Schreibbefehl verloren ({e})") from e
                # Lesen: nochmals nach abgerissener Keep-Alive-Verbindung, nicht nach einem Timeout
                if attempt == 2 or (method != "GET" and isinstance(e, TimeoutError)):
                    raise StoreUnavailable(f"rqlite nicht erreichbar: {e}") from e
        raise AssertionError("unreachable")

    @staticmethod
    def _rows(res: dict) -> list[dict]:
        cols = res.get("columns") or []
        return [dict(zip(cols, v)) for v in res.get("values") or []]

    # -- Store-API ---------------------------------------------------------
    @staticmethod
    def _top_error(data: dict) -> None:
        """Fehler auf oberster Ebene der Antwort = Cluster-Problem (Leaderwechsel, Weiterleitung, Timeout),
        nicht das SQL. Deshalb vorübergehend: die App sendet später nochmals."""
        if "error" in data:
            raise StoreUnavailable(f"rqlite: {data['error']}")
        if not data.get("results"):
            raise StoreUnavailable("rqlite: Antwort ohne Ergebnis")

    def query(self, sql, params=()):
        data = self._request("POST", f"/db/query?level={self.read_level}", [[sql, *params]])
        self._top_error(data)
        res = data["results"][0]
        if "error" in res:
            raise StoreError(res["error"])
        return self._rows(res)

    def batch(self, stmts, transaction=True):
        if not stmts:
            return []
        path = "/db/execute?transaction" if transaction else "/db/execute"
        data = self._request("POST", path, [[sql, *params] for sql, params in stmts])
        self._top_error(data)
        out = []
        for i, res in enumerate(data["results"]):
            if "error" in res:
                raise StoreError(res["error"], i)
            out.append(Result(res.get("last_insert_id"), res.get("rows_affected", 0), self._rows(res)))
        return out

    def status(self) -> dict:
        return self._request("GET", "/status")

    def is_leader(self) -> bool:
        return self.leader_state() is True

    def leader_state(self) -> bool | None:
        try:
            return self.status()["store"]["raft"]["state"] == "Leader"
        except (StoreError, KeyError, TypeError, ValueError):
            return None                         # unbekannt – nicht mit "kein Leader" verwechseln

    NODES_TIMEOUT = 2               # Sekunden: so lange wartet rqlite höchstens auf einen Pi, der nicht antwortet

    def cluster_info(self) -> dict:
        """Mitglieder und Erreichbarkeit. rqlite fragt dafür jeden Pi an und wartet ohne Angabe 30 s auf einen
        ausgesteckten – darum kurze Wartezeit und eigene Verbindung (kein zweiter Versuch, keine Keep-Alive-
        Verbindung, die danach für andere Abfragen blockiert wäre). Die Anzeigen lesen ohnehin den Zwischenstand
        aus dem Monitor (core.cluster_info)."""
        raw = self._raw("GET", f"/nodes?nonvoters&timeout={self.NODES_TIMEOUT}s", timeout=self.NODES_TIMEOUT + 3)
        try:
            nodes = json.loads(raw) if raw else {}
        except ValueError as e:
            raise StoreUnavailable(f"rqlite /nodes: keine gültige Antwort ({e})") from e
        return {"mode": "rqlite", "nodes": nodes}

    def _raw(self, method: str, path: str, body: bytes | None = None, timeout: float = 60) -> bytes:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers={"Content-Type": "application/octet-stream"})
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                raise StoreError(f"rqlite HTTP {resp.status}: {data[:200]!r}")
            return data
        except (OSError, http.client.HTTPException) as e:
            raise StoreUnavailable(f"rqlite nicht erreichbar: {e}") from e
        finally:
            conn.close()

    def backup(self) -> bytes:
        return self._raw("GET", "/db/backup")             # vollständige SQLite-Datei vom Leader

    def restore(self, data: bytes) -> None:
        self._raw("POST", "/db/load", data)               # ersetzt die Datenbank im ganzen Cluster


def open_store(url: str) -> Store:
    u = urlparse(url)
    if u.scheme == "sqlite":
        return SQLiteStore(u.path if not u.netloc else u.netloc + u.path)
    if u.scheme == "rqlite":
        return RqliteStore(u.hostname or "127.0.0.1", u.port or 4001)
    raise ValueError(f"Unbekanntes DB-Schema: {url}")
