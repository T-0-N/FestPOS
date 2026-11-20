/* FestPOS – Verwaltung › Kasse & App › Bons & Quittung (T24, T34): der Bon-Baukasten (app/bon_design.py).

   Oben Quittung | Küchen- und Getränkebon, Vorlage, Papier. Darunter drei Spalten:
     1. Bausteine in Druckreihenfolge – ziehen am ⋮⋮ (Maus und Finger: die Zeile schwebt, die anderen gleiten zur
        Seite), Alt+↑/↓ oder ▲ ▼ (schmaler Bildschirm, gleitet ebenfalls), Auge = ein/aus, «+ Baustein»
     2. Vorschau – Bild aus denselben Druckerdaten; antippen wählt den Baustein, ziehen verschiebt ihn
     3. Eigenschaften des gewählten Bausteins
   Unten ein Balken «Geändert – noch nicht gespeichert», solange etwas nicht gespeichert ist.
   Welche Bausteine und Eigenschaften es gibt, kommt vom Server (schema) – hier steht nur, wie man sie bedient.
   Ohne Bibliothek, strenge CSP (kein Inline-Script, Stile nur über Klassen oder element.style). */
import { $, $$, api, esc, guard, toast } from "./core.js";
import { pageHead, RENDER, view } from "./shell.js";

let V = null;                    // Antwort des Servers (gespeicherter Stand, Schema, Vorlagen, Drucker …)
let S = null;                    // Arbeitskopie: { receipt: {blocks, template}, ticket: {…}, receipt_printer_id }
let saved = "";                  // gespeicherter Stand (JSON) – Vergleich für «Geändert»
let kind = "receipt";
const sel = { receipt: 0, ticket: 0 };
let width = 48, when = null, prevTimer = null, prevUrl = null, hits = [], seq = 0, dropped = false;

const EYE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.9 17.9A10.4 10.4 0 0 1 12 19C5 19 1 12 1 12a18.4 18.4 0 0 1 5.1-5.9M9.9 5.2A9.7 9.7 0 0 1 12 5c7 0 11 7 11 7a18.5 18.5 0 0 1-2.2 3.2M14.1 14.1a3 3 0 1 1-4.2-4.2"/><path d="M1 1l22 22"/></svg>';

// Kasse & App › Reiter «Bons & Quittung» (nur Admin)
RENDER.bon = async () => {
  if (view !== "bon") return;
  $("#main").innerHTML = `${pageHead()}<div class="card bk-card" id="bondesign"><p class="hint">${th("admin.common.loading")}</p></div>`;
  await mountBonDesign();
};

// ------------------------------------------------------------------ Hilfen
const spec = (typ) => V.schema[kind][typ] || { props: [], l: typ };
const blocks = () => S[kind].blocks;
const state = () => JSON.stringify({ receipt: S.receipt, ticket: S.ticket, rp: S.receipt_printer_id });
const dirty = () => state() !== saved;
const word = () => t(V.table_word === "Platz" ? "admin.word.seat" : "admin.word.table");
const lbl = (key) => th(key, { word: word() });
const optLabel = (o) => (o[1] ? lbl(o[1]) : esc(o[2]));
const strip = (list) => list.map(({ text, ...rest }) => rest);          // Texte (Kopf, Fusszeile) für den Vergleich weglassen
const sameAs = (a, b) => JSON.stringify(strip(a)) === JSON.stringify(strip(b));

function fromView(v) {
  V = v;
  S = JSON.parse(JSON.stringify({ receipt: v.receipt, ticket: v.ticket, receipt_printer_id: v.receipt_printer_id }));
  saved = state();
  if (when == null || !v.delivery_times.includes(when)) when = v.delivery_times[0];
}

// Lieferzeitpunkt: welche Zeitpunkte gedruckt werden – null = Standard (alle ausser dem ersten,
// «Sofort»), ["*"] = alle, sonst die gewählten Namen
const whenOn = (v, x, i) => (v == null ? i > 0 : v.includes("*") || v.some((y) => y.toLowerCase() === x.toLowerCase()));
function whenText(v) {
  const times = V.delivery_times || [], on = times.filter((x, i) => whenOn(v, x, i));
  if (v == null) return th("admin.bon.when_std", { now: times[0] || "" });
  return on.length ? esc(on.join(" / ")) : th("admin.bon.when_none");
}
function summary(k) {
  const s = spec(k.type), parts = [];
  if (s.cond) parts.push(lbl(s.cond));
  for (const p of s.props) {
    const v = k[p.k];
    if (p.k === "before" || p.t === "image") continue;
    if (p.t === "times") {
      parts.push(whenText(v));
    } else if (p.t === "seg" && ["size", "char", "spacing", "lines", "width", "qsize"].includes(p.k)) {
      const o = p.o.find((x) => String(x[0]) === String(v));
      if (o) parts.push(optLabel(o));
    } else if (p.t === "bool" && v && !["line", "part", "word", "each", "table", "order", "device", "cats", "cat_ul"].includes(p.k)) {
      parts.push(lbl(p.l));
    } else if ((p.t === "text" || p.t === "area") && v) {
      parts.push(esc(String(v).split("\n")[0]));
    }
  }
  if (k.type === "image" || k.type === "logo") parts.push(th(k.type === "logo" ? "admin.bon.logo_sub" : k.image ? "admin.bon.image_set" : "admin.bon.image_none"));
  return parts.join(" · ");
}

// ------------------------------------------------------------------ Aufbau
function listHtml() {
  const list = blocks(), i0 = sel[kind];
  return list.map((k, i) => {
    const s = spec(k.type), fixed = !!s.fixed;
    const tag = s.fixed ? th(s.fixed === "top" ? "admin.bon.fixed_top" : "admin.bon.fixed_bottom") : s.req ? th("admin.bon.required") : "";
    const badge = tag ? `<span class="bk-badge">${tag}</span>` : "", sum = summary(k);
    const eye = s.req ? "" : `<button type="button" class="bk-eye" data-eye="${i}" aria-pressed="${k.on}"
        aria-label="${th(k.on ? "admin.bon.hide" : "admin.bon.show")}" title="${th(k.on ? "admin.bon.hide" : "admin.bon.show")}">${k.on ? EYE : EYE_OFF}</button>`;
    return `<li class="bk-item${k.on ? "" : " off"}${i === i0 ? " sel" : ""}${fixed ? " fixed" : ""}" data-i="${i}" tabindex="0"
        aria-current="${i === i0}">
      ${fixed ? '<span class="bk-grip ph"></span>' : `<button type="button" class="bk-grip" aria-label="${th("admin.bon.move")}" title="${th("admin.bon.move")}">⋮⋮</button>`}
      <span class="bk-txt"><b>${lbl(s.l)}</b><small>${tag ? `<i class="bk-tag">${tag}${sum ? " · " : ""}</i>` : ""}${sum}</small></span>
      ${badge}${eye}
      ${fixed ? "" : `<span class="bk-mv"><button type="button" data-mv="-1" aria-label="${th("admin.bon.up")}">▲</button><button type="button" data-mv="1" aria-label="${th("admin.bon.down")}">▼</button></span>`}
    </li>`;
  }).join("");
}

function addHtml() {
  const types = Object.entries(V.schema[kind]).filter(([, s]) => s.add);
  return types.map(([typ, s]) => {
    const n = blocks().filter((k) => k.type === typ).length;
    return `<button type="button" class="btn sm bk-new" data-add="${typ}" ${n >= s.max ? "disabled" : ""}>${lbl(s.l)}</button>`;
  }).join("");
}

function fieldHtml(p, k) {
  const v = k[p.k];
  if (p.t === "seg") {
    return `<div class="bk-field"><span>${lbl(p.l)}</span><div class="pe-sg bk-sg" role="group" aria-label="${lbl(p.l)}">
      ${p.o.map((o) => `<button type="button" data-p="${p.k}" data-v='${esc(JSON.stringify(o[0]))}' aria-pressed="${String(o[0]) === String(v)}">${optLabel(o)}</button>`).join("")}
      </div></div>`;
  }
  if (p.t === "bool") return `<label class="check"><input type="checkbox" data-p="${p.k}" ${v ? "checked" : ""}> ${lbl(p.l)}</label>`;
  if (p.t === "times") {
    const times = V.delivery_times || [];
    return `<div class="bk-field"><span>${lbl(p.l)}</span>
      ${times.map((x, i) => `<label class="check"><input type="checkbox" data-pt="${p.k}" data-tv="${esc(x)}" ${whenOn(v, x, i) ? "checked" : ""}> ${esc(x)}</label>`).join("")}
      <p class="hint">${th("admin.bon.when_hint")}</p></div>`;
  }
  if (p.t === "text") {
    return `<label class="bk-field">${lbl(p.l)}<input class="in" data-p="${p.k}" value="${esc(v || "")}" maxlength="${p.n}"
      ${p.ph ? `placeholder="${th(p.ph)}"` : ""}></label>`;
  }
  if (p.t === "area") {
    return `<label class="bk-field">${lbl(p.l)}<textarea class="in" data-p="${p.k}" rows="${Math.min(p.lines, 4)}" maxlength="${p.n}"
      ${p.ph ? `placeholder="${th(p.ph)}"` : ""}>${esc(v || "")}</textarea></label>`;
  }
  if (p.t === "image") {
    return `<div class="bk-field"><span>${lbl(p.l)}</span><div class="bd-logo">${k.image ? `<img src="/media/${esc(k.image)}" alt="">` : `<span class="hint">${th("admin.bon.image_none")}</span>`}</div>
      <div class="form"><button type="button" class="btn sm" data-act="img-pick">${th(k.image ? "admin.bon.image_replace" : "admin.bon.image_upload")}</button></div>
      <p class="hint">${th("admin.bon.image_hint")}</p></div>`;
  }
  return "";
}

function logoHtml() {
  return `<div class="bk-field"><span>${th("admin.bon.logo_title")}</span>
    <div class="bd-logo">${V.logo ? `<img src="${esc(V.logo)}" alt="">` : V.app_logo ? `<img src="${esc(V.app_logo)}" alt=""><small>${th("admin.bon.logo_app")}</small>`
      : `<span class="hint">${th("admin.bon.logo_none")}</span>`}</div>
    <div class="form"><button type="button" class="btn sm" data-act="logo-pick">${th(V.logo ? "admin.bon.logo_replace" : "admin.bon.logo_upload")}</button>
      ${V.logo ? `<button type="button" class="btn sm danger" data-act="logo-del">${th("admin.bon.logo_remove")}</button>` : ""}</div>
    <p class="hint">${th("admin.bon.logo_hint2")}</p></div>`;
}

function propsHtml() {
  const k = blocks()[sel[kind]];
  if (!k) return "";
  const s = spec(k.type);
  const head = `<h3>${lbl(s.l)}</h3><p class="hint">${lbl(s.h)}</p>`;
  const fields = s.props.map((p) => fieldHtml(p, k)).join("");
  const free = s.add ? `<div class="form bk-acts"><button type="button" class="btn" data-act="dup" ${blocks().filter((x) => x.type === k.type).length >= s.max ? "disabled" : ""}>${th("admin.bon.duplicate")}</button>
    <button type="button" class="btn danger" data-act="del">${th("admin.bon.remove")}</button></div>` : "";
  const note = s.fixed || s.req ? `<p class="bk-note">${th(kind === "receipt" ? "admin.bon.rules_receipt" : "admin.bon.rules_ticket")}</p>` : "";
  return head + (k.type === "logo" ? logoHtml() : "") + fields + free + note;
}

function tplHtml() {
  const tpls = V.templates[kind];
  const cur = Object.entries(tpls).find(([, v]) => sameAs(v.blocks, blocks()));
  return Object.entries(tpls).map(([n, v]) => `<option value="${n}" ${cur && cur[0] === n ? "selected" : ""}>${lbl(v.l)}</option>`).join("")
    + (cur ? "" : `<option value="" selected disabled>${th("admin.bon.tpl_own")}</option>`);
}

function frame() {
  const printers = V.printers.map((p) => `<option value="${p.id}" ${p.id === S.receipt_printer_id ? "selected" : ""}>${esc(p.name)}${p.enabled ? "" : ` (${t("admin.receipt.printer_off")})`}</option>`).join("");
  return `<div class="bk-top">
      <div class="pe-sg bk-kind" role="group" aria-label="${th("admin.bon.kind")}">
        <button type="button" data-kind="receipt" aria-pressed="${kind === "receipt"}">${th("admin.bon.prev_receipt")}</button>
        <button type="button" data-kind="ticket" aria-pressed="${kind === "ticket"}">${th("admin.bon.kind_ticket")}</button></div>
      <div class="form bk-opts">
        ${kind === "receipt" ? `<label>${th("admin.receipt.printer")}<select id="bk-printer"><option value="0">${th("admin.receipt.off")}</option>${printers}</select></label>` : ""}
        <label>${th("admin.bon.template")}<select id="bk-tpl">${tplHtml()}</select></label>
        <label>${th("admin.bon.paper")}<select id="bk-width"><option value="48" ${width === 48 ? "selected" : ""}>${th("admin.bon.paper_80")}</option>
          <option value="32" ${width === 32 ? "selected" : ""}>${th("admin.bon.paper_58")}</option></select></label>
      </div>
    </div>
    <div class="bk">
      <section class="bk-col bk-list"><h3>${th("admin.bon.blocks")}</h3><p class="hint">${th("admin.bon.blocks_hint")}</p>
        <ol id="bk-blocks">${listHtml()}</ol>
        <div class="bk-add"><b>${th("admin.bon.add")}</b><div class="bk-addrow">${addHtml()}</div></div>
        ${kind === "receipt" ? `<p class="hint">${th("admin.receipt.rules")}</p>` : ""}
      </section>
      <section class="bk-col bk-prev"><h3>${th("admin.bon.preview")}</h3><p class="hint">${th("admin.bon.preview_tap")}</p>
        <div class="bd-paper"><div class="bk-sheet"><img id="bk-img" alt="${th("admin.bon.preview")}"><div class="bk-hits" id="bk-hits"></div></div></div>
        ${kind === "ticket" ? `<div class="bk-field"><span>${th("admin.bon.preview_when")}</span><div class="pe-sg bk-sg" role="group" aria-label="${th("admin.bon.preview_when")}">
          ${V.delivery_times.map((x) => `<button type="button" data-when="${esc(x)}" aria-pressed="${x === when}">${esc(x)}</button>`).join("")}</div></div>` : ""}
        <div class="form bk-acts"><button type="button" class="btn" data-act="sample">${th("admin.bon.sample")}</button>
          <button type="button" class="btn" data-act="reset">${th("admin.bon.reset")}</button></div>
      </section>
      <section class="bk-col bk-props" id="bk-props" aria-live="polite">${propsHtml()}</section>
    </div>
    <div class="bk-bar" id="bk-bar" ${dirty() ? "" : "hidden"}><span>${th("admin.bon.unsaved")} <span class="bk-bar-more">${th("admin.bon.unsaved_more")}</span></span>
      <span class="bk-bar-btns"><button type="button" class="btn" data-act="discard">${th("admin.bon.discard")}</button>
      <button type="button" class="btn pri" data-act="save">${th("admin.common.save")}</button></span></div>
    <input type="file" id="bk-file" accept="image/png,image/jpeg,image/webp" hidden>`;
}

function draw() {
  const el = $("#bondesign");
  if (!el) return;
  el.innerHTML = frame();
  preview();
}

/** Nach einer Änderung: Liste, Eigenschaften und Balken neu, Vorschau etwas später. focus: Eigenschaften neu zeichnen? */
function changed({ props = true, list = true } = {}) {
  if (list) {
    const ol = $("#bk-blocks");
    if (ol) ol.innerHTML = listHtml();
    const add = $(".bk-addrow");
    if (add) add.innerHTML = addHtml();
    const tpl = $("#bk-tpl");
    if (tpl) tpl.innerHTML = tplHtml();
    const box = $("#bk-hits");                      // Nummern stimmen erst mit der neuen Vorschau wieder
    if (box) box.innerHTML = "";
  }
  if (props) { const p = $("#bk-props"); if (p) p.innerHTML = propsHtml(); }
  const bar = $("#bk-bar");
  if (bar) bar.hidden = !dirty();
  clearTimeout(prevTimer);
  prevTimer = setTimeout(preview, 250);
}

// ------------------------------------------------------------------ Vorschau
async function preview() {
  const img = $("#bk-img");
  if (!img) return;
  const my = ++seq;
  const body = { kind, design: { blocks: blocks() }, width };
  if (kind === "ticket") body.delivery = when;
  const r = await api("POST", "/bon-design/preview", body, { raw: true }).catch(() => null);
  if (!r || my !== seq) return;
  try { hits = JSON.parse(r.headers.get("X-Blocks") || "[]"); } catch { hits = []; }
  const blob = await r.blob();
  if (my !== seq) return;
  if (prevUrl) URL.revokeObjectURL(prevUrl);
  prevUrl = URL.createObjectURL(blob);
  img.onload = drawHits;
  img.src = prevUrl;
}

function drawHits() {
  const img = $("#bk-img"), box = $("#bk-hits");
  if (!img || !box || !img.naturalHeight) return;
  const H = img.naturalHeight;
  box.innerHTML = hits.map(([i, a, b]) => `<button type="button" class="bk-hit${i === sel[kind] ? " sel" : ""}" data-hit="${i}"
      aria-label="${lbl(spec(blocks()[i]?.type).l)}"><span>${i + 1}</span></button>`).join("");
  $$(".bk-hit", box).forEach((el, n) => {
    const [, a, b] = hits[n];
    el.style.top = `${(a / H) * 100}%`;
    el.style.height = `${Math.max(1, ((b - a) / H) * 100)}%`;
  });
}

function select(i, { scroll = false } = {}) {
  sel[kind] = i;
  $$("#bk-blocks .bk-item").forEach((li) => { const on = +li.dataset.i === i; li.classList.toggle("sel", on); li.setAttribute("aria-current", String(on)); });
  $$(".bk-hit").forEach((h) => h.classList.toggle("sel", +h.dataset.hit === i));
  const p = $("#bk-props");
  if (p) p.innerHTML = propsHtml();
  if (scroll) $(`#bk-blocks .bk-item[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------ Bearbeiten
function bounds() {
  const list = blocks(), fx = (k) => spec(k.type).fixed;
  let lo = 0, hi = list.length - 1;
  while (lo < list.length && fx(list[lo]) === "top") lo++;
  while (hi >= 0 && fx(list[hi]) === "bottom") hi--;
  return [lo, hi];
}

const calm = () => !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Liste neu zeichnen und die Zeilen von ihrem alten Platz an den neuen gleiten lassen (▲ ▼, Alt+↑/↓, Vorschau). */
function glide(apply) {
  const before = new Map($$("#bk-blocks .bk-item").map((li) => [blocks()[+li.dataset.i], li.getBoundingClientRect().top]));
  apply();
  if (calm()) return;
  $$("#bk-blocks .bk-item").forEach((li) => {
    const old = before.get(blocks()[+li.dataset.i]);
    const dy = old == null ? 0 : old - li.getBoundingClientRect().top;
    if (!dy) return;
    li.style.transform = `translateY(${dy}px)`;
    void li.offsetHeight;                          // alter Platz zuerst zeichnen, dann gleiten
    li.classList.add("bk-slide");
    li.style.transform = "";
  });
}

/** Baustein von «from» vor die Stelle «to» (Index in der alten Liste; list.length = ans Ende) verschieben. */
function move(from, to, { animate = true } = {}) {
  const list = blocks();
  if (spec(list[from].type).fixed) return;
  const [lo, hi] = bounds();
  let dest = to > from ? to - 1 : to;
  dest = Math.max(lo, Math.min(hi, dest));
  if (dest === from) return;
  const apply = () => {
    const [k] = list.splice(from, 1);
    list.splice(dest, 0, k);
    sel[kind] = dest;
    changed();
  };
  if (animate) glide(apply); else apply();
}

function setProp(name, value) {
  const k = blocks()[sel[kind]];
  if (!k) return;
  k[name] = value;
  changed({ props: false });
}

function addBlock(typ) {
  const s = spec(typ), list = blocks();
  const k = { type: typ, on: true };
  s.props.forEach((p) => { k[p.k] = p.d; });
  const [, hi] = bounds();
  const at = Math.min(sel[kind] + 1, hi + 1);
  list.splice(Math.max(bounds()[0], at), 0, k);
  sel[kind] = list.indexOf(k);
  changed();
  $(`#bk-blocks .bk-item[data-i="${sel[kind]}"]`)?.scrollIntoView({ block: "nearest" });
}

function applyTemplate(name) {
  const tpl = V.templates[kind][name];
  if (!tpl) return;
  const texts = Object.fromEntries(blocks().filter((k) => "text" in k && (k.type === "header" || k.type === "footer")).map((k) => [k.type, k.text]));
  S[kind].blocks = JSON.parse(JSON.stringify(tpl.blocks)).map((k) => (k.type in texts ? { ...k, text: texts[k.type] } : k));
  S[kind].template = name;
  sel[kind] = Math.min(sel[kind], S[kind].blocks.length - 1);
  changed();
}

async function upload(path, file) {
  const fd = new FormData();
  fd.append("file", file);
  return guard(() => api("POST", path, fd));
}

function sampleDialog() {
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  const opts = V.printers.filter((p) => p.enabled).map((p) => `<option value="${p.id}" ${p.id === S.receipt_printer_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  dlg.innerHTML = `<h3>${th("admin.bon.sample_title")}</h3><p class="hint">${th("admin.bon.sample_hint")}</p>
    ${opts ? `<div class="form"><label>${th("admin.bon.sample_printer_pick")}<select id="bk-sp">${opts}</select></label></div>` : `<p class="hint">${th("admin.bon.sample_no_printer")}</p>`}
    <div class="form dlg-foot"><button type="button" class="btn" id="bk-sx">${th("admin.common.cancel")}</button>
      ${opts ? `<button type="button" class="btn pri" id="bk-sgo">${th("admin.bon.sample_go")}</button>` : ""}</div>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $("#bk-sx", dlg).addEventListener("click", () => dlg.close());
  $("#bk-sgo", dlg)?.addEventListener("click", async () => {
    const body = { kind, design: { blocks: blocks() }, width, printer_id: +$("#bk-sp", dlg).value };
    if (kind === "ticket") body.delivery = when;
    $("#bk-sgo", dlg).disabled = true;
    const r = await guard(() => api("POST", "/bon-design/test", body));
    if (r) { toast(t("admin.bon.sample_done", { printer: r.printer })); dlg.close(); } else $("#bk-sgo", dlg).disabled = false;
  });
  dlg.showModal();
}

// ------------------------------------------------------------------ Ziehen in der Liste (Maus und Finger)
// Die gepackte Zeile löst sich und schwebt (Kopie mit Schatten), an ihrem Platz bleibt eine gestrichelte Lücke. Die
// anderen Zeilen gleiten zur Seite, sodass die Lücke immer zeigt, wo sie landet. Am Rand scrollt die Seite mit.
const EDGE = 80;                                   // Abstand vom Rand, ab dem die Seite mitscrollt (Punkte)

function dragRows(root) {
  let d = null, busy = false;
  const shift = (g, t) => {
    const R = g.rects, f = g.from;
    return t > f ? R[t].bottom - R[f].bottom : t < f ? R[t].top - R[f].top : 0;
  };
  const update = () => {
    const dy = d.y - d.y0, R = d.rects, f = d.from;
    d.ghost.style.transform = `translateY(${dy}px) scale(1.02) rotate(-0.6deg)`;
    const cy = R[f].top + R[f].h / 2 + dy + (scrollY - d.sy0);
    let t = f;
    for (let i = d.lo; i < f; i++) if (cy < R[i].top + R[i].h / 2) { t = i; break; }
    if (t === f) for (let i = d.hi; i > f; i--) if (cy > R[i].top + R[i].h / 2) { t = i; break; }
    if (t === d.to) return;
    d.to = t;
    d.items.forEach((x, i) => {
      let s = 0;
      if (f < t && i > f && i <= t) s = -d.h;
      if (f > t && i >= t && i < f) s = d.h;
      if (x !== d.li) x.style.transform = s ? `translateY(${s}px)` : "";
    });
    d.li.style.transform = `translateY(${shift(d, t)}px)`;
  };
  const tick = () => {                             // am oberen/unteren Rand mitscrollen – nur in Zugrichtung
    if (!d) return;                                // (sonst scrollt schon das Greifen einer Zeile am Rand)
    const bar = $("#bk-bar"), bottom = bar && !bar.hidden ? bar.getBoundingClientRect().top : innerHeight;
    const up = d.y < d.y0 - 6 && d.y < EDGE, down = d.y > d.y0 + 6 && d.y > bottom - EDGE;
    const v = up ? -Math.ceil((EDGE - d.y) / 5) : down ? Math.ceil((d.y - bottom + EDGE) / 5) : 0;
    if (v) { scrollBy(0, v); update(); }
    d.raf = requestAnimationFrame(tick);
  };
  const start = () => {
    d.items = $$("#bk-blocks .bk-item");
    d.sy0 = scrollY;
    d.rects = d.items.map((x) => { const r = x.getBoundingClientRect(); return { top: r.top + scrollY, bottom: r.bottom + scrollY, h: r.height }; });
    d.r = d.li.getBoundingClientRect();
    const gap = d.rects.length > 1 ? d.rects[1].top - d.rects[0].bottom : 6;
    d.h = d.r.height + gap;
    [d.lo, d.hi] = bounds();
    const g = d.li.cloneNode(true);
    g.classList.add("bk-ghost");
    g.setAttribute("aria-hidden", "true");
    Object.assign(g.style, { left: `${d.r.left}px`, top: `${d.r.top}px`, width: `${d.r.width}px`, height: `${d.r.height}px` });
    document.body.appendChild(g);
    d.ghost = g;
    d.li.classList.add("bk-hole");
    d.items.forEach((x) => x.classList.add("bk-slide"));
    d.raf = requestAnimationFrame(tick);
  };
  root.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest("#bk-blocks .bk-grip:not(.ph)");
    if (!grip || e.button > 0 || busy) return;
    e.preventDefault();
    grip.setPointerCapture?.(e.pointerId);
    const li = grip.closest(".bk-item");
    d = { li, from: +li.dataset.i, to: +li.dataset.i, x0: e.clientX, y0: e.clientY, y: e.clientY, moved: false };
  });
  root.addEventListener("pointermove", (e) => {
    if (!d) return;
    d.y = e.clientY;
    if (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 6) return;
    e.preventDefault();
    if (!d.moved) { d.moved = true; start(); }
    update();
  });
  const end = (ok) => {
    if (!d) return;
    const g = d;
    d = null;
    if (!g.moved) return;
    cancelAnimationFrame(g.raf);
    dropped = true;                                  // der Klick gleich nach dem Loslassen wählt nichts
    setTimeout(() => { dropped = false; }, 0);
    const to = ok ? g.to : g.from;
    const target = g.rects[g.from].top + shift(g, to) - scrollY;
    busy = true;
    g.ghost.classList.add("land");                   // in die Lücke gleiten (abgebrochen: zurück)
    g.ghost.style.transform = `translateY(${target - g.r.top}px)`;
    if (!ok) g.items.forEach((x) => { x.style.transform = ""; });
    setTimeout(() => {
      g.items.forEach((x) => { x.classList.remove("bk-slide", "bk-hole"); x.style.transform = ""; });
      g.ghost.remove();
      busy = false;
      if (to !== g.from) move(g.from, to > g.from ? to + 1 : to, { animate: false });
    }, calm() ? 0 : 220);
  };
  root.addEventListener("pointerup", () => end(true));
  root.addEventListener("pointercancel", () => end(false));
}

// ------------------------------------------------------------------ Ziehen in der Vorschau (Maus)
function dragPreview(root) {
  let d = null;
  const line = () => $(".bk-drop") || Object.assign(document.createElement("div"), { className: "bk-drop" });
  root.addEventListener("pointerdown", (e) => {
    const hit = e.target.closest(".bk-hit");
    if (!hit || e.button > 0) return;
    const from = +hit.dataset.hit;
    if (spec(blocks()[from]?.type).fixed) return;
    hit.setPointerCapture?.(e.pointerId);
    d = { from, y0: e.clientY, x0: e.clientX, moved: false, to: null, src: hit };
  });
  root.addEventListener("pointermove", (e) => {
    if (!d) return;
    if (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 6) return;
    if (!d.moved) { d.moved = true; d.src.classList.add("lift"); }
    e.preventDefault();
    let best = null;
    for (const el of $$(".bk-hit")) {
      const r = el.getBoundingClientRect(), i = +el.dataset.hit;
      if (e.clientY < r.top + r.height / 2) { best = { el, to: i, before: true }; break; }
      best = { el, to: i + 1, before: false };
    }
    if (!best) return;
    d.to = best.to;
    const ln = line(), r = best.el.getBoundingClientRect(), host = best.el.offsetParent || document.body;
    const hr = host.getBoundingClientRect();
    host.appendChild(ln);
    ln.style.top = `${(best.before ? r.top : r.bottom) - hr.top + host.scrollTop - 2}px`;
    ln.style.left = `${r.left - hr.left}px`;
    ln.style.width = `${r.width}px`;
  });
  const end = (ok) => {
    if (!d) return;
    const { from, to, moved, src } = d;
    d = null;
    src.classList.remove("lift");
    $(".bk-drop")?.remove();
    if (!moved) return;
    dropped = true;
    setTimeout(() => { dropped = false; }, 0);
    if (ok && to != null) move(from, to);
  };
  root.addEventListener("pointerup", () => end(true));
  root.addEventListener("pointercancel", () => end(false));
}

// ------------------------------------------------------------------ Start und Bedienung
async function mountBonDesign() {
  const el = $("#bondesign");
  if (!el) return;
  const v = await guard(() => api("GET", "/bon-design"));
  if (!v || !el.isConnected) return;                 // inzwischen anderer Reiter (REV-048)
  const keep = S && dirty() ? S : null;          // Seite neu gezeichnet (anderer Reiter): Ungespeichertes bleibt
  fromView(v);
  if (keep) S = keep;
  draw();
  dragRows(el);
  dragPreview(el);
  el.addEventListener("click", async (e) => {
    if (dropped) return;
    const b = e.target.closest("button");
    if (!b) {
      const li = e.target.closest(".bk-item");
      if (li) select(+li.dataset.i);
      return;
    }
    if (b.dataset.kind) { if (b.dataset.kind !== kind) { kind = b.dataset.kind; draw(); } return; }
    if (b.dataset.eye) {
      const k = blocks()[+b.dataset.eye];
      k.on = !k.on;
      changed({ props: +b.dataset.eye === sel[kind] });
      return;
    }
    if (b.dataset.mv) {
      const i = +b.closest(".bk-item").dataset.i, step = +b.dataset.mv;
      move(i, step < 0 ? i - 1 : i + 2);
      $(`#bk-blocks .bk-item[data-i="${sel[kind]}"] [data-mv="${step}"]`)?.focus();
      return;
    }
    if (b.dataset.hit) { select(+b.dataset.hit, { scroll: true }); return; }
    if (b.dataset.add) { addBlock(b.dataset.add); return; }
    if (b.dataset.when) {
      when = b.dataset.when;
      $$("[data-when]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      preview();
      return;
    }
    if (b.dataset.p) {
      setProp(b.dataset.p, JSON.parse(b.dataset.v));
      $$(`[data-p="${b.dataset.p}"]`, b.parentElement).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      return;
    }
    switch (b.dataset.act) {
      case "dup": {
        const list = blocks(), i = sel[kind];
        list.splice(i + 1, 0, JSON.parse(JSON.stringify(list[i])));
        sel[kind] = i + 1;
        changed();
        break;
      }
      case "del":
        blocks().splice(sel[kind], 1);
        sel[kind] = Math.max(0, sel[kind] - 1);
        changed();
        break;
      case "reset":
        if (confirm(t("admin.bon.reset_confirm"))) applyTemplate(S[kind].template || "standard");
        break;
      case "sample": sampleDialog(); break;
      case "discard": fromView(V); draw(); break;
      case "save": {
        const r = await guard(() => api("PUT", "/bon-design", { receipt: S.receipt, ticket: S.ticket,
          receipt_printer_id: S.receipt_printer_id }), t("admin.common.saved"));
        if (r) { fromView(r); draw(); }
        break;
      }
      case "img-pick": case "logo-pick":
        $("#bk-file").dataset.for = b.dataset.act;
        $("#bk-file").click();
        break;
      case "logo-del": {
        if (!confirm(t("admin.bon.logo_remove_confirm"))) return;
        const r = await guard(() => api("DELETE", "/bon-design/logo"));
        if (r) { V.logo = r.logo; V.app_logo = r.app_logo; changed(); }
        break;
      }
      default:
    }
  });
  el.addEventListener("keydown", (e) => {
    const li = e.target.closest?.(".bk-item");
    if (!li || e.target.closest("button:not(.bk-grip)")) return;
    const i = +li.dataset.i;
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      move(i, e.key === "ArrowUp" ? i - 1 : i + 2);
      $(`#bk-blocks .bk-item[data-i="${sel[kind]}"]`)?.focus();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      select(i);
    }
  });
  el.addEventListener("input", (e) => {
    const f = e.target;
    if (f.dataset.p && (f.tagName === "INPUT" || f.tagName === "TEXTAREA") && f.type !== "checkbox") {
      const k = blocks()[sel[kind]];
      if (!k) return;
      k[f.dataset.p] = f.value;
      changed({ props: false });
    }
  });
  el.addEventListener("change", async (e) => {
    const f = e.target;
    if (f.dataset.p && f.type === "checkbox") { setProp(f.dataset.p, f.checked); return; }
    if (f.dataset.pt) {                                   // Lieferzeitpunkte: gewählte Namen als Liste
      setProp(f.dataset.pt, $$(`[data-pt="${f.dataset.pt}"]`, el).filter((x) => x.checked).map((x) => x.dataset.tv));
      return;
    }
    if (f.id === "bk-width") { width = +f.value; preview(); return; }
    if (f.id === "bk-printer") { S.receipt_printer_id = +f.value; changed({ props: false, list: false }); return; }
    if (f.id === "bk-tpl") { applyTemplate(f.value); return; }
    if (f.id === "bk-file" && f.files[0]) {
      const which = f.dataset.for, file = f.files[0];
      f.value = "";
      if (which === "logo-pick") {
        const r = await upload("/bon-design/logo", file);
        if (r) { V.logo = r.logo; V.app_logo = r.app_logo; toast(t("admin.bon.logo_saved")); changed(); }
      } else {
        const r = await upload("/bon-design/image", file);
        if (r) { setProp("image", r.id); changed(); }
      }
    }
  });
}
