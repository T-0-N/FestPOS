/* FestPOS – Bestell-App › 3. Senden (mit Bestandsprüfung vor dem Kassieren). */
"use strict";

// ---------- 3. Senden
// Vor dem Kassieren auf den Server warten (Verwaltung › Einstellungen › Kasse, app/stock.py check_config): «Senden»
// schickt die Bestellung selbst und wartet höchstens wait Sekunden. Angenommen -> Kasse. Ausverkauft / nicht genug ->
// die Bedienung bleibt im Warenkorb, die Positionen sind markiert (nichts gedruckt, nichts zu kassieren). Keine Antwort,
// offline oder ausgeschaltet -> wie bisher über den Ausgang zur Kasse (gleiche Nummer: der Server nimmt sie nur einmal).
const checkCfg = () => S.boot?.stock_check || { mode: "always", wait: 2 };
function waitForServer(lines) {
  const c = checkCfg();
  if (c.mode === "off" || S.net === "off" || !navigator.onLine) return false;
  return c.mode === "always" || lines.some((l) => l.p.stock != null);
}
// Was im Warenkorb nicht geht: laut Handy (grau, Restmenge) und laut letzter Antwort des Servers
function cartProblems() {
  if (S.tour) return [];
  const need = {};
  cartLines().forEach((l) => { need[l.p.id] = (need[l.p.id] || 0) + l.q; });
  const out = [];
  for (const [k, q] of Object.entries(need)) {
    const p = productById(+k), sp = S.check?.problems?.find((x) => x.product_id === +k);
    let left = soldOut(p) ? 0 : p.stock ?? Infinity;
    if (sp) left = Math.min(left, sp.left);
    if (q > left) out.push({ product_id: +k, name: p.name, left, q });
  }
  return out.sort((a, b) => (a.left > 0) - (b.left > 0) || a.name.localeCompare(b.name));   // wie der Server
}
// «Automatisch anpassen»: Ausverkauftes weg, Mengen auf die Restmenge kürzen (zuletzt hinzugefügte Positionen zuerst)
function autoAdjust() {
  const done = [];
  for (const pr of cartProblems()) {
    let cut = pr.q - pr.left;
    for (const k of Object.keys(S.cart).filter((x) => S.cart[x].pid === pr.product_id).reverse()) {
      if (cut <= 0) break;
      const l = S.cart[k], d = Math.min(l.q, cut);
      l.q -= d; cut -= d;
      if (!l.q) delete S.cart[k];
    }
    done.push(pr.left === 0 ? t("app.check.removed", { count: pr.q, name: pr.name })
      : t("app.check.reduced", { name: pr.name, from: pr.q, to: pr.left }));
  }
  saveCart();
  return done.join(", ");
}
function checkFix() {
  const txt = autoAdjust();
  S.check = txt ? { problems: [], adjusted: txt } : null;
  buzz(15);
  if (cartCount()) openCart(); else { S.check = null; closeSheet(); }
  VIEWS[S.view]();
}
function checkBoxHTML(probs) {
  if (!S.check && !probs.length) return "";
  if (probs.length) return `<div class="chkbox"><b>${th("app.check.title")}</b>
    ${S.check?.msg ? `<p class="why">${esc(S.check.msg)}</p>` : ""}<p>${th("app.check.text")}</p>
    <button class="alt" data-act="check-fix">${th("app.check.fix")}</button></div>`;
  if (S.check?.adjusted) return `<div class="chkbox okd"><b>${th("app.check.adjusted")}</b><p>${esc(S.check.adjusted)}</p></div>`;
  if (S.check?.msg) return `<div class="chkbox"><b>${th("app.check.title")}</b><p class="why">${esc(S.check.msg)}</p></div>`;
  return "";
}
function setSending(on) {
  document.querySelectorAll('.sheet [data-act="send"], #send').forEach((b) => {
    if (on) { b.dataset.label = b.textContent; b.textContent = t("app.check.sending"); b.classList.add("sendwait"); b.disabled = true; }
    else if (b.dataset.label) { b.textContent = b.dataset.label; b.classList.remove("sendwait"); b.disabled = false; }
  });
}
function orderBody(id, lines) {
  const body = { client_uuid: id, table: S.table, device: S.device || null,
    lines: lines.map((l) => ({ product_id: l.p.id, qty: l.q, options: l.opts, note: l.note || null, when: l.when || null })) };
  if (S.test) body.test = true;
  if (nbOn()) body.no_print = true;
  return body;
}

// ---------- Nachbuchen ohne Bon (schon geliefert, app/orders.py no_print): keine Bons, sonst wie jede Bestellung.
// Einstieg: Link im Warenkorb (S.nb = { table }) oder beim Abrechnen eines offenen Tisches (S.nb = { table, tab }).
// Gilt nur für diesen einen Vorgang: nach dem Senden, beim Tischwechsel und beim Verlassen der Bestellung wieder aus.
const nbAllowed = () => !!S.boot?.can_nachbuchen && !S.tour;
const nbOn = () => !!S.nb && !S.tour;
function nbBandHTML() {
  return nbOn() ? `<div class="nb-band" role="status"><span><b>${th("app.nb.band_title")}</b>${th("app.nb.band_text")}</span>
    <button class="link" data-act="nb-cancel">${th("app.common.cancel")}</button></div>` : "";
}
// Beim Abrechnen eines offenen Tisches: Produkte wählen, danach zurück zur Abrechnung
function nbFromTab() {
  const h = S.pay;
  if (!h?.tab || !nbAllowed()) return;
  if (cartCount() > 0) { toast(t("app.nb.cart_busy"), true); return; }
  // T35: auch aus «Einzeln» – danach zurück in denselben Modus, die Auswahl bleibt
  S.nb = { table: h.table, tab: h.tab, mode: S.payMode, sel: { ...(S.part?.sel || {}) } };
  tabOrder(h.table);
}
function nbCancel() {
  if (!S.nb) return;
  if (S.nb.tab) { S.cart = {}; saveCart(); S.check = null; closeSheet(); history.back(); return; }   // zurück zur Abrechnung
  S.nb = null;
  closeSheet();
  render();
}
// Beim Verlassen der Bestellung (Zurück, anderer Tisch): Modus aus; vom offenen Tisch aus Gewähltes verwerfen
function nbLeave() {
  if (S.nb?.tab) { S.cart = {}; saveCart(); }
  S.nb = null;
}
const nbItems = (lines) => lines.map((l) => `${l.q}× ${l.p.name}`).join(", ");
function addHist(id, lines, extra) {
  const items = lines.map((l, i) => ({ id: i + 1, pid: l.p.id, name: l.p.name, opt: optText(l), price: l.p.price_cents, qty: l.q }));
  const total = items.reduce((s, i) => s + i.price * i.qty, 0);
  const tabMode = tabsOn() || undefined;             // Tisch bleibt offen (static/app/tabs.js)
  history_ = trimHist([{ id, table: S.table, items, total, ts: Date.now(), test: S.test || undefined, tabMode, ...extra }, ...history_]);
  store.set("history", history_);
}
let sending = false;
async function send() {
  if (S.tour || sending) return;                   // Einführung: nie wirklich senden; Doppeltipp während des Wartens
  const lines = cartLines();
  if (!lines.length) return;
  if (!S.table) {                                  // ohne Tisch lehnt der Server ab – gar nicht erst senden
    closeSheet();
    toast(t("app.table.choose_first", { word: W().one }), true);
    go("table");
    return;
  }
  // ausverkauft oder nicht mehr genug (laut Handy): gar nicht erst senden
  if (cartProblems().length) { S.check = { problems: S.check?.problems || [] }; buzz([60, 40, 60]); openCart(); return; }
  const id = uuid(), body = orderBody(id, lines);
  if (waitForServer(lines)) {
    sending = true;
    setSending(true);
    let r = null;
    try { r = await api("/api/orders", { method: "POST", body: JSON.stringify(body), timeout: checkCfg().wait * 1000 }); }
    catch { r = null; }
    sending = false;
    setSending(false);
    if (r?.status === 422) {                       // abgelehnt: nichts gedruckt – im Warenkorb anpassen
      let j = {};
      try { j = await r.json(); } catch { /* egal */ }
      if (j.stock) applyStock(j.stock);
      S.check = { problems: j.stock_problems || [], msg: typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: 422 }) };
      document.querySelectorAll(".toast").forEach((e) => e.remove());   // der rote Kasten im Warenkorb sagt es
      buzz([80, 60, 80]);
      openCart();
      return;
    }
    if (r?.ok) {
      let j = null;
      try { j = await r.json(); } catch { j = null; }
      if (j) {
        const nb = S.nb;
        addHist(id, lines, { status: "sent", order_id: j.order_id, warnings: j.warnings || [], nb: nb ? true : undefined });
        serverTotal(id, j.total_cents);
        if (tabsOn()) loadTabs(true);
        S.cart = {}; saveCart(); S.check = null; S.nb = null;
        closeSheet();
        buzz(40);
        if (nb?.tab) {                             // zurück zur Abrechnung (über Zurück: kein doppelter Eintrag)
          S.nbReturn = nb.tab; S.nbRestore = { tab: nb.tab, mode: nb.mode, sel: nb.sel }; history.back();
          toast(t("app.nb.done", { items: nbItems(lines) }));
          return;
        }
        openPay(id, true);
        if (j.warnings?.length) toast(j.warnings.join(" · "), true);
        else if (nb) toast(t("app.nb.done", { items: nbItems(lines) }));
        return;
      }
    }
    // keine Antwort (Zeit abgelaufen), Gerät gesperrt, Server-Fehler: wie bisher über den Ausgang
  }
  closeSheet();
  addHist(id, lines, { status: "wait", nb: S.nb ? true : undefined });
  S.nb = null;
  outbox.push(body);
  if (!store.set("outbox", outbox)) {              // Speicher voll/gesperrt: nicht still verlieren (REV-047)
    toast(t("app.order.store_failed"), true);
    buzz([80, 60, 80]);
  }
  S.cart = {}; saveCart(); S.check = null;
  buzz(40);
  openPay(id, true);
  flush();
}

// Verlauf: die neuesten 40 – ältere nur, solange sie nicht gesendet sind oder als abgelehnt noch offen (T42)
function trimHist(list) {
  const open = new Set(rejected.map((r) => r.id));
  return list.filter((h, i) => i < 40 || (h.status !== "sent" && h.status !== "rejected") || open.has(h.id));
}
