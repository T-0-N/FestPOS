/* FestPOS – Bestell-App › Eingaben: ein Listener für alle Klicks, Wischen, langes Drücken, Tastatur. */
"use strict";

// ------------------------------------------------------------------ Eingaben (ein Listener für alles)
const VIEWS = { table: renderTable, order: renderOrder, pay: renderPay, history: renderHistory };
let swipeAt = 0;
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b || b.disabled) return;
  const act = b.dataset.act, id = b.dataset.id != null && b.dataset.id !== "" ? +b.dataset.id : null;
  // Einführung: nur ihre eigenen Knöpfe (die App darunter ist gesperrt)
  if (S.tour && !act.startsWith("tour-")) return;
  // Kurz nach dem Wechsel zur Kasse: ein zweiter Tipp (Doppeltipp auf «Senden») soll die Kasse nicht gleich
  // wieder verlassen (REV-047)
  if ((act === "done" || act === "change-paid") && Date.now() - (S.payShownAt || 0) < 600) return;
  if ((act === "done" || act === "change-paid") && payMissing()) { needPayment(); return; }
  // Einzeln: noch nicht alles bezahlt – nachfragen, bevor die Kasse verlassen wird («Trotzdem weiter»: data-force)
  if (act === "done" && !b.dataset.force && !S.pay?.tab && partOpen()) { askLeavePart(act); return; }
  if (act === "back" && !b.dataset.force && S.view === "pay" && !S.pay?.tab && partBusy()) { askLeavePart("back"); return; }
  if (act === "done" && S.pay?.tab && S.view === "pay") { settleTab(); return; }          // offener Tisch: Rest buchen, schliessen
  switch (act) {
    case "back": closeSheet(); history.back(); break;
    case "settings": closeSheet(); openSettings(); break;
    case "settings-name": closeSheet(); openSettings(); setTimeout(() => { const n = $("#devname"); if (n) { n.focus(); n.select(); } }, 60); break;
    case "status": openStatus(); break;
    // Tisch
    case "digit": if (S.input.length < 4) { S.input += b.dataset.d; buzz(8); refreshTableInput(); } break;
    case "digit-del": S.input = S.input.slice(0, -1); refreshTableInput(); break;
    case "digit-clear": S.input = ""; buzz(8); refreshTableInput(); break;      // QR fürs Personal aus: ✕ löscht alles
    case "table-ok": setTable(S.input); break;
    case "table-view": store.set("tableView", b.dataset.v); buzz(8); render(); break;   // Plan | Nummer
    case "plan-area": store.set("planArea", id); PZ.area = null; buzz(8); render(); break;
    case "plan-zoom": planZoomBtn(b.dataset.d); break;
    case "plan-table": planTap(e, b); break;
    case "tab-order": tabOrder(b.dataset.t); break;                       // offener Tisch: weiter bestellen
    case "tab-pay": openTabPay(+b.dataset.id); break;
    case "tab-pay-table": openTabPayFor(b.dataset.t); break;
    case "table-force": closeSheet(); setTable(b.dataset.t, true); break;
    case "table-retry": closeSheet(); S.input = ""; if (S.view === "table") refreshTableInput(); break;
    case "zoom": setZoom(+b.dataset.z); break;
    case "table-cart": closeSheet();
      if (b.dataset.d === "back") setTable(b.dataset.t, true, "keep", b.dataset.r === "1");
      else setTable(b.dataset.t, true, b.dataset.d, b.dataset.r === "1");
      break;
    case "leave": closeSheet();
      if (b.dataset.d === "drop") { S.cart = {}; saveCart(); }
      S.leaveOk = true; history.back(); break;
    case "pick-table": closeSheet(); setTable(b.dataset.t); break;
    case "all-tables": openSheet(`<h2>${th("app.table.all", { many: W().many })}</h2><div class="recent" style="flex-wrap:wrap">
        ${(S.boot?.tables || []).map((x) => `<button class="chip" data-act="pick-table" data-t="${esc(x)}">${esc(x)}</button>`).join("")}</div>`); break;
    case "tabs-all": tabsListSheet(); break;                        // offene Tische als Liste (T35)
    case "scan": startScan(); break;
    case "scan-stop": stopScan(); history.back(); break;
    case "torch": toggleTorch(b); break;
    // Bestellen
    case "cat": S.cat = b.dataset.id === "none" ? "none" : id; render(); break;
    case "add":
      if (e.target.closest(".minus")) break;
      if (longPress?.fired) { longPress = null; break; }
      if (Date.now() - swipeAt < 450) break;             // Ende einer Wischbewegung ist kein Antippen
      { const pr = productById(id); if (needsOpts(pr) || needsWhen(pr)) openOpts(id); else changeQty(id, +1); }
      break;
    case "sub": e.stopPropagation(); changeQty(id, -1); break;
    case "opts": e.stopPropagation(); openOpts(id); break;
    case "opt-pick": optPick(+b.dataset.g, +b.dataset.c); break;
    case "opt-q": if (S.optEdit) { S.optEdit.q = Math.max(1, Math.min(S.optEdit.max, S.optEdit.q + +b.dataset.d)); buzz(8); refreshOpts(); } break;
    case "opt-ok": optOk(); break;
    case "opt-when": if (S.optEdit) { S.optEdit.when = b.dataset.v; buzz(8); refreshOpts(); } break;
    case "cart": openCart(); break;
    case "cart-add": changeQty(id, +1, b.dataset.key); openCart(); break;
    case "cart-sub": changeQty(id, -1, b.dataset.key); openCart(); break;
    case "cart-edit": openOpts(S.cart[b.dataset.key]?.pid, b.dataset.key); break;
    case "cart-clear": S.cart = {}; S.check = null; saveCart(); closeSheet(); render(); break;
    case "check-fix": checkFix(); break;
    case "close-sheet": closeSheet(); break;
    case "renew": renewCert(); break;
    case "send": send(); break;
    case "nb-on": S.nb = { table: S.table }; buzz(15); render(); openCart(); break;      // Nachbuchen ohne Bon (send.js)
    case "nb-off": S.nb = null; buzz(8); render(); openCart(); break;
    case "nb-cancel": nbCancel(); break;
    case "nb-tab": nbFromTab(); break;
    // Kasse
    case "mode": S.payMode = b.dataset.m; S.part.step = "pick"; render(); break;
    case "given": S.given = (b.dataset.v / 100).toFixed(2).replace(/\.00$/, ""); buzz(); refreshPanel(); break;
    case "key":
      keyInput(b.dataset.t, b.dataset.k);
      if (b.dataset.t === "own") { const el = $("#ownsheet"); if (el) el.outerHTML = ownSheet(); }
      else if (b.dataset.t === "tip") refreshTip();
      else refreshPanel();
      break;
    // Trinkgeld (payment.js)
    case "tip": openTip(); break;
    case "tip-set": tipPick(+b.dataset.v, false); break;
    case "tip-plus": tipPick(+b.dataset.v, true); break;
    case "tip-ok": tipDone(false); break;
    case "tip-none": tipDone(true); break;
    case "part-amount": {                            // Rückgeld: zu wenig gegeben -> Teilzahlung, Rest bleibt offen
      const g = parseEntry(S.given || "");
      if (g > 0 && g < partState().rest) { S.part.paid.push({ amount: g, items: {} }); S.given = ""; savePaid(); buzz(30); render(); }
      break;
    }
    // Teilen pro Person (pay.js)
    case "split-own": splitStart(+b.dataset.n); break;   // offene Person antippen = kassieren wie der grüne Knopf
    case "own-pay": {
      const v = parseEntry(S.ownGiven || ""), n = S.ownFor, P = splitPlan();
      if (n && v > 0 && v <= P.rest - 5 * (P.open.length - 1)) { S.splitOwn = { ...S.splitOwn, [n]: v }; closeSheet(); buzz(15); render(); }
      break;
    }
    case "own-clear": { const o = { ...S.splitOwn }; delete o[S.ownFor]; S.splitOwn = o; closeSheet(); render(); break; }
    case "split-start": closeSheet(); splitStart(+b.dataset.n); break;
    case "split-go": { const n = splitPlan().next; if (n) splitStart(n); break; }
    case "split-back": S.splitStep = null; S.splitGiven = ""; S.personKind = null; render(); window.scrollTo(0, 0); break;
    case "split-paid": splitBook(); break;
    case "split-person": { const i = S.part.paid.findIndex((p) => p.person === +b.dataset.n); if (i >= 0) openPartPerson(i); break; }
    case "receipt-split": closeSheet(); receiptSplit(+b.dataset.n); break;
    case "person-kind": S.personKind = b.dataset.v; buzz(8); refreshPanel(); break;
    case "change-paid": changePaid(); break;
    case "split": {
      const P = splitPlan();
      S.split = Math.max(P.min, Math.min(30, P.N + +b.dataset.d));
      S.splitOwn = Object.fromEntries(Object.entries(S.splitOwn || {}).filter(([n]) => +n <= S.split));
      buzz(); refreshPanel(); break;
    }
    case "split-given": S.splitGiven = (b.dataset.v / 100).toFixed(2).replace(/\.00$/, ""); buzz(); refreshPanel(); break;
    case "to-pad": toPad(b); break;
    case "pick": pickItem(id); refreshPanel(); break;
    case "unpick": unpickItem(id); refreshPanel(); break;
    case "part-given": S.part.given = (b.dataset.v / 100).toFixed(2).replace(/\.00$/, ""); buzz(); refreshPanel(); break;
    case "part-pay": partPay(); break;
    case "part-undo": {                              // jede Person, nicht nur die letzte
      closeSheet();
      const i = b.dataset.n != null ? +b.dataset.n : S.part.paid.length - 1, p = S.part.paid[i];
      if (!p || p.receipt || p.payrexx || (S.pay?.tab && S.tabSettling)) break;
      if (S.pay?.tab) tabDropPaid(p); else S.part.paid.splice(i, 1);   // offener Tisch: Rücknahme merken (undo, tabs.js)
      const tk = p.tk || (p.person ? `s${p.person}` : `p${i + 1}`);
      if (!tipOf(tk)?.x) setTip(tk, 0);                       // Trinkgeld dieser Person geht mit (ausser mit Payrexx)
      S.part.step = "pick"; savePaid(); buzz(20); render();
      toast(t("app.split.undone", { n: p.person || i + 1 }));
      break;
    }
    case "ez-next": if (partSel().amount > 0) { S.part.step = "pay"; S.part.given = ""; buzz(); render(); toPerson(); } break;
    case "ez-back": S.part.step = "pick"; render(); window.scrollTo(0, 0); break;
    case "ez-all": pickAll(); refreshPanel(); break;
    case "ez-none": S.part.sel = {}; buzz(); refreshPanel(); break;
    case "ez-person": openPartPerson(+b.dataset.n); break;
    case "rejected": openRejected(); break;
    case "rej-restore": restoreRejected(b.dataset.id); break;
    case "payrej-fix": restoreRejected(S.pay?.id); break;
    case "payrej-drop":
      rejected = rejected.filter((x) => x.id !== S.pay?.id); store.set("rejected", rejected);
      closeSheet(); S.input = ""; S.table = null; history.replaceState({ view: "table" }, ""); S.view = "table"; render(); break;
    case "rej-drop":
      rejected = rejected.filter((x) => x.id !== b.dataset.id); store.set("rejected", rejected);
      openRejected(); refreshProb(); break;
    case "done": closeSheet(); S.input = ""; S.table = null; history.replaceState({ view: "table" }, ""); S.view = "table"; render(); break;
    case "open-pay": openHistEntry(b.dataset.id); break;
    case "pay-kind": setPayment(b.dataset.v); break;
    case "payrexx-all": tapWhole(); break;                         // T13: Karte/TWINT mit NFC (Payrexx)
    case "payrexx-part": tapPerson(); break;
    case "payrexx-split": tapSplit(+b.dataset.v); break;
    case "payrexx-cancel": cancelTap(); break;
    case "payrexx-again": tapAgain(); break;
    case "payrexx-manual": tapManual(); break;
    case "receipt": receiptWhole(); break;
    case "receipt-part": closeSheet(); receiptPart(+b.dataset.n); break;
    case "storno": openStorno(); break;
    case "storno-q": stornoQty(+b.dataset.l, +b.dataset.d); break;
    case "storno-all": stornoAll(); break;
    case "storno-reason": if (S.storno) { S.storno.reason = b.dataset.v; buzz(8); refreshStorno(); } break;
    case "storno-ok": stornoOk(); break;
    // Einstellungen
    case "theme": store.set("theme", b.dataset.v); applyTheme(); openSettingsMore(); break;
    case "wake": store.set("wake", b.dataset.v === "1"); applyWake(); openSettingsMore(); break;
    case "soldout-open": openSoldout(); break;
    case "soldout-toggle": toggleSoldout(id); break;
    case "stock-edit": openStockEdit(id); break;
    case "stock-q": if (S.stockEdit) { S.stockEdit.v = Math.max(0, (+S.stockEdit.v || 0) + +b.dataset.d); buzz(8); refreshSoldout(); } break;
    case "stock-set": if (S.stockEdit) { S.stockEdit.v = +b.dataset.v; buzz(8); refreshSoldout(); } break;
    case "stock-save": if (S.stockEdit && S.stockEdit.v !== "") saveStock(+S.stockEdit.v); break;
    case "stock-none": saveStock(null); break;
    case "stock-back": S.stockEdit = null; refreshSoldout(); break;
    case "settings-more": openSettingsMore(); break;
    case "history": if (historyOn()) { go("history"); checkHistory(); } break;
    // Testmodus
    case "test-start": startTest("app"); break;
    case "test-go": closeSheet(); setTest(true, "app"); toast(t("app.test.started")); break;
    case "test-exit": closeSheet(); setTest(false, "app"); toast(t("app.test.ended")); break;
    case "tpin": if (S.testPin.length < 8) { S.testPin += b.dataset.d; buzz(8); testPinTyped(); } break;
    case "tpin-del": S.testPin = S.testPin.slice(0, -1); testPinTyped(); break;
    // Einführung
    case "tour-start": closeSheet(); startTour(); break;
    case "tour-next": tourGo(+1); break;
    case "tour-prev": tourGo(-1); break;
    case "tour-end": endTour(); break;
    // Letzter Schritt: Testmodus ja/nein. Den Eintrag der Einführung im Verlauf nicht mit history.back() entfernen –
    // das «Zurück» käme erst nach dem PIN-Fenster an und würde es wieder schliessen.
    case "tour-test": endTour(true); history.replaceState({ view: S.view }, ""); if (b.dataset.v === "1") startTest("tour"); break;
    // PIN-Sperre
    case "pin": if (S.pinInput.length < 8) { S.pinInput += b.dataset.d; buzz(8); pinTyped(); } break;
    case "pin-del": S.pinInput = S.pinInput.slice(0, -1); pinTyped(); break;
    case "app-reload": closeSheet(); location.reload(); break;    // Warenkorb, Ausgang, Verlauf bleiben im Gerät
    case "fully-bright": case "fully-screen-off": case "fully-cache": case "fully-restart": case "fully-reboot":
    case "fully-kiosk-off": case "fully-kiosk-on": case "fully-android": fullyAction(act, b.dataset.d); break;
  }
});

// Nach langem Drücken («Ausverkauft melden», Person beim Teilen) den einen Klick beim Loslassen schlucken: das Fenster
// ist dann schon offen, der Klick träfe sonst dort einen Knopf (oder schaltete das Produkt um bzw. kassierte die Person)
let longAt = 0;
document.addEventListener("click", (e) => {
  if (Date.now() - longAt < 350) { longAt = 0; e.stopPropagation(); e.preventDefault(); }
}, true);
// Lange drücken auf eine Kachel: Optionen wählen (bei Produkten mit freiwilligen Optionen)
let longPress = null;
// Wischen im Produktbereich (links/rechts) = andere Kategorie. Senkrecht scrollt der Browser wie gewohnt
// (touch-action: pan-y auf .grid.swipe); erst eine klar waagrechte Bewegung zählt als Wischen.
let swipe = null;
document.addEventListener("pointerdown", (e) => {
  longAt = 0;                                        // neue Berührung: die Sperre nach langem Drücken gilt nicht mehr
  if (S.tour || S.locked) return;
  const g = S.view === "order" && swipeOn() && e.isPrimary ? e.target.closest(".grid.swipe") : null;
  swipe = g ? { x: e.clientX, y: e.clientY, t0: Date.now(), g, dir: null } : null;
  const sold = e.target.closest(".soldrow");          // «Ausverkauft melden»: lange drücken = Restmenge eingeben
  if (sold) {
    const sid = +sold.dataset.id;
    longPress = { x: e.clientX, y: e.clientY, fired: false,
      swallow: true, timer: setTimeout(() => { if (longPress) { longPress.fired = true; buzz(30); openStockEdit(sid); } }, 450) };
    return;
  }
  const own = S.view === "pay" && splitPlan().open.length > 1 ? e.target.closest('#splitplan [data-act="split-own"]') : null;
  if (own) {                                           // Teilen: offene Person lange drücken = eigener Betrag
    const n = +own.dataset.n;
    longPress = { x: e.clientX, y: e.clientY, fired: false, swallow: true,
      timer: setTimeout(() => { if (longPress) { longPress.fired = true; buzz(30); openOwn(n); } }, 450) };
    return;
  }
  const tile = e.target.closest(".tile");
  if (!tile || e.target.closest(".minus") || !configurable(productById(+tile.dataset.id))) { longPress = null; return; }
  const pid = +tile.dataset.id;
  longPress = { x: e.clientX, y: e.clientY, fired: false,
    timer: setTimeout(() => { if (longPress) { longPress.fired = true; buzz(30); openOpts(pid); } }, 450) };
});
document.addEventListener("pointermove", (e) => {
  if (longPress && !longPress.fired && Math.hypot(e.clientX - longPress.x, e.clientY - longPress.y) > 12) {
    clearTimeout(longPress.timer); longPress = null;
  }
  if (!swipe) return;
  const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
  if (!swipe.dir && Math.hypot(dx, dy) > 10) swipe.dir = Math.abs(dx) > Math.abs(dy) * 1.3 ? "x" : "y";
  if (swipe.dir === "x") swipe.g.style.transform = `translateX(${Math.max(-60, Math.min(60, dx * 0.3))}px)`;
});
function swipeEnd(e, cancel) {
  const sw = swipe;
  swipe = null;
  if (!sw) return;
  sw.g.style.transform = "";
  if (cancel || sw.dir !== "x") return;
  const dx = e.clientX - sw.x, dy = e.clientY - sw.y;
  if (Math.abs(dx) >= 70 && Math.abs(dx) > Math.abs(dy) * 1.3 && Date.now() - sw.t0 < 900) {
    swipeAt = Date.now();
    switchCat(dx < 0 ? +1 : -1);                  // nach links wischen = nächste Kategorie
  }
}
document.addEventListener("pointerup", (e) => {
  if (longPress && !longPress.fired) { clearTimeout(longPress.timer); longPress = null; }
  else if (longPress?.fired) {
    clearSelection();                                // Chrome markiert sonst Text im eben geöffneten Fenster
    if (longPress.swallow) longAt = Date.now();      // der Klick danach gehört zum langen Drücken (nicht umschalten/kassieren)
  }
  swipeEnd(e, false);
});
document.addEventListener("pointercancel", (e) => {
  if (longPress && !longPress.fired) { clearTimeout(longPress.timer); longPress = null; }
  swipeEnd(e, true);
});
// Langes Drücken ist in der App eine Bedienung, kein «Text markieren / kopieren»: kein Kontextmenü und keine
// Markierung (siehe auch user-select in app.css). In Eingabefeldern bleibt alles wie gewohnt.
const EDITABLE = "input, textarea, [contenteditable], .bigpw";
document.addEventListener("contextmenu", (e) => { if (!e.target.closest(EDITABLE)) e.preventDefault(); });
function clearSelection() {
  const sel = window.getSelection?.();
  if (sel && sel.rangeCount && !document.activeElement?.closest?.(EDITABLE)) sel.removeAllRanges();
}
document.addEventListener("selectionchange", () => {
  const n = window.getSelection?.()?.anchorNode;
  const el = n && (n.nodeType === 1 ? n : n.parentElement);
  if (el && !el.closest(EDITABLE)) clearSelection();
});

function refreshTableInput() {
  $("#tin").textContent = S.input;
  const ok = $('[data-act="table-ok"]');
  ok.disabled = !S.input;
  ok.innerHTML = tableOkText();
}
function keyInput(target, k) {
  // given: Rückgeld · sgiven: Teilen · pgiven: Einzeln · own: eigener Betrag · tip: Trinkgeld
  const obj = target === "pgiven" ? S.part : target === "tip" ? S.tipEdit : S;
  if (!obj) return;
  const field = target === "sgiven" ? "splitGiven" : target === "own" ? "ownGiven" : target === "tip" ? "v" : "given";
  let v = obj[field] || "";
  if (k === "del") v = v.slice(0, -1);
  else if (k === ".") { if (!v.includes(".")) v = (v || "0") + "."; }
  else {
    const [, dec] = v.split(".");
    if (dec !== undefined && dec.length >= 2) return;
    if (v.replace(".", "").length >= 7) return;
    v = v === "0" ? k : v + k;
  }
  obj[field] = v;
  buzz(6);
}
function pickItem(id) {
  const P = S.part, item = S.pay.items.find((i) => i.id === id);
  const paid = P.paid.reduce((s, p) => s + ((p.items || {})[id] || 0), 0);
  const sel = P.sel[id] || 0;
  if (sel + paid >= liveQty(item) || partCovered()) { buzz([20, 40, 20]); return; }   // nichts mehr offen oder Rest schon gedeckt
  P.sel[id] = sel + 1;
  buzz();
}
function unpickItem(id) {
  const P = S.part, sel = P.sel[id] || 0;
  if (sel > 1) P.sel[id] = sel - 1; else delete P.sel[id];
  buzz(6);
}
function pickAll() {
  const { paidQty } = partState(), sel = {};
  S.pay.items.forEach((i) => { const open = liveQty(i) - (paidQty[i.id] || 0); if (open > 0) sel[i.id] = open; });
  S.part.sel = sel;
  buzz();
}
function partPay() {
  const P = S.part;
  const { amount } = partSel();                     // höchstens, was noch offen ist (eigene Beträge schon abgezogen)
  if (amount <= 0) return;
  if (payModeCfg() === "required" && !S.personKind) { needKind(); return; }
  P.paid.push({ amount, items: { ...P.sel }, payment: S.personKind || null, tk: `p${P.paid.length + 1}` });
  P.sel = {}; P.given = ""; P.step = "pick"; S.personKind = null;
  savePaid();
  buzz(30);
  render();                                          // nächste Person (Schritt 1) oder «Alles bezahlt»
  window.scrollTo(0, 0);
}
// Einzeln, Schritt «kassieren»: gleich zu «Person n» scrollen (unter die Kopfzeile). Die ganze Tastatur muss über dem
// grünen Knopf stehen – auch wenn der Kopf höher ist (Übungsbalken, kleines Handy); dann darf «Person n» etwas unter
// den Kopf rutschen. Das Bezahlt/Offen oben bleibt per Scrollen erreichbar.
function toPerson() {
  const el = document.getElementById("partpay"), top = document.querySelector(".top");
  if (!el) { window.scrollTo(0, 0); return; }
  const y = el.getBoundingClientRect().top + scrollY - (top ? top.offsetHeight : 0) - 6;
  const pad = el.querySelector(".pad"), foot = document.getElementById("payfoot");
  const need = pad && foot ? pad.getBoundingClientRect().bottom + scrollY - foot.getBoundingClientRect().top + 8 : 0;
  window.scrollTo(0, Math.max(0, y, need));
}
// Antippen von «Gegeben» (T25): nach unten scrollen, bis Feld, Tastatur und grüner Knopf ganz sichtbar sind
function toPad(el) {
  const panel = el.closest(".panel") || document, pad = panel.querySelector(".pad"), foot = document.getElementById("payfoot");
  if (!pad) return;
  const limit = foot ? foot.getBoundingClientRect().top : innerHeight;
  const need = pad.getBoundingClientRect().bottom + scrollY - limit + 8;
  if (need > scrollY) window.scrollTo({ top: need, behavior: "smooth" });
}
function savePaid() {
  if (S.tour) return;
  if (S.pay?.tab) { S.pay.paid = S.part.paid; syncTabPaid(); return; }     // offener Tisch: Zahlung an den Server
  S.part.paid.forEach((p) => { if (!p.key) p.key = payKey(); });             // auch sofort kassierte
  updateHist(S.pay.id, { paid: S.part.paid });
  if (!S.pay.tabMode) queueParts(S.pay.id);                                  // Bestellungen: Teilzahlungen an den Server
}
// Teilen: offene Person lange drücken = eigener Betrag (der Rest verteilt sich neu auf die übrigen offenen Personen)
function openOwn(n) {                                                      // nur mit mehr als einer offenen Person
  S.ownFor = n; S.ownGiven = S.splitOwn?.[n] ? (S.splitOwn[n] / 100).toFixed(2).replace(/\.00$/, "") : "";
  openSheet(ownSheet());
}
// Teilen: Person n kassieren (Schritt 2)
function splitStart(n) {
  if (!splitPlan().open.includes(n)) return;
  S.splitStep = n; S.splitGiven = ""; S.personKind = null;
  buzz(); render(); window.scrollTo(0, 0);
}
// Zahlart Pflicht, aber für diese Person nicht gewählt: Zeile kurz hervorheben
function needKind() {
  buzz([40, 30, 40]);
  toast(t("app.payment.choose_first"), true);
  const row = $("#personkind");
  if (row) { row.classList.remove("nudge"); void row.offsetWidth; row.classList.add("nudge"); }
}
// Teilen: «Person n hat bezahlt» – Anteil als Teilzahlung mit Zahlart (und Payrexx), dann zurück zur Aufteilung
function splitBook(extra = {}) {
  const n = S.splitStep, p = splitPlan().persons.find((x) => x.n === n);
  if (!p || p.paid || p.amount <= 0) return;
  if (!extra.payrexx && payModeCfg() === "required" && !S.personKind) { needKind(); return; }
  const e = { amount: p.amount, items: {}, own: true, person: n, payment: S.personKind || null, tk: `s${n}` };
  if (extra.payrexx) { e.payrexx = extra.payrexx; if (S.pay.tab) e.key = `payrexx-${extra.payrexx}`; }   // Tisch: bucht der Server
  S.part.paid.push(e);
  const o = { ...S.splitOwn }; delete o[n]; S.splitOwn = o;
  S.splitStep = null; S.splitGiven = ""; S.personKind = null;
  savePaid(); buzz(30); render(); window.scrollTo(0, 0);
}
// Rückgeld: «Bezahlt · Nächste Bestellung» hält die Zahlung fest (Rest mit der Zahlart der Bestellung), dann weiter
function changePaid() {
  const { rest } = partState();
  if (rest > 0) S.part.paid.push({ amount: rest, items: {}, payment: S.pay.payment || null, change: true, tk: "c" });
  savePaid(); buzz(30);
  closeSheet(); S.input = ""; S.table = null; history.replaceState({ view: "table" }, ""); S.view = "table"; render();
}
