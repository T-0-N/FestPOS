/* FestPOS – Bestell-App › Offene Tische (Verwaltung › Einstellungen › Kasse › «Tisch offen lassen», app/tabs.py).
   Nach «Senden» bleibt der Tisch offen («Nächste Bestellung» grün, daneben «Jetzt abrechnen»). Wer einen offenen
   Tisch wählt, sieht, wem er gehört und was offen ist: grün «Weiter bestellen», sonst «Abrechnen». Abgerechnet wird
   mit der bekannten Kasse (Rückgeld, Teilen, Einzeln) über alle Bestellungen; jede Zahlung geht sofort an den Server
   (Abrechnen nur mit Verbindung), «Bezahlt · Tisch schliessen» schliesst den Tisch. */
"use strict";

const tabsOn = () => S.boot?.tabs?.mode === "open" && !S.tour;
let TABS = { list: [], at: 0 };
let tabSkip = false;                                  // «Weiter bestellen»: das Fenster nicht nochmals zeigen
const tabFor = (label) => TABS.list.find((x) => x.table.toLowerCase() === String(label).toLowerCase() && !!x.test === !!S.test);
const tabIdle = (x) => { const m = S.boot?.tabs?.idle || 0; return m > 0 && x.last_at && Date.now() - Date.parse(x.last_at) > m * 60000; };
const tabSince = (x) => I18n.fmtTime(Date.parse(x.opened_at));
// «XCover101 (Anna)» -> «Anna» (Name der Bedienung), sonst das Gerät
const tabWho = (name) => { const m = String(name || "").match(/\(([^)]+)\)$/); return m ? m[1] : (name || "?"); };

async function loadTabs(force = false) {
  if (!tabsOn() || (!force && Date.now() - TABS.at < 4000)) return;
  TABS.at = Date.now();
  let r;
  try { r = await api(`/api/tabs?test=${S.test ? "true" : "false"}`); } catch { return; }
  if (!r.ok) return;
  let j;
  try { j = await r.json(); } catch { return; }
  const sig = JSON.stringify(j.tabs);
  const changed = sig !== JSON.stringify(TABS.list);
  TABS = { list: j.tabs || [], at: Date.now(), got: Date.now() };   // got: wirklich angekommen (T35)
  if (changed && S.view === "table" && !document.querySelector(".sheet")) render();   // Plan und Leiste (T35)
}

// Tischplan: Stand pro Tisch (grün = bei mir offen, gestrichelt = anderes Handy, orange = lange nichts bestellt)
function tabClass(label) {
  const x = tabsOn() ? tabFor(label) : null;
  if (!x) return { cls: "", sub: "" };
  return { cls: `${x.mine ? "mine" : "other"}${tabIdle(x) ? " late" : ""}`, sub: x.mine ? chf(x.open) : tabWho(x.owner_name) };
}
function tabLegendHTML() {
  if (!tabsOn()) return "";
  return `<div class="pl-legend"><span><i class="mine"></i>${th("app.tab.legend_mine")}</span><span><i class="other"></i>${th("app.tab.legend_other")}</span>
    ${S.boot?.tabs?.idle ? `<span><i class="late"></i>${th("app.tab.legend_late", { min: S.boot.tabs.idle })}</span>` : ""}
    <button class="link" data-act="tabs-all">${th("app.tab.all_open")}</button></div>`;
}

// Offene Tische ohne Plan sichtbar (T35): Leiste wie «Letzte Tische», links/rechts scrollbar – eigene
// zuerst und grün, mit Betrag; andere mit Name; lange nichts bestellt orange. «Alle» öffnet die Liste.
function tabsSorted() {
  return TABS.list.filter((x) => !!x.test === !!S.test)
    .sort((a, b) => (b.mine - a.mine) || String(a.table).localeCompare(String(b.table), undefined, { numeric: true }));
}
function tabStripHTML() {
  if (!tabsOn()) return "";
  const list = tabsSorted();
  if (!list.length) return "";
  const w = W();
  return `<div class="recent tabstrip" id="tabstrip" aria-label="${th("app.tab.strip_title", { many: w.many })}">
    <span class="ts-l">${th("app.tab.strip")}</span>
    ${list.map((x) => `<button class="chip ts ${x.mine ? "mine" : "other"}${tabIdle(x) ? " late" : ""}" data-act="pick-table" data-t="${esc(x.table)}"><b>${esc(x.table)}</b><small>${esc(x.mine ? chf(x.open) : tabWho(x.owner_name))}</small></button>`).join("")}
    <button class="chip muted" data-act="tabs-all">${th("app.tab.all_open")}</button></div>`;
}
function tabsListSheet() {
  const w = W(), list = tabsSorted();
  openSheet(`<h2>${th("app.tab.list_title", { many: w.many })}</h2>
    ${list.length ? `<div class="tab-list">${list.map((x) => `<button class="tl-row ${x.mine ? "mine" : "other"}${tabIdle(x) ? " late" : ""}" data-act="pick-table" data-t="${esc(x.table)}">
        <b>${esc(`${w.title} ${x.table}`)}</b><span>${th(x.mine ? "app.tab.who_me" : "app.tab.who", { since: tabSince(x), name: x.owner_name || "?" })}</span>
        <strong>${chf(x.open)}</strong></button>`).join("")}</div>`
      : `<p class="muted">${th("app.tab.list_none", { many: w.many })}</p>`}`);
}

// Tisch gewählt (Plan oder Nummer): ist er offen, zuerst fragen. -> true, wenn das Fenster offen ist
function tabAsk(label) {
  if (!tabsOn() || tabSkip) return false;
  const x = tabFor(label);
  if (!x) return false;
  const w = W();
  buzz(20);
  openSheet(`<h2>${th("app.tab.title", { word: w.title, table: x.table })}</h2>
    <div class="tab-who">${th(x.mine ? "app.tab.who_me" : "app.tab.who", { since: tabSince(x), name: x.owner_name || "?" })}</div>
    <div class="tab-tot"><span>${th("app.tab.orders", { count: x.orders })}</span><span>${th("app.tab.open", { cur: cur() })} <b>${chf(x.open)}</b></span></div>
    ${x.can_pay ? "" : `<p class="muted">${th("app.tab.no_right", { name: tabWho(x.owner_name) })}</p>`}
    <div class="actions">
      <button class="go" data-act="tab-order" data-t="${esc(x.table)}">${th("app.tab.order_more")}</button>
      ${x.can_pay ? `<button class="alt" data-act="tab-pay" data-id="${x.id}">${th("app.tab.settle", { cur: cur(), amount: chf(x.open) })}</button>` : ""}
    </div>`);
  return true;
}
function tabOrder(label) {
  closeSheet();
  tabSkip = true;
  try { setTable(label); } finally { tabSkip = false; }
}

// Nach «Senden»: der Tisch bleibt offen – kein Kassieren, stattdessen was am Tisch offen ist (T35):
// «Vorher: 2 Bestellungen seit 18:40 · 45.00» und gross «Total offen» MIT dieser Bestellung. Kam der Stand vom Server
// erst vor dieser Bestellung, zählt die App sie selbst dazu (ohne Verbindung mit Hinweis), bis der Server antwortet.
function tabSentSum(h, x) {
  const fresh = !!x && (TABS.got || 0) >= (h.ts || 0);   // Stand vom Server erst NACH dem Senden geholt
  const own = netOf(h);
  if (!x) return { prevCount: 0, prev: 0, total: own, guess: true, since: null };
  if (fresh) return { prevCount: Math.max(0, x.orders - 1), prev: Math.max(0, x.open - own), total: x.open, guess: false, since: tabSince(x) };
  return { prevCount: x.orders, prev: x.open, total: x.open + own, guess: true, since: tabSince(x) };
}
function tabItemsHTML(items) {
  return `<div class="lines">${items.filter((i) => i.qty > 0).map((i) => `<div class="line"><span>${i.qty}× ${esc(i.name)}${i.opt ? ` <small>${esc(i.opt)}</small>` : ""}</span><span>${chf(i.qty * i.price)}</span></div>`).join("")}</div>`;
}
// Positionen vom Server (qty = was noch gilt, cancelled = storniert; ganz Storniertes mit qty 0, die Nummern bleiben)
// in die Form der Kasse: qty wie bestellt, h.cut storniert (pay.js: bonLine, liveQty)
function tabItems(d) {
  return { items: d.items.map(({ cancelled, ...i }) => ({ ...i, qty: i.qty + (cancelled || 0) })),
    cut: Object.fromEntries(d.items.filter((i) => i.cancelled).map((i) => [i.id, i.cancelled])) };
}
async function tabAllItems(el, id) {
  if (el.dataset.loaded) return;
  el.dataset.loaded = "1";
  let r;
  try { r = await api(`/api/tabs/${id}`); } catch { r = null; }
  const box = el.querySelector(".tab-items");
  if (!box) return;
  if (!r?.ok) { box.textContent = t("app.tab.need_net"); el.dataset.loaded = ""; return; }
  box.innerHTML = tabItemsHTML((await r.json()).items || []);
}
function renderTabSent() {
  const h = S.pay, x = tabFor(h.table), sum = tabSentSum(h, x), w = W();
  app.innerHTML = `<section class="view pay-view">
    ${header(`${esc(W().title)} ${esc(h.table)}`, { back: true })}
    <div class="pay-body">
      ${stateHTML()}
      ${h.test ? `<div class="banner test"><b>${th("app.test.pay_title")}</b>${th("app.test.pay_text")}</div>` : ""}
      ${bonHTML()}
      <div class="tab-open" id="tabopen">
        ${sum.prevCount ? `<div class="r prev"><span>${th("app.tab.before", { count: sum.prevCount, since: sum.since })}</span><b>${chf(sum.prev)}</b></div>` : ""}
        <div class="r tot"><span>${th("app.tab.total_open", { word: w.title, table: h.table })}</span><b>${chf(sum.total)}</b></div>
        <small>${th(sum.guess && S.net === "off" ? "app.tab.total_guess" : "app.tab.stays_later")}</small>
        ${x && sum.prevCount ? `<details class="tab-all" data-tab="${x.id}"><summary>${th("app.tab.all_items")}</summary><div class="tab-items muted">${th("app.tab.loading")}</div></details>` : ""}
      </div>
    </div>
    <div class="foot" id="payfoot">
      <button class="alt" data-act="tab-pay-table" data-t="${esc(h.table)}">${th("app.tab.settle_now")}</button>
      <button class="go" data-act="done">${th("app.pay.next")}</button>
    </div>
  </section>`;
  $("#tabopen .tab-all")?.addEventListener("toggle", (e) => { if (e.target.open) tabAllItems(e.target, e.target.dataset.tab); });
  if (sum.guess) {                                   // Stand vom Server holen, dann mit dieser Bestellung neu zeichnen
    loadTabs(true).then(() => {
      if (S.view === "pay" && S.pay === h && !tabSentSum(h, tabFor(h.table)).guess) renderTabSent();
    });
  }
}

// Abrechnen: Stand vom Server holen, als Eintrag für die Kasse aufbereiten (Positionen zusammengefasst)
async function openTabPay(id, replace = S.view === "pay") {   // replace: true auch nach dem Nachbuchen (view.js)
  closeSheet();
  if (S.net === "off") { toast(t("app.tab.need_net"), true); return; }
  let r;
  try { r = await api(`/api/tabs/${id}`); } catch { r = null; }
  if (!r?.ok) { toast(t("app.tab.need_net"), true); return; }
  const d = await r.json();
  if (d.closed_at) { toast(t("app.tab.already_closed"), true); loadTabs(true); return; }
  if (!d.can_pay) { toast(t("app.tab.no_right", { name: tabWho(d.owner_name) }), true); return; }
  const h = { id: `tab:${d.id}`, tab: d.id, table: d.table, ...tabItems(d), total: d.net, status: "sent", test: d.test || undefined,
    ts: Date.parse(d.opened_at), tabInfo: { orders: d.orders, since: tabSince(d) }, tips: [],
    paid: d.payments.filter((p) => !p.undone_at && !tabUndos(d.id).includes(p.key)).map((p) => ({ amount: p.amount_cents, items: p.items || {}, key: p.key, own: !Object.keys(p.items || {}).length })) };
  S.tabPaidKeys = h.paid.map((p) => p.key);
  openPayEntry(h, replace);                          // aus «Tisch bleibt offen»: ersetzen – Zurück führt zur Tischwahl
  const back = S.nbRestore;                          // nach dem Nachbuchen (T35): Modus und Auswahl wie vorher
  S.nbRestore = null;
  if (back && back.tab === d.id && back.mode && back.mode !== "change") {
    S.payMode = back.mode;
    if (back.mode === "part") {                      // Nummern der Positionen bleiben (app/tabs.py), neue kommen hinten
      const ids = new Set(h.items.map((i) => String(i.id)));
      S.part.sel = Object.fromEntries(Object.entries(back.sel || {}).filter(([k]) => ids.has(String(k))));
    }
    render();
  }
}
async function openTabPayFor(label) {
  await loadTabs(true);
  const x = tabFor(label);
  if (!x) { toast(t(S.net === "off" ? "app.tab.need_net" : "app.tab.not_found"), true); return; }
  if (!x.can_pay) { toast(t("app.tab.no_right", { name: tabWho(x.owner_name) }), true); return; }
  openTabPay(x.id);
}

// Zahlungen an den Server: neue Einträge (ohne key) senden, entfernte (rückgängig) zurücknehmen
const tabKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
// tab: der Tisch, zu dem die Anfrage gehört – nie der eben offene Bildschirm (ein Abgleich kann länger dauern)
async function tabPost(tab, path, body) {
  let r;
  try { r = await api(`/api/tabs/${tab}/${path}`, { method: "POST", body: JSON.stringify(body || {}) }); } catch { r = null; }
  if (r?.ok) return r.json();
  let msg = t("app.tab.need_net");
  try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
  toast(msg, true);
  return null;
}
// Rücknahmen am offenen Tisch ausdrücklich merken (pro Tisch, bis er zu ist): nie aus einem fehlenden key ableiten.
// Das undo geht bei jedem Abgleich nochmals (der Server nimmt nur zurück, was noch gilt – auch eine Zahlung, deren
// Antwort verloren ging)
const tabUndos = (tab) => ((S.tabUndo ||= {})[tab] ||= []);
function tabDropPaid(p) {
  const h = S.pay, i = h?.paid.indexOf(p) ?? -1;
  if (i !== -1) h.paid.splice(i, 1);
  if (h) S.part.paid = h.paid;
  if (h?.tab && p?.key && !tabUndos(h.tab).includes(p.key)) tabUndos(h.tab).push(p.key);
}
// Abgleiche nacheinander: ein undo geht nie vor der Zahlung los, die noch unterwegs ist. Ergebnis: true, wenn alle
// Rücknahmen und Zahlungen beim Server angekommen sind
let tabSyncQ = Promise.resolve(true);
function syncTabPaid() {
  const h = S.pay;
  if (!h?.tab) return Promise.resolve(true);
  tabSyncQ = tabSyncQ.catch(() => false).then(() => syncTabPaidNow(h));
  return tabSyncQ;
}
async function syncTabPaidNow(h) {
  let all = true;
  for (const k of tabUndos(h.tab)) if (!await tabPost(h.tab, "undo", { key: k })) all = false;
  for (let n = 0; n < h.paid.length; n++) {
    const p = h.paid[n];
    if (p.key) continue;
    p.key = tabKey();
    const items = Object.keys(p.items || {}).length ? p.items : null;
    const ok = await tabPost(h.tab, "pay", { key: p.key, amount_cents: p.amount, payment: p.payment || h.payment || null,
      tip_cents: tipsOf(h).find((x) => x.k === (p.tk || (p.person ? `s${p.person}` : `p${n + 1}`)))?.a || 0,
      part: items ? n + 1 : p.person || null, items });
    if (ok) (S.tabPaidAt ||= {})[p.key] = Date.now();   // wann bestätigt: ein älterer Stand vom Server kennt sie nicht
    if (!ok) {                                       // nicht gebucht: wieder offen (nach der Zahlung selbst suchen –
      const i = h.paid.indexOf(p);                   // ein Abgleich mit dem Server kann die Reihenfolge ändern)
      // Antwort verloren, aber doch gebucht? Kein undo daraus: der nächste Abgleich mit dem Server (tabRefresh) bringt
      // sie zurück, abgerechnet wird nach dem Server. Ein undo gibt es nur, wenn die Bedienung sie zurückgenommen hat
      if (i !== -1) h.paid.splice(i, 1);
      if (S.pay === h) { S.part.paid = h.paid; render(); }      // inzwischen ein anderer Tisch offen: nichts anfassen
      return false;
    }
  }
  if (S.pay === h) S.tabPaidKeys = h.paid.map((p) => p.key);
  return all;
}
// «Bezahlt · Tisch schliessen»: Rest buchen (Zahlart, Trinkgeld), dann schliessen
// Während des Schliessens keine Rücknahme (input.js «part-undo»): sie käme nach dem Abgleich und ginge verloren
async function settleTab() {
  if (S.tabSettling) return;
  S.tabSettling = true;
  try { await settleTabNow(); } finally { S.tabSettling = false; }
}
async function settleTabNow() {
  const h = S.pay;
  if (!await syncTabPaid()) return;                  // Rücknahme oder Zahlung nicht angekommen: nicht schliessen
  // offen laut Server (auch Payrexx-Zahlungen, die der Server selbst gebucht hat); bar: auf 5 Rappen gerundet
  let d = null;
  try { const r = await api(`/api/tabs/${h.tab}`); if (r.ok) d = await r.json(); } catch { d = null; }
  if (!d) { toast(t("app.tab.need_net"), true); return; }
  const rest = !h.payment || h.payment === "cash" ? d.open_cash : d.open;
  const tipOfH = (k) => tipsOf(h).find((x) => x.k === k)?.a || 0;
  const partTips = h.paid.reduce((s, p, n) => s + (p.payrexx ? 0 : tipOfH(`p${n + 1}`)), 0);
  if (rest > 0) {
    const ok = await tabPost(h.tab, "pay", { key: tabKey(), amount_cents: rest, payment: h.payment || null,
      tip_cents: Math.max(0, tipSum(h, false) - partTips) });   // ohne Payrexx-Trinkgeld (steht dort im Betrag)
    if (!ok) return;
  }
  const done = await tabPost(h.tab, "close");
  if (!done) return;
  buzz(40);
  toast(t("app.tab.closed_toast", { word: W().title, table: h.table }));
  loadTabs(true);
  closeSheet(); S.input = ""; S.table = null; history.replaceState({ view: "table" }, ""); S.view = "table"; render();
}

setInterval(() => { if (tabsOn() && S.view === "table") loadTabs(); }, 15000);
