/* FestPOS – Bestell-App › Storno am Handy (Rolle «Bedienung + Storno» oder «Festleitung»). */
"use strict";

// ---------- Storno am Handy (nur mit der Rolle «Bedienung + Storno», Verwaltung › Geräte)
// Braucht eine Verbindung: Der Server weiss, was noch offen ist, bucht die Gegenbuchung und druckt den Storno-Bon.
const STORNO_REASONS = [["wrong", "ledger.reason.wrong"], ["unpaid", "ledger.reason.unpaid"],
  ["complaint", "ledger.reason.complaint"], ["other", "ledger.reason.other"]];
// Offener Tisch (S.pay.tab): auch beim Abrechnen – über alle Bestellungen am Tisch (app/tabs.py: storno)
const stornoBtn = () => (S.boot?.can_storno && S.pay?.status === "sent" && !S.tour
  ? `<button class="link danger" data-act="storno">${th("app.storno.button")}</button>` : "");
// Storno pro Position der Kasse (h.cut, pay.js): der Server zählt pro Bestellposition (eigene Reihenfolge, gleiches
// Produkt mit gleichen Optionen zusammengefasst) – auf die Positionen am Handy verteilen, passende Optionen zuerst
function cutFromLines(h, lines) {
  const cut = {}, room = Object.fromEntries(h.items.map((i) => [i.id, i.qty]));
  for (const l of lines) {
    let q = l.cancelled || 0;
    const fits = (i) => [l.options, l.note].filter(Boolean).every((x) => (i.opt || "").includes(x));
    const cands = h.items.filter((i) => (l.product_id ? i.pid === l.product_id : i.name === l.name) && i.price === l.price_cents)
      .sort((a, b) => fits(b) - fits(a));
    for (const i of cands) {
      const n = Math.min(q, room[i.id]);
      if (n > 0) { cut[i.id] = (cut[i.id] || 0) + n; room[i.id] -= n; q -= n; }
    }
  }
  return Object.keys(cut).length ? cut : undefined;
}
// Offener Tisch: Positionen wie in der Kasse (zusammengefasst, Nummer = id); «Einzeln» bezahlte Stück sind gesperrt
function tabStornoData(d) {
  const paid = {};
  d.payments.filter((p) => !p.undone_at).forEach((p) => Object.entries(p.items || {}).forEach(([k, q]) => { paid[k] = (paid[k] || 0) + q; }));
  return { closed: !!d.closed_at, tab: d, items: d.items.map((i) => ({ line_no: i.id, name: i.name, options: i.opt, note: "",
    price_cents: i.price, qty: i.qty + (i.cancelled || 0), cancelled: i.cancelled || 0, paid: Math.min(i.qty, paid[i.id] || 0) })) };
}
// Stand des Tisches in die Kasse übernehmen (nach dem Storno oder wenn inzwischen anderswo storniert wurde)
// asOf: wann der Stand d beim Server angefragt wurde. Eine Zahlung, die erst danach bestätigt wurde, fehlt darin noch –
// sie bleibt, statt kurz zu verschwinden (der offene Betrag wäre sonst zu hoch)
function tabRefresh(d, asOf = Date.now()) {
  const h = S.pay;
  if (!h?.tab || h.tab !== d.id) return;
  Object.assign(h, tabItems(d), { total: d.net });
  // Zahlungen laut Server (auch von anderen Handhelds). Bleiben wie hier: noch nicht gesendet (ohne key) oder eben
  // unterwegs (key, vom Server noch nie bestätigt). Was die Bedienung hier zurückgenommen hat (tabUndos), bleibt weg –
  // auch wenn der Server es noch führt; syncTabPaid (tabs.js) holt beides nach
  const confirmed = new Set(S.tabPaidKeys || []);
  const local = new Map(h.paid.filter((p) => p.key).map((p) => [p.key, p]));
  const active = d.payments.filter((p) => !p.undone_at), onServer = new Set(active.map((p) => p.key));
  const dropped = new Set(tabUndos(h.tab));
  const before = h.paid;
  h.paid = [...active.filter((p) => !dropped.has(p.key)).map((p) => local.get(p.key)
    || { amount: p.amount_cents, items: p.items || {}, key: p.key, own: !Object.keys(p.items || {}).length }),
  ...before.filter((p) => !p.key || (!onServer.has(p.key) && !dropped.has(p.key)
    && (!confirmed.has(p.key) || (S.tabPaidAt?.[p.key] || 0) >= asOf)))];
  // Trinkgeld «Einzeln» hängt an der Nummer der Person (p<n>): mit der Zahlung mitziehen, auch das der nächsten Person
  const where = (n) => (n === before.length + 1 ? h.paid.length + 1 : h.paid.indexOf(before[n - 1]) + 1);
  h.tips = tipsOf(h).flatMap((x) => {
    if (x.k[0] !== "p") return [x];
    const m = where(+x.k.slice(1));
    return m > 0 ? [{ ...x, k: `p${m}` }] : [];
  });
  S.part.paid = h.paid;
  // vom Server bestätigt – auch was nach dem Stand bestätigt wurde und deshalb darin noch fehlt
  S.tabPaidKeys = [...active.map((p) => p.key), ...h.paid.filter((p) => p.key && !onServer.has(p.key) && confirmed.has(p.key)).map((p) => p.key)];
  for (const [k, q] of Object.entries(S.part.sel)) {          // Auswahl bei «Einzeln»: nie mehr als noch offen
    const i = h.items.find((x) => x.id === +k), { paidQty } = partState();
    const open = i ? liveQty(i) - (paidQty[k] || 0) : 0;
    if (open <= 0) delete S.part.sel[k]; else if (q > open) S.part.sel[k] = open;
  }
  if (S.view === "pay") VIEWS.pay();                 // ohne render(): das Storno-Fenster bleibt offen
}
async function openStorno() {
  const h = S.pay;
  if (!h || h.status !== "sent" || !S.boot?.can_storno || S.tour) return;
  openSheet(`<h2 id="storno-load">${th("app.storno.title")}</h2><p class="muted">${th("app.storno.loading")}</p>`, { outside: "warn" });
  let d = null, msg = "";
  const asOf = Date.now();                          // Stand vom Server ab jetzt (tabRefresh)
  try {
    const r = await api(h.tab ? `/api/tabs/${h.tab}` : `/api/orders/lines?uuid=${encodeURIComponent(h.id)}`);
    if (r.ok) d = await r.json();
    else {
      msg = t("app.net.error_status", { status: r.status });
      try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
    }
  } catch { msg = t("app.storno.offline"); }
  if (!$("#storno-load") || S.pay?.id !== h.id) return;                   // inzwischen geschlossen
  if (!d) { closeSheet(); toast(msg, true); return; }
  if (h.tab) {
    const raw = d;
    d = tabStornoData(raw);
    if (!d.closed) tabRefresh(raw, asOf);                   // auch anderswo storniert: Kasse dahinter nachführen
  } else {
    // auch in der Verwaltung storniert: Betrag und Positionen in der Kasse dahinter nachführen (Fenster bleibt offen)
    const cut = cutFromLines(h, d.items);
    if ((h.storno || 0) !== d.storno_cents || JSON.stringify(h.cut) !== JSON.stringify(cut)) {
      updateHist(h.id, { storno: d.storno_cents || undefined, cut });
      if (S.view === "pay") VIEWS.pay();
    }
  }
  S.storno = { id: h.id, tab: h.tab, uuid: uuid(), d, sel: {}, reason: null, note: "", busy: false };
  refreshStorno();
}
const stornoLeft = (i) => i.qty - i.cancelled - (i.paid || 0);
const stornoSum = () => S.storno.d.items.reduce((s, i) => s + (S.storno.sel[i.line_no] || 0) * i.price_cents, 0);
function stornoHTML() {
  const E = S.storno, d = E.d, h = S.pay;
  const head = `<h2 data-storno>${th("app.storno.title_table", { word: W().title, table: h.table })}</h2>${
    E.tab ? `<p class="muted">${th("app.storno.tab_hint")}</p>` : ""}`;
  const open = d.items.filter((i) => stornoLeft(i) > 0);
  if (d.closed || !open.length) {
    const why = d.closed ? (E.tab ? "app.tab.already_closed" : "app.storno.closed") : E.tab ? "app.storno.tab_nothing" : "app.storno.nothing_left";
    return `${head}<p>${th(why)}</p>
      <div class="actions"><button class="alt" data-act="close-sheet">${th("app.common.understood")}</button></div>`;
  }
  const sum = stornoSum();
  const all = open.every((i) => (E.sel[i.line_no] || 0) === stornoLeft(i));
  return `${head}
    ${d.items.map((i) => {
      const left = stornoLeft(i), q = E.sel[i.line_no] || 0, sub = [i.options, i.note].filter(Boolean).join(", ");
      return `<div class="row${left ? "" : " gone"}">
        <span class="nm">${i.qty}× ${esc(i.name)}${sub ? `<small>${esc(sub)}</small>` : ""}${i.cancelled ? `<small>${th("app.storno.already", { count: i.cancelled })}</small>` : ""}${i.paid ? `<small>${th("app.storno.paid", { count: i.paid })}</small>` : ""}</span>
        ${left ? `<div class="qty">
          <button data-act="storno-q" data-l="${i.line_no}" data-d="-1" ${q ? "" : "disabled"} aria-label="${th("app.common.less")}">${ICON.minus}</button>
          <output>${q}</output>
          <button data-act="storno-q" data-l="${i.line_no}" data-d="1" ${q < left ? "" : "disabled"} aria-label="${th("app.common.more")}">${ICON.plus}</button>
        </div>` : ""}</div>`;
    }).join("")}
    <button class="link" data-act="storno-all">${th(all ? "app.storno.none" : "app.storno.all")}</button>
    <div class="optg${E.reason ? "" : " todo"}"><div class="optg-h">${th("app.storno.reason")}</div>
      <div class="optg-c">${STORNO_REASONS.map(([v, k]) => `<button class="chip" data-act="storno-reason" data-v="${v}" aria-pressed="${E.reason === v}">${th(k)}</button>`).join("")}</div></div>
    <label class="field">${th("app.storno.note")}<input id="stornonote" value="${esc(E.note)}" maxlength="80" autocomplete="off" enterkeyhint="done"></label>
    <div class="actions">
      <button class="go danger" data-act="storno-ok" ${sum > 0 && E.reason && !E.busy ? "" : "disabled"}>${
        sum <= 0 ? th("app.storno.pick") : !E.reason ? th("app.storno.pick_reason") : th("app.storno.ok", { cur: cur(), amount: chf(sum) })}</button>
      <button class="alt" data-act="close-sheet">${th("app.common.cancel")}</button>
    </div>`;
}
function refreshStorno() {
  const sh = document.querySelector(".sheet");
  if (!sh || !S.storno || !sh.querySelector("#storno-load, [data-storno]")) return;     // nur das Storno-Fenster
  const top = sh.scrollTop;
  sh.innerHTML = stornoHTML();
  sh.scrollTop = top;
  $("#stornonote")?.addEventListener("input", (e) => { if (S.storno) S.storno.note = e.target.value; });
}
function stornoQty(line, d) {
  const E = S.storno, it = E?.d.items.find((i) => i.line_no === line);
  if (!it) return;
  E.sel[line] = Math.max(0, Math.min(stornoLeft(it), (E.sel[line] || 0) + d));
  buzz(8);
  refreshStorno();
}
function stornoAll() {
  const E = S.storno, open = E.d.items.filter((i) => stornoLeft(i) > 0);
  const all = open.every((i) => (E.sel[i.line_no] || 0) === stornoLeft(i));
  E.sel = all ? {} : Object.fromEntries(open.map((i) => [i.line_no, stornoLeft(i)]));
  buzz(8);
  refreshStorno();
}
async function stornoOk() {
  const E = S.storno;
  if (!E || E.busy || !E.reason) return;
  const lines = Object.entries(E.sel).filter(([, q]) => q > 0).map(([l, q]) => ({ line_no: +l, qty: q }));
  if (!lines.length) return;
  E.busy = true; refreshStorno();
  let r = null, msg = t("app.storno.offline");
  const note = E.note.trim().slice(0, 80), asOf = Date.now();
  try {
    r = E.tab
      ? await api(`/api/tabs/${E.tab}/storno`, { method: "POST", timeout: 12000, body: JSON.stringify({
        client_uuid: E.uuid, lines: lines.map((l) => ({ id: l.line_no, qty: l.qty })), reason: E.reason, note }) })
      : await api("/api/orders/storno", { method: "POST", timeout: 12000, body: JSON.stringify({
        client_uuid: E.uuid, order_uuid: E.id, lines, reason: E.reason, note }) });
  } catch { r = null; }
  E.busy = false;
  if (r?.ok) {
    const j = await r.json();
    if (E.tab) {
      if (j.tab) tabRefresh(j.tab, asOf);
      loadTabs(true);
    } else {
      // gleiche Nummer bei Wiederholung: der Server bucht nur einmal (duplicate) – Summe vom Server, nicht addieren
      const now = E.d.items.map((i) => ({ ...i, cancelled: i.cancelled + (E.sel[i.line_no] || 0) }));
      const h = history_.find((x) => x.id === E.id) || S.pay;
      updateHist(E.id, { storno: (E.d.storno_cents || 0) + j.amount_cents, cut: cutFromLines(h, now) });
    }
    S.storno = null;
    closeSheet();
    buzz(40);
    toast(t(j.printer_ids?.length ? "app.storno.done_bon" : "app.storno.done", { cur: cur(), amount: chf(j.amount_cents) }));
    if (S.view === "pay") render();
    return;
  }
  if (r) {
    msg = t("app.net.error_status", { status: r.status });
    try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* egal */ }
  }
  toast(msg, true);
  refreshStorno();                                 // Fenster bleibt offen: nochmals versuchen (gleiche Nummer)
}
