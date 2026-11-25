-- FestPOS Datenmodell – läuft identisch auf SQLite (Einzelbetrieb/Tests) und rqlite (Cluster).
-- Regeln für rqlite-Tauglichkeit:
--   * keine nicht-deterministischen Defaults (now/random): Zeitstempel setzt die App
--   * Beträge als Integer in Rappen
-- Statements werden an ';' + Zeilenende getrennt ausgeführt.

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printers (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE COLLATE NOCASE,  -- "Getränkeausgabe", "Küche"
    host            TEXT    NOT NULL,
    port            INTEGER NOT NULL DEFAULT 9100,
    chars_per_line  INTEGER NOT NULL DEFAULT 48,              -- 80 mm Font A = 48, 58 mm = 32
    show_prices     INTEGER NOT NULL DEFAULT 1,
    enabled         INTEGER NOT NULL DEFAULT 1,
    -- Wenn der Drucker länger als fail_after Sekunden nicht erreichbar ist:
    --   wait = weiter warten | reroute = Bons auf fail_target drucken | warn = Warnbon auf fail_target
    fail_mode       TEXT    NOT NULL DEFAULT 'wait',
    fail_target     INTEGER,
    fail_after      INTEGER NOT NULL DEFAULT 60,
    language        TEXT,                                     -- Sprache der Bons (NULL = Standardsprache)
    status_seen     INTEGER NOT NULL DEFAULT 0                -- hat die Statusabfrage schon gültig beantwortet (T15)
);

CREATE TABLE IF NOT EXISTS categories (
    id     INTEGER PRIMARY KEY,
    name   TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    color  TEXT    NOT NULL DEFAULT '#3a3a3a',
    sort   INTEGER NOT NULL DEFAULT 0,
    vat_code TEXT                                             -- MWST: normal | reduced | lodging (NULL = normal)
);

-- Produktbilder liegen IN der Datenbank, damit sie auf allen Knoten repliziert sind.
-- id = SHA-256-Präfix des Inhalts -> URL ist unveränderlich und darf ewig gecacht werden.
CREATE TABLE IF NOT EXISTS images (
    id     TEXT PRIMARY KEY,
    mime   TEXT NOT NULL,
    data   TEXT NOT NULL                                      -- base64
);

CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
    category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    printer_id    INTEGER REFERENCES printers(id)   ON DELETE SET NULL,
    single_ticket INTEGER NOT NULL DEFAULT 0,                -- 1 = pro Stück eigener Bon
    image_id      TEXT    REFERENCES images(id)     ON DELETE SET NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    sort          INTEGER NOT NULL DEFAULT 0,
    options       TEXT,                                      -- JSON, siehe app/options.py
    color         TEXT,                                      -- eigene Kachelfarbe, NULL = von der Kategorie
    delivery      INTEGER NOT NULL DEFAULT 0,                -- 1 = Lieferzeit gilt für dieses Produkt
    delivery_allowed  TEXT,                                  -- erlaubte Zeitpunkte "Pause, Schluss" (NULL = alle);
                                                             -- nur einer erlaubt = erzwungen
    delivery_required INTEGER NOT NULL DEFAULT 0,            -- 1 = Bedienung muss aktiv wählen
    vat_code      TEXT,                                      -- MWST abweichend von der Kategorie (NULL = wie Kategorie)
    soldout       INTEGER NOT NULL DEFAULT 0,                -- 1 = von Hand als ausverkauft markiert
    stock         INTEGER CHECK (stock IS NULL OR stock >= 0),   -- Restmenge (NULL = ohne Bestand), zählt beim Bestellen
                                                             -- herunter; 0 = ausverkauft. Der CHECK verhindert
                                                             -- Überverkauf (Bestellung wird als Ganzes abgelehnt)
    stock_warn    INTEGER                                    -- Statusanzeige warnt ab dieser Restmenge (NULL = nie)
);

-- Tischplan (app/plan.py): area_id = Bereich, px/py/pw/ph = Lage und Grösse im Plan (Plan ist 1000 breit,
-- plan_areas.ratio hoch), rot = Drehung in Grad, shape = bench | rect | round. px NULL = nicht im Plan.
CREATE TABLE IF NOT EXISTS tables (
    id     INTEGER PRIMARY KEY,
    label  TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    sort   INTEGER NOT NULL DEFAULT 0,
    area_id INTEGER,
    px     INTEGER,
    py     INTEGER,
    pw     INTEGER,
    ph     INTEGER,
    rot    INTEGER NOT NULL DEFAULT 0,
    shape  TEXT
);

-- Tischplan: Bereiche (Saal, Terrasse …), jeder mit eigenem Plan und wahlweise einem Grundriss (Bild in images)
CREATE TABLE IF NOT EXISTS plan_areas (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    sort     INTEGER NOT NULL DEFAULT 0,
    image_id TEXT,                                        -- Grundriss (images.id), NULL = ohne
    ratio    INTEGER NOT NULL DEFAULT 1400,               -- Höhe des Plans (Breite = 1000)
    opacity  INTEGER NOT NULL DEFAULT 50,                 -- Deckkraft des Grundrisses in Prozent
    labels   TEXT                                         -- JSON: Beschriftungen [{t, x, y, w, h}] (Bühne, Bar …)
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY,
    client_uuid  TEXT    NOT NULL UNIQUE,                     -- Idempotenz
    table_label  TEXT    NOT NULL,
    device       TEXT,                                        -- Name, der in der App eingetragen ist (Bedienung)
    device_cert  TEXT,                                        -- Gerät laut Gerätezertifikat (z.B. XCover101)
    total_cents  INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    test         INTEGER NOT NULL DEFAULT 0,                  -- 1 = Testmodus (Übung): zählt nirgends mit
    payment      TEXT,                                        -- cash | card | twint | other, NULL = nicht angegeben
    digest       TEXT,                                        -- Prüfsumme des Inhalts (HMAC, app/ledger.py)
    closing_id   INTEGER,                                     -- Tagesabschluss, NULL = noch offen
    tab_id       INTEGER,                                     -- offener Tisch (app/tabs.py), NULL = sofort kassiert
    no_print     INTEGER NOT NULL DEFAULT 0                   -- 1 = nachgebucht ohne Bon (war schon geliefert)
);

-- Offene Tische (app/tabs.py, Einstellung «Tisch offen lassen»): Bestellungen sammeln sich am Tisch, abgerechnet
-- wird am Schluss. Ein offener Tisch pro Tisch (open_key, solange offen), Eigentümer = Gerät, das ihn eröffnet hat.
CREATE TABLE IF NOT EXISTS tabs (
    id           INTEGER PRIMARY KEY,
    table_label  TEXT    NOT NULL,
    owner        TEXT,                                        -- Geräte-ID (T1) bzw. «name:XCover 1»
    owner_name   TEXT,                                        -- zur Anzeige «XCover101 (Anna)»
    opened_at    TEXT    NOT NULL,
    closed_at    TEXT,
    closed_by    TEXT,
    close_reason TEXT,                                        -- paid | unpaid
    test         INTEGER NOT NULL DEFAULT 0,
    open_key     TEXT    UNIQUE,                              -- «0:12» solange offen, danach NULL
    uuid         TEXT                                         -- bleibende Kennung (Quittungen über den Tisch: «tab:…»)
);

-- Zahlungen an offenen Tischen (auf dem Server, damit Übergeben, Festleitung und Tagesabschluss stimmen)
CREATE TABLE IF NOT EXISTS tab_payments (
    id           INTEGER PRIMARY KEY,
    key          TEXT    NOT NULL UNIQUE,                     -- vom Handy: nochmals senden = dieselbe Zahlung
    tab_id       INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),   -- an die Rechnung (ohne Trinkgeld)
    payment      TEXT,                                        -- cash | card | twint | other, NULL = nicht angegeben
    tip_cents    INTEGER NOT NULL DEFAULT 0 CHECK (tip_cents >= 0),
    part         INTEGER,                                     -- Person bei «Einzeln»
    items        TEXT,                                        -- JSON {Position: Menge} bei «Einzeln»
    by_device    TEXT,
    by_user      TEXT,
    created_at   TEXT    NOT NULL,
    undone_at    TEXT                                         -- rückgängig gemacht (bleibt sichtbar)
);

-- Teilzahlungen einer Bestellung, die sofort kassiert wird (Teilen pro Person, Einzeln, Rückgeld mit Teilzahlung):
-- Zahlart pro Person für den Tagesabschluss. Das Handy schickt jeweils alle Zahlungen der Bestellung
-- (POST /api/orders/payments); was fehlt, ist zurückgenommen (bleibt mit undone_at sichtbar).
CREATE TABLE IF NOT EXISTS order_payments (
    id           INTEGER PRIMARY KEY,
    key          TEXT    NOT NULL UNIQUE,                     -- vom Handy: nochmals senden = dieselbe Zahlung
    order_id     INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),   -- an die Rechnung (ohne Trinkgeld)
    payment      TEXT,                                        -- cash | card | twint | other | payrexx, NULL = wie die Bestellung
    tip_cents    INTEGER NOT NULL DEFAULT 0 CHECK (tip_cents >= 0),   -- zur Anzeige; gezählt wird die Tabelle tips
    person       INTEGER,                                     -- Person bei «Teilen» / «Einzeln»
    items        TEXT,                                        -- JSON {Position: Menge} bei «Einzeln»
    by_device    TEXT,
    created_at   TEXT    NOT NULL,
    undone_at    TEXT                                         -- rückgängig gemacht (bleibt sichtbar)
);
CREATE INDEX IF NOT EXISTS idx_orderpay_order ON order_payments(order_id);

-- Snapshot zum Bestellzeitpunkt. printer_id = Station, an die das Produkt geroutet wurde.
CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id    INTEGER REFERENCES products(id) ON DELETE SET NULL,
    name          TEXT    NOT NULL,
    price_cents   INTEGER NOT NULL,
    qty           INTEGER NOT NULL CHECK (qty > 0),
    printer_id    INTEGER REFERENCES printers(id) ON DELETE SET NULL,
    single_ticket INTEGER NOT NULL DEFAULT 0,
    sort          INTEGER NOT NULL DEFAULT 0,
    line_no       INTEGER,                                   -- Position innerhalb der Bestellung
    options       TEXT,                                      -- gewählte Optionen, "2 Zucker, Hafermilch"
    note          TEXT,                                      -- Freitext
    category      TEXT,                                      -- Kategorie-Name (Bon gruppiert danach)
    cat_sort      INTEGER NOT NULL DEFAULT 0,
    delivery      TEXT,                                      -- Lieferzeitpunkt, NULL = sofort
    vat_code      TEXT,                                      -- MWST zum Bestellzeitpunkt (NULL = nicht MWST-pflichtig)
    vat_bp        INTEGER                                    -- Satz in Hundertstel-Prozent, 810 = 8,1 %
);

-- Druck-Warteschlange. Der Bon wird erst beim Drucken aus order/order_items gerendert,
-- dadurch funktioniert auch Umleiten auf einen anderen Drucker (andere Papierbreite).
--   station_id : fachliche Station (Küche) – bestimmt Inhalt & Kopfzeile
--   printer_id : physischer Zieldrucker – normalerweise = station_id, nach Umleitung anders
-- status: pending -> printing -> done | failed | uncertain | cancelled
-- 'printing' wird per Compare-and-Set gesetzt (WHERE status='pending'); erst nach erfolgreichem
-- Raft-Commit wird gesendet -> kein Knoten ohne Mehrheit kann drucken.
CREATE TABLE IF NOT EXISTS print_jobs (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    station_id  INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
    printer_id  INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,                             -- 'table' | 'single' | 'test' | 'qr' | 'storno'
    item_id     INTEGER REFERENCES order_items(id) ON DELETE CASCADE,
    n           INTEGER NOT NULL DEFAULT 1,
    of_n        INTEGER NOT NULL DEFAULT 1,
    status      TEXT    NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    claimed_by  TEXT,                                         -- Knoten, der gerade druckt
    claimed_at  TEXT,                                         -- seit wann (hängende Aufträge erkennen)
    reprint     INTEGER NOT NULL DEFAULT 0,                   -- 1 = Nachdruck (steht so auf dem Bon)
    note        TEXT,                                         -- bei 'qr': Tischbezeichnung
    delivery    TEXT,                                         -- Lieferzeitpunkt der Bons (NULL = sofort)
    last_error  TEXT,                                         -- Text in der Standardsprache (Anzeige)
    error_code  TEXT,                                         -- unreachable | paper_out | rerouted | … (Programm)
    created_at  TEXT    NOT NULL,
    printed_at  TEXT
);

-- Ausgestellte Gerätezertifikate (nur Übersicht; den privaten Schlüssel kennt nur das Handy)
CREATE TABLE IF NOT EXISTS device_certs (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    serial      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT,
    revoked_at  TEXT,                                  -- gesperrt: Bestellen/Erneuern abgewiesen
    replaces    TEXT,                                  -- Erneuerung: Seriennummer des Vorgängers
    removed_at  TEXT,                                  -- endgültig gelöscht (bleibt gesperrt, nur nicht mehr gezeigt)
    device_id   TEXT                                   -- Gerät (T1): Seriennummer des ersten Zertifikats der Kette
);

-- Storno als Gegenbuchung: die Bestellung bleibt, die Stornierung kommt dazu (wer, wann, warum, was)
CREATE TABLE IF NOT EXISTS cancellations (
    id           INTEGER PRIMARY KEY,
    client_uuid  TEXT    NOT NULL UNIQUE,                     -- Idempotenz (Handy sendet nochmals)
    order_id     INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    reason       TEXT    NOT NULL,
    by_user      TEXT,                                        -- Benutzer der Verwaltung …
    by_device    TEXT,                                        -- … oder Handy (Name aus dem Gerätezertifikat)
    amount_cents INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    digest       TEXT,
    closing_id   INTEGER,
    seq          INTEGER                                      -- laufende Nummer pro Bestellung (gleichzeitige Stornos)
);

CREATE TABLE IF NOT EXISTS corrections (                      -- T45: Korrektur früherer Tage (Bestellung schon abgeschlossen)
    id           INTEGER PRIMARY KEY,
    key          TEXT    NOT NULL UNIQUE,                     -- wie der Rückerstattungsauftrag (gast_refunds.key)
    kind         TEXT    NOT NULL,                            -- refund: Rückerstattung einer Online-Bestellung über Payrexx
    order_id     INTEGER NOT NULL,                            -- abgeschlossene Bestellung (Abschluss bleibt unverändert)
    gast_id      INTEGER,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),   -- zurück an den Gast, inkl. Trinkgeld
    goods_cents  INTEGER NOT NULL,                            -- davon Umsatz (mindert den Umsatz dieses Zeitraums)
    tip_cents    INTEGER NOT NULL DEFAULT 0,                  -- davon Trinkgeld
    vat          TEXT,                                        -- JSON [[code, Satz, Rappen]] – MWST-Aufteilung des Umsatzes
    why          TEXT,
    by_user      TEXT,
    created_at   TEXT    NOT NULL,
    digest       TEXT,
    closing_id   INTEGER                                      -- Abschluss, in dem die Korrektur gebucht ist
);
CREATE INDEX IF NOT EXISTS idx_corrections_open ON corrections(closing_id);

CREATE TABLE IF NOT EXISTS cancellation_items (
    id              INTEGER PRIMARY KEY,
    cancellation_id INTEGER NOT NULL REFERENCES cancellations(id) ON DELETE CASCADE,
    item_id         INTEGER NOT NULL REFERENCES order_items(id) ON DELETE CASCADE,
    qty             INTEGER NOT NULL CHECK (qty > 0),
    amount_cents    INTEGER NOT NULL
);

-- Tagesabschluss: fortlaufende Nummer, Summen, Kassensturz, Prüfsumme (verkettet mit dem vorherigen).
-- Bleibt auch nach «Alle Bestellungen löschen» – mit allen Bestellungen als CSV (komprimiert).
CREATE TABLE IF NOT EXISTS closings (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT    NOT NULL,
    by_user       TEXT,
    period_from   TEXT,
    period_to     TEXT,
    first_order   INTEGER,
    last_order    INTEGER,
    orders        INTEGER NOT NULL,
    gross_cents   INTEGER NOT NULL,
    storno_cents  INTEGER NOT NULL,
    net_cents     INTEGER NOT NULL,
    float_cents   INTEGER,                                    -- Wechselgeld zu Beginn
    counted_cents INTEGER,                                    -- gezähltes Bargeld
    card_cents    INTEGER,
    twint_cents   INTEGER,
    other_cents   INTEGER,
    expected_cents   INTEGER,                                 -- Bargeld laut Kasse
    difference_cents INTEGER,                                 -- gezählt minus erwartet
    note          TEXT,
    summary       TEXT    NOT NULL,                           -- JSON: pro Zahlart, Handy, Produkt, MWST, Prüfung
    orders_csv    TEXT    NOT NULL,                           -- base64(gzip(CSV))
    prev_hash     TEXT,
    chain_hash    TEXT    NOT NULL UNIQUE
);

-- Protokoll: wer hat wann Preise, Einstellungen, Benutzer … geändert, storniert, abgeschlossen, gelöscht
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL,
    actor   TEXT,
    action  TEXT NOT NULL,
    details TEXT                                              -- JSON
);

-- MWST-Sätze mit Gültigkeit ab Datum (ändert der Bund die Sätze, kommt eine neue Zeile dazu)
CREATE TABLE IF NOT EXISTS vat_rates (
    id         INTEGER PRIMARY KEY,
    code       TEXT    NOT NULL,                              -- normal | reduced | lodging
    rate_bp    INTEGER NOT NULL CHECK (rate_bp >= 0 AND rate_bp < 10000),
    valid_from TEXT    NOT NULL,                              -- YYYY-MM-DD
    UNIQUE (code, valid_from)
);

-- Rolle eines Handys (nach dem Namen im Gerätezertifikat): service = Bedienung, storno = Bedienung + Storno
CREATE TABLE IF NOT EXISTS device_roles (
    name  TEXT PRIMARY KEY COLLATE NOCASE,
    role  TEXT NOT NULL DEFAULT 'service'
);

-- Rolle eines Geräts nach seiner ID (T1, app/device_ids.py) – gilt vor device_roles; device_roles wird für ältere
-- Versionen mitgeschrieben und nur noch für Zertifikate gelesen, die dieses System nicht kennt
CREATE TABLE IF NOT EXISTS device_role_ids (
    device_id  TEXT PRIMARY KEY,
    role       TEXT NOT NULL DEFAULT 'service'
);

-- Quittungen für Gäste (app/receipts.py): fortlaufende Nummer (NULL = Übung aus dem Testmodus), Inhalt beim
-- Ausstellen festgehalten (lines, data: Kopf, Fuss, MWST, Zahlart …) und mit Prüfsumme gesichert. Bleibt auch nach
-- «Alle Bestellungen löschen» (keine Fremdschlüssel: die Quittung trägt alles selbst).
CREATE TABLE IF NOT EXISTS receipts (
    id          INTEGER PRIMARY KEY,
    no          INTEGER UNIQUE,                               -- Quittungsnummer (fortlaufend, nur echte Bestellungen)
    request_id  TEXT    NOT NULL UNIQUE,                      -- Idempotenz (Doppeltipp, Wiederholung)
    order_id    INTEGER NOT NULL,                             -- Bestellung (orders.id, zum Anzeigen)
    order_uuid  TEXT    NOT NULL,                             -- Bestellung (eindeutig, auch nach «Alle löschen»)
    part        INTEGER,                                      -- Person bei «Einzeln» (NULL = ganze Bestellung / Rest)
    lines       TEXT    NOT NULL,                             -- JSON: Positionen (line_no, Name, Menge, Preis, MWST)
    total_cents INTEGER NOT NULL,
    data        TEXT    NOT NULL,                             -- JSON: Kopf, Fuss, MWST-Nummer, Tisch, Zahlart …
    by_user     TEXT,
    by_device   TEXT,
    printer_id  INTEGER,
    test        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    digest      TEXT,
    closing_id  INTEGER,
    seq         INTEGER,                                      -- laufende Nummer pro Bestellung (gleichzeitige Quittungen)
    digest_v    INTEGER                                       -- T41: Version der Prüfsumme (leer = 1, 2 = mit data)
);

-- Jede Kopie einer Quittung (Druck nochmals): wann, von wem
CREATE TABLE IF NOT EXISTS receipt_copies (
    id          INTEGER PRIMARY KEY,
    request_id  TEXT    NOT NULL UNIQUE,
    receipt_id  INTEGER NOT NULL,
    by_user     TEXT,
    by_device   TEXT,
    created_at  TEXT    NOT NULL,
    closing_id  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue  ON print_jobs(printer_id, status, id);
CREATE INDEX IF NOT EXISTS idx_receipts_order ON receipts(order_uuid);
CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_time ON orders(created_at);

INSERT OR IGNORE INTO settings(key, value) VALUES ('event_name', 'Vereinsfest');
INSERT OR IGNORE INTO settings(key, value) VALUES ('currency', 'CHF');
INSERT OR IGNORE INTO settings(key, value) VALUES ('show_history', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('show_recent_tables', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('table_word', 'Tisch');
INSERT OR IGNORE INTO settings(key, value) VALUES ('tables_mode', 'free');
INSERT OR IGNORE INTO settings(key, value) VALUES ('delivery_times', 'Sofort, Pause, Schluss');
INSERT OR IGNORE INTO settings(key, value) VALUES ('monitor_hosts', 'Switch=192.168.55.30; Access Point=192.168.55.40');
INSERT OR IGNORE INTO settings(key, value) VALUES ('pin_enabled', '0');
INSERT OR IGNORE INTO settings(key, value) VALUES ('cert_days', '3650');
INSERT OR IGNORE INTO settings(key, value) VALUES ('cert_warn_days', '730');
-- Gerätezertifikate automatisch erneuern so viele Tage vor Ablauf (T3, 0 = aus)
INSERT OR IGNORE INTO settings(key, value) VALUES ('cert_renew_days', '730');
-- MAC-Wechsel: feste IP nach so vielen Minuten auf die neue MAC umschreiben (T4, 0 = nur von Hand)
INSERT OR IGNORE INTO settings(key, value) VALUES ('mac_change_minutes', '10');
INSERT OR IGNORE INTO settings(key, value) VALUES ('pin_idle_minutes', '15');
INSERT OR IGNORE INTO settings(key, value) VALUES ('pin_on_screen_off', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('default_printer_id', '0');
INSERT OR IGNORE INTO settings(key, value) VALUES ('language', 'de');
INSERT OR IGNORE INTO settings(key, value) VALUES ('product_images', 'small');
INSERT OR IGNORE INTO settings(key, value) VALUES ('swipe_categories', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('tutorial_auto', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('test_enabled', '0');
INSERT OR IGNORE INTO settings(key, value) VALUES ('test_print', '0');

-- Benutzer der Verwaltung (Rollen: view = Anzeige, lead = Festleitung, admin). Der Notfall-Login «admin» mit dem
-- Passwort aus festpos.conf steht nicht hier. gen zählt hoch bei neuem Passwort / Sperren: alte Sitzungen enden.
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role        TEXT NOT NULL DEFAULT 'view',
    pw_hash     TEXT NOT NULL,
    gen         INTEGER NOT NULL DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    last_login  TEXT,
    totp        TEXT                -- zweiter Faktor (JSON, app/twofa.py)
);
INSERT OR IGNORE INTO vat_rates(code, rate_bp, valid_from) VALUES ('normal', 810, '2024-01-01');
INSERT OR IGNORE INTO vat_rates(code, rate_bp, valid_from) VALUES ('reduced', 260, '2024-01-01');
INSERT OR IGNORE INTO vat_rates(code, rate_bp, valid_from) VALUES ('lodging', 380, '2024-01-01');

CREATE TABLE IF NOT EXISTS payrexx_payments (                 -- Kartenzahlung über Payrexx (T13)
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
    paid_at        TEXT,
    refunded_cents INTEGER NOT NULL DEFAULT 0,             -- T22: zurückerstattet (Online-Bestellung)
    tip_cents      INTEGER NOT NULL DEFAULT 0,             -- davon Trinkgeld (app/tips.py)
    tab_id         INTEGER,                                -- offener Tisch (app/tabs.py): Zahlung an den Tisch
    device_id      TEXT,                                   -- T39: Gerät, das die Zahlung gestartet hat (Rechte)
    extra_tx       TEXT,                                   -- T43: weitere erfolgreiche Transaktion = Doppelbelastung
    double_at      TEXT,                                   -- T44: wann die letzte davon gemeldet wurde
    closing_id     INTEGER,                                -- T45: Abschluss, in dem die Zahlung gezählt wurde
    late           TEXT                                    -- T46: nach dem Abschluss bestätigt, Bestellung bar: open |
                                                           -- refund (Gast hat bar bezahlt) | booked (nachgebucht)
);
CREATE INDEX IF NOT EXISTS idx_payrexx_order ON payrexx_payments(order_id);

CREATE TABLE IF NOT EXISTS gast_orders (                      -- QR-Bestellung am Tisch (T22)
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
    tip_cents      INTEGER NOT NULL DEFAULT 0,               -- davon Trinkgeld (in amount_cents enthalten)
    refund_open    INTEGER NOT NULL DEFAULT 0,               -- Rückerstattung gescheitert: von Hand nötig
    ip             TEXT,                                     -- nur Prüfsumme (Bremse), keine Adresse
    error          TEXT,
    created_at     TEXT    NOT NULL,
    checked_at     TEXT,
    paid_at        TEXT,
    cancelled_at   TEXT                                      -- T43: Abbruch durch FestPOS (Nachkontrolle ab dann)
);
CREATE INDEX IF NOT EXISTS idx_gast_order ON gast_orders(order_id);
CREATE INDEX IF NOT EXISTS idx_gast_status ON gast_orders(status);

CREATE TABLE IF NOT EXISTS gast_refunds (                     -- T37: Rückerstattung einer Online-Bestellung als Auftrag
    id             INTEGER PRIMARY KEY,
    key            TEXT    NOT NULL UNIQUE,                  -- storno:<client_uuid> | reject:<id> | admin:<uuid>: nie doppelt
    gast_id        INTEGER NOT NULL,                         -- gast_orders.id
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    why            TEXT,                                     -- storno:… | auto:soldout | admin:… (Protokoll)
    status         TEXT    NOT NULL,                         -- open | sending | done | unclear | failed
    attempts       INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    created_at     TEXT    NOT NULL,
    sent_at        TEXT,
    done_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_gast_refunds_gast ON gast_refunds(gast_id);
CREATE INDEX IF NOT EXISTS idx_gast_refunds_status ON gast_refunds(status);

CREATE TABLE IF NOT EXISTS tips (                             -- Trinkgeld an der Kasse, ohne Payrexx (app/tips.py)
    id           INTEGER PRIMARY KEY,                         -- neu bei jeder Änderung (Tagesabschluss merkt es)
    order_id     INTEGER NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    device       TEXT,
    updated_at   TEXT    NOT NULL
);
