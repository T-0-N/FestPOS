/* FestPOS – Bestell-App › Navigation (Android-Zurück-Taste), Rendering, 1. Tischwahl. */
"use strict";

// ------------------------------------------------------------------ Navigation (Android-Zurück-Taste)
function go(view, push = true) {
  S.view = view;
  if (push) history.pushState({ view }, "");
  render();
  window.scrollTo(0, 0);
}
window.addEventListener("popstate", (e) => {
  if (S.tour) { endTour(true); return; }              // Zurück während der Einführung: beenden
  closeOverlays();
  if (S.nbReturn) { const id = S.nbReturn; S.nbReturn = null; openTabPay(id, true); return; }   // nach dem Nachbuchen (send.js)
  const v = e.state?.view || "table";
  // Zurück aus der Bestellung mit ungesendeten Produkten: erst fragen
  if (S.view === "order" && v === "table" && cartCount() > 0 && !S.leaveOk) {
    history.pushState({ view: "order" }, "");
    const w = W();
    buzz([40, 30, 40]);
    openSheet(`<h2>${th("app.unsent.title")}</h2>
      <p class="muted">${th("app.unsent.text", { count: cartCount(), word: w.one, table: S.table })}</p>
      <div class="actions">
        <button class="go" data-act="close-sheet">${th("app.unsent.continue")}</button>
        <button class="alt" data-act="leave" data-d="keep">${th("app.unsent.keep_switch", { word: w.one })}</button>
        <button class="alt danger" data-act="leave" data-d="drop">${th("app.unsent.drop")}</button>
      </div>`);
    return;
  }
  S.leaveOk = false;
  if (S.nb && v !== "order") nbLeave();               // Nachbuchen ohne Bon gilt nur in dieser Bestellung (send.js)
  // Aus der Kasse geht "zurück" immer zur Tischwahl (die Bestellung ist bereits abgeschickt)
  S.view = (S.view === "pay" && v === "order") ? "table" : v;
  if (S.view === "table") S.input = "";
  render();
});

// ------------------------------------------------------------------ Rendering
const app = $("#app");
function render() {
  if (S.view !== "history") HIST.open++;     // Verlauf verlassen: ein noch wartendes Antippen gilt nicht mehr (settings.js)
  closeSheet();
  VIEWS[S.view]();
  if (S.locked) showLock();
}

// Logo aus der Verwaltung (Einstellungen › Logo); offline aus dem Cache des Service Workers (/media/…)
const logoImg = () => (S.boot?.logo ? `<img class="logo${S.boot.logo_plate ? " plate" : ""}" src="${esc(S.boot.logo)}" alt="">` : "");
// Kopf der Seiten: links auf der Tisch-/Platzwahl ☰ und Name (beides öffnet die Einstellungen), sonst Zurück und
// Titel; rechts immer «abgelehnt» (falls nötig), der Status-Punkt mit Logo und der Verlauf (ausser im Verlauf selbst).
// T19: ob Übungsbons gedruckt werden (Einstellung «Übungsbestellungen drucken»), steht im Balken
const testBarSub = () => th(testCfg().print ? "app.test.bar_print" : "app.test.bar_no_print");
function header(title, { back, menu = false, hist = true } = {}) {
  const left = menu
    ? `<button class="icon-btn menu" data-act="settings" aria-label="${th("app.settings.open")}">${ICON.menu}</button>
       <button class="who" data-act="settings-name" aria-label="${th("app.settings.name")}">${esc(S.device || "")}</button><h1 class="sr">${title}</h1>`
    : `${back ? `<button class="icon-btn" data-act="back" aria-label="${th("app.common.back")}">${ICON.back}</button>` : ""}<h1>${title}</h1>`;
  const row = `${left}${probPill()}${statusChip()}${hist ? histBtn() : ""}`;
  if (!S.test) return `<header class="top">${row}</header>`;
  // Übung: der Balken steht über einer eigenen Zeile, die nie umbricht – der Kopf bleibt gleich hoch wie ohne Übung
  return `<header class="top test"><div class="testbar" role="status"><b>${th("app.test.bar")}</b><span id="testbar-sub">${testBarSub()}</span>
      <button data-act="test-exit">${th("app.test.exit_short")}</button></div><div class="top-row">${row}</div></header>`;
}
const probPill = () => rejected.length
  ? `<button class="prob" data-act="rejected" aria-label="${th("app.rejected.aria")}">⚠ ${th("app.rejected.pill", { count: rejected.length })}</button>` : "";
// Hinweis "abgelehnt" im Kopf aktualisieren, ohne offene Fenster zu stören
function refreshProb() {
  const top = $("header.top");
  if (!top) return;
  top.querySelector(".prob")?.remove();
  const anchor = top.querySelector("#stchip") || top.querySelector('[data-act="history"]');
  if (!rejected.length) return;
  if (anchor) anchor.insertAdjacentHTML("beforebegin", probPill());
  else (top.querySelector(".top-row") || top).insertAdjacentHTML("beforeend", probPill());
}
function openRejected() {
  document.querySelectorAll(".toast").forEach((e) => e.remove());   // Hinweis würde die Knöpfe verdecken
  if (!rejected.length) { closeSheet(); return; }
  const w = W();
  openSheet(`<h2>${th("app.rejected.title")}</h2>
    <p class="muted">${th("app.rejected.text")}</p>
    ${rejected.map((r) => `<div class="rej">
      <div class="rej-h"><b>${r.test ? `<span class="testtag">${th("app.test.tag")}</span> ` : ""}${esc(w.one)} ${esc(r.table || "–")}</b><span>${esc(I18n.fmtTime(r.ts))}</span></div>
      <div class="rej-i">${(r.lines || []).map((l) => { const x = (r.stock || []).find((s) => s.product_id === l.product_id);
        const nm = `${l.qty}× ${esc(productById(l.product_id)?.name || t("app.rejected.product_gone"))}`;
        return x ? `<span class="soldline">${nm}</span><span class="otag">${th(x.left === 0 ? "app.stock.tag_soldout" : "app.check.only", { count: x.left })}</span>` : nm; }).join(", ")}</div>
      <div class="rej-e">${esc(r.error || "")}</div>
      <div class="actions"><button class="go" data-act="rej-restore" data-id="${esc(r.id)}">${restoreLabel(r)}</button>
        <button class="alt danger" data-act="rej-drop" data-id="${esc(r.id)}">${th("app.rejected.drop")}</button></div></div>`).join("")}`);
}
function restoreLabel(r) {
  const out = (r.stock || []).filter((x) => x.left === 0).map((x) => x.name);
  return out.length ? th("app.rejected.restore_without", { names: out.join(", ") })
    : r.stock?.length ? th("app.rejected.restore_adjusted") : th("app.rejected.restore");
}
function restoreRejected(id) {
  const r = rejected.find((x) => x.id === id);
  if (!r) return;
  if (!!r.test !== S.test) { toast(t(r.test ? "app.rejected.need_test" : "app.rejected.need_real"), true); return; }
  if (cartCount() > 0) { toast(t("app.rejected.cart_busy"), true); return; }
  let missing = 0;
  S.cart = {};
  for (const l of r.lines || []) {
    const pr = productById(l.product_id);
    if (!pr) { missing += l.qty; continue; }
    const when = isLater(l.when) ? l.when : null, opts = l.options || [], note = l.note || "";
    const key = lineKey(pr.id, opts, note, when);
    const c = S.cart[key] || { pid: pr.id, q: 0, opts, note, when };
    c.q = Math.min(999, c.q + l.qty);
    S.cart[key] = c;
  }
  // ausverkauft / nicht genug: gleich anpassen, damit die Bestellung nicht nochmals abgelehnt wird
  S.check = { problems: r.stock || [] };
  const adjusted = autoAdjust();
  S.check = adjusted ? { problems: [], adjusted } : null;
  saveCart();
  rejected = rejected.filter((x) => x.id !== id); store.set("rejected", rejected);
  closeSheet();
  if (missing) toast(t("app.rejected.missing", { count: missing }), true);
  else if (adjusted) toast(t("app.check.adjusted_toast", { text: adjusted }));
  if (r.table) { store.set(cartKey("cartTable"), r.table); setTable(r.table); }   // prüft den Tisch wie bei der Eingabe
  else { store.set(cartKey("cartTable"), null); go("table"); toast(t("app.table.choose_first", { word: W().one })); }
}
const historyOn = () => S.boot?.show_history !== false;
const recentOn = () => S.boot?.show_recent_tables !== false;
// Bezeichnung "Tisch" oder "Platz" (Einstellung in der Verwaltung), in der Sprache des Geräts.
// one/many/num stehen in Sätzen (fr «la table»: klein), title am Anfang einer Überschrift («Table 12»).
const capFirst = (s) => (s ? s.charAt(0).toLocaleUpperCase() + s.slice(1) : s);
const W = () => {
  const w = S.boot?.table_word === "Platz"
    ? { one: t("app.word.seat.one"), many: t("app.word.seat.many"), num: t("app.word.seat.num") }
    : { one: t("app.word.table.one"), many: t("app.word.table.many"), num: t("app.word.table.num") };
  return { ...w, title: capFirst(w.one) };
};
const histBtn = () => historyOn() ? `<button class="icon-btn" data-act="history" aria-label="${th("app.history.title")}">${ICON.hist}</button>` : "";

// ---------- 1. Tischwahl
function renderTable() {
  if (tableViewMode() === "plan") { renderPlanTable(); return; }     // Tischplan (static/app/plan.js)
  const recent = recentOn() ? store.get("recentTables", []) : [];
  const w = W();
  const named = (S.boot?.tables || []).filter((x) => !/^\d+$/.test(x));
  app.innerHTML = `<section class="view table-view">
    ${header(esc(w.title), { menu: true })}
    ${certBanner()}
    ${viewSwitchHTML("number")}
    <div class="table-display"><span>${esc(w.num)}</span><output id="tin">${esc(S.input)}</output></div>
    <div class="recent">
      ${recent.map((x) => `<button class="chip" data-act="pick-table" data-t="${esc(x)}">${esc(x)}</button>`).join("")}
      ${named.length ? `<button class="chip muted" data-act="all-tables">${th("app.table.all", { many: w.many })}</button>` : ""}
      ${!recent.length && !named.length && recentOn() ? `<span class="chip muted" style="border:0">${th("app.table.recent_hint", { many: w.many })}</span>` : ""}
    </div>
    <div class="pad">
      ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-act="digit" data-d="${n}">${n}</button>`).join("")}
      ${S.boot?.staff_qr ? `<button class="k-fn" data-act="scan" aria-label="${th("app.table.scan")}">${ICON.qr}</button>`
        : `<button class="k-fn" data-act="digit-clear" aria-label="${th("app.table.clear")}">${ICON.clear}</button>`}
      <button data-act="digit" data-d="0">0</button>
      <button class="k-fn" data-act="digit-del" aria-label="${th("app.common.delete")}">${ICON.del}</button>
    </div>
    <div class="spacer"></div>
    ${tabStripHTML()}
    ${openTableHTML()}
    <button class="go" data-act="table-ok" ${S.input ? "" : "disabled"}>${tableOkText()}</button>
  </section>`;
  loadTabs();                                       // offene Tische (tabs.js): für die Nachfrage beim Wählen
}
// Noch ungesendete Produkte: unten zeigen, bei welchem Tisch – Antippen macht dort weiter
function openTableHTML() {
  const count = S.tour ? 0 : cartCount();
  if (!count) return "";
  const w = W(), tbl = store.get(cartKey("cartTable"), null);
  return tbl
    ? `<button class="openbar" data-act="pick-table" data-t="${esc(tbl)}"><b>${th("app.table.open", { count, word: w.one, table: tbl })}</b>
        <small>${th("app.table.open_go")}</small></button>`
    : `<div class="openbar"><b>${th("app.table.open_notable", { count, word: w.one })}</b></div>`;
}
const tableOkText = () => (S.input ? th("app.table.go", { word: W().one, table: S.input }) : th(S.boot?.staff_qr ? "app.table.enter" : "app.table.enter_noscan", { word: W().one }));

// cartDecision: "keep" = Produkte mitnehmen, "drop" = leer beginnen (nur wenn noch ungesendete Produkte da sind)
function setTable(tbl, force = false, cartDecision = null, replace = false) {   // replace: Bezahlansicht ersetzen
  tbl = String(tbl).trim();
  if (!tbl) return;
  const known = S.boot?.tables || [];
  const match = known.find((x) => x.toLowerCase() === tbl.toLowerCase());
  const mode = S.boot?.tables_mode || "free";              // Einstellung in der Verwaltung › Tische
  if (match) tbl = match;
  else if (known.length && mode === "strict") {
    toast(t("app.table.unknown", { word: W().one, table: tbl }), true);
    buzz([60, 40, 60]);
    S.input = "";
    if (S.view === "table") render();
    return;
  } else if (known.length && mode === "warn" && !force) {
    buzz([40, 30, 40]);
    openSheet(`<h2>${th("app.table.unknown", { word: W().one, table: tbl })}</h2>
      <p class="muted">${th("app.table.unknown_text")}</p>
      <div class="actions">
        <button class="alt" data-act="table-retry">${th("app.table.retry")}</button>
        <button class="go" data-act="table-force" data-t="${esc(tbl)}">${th("app.table.force", { table: tbl })}</button>
      </div>`);
    return;
  }
  if (!replace && tabAsk(tbl)) return;              // offener Tisch: zuerst fragen (weiter bestellen / abrechnen)
  // Noch ungesendete Produkte von einem anderen Platz? Nachfragen statt still mitnehmen.
  const from = store.get(cartKey("cartTable"), null);
  if (cartCount() > 0 && from && from.toLowerCase() !== tbl.toLowerCase() && !cartDecision) {
    buzz([40, 30, 40]);
    const w = W(), rr = replace ? 1 : "";
    openSheet(`<h2>${th("app.unsent.title")}</h2>
      <p class="muted">${th("app.unsent.text", { count: cartCount(), word: w.one, table: from })}</p>
      <div class="actions">
        <button class="go" data-act="table-cart" data-d="back" data-r="${rr}" data-t="${esc(from)}">${th("app.unsent.back_to", { word: w.one, table: from })}</button>
        <button class="alt" data-act="table-cart" data-d="keep" data-r="${rr}" data-t="${esc(tbl)}">${th("app.unsent.take_to", { word: w.one, table: tbl })}</button>
        <button class="alt danger" data-act="table-cart" data-d="drop" data-r="${rr}" data-t="${esc(tbl)}">${th("app.unsent.start_empty", { word: w.one, table: tbl })}</button>
      </div>`);
    return;
  }
  if (cartDecision === "drop") { S.cart = {}; saveCart(); }
  if (S.nb && String(S.nb.table).toLowerCase() !== tbl.toLowerCase()) S.nb = null;   // anderer Tisch: normal bestellen
  store.set(cartKey("cartTable"), tbl);
  S.table = tbl;
  S.input = "";
  const recent = [tbl, ...store.get("recentTables", []).filter((x) => x !== tbl)].slice(0, 8);
  store.set("recentTables", recent);
  if (S.cat == null && cats().length) S.cat = cats()[0].id;
  buzz(20);
  if (replace) history.replaceState({ view: "order" }, "");
  go("order", !replace);
}
