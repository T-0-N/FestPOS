/* FestPOS – Bestell-App › Netzwerk, Offline-Hilfe (WLAN neu verbinden), Status oben rechts, Ausgang (Outbox) senden. */
"use strict";

// ------------------------------------------------------------------ Netzwerk
async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || 8000);
  try {
    // Gerätename, wartende Bestellungen, Version und Testmodus: für die Statusanzeige (Handy online?)
    const hb = { "X-Festpos-Device": encodeURIComponent(S.device || "").slice(0, 120), "X-Festpos-Outbox": String(outbox.length),
      "X-Festpos-Version": APP_VERSION, "X-Festpos-Lang": I18n.lang, "X-Festpos-Test": S.test ? "1" : "0" };
    if (BAT.level != null) Object.assign(hb, { "X-Festpos-Battery": String(BAT.level), "X-Festpos-Charging": BAT.charging ? "1" : "0" });
    return await fetch(path, { ...opts, signal: ctrl.signal, headers: { "Content-Type": "application/json", ...hb, ...(opts.headers || {}) } });
  } finally { clearTimeout(timer); }
}

// Rolle «Nur Statusanzeige» (Verwaltung › Handhelds): dieses Gerät zeigt nur die Statusanzeige. Die
// Statusanzeige schickt es zurück, sobald die Rolle wieder eine andere ist (festpos.statusOnly).
function statusOnly() {
  if (!S.boot?.status_only) return false;
  try { sessionStorage.setItem("festpos.statusOnly", "1"); } catch { /* egal */ }
  location.replace("/status.html");
  return true;
}
async function loadBoot() {
  try {
    const r = await api("/api/bootstrap");
    if (!r.ok) throw new Error(r.status);
    S.boot = await r.json();
    store.set("boot", S.boot);
    if (statusOnly()) return;                           // Rolle «Nur Statusanzeige»: weiter zur Statusanzeige
    setNet(outbox.length ? "wait" : "ok");
    if (S.view === "history" && !historyOn()) S.view = "table";
    checkLock();
    // Uhr dieses Handys weicht vom Pi ab: melden (nur zur Anzeige unter Verwaltung › Geräte – stellt nichts, T2)
    if (S.boot.server_time && Math.abs(S.boot.server_time * 1000 - Date.now()) > 120000 && Date.now() - lastSync > 600000) syncTime();
    // Testmodus in der Verwaltung ausgeschaltet: dieses Handy verlässt ihn (Übungen würden sonst abgelehnt)
    if (S.test && S.boot.test && !S.boot.test.enabled) { setTest(false, "admin"); toast(t("app.test.disabled_by_admin"), true); }
    // Nur neu zeichnen, wenn sich etwas geändert hat – und nie, während ein Fenster offen ist
    // (Optionen, Warenkorb, Einstellungen, Scanner, Einführung): sonst würde es mitten in der Eingabe geschlossen.
    // Restmengen ändern sich mit jeder Bestellung: dafür nur die betroffenen Kacheln (syncStockTiles), nicht alles
    const sig = JSON.stringify({ ...S.boot, server_time: 0, products: products().map(({ soldout, stock, stock_warn, ...p }) => p) });
    const busy = document.querySelector(".sheet-bg, .scan, #lock, #tour") || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
    if (["order", "table", "history"].includes(S.view) && sig !== S.bootSig && !busy) { S.bootSig = sig; render(); }
    else syncStockTiles();
  } catch { setNet("off"); }
}

let lastSync = 0;
function syncTime() {
  lastSync = Date.now();
  api("/api/time/sync", { method: "POST", body: JSON.stringify({ epoch_ms: Date.now() }) }).catch(() => {});
}

function setNet(s) {
  if (S.net === s) return;
  const was = S.net;
  S.net = s;
  if (was === "off" && s !== "off") NETFIX.offSince = 0;
  if (s === "off" && !NETFIX.offSince) NETFIX.offSince = Date.now();
  refreshStatus();
}

// ------------------------------------------------------------------ Offline: häufiger prüfen, WLAN neu verbinden
// Wartet eine Bestellung und der Server ist nicht erreichbar, versucht die App es alle 2 Sekunden (flush). Mit der
// JavaScript-Schnittstelle von Fully Kiosk zusätzlich: WLAN ausgeschaltet -> einschalten; nach 20 Sekunden ohne
// Verbindung WLAN kurz aus- und wieder einschalten (Android sucht neu und verbindet sich wieder) – höchstens alle
// 90 Sekunden, nach 5 Versuchen nur noch alle 5 Minuten. Android 10+: Ein-/Ausschalten nur, wenn Fully als
// Geräteinhaber (Provisioning) eingerichtet ist – sonst passiert dabei nichts, das häufigere Senden bleibt.
const NETFIX = { offSince: 0, lastKick: 0, kicks: 0 };
function netFix() {
  if (!outbox.length || S.net !== "off" || !NETFIX.offSince) { if (S.net !== "off") NETFIX.kicks = 0; return; }
  const f = window.fully;
  if (typeof f?.enableWifi !== "function") return;
  const now = Date.now(), pause = NETFIX.kicks >= 5 ? 300000 : 90000;
  try {
    if (typeof f.isWifiEnabled === "function" && !f.isWifiEnabled()) {
      if (now - NETFIX.lastKick < 15000) return;
      NETFIX.lastKick = now;
      f.enableWifi();
      toast(t("app.net.wifi_on"));
      return;
    }
    if (now - NETFIX.offSince < 20000 || now - NETFIX.lastKick < pause) return;
    NETFIX.lastKick = now;
    NETFIX.kicks += 1;
    if (typeof f.disableWifi !== "function") return;
    f.disableWifi();
    toast(t("app.net.wifi_reconnect"));
    setTimeout(() => { try { f.enableWifi(); } catch { /* egal */ } }, 3000);
  } catch { /* Fully kann es nicht: egal */ }
}
function bindNetEvents() {
  const f = window.fully;
  window.festposNetBack = () => { refreshStatus(); flush(); };       // fully.bind ruft Code als Text auf
  window.festposNetGone = () => { refreshStatus(); };
  try {
    if (typeof f?.bind === "function") {
      f.bind("networkReconnect", "festposNetBack();");
      f.bind("networkDisconnect", "festposNetGone();");
    }
  } catch { /* egal */ }
  window.addEventListener("offline", () => refreshStatus());
}

// ------------------------------------------------------------------ Status oben rechts (alle Seiten)
// Punkt für Server und Drucker, daneben das Logo. Bei einem Problem steht statt des Logos der Hinweis (zweizeilig,
// damit der Name bzw. Titel links lesbar bleibt): rot = Server nicht erreichbar oder Drucker ausgefallen, orange =
// Drucker umgeleitet oder Bestellung wird gerade gesendet. Antippen: Fenster mit Server und Druckern, von dort in die
// Einstellungen. Den Stand der Drucker meldet der Server (/api/phone/status, alle 10 Sekunden).
// Mobilfunk-Router oder (dauerhaft über WLAN/USB des Pi) das Internet ohne Verbindung, obwohl gebraucht
const upName = () => (PST.uplink?.kind === "internet" ? t("app.status.internet") : PST.uplink?.name || t("app.status.uplink"));
const PST = { printers: null, receipt: null, at: 0, lowSeen: new Set(), uplink: null };
// Papier fast leer: nur für Handys mit Rolle Festleitung (gelb wie in der Statusanzeige), die Bedienung sieht erst «leer»
const paperLow = () => (S.boot?.can_soldout ? (PST.printers || []).filter((p) => p.state === "ok" && p.paper_low) : []);
function prnSummary() {
  const list = PST.printers || [];
  if (PST.printers == null) return { s: "", key: "app.status.prn" };
  if (list.some((p) => p.state === "down")) return { s: "bad", key: "app.status.prn_down" };
  if (list.some((p) => p.state === "rerouted")) return { s: "warn", key: "app.status.prn_rerouted" };
  if (paperLow().length) return { s: "low", key: "app.status.paper_low" };
  return { s: "ok", key: "app.status.prn" };
}
// Einmal pro Drucker melden (mit Vibration), wenn er «Papier fast leer» wird – nicht bei jeder Abfrage
function notePaperLow() {
  const now = new Set(paperLow().map((p) => p.name));
  for (const name of now) {
    if (!PST.lowSeen.has(name)) { toast(t("app.status.d_prn_paper_low", { name })); buzz([40, 60, 40]); }
  }
  PST.lowSeen = now;
}
function statusInfo() {
  const list = PST.printers || [], down = list.filter((p) => p.state === "down"), moved = list.filter((p) => p.state === "rerouted");
  const waiting = outbox.length ? t("app.status.srv_wait", { count: outbox.length }) : "";
  if (S.net === "off") return { s: "bad", a: t("app.status.srv_off"), b: waiting || t("app.status.no_server") };
  const who = (l) => (l.length > 1 ? t("app.status.prn_n", { count: l.length }) : l[0].name);
  if (down.length) return { s: "bad", a: who(down), b: t("app.status.prn_down") };
  // Mobilfunk-Router ohne Verbindung, obwohl gebraucht (Karte/TWINT, QR-Bestellung, Internet darüber)
  if (PST.uplink) return { s: "bad", a: upName(), b: t("app.status.uplink_down") };
  if (moved.length) return { s: "warn", a: who(moved), b: t("app.status.prn_rerouted") };
  if (outbox.length) return { s: "warn", a: t("app.status.sending"), b: waiting };
  const low = paperLow();
  if (low.length) return { s: "low", a: who(low), b: t("app.status.paper_low") };
  return { s: "ok" };
}
function statusChip() {
  const i = statusInfo();
  const inner = i.s === "ok" ? logoImg() : `<span><strong>${esc(i.a)}</strong><small>${esc(i.b)}</small></span><b aria-hidden="true">›</b>`;
  return `<button class="stc" id="stchip" data-act="status" data-s="${i.s}" aria-label="${th("app.status.aria")}"><i></i>${inner}</button>`;
}
function refreshStatus() {
  const el = $("#stchip");
  if (el) el.outerHTML = statusChip();
  if ($("#statussheet")) $("#statussheet").innerHTML = statusSheetBody();
}
async function pollStatus() {
  if (document.visibilityState === "hidden") return;
  try {
    const r = await api("/api/phone/status", { timeout: 5000 });
    if (!r.ok) throw new Error(r.status);
    const j = await r.json();
    PST.printers = j.printers || [];
    PST.receipt = !!j.receipt;
    PST.uplink = j.uplink || null;
    PST.at = Date.now();
    if (j.stock) applyStock(j.stock);
    festposNotice(j.notice || null);                  // Herunterfahren / Neustart angekündigt (T27)
    noteDoubles(j.payrexx_double || []);              // zweite Kartenbelastung, auch nach «Bezahlt ✓» (T44)
    // Statusanzeige oder Verwaltung hat den Testmodus dieses Handys beendet (T7)
    if (j.test_exit && S.test) { closeOverlays(); setTest(false, "remote"); toast(t("app.test.ended_remote"), true); }
    // Einstellung «Übungsbestellungen drucken» geändert (T19): Text im gelben Balken nachführen
    if (S.boot?.test && typeof j.test_print === "boolean" && S.boot.test.print !== j.test_print) {
      S.boot.test.print = j.test_print;
      store.set("boot", S.boot);
      const sub = $("#testbar-sub");
      if (sub) sub.innerHTML = th(j.test_print ? "app.test.bar_print" : "app.test.bar_no_print");
    }
    if (S.boot && S.boot.receipt !== PST.receipt) { S.boot.receipt = PST.receipt; if (S.view === "pay") renderPayState(); }
    notePaperLow();
    if (!outbox.length) setNet("ok");
  } catch { setNet("off"); }
  refreshStatus();
}
// T44: Doppelbelastung einer Kartenzahlung, die dieses Handy gestartet hat – jede genau einmal melden
function noteDoubles(list) {
  const seen = new Set(store.get("payrexxDoubleSeen", []));
  const fresh = list.filter((d) => !seen.has(`${d.id}:${d.tx}`));
  if (!fresh.length) return;
  buzz([200, 100, 200]);
  // ein Fenster, das bestätigt werden muss – ein anderer Hinweis kann es nicht überdecken; erst danach gilt es als gesehen
  alert(t("app.payrexx.double"));
  fresh.forEach((d) => seen.add(`${d.id}:${d.tx}`));
  store.set("payrexxDoubleSeen", [...seen].slice(-50));
}
// Ausverkauft / Restmengen vom Server (alle 10 Sekunden): nur die betroffenen Kacheln neu zeichnen – auch während
// ein Fenster offen ist, damit niemand etwas bestellt, das es nicht mehr gibt
function applyStock(st) {
  if (!S.boot?.products) return;
  const out = new Set(st.soldout || []), left = st.stock || {}, warn = st.warn || {};
  let changed = false;
  for (const p of S.boot.products) {
    const k = String(p.id), next = { soldout: out.has(p.id) ? 1 : 0, stock: left[k] ?? null, stock_warn: warn[k] ?? null };
    if (next.soldout === (p.soldout ? 1 : 0) && next.stock === (p.stock ?? null) && next.stock_warn === (p.stock_warn ?? null)) continue;
    Object.assign(p, next);
    changed = true;
  }
  if (!changed) return;
  store.set("boot", S.boot);
  syncStockTiles();
  if ($("#soldoutsheet")) $("#soldoutsheet").innerHTML = soldoutBody();
}
const stockSig = (p) => (soldOut(p) ? "out" : stockLow(p) ? `low${p.stock}` : "");
function syncStockTiles() {
  app.querySelectorAll(".tile[data-id]").forEach((el) => {
    const p = productById(+el.dataset.id);
    if (p && (el.dataset.st || "") !== stockSig(p)) el.outerHTML = tileHTML(p);
  });
}
const PRN_WHY = { unreachable: "app.status.why_unreachable", paper_out: "app.status.why_paper_out",
  cover_open: "app.status.why_cover_open", printer_error: "app.status.why_printer_error", no_status: "app.status.why_no_status" };
function statusSheetBody() {
  const rows = [];
  const row = (s, label, text) => `<div class="strow" data-s="${s}"><b>${esc(label)}</b><span>${text}</span></div>`;
  rows.push(row(S.net === "off" ? "bad" : outbox.length ? "warn" : "ok", t("app.status.srv"),
    S.net === "off" ? th(outbox.length ? "app.status.d_srv_off_wait" : "app.status.d_srv_off", { count: outbox.length })
      : outbox.length ? th("app.status.d_srv_wait", { count: outbox.length }) : th("app.status.d_srv", { host: location.host })));
  const prn = PST.printers == null ? [th("app.status.d_prn_unknown")]
    : !PST.printers.length ? [th("app.status.d_prn_none")]
      : PST.printers.map((p) => {
        const why = p.code ? t(PRN_WHY[p.code] || PRN_WHY.unreachable) : "";
        const txt = p.state === "rerouted" ? th("app.status.d_prn_rerouted", { name: p.name, why, target: p.target })
          : p.state === "down" ? th("app.status.d_prn_down", { name: p.name, why, count: p.waiting })
            : paperLow().includes(p) ? th("app.status.d_prn_paper_low", { name: p.name })
              : th("app.status.d_prn_ok", { name: p.name });
        return `<span class="pl-${paperLow().includes(p) ? "low" : p.state}">${txt}</span>`;
      });
  rows.push(row(prnSummary().s, t("app.status.prn"), prn.join("<br>")));
  if (PST.uplink) rows.push(row("bad", upName(), th("app.status.d_uplink_down")));
  return rows.join("");
}
function openStatus() {
  document.querySelectorAll(".toast").forEach((e) => e.remove());
  openSheet(`<h2>${th("app.status.title")}</h2><div id="statussheet">${statusSheetBody()}</div>
    <div class="actions"><button class="alt" data-act="settings">${th("app.settings.open")}</button>
      <button class="go" data-act="close-sheet">${th("app.status.close")}</button></div>`);
}

// Outbox: senden, bis der Server bestätigt
let flushing = false, retryTimer = null;
async function flush() {
  if (flushing) return;
  flushing = true;
  clearTimeout(retryTimer);
  let rotated = false;
  try {
    while (outbox.length) {
      const o = outbox[0];
      const sentPay = o.payment;
      let r;
      try { r = await api("/api/orders", { method: "POST", body: JSON.stringify({ ...o, _fails: undefined }) }); }
      catch { setNet("off"); break; }
      if (r.ok) {
        const j = await r.json();
        updateHist(o.client_uuid, { status: "sent", order_id: j.order_id, warnings: j.warnings || [] });
        serverTotal(o.client_uuid, j.total_cents);
        outbox.shift(); store.set("outbox", outbox);
        // Zahlart erst unterwegs gewählt (oder Bestellung war schon da, z.B. Antwort verloren): nachreichen
        if (o.payment && (o.payment !== sentPay || j.duplicate)) queuePay(o.client_uuid, o.payment);
        if (j.warnings?.length) toast(j.warnings.join(" · "), true);
        setNet(outbox.length ? "wait" : "ok");
      } else if (r.status >= 500 || r.status === 408 || r.status === 429) {
        // Scheitert immer dieselbe Bestellung mit einem Programmfehler (500, nur bei ihr), nicht alle dahinter
        // blockieren: nach 5 Versuchen einmal ans Ende stellen (REV-047). Nie bei 503 (Ausfall/Neustart):
        // dann bleibt die Reihenfolge, sonst kämen ältere Bestellungen nach neueren.
        o._fails = (o._fails || 0) + 1;
        if (r.status === 500 && o._fails >= 5 && outbox.length > 1 && !rotated) {
          rotated = true; o._fails = 0;
          outbox.push(outbox.shift()); store.set("outbox", outbox);
          continue;
        }
        setNet("wait"); break;                                 // Cluster gerade ohne Leader: später nochmal
      } else if (r.status === 403) {                           // kein/gesperrtes Gerätezertifikat: Bestellung behalten
        let msg = t("app.net.phone_only");
        try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
        setNet("wait");
        toast(t("app.net.kept_on_device", { msg }), true);
        break;
      } else {
        let msg = t("app.net.error_status", { status: r.status }), stock;
        try {
          const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : msg;
          stock = j.stock_problems?.length ? j.stock_problems : undefined;          // ausverkauft / nicht genug
          if (j.stock) applyStock(j.stock);
        } catch { /* egal */ }
        updateHist(o.client_uuid, { status: "rejected", error: msg, stock });
        // T42: nie wegschneiden – jede steht hier, bis jemand sie korrigiert oder verwirft
        rejected = [{ id: o.client_uuid, table: o.table, lines: o.lines, error: msg, ts: Date.now(), test: !!o.test, stock }, ...rejected];
        store.set("rejected", rejected);
        outbox.shift(); store.set("outbox", outbox);
        toast(t("app.rejected.toast", { word: W().one, table: o.table || "?", msg }), true);
        buzz([80, 60, 80]);
        refreshProb();
      }
    }
  } finally {
    flushing = false;
    refreshStatus();
    if (S.view === "pay") renderPayState();
    if (S.view === "history" && !S.tour) render();
    if (outbox.length) retryTimer = setTimeout(flush, S.net === "off" ? 2000 : 1500);   // offline: alle 2 s neu
    flushPay();
    flushTip();
    flushParts();
  }
}

function updateHist(id, patch) {
  const h = history_.find((x) => x.id === id);
  if (h) Object.assign(h, patch);
  else if (S.pay?.tab && S.pay.id === id) Object.assign(S.pay, patch);    // offener Tisch (nicht im Verlauf)
  store.set("history", history_);
}
// Total laut Server übernehmen: wurde ein Preis inzwischen geändert, kassiert die Bedienung sonst falsch (REV-047)
function serverTotal(id, cents) {
  const h = history_.find((x) => x.id === id);
  if (!h || typeof cents !== "number" || cents === h.total) return;
  const before = h.total;
  updateHist(id, { total: cents, localTotal: before });
  toast(t("app.pay.price_changed_toast", { word: W().one, table: h.table, cur: cur(), total: chf(cents), before: chf(before) }), true);
  buzz([60, 40, 60]);
  if (S.view === "pay" && S.pay?.id === id) render();
}
