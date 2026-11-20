/* FestPOS – Bestell-App › Einführung (Tutorial). */
"use strict";

// ------------------------------------------------------------------ Einführung (Tutorial)
// Zeigt Schritt für Schritt die echten Ansichten mit Beispieldaten. Klicks auf die App sind gesperrt,
// es wird nichts bestellt und nichts gespeichert. Start: Einstellungen › Einführung, oder automatisch
// nach dem Eingeben/Ändern des Gerätenamens (Verwaltung › Einstellungen › Bestell-App).
// Jeder Schritt sagt, was man tut und warum (T7); Schritte zu Funktionen, die aus sind, fallen weg. Ist der Testmodus
// erlaubt, fragt der letzte Schritt «Testmodus einschalten?» (Vorwahl: ja, zum Üben nach der Einführung).
function tourSteps() {
  const w = W(), c0 = cats()[0]?.id, inFirst = (p) => cats().length === 0 || p.category_id === c0;
  const catOf = (p) => p.category_id ?? (cats().length ? "none" : null);
  const stockP = products().find((p) => soldOut(p) || stockLow(p));
  const whenP = times().length > 1 && products().find((p) => allowedOf(p).length > 0);
  const askTest = testCfg().enabled && !S.test;
  return [
    { view: "table", title: S.device ? t("app.tour.welcome_title", { name: S.device }) : t("app.tour.welcome_title_anon"),
      text: t("app.tour.welcome") },
    { view: "table", sel: ".pad", text: t("app.tour.table_input", { num: w.num, word: w.one }) },
    S.boot?.staff_qr && { view: "table", sel: '[data-act="scan"]', text: t("app.tour.scan", { word: w.one }) },
    recentOn() && { view: "table", sel: ".recent", text: t("app.tour.recent", { many: w.many }) },
    cats().length > 0 && { view: "order", sel: ".cats", text: t(swipeOn() && catOrder().length > 1 ? "app.tour.cats_swipe" : "app.tour.cats") },
    products().some(inFirst) && { view: "order", sel: ".grid .tile", text: t("app.tour.tile"), setup: () => { S.cat = c0 ?? null; } },
    stockP && { view: "order", text: t("app.tour.stock"), setup: () => { S.cat = catOf(stockP); },
      sel: () => document.querySelector(".grid .tile.soldout, .grid .tile .stocktag")?.closest(".tile") },
    products().some(configurable) && { view: "order", text: t("app.tour.options"),
      setup: () => { S.cat = catOf(products().find(configurable)); },
      sel: () => document.querySelector(".grid .optico")?.closest(".tile") },
    whenP && { view: "order", text: t("app.tour.when", { later: times()[1] }) },
    { view: "order", sel: ".cartbtn", text: t("app.tour.cart") },
    { view: "order", sel: "#send", text: t("app.tour.send") },
    { view: "pay", sel: ".bon", text: t("app.tour.pay_total") },
    { view: "pay", sel: ".seg", title: t("app.tour.pay_modes_title"), text: t("app.tour.pay_modes") },
    payModeCfg() !== "off" && { view: "pay", sel: "#payrow", text: t(payModeCfg() === "required" ? "app.tour.pay_kind_required" : "app.tour.pay_kind") },
    S.boot?.receipt && { view: "pay", sel: "#rcptbtn", text: t("app.tour.receipt") },
    { view: "pay", sel: ".foot", text: t("app.tour.pay_next") },
    { view: "table", sel: "#stchip", text: t("app.tour.status") },
    historyOn() && { view: "table", sel: '[data-act="history"]', text: t("app.tour.history") },
    S.boot?.pin?.enabled && { view: "table", text: t("app.tour.pin") },
    askTest ? { view: "table", title: t("app.tour.test_title"), ask: true,
      text: t("app.tour.test_text", { print: t(testCfg().print ? "app.test.prints" : "app.test.no_print") }) }
      : { view: "table", title: t("app.tour.done_title"), text: t("app.tour.done") },
  ].filter(Boolean);
}
function startTour() {
  HIST.open++;                                // Einführung wechselt die Ansicht ohne render(): wartendes Antippen verwerfen
  closeOverlays();
  document.querySelectorAll(".toast").forEach((e) => e.remove());
  const demoTable = t("app.tour.demo_table");
  const first = products().find((p) => cats().length === 0 || p.category_id === cats()[0]?.id) || products()[0];
  const demoCart = first ? { [lineKey(first.id)]: { pid: first.id, q: 2, opts: [], note: "", when: null } } : {};
  const items = first ? [{ id: 1, pid: first.id, name: first.name, opt: "", price: first.price_cents, qty: 2 }] : [];
  S.tour = { i: 0, steps: tourSteps(),
    saved: { view: S.view, table: S.table, cat: S.cat, cart: S.cart, pay: S.pay, input: S.input, payMode: S.payMode, given: S.given },
    demo: { table: demoTable, cart: demoCart,
      pay: { id: "tour", table: demoTable, items, total: items.reduce((s, i) => s + i.qty * i.price, 0), ts: Date.now(), status: "sent", order_id: 42, test: S.test } } };
  S.cart = { ...demoCart };
  S.input = "";
  history.pushState({ view: S.view, overlay: "tour" }, "");   // Zurück-Taste beendet die Einführung
  tourShow();
}
function tourShow() {
  const T = S.tour;
  if (!T) return;
  const st = T.steps[T.i];
  // Ansicht mit Beispieldaten zeichnen (ohne Verlauf/Speichern)
  S.table = T.demo.table; S.cat = S.cat ?? cats()[0]?.id ?? null;
  st.setup?.();
  if (st.view === "pay") { S.pay = T.demo.pay; S.payMode = "change"; S.given = ""; S.part = { sel: {}, given: "", paid: [], step: "pick" }; }
  S.view = st.view;
  VIEWS[S.view]();
  let el = document.getElementById("tour");
  if (!el) { el = document.createElement("div"); el.id = "tour"; el.className = "tour"; document.body.appendChild(el); }
  const last = T.i === T.steps.length - 1;
  el.innerHTML = `<div class="tour-spot"></div>
    <div class="tour-bubble" role="dialog" aria-modal="true" aria-live="polite">
      ${st.title ? `<h2>${esc(st.title)}</h2>` : ""}
      <p>${esc(st.text)}</p>
      ${st.ask ? `<div class="tour-choice">
          <button class="go" data-act="tour-test" data-v="1">${th(testCfg().pin === false ? "app.tour.test_yes" : "app.tour.test_yes_pin")}</button>
          <button class="alt" data-act="tour-test" data-v="0">${th("app.tour.test_no")}</button></div>
        <p class="tour-later">${th("app.tour.test_later")}</p>` : ""}
      <div class="tour-nav">
        <span class="tour-n">${th("app.tour.step", { n: T.i + 1, of: T.steps.length })}</span>
        ${T.i > 0 ? `<button class="alt" data-act="tour-prev">${th("app.tour.prev")}</button>` : ""}
        ${st.ask ? "" : `<button class="go" data-act="${last ? "tour-end" : "tour-next"}">${th(last ? "app.tour.finish" : "app.tour.next")}</button>`}
      </div>
      ${last ? "" : `<button class="link sm tour-skip" data-act="tour-end">${th("app.tour.skip")}</button>`}
    </div>`;
  tourPlace();
  el.querySelector(".tour-bubble .go")?.focus({ preventScroll: true });
}
function tourPlace() {
  const T = S.tour, el = document.getElementById("tour");
  if (!T || !el) return;
  const st = T.steps[T.i], spot = el.querySelector(".tour-spot"), bub = el.querySelector(".tour-bubble");
  const target = typeof st.sel === "function" ? st.sel() : st.sel ? document.querySelector(st.sel) : null;
  spot.classList.toggle("none", !target);
  if (!target) {                                     // ohne Ziel: Hinweis in der Mitte
    spot.style.cssText = "left:50%;top:50%;width:0;height:0";
    bub.style.cssText = `left:50%;top:50%;transform:translate(-50%,-50%);width:${Math.min(innerWidth - 24, 380)}px`;
    return;
  }
  target.scrollIntoView({ block: "center", inline: "nearest" });
  const r = target.getBoundingClientRect(), pad = 6, vw = innerWidth, vh = innerHeight;
  // Rahmen ganz im Bild halten (auch bei Leisten, die bis an den Rand gehen): 14 px Abstand für die Ringe
  const m = 14, x0 = Math.max(m, r.left - pad), y0 = Math.max(m, r.top - pad);
  const x1 = Math.min(vw - m, r.right + pad), y1 = Math.min(vh - m, r.bottom + pad);
  spot.style.cssText = `left:${x0}px;top:${y0}px;width:${Math.max(0, x1 - x0)}px;height:${Math.max(0, y1 - y0)}px`;
  const bw = Math.min(vw - 24, 380), bh = bub.offsetHeight || 180;
  const below = r.bottom + pad + 12 + bh <= vh, top = below ? r.bottom + pad + 12 : Math.max(12, r.top - pad - 12 - bh);
  const left = Math.max(12, Math.min(vw - bw - 12, r.left + r.width / 2 - bw / 2));
  bub.style.cssText = `left:${left}px;top:${top}px;width:${bw}px`;
}
function tourGo(d) {
  const T = S.tour;
  if (!T) return;
  T.i = Math.max(0, Math.min(T.steps.length - 1, T.i + d));
  buzz(8);
  tourShow();
}
function endTour(viaBack = false) {
  const T = S.tour;
  if (!T) return;
  S.tour = null;
  document.getElementById("tour")?.remove();
  Object.assign(S, { view: T.saved.view === "pay" && !T.saved.pay ? "table" : T.saved.view, table: T.saved.table, cat: T.saved.cat,
    cart: T.saved.cart, pay: T.saved.pay, input: T.saved.input, payMode: T.saved.payMode, given: T.saved.given });
  store.set("tourDone", Date.now());
  render();
  if (!viaBack) history.back();                     // den Eintrag der Einführung wieder entfernen
}
addEventListener("resize", () => { if (S.tour) tourPlace(); });
