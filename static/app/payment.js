/* FestPOS – Bestell-App › Quittung für den Gast und Zahlart (aus / freiwillig / Pflicht). */
"use strict";

// ---------- Quittung für den Gast (Verwaltung › Einstellungen › Kasse: Drucker für Quittungen)
// Unten links an der Kasse: ganze Bestellung (ist schon alles quittiert: Kopie). Bei «Einzeln» pro Person in der Zeile
// «Person 1 · 10.50 ✓». Nur mit Verbindung (die Nummer vergibt der Server) und erst, wenn die Bestellung angekommen ist.
// In der Einführung sichtbar (sie zeigt darauf), antippen geht dort nicht.
const receiptOn = () => !!S.boot?.receipt && !!S.pay && S.pay.status !== "rejected";
function footReceipt() {
  if (!receiptOn() || S.payMode === "part") return "";
  const h = S.pay, done = (h.receipts || []).some((r) => !r.part);
  return `<button class="alt rcpt" id="rcptbtn" data-act="receipt" ${h.status === "sent" ? "" : "disabled"}
    aria-label="${th(done ? "app.receipt.again" : "app.receipt.print")}">${ICON.receipt}<small>${th(done ? "app.receipt.copy_short" : "app.receipt.short")}</small></button>`;
}
function partReceiptBtn(p, i) {
  if (!S.boot?.receipt || S.tour || S.pay?.status !== "sent" || !p.items || !Object.keys(p.items).length) return "";
  return `<button class="link sm rcpt-l" data-act="receipt-part" data-n="${i}">${ICON.receipt}${th(p.receipt ? "app.receipt.copy_short" : "app.receipt.short")}</button>`;
}
// Eine bezahlte Person antippen (Chip oder Liste): Quittung und – bei jeder Person, nicht nur der letzten –
// «Zahlung rückgängig». Nicht mit Quittung (fortlaufende Nummer, Prüfsumme) und nicht mit Karte/TWINT über Payrexx
// (schon abgebucht: Rückerstattung in der Verwaltung); das Fenster sagt warum.
function openPartPerson(n) {
  const P = S.part, p = P.paid[n];
  if (!p) return;
  const no = p.person || n + 1, can = S.boot?.receipt && !S.tour && S.pay?.status === "sent";
  const rc = p.person ? (can ? `<button class="alt" data-act="receipt-split" data-n="${p.person}">${th(p.receipt ? "app.receipt.again" : "app.receipt.print")}</button>` : "")
    : partReceiptBtn(p, n) ? `<button class="alt" data-act="receipt-part" data-n="${n}">${th(p.receipt ? "app.receipt.again" : "app.receipt.print")}</button>` : "";
  const why = p.receipt ? t("app.split.undo_receipt") : p.payrexx ? t("app.split.undo_payrexx") : "";
  const undo = !why && !(S.pay?.tab && S.tabSettling) ? `<button class="alt danger" data-act="part-undo" data-n="${n}">${th("app.split.undo_person", { n: no })}</button>` : "";
  const what = [itemNames(p.items), kindOf(p) ? payLabel(kindOf(p)) : ""].filter(Boolean).join(" · ");
  openSheet(`<h2>${p.person || Object.keys(p.items || {}).length ? th("app.pay.person", { n: no }) : th("app.split.part_payment")} · ${chf(p.amount)}</h2>
    <p class="muted">${esc(what) || th("app.pay.own_title")}</p>${why ? `<p class="muted small">${esc(why)}</p>` : ""}
    <div class="actions">${rc}${undo}<button class="go" data-act="close-sheet">${th("app.status.close")}</button></div>`);
}
// Teilen: Quittung über den Anteil dieser Person (ohne Positionen, MWST anteilig; app/receipts.py)
function receiptSplit(n) {
  const h = S.pay, p = S.part.paid.find((x) => x.person === n);
  if (!h || !p) return;
  if (p.receipt?.id) { printReceipt({ copy_of: p.receipt.id }); return; }
  printReceipt({ share: { person: n, persons: splitPlan().N, amount: p.amount }, payment: kindOf(p) }, (j) => {
    const r = j.receipts?.[0];
    if (r && !j.copy) { p.receipt = { id: r.id, no: r.no }; savePaid(); }
  });
}
function receiptToast(j) {
  const r = j.receipts?.[0] || {};
  const nos = (j.receipts || []).map((x) => (x.no ? String(x.no).padStart(5, "0") : t("app.test.tag"))).join(", ");
  const key = j.copy ? "app.receipt.copy_printing" : r.test ? "app.receipt.test_printing" : "app.receipt.printing";
  let msg = t(key, { no: nos, printer: j.printer || "" });
  const pr = (PST.printers || []).find((x) => x.name === j.printer);
  if (pr && pr.state === "rerouted") msg += " " + t("app.receipt.printer_rerouted", { target: pr.target });
  else if (pr && pr.state === "down") msg += " " + t("app.receipt.printer_down");
  toast(msg, !!pr && pr.state !== "ok");
}
function rememberReceipts(h, j) {
  if (j.copy || !h) return;
  const have = new Set((h.receipts || []).map((x) => x.id));
  const add = (j.receipts || []).filter((x) => !have.has(x.id)).map((x) => ({ id: x.id, no: x.no, part: x.part || null }));
  if (add.length) updateHist(h.id, { receipts: [...(h.receipts || []), ...add] });
}
let rcptBusy = false;
async function printReceipt(body, onDone) {
  const h = S.pay;
  if (!h || rcptBusy) return;
  if (h.status !== "sent") { toast(t("app.receipt.wait_sent"), true); return; }
  if (S.net === "off") { toast(t("app.receipt.offline"), true); return; }
  rcptBusy = true;
  buzz(20);
  try {
    // offener Tisch (tabs.js): Quittung über alle Bestellungen des Tisches, mit der an der Kasse gewählten Zahlart
    const who = h.tab ? { tab_id: h.tab, payment: h.payment || null } : { order_uuid: h.id };
    const r = await api("/api/orders/receipt", { method: "POST", body: JSON.stringify({ ...who, request_id: uuid(), ...body }) });
    let j = {};
    try { j = await r.json(); } catch { /* leer */ }
    if (!r.ok) { toast(typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: r.status }), true); return; }
    rememberReceipts(h, j);
    if (onDone) onDone(j);
    receiptToast(j);
  } catch {
    setNet("off");
    toast(t("app.receipt.offline"), true);
  } finally {
    rcptBusy = false;
    if (S.view === "pay") { renderPayState(); refreshPanel(); }
  }
}
function receiptWhole() { printReceipt({}); }
function receiptPart(n) {
  const h = S.pay, p = S.part.paid[n];
  if (!h || !p) return;
  if (p.receipt?.id) { printReceipt({ copy_of: p.receipt.id }); return; }
  const items = Object.entries(p.items || {}).map(([id, q]) => {
    const it = h.items.find((i) => i.id === +id);
    return it ? { product_id: it.pid, qty: q, options: it.opt || null } : null;
  }).filter(Boolean);
  if (!items.length) return;
  printReceipt({ items, part: n + 1 }, (j) => {
    const r = j.receipts?.[0];
    if (r && !j.copy) { p.receipt = { id: r.id, no: r.no }; savePaid(); }
  });
}

// ---------- Zahlart (Verwaltung › Einstellungen: aus / freiwillig / Pflicht)
// Ohne Einstellung ändert sich für die Bedienung nichts. Die Zahlart geht mit der Bestellung mit, solange sie noch
// im Gerät wartet, sonst wird sie nachgereicht (auch offline: Warteschlange «payq», wie der Ausgang).
const payModeCfg = () => (["optional", "required"].includes(S.boot?.payment_mode) ? S.boot.payment_mode : "off");
const PAY_KINDS = [["cash", "ledger.pay.cash"], ["card", "ledger.pay.card"], ["twint", "ledger.pay.twint"]];
const payLabel = (v) => (v === "other" ? t("ledger.pay.other") : v === "payrexx" ? t("ledger.pay.payrexx")
  : t(PAY_KINDS.find(([x]) => x === v)?.[1] || "ledger.pay.none"));
// Pflicht: «Nächste Bestellung» erst mit Zahlart (abgelehnte Bestellungen ausgenommen – da gibt es nichts zu kassieren)
const payMissing = () => payModeCfg() === "required" && !!S.pay && !S.pay.payment && S.pay.status !== "rejected" && !S.tour
  && !(S.pay.tabMode && !S.pay.tab)                  // Tisch bleibt offen: bezahlt wird beim Abrechnen
  && !(partState().rest <= 0 && S.part.paid.length && S.part.paid.every((p) => p.payment));   // jede Person mit Zahlart
function payRowHTML() {
  const h = S.pay, mode = payModeCfg();
  if (!h || h.status === "rejected" || (mode === "off" && !tapOn())) return "";
  // T13: Karte/TWINT mit NFC (Payrexx) – bezahlt: grün statt Knopf
  const qr = h.payment === "payrexx" ? `<span class="status ok qrdone">${th("app.payrexx.paid_row")}</span>`
    : tapBtn("payrexx-all", t("ledger.pay.payrexx"));
  return `<div class="payrow${mode === "required" && !h.payment ? " need" : ""}" id="payrow">
    <span>${th("app.payment.title")}${mode === "optional" ? ` <small>${th("app.payment.optional")}</small>` : ""}</span>
    ${mode === "off" ? "" : `<div class="seg">${PAY_KINDS.map(([v, k]) => `<button data-act="pay-kind" data-v="${v}" aria-pressed="${h.payment === v}" ${h.payment === "payrexx" ? "disabled" : ""}>${th(k)}</button>`).join("")}</div>`}
    ${qr}
  </div>`;
}
function setPayment(v) {
  const h = S.pay;
  if (!h || S.tour || h.payment === v || h.payment === "payrexx") return;   // Karte/TWINT bestätigt: bleibt (Server ebenso)
  updateHist(h.id, { payment: v });
  buzz(15);
  const o = outbox.find((x) => x.client_uuid === h.id);
  if (o) { o.payment = v; store.set("outbox", outbox); }          // geht mit der Bestellung mit
  else if (h.status === "sent" && !h.tab) queuePay(h.id, v);       // offener Tisch: Zahlart geht mit der Zahlung
  const row = $("#payrow"); if (row) row.outerHTML = payRowHTML();
}
// Einzeln gewählt und noch etwas offen (auch wenn noch niemand bezahlt hat)
const partOpen = () => S.view === "pay" && (S.payMode === "part" || S.part.paid.length > 0) && !!S.pay
  && S.pay.status !== "rejected" && !S.tour && partState().rest > 0;
// Einzeln begonnen (jemand hat bezahlt oder etwas ist gewählt) und noch etwas offen
const partBusy = () => partOpen() && (S.part.paid.length > 0 || Object.keys(S.part.sel).length > 0);
function askLeavePart(act) {
  buzz([40, 30, 40]);
  document.querySelectorAll(".toast").forEach((e) => e.remove());   // Hinweis würde die Knöpfe verdecken
  openSheet(`<h2>${th("app.pay.leave_title")}</h2>
    <p>${th("app.pay.leave_text", { cur: cur(), amount: chf(partState().rest) })}</p>
    <div class="actions"><button class="go" data-act="close-sheet">${th("app.pay.leave_back")}</button>
      <button class="alt" data-act="${act}" data-force="1">${th("app.pay.leave_anyway")}</button></div>`);
}
function needPayment() {
  buzz([40, 30, 40]);
  toast(t("app.payment.choose_first"), true);
  const row = $("#payrow");
  if (row) { row.classList.remove("nudge"); void row.offsetWidth; row.classList.add("nudge"); }
}
function queuePay(id, payment) {
  payq = [...payq.filter((x) => x.id !== id), { id, payment }];
  store.set("payq", payq);
  flushPay();
}
let payFlushing = false, payTimer = null;
async function flushPay() {
  if (payFlushing || !payq.length) return;
  payFlushing = true;
  clearTimeout(payTimer);
  try {
    while (payq.length) {
      const p = payq[0];
      let r;
      try { r = await api("/api/orders/payment", { method: "POST", body: JSON.stringify({ client_uuid: p.id, payment: p.payment }) }); }
      catch { break; }
      if (r.status >= 500 || [403, 408, 429].includes(r.status)) break;   // Ausfall, Zertifikat: später nochmals
      if (!r.ok) {                                   // schon abgeschlossen oder unbekannt: nicht endlos wiederholen
        let msg = t("app.net.error_status", { status: r.status });
        try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
        toast(t("app.payment.not_saved", { msg }), true);
      }
      payq = payq.filter((x) => x !== p);           // nur diesen Eintrag (inzwischen neu gewählt: der bleibt)
      store.set("payq", payq);
    }
  } finally {
    payFlushing = false;
    if (payq.length) payTimer = setTimeout(flushPay, 5000);
  }
}

// ---------- Trinkgeld (Verwaltung › Einstellungen › Kasse, app/tips.py)
// Unter dem Ziffernblock «+ Trinkgeld» (Rückgeld, Teilen, Einzeln). Es gehört zur Bestellung: h.tips = [{k, a, x}],
// k = c (Rückgeld) | s<n> (Teilen, n-tes Trinkgeld) | p<n> (Einzeln, Person n), a = Rappen, x = mit Payrexx bezahlt
// (steht dort im Betrag). Der Pi bekommt die Summe ohne Payrexx – nachgereicht wie die Zahlart (Warteschlange «tipq»).
const TIP_MAX = 100000;
const tipOn = () => !!S.boot?.tips && !!S.pay && S.pay.status !== "rejected" && !S.tour;
const tipsOf = (h) => h?.tips || [];
const tipOf = (k) => tipsOf(S.pay).find((x) => x.k === k);
const tipAt = (k) => tipOf(k)?.a || 0;
const tipSum = (h, withPayrexx = true) => tipsOf(h).reduce((s, x) => s + (withPayrexx || !x.x ? x.a : 0), 0);
function setTip(k, a, payrexx = false) {
  const h = S.pay;
  if (!h || S.tour) return;
  const list = tipsOf(h).filter((x) => x.k !== k);
  if (a > 0) list.push(payrexx ? { k, a, x: 1 } : { k, a });
  updateHist(h.id, { tips: list });
  if (!h.tab) queueTip(h.id, tipSum(h, false));      // offener Tisch: Trinkgeld geht mit der Zahlung
  const bon = $("#bon"); if (bon && S.view === "pay") bon.outerHTML = bonHTML();   // «Trinkgeld …» unter dem Total
}
// Teilen: Trinkgeld pro Person «s<Person>» (fest an der Person, auch nach «rückgängig» einer anderen)
// Wofür gilt «+ Trinkgeld» gerade: Platz, Betrag ohne Trinkgeld, eingetippter Betrag
function tipCtx() {
  if (S.payMode === "split") {
    const n = S.splitStep || splitPlan().next || 1;
    return { k: `s${n}`, due: splitPlan().persons.find((p) => p.n === n)?.amount || 0, given: S.splitGiven };
  }
  if (S.payMode === "part" && partStep() === "pay") return { k: `p${S.part.paid.length + 1}`, due: partSel().amount, given: S.part.given };
  return { k: "c", due: partState().rest, given: S.given };
}
// Zeile «inkl. Trinkgeld 1.00 ✓» unter Gegeben/Rückgeld und der Knopf unter dem Ziffernblock
function tipLine(k) {
  const x = k && tipOf(k);
  if (!tipOn() || !x) return "";
  return x.x ? `<p class="tipline">${th("app.tip.included_paid", { amount: chf(x.a) })}</p>`
    : `<button class="link sm tipline" data-act="tip">${th("app.tip.included", { amount: chf(x.a) })}</button>`;
}
function tipBtn(k) {
  if (!tipOn() || (k && tipOf(k)) || (k === "c" && S.pay.payment === "payrexx")) return "";
  return `<button class="alt part-btn tip-btn" data-act="tip">${th("app.tip.add")}</button>`;
}
function openTip() {
  const c = tipCtx(), a = tipAt(c.k);
  S.tipEdit = { k: c.k, due: c.due, given: parseEntry(c.given || ""), v: a ? (a / 100).toFixed(2).replace(/\.00$/, "") : "" };
  openSheet(tipSheet());
}
function tipSheet() {
  const E = S.tipEdit, v = parseEntry(E.v || ""), up = Math.floor(E.due / 100) * 100 + 100 - E.due;
  const exact = E.given > E.due ? E.given - E.due : 0;
  const chip = (val, label) => `<button class="chip wide" data-act="tip-set" data-v="${val}" aria-pressed="${v === val}">${label}</button>`;
  const rest = E.given ? E.given - E.due - v : null;
  const out = rest == null ? `<div class="c-out ${v ? "good" : "none"}"><span>${th("app.tip.with_total")}</span><b>${chf(E.due + v)}</b></div>`
    : rest >= 0 ? `<div class="c-out good"><span>${th("app.tip.change_after")}</span><b>${chf(rest)}</b></div>`
    : `<div class="c-out short"><span>${th("app.pay.missing")}</span><b>${chf(-rest)}</b></div>`;
  const ok = v > 0 && v <= TIP_MAX;
  return `<div id="tipsheet"><h2>${th("app.tip.title")}</h2>
    <p class="muted">${E.given ? th("app.tip.sub_given", { total: chf(E.due), given: chf(E.given) }) : th("app.tip.sub", { total: chf(E.due) })}</p>
    <div class="tipgrid">${chip(up, th("app.tip.round", { amount: chf(E.due + up), tip: chf(up) }))}
      ${exact && exact !== up ? chip(exact, th("app.tip.exact", { given: chf(E.given), tip: chf(exact) })) : ""}
      ${[100, 200, 500].map((x) => `<button class="chip" data-act="tip-plus" data-v="${x}">+${x / 100}</button>`).join("")}</div>
    <div class="calc"><div class="c-given"><span>${th("app.tip.amount")}</span><output>${esc(E.v || "")}</output></div>${out}</div>
    ${keypad("tip")}
    <div class="actions"><button class="go" data-act="tip-ok" ${ok ? "" : "disabled"}>${ok ? th("app.tip.ok", { amount: chf(v) }) : th("app.tip.enter")}</button>
      <button class="alt" data-act="tip-none">${th("app.tip.none")}</button></div></div>`;
}
function refreshTip() { const el = $("#tipsheet"); if (el && S.tipEdit) el.outerHTML = tipSheet(); }
function tipPick(cents, add) {
  const E = S.tipEdit;
  if (!E) return;
  const v = Math.min(TIP_MAX, (add ? parseEntry(E.v || "") : 0) + cents);
  E.v = (v / 100).toFixed(2).replace(/\.00$/, "");
  buzz();
  refreshTip();
}
function tipDone(none) {
  const E = S.tipEdit;
  if (!E) return;
  const v = none ? 0 : parseEntry(E.v || "");
  if (v > TIP_MAX) return;
  setTip(E.k, v);
  S.tipEdit = null;
  closeSheet();
  buzz(30);
  refreshPanel();
}
// Teilzahlungen einer sofort kassierten Bestellung an den Server: jeweils alle, die am Handy gelten –
// was fehlt, nimmt der Server zurück. Nachgereicht wie Zahlart und Trinkgeld (Warteschlange «partq»).
const payKey = () => `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
function queueParts(id) {
  partq = [...partq.filter((x) => x !== id), id];
  store.set("partq", partq);
  flushParts();
}
let partFlushing = false, partTimer = null;
async function flushParts() {
  if (partFlushing || !partq.length) return;
  partFlushing = true;
  clearTimeout(partTimer);
  try {
    for (const id of [...partq]) {
      if (outbox.some((o) => o.client_uuid === id)) continue;       // Bestellung noch unterwegs: danach
      const h = history_.find((x) => x.id === id);
      const payments = (h?.paid || []).map((p, i) => ({ key: p.key, amount_cents: p.amount, payment: p.payment || null,
        tip_cents: tipsOf(h).find((x) => x.k === (p.tk || (p.person ? `s${p.person}` : `p${i + 1}`)))?.a || 0,
        person: p.person || (Object.keys(p.items || {}).length ? i + 1 : null),
        items: Object.keys(p.items || {}).length ? p.items : null })).filter((p) => p.key);
      let r;
      try { r = await api("/api/orders/payments", { method: "POST", body: JSON.stringify({ client_uuid: id, payments }) }); }
      catch { break; }
      if (r.status >= 500 || [403, 408, 429].includes(r.status)) break;   // Ausfall, Zertifikat: später nochmals
      if (!r.ok) {                                   // abgeschlossen, unbekannt oder zu viel: nicht endlos wiederholen
        let msg = t("app.net.error_status", { status: r.status });
        try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
        toast(t("app.split.not_saved", { msg }), true);
      }
      partq = partq.filter((x) => x !== id);
      store.set("partq", partq);
    }
  } finally {
    partFlushing = false;
    if (partq.length) partTimer = setTimeout(flushParts, 5000);
  }
}
function queueTip(id, cents) {
  tipq = [...tipq.filter((x) => x.id !== id), { id, cents }];
  store.set("tipq", tipq);
  flushTip();
}
let tipFlushing = false, tipTimer = null;
async function flushTip() {
  if (tipFlushing || !tipq.length) return;
  tipFlushing = true;
  clearTimeout(tipTimer);
  try {
    for (const p of [...tipq]) {
      if (outbox.some((o) => o.client_uuid === p.id)) continue;      // Bestellung noch unterwegs: danach nachreichen
      let r;
      try { r = await api("/api/orders/tip", { method: "POST", body: JSON.stringify({ client_uuid: p.id, tip_cents: p.cents }) }); }
      catch { break; }
      if (r.status >= 500 || [403, 408, 429].includes(r.status)) break;   // Ausfall, Zertifikat: später nochmals
      if (!r.ok) {                                   // schon abgeschlossen oder abgelehnt: nicht endlos wiederholen
        let msg = t("app.net.error_status", { status: r.status });
        try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
        toast(t("app.tip.not_saved", { msg }), true);
      }
      tipq = tipq.filter((x) => x !== p);           // nur diesen Eintrag (inzwischen geändert: der neue bleibt)
      store.set("tipq", tipq);
    }
  } finally {
    tipFlushing = false;
    if (tipq.length) tipTimer = setTimeout(flushTip, 5000);
  }
}
