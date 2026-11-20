/* FestPOS – Kellner-App (Vanilla JS, kein Build-Schritt).
   Ablauf: Tisch → Produkte → Senden → Kasse (Rückgeld / Teilen / Einzeln zahlen) → nächster Tisch.

   Ausfallsicherheit im Browser:
   - Produkte & Tische werden lokal gespeichert -> App startet auch ohne Server.
   - Bestellungen gehen in einen lokalen Ausgang ("Outbox") und werden gesendet, bis der Server
     sie bestätigt. Die client_uuid verhindert doppelte Bons bei Wiederholungen.
   - Die Kasse rechnet komplett lokal, das Kassieren wartet nie auf das Netz.

   Texte: nur über t()/th() aus der Sprachdatei (static/i18n.js, app/i18n/de.json) – nie fest im Code.

   Aufbau: diese Datei (Version, Hilfen, Zustand) und danach static/app/*.js – klassische Skripte in der Reihenfolge
   von index.html mit gemeinsamen globalen Namen. Was beim Laden läuft, darf nur Früheres brauchen; Funktionen
   rufen einander erst später auf. Jede Datei steht auch in sw.js (SHELL), sonst startet die App offline nicht. */
"use strict";
const APP_VERSION = "__FESTPOS_VERSION__";   // setzt der Server aus der Datei VERSION ein

// ------------------------------------------------------------------ Hilfen
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("festpos." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("festpos." + k, JSON.stringify(v)); return true; } catch { return false; } },
  del(k) { try { localStorage.removeItem("festpos." + k); } catch { /* egal */ } },
};
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() :
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0; return (c === "x" ? r : (r & 3) | 8).toString(16);
  }));
const buzz = (ms = 12) => { try { navigator.vibrate && navigator.vibrate(ms); } catch { /* egal */ } };

/** Rappen -> "1'250.50" (Schweizer Schreibweise, Bargeld in CHF – in allen Sprachen gleich) */
function chf(c) {
  const neg = c < 0; c = Math.abs(Math.round(c));
  const fr = Math.floor(c / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "'");
  return (neg ? "−" : "") + fr + "." + String(c % 100).padStart(2, "0");
}
const cur = () => S.boot?.currency || "CHF";
/** Bargeld: auf 5 Rappen runden */
const round5 = (c) => Math.round(c / 5) * 5;
/** Eingabe "20.5" / "20" -> Rappen */
function parseEntry(s) {
  if (!s) return 0;
  const [fr, rp = ""] = s.split(".");
  return (parseInt(fr || "0", 10) * 100) + parseInt((rp + "00").slice(0, 2), 10);
}
const fmtDate = (iso) => {
  if (!iso) return "";
  try { return new Date(iso + "T12:00:00").toLocaleDateString(I18n.locale, { day: "2-digit", month: "2-digit", year: "numeric" }); }
  catch { return iso.split("-").reverse().join("."); }
};

const ICON = {
  lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>',
  hist: '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/></svg>',
  qr: '<svg viewBox="0 0 24 24"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4z"/><path d="M14 14h2v2h-2zM18 14h2M14 18h2v2M18 18h2v2h-2"/></svg>',
  del: '<svg viewBox="0 0 24 24"><path d="M21 5H9l-6 7 6 7h12z"/><path d="M17 9l-6 6M11 9l6 6"/></svg>',
  clear: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  minus: '<svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>',
  menu: '<svg viewBox="0 0 24 24"><path d="M4 6.5h16M4 12h16M4 17.5h16"/></svg>',
  checkall: '<svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M7.5 12.5l3 3 6-7"/></svg>',
  receipt: '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4L6 21z"/><path d="M9 8h6M9 11.5h6M9 15h4"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M5 12h14M12 5v14"/></svg>',
  cart: '<svg viewBox="0 0 24 24"><path d="M3 4h2.5l2.2 10.5h10.6L20.5 7H6.6"/><circle cx="9.5" cy="19" r="1.6"/><circle cx="17" cy="19" r="1.6"/></svg>',
  map: '<svg viewBox="0 0 24 24"><path d="M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/></svg>',
  pad: '<svg viewBox="0 0 24 24"><path d="M5 4h2M11 4h2M17 4h2M5 10h2M11 10h2M17 10h2M5 16h2M11 16h2M17 16h2M11 21h2"/></svg>',
  fit: '<svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  opts: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
};

// ------------------------------------------------------------------ Zustand
// Testmodus endet beim Start der App (T7): nach Neuladen, Neustart von Fully oder Handy wird wieder echt bestellt
const testAtStart = !!store.get("test", false);
if (testAtStart) ["test", "cart.test", "cartTable.test"].forEach((k) => store.del(k));
const S = {
  view: "table",
  input: "",                       // Ziffernblock Tisch
  table: null,
  test: false,                     // Testmodus (Üben): eigene Warenkörbe, Bestellungen zählen nirgends
  cart: {},                        // Positionen: key -> { pid, q, opts: [..], note }
  optEdit: null,                   // offenes Optionen-Fenster
  cat: null,
  slide: 0,                        // Wischen: -1/+1 = neue Kategorie kommt von links/rechts
  boot: store.get("boot", null),   // Produkte/Kategorien/Tische (letzter Stand vom Server)
  pay: null,                       // aktuelle Bestellung in der Kasse
  payMode: "change",
  given: "",
  split: 2,
  splitGiven: "",
  part: { sel: {}, given: "", paid: [], step: "pick" },
  device: store.get("device", ""),
  pinInput: "",
  testPin: "",
  locked: false,
  tour: null,                      // laufende Einführung
  net: "ok",                       // ok | wait | off
  nb: null,                        // Nachbuchen ohne Bon: { table, tab? } (static/app/send.js)
};
// Warenkorb und Tisch des Warenkorbs: im Testmodus getrennt, damit Üben nie echte Bestellungen vermischt
const cartKey = (k) => (S.test ? `${k}.test` : k);
S.cart = loadCart();
let outbox = store.get("outbox", []);
let history_ = store.get("history", []);
let rejected = store.get("rejected", []);      // abgelehnte Bestellungen: bleiben sichtbar, bis jemand entscheidet
let payq = store.get("payq", []);              // Zahlart für schon gesendete Bestellungen: wird nachgereicht
let partq = store.get("partq", []);            // Teilzahlungen (Teilen/Einzeln/Rückgeld) einer Bestellung: nachgereicht
let tipq = store.get("tipq", []);              // Trinkgeld (Summe pro Bestellung): wird nachgereicht wie die Zahlart

const products = () => (S.boot?.products || []);
const productById = (id) => products().find((p) => p.id === id);
// common.js fehlt nur, wenn ein Update-Zwischenstand im Cache liegt: dann eben ohne automatische Farben
const catColorAt = (all, i) => (typeof festposCategoryColor === "function" ? festposCategoryColor(all, i) : all[i]?.color || "#3a3a3a");
const cats = () => { const all = S.boot?.categories || []; return all.map((c, i) => ({ ...c, color: catColorAt(all, i) })); };
const catColor = (id) => cats().find((c) => c.id === id)?.color || "#000";
const catSort = (id) => { const i = cats().findIndex((c) => c.id === id); return i < 0 ? 999 : i; };
const imgMode = () => (["none", "small", "large"].includes(S.boot?.product_images) ? S.boot.product_images : "small");
const swipeOn = () => S.boot?.swipe_categories !== false;
const testCfg = () => S.boot?.test || { enabled: false };

// Warenkorb: eine Position = Produkt + gewählte Optionen + Freitext. Gleiches Produkt mit anderen
// Optionen ist eine eigene Position (z.B. 2x Kaffee und 1x Kaffee "2 Zucker").
function loadCart() {
  const raw = store.get(cartKey("cart"), {}), out = {};
  for (const [k, v] of Object.entries(raw || {})) {
    if (typeof v === "number") { if (v > 0) out[`p${k}`] = { pid: +k, q: v, opts: [], note: "" }; }  // altes Format
    else if (v && v.pid && v.q > 0) out[k] = { pid: v.pid, q: v.q, opts: v.opts || [], note: v.note || "", when: v.when || null };
  }
  return out;
}
// Lieferzeit (Einstellung in der Verwaltung, z.B. Sofort / Pause / Schluss). Erster Eintrag = Standard.
const times = () => (S.boot?.delivery_times?.length ? S.boot.delivery_times : [t("app.when.now")]);
const defWhen = () => times()[0];
const isLater = (w) => !!w && w !== defWhen();
// Pro Produkt (Verwaltung › Produkte › Lieferzeit): aus | wählbar (mit Vorgabe) | Pflicht | nur bestimmte
// Zeitpunkte – ist nur einer erlaubt, ist er erzwungen (z.B. immer "Schluss").
function allowedOf(p) {
  if (!p?.delivery) return [];
  if (!p.delivery_allowed) return times();
  const want = p.delivery_allowed.split(",").map((x) => x.trim().toLowerCase());
  const a = times().filter((w) => want.includes(w.toLowerCase()));
  return a.length ? a : times();
}
const choiceOf = (p) => allowedOf(p).length > 1;                       // Bedienung kann wählen
const needsWhen = (p) => choiceOf(p) && !!p.delivery_required;          // muss aktiv wählen
function defaultWhenOf(p) {
  const a = allowedOf(p);
  if (!a.length) return null;
  if (a.length === 1) return a[0];                                      // erzwungen
  if (p.delivery_required) return null;
  return a.includes(defWhen()) ? defWhen() : a[0];
}
const lineKey = (pid, opts = [], note = "", when = null) => {
  const w = isLater(when) ? when : null;
  return (opts.length || note || w) ? `p${pid}|${JSON.stringify(w ? [opts, note, w] : [opts, note])}` : `p${pid}`;
};
const optText = (l) => [...(isLater(l.when) ? [`⏱ ${l.when}`] : []), ...(l.opts || []), ...(l.note ? [t("app.opts.note_quoted", { note: l.note })] : [])].join(", ");
const prodOpts = (p) => p?.options || null;
const configurable = (p) => !!prodOpts(p) || choiceOf(p);
const needsOpts = (p) => !!prodOpts(p)?.groups?.some((g) => g.required);
function cartLines() {
  return Object.entries(S.cart)
    .map(([key, l]) => ({ key, p: productById(l.pid), q: l.q, opts: l.opts || [], note: l.note || "", when: isLater(l.when) ? l.when : null }))
    .filter((l) => l.p && l.q > 0)
    .sort((a, b) => (catSort(a.p.category_id) - catSort(b.p.category_id)) || (a.p.sort - b.p.sort) ||
      a.p.name.localeCompare(b.p.name) || (times().indexOf(a.when || defWhen()) - times().indexOf(b.when || defWhen())) ||
      (optText(a) ? 1 : 0) - (optText(b) ? 1 : 0));
}
const qtyOf = (pid) => Object.values(S.cart).reduce((s, l) => s + (l.pid === pid ? l.q : 0), 0);
// Ausverkauft und Restmenge (app/stock.py): grau = von Hand ausverkauft oder Restmenge 0; der Server prüft nochmals
const soldOut = (p) => !!p && (!!p.soldout || p.stock === 0);
const stockLow = (p) => !!p && p.stock != null && p.stock_warn != null && p.stock <= p.stock_warn;
function stockBlock(p, add = 1) {                  // Text, warum nicht (mehr) bestellt werden kann, sonst ""
  if (!p || S.tour) return "";
  if (soldOut(p)) return t("app.stock.soldout", { name: p.name });
  if (p.stock != null && qtyOf(p.id) + add > p.stock) return t("app.stock.only", { count: p.stock, name: p.name });
  return "";
}
function stockRefused(p, add = 1) {
  const why = stockBlock(p, add);
  if (why) { toast(why, true); buzz([60, 40, 60]); }
  return !!why;
}
const cartCount = () => cartLines().reduce((s, l) => s + l.q, 0);
const cartTotal = () => cartLines().reduce((s, l) => s + l.q * l.p.price_cents, 0);
const saveCart = () => (S.tour ? true : store.set(cartKey("cart"), S.cart));    // Einführung: nichts speichern
