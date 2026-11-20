/* FestPOS – Bestell-App › 4. Kasse: Rückgeld, Teilen, Einzeln zahlen, Tastatur. */
"use strict";

// ---------- 4. Kasse
function openPay(id, replace = false) { openPayEntry(history_.find((h) => h.id === id), replace); }
// entry: Eintrag aus dem Verlauf oder ein offener Tisch (static/app/tabs.js, id «tab:…»)
function openPayEntry(entry, replace = false) {
  S.pay = entry;
  if (!S.pay) return;
  S.payMode = "change"; S.given = ""; S.split = 2; S.splitGiven = ""; S.splitOwn = {}; S.splitStep = null; S.personKind = null;
  S.part = { sel: {}, given: "", paid: S.pay.paid || [], step: "pick" };
  S.payShownAt = Date.now();                       // Doppeltipp auf «Senden» darf nicht «Nächste Bestellung» treffen
  if (replace) { S.view = "pay"; history.replaceState({ view: "pay" }, ""); render(); window.scrollTo(0, 0); }
  else go("pay");
}

function stateHTML() {
  const h = S.pay;
  if (h.tab) return `<div class="state" id="paystate" data-s="sent"><i></i><span>${th("app.tab.state", { count: h.tabInfo.orders, since: h.tabInfo.since })}</span></div>`;
  const txt = h.status === "sent" ? th("app.pay.arrived", { no: String(h.order_id).padStart(4, "0") })
    : h.status === "rejected" ? th("app.pay.rejected", { error: h.error || "" })
    : (S.net === "off" ? th("app.pay.offline") : th("app.pay.sending"));
  return `<div class="state" id="paystate" data-s="${h.status}"><i></i><span>${txt}</span></div>`;
}
function renderPayState() {
  if (S.view === "pay" && S.pay?.status === "rejected" && !$("#rejbox")) { renderPay(); return; }
  const el = $("#paystate"); if (el && S.pay) el.outerHTML = stateHTML();
  const fr = $("#rcptbtn"), foot = $("#payfoot");                  // Quittung erst, wenn die Bestellung angekommen ist
  if (foot && S.pay) {
    foot.classList.toggle("with-rcpt", !!footReceipt());
    if (fr) fr.outerHTML = footReceipt();
    else if (footReceipt()) foot.querySelector('[data-act="done"]')?.insertAdjacentHTML("beforebegin", footReceipt());
  }
  const sb = $("#stornobox"); if (sb && S.pay) sb.innerHTML = stornoBtn();     // erst gesendet lässt sich stornieren
  const pr = $("#payrow"); if (pr && S.pay) pr.outerHTML = payRowHTML();
}
const netOf = (h) => h.total - (h.storno || 0);
// Storno pro Position (h.cut: {Positionsnummer: Stück}, storno.js): die Kasse zeigt, was noch gilt – Storniertes nur
// als Vermerk auf dem Bon, bei «Einzeln» nicht mehr wählbar. Die Gegenbuchung im Kassenbuch bleibt (Server).
const cutOf = (i, h = S.pay) => Math.min(i.qty, h?.cut?.[i.id] || 0);
const liveQty = (i, h = S.pay) => i.qty - cutOf(i, h);

// «Einzeln» in zwei Schritten pro Person: pick (Positionen wählen) -> pay (kassieren) -> nächste Person; done, wenn
// alles bezahlt ist. Solange etwas offen ist, ist der grüne Knopf unten immer der nächste Schritt – «Nächste
// Bestellung» erscheint erst am Schluss. Der Bon ist bei «Einzeln» ausgeblendet: die Liste zeigt dieselben Positionen.
function partStep() {
  if (S.payMode !== "part" || !S.pay || S.pay.status === "rejected") return null;
  if (partState().rest <= 0) return "done";
  return S.part.step === "pay" && partSel().amount > 0 ? "pay" : "pick";
}
// Nicht angenommen (kam erst nach dem Wechsel zur Kasse): nichts gedruckt – kein Total, keine Rückgeld-Tastatur
function renderPayRejected() {
  const h = S.pay, out = new Map((h.stock || []).map((x) => [x.product_id, x]));
  const tag = (i) => { const x = out.get(i.pid); return x ? `<span class="otag">${th(x.left === 0 ? "app.stock.tag_soldout" : "app.check.only", { count: x.left })}</span>` : ""; };
  app.innerHTML = `<section class="view pay-view">
    ${header(`${esc(W().title)} ${esc(h.table)}`, { back: true })}
    <div class="pay-body">
      <div class="rejbox" id="rejbox"><h3>${th("app.payrej.title")}</h3><p class="why">${esc(h.error || "")}</p>
        <p>${th("app.payrej.text")}</p></div>
      <div class="bon" id="bon"><div class="lines">${h.items.map((i) => `<div class="line${out.has(i.pid) ? " soldline" : ""}"><span>${i.qty}× ${esc(i.name)}${tag(i)}</span><span>${chf(i.qty * i.price)}</span></div>`).join("")}</div>
        <div class="total"><span>${th("app.payrej.no_pay")}</span><b>–</b></div></div>
    </div>
    <div class="foot" id="payfoot">
      <button class="alt" data-act="payrej-drop">${th("app.rejected.drop")}</button>
      <button class="go" data-act="payrej-fix" ${rejected.some((r) => r.id === h.id) ? "" : "disabled"}>${th("app.payrej.fix")}</button>
    </div>
  </section>`;
}
function renderPay() {
  if (S.pay?.status === "rejected" && !S.tour) { renderPayRejected(); return; }
  if (S.pay?.tabMode && !S.pay.tab && !S.tour) { renderTabSent(); return; }     // Tisch bleibt offen (tabs.js)
  const h = S.pay, step = partStep(), ss = splitStep();
  const payRow = step === "pick" || step === "pay" || ss ? "" : payRowHTML();
  app.innerHTML = `<section class="view pay-view">
    ${header(`${esc(W().title)} ${esc(h.table)}`, { back: true })}
    <div class="pay-body${payRow ? " with-pay" : ""}">
      ${stateHTML()}
      ${h.test ? `<div class="banner test"><b>${th("app.test.pay_title")}</b>${th("app.test.pay_text")}</div>` : ""}
      ${step || ss === "pay" || ss === "done" ? "" : bonHTML()}
      ${!step && ss !== "pay" && h.tab && nbAllowed() ? `<button class="link nb-link" data-act="nb-tab">${ICON.plus}${th("app.nb.link")}</button>` : ""}
      ${step === "pay" || ss === "pay" ? "" : `<div class="seg" role="tablist">
        <button data-act="mode" data-m="change" aria-pressed="${S.payMode === "change"}">${th("app.pay.mode_change")}</button>
        <button data-act="mode" data-m="split" aria-pressed="${S.payMode === "split"}">${th("app.pay.mode_split")}</button>
        <button data-act="mode" data-m="part" aria-pressed="${S.payMode === "part"}">${th("app.pay.mode_part")}</button>
      </div>`}
      <div id="paypanel">${payPanel()}</div>
      <div id="stornobox">${stornoBtn()}</div>
    </div>
    ${payFootHTML()}
  </section>`;
}
function payFootHTML() {
  const step = partStep(), ss = splitStep();
  // Teilen: Aufteilen -> «Weiter: Person n kassieren», Kassieren -> «Person n hat bezahlt», dann fertig
  if (ss === "plan") {
    const P = splitPlan(), p = P.persons.find((x) => x.n === P.next);
    return `<div class="foot ez1" id="payfoot"><button class="go" data-act="split-go" ${p ? "" : "disabled"}>${p
      ? th("app.split.next", { n: p.n, cur: cur(), amount: chf(p.amount) }) : th("app.pay.all_paid")}</button></div>`;
  }
  if (ss === "pay") {
    return `<div class="foot ez2" id="payfoot"><button class="alt" data-act="split-back">${th("app.common.back")}</button>
      <button class="go" data-act="split-paid">${th("app.pay.person_paid", { n: S.splitStep })}</button></div>`;
  }
  if (ss === "done") {
    return `<div class="foot pfoot" id="payfoot"><button class="go" data-act="done">${th(S.pay?.tab ? "app.tab.paid_close" : "app.pay.next")}</button></div>`;
  }
  if (step === "pick") {
    const { amount, raw, capped } = partSel();
    const sub = capped ? `<small>${th("app.pay.over_foot", { sel: chf(round5(raw)) })}</small>` : "";
    return `<div class="foot ez1" id="payfoot"><button class="go" data-act="ez-next" ${amount ? "" : "disabled"}>${amount
      ? th("app.pay.ez_next", { cur: cur(), amount: chf(amount) }) : th("app.pay.ez_next_none")}${sub}</button></div>`;
  }
  if (step === "pay") {
    return `<div class="foot ez2" id="payfoot"><button class="alt" data-act="ez-back">${th("app.common.back")}</button>
      <button class="go" data-act="part-pay">${th("app.pay.person_paid", { n: S.part.paid.length + 1 })}</button></div>`;
  }
  // Rückgeld: deckt «Gegeben» den Betrag, hält der grüne Knopf die Zahlung fest
  const paidNow = changeCovered();
  return `<div class="foot pfoot${footReceipt() ? " with-rcpt" : ""}" id="payfoot">
      ${payRowHTML()}
      ${footReceipt()}
      <button class="go" data-act="${paidNow ? "change-paid" : "done"}">${th(S.pay?.tab ? "app.tab.paid_close" : paidNow ? "app.pay.paid_next" : "app.pay.next")}</button>
    </div>`;
}
// Rückgeld, normale Bestellung: «Gegeben» deckt den offenen Betrag (mit Trinkgeld)
const changeCovered = () => S.payMode === "change" && !!S.pay && !S.pay.tab && !S.pay.tabMode && S.pay.status !== "rejected"
  && !S.tour && partState().rest > 0 && !!S.given && parseEntry(S.given) >= partState().rest + tipAt("c");
function refreshPanel() {
  const el = $("#paypanel"); if (el) el.innerHTML = payPanel();
  const f = $("#payfoot");                           // «Weiter: CHF …», «Bezahlt · …» mitführen
  if (f && ["part", "split", "change"].includes(S.payMode)) f.outerHTML = payFootHTML();
}

// Bon oben in der Kasse – in allen drei Arten gleich (bei «Einzeln» steht die Auswahl darunter, siehe payPanel)
function bonNotes(h) {
  return `${h.storno ? `<div class="round storno">${th("app.pay.storno", { amount: chf(h.storno), before: chf(h.total) })}</div>` : ""}
    ${h.localTotal != null ? `<div class="round">${th("app.pay.price_changed", { before: chf(h.localTotal) })}</div>` : ""}
    ${tipSum(h) ? `<div class="round tipsum">${th("app.tip.total", { amount: chf(tipSum(h)) })}</div>` : ""}`;
}
// Ganz storniert: durchgestrichen, statt des Betrags «storniert»; teilweise: was noch gilt und darunter «1 storniert»
function bonLine(i, h) {
  const cut = cutOf(i, h), live = i.qty - cut;
  const opt = i.opt ? `<div class="line opt${live ? "" : " gone"}">${esc(i.opt)}</div>` : "";   // welche Variante
  if (!live) return `<div class="line gone"><span>${i.qty}× ${esc(i.name)}</span><span>${th("app.pay.cut_all")}</span></div>${opt}`;
  return `<div class="line"><span>${live}× ${esc(i.name)}${i.nb ? `<small class="nbmark"> · ${th("app.nb.marked")}</small>` : ""}</span><span>${chf(live * i.price)}</span></div>${opt}${cut ? `<div class="line cut">${th("app.pay.cut", { count: cut })}</div>` : ""}`;
}
function bonHTML() {
  const h = S.pay, net = netOf(h), cash = round5(net);
  // Teilen: nur das Total – sonst passt die Rechenhilfe samt Tastatur nicht ohne Scrollen auf den Bildschirm
  return `<div class="bon${h.test ? " test" : ""}${S.payMode === "split" ? " compact" : ""}" id="bon">
    <div class="lines">${h.items.map((i) => bonLine(i, h)).join("")}</div>
    <div class="total"><span>${th("app.pay.total", { cur: cur() })}</span><b>${chf(net)}</b></div>
    ${bonNotes(h)}
    ${cash !== net ? `<div class="round">${th("app.pay.rounded", { amount: chf(cash) })}</div>` : ""}</div>`;
}
// Einzeln: was ist schon bezahlt (Stück pro Position, von welcher Person), was ist noch offen
function partState() {
  const h = S.pay, P = S.part, paidQty = {}, paidBy = {};
  P.paid.forEach((p, n) => Object.entries(p.items || {}).forEach(([id, q]) => {
    paidQty[id] = (paidQty[id] || 0) + q;
    (paidBy[id] = paidBy[id] || []).push(n + 1);
  }));
  const paidSum = P.paid.reduce((s, p) => s + p.amount, 0);
  return { paidQty, paidBy, paidSum, rest: Math.max(0, round5(netOf(h)) - paidSum) };
}
// Auswahl der aktuellen Person: Betrag und Namen («2× Bier 5dl, Mineral»)
const itemNames = (sel) => Object.entries(sel || {}).map(([id, q]) => {
  const it = S.pay.items.find((i) => i.id === +id);
  return it ? (q > 1 ? `${q}× ${it.name}` : it.name) : "";
}).filter(Boolean).join(", ");
// Nie mehr als noch offen ist: ein eigener Betrag (Rückgeld/Teilen) ist ohne Positionen bezahlt – die Positionen sind
// dann noch «offen», kosten aber nur noch den Rest. amount: bar (auf 5 Rp.), exact: Karte/TWINT, capped: gekürzt
function partSel() {
  const sel = S.part.sel, { rest } = partState();
  const raw = Object.entries(sel).reduce((s, [id, q]) => s + q * (S.pay.items.find((i) => i.id === +id)?.price || 0), 0);
  const amount = raw >= rest ? rest : Math.min(round5(raw), rest);
  return { amount, exact: Math.min(raw, rest), raw, capped: raw > 0 && round5(raw) > amount, names: itemNames(sel) };
}
// Die Auswahl deckt den Rest (genau oder mehr): weitere Positionen kosten nichts mehr und sind nicht mehr wählbar
function partCovered() {
  const { rest } = partState();
  return rest > 0 && Object.keys(S.part.sel).length > 0 && partSel().amount >= rest;
}
// Auswahl ist mehr als noch offen (vorher ohne Positionen bezahlt: Teilzahlung, eigener Betrag, Teilen) – die Rechnung
// gut sichtbar zeigen: Gewählt − schon bezahlt = Rest
const freePaid = () => S.part.paid.map((p, i) => (Object.keys(p.items || {}).length ? 0 : i + 1)).filter(Boolean);
function capHint(person) {
  const { raw, amount, capped } = partSel();
  if (!capped) return "";
  const who = freePaid().map((n) => t("app.pay.person_short", { n })).join(", ");
  return `<div class="ez-over" role="note">
    <div class="r"><span>${th("app.pay.over_sel")}</span><b>${chf(round5(raw))}</b></div>
    <div class="r"><span>${who ? th("app.pay.over_paid_by", { who }) : th("app.pay.over_paid")}</span><b>−${chf(round5(raw) - amount)}</b></div>
    <div class="r sum"><span>${th("app.pay.over_rest", { n: person })}</span><b>${chf(amount)}</b></div></div>`;
}
// Gegeben | Rückgeld in EINER Zeile mit fester Höhe: die Tastatur darunter bleibt immer am selben Ort.
// Zu wenig gegeben: «Fehlt noch» rot, sonst das Rückgeld grün (in allen Arten gleich).
function calcRow(due, givenStr) {
  let cls = "none", label = t("app.pay.change"), out = "–";
  if (givenStr && due > 0) {
    const diff = parseEntry(givenStr) - due;
    if (diff >= 0) { cls = "good"; out = chf(diff); }
    else { cls = "short"; label = t("app.pay.missing"); out = chf(-diff); }
  }
  return `<div class="calc"><div class="c-given" data-act="to-pad" role="button" tabindex="0"><span>${th("app.pay.given")}</span><output>${esc(givenStr || "")}</output></div>
    <div class="c-out ${cls}"><span>${esc(label)}</span><b>${out}</b></div></div>`;
}

function quickAmounts(due) {
  const out = new Set();
  for (const step of [500, 1000, 2000, 5000, 10000, 20000]) {
    const v = Math.ceil(due / step) * step;
    if (v > due) out.add(v);
    if (out.size >= 4) break;
  }
  return [...out].sort((a, b) => a - b).slice(0, 4);
}
function keypad(target) {
  return `<div class="pad small">
    ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-act="key" data-k="${n}" data-t="${target}">${n}</button>`).join("")}
    <button class="k-fn" data-act="key" data-k="." data-t="${target}">.</button>
    <button data-act="key" data-k="0" data-t="${target}">0</button>
    <button class="k-fn" data-act="key" data-k="del" data-t="${target}" aria-label="${th("app.common.delete")}">${ICON.del}</button>
  </div>`;
}
// Bezahlt / noch offen und wer schon bezahlt hat – gleich in Rückgeld, Teilen und Einzeln (Teilzahlungen gehören zur
// Bestellung, nicht zur Art). Antippen: Quittung (nur mit Positionen) oder letzte Zahlung rückgängig.
function partProg(cur = "") {
  const h = S.pay, P = S.part, { rest } = partState(), total = round5(netOf(h)), paid = total - rest;
  const chips = P.paid.map((p, i) => (p.person
    ? `<button class="pc done" data-act="ez-person" data-n="${i}">${th("app.split.chip", { n: p.person, amount: chf(p.amount), kind: kindShort(p) })}</button>`
    : Object.keys(p.items || {}).length
    ? `<button class="pc done" data-act="ez-person" data-n="${i}">${th("app.pay.ez_chip_done", { n: i + 1, amount: chf(p.amount) })}</button>`
    : `<button class="pc done part" data-act="ez-person" data-n="${i}">${th("app.pay.ez_chip_part", { n: i + 1, amount: chf(p.amount) })}</button>`)).join("") + cur;
  return `<div class="ez-prog" id="partsum">
    <div class="row"><span>${th("app.pay.ez_paid")} <b class="${paid ? "ok" : ""}">${chf(paid)}</b></span><span>${th("app.pay.open")} <b>${chf(rest)}</b></span></div>
    <div class="ez-bar"><i style="width:${total ? Math.round(100 * paid / total) : 0}%"></i></div>
    <div class="pchips">${chips}</div>${bonNotes(h)}</div>`;
}
// Teilen, eigener Betrag (lange drücken seit 16.11.): eine offene Person übernimmt einen festen Betrag, der Rest verteilt sich auf
// die übrigen offenen Personen (jede mindestens 5 Rp.). Hier auch «Jetzt kassieren» für genau diese Person.
function ownSheet() {
  const n = S.ownFor, P = splitPlan(), v = parseEntry(S.ownGiven || ""), others = P.open.filter((x) => x !== n);
  const ok = others.length > 0 && v > 0 && v <= P.rest - 5 * others.length;
  const after = ok ? splitPlan({ ...S.splitOwn, [n]: v }) : null;
  const other = after?.persons.find((p) => others.includes(p.n) && !p.own);
  return `<div id="ownsheet"><h2>${th("app.split.own_title", { n })}</h2>
    <div class="calc"><div class="c-given"><span>${th("app.pay.own_amount")}</span><output>${esc(S.ownGiven || "")}</output></div>
      <div class="c-out ${ok ? "good" : "none"}"><span>${th("app.split.rest_each")}</span><b>${other ? chf(other.amount) : "–"}</b></div></div>
    ${keypad("own")}
    <div class="actions"><button class="go" data-act="own-pay" ${ok ? "" : "disabled"}>${ok ? th("app.split.own_ok", { n, amount: chf(v) }) : th("app.pay.own_enter")}</button>
      ${S.splitOwn?.[n] ? `<button class="alt" data-act="own-clear">${th("app.split.own_clear")}</button>` : ""}
      <button class="alt" data-act="split-start" data-n="${n}">${th("app.split.cash_now", { n })}</button>
      <button class="alt" data-act="close-sheet">${th("app.common.cancel")}</button></div></div>`;
}
// Rückgeld: zu wenig gegeben -> als Teilzahlung buchen, der Rest bleibt offen (z.B. «ich zahle 20, du den Rest»)
function partBtn(due) {
  const g = parseEntry(S.given || "");
  return g > 0 && g < due
    ? `<button class="alt part-btn" data-act="part-amount">${th("app.pay.part_amount", { amount: chf(g), rest: chf(due - g) })}</button>` : "";
}
// ---------- Teilen pro Person: wie Einzeln in zwei Schritten – aufteilen, dann jede Person kassieren
// Personen haben feste Nummern; bezahlt = Eintrag in S.part.paid mit «person» (Betrag, Zahlart, Quittung). Offene
// Personen teilen den Rest auf 5 Rp. (der höhere Betrag zuerst), ausser sie haben einen eigenen Betrag (S.splitOwn).
// Andere Teilzahlungen (Rückgeld, Einzeln) verkleinern nur den Rest.
function splitPlan(ownIn = S.splitOwn || {}) {
  const { rest } = partState(), paid = S.part.paid.filter((p) => p.person);
  const paidNo = new Set(paid.map((p) => p.person)), maxPaid = Math.max(0, ...paidNo);
  const min = Math.max(paidNo.size ? 1 : 2, maxPaid, paidNo.size + (rest > 0 ? 1 : 0));
  const N = Math.min(30, Math.max(S.split || 2, min));
  const open = [];
  for (let n = 1; n <= N; n++) if (!paidNo.has(n)) open.push(n);
  const own = {};
  let fixed = 0;
  for (const n of open) {
    const v = ownIn[n];
    if (v > 0 && fixed + v + 5 * (open.length - Object.keys(own).length - 1) <= rest) { own[n] = v; fixed += v; }
  }
  let free = open.filter((n) => !(n in own));
  if (!free.length && open.length) { const last = open.at(-1); fixed -= own[last]; delete own[last]; free = [last]; }
  const left = Math.max(0, rest - fixed), base = free.length ? Math.floor(left / free.length / 5) * 5 : 0;
  let r = left - base * free.length;
  const amt = {};
  for (const n of free) { const add = Math.min(5, r); amt[n] = base + add; r -= add; }
  const persons = [];
  for (let n = 1; n <= N; n++) {
    const e = paid.find((p) => p.person === n);
    persons.push(e ? { n, amount: e.amount, paid: e } : { n, amount: own[n] ?? amt[n] ?? 0, own: n in own });
  }
  return { N, min, rest, persons, open, next: open[0] || null };
}
function splitStep() {
  if (S.payMode !== "split" || !S.pay || S.pay.status === "rejected" || (S.pay.tabMode && !S.pay.tab)) return null;
  if (partState().rest <= 0 && S.part.paid.length) return "done";
  return S.splitStep && splitPlan().open.includes(S.splitStep) ? "pay" : "plan";
}
// Zahlart einer Person (kurz für Chip und Liste); ohne eigene Zahlart wie die Bestellung
const kindOf = (p) => p?.payment || S.pay?.payment || null;
const kindShort = (p) => (kindOf(p) ? payLabel(kindOf(p)) : t("app.pay.paid"));
// Zahlart pro Person (Teilen und Einzeln) – nur wenn die Zahlart eingeschaltet ist
function kindRow(n) {
  if (payModeCfg() === "off") return "";
  const need = payModeCfg() === "required" && !S.personKind;
  return `<div class="payrow person-kind${need ? " need" : ""}" id="personkind"><span>${th("app.split.kind", { n })}</span>
    <div class="seg">${PAY_KINDS.map(([v, k]) => `<button data-act="person-kind" data-v="${v}" aria-pressed="${S.personKind === v}">${th(k)}</button>`).join("")}</div></div>`;
}
function splitReceiptBtn(p) {
  if (!S.boot?.receipt || S.tour || S.pay?.status !== "sent") return "";
  return `<button class="link sm rcpt-l" data-act="receipt-split" data-n="${p.person}">${ICON.receipt}${th(p.receipt ? "app.receipt.copy_short" : "app.receipt.short")}</button>`;
}
function splitPanel() {
  const P = splitPlan(), ss = splitStep(), prog = S.part.paid.length ? partProg() : "";
  if (ss === "done") {
    const rows = S.part.paid.map((p, i) => `<div class="r" data-act="ez-person" data-n="${i}" role="button" tabindex="0">
      <span>${p.person ? th("app.pay.person", { n: p.person }) : th("app.split.part_payment")} · ${chf(p.amount)}<small>${esc(kindShort(p))}</small></span>
      ${p.person ? splitReceiptBtn(p) : ""}</div>`).join("");
    return `${prog}<div class="panel" id="splitdone"><div class="big-out good"><span>${th("app.pay.all_paid")}</span><b>✓</b></div>
      <div class="ez-sum">${rows}</div><p class="muted small">${th("app.split.tap_person")}</p></div>`;
  }
  if (ss === "pay") {
    const n = S.splitStep, p = P.persons.find((x) => x.n === n), tk = `s${n}`, due = p.amount + tipAt(tk);
    const qchips = [due, ...quickAmounts(due)].map((v, k) => `<button class="chip" data-act="split-given" data-v="${v}" aria-pressed="${parseEntry(S.splitGiven) === v}">${k ? v / 100 : th("app.pay.exact")}</button>`).join("");
    return `${prog}<div class="panel" id="splitpay">
      <div class="prow"><div><strong>${th("app.pay.person", { n })}</strong><small>${th("app.split.share_of", { n, count: P.N })}</small></div><b>${chf(p.amount)}</b></div>
      ${kindRow(n)}
      ${calcRow(due, S.splitGiven)}
      ${tipLine(tk)}
      <div class="quick">${qchips}</div>
      ${keypad("sgiven")}
      ${tapBtn("payrexx-split", t("app.payrexx.person_btn", { n }), `data-v="${p.amount}"`)}
      ${tipBtn(tk)}
    </div>`;
  }
  const rows = P.persons.map((p) => (p.paid
    ? `<div class="pi paid" data-act="split-person" data-n="${p.n}" role="button" tabindex="0"><span class="nm">${th("app.pay.person", { n: p.n })}</span>
        <span class="st"><span class="tag">✓ ${esc(kindShort(p.paid))}</span></span><span class="mark"></span><span class="pr">${chf(p.amount)}</span></div>`
    : `<div class="pi${p.n === P.next ? " sel" : ""}" data-act="split-own" data-n="${p.n}" role="button" tabindex="0"><span class="nm">${th("app.pay.person", { n: p.n })}${p.own ? ` <small>${th("app.split.own_tag")}</small>` : ""}</span>
        <span class="st"><span>${th("app.split.open")}</span></span><span class="mark"></span><span class="pr">${chf(p.amount)}</span></div>`)).join("");
  const sum = S.part.paid.length ? th("app.split.still_open") : th("app.split.together");
  return `${prog}<div class="panel split" id="splitplan">
    <div class="split-row">
      <div class="stepper">
        <button data-act="split" data-d="-1" ${P.N <= P.min ? "disabled" : ""} aria-label="${th("app.pay.split_less")}">−</button>
        <output>${P.N}<small>${th("app.pay.split_people")}</small></output>
        <button data-act="split" data-d="1" ${P.N >= 30 ? "disabled" : ""} aria-label="${th("app.pay.split_more")}">+</button>
      </div>
      <div class="split-res num">${sum}<br><b>${chf(P.rest)}</b></div>
    </div>
    <div class="pick split-list">${rows}</div>
    <p class="muted small">${th(P.open.length > 1 ? "app.split.hint_tap" : "app.split.hint_one")}</p>
  </div>`;
}
function payPanel() {
  const h = S.pay;
  const { rest } = partState();
  const due = rest;                                   // ohne Teilzahlungen = ganzes Total (gerundet)
  const restHint = S.part.paid.length ? partProg() : "";
  if (S.payMode === "change") {
    const dueT = due + tipAt("c");                    // mit Trinkgeld: so viel muss der Gast geben
    return `<div class="panel">
      ${restHint}
      ${calcRow(dueT, S.given)}
      ${tipLine("c")}
      <div class="quick">
        <button class="chip" data-act="given" data-v="${dueT}">${th("app.pay.exact")}</button>
        ${quickAmounts(dueT).map((v) => `<button class="chip" data-act="given" data-v="${v}">${v / 100}</button>`).join("")}
      </div>
      ${keypad("given")}
      ${partBtn(due)}
      ${tipBtn("c")}
    </div>`;
  }
  if (S.payMode === "split") return splitPanel();
  // Einzeln: oben bezahlt / noch offen und wer schon bezahlt hat (antippen: Quittung, rückgängig), darunter der Schritt
  const P = S.part, step = partStep(), { paidQty, paidBy } = partState();
  const person = P.paid.length + 1, total = round5(netOf(h)), paid = total - rest;
  const { amount: amt, names } = partSel();
  const prog = partProg(step === "done" ? "" : `<span class="pc cur">${step === "pay" ? th("app.pay.ez_chip_pay", { n: person, amount: chf(amt) }) : th("app.pay.person", { n: person })}</span>`);
  if (step === "done") {
    const last = P.paid.length - 1;
    return `${prog}<div class="panel" id="partdone"><div class="big-out good"><span>${th("app.pay.all_paid")}</span><b>✓</b></div>
      <div class="ez-sum">${P.paid.map((p, i) => `<div class="r" data-act="ez-person" data-n="${i}" role="button" tabindex="0"><span>${th("app.pay.person", { n: i + 1 })} · ${chf(p.amount)}<small>${esc([itemNames(p.items), p.payment ? payLabel(p.payment) : ""].filter(Boolean).join(" · "))}</small></span>${partReceiptBtn(p, i)}</div>`).join("")}</div>
      ${last >= 0 ? `<p class="muted small">${th("app.split.tap_person")}</p>` : ""}</div>`;
  }
  if (step === "pay") {
    const tk = `p${person}`, amtT = amt + tipAt(tk);
    const qchips = [amtT, ...quickAmounts(amtT)].map((v, k) => `<button class="chip" data-act="part-given" data-v="${v}" aria-pressed="${parseEntry(P.given) === v}">${k ? v / 100 : th("app.pay.exact")}</button>`).join("");
    return `${prog}<div class="panel" id="partpay">
      <div class="prow"><div><strong>${th("app.pay.person", { n: person })}</strong><small>${esc(names)}</small></div><b>${chf(amt)}</b></div>
      ${kindRow(person)}
      ${capHint(person)}
      ${calcRow(amtT, P.given)}
      ${tipLine(tk)}
      <div class="quick">${qchips}</div>
      ${keypad("pgiven")}
      ${tapBtn("payrexx-part", t("app.payrexx.person_btn", { n: person }))}
      ${tipBtn(tk)}
    </div>`;
  }
  const isPaid = (i) => (liveQty(i) - (paidQty[i.id] || 0) <= 0 ? 1 : 0);
  // Rest schon gedeckt (Auswahl reicht genau oder mehr, vorher ohne Positionen bezahlt): übrige offene Positionen
  // kosten nichts mehr und lassen sich nicht mehr wählen (T25: schon bei Rest 0, nicht erst darunter)
  const covered = partCovered();
  // ganz Storniertes fehlt (ausser es hat schon jemand bezahlt), ganz Bezahltes ans Ende
  const rows = h.items.filter((i) => liveQty(i) > 0 || paidQty[i.id]).sort((a, b) => isPaid(a) - isPaid(b)).map((i) => {
    const qty = liveQty(i), pd = paidQty[i.id] || 0, sel = P.sel[i.id] || 0, open = qty - pd, cov = covered && !sel && open > 0;
    const state = open <= 0 ? t("app.pay.paid") : cov ? t("app.pay.covered") : t("app.pay.open_of", { open, qty });
    const tags = [...new Set(paidBy[i.id] || [])].map((n) => `<span class="tag">${th("app.pay.person_short", { n })}</span>`).join("");
    // feste Höhe: Zeile 1 Produkt (bricht nur um, wenn der Name zu lang ist), Zeile 2 Stand | − Anzahl | Preis
    return `<div class="pi${sel ? " sel" : ""}${open <= 0 ? " paid" : ""}${cov ? " cov" : ""}" ${cov ? 'aria-disabled="true"' : `data-act="pick" role="button" tabindex="0"`} data-id="${i.id}" data-full="${open - sel <= 0 ? 1 : 0}">
      <span class="nm">${esc(i.name)}${i.opt ? ` <small>${esc(i.opt)}</small>` : ""}</span>
      <span class="st">${tags}<span>${esc(state)}</span></span>
      <span class="mark">${sel ? `<b class="badge">${sel}×</b><button class="minus" data-act="unpick" data-id="${i.id}" aria-label="${th("app.pay.unpick")}">−</button>` : ""}</span>
      <span class="pr">${chf(i.price)}</span></div>`;
  }).join("");
  const allSelected = amt >= rest, anySelected = Object.keys(P.sel).length > 0;
  return `${prog}<div class="panel" id="pickpanel">
    <div class="ez-q">${th("app.pay.ez_question", { n: person })}<small>${th("app.pay.ez_hint")}</small></div>
    <div class="ez-btns"><button class="alt ez-all" data-act="ez-none" ${anySelected ? "" : "disabled"}>${th("app.pay.ez_none")}</button>
      <button class="alt ez-all" data-act="ez-all" ${allSelected ? "disabled" : ""}>${ICON.checkall}${th("app.pay.ez_all")}</button></div>
    ${capHint(person)}
    <div class="pick">${rows}</div>
    ${h.tab && nbAllowed() ? `<button class="link nb-link" data-act="nb-tab">${ICON.plus}${th("app.nb.link")}</button>` : ""}
  </div>`;
}
