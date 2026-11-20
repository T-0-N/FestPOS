/* FestPOS – Gästeseite der QR-Bestellung am Tisch (T22). Läuft hinter dem Gästedienst (app/gast_proxy.py):
   ?t=<Tisch>&s=<Signatur>  Speisekarte und Warenkorb   ?b=<Beleg>  Stand der Bestellung (nach Payrexx)
   Bezahlt wird bei Payrexx; die Bestellung entsteht erst, wenn Payrexx die Zahlung bestätigt. Pro Bildschirm ein
   grüner Hauptknopf unten. Texte: app/i18n (app.gast.*), Farben: app.css (Variablen). */
"use strict";
const G = { menu: null, cart: [], cat: null, view: "menu", order: null, poll: null, sheet: null, tip: "none", agreed: false };
const Q = new URLSearchParams(location.search);
const LANGS = [["de", "DE"], ["fr", "FR"], ["it", "IT"], ["en", "EN"], ["rm-vallader", "RM"]];
const $ = (s) => document.querySelector(s);
const esc = (s) => I18n.escape(s);
const chf = (c) => (c / 100).toFixed(2);
const money = (c) => `CHF ${chf(c)}`;
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("festpos.gast." + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("festpos.gast." + k, JSON.stringify(v)); } catch { /* egal */ } },
};

function langWanted() {
  const saved = store.get("lang", "");
  if (saved) return saved;
  const nav = (navigator.language || "de").slice(0, 2).toLowerCase();
  return { fr: "fr", it: "it", en: "en", rm: "rm-vallader" }[nav] || "de";
}

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json" }, cache: "no-store" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(typeof j.detail === "string" ? j.detail : t("app.gast.offline"));
    e.status = r.status;
    throw e;
  }
  return j;
}

/* -- Aufbau ---------------------------------------------------------------------------------------------------- */
function head() {
  const m = G.menu, table = m ? m.table : (G.order ? G.order.table : "");
  const word = m ? m.table_word : "";
  return `<header class="g-top">${m && m.logo ? '<img src="/api/logo" alt="">' : ""}
    <h1>${esc(m && m.name ? m.name : t("app.gast.title"))}${table ? `<small>${esc(word || t("app.gast.table_word"))} ${esc(table)}</small>` : ""}</h1>
    <label class="g-lang"><span class="sr">${th("app.gast.lang")}</span><select data-act="lang">
      ${LANGS.map(([v, l]) => `<option value="${v}" ${I18n.lang === v || (v.startsWith(I18n.lang) && I18n.lang.length > 1) ? "selected" : ""}>${l}</option>`).join("")}</select></label></header>`;
}
function render(main, foot = "") {
  $("#g").innerHTML = `${head()}<main>${main}</main>${foot ? `<div class="g-foot">${foot}</div>` : ""}`;
}
const cartSum = () => G.cart.reduce((s, x) => s + x.price * x.qty, 0);
const cartCount = () => G.cart.reduce((s, x) => s + x.qty, 0);
const inCart = (pid) => G.cart.filter((x) => x.product_id === pid).reduce((s, x) => s + x.qty, 0);
const product = (pid) => G.menu.products.find((p) => p.id === pid);
// Trinkgeld (nur wenn in der Verwaltung eingeschaltet): kein, aufrunden auf den nächsten Franken, 5 %, 10 %
function tipOptions(sum) {
  const pct = (p) => Math.max(10, Math.round(sum * p / 1000) * 10);   // auf 10 Rappen
  return [["none", 0], ["round", Math.floor(sum / 100) * 100 + 100 - sum], ["5", pct(5)], ["10", pct(10)]];
}
const tipCents = () => (G.menu && G.menu.tips ? (tipOptions(cartSum()).find(([k]) => k === G.tip) || [0, 0])[1] : 0);
function tipHTML() {
  if (!G.menu.tips) return "";
  const sum = cartSum(), tip = tipCents();
  const label = (k, v) => (k === "none" ? th("app.gast.tip_none") : k === "round" ? esc(t("app.gast.tip_round", { amount: chf(sum + v) }))
    : esc(t("app.gast.tip_pct", { pct: k, amount: chf(v) })));
  return `<div class="g-tip"><h3>${th("app.gast.tip")}</h3><div class="g-tips">
    ${tipOptions(sum).map(([k, v]) => `<button class="chip" data-act="tip" data-k="${k}" aria-pressed="${G.tip === k}">${label(k, v)}</button>`).join("")}</div>
    <p class="g-small">${th("app.gast.tip_hint")}</p></div>
    ${tip ? `<div class="g-total"><span>${th("app.gast.total_tip")}</span><span>${money(sum + tip)}</span></div>` : ""}`;
}

function viewMenu() {
  const m = G.menu;
  if (m.closed) return viewClosed();
  const cats = m.categories;
  if (!G.cat || !cats.some((c) => c.id === G.cat)) G.cat = cats.length ? cats[0].id : null;
  const prods = m.products.filter((p) => !cats.length || p.category_id === G.cat);
  const row = (p) => {
    const q = inCart(p.id), simple = !p.groups.length;
    const right = p.soldout ? `<span class="g-so">${th("app.gast.soldout")}</span>`
      : q && simple ? `<div class="qty"><button data-act="minus" data-p="${p.id}" aria-label="−">−</button><output>${q}</output><button data-act="plus" data-p="${p.id}" aria-label="+">+</button></div>`
      : `<button class="g-add" data-act="plus" data-p="${p.id}" aria-label="${th("app.gast.add")}">${q ? `${q} +` : "+"}</button>`;
    return `<div class="g-p${p.soldout ? " out" : ""}"><span class="nm">${esc(p.name)}</span>${right}
      <span class="ds">${p.left != null && !p.soldout ? esc(t("app.gast.left", { n: p.left })) : ""}</span>
      <span class="pr">${money(p.price)}</span></div>`;
  };
  render(`${cats.length > 1 ? `<div class="g-cats">${cats.map((c) => `<button class="cat" data-act="cat" data-c="${c.id}"
      aria-pressed="${c.id === G.cat}" style="--c:${esc(c.color || "#000")}">${esc(c.name)}</button>`).join("")}</div>` : ""}
    ${prods.map(row).join("")}`,
    `<button class="go" data-act="cart" ${cartCount() ? "" : "disabled"}>${cartCount()
      ? esc(t("app.gast.cart_btn", { amount: money(cartSum()) })) : th("app.gast.cart_empty")}</button>`);
}
// Rechtsseiten (Verwaltung › Webseite & Rechtliches): Kästchen «AGB akzeptieren» (wenn es AGB gibt) und Links
function legalLink(k, key) {
  const href = G.menu.legal && G.menu.legal[k];
  if (!href) return "";
  const own = href.startsWith("/"), lang = I18n.lang && I18n.lang !== "de" ? `?l=${encodeURIComponent(I18n.lang)}` : "";
  return `<a href="${esc(own ? href.split("?")[0] + lang : href)}" target="_blank" rel="noopener">${th(key)}</a>`;
}
function legalHTML() {                               // -> [Kästchen über dem Bezahlen-Knopf, Hinweis und Links unten]
  const m = G.menu, links = [legalLink("imprint", "app.gast.l_imprint"), legalLink("refund", "app.gast.l_refund")].filter(Boolean).join(" · ");
  const accept = m.terms ? `<label class="g-accept"><input type="checkbox" data-act="agree" ${G.agreed ? "checked" : ""}>
    <span>${t("app.gast.accept", { terms: legalLink("terms", "app.gast.l_terms") || th("app.gast.l_terms"),
      privacy: legalLink("privacy", "app.gast.l_privacy") || th("app.gast.l_privacy") })}</span></label>` : "";
  return [accept, `<p class="g-small">${th("app.gast.terms")}${links ? `<br>${links}` : ""}</p>`];
}
function viewCart() {
  if (!G.cart.length) { G.view = "menu"; return viewMenu(); }
  const m = G.menu;
  render(`<h2 class="g-h">${th("app.gast.your_order")}</h2>
    ${G.cart.map((x, i) => `<div class="g-row"><span class="nm">${esc(x.name)}${x.options.length ? `<small>${esc(x.options.join(", "))}</small>` : ""}</span>
      <div class="qty"><button data-act="cminus" data-i="${i}" aria-label="−">−</button><output>${x.qty}</output><button data-act="cplus" data-i="${i}" aria-label="+">+</button></div>
      <span class="sum">${chf(x.price * x.qty)}</span></div>`).join("")}
    <div class="g-total"><span>${th("app.gast.total")}</span><span>${money(cartSum())}</span></div>
    ${cartSum() > m.max_cents ? `<p class="g-err">${esc(t("app.gast.max", { amount: money(m.max_cents) }))}</p>` : ""}
    ${tipHTML()}
    <div class="g-info">${th("app.gast.pay_info", { word: m.table_word, table: m.table })}</div>`,
    `${legalHTML()[0]}<button class="go" data-act="pay" ${cartSum() > m.max_cents || (m.terms && !G.agreed) ? "disabled" : ""}>${esc(t("app.gast.pay_btn", { amount: money(cartSum() + tipCents()) }))}</button>
     <button class="alt" data-act="menu">${th("app.gast.more")}</button>
     ${legalHTML()[1]}`);
}
function viewClosed() {
  render(`<div class="g-mark warn"><b>⏸</b><h2>${th("app.gast.closed_title")}</h2></div>
    <div class="g-info">${th("app.gast.closed_text")}</div>`);
}
function viewMessage(title, text) {
  render(`<div class="g-mark warn"><b>!</b><h2>${esc(title)}</h2></div><div class="g-info">${esc(text)}</div>`);
}
function viewOrder() {
  const o = G.order, word = G.menu ? G.menu.table_word : t("app.gast.table_word");
  const lines = `<div class="g-info">${o.lines.map((x) => `<div class="g-line"><span>${x.qty}× ${esc(x.name)}${x.options.length ? ` <small>(${esc(x.options.join(", "))})</small>` : ""}</span><span>${chf(x.sum)}</span></div>`).join("")}
    ${o.tip ? `<div class="g-line"><span>${th("app.gast.tip")}</span><span>${chf(o.tip)}</span></div>` : ""}
    <div class="g-line g-strong"><span>${th("app.gast.total")}</span><span>${money(o.amount)}</span></div>
    ${o.refunded ? `<div class="g-line"><span>${th("app.gast.refunded")}</span><span>− ${chf(o.refunded)}</span></div>` : ""}</div>`;
  const again = `<button class="go" data-act="again">${th(o.status === "ordered" ? "app.gast.again" : "app.gast.new")}</button>`;
  if (o.status === "waiting" || o.status === "new" || o.status === "paid") {
    const title = o.status === "paid" ? "app.gast.taking" : o.cancelling ? "app.gast.cancelling" : "app.gast.waiting";
    return render(`<div class="g-mark"><i class="g-spin"></i><h2>${th(title)}</h2></div>
      <p class="g-small">${th(o.cancelling ? "app.gast.cancelling_hint" : "app.gast.waiting_hint")}</p>${lines}`,
      o.link ? `<a class="go g-link" href="${esc(o.link)}" rel="noopener">${th("app.gast.back_pay")}</a>
        <button class="alt" data-act="cancel">${th("app.gast.cancel")}</button>` : "");
  }
  if (o.status === "ordered" || (o.status === "refunded" && o.nr && o.reason !== "printer")) {
    const done = o.step === "printed";
    return render(`<div class="g-mark ok"><b>✓</b><h2>${th("app.gast.paid")}</h2><div class="g-nr">${esc(t("app.gast.nr", { nr: o.nr }))}</div></div>
      <div class="g-steps"><span>${esc(t("app.gast.step_paid", { amount: money(o.amount) }))}${o.method ? ` (${esc(o.method)})` : ""}</span>
        <span class="${done ? "" : "wait"}">${th(done ? "app.gast.step_printed" : "app.gast.step_queued")}</span>
        <span class="wait">${esc(t("app.gast.step_bring", { word, table: o.table }))}</span></div>
      ${lines}<p class="g-small">${th("app.gast.keep")}</p>`, again);
  }
  if (o.status === "rejected" || o.status === "refunded") {
    const title = t({ soldout: "app.gast.rejected_soldout", printer: "app.gast.rejected_printer", late: "app.gast.rejected_late",
      price: "app.gast.rejected_price" }[o.reason] || "app.gast.rejected_order");
    const left = o.amount - o.refunded;
    return render(`<div class="g-mark warn"><b>!</b><h2>${esc(title)}</h2></div>
      <div class="g-info">${th("app.gast.not_taken")} ${esc(t(left > 0 ? "app.gast.refund_wait" : "app.gast.refund_done", { amount: money(o.amount) }))}</div>
      <p class="g-small">${th("app.gast.ask_staff")}</p>`, again);
  }
  return render(`<div class="g-mark warn"><b>×</b><h2>${th("app.gast.cancelled")}</h2></div>${lines}`, again);
}
function draw() {
  stopPoll();
  if (G.view === "cart") viewCart();
  else if (G.view === "order") { viewOrder(); if (["waiting", "new", "paid"].includes(G.order.status) || (G.order.status === "ordered" && G.order.step !== "printed")) startPoll(); }
  else viewMenu();
  if (G.sheet) drawSheet();
}

/* -- Optionen (z.B. «Zucker») ----------------------------------------------------------------------------------- */
// Auswahl pro Gruppe (Nummer der Auswahl): gleichnamige Auswahl in zwei Gruppen («Beilage › Pommes», «Extra › Pommes»)
// bleibt getrennt – die Gruppe zählt, nicht der Text
function openSheet(p) { G.sheet = { p, picked: p.groups.map(() => []) }; drawSheet(); }
// Text wie in der Bestell-App: gleichnamige Auswahl mit Gruppennamen («Extra: Pommes»), sonst weiss die Küche nicht, was gemeint ist
function optLabel(p, gi, c) {
  const g = p.groups[gi], lc = c.toLowerCase();
  const dup = g.name && p.groups.some((h, hi) => hi !== gi && h.choices.some((x) => x.toLowerCase() === lc));
  return dup ? `${g.name}: ${c}` : c;
}
function drawSheet() {
  const { p, picked } = G.sheet;
  const ok = p.groups.every((g, gi) => !g.required || picked[gi].length > 0);
  const el = document.createElement("div");
  el.className = "sheet-bg";
  el.innerHTML = `<div class="sheet" role="dialog" aria-modal="true"><h2>${esc(p.name)}</h2>
    ${p.groups.map((g, gi) => `<p class="g-small g-left">${esc(g.name)}${g.required ? " *" : ""}</p><div class="g-choices">
      ${g.choices.map((c, ci) => `<button class="chip" data-act="pick" data-g="${gi}" data-c="${ci}" aria-pressed="${picked[gi].includes(ci)}">${esc(c)}</button>`).join("")}</div>`).join("")}
    <div class="actions"><button class="go" data-act="sheet-add" ${ok ? "" : "disabled"}>${th("app.gast.add")}</button>
    <button class="alt" data-act="sheet-close">${th("app.gast.close")}</button></div></div>`;
  document.querySelector(".sheet-bg")?.remove();
  document.body.appendChild(el);
}
function closeSheet() { G.sheet = null; document.querySelector(".sheet-bg")?.remove(); }

/* -- Warenkorb ---------------------------------------------------------------------------------------------------- */
// picks: [[Gruppe, Gruppenname, Auswahl], …] (eindeutig; der Name sichert gegen umsortierte Gruppen), options: Anzeige
function add(p, picks = []) {
  const options = picks.map((x) => optLabel(p, x[0], x[x.length - 1]));
  const key = JSON.stringify([p.id, picks]);
  const x = G.cart.find((c) => JSON.stringify([c.product_id, c.picks || c.options]) === key);
  if (x) x.qty = Math.min(x.qty + 1, G.menu.max_qty);
  else G.cart.push({ product_id: p.id, name: p.name, price: p.price, options, picks, qty: 1 });
  saveCart();
}
function saveCart() { store.set("cart." + G.menu.table, G.cart); }

async function pay(btn) {
  btn.disabled = true;
  try {
    const key = store.get("key", null) || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    store.set("key", key);                                             // nochmals tippen = gleiche Bestellung
    const o = await api("/api/gast/order", { method: "POST", body: JSON.stringify({ t: Q.get("t"), s: Q.get("s"),
      key, lang: I18n.lang.slice(0, 2), tip_cents: tipCents(), terms: G.agreed, lines: G.cart.map((x) => (x.picks ? { product_id: x.product_id, qty: x.qty, picks: x.picks }
        : { product_id: x.product_id, qty: x.qty, options: x.options })) }) });
    store.set("key", null);
    store.set("last", { b: o.token, t: Q.get("t"), s: Q.get("s") });
    if (o.link) { location.href = o.link; return; }
    G.order = o; G.view = "order"; draw();
  } catch (e) {
    btn.disabled = false;
    if (e.status === 409 || e.status === 429) store.set("key", null);  // Warenkorb ändern -> neue Bestellung
    alert(e.message);
    if (e.status === 409) loadMenu(true);
  }
}

/* -- Stand nach der Zahlung --------------------------------------------------------------------------------------- */
async function loadOrder() {
  try {
    G.order = await api(`/api/gast/order/${encodeURIComponent(Q.get("b"))}`);
    if (G.order.status === "ordered" || G.order.status === "rejected") store.set("cart." + G.order.table, []);
    G.view = "order"; draw();
  } catch (e) { viewMessage(t("app.gast.offline"), e.message); }
}
function startPoll() { G.poll = setTimeout(loadOrder, 3000); }
function stopPoll() { if (G.poll) clearTimeout(G.poll); G.poll = null; }

async function loadMenu(keepView = false) {
  try {
    G.menu = await api(`/api/gast/menu?t=${encodeURIComponent(Q.get("t") || "")}&s=${encodeURIComponent(Q.get("s") || "")}`);
  } catch (e) {
    return viewMessage(t(e.status === 403 ? "app.gast.bad_qr_title" : "app.gast.offline"), e.status === 403 ? t("app.gast.bad_qr") : e.message);
  }
  const ids = new Set(G.menu.products.filter((p) => !p.soldout).map((p) => p.id));
  G.cart = store.get("cart." + G.menu.table, []).filter((x) => ids.has(x.product_id));
  if (!keepView) G.view = "menu";
  draw();
}

/* -- Klicks -------------------------------------------------------------------------------------------------------- */
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-act]");
  if (!b || b.tagName === "SELECT") return;
  const act = b.dataset.act, p = b.dataset.p ? product(Number(b.dataset.p)) : null;
  if (act === "cat") { G.cat = Number(b.dataset.c); draw(); }
  else if (act === "plus" && p && !p.soldout) { if (p.groups.length) openSheet(p); else { add(p); draw(); } }
  else if (act === "minus" && p) {
    const i = G.cart.findIndex((x) => x.product_id === p.id);
    if (i >= 0 && --G.cart[i].qty <= 0) G.cart.splice(i, 1);
    saveCart(); draw();
  } else if (act === "cplus") { const x = G.cart[Number(b.dataset.i)]; x.qty = Math.min(x.qty + 1, G.menu.max_qty); saveCart(); draw(); }
  else if (act === "cminus") { const i = Number(b.dataset.i); if (--G.cart[i].qty <= 0) G.cart.splice(i, 1); saveCart(); draw(); }
  else if (act === "cart") { G.view = "cart"; draw(); scrollTo(0, 0); }
  else if (act === "menu") { G.view = "menu"; draw(); }
  else if (act === "pay") pay(b);
  else if (act === "tip") { G.tip = b.dataset.k; draw(); }
  else if (act === "pick") {
    const gi = Number(b.dataset.g), ci = Number(b.dataset.c), g = G.sheet.p.groups[gi], sel = G.sheet.picked[gi];
    if (sel.includes(ci)) G.sheet.picked[gi] = sel.filter((x) => x !== ci);           // nur in DIESER Gruppe
    else G.sheet.picked[gi] = g.multi ? [...sel, ci] : [ci];
    drawSheet();
  } else if (act === "sheet-add") {
    const p = G.sheet.p;
    add(p, p.groups.flatMap((g, gi) => g.choices.map((c, ci) => (G.sheet.picked[gi].includes(ci) ? [gi, g.name, c] : null)).filter(Boolean)));
    closeSheet(); draw();
  }
  else if (act === "sheet-close") closeSheet();
  else if (act === "cancel") {
    if (!confirm(t("app.gast.cancel_q"))) return;
    api(`/api/gast/order/${encodeURIComponent(Q.get("b"))}/cancel`, { method: "POST" })
      .then((o) => { G.order = o; draw(); }).catch((e) => alert(e.message));
  } else if (act === "again") {
    const last = store.get("last", null);
    if (last && last.t && last.s) location.href = `/?t=${encodeURIComponent(last.t)}&s=${encodeURIComponent(last.s)}`;
  }
});
document.addEventListener("change", async (ev) => {
  if (ev.target.dataset.act === "agree") { G.agreed = ev.target.checked; draw(); return; }
  if (ev.target.dataset.act !== "lang") return;
  store.set("lang", ev.target.value);
  await I18n.load(ev.target.value);
  document.documentElement.lang = I18n.locale;
  draw();
});

(async function start() {
  await I18n.load(langWanted());
  document.title = t("app.gast.title");
  if (Q.get("b")) {
    const last = store.get("last", null);
    if (last && last.t && last.s && !Q.get("t")) { Q.set("t", last.t); Q.set("s", last.s); }
    if (Q.get("t")) await loadMenu(true).catch(() => {});
    G.view = "order";
    return loadOrder();
  }
  if (!Q.get("t") || !Q.get("s")) return viewMessage(t("app.gast.bad_qr_title"), t("app.gast.bad_qr"));
  loadMenu();
})();
