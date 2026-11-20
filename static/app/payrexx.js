/* FestPOS – Bestell-App: Karte/TWINT mit NFC am Handy über Payrexx (T13).
   PAYREXX-UNGETESTET: noch nie gegen ein echtes Payrexx-Konto gelaufen (PAYREXX_GEPRUEFT in app/payrexx_api.py,
   docs/testplan-payrexx.md). Die Bestell-App startet die Begleit-App
   «FestPOS Tap» (fully.startIntent), die ruft die Payrexx-App auf: Gast hält Karte oder Handy an das XCover. Das
   Ergebnis meldet «FestPOS Tap» an den Pi, hier erscheint «Bezahlt ✓». Kam es nicht an (Verbindung weg), bleibt die
   Zahlung offen; zeigte die Payrexx-App «bezahlt», bestätigt die Bedienung von Hand. Übung: simuliert.
   Wege: ganze Bestellung (Zeile «Zahlung»), Einzeln pro Person, Teilen pro Person. */
"use strict";
let TAP = null;         // { id, key, amount, tip, part, onPaid, launch, timer, last, started }
const tapOn = () => !!S.boot?.payrexx && !!S.pay && S.pay.status === "sent" && !S.tour && !(S.pay.tabMode && !S.pay.tab);
const tapKey = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

function tapBtn(act, label, extra = "") {
  return tapOn() ? `<button class="alt tappay" data-act="${act}" ${extra}>${esc(label)}</button>` : "";
}
function tapLaunch(url) {
  if (!url) return;
  try {
    if (window.fully && typeof window.fully.startIntent === "function") window.fully.startIntent(url);
    else location.href = url;                          // Chrome: intent:// öffnet die App ebenfalls
  } catch { toast(t("app.payrexx.no_app"), true); }
}
async function openTap(amount, part, onPaid, tip = 0) {            // tip: davon Trinkgeld (steht im Payrexx-Betrag)
  if (!tapOn() || amount <= 0) return;
  stopTap();
  TAP = { amount, tip, part, onPaid, key: tapKey(), last: null, started: Date.now() };
  openSheet(tapSheet({ status: "loading" }), { outside: "warn" });
  let r, j = {};
  try {
    const who = S.pay.tab ? { tab_id: S.pay.tab } : { client_uuid: S.pay.id };     // offener Tisch (tabs.js)
    r = await api("/api/phone/payrexx", { method: "POST", body: JSON.stringify({ ...who, amount_cents: amount,
      tip_cents: tip, part, key: TAP.key }), timeout: 15000 });
    j = await r.json().catch(() => ({}));
  } catch { return tapShow({ status: "error", error: t("app.payrexx.no_server") }); }
  if (!r.ok) return tapShow({ status: "error", error: typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: r.status }) });
  TAP.id = j.id;
  TAP.launch = j.launch;
  tapShow(j);
  if (j.autostart) tapLaunch(j.launch);                // T44: nur eine eben angelegte Zahlung startet von selbst
  TAP.timer = setInterval(tapPoll, 2000);
}
function stopTap() { if (TAP?.timer) clearInterval(TAP.timer); }
async function tapPoll() {
  if (!TAP?.id || !document.querySelector("#tapsheet")) { stopTap(); return; }
  try {
    const r = await api(`/api/phone/payrexx/${TAP.id}`, { timeout: 10000 });
    if (r.ok) tapShow(await r.json());
  } catch { /* Pi kurz weg: weiter versuchen */ }
}
function tapShow(p) {
  if (!TAP) return;
  TAP.last = p;
  const box = document.querySelector("#tapsheet");
  if (!box) { stopTap(); return; }
  box.outerHTML = tapSheet(p);
  if (p.double && !TAP.doubleShown) {                  // T44: zweite Belastung gemeldet – zurückerstatten
    TAP.doubleShown = true;
    toast(t("app.payrexx.double"), true);
    buzz([200, 100, 200]);
  }
  if (p.status === "confirmed") {
    stopTap();
    buzz([30, 40, 30]);
    const done = TAP.onPaid, id = TAP.id;
    setTimeout(() => { closeSheet(); TAP = null; done?.(id); }, 1200);
  } else if (p.status === "cancelled") stopTap();
}
function tapSheet(p) {
  const st = p.status, test = p.test || S.pay?.test, late = TAP && Date.now() - TAP.started > 15000;
  const msg = st === "loading" ? th("app.payrexx.loading")
    : st === "confirmed" ? `<div class="big-out good"><span>${th("app.payrexx.paid")}</span><b>✓</b></div>`
    : st === "failed" ? `<p class="err">${th("app.payrexx.failed")}${p.error ? ` <small>(${esc(p.error)})</small>` : ""}</p>`
    : st === "cancelled" ? `<p class="muted">${th("app.payrexx.cancelled")}</p>`
    : st === "error" ? `<p class="err">${esc(p.error || "")}</p><p class="muted">${th("app.payrexx.fallback")}</p>`
    : p.error === "unclear" ? `<p class="err">${th("app.payrexx.unclear")}</p>`        // T39: Erfolg ohne Betrag
    : `<p class="muted">${th(test ? "app.payrexx.test" : "app.payrexx.waiting")}</p>`;
  const open = st === "waiting" || st === "failed";
  return `<div id="tapsheet"><h2>${th("app.payrexx.title", { cur: cur(), amount: chf(TAP?.amount || p.amount || 0) })}</h2>
    ${TAP?.tip ? `<p class="muted">${th("app.tip.in_payrexx", { amount: chf(TAP.tip) })}</p>` : ""}
    ${test ? `<div class="banner test"><b>${th("app.test.pay_title")}</b>${th("app.payrexx.test")}</div>` : ""}
    ${msg}
    <div class="actions">
      ${open && !test && p.error !== "unclear" ? `<button class="go" data-act="payrexx-again">${th("app.payrexx.again")}</button>` : ""}
      ${open && !test && (late || p.error === "unclear") ? `<button class="alt" data-act="payrexx-manual">${th("app.payrexx.manual")}</button>` : ""}
      ${open || st === "loading" ? `<button class="alt" data-act="payrexx-cancel">${th("app.payrexx.cancel")}</button>`
        : st === "confirmed" ? "" : `<button class="alt" data-act="close-sheet">${th("app.payrexx.close")}</button>`}</div></div>`;
}
function tapAgain() { if (TAP?.launch) { TAP.started = Date.now(); tapLaunch(TAP.launch); } }
async function tapManual() {
  if (!TAP?.id || !confirm(t("app.payrexx.manual_q"))) return;
  try {
    const r = await api(`/api/phone/payrexx/${TAP.id}/manual`, { method: "POST", timeout: 10000 });
    const j = await r.json().catch(() => ({}));
    if (r.ok) tapShow(j);
    else toast(typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: r.status }), true);   // T39: kein Recht
  } catch { toast(t("app.payrexx.no_server"), true); }
}
async function cancelTap() {
  if (!TAP) { closeSheet(); return; }
  if (!TAP.id) { stopTap(); TAP = null; closeSheet(); return; }
  if (!confirm(t("app.payrexx.cancel_q"))) return;
  try {
    const r = await api(`/api/phone/payrexx/${TAP.id}/cancel`, { method: "POST", timeout: 10000 });
    const j = await r.json().catch(() => ({}));
    if (j.status === "confirmed") return tapShow(j);                // in der Zwischenzeit doch bezahlt
    if (r.status === 403) return toast(typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: 403 }), true);
  } catch { /* egal: offen bleibt offen */ }
  stopTap(); TAP = null; closeSheet();
}
// Ganze Bestellung: Zahlart «Karte/TWINT (NFC)», sobald bezahlt (der Server setzt sie auch selbst)
function tapWhole() {
  const h = S.pay, tip = tipOf("c")?.x ? 0 : tipAt("c");
  const due = h.tab ? Math.max(0, netOf(h) - partState().paidSum) : netOf(h);   // offener Tisch: nur was noch offen ist
  openTap(due + tip, "all", () => {
    if (tip) setTip("c", tip, true);
    updateHist(h.id, { payment: "payrexx" });
    const row = $("#payrow"); if (row) row.outerHTML = payRowHTML();
    if (tip) refreshPanel();                                       // «inkl. Trinkgeld … (Karte/TWINT)»
    toast(t("app.payrexx.done_order"));
  }, tip);
}
// Einzeln: die gewählte Person zahlt mit NFC -> wie «Person n hat bezahlt»
function tapPerson() {
  const { exact: amount } = partSel(), n = S.part.paid.length + 1, tip = tipAt(`p${n}`);
  openTap(amount + tip, `p${n}`, (id) => {
    if (tip) setTip(`p${n}`, tip, true);
    S.personKind = "payrexx"; partPay(); const last = S.part.paid.at(-1);
    if (last) { last.payrexx = id; if (S.pay.tab) last.key = `payrexx-${id}`; savePaid(); }   // Tisch: bucht der Server
  }, tip);
}
// Teilen: die Person, die gerade kassiert wird, zahlt mit NFC -> wie «Person n hat bezahlt» (Zahlart Payrexx)
function tapSplit(amount) {
  const n = S.splitStep, tk = `s${n}`, tip = tipAt(tk);
  if (!n) return;
  openTap(amount + tip, tk, (id) => {
    if (tip) setTip(tk, tip, true);
    S.personKind = "payrexx";
    splitBook({ payrexx: id });
    toast(t("app.payrexx.done_split", { n }));
  }, tip);
}
