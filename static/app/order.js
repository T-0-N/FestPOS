/* FestPOS – Bestell-App › 2. Bestellen: Kategorien, Kacheln, Warenkorb, Optionen wählen. */
"use strict";

// ---------- 2. Bestellen
// Reihenfolge der Kategorien zum Wischen: wie die Knöpfe, «Weitere» (ohne Kategorie) zuletzt
function catOrder() {
  const ids = cats().map((c) => c.id);
  if (products().some((p) => p.category_id == null)) ids.push("none");
  return ids;
}
function renderOrder() {
  const cs = cats();
  const uncategorized = products().filter((p) => p.category_id == null);
  // "none" = Knopf "Weitere" (Produkte ohne Kategorie); null hiesse "noch nichts gewählt"
  if (S.cat === "none" ? !uncategorized.length : (S.cat == null || !cs.find((c) => c.id === S.cat))) S.cat = cs[0]?.id ?? null;
  const list = products().filter((p) => cs.length === 0 || p.category_id === S.cat ||
    (S.cat === "none" && p.category_id == null));
  const slide = S.slide > 0 ? " slide-r" : S.slide < 0 ? " slide-l" : "";
  S.slide = 0;
  app.innerHTML = `<section class="view${nbOn() ? " nb-mode" : ""}">
    ${header(`${esc(W().title)} ${esc(S.table)}`, { back: true })}
    <div class="ordhead">${nbBandHTML()}
    ${cs.length ? `<nav class="cats" aria-label="${th("app.order.categories")}"><div class="cat-row">
      ${cs.map((c) => `<button class="cat" style="--c:${c.color}" data-act="cat" data-id="${c.id}" aria-pressed="${c.id === S.cat}">${esc(c.name)}</button>`).join("")}
      ${uncategorized.length ? `<button class="cat" data-act="cat" data-id="none" aria-pressed="${S.cat === "none"}">${th("app.order.other_cat")}</button>` : ""}
    </div><div class="cat-row"></div></nav>` : ""}
    </div>
    <div class="grid img-${imgMode()}${swipeOn() && catOrder().length > 1 ? " swipe" : ""}${slide}">
      ${list.length ? list.map(tileHTML).join("") :
        `<p class="empty">${S.boot ? th("app.order.empty_cat") : th("app.order.no_pricelist")}</p>`}
    </div>
    <div class="bar">
      <button class="cartbtn" data-act="cart" aria-label="${th("app.cart.show")}"><i class="carticon">${ICON.cart}</i><span class="ct"><small id="cnt"></small><b id="tot" class="num"></b></span></button>
      <button class="go" data-act="send" id="send">${th(nbOn() ? "app.nb.send_short" : "app.order.send")}</button>
    </div>
  </section>`;
  updateBar();
  layoutCats();
  const top = $("header.top"), oh = $(".ordhead");     // Kategorien unter dem Kopf (im Testmodus ist er höher)
  if (top && oh) oh.style.top = `${top.offsetHeight}px`;
}

// Kategorien: passen sie nicht auf eine Zeile, auf 2 Zeilen verteilen (ungefähr gleich breit).
// Reicht auch das nicht, lassen sich beide Zeilen zusammen seitlich wischen.
function layoutCats() {
  const nav = $(".cats");
  if (!nav) return;
  const [r1, r2] = nav.querySelectorAll(".cat-row");
  const btns = [...nav.querySelectorAll(".cat")];
  btns.forEach((b) => r1.appendChild(b));
  nav.classList.remove("two");
  if (r1.scrollWidth > nav.clientWidth - 20) {
    nav.classList.add("two");                                // kompaktere Knöpfe, dann messen
    const gap = 6, w = btns.map((b) => b.offsetWidth + gap);
    const half = w.reduce((a, b) => a + b, 0) / 2;
    let acc = 0, k = 0;
    while (k < btns.length - 1 && acc + w[k] / 2 < half) acc += w[k++];
    btns.slice(k).forEach((b) => r2.appendChild(b));
  }
  nav.classList.toggle("two", r2.children.length > 0);
  nav.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function tileHTML(p) {
  const q = qtyOf(p.id);
  const img = imgMode() !== "none" && p.image_id;
  const out = soldOut(p);
  return `<button class="tile${q ? " on" : ""}${img ? " has-img" : ""}${out ? " soldout" : ""}" style="--c:${p.color || catColor(p.category_id)}" data-act="add" data-id="${p.id}" data-st="${stockSig(p)}">
    <span class="nm">${esc(p.name)}</span>
    ${allowedOf(p).length === 1 && isLater(allowedOf(p)[0]) ? `<span class="whentag">⏱ ${esc(allowedOf(p)[0])}</span>` : ""}
    ${out ? `<span class="stocktag out">${th("app.stock.tag_soldout")}</span>` : stockLow(p) ? `<span class="stocktag">${th("app.stock.tag_left", { count: p.stock })}</span>` : ""}
    <span class="pr">${chf(p.price_cents)}${configurable(p) ? `<i class="optico${needsOpts(p) || needsWhen(p) ? " req" : ""}" title="${th("app.order.opts_hint")}">${ICON.opts}</i>` : ""}</span>
    ${img ? `<img src="/media/${esc(p.image_id)}" alt="" loading="lazy" decoding="async">` : ""}
    <span class="q">${q}</span>
    <span class="minus" data-act="sub" data-id="${p.id}" role="button" aria-label="${th("app.order.one_less")}">${ICON.minus}</span>
  </button>`;
}

function updateTile(id) {
  const el = app.querySelector(`.tile[data-id="${id}"]`);
  if (!el) return;
  const q = qtyOf(id);
  el.classList.toggle("on", q > 0);
  const qe = $(".q", el);
  qe.textContent = q;
  qe.classList.remove("bump"); void qe.offsetWidth; qe.classList.add("bump");
}
function updateBar() {
  const n = cartCount();
  const cnt = $("#cnt"), tot = $("#tot"), snd = $("#send");
  if (!cnt) return;
  cnt.textContent = n ? t("app.cart.show_n", { count: n }) : t("app.cart.nothing");
  tot.textContent = `${cur()} ${chf(cartTotal())}`;
  snd.disabled = n === 0;
}
// Menge einer Position ändern (key) – ohne key: Produkt ohne Optionen bzw. zuletzt hinzugefügte Position
function changeQty(pid, d, key = null) {
  if (d > 0 && stockRefused(productById(pid), d)) return;
  const when = defaultWhenOf(productById(pid));
  if (!key) {
    if (d > 0) key = lineKey(pid, [], "", when);
    else key = Object.keys(S.cart).filter((k) => S.cart[k].pid === pid).pop();
    if (!key) return;
  }
  const l = S.cart[key] || { pid, q: 0, opts: [], note: "", when: isLater(when) ? when : null };
  l.q = Math.max(0, Math.min(999, l.q + d));
  if (l.q) S.cart[key] = l; else delete S.cart[key];
  saveCart();
  updateTile(pid);
  updateBar();
  buzz(d > 0 ? 10 : 25);
}
// Wischen im Produktbereich: nächste (dir=+1) bzw. vorherige Kategorie
function switchCat(dir) {
  const ids = catOrder(), i = ids.indexOf(S.cat), next = i + dir;
  if (i < 0 || next < 0 || next >= ids.length) {              // am Rand: kurz anstossen
    buzz([20, 30, 20]);
    const g = $(".grid");
    if (g) { g.classList.remove("edge"); void g.offsetWidth; g.classList.add("edge"); }
    return;
  }
  S.cat = ids[next];
  S.slide = dir;
  buzz(15);
  render();
}

// ---------- Optionen wählen (Pflicht-Optionen, Vorgaben, Freitext)
// fromKey: bestehende Position anpassen – z.B. "1 von 3 Kaffee mit 2 Zucker"
function openOpts(pid, fromKey = null) {
  const p = productById(pid);
  if (!p || !configurable(p)) return;
  if (!fromKey && stockRefused(p)) return;
  const o = optsOf(p);
  const from = fromKey ? S.cart[fromKey] : null;
  S.optEdit = {
    pid, fromKey, max: from ? from.q : 999, q: 1,
    sel: o.groups.map((g, gi) => g.choices.filter((c) => from?.opts?.includes(optLabel(o, gi, c)))),
    note: from?.note || "",
    when: from ? (from.when || (allowedOf(p).length ? defWhen() : null)) : defaultWhenOf(p),
  };
  openSheet(optSheetHTML(), { outside: S.tour ? "close" : S.boot?.options_outside || "warn" });
  $("#optnote")?.addEventListener("input", (e) => { if (S.optEdit) S.optEdit.note = e.target.value; });
}
const optsOf = (p) => prodOpts(p) || { groups: [], free: false };
// Gleiche Auswahl in zwei Gruppen («ohne» bei Zucker und bei Milch): mit Gruppennamen senden und anzeigen,
// sonst weiss die Küche nicht, was gemeint ist
function optLabel(o, gi, c) {
  const g = o.groups[gi], lc = c.toLowerCase();
  const dup = g.name && o.groups.some((h, hi) => hi !== gi && h.choices.some((x) => x.toLowerCase() === lc));
  return dup ? `${g.name}: ${c}` : c;
}
function optMissing() {
  const p = productById(S.optEdit.pid), o = optsOf(p);
  const miss = o.groups.filter((g, i) => g.required && !S.optEdit.sel[i].length).map((g) => g.name || t("app.opts.choice"));
  if (needsWhen(p) && !S.optEdit.when) miss.unshift(t("app.opts.delivery"));
  return miss;
}
function optSheetHTML() {
  const E = S.optEdit, p = productById(E.pid), o = optsOf(p);
  const missing = optMissing();
  return `<h2>${esc(p.name)}</h2>
    ${choiceOf(p) ? `<div class="optg when-g${needsWhen(p) && !E.when ? " todo" : ""}">
      <div class="optg-h">${th("app.opts.delivery")}${needsWhen(p) ? ` <em>${th("app.opts.required")}</em>` : ""}</div>
      <div class="optg-c">${allowedOf(p).map((w) => `<button class="chip${isLater(w) ? " later" : ""}" data-act="opt-when" data-v="${esc(w)}" aria-pressed="${E.when === w}">${esc(w)}</button>`).join("")}</div>
    </div>` : (isLater(E.when) ? `<p class="when-forced">${th("app.opts.delivery_forced", { when: E.when })}</p>` : "")}
    ${o.groups.map((g, gi) => `<div class="optg${g.required && !E.sel[gi].length ? " todo" : ""}">
      <div class="optg-h">${esc(g.name || t("app.opts.choice"))}${g.required ? ` <em>${th("app.opts.required")}</em>` : ""}${g.multi ? ` <small>${th("app.opts.multi")}</small>` : ""}</div>
      <div class="optg-c">${g.choices.map((c, ci) => `<button class="chip" data-act="opt-pick" data-g="${gi}" data-c="${ci}" aria-pressed="${E.sel[gi].includes(c)}">${esc(c)}</button>`).join("")}</div>
    </div>`).join("")}
    ${o.free ? `<label class="field">${th("app.opts.note")}<input id="optnote" value="${esc(E.note)}" maxlength="60" placeholder="${th("app.opts.note_placeholder")}" autocomplete="off" enterkeyhint="done"></label>` : ""}
    <div class="qty optq"><span>${E.fromKey ? th("app.opts.how_many", { max: E.max }) : th("app.opts.quantity")}</span>
      <button data-act="opt-q" data-d="-1" aria-label="${th("app.common.less")}" ${E.q <= 1 ? "disabled" : ""}>${ICON.minus}</button>
      <output>${E.q}</output>
      <button data-act="opt-q" data-d="1" aria-label="${th("app.common.more")}" ${E.q >= E.max ? "disabled" : ""}>${ICON.plus}</button></div>
    <div class="actions">
      <button class="go" data-act="opt-ok" ${missing.length ? "disabled" : ""}>${missing.length ? th("app.opts.please_choose", { list: missing.join(", ") })
        : th(E.fromKey ? "app.opts.apply" : "app.opts.add", { price: chf(E.q * p.price_cents) })}</button>
      <button class="alt" data-act="close-sheet">${th("app.common.cancel")}</button>
    </div>`;
}
// Bemerkungsfeld: Taste unten rechts auf der Tastatur ("Fertig") schliesst die Tastatur
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && ["optnote", "devname", "stornonote"].includes(e.target.id)) { e.preventDefault(); e.target.blur(); }
});
function refreshOpts() {
  const sh = document.querySelector(".sheet");
  if (!sh || !S.optEdit) return;
  sh.innerHTML = optSheetHTML();
  $("#optnote")?.addEventListener("input", (e) => { if (S.optEdit) S.optEdit.note = e.target.value; });
}
function optPick(gi, ci) {
  const E = S.optEdit, g = optsOf(productById(E.pid)).groups[gi], c = g.choices[ci];
  const sel = E.sel[gi];
  if (sel.includes(c)) E.sel[gi] = sel.filter((x) => x !== c);
  else E.sel[gi] = g.multi ? g.choices.filter((x) => x === c || sel.includes(x)) : [c];
  buzz(8);
  refreshOpts();
}
function optOk() {
  const E = S.optEdit;
  if (!E || optMissing().length) return;
  if (!E.fromKey && stockRefused(productById(E.pid), E.q)) return;
  const o = optsOf(productById(E.pid));
  const opts = E.sel.flatMap((sel, gi) => sel.map((c) => optLabel(o, gi, c))), note = (E.note || "").trim().slice(0, 60);
  if (E.fromKey && S.cart[E.fromKey]) {
    const src = S.cart[E.fromKey];
    src.q -= E.q;
    if (src.q <= 0) delete S.cart[E.fromKey];
  }
  const when = isLater(E.when) ? E.when : null;
  const key = lineKey(E.pid, opts, note, when);
  const l = S.cart[key] || { pid: E.pid, q: 0, opts, note, when };
  l.q = Math.min(999, l.q + E.q);
  S.cart[key] = l;
  saveCart();
  const reopenCart = !!E.fromKey;
  S.optEdit = null;
  closeSheet();
  updateTile(E.pid);
  updateBar();
  buzz(15);
  if (reopenCart) openCart();
}

// Nachbuchen ohne Bon (send.js): im Warenkorb ein- und wieder ausschalten; vom offenen Tisch aus nur «Abbrechen» oben
const cartNbLink = () => (!nbAllowed() ? ""
  : !S.nb ? `<button class="link" data-act="nb-on">${th("app.nb.link")}</button>`
  : !S.nb.tab ? `<button class="link" data-act="nb-off">${th("app.nb.with_bon")}</button>` : "");
function openCart() {
  const lines = cartLines(), probs = cartProblems();
  if (!lines.length) S.check = null;
  const bad = (l) => probs.find((x) => x.product_id === l.p.id);
  const nb = nbOn();
  openSheet(`<h2>${nb ? th("app.nb.cart_title", { word: W().title, table: S.table }) : `${esc(W().title)} ${esc(S.table)}`}</h2>
    ${nb ? `<div class="nb-warn"><b>${th("app.nb.warn_title")}</b>${th("app.nb.warn_text")}</div>` : ""}
    ${checkBoxHTML(probs)}
    ${lines.length ? lines.map((l) => `<div class="row${bad(l) ? " rowbad" : ""}">
      <span class="nm">${esc(l.p.name)}${optText(l) ? `<small>${esc(optText(l))}</small>` : ""}
        ${bad(l) ? `<small class="bad">${bad(l).left === 0 ? th("app.stock.tag_soldout") : th("app.check.only", { count: bad(l).left })}</small>` : ""}
        ${configurable(l.p) ? `<button class="link sm" data-act="cart-edit" data-key="${esc(l.key)}">${th(l.q > 1 ? "app.cart.adjust_one" : "app.cart.adjust")}</button>` : ""}</span>
      <div class="qty">
        <button data-act="cart-sub" data-id="${l.p.id}" data-key="${esc(l.key)}" aria-label="${th("app.common.less")}">${ICON.minus}</button>
        <output>${l.q}</output>
        <button data-act="cart-add" data-id="${l.p.id}" data-key="${esc(l.key)}" aria-label="${th("app.common.more")}">${ICON.plus}</button>
      </div>
      <span class="sum">${chf(l.q * l.p.price_cents)}</span></div>`).join("") : `<p class="empty">${th("app.cart.nothing")}</p>`}
    <div class="row" style="border:0"><span class="nm">${th("app.cart.total")}</span><span class="sum" style="font-size:30px">${chf(cartTotal())}</span></div>
    <div class="actions">
      <button class="go" data-act="send" ${lines.length && !probs.length ? "" : "disabled"}>${th(probs.length ? "app.check.fix_first" : nb ? "app.nb.send" : "app.order.send")}</button>
      <button class="alt" data-act="close-sheet">${th("app.cart.continue")}</button>
      ${lines.length ? `<div class="links2"><button class="link" data-act="cart-clear">${th("app.cart.clear")}</button>${cartNbLink()}</div>` : ""}
    </div>`);
}
