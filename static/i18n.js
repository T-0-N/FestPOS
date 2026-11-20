/* FestPOS – Übersetzungen für Bestell-App, Verwaltung und Statusanzeige (gleiche Sprachdateien wie der Server).

   t("app.send")                         -> Text als reiner Text (für textContent, Meldungen)
   th("app.unsent.text", { table })      -> für HTML: Platzhalter werden escaped, der Text selbst darf <b> enthalten
   t("app.cart.items", { count: 3 })     -> Mehrzahl über count (Intl.PluralRules)
   I18n.fmtTime(date)                    -> Uhrzeit im Format der Sprache

   Die Sprache kommt vom Gerät (localStorage) oder – «auto» – aus der Verwaltung. Die geladene Sprachdatei
   bleibt im Gerät gespeichert: die App startet auch ohne Server in der gewählten Sprache. */
"use strict";
const I18n = (() => {
  let msgs = {}, code = "de", locale = "de-CH", warned = new Set();
  const CACHE = "festpos.i18n.";
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const escape = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
  let rules = null;
  // «Platz» statt «Tisch» (Verwaltung, Anlass › Bezeichnung): in den Texten zu Tischen, Tischplan und
  // offenen Tischen das Wort ersetzen – pro Sprache (Geschlecht gleich: Tisch/Platz, table/place, tavolo/posto …)
  const WORD_KEYS = /^admin\.(tables\.|plan\.|tabs\.|opentabs\.|nav\.tables$|nav\.opentabs$)/;
  const SEAT = {
    de: [[/Tischen\b/g, "Plätzen"], [/Tische\b/g, "Plätze"], [/Tisch(e?s)\b/g, "Platzes"], [/Tisch/g, "Platz"]],
    en: [[/\bTables\b/g, "Seats"], [/\btables\b/g, "seats"], [/\bTable\b/g, "Seat"], [/\btable\b/g, "seat"]],
    fr: [[/\bTables\b/g, "Places"], [/\btables\b/g, "places"], [/\bTable\b/g, "Place"], [/\btable\b/g, "place"]],
    it: [[/\bTavoli\b/g, "Posti"], [/\btavoli\b/g, "posti"], [/\bTavolo\b/g, "Posto"], [/\btavolo\b/g, "posto"]],
    rm: [[/\bMaisas\b/g, "Plazzas"], [/\bmaisas\b/g, "plazzas"], [/\bMaisa\b/g, "Plazza"], [/\bmaisa\b/g, "plazza"]],
  };
  let seats = false;
  const seatWords = (s) => (SEAT[code.split("-")[0]] || []).reduce((acc, [rx, to]) => acc.replace(rx, to), s);
  const plural = (n) => {
    try { rules = rules || new Intl.PluralRules(locale); return rules.select(Number(n) || 0); } catch { return Number(n) === 1 ? "one" : "other"; }
  };
  function raw(key, p) {
    let v = msgs[key];
    if (v == null || v === "") {
      if (!warned.has(key)) { warned.add(key); console.warn("i18n: Text fehlt:", key); }
      return key;
    }
    if (typeof v === "object") v = v[plural(p?.count)] ?? v.other ?? "";
    return seats && WORD_KEYS.test(key) ? seatWords(String(v)) : String(v);
  }
  const fill = (s, p, enc) => s.replace(/\{(\w+)\}/g, (m, k) => (p && p[k] != null ? (enc ? escape(p[k]) : String(p[k])) : m));
  const t = (key, p) => fill(raw(key, p), p, false);
  const th = (key, p) => fill(raw(key, p), p, true);

  function use(cat) {
    msgs = cat || {};
    code = msgs._meta?.code || "de";
    locale = msgs._meta?.locale || code;
    rules = null;
    try { document.documentElement.lang = locale; } catch { /* egal */ }
  }
  /** Sprache laden. want = "de", "fr" … oder "" / "auto" (= Standard aus der Verwaltung). */
  async function load(want, { timeout = 5000 } = {}) {
    const name = want && want !== "auto" ? want : "auto";
    const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      const r = await fetch(`/i18n/${encodeURIComponent(name)}.json`, { cache: "no-cache", signal: ctrl.signal });
      if (!r.ok) throw new Error(r.status);
      const cat = await r.json();
      use(cat);
      try { localStorage.setItem(CACHE + name, JSON.stringify(cat)); } catch { /* Speicher voll: egal */ }
      return true;
    } catch {
      let cat = null;                                  // ohne Server: zuletzt geladene Sprachdatei
      try { cat = JSON.parse(localStorage.getItem(CACHE + name) || localStorage.getItem(CACHE + "auto") || "null"); } catch { /* leer */ }
      use(cat);
      return !!cat;
    } finally { clearTimeout(timer); }
  }
  /** Feste Texte in HTML-Seiten: <b data-i18n="status.connecting">, Attribute über data-i18n-attr="title:schluessel". */
  function apply(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll("[data-i18n-attr]").forEach((el) => {
      el.dataset.i18nAttr.split(";").forEach((pair) => {
        const [attr, key] = pair.split(":").map((x) => x.trim());
        if (attr && key) el.setAttribute(attr, t(key));
      });
    });
  }
  const fmtTime = (d, opts = { hour: "2-digit", minute: "2-digit" }) => {
    try { return new Date(d).toLocaleTimeString(locale, opts); } catch { return new Date(d).toLocaleTimeString(); }
  };
  const fmtDateTime = (d) => { try { return new Date(d).toLocaleString(locale); } catch { return new Date(d).toLocaleString(); } };
  /** Bezeichnung aus Anlass: "Platz" -> Texte zu Tischen sagen «Platz» (siehe WORD_KEYS). */
  const setTableWord = (w) => { seats = w === "Platz"; };
  return { t, th, load, use, apply, escape, fmtTime, fmtDateTime, setTableWord,
    get lang() { return code; }, get locale() { return locale; }, has: (k) => msgs[k] != null };
})();
const t = I18n.t, th = I18n.th;
