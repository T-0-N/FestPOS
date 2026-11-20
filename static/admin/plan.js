/* FestPOS – Verwaltung › Tische › Tischplan: Bereiche, Grundriss, Tische frei platzieren (ziehen, grösser, drehen),
   Beschriftungen, Reihe einfügen, Rückgängig und Vorschau fürs Handy. Ohne Bibliothek (strenge CSP), Maus und Finger.
   Koordinaten wie app/plan.py: der Plan ist 1000 breit und area.ratio hoch; Tische x/y = linke obere Ecke. */
import { $, api, esc, guard, toast } from "./core.js";

const W = 1000;
const SIZE = { bench: [60, 240], rect: [120, 80], round: [90, 90] };      // neue Tische (Plan-Einheiten)
const BADGE = 40, PHONE = 328;          // Nummer am Handy: Kreis 40 px; Planbreite auf einem 360-px-Handy
const SNAP = 10;
const SHAPE_KEY = { bench: "admin.plan.shape_bench", rect: "admin.plan.shape_rect", round: "admin.plan.shape_round" };

let P = null;                 // Stand vom Server (GET /plan)
let S = null;                 // Editor: { aid, sel:Set, undo:[], redo:[], grid, phone, removed:Set }
let root = null, saveTimer = null, drag = null;

const area = () => P.areas.find((a) => a.id === S.aid);
const placed = () => P.tables.filter((x) => x.placed && x.area_id === S.aid);
const unplaced = () => P.tables.filter((x) => !x.placed);
const keyOf = (kind, id) => `${kind}${id}`;
const snap = (v) => (S.grid ? Math.round(v / SNAP) * SNAP : Math.round(v));

export async function mountPlan(el, word) {
  root = el;
  root.dataset.word = word.many;
  const p = await guard(() => api("GET", "/plan"));
  if (!p) return;
  P = p;
  S = { aid: P.areas[0]?.id ?? null, sel: new Set(), undo: [], redo: [], grid: true, phone: false, removed: new Set() };
  draw();
}

// ------------------------------------------------------------------ Zeichnen
function draw() {
  const a = area();
  root.innerHTML = `<h3>${th("admin.plan.title")}</h3>
    <p class="hint">${th("admin.plan.intro")}</p>
    <div class="pe-areas">${P.areas.map((x) => `<button class="btn sm${x.id === S.aid ? " on" : ""}" data-area="${x.id}">${esc(x.name)} · ${P.tables.filter((t) => t.placed && t.area_id === x.id).length}</button>`).join("")}
      <button class="btn sm" data-act="area-add">${th("admin.plan.area_add")}</button></div>
    ${a ? editorHTML(a) : `<p class="hint">${th("admin.plan.no_area")}</p>`}
    ${startHTML()}`;
  if (a) paint();
}

function editorHTML(a) {
  return `<div class="pe-bar">
      <button class="btn sm" data-act="img-pick">${th(a.image ? "admin.plan.image_replace" : "admin.plan.image_add")}</button>
      <input type="file" id="pe-file" accept="image/png,image/jpeg,image/webp" hidden>
      ${a.image ? `<label class="pe-op">${th("admin.plan.opacity")} <input type="range" id="pe-op" min="10" max="100" step="5" value="${a.opacity}"></label>
        <button class="btn sm" data-act="img-del">${th("admin.plan.image_remove")}</button>` : ""}
      <span class="pe-sep"></span>
      <button class="btn sm${S.grid ? " on" : ""}" data-act="grid" aria-pressed="${S.grid}">${th("admin.plan.grid")}</button>
      <button class="btn sm${S.phone ? " on" : ""}" data-act="phone" aria-pressed="${S.phone}">${th("admin.plan.phone_view")}</button>
      <span class="pe-sep"></span>
      <button class="btn sm" data-act="undo" ${S.undo.length ? "" : "disabled"}>${th("admin.plan.undo")}</button>
      <button class="btn sm" data-act="redo" ${S.redo.length ? "" : "disabled"} aria-label="${th("admin.plan.redo")}">↷</button>
      <span class="pe-state" id="pe-state"></span>
    </div>
    <div class="pe">
      <div class="pe-left">
        <div class="pe-cv${S.phone ? " phone" : ""}${S.grid ? " grid" : ""}" id="pe-cv" tabindex="0" style="aspect-ratio:${W} / ${a.ratio};--op:${a.opacity / 100}"
          aria-label="${th("admin.plan.canvas", { name: a.name })}">
          ${a.image ? `<img src="${esc(a.image)}" alt="" draggable="false">` : ""}<div id="pe-items"></div><div class="pe-lasso" id="pe-lasso" hidden></div>
        </div>
        <p class="hint">${th("admin.plan.how")}</p>
      </div>
      <div class="pe-side" id="pe-side"></div>
    </div>`;
}

function startHTML() {
  const opts = [["plan", "admin.plan.start_plan", "admin.plan.start_plan_hint"], ["number", "admin.plan.start_number", "admin.plan.start_number_hint"],
    ["off", "admin.plan.start_off", "admin.plan.start_off_hint"]];
  return `<div class="pe-start"><h4>${th("admin.plan.start_title")}</h4>
    ${opts.map(([v, l, h]) => `<label class="check"><input type="radio" name="pe-start" value="${v}" ${P.start === v ? "checked" : ""}> <b>${th(l)}</b>&nbsp;– ${th(h)}</label>`).join("")}</div>`;
}

// Tische und Beschriftungen in % der Planfläche (so passt es zu jeder Breite des Editors)
function paint() {
  const a = area(), items = $("#pe-items", root);
  if (!items) return;
  const pct = (v, total) => `${(v / total) * 100}%`;
  const one = S.sel.size === 1;
  const lab = a.labels.map((l, i) => {
    const k = keyOf("l", i), sel = S.sel.has(k);
    return `<div class="pe-l${sel ? " sel" : ""}" data-k="${k}" style="left:${pct(l.x, W)};top:${pct(l.y, a.ratio)};width:${pct(l.w, W)};height:${pct(l.h, a.ratio)}">
      <span>${esc(l.t)}</span>${sel && one ? `<i class="hd" data-h="se"></i>` : ""}</div>`;
  });
  const tbl = placed().map((x) => {
    const k = keyOf("t", x.id), sel = S.sel.has(k);
    return `<div class="pe-t ${x.shape}${sel ? " sel" : ""}" data-k="${k}" style="left:${pct(x.x, W)};top:${pct(x.y, a.ratio)};width:${pct(x.w, W)};height:${pct(x.h, a.ratio)}">
      <i class="sh" style="transform:rotate(${x.rot}deg)"></i><b>${esc(x.label)}</b>
      ${sel && one ? `<i class="hd" data-h="se"></i><i class="rt" data-h="rot"></i>` : ""}</div>`;
  });
  items.innerHTML = lab.join("") + tbl.join("");
  root.querySelectorAll(".pe-areas [data-area]").forEach((b) => {
    const x = P.areas.find((ar) => ar.id === +b.dataset.area);
    if (x) b.textContent = `${x.name} · ${P.tables.filter((t2) => t2.placed && t2.area_id === x.id).length}`;
  });
  // Handy-Ansicht: Nummern so gross wie am Handy (360 px) – zeigt, wo es eng wird
  const cv = $("#pe-cv", root);
  cv.style.setProperty("--badge", `${(BADGE / PHONE) * cv.clientWidth}px`);
  side();
}

function side() {
  const el = $("#pe-side", root);
  if (!el) return;
  const sel = [...S.sel];
  const tables = sel.filter((k) => k[0] === "t").map((k) => P.tables.find((x) => keyOf("t", x.id) === k)).filter(Boolean);
  const labels = sel.filter((k) => k[0] === "l").map((k) => area().labels[+k.slice(1)]).filter(Boolean);
  let box = "";
  if (tables.length === 1 && !labels.length) {
    const x = tables[0];
    box = `<div class="pe-box"><h4>${th("admin.plan.table", { label: x.label })}</h4>
      <div class="pe-row"><span>${th("admin.plan.shape")}</span><div class="pe-sg">${["bench", "rect", "round"].map((s) =>
        `<button data-shape="${s}" aria-pressed="${x.shape === s}">${th(SHAPE_KEY[s])}</button>`).join("")}</div></div>
      <div class="pe-row"><span>${th("admin.plan.size")}</span>
        <label>${th("admin.plan.width")} <input type="number" class="in" id="pe-w" min="10" max="1000" value="${x.w}"></label>
        <label>${th("admin.plan.height")} <input type="number" class="in" id="pe-h" min="10" max="4000" value="${x.h}"></label></div>
      <div class="pe-row"><span>${th("admin.plan.rotate")}</span><div class="pe-sg">${[0, 30, 45, 90, -45].map((r) =>
        `<button data-rot="${r}" aria-pressed="${x.rot === r}">${r}°</button>`).join("")}</div>
        <input type="number" class="in" id="pe-r" min="-180" max="180" value="${x.rot}" aria-label="${th("admin.plan.rotate")}"></div>
      <div class="pe-row"><button class="btn sm" data-act="remove">${th("admin.plan.remove")}</button></div></div>`;
  } else if (labels.length === 1 && !tables.length) {
    const l = labels[0];
    box = `<div class="pe-box"><h4>${th("admin.plan.label")}</h4>
      <div class="pe-row"><label>${th("admin.plan.label_text")} <input class="in" id="pe-lt" maxlength="30" value="${esc(l.t)}"></label></div>
      <div class="pe-row"><button class="btn sm danger" data-act="remove">${th("admin.plan.label_delete")}</button></div></div>`;
  } else if (sel.length > 1) {
    box = `<div class="pe-box"><h4>${th("admin.plan.selected", { count: sel.length })}</h4>
      <div class="pe-row"><span>${th("admin.plan.align")}</span>
        <button class="btn sm" data-align="left">${th("admin.plan.align_left")}</button>
        <button class="btn sm" data-align="top">${th("admin.plan.align_top")}</button>
        <button class="btn sm" data-align="hspace">${th("admin.plan.align_hspace")}</button>
        <button class="btn sm" data-align="vspace">${th("admin.plan.align_vspace")}</button></div>
      <div class="pe-row"><button class="btn sm" data-act="remove">${th("admin.plan.remove")}</button></div></div>`;
  }
  const free = unplaced();
  const near = crowded(1), bad = crowded(2).slice(0, 6);
  el.innerHTML = `${box}
    <div class="pe-box"><h4>${th("admin.plan.add_title")}</h4>
      <div class="pe-row"><span>${th("admin.plan.unplaced")}</span>${free.length
        ? `<div class="chips pe-free">${free.map((x) => `<button class="btn sm" data-place="${x.id}">${esc(x.label)}</button>`).join("")}</div>`
        : `<span class="hint" style="margin:0">${th("admin.plan.unplaced_none")}</span>`}</div>
      ${free.length > 1 ? `<div class="pe-row"><span></span><button class="btn sm" data-act="place-all">${th("admin.plan.place_all", { count: free.length })}</button></div>` : ""}
      <form class="pe-row" id="pe-rowform"><span>${th("admin.plan.row")}</span>
        <label>${th("admin.plan.row_count")} <input type="number" class="in" name="n" min="1" max="30" value="5"></label>
        <label>${th("admin.plan.row_from")} <input class="in" name="from" maxlength="12" value="${nextNumber()}" style="width:70px"></label>
        <select class="in" name="dir"><option value="h">${th("admin.plan.row_h")}</option><option value="v">${th("admin.plan.row_v")}</option></select>
        <select class="in" name="shape">${["rect", "bench", "round"].map((s) => `<option value="${s}">${th(SHAPE_KEY[s])}</option>`).join("")}</select>
        <button class="btn sm">${th("admin.plan.row_add")}</button></form>
      <div class="pe-row"><span>${th("admin.plan.labels")}</span><button class="btn sm" data-act="label-add">${th("admin.plan.label_add")}</button></div>
    </div>
    <div class="pe-box"><h4>${th("admin.plan.check_title")}</h4>
      <p class="hint" style="margin:0 0 6px">${th(!placed().length ? "admin.plan.check_empty" : near.length ? "admin.plan.check_near" : "admin.plan.check_ok",
        { count: new Set(near.flat()).size })}</p>
      ${bad.length ? `<ul class="pe-warn">${bad.map((w) => `<li>${th("admin.plan.check_close", { a: w[0], b: w[1] })}</li>`).join("")}</ul>
        <p class="hint" style="margin:0">${th("admin.plan.check_close_hint")}</p>` : ""}
    </div>
    <div class="pe-box"><h4>${th("admin.plan.area_title", { name: area().name })}</h4>
      <div class="pe-row"><label>${th("admin.plan.area_name")} <input class="in" id="pe-an" maxlength="40" value="${esc(area().name)}"></label>
        <button class="btn sm danger" data-act="area-del">${th("admin.plan.area_delete")}</button></div></div>`;
}

// Nummern, die am Handy (360 px) übereinander lägen: Mittelpunkte näher als ein Kreis. zoom 1 = ganzer Plan,
// 2 = doppelt gezoomt (geht mit zwei Fingern oder Doppeltipp). Liefert die Paare.
function crowded(zoom = 1) {
  const px = (PHONE * zoom) / W, out = [];
  const pts = placed().map((x) => ({ l: x.label, cx: (x.x + x.w / 2) * px, cy: (x.y + x.h / 2) * px }));
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      if (Math.abs(pts[i].cx - pts[j].cx) < BADGE + 2 && Math.abs(pts[i].cy - pts[j].cy) < BADGE + 2) out.push([pts[i].l, pts[j].l]);
    }
  }
  return out;
}

function nextNumber() {
  const nums = P.tables.map((x) => x.label).filter((l) => /^\d+$/.test(l)).map(Number);
  return String(nums.length ? Math.max(...nums) + 1 : 1);
}

// ------------------------------------------------------------------ Ändern, Rückgängig, Speichern
const snapshot = () => JSON.stringify({ t: P.tables.map(({ id, x, y, w, h, rot, shape, placed, area_id }) => ({ id, x, y, w, h, rot, shape, placed, area_id })),
  l: area().labels, removed: [...S.removed] });
function restore(json) {
  const s = JSON.parse(json);
  s.t.forEach((v) => { const x = P.tables.find((t) => t.id === v.id); if (x) Object.assign(x, v); });
  area().labels = s.l;
  S.removed = new Set(s.removed);
}
function change(fn, { now = false } = {}) {
  S.undo.push(snapshot());
  if (S.undo.length > 100) S.undo.shift();
  S.redo = [];
  fn();
  paint();
  barState();
  save(now);
}
function barState() {
  const u = root.querySelector('[data-act="undo"]'), r = root.querySelector('[data-act="redo"]');
  if (u) u.disabled = !S.undo.length;
  if (r) r.disabled = !S.redo.length;
}
function save(now = false) {
  clearTimeout(saveTimer);
  const st = $("#pe-state", root);
  if (st) { st.textContent = t("admin.plan.saving"); st.className = "pe-state"; }
  saveTimer = setTimeout(flush, now ? 0 : 500);
}
async function flush() {
  const a = area();
  if (!a) return;
  const body = { labels: a.labels, tables: placed().map(({ id, x, y, w, h, rot, shape }) => ({ id, x, y, w, h, rot, shape })),
    remove: [...S.removed] };
  const p = await guard(() => api("PUT", `/plan/areas/${a.id}`, body));
  const st = $("#pe-state", root);
  if (!p) { if (st) { st.textContent = t("admin.plan.save_failed"); st.className = "pe-state bad"; } return; }
  S.removed.clear();
  if (st) { st.textContent = t("admin.plan.saved"); st.className = "pe-state ok"; }
}

// freie Stelle für neue Tische: Mitte, dann versetzt
function freeSpot(w, h, n = 0) {
  const a = area();
  const cx = W / 2 - w / 2 + ((n % 6) - 2.5) * 40, cy = a.ratio / 2 - h / 2 + Math.floor(n / 6) * 40;
  return [snap(Math.max(0, Math.min(W - w, cx))), snap(Math.max(0, Math.min(a.ratio - h, cy)))];
}
function place(x, n = 0, shape = "rect") {
  const [w, h] = SIZE[shape];
  const [px, py] = freeSpot(w, h, n);
  Object.assign(x, { placed: true, area_id: S.aid, x: px, y: py, w, h, rot: 0, shape });
  S.removed.delete(x.id);
}

// ------------------------------------------------------------------ Ziehen, Grösse, Drehen, Rahmen
function toPlan(e) {
  const r = $("#pe-cv", root).getBoundingClientRect(), a = area();
  return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * a.ratio];
}
function itemOf(k) {
  return k[0] === "t" ? P.tables.find((x) => keyOf("t", x.id) === k) : area().labels[+k.slice(1)];
}

function onDown(e) {
  if (e.button > 0) return;
  const cv = $("#pe-cv", root);
  if (!cv?.contains(e.target)) return;
  cv.focus({ preventScroll: true });
  const [px, py] = toPlan(e);
  const hit = e.target.closest("[data-k]"), handle = e.target.closest("[data-h]");
  e.preventDefault();
  cv.setPointerCapture?.(e.pointerId);
  if (handle && hit) {
    const it = itemOf(hit.dataset.k);
    drag = { mode: handle.dataset.h, k: hit.dataset.k, start: [px, py], orig: { ...it }, before: snapshot(), moved: false };
    return;
  }
  if (hit) {
    const k = hit.dataset.k;
    if (e.shiftKey || e.ctrlKey || e.metaKey) { S.sel.has(k) ? S.sel.delete(k) : S.sel.add(k); paint(); return; }
    if (!S.sel.has(k)) { S.sel = new Set([k]); paint(); }
    drag = { mode: "move", start: [px, py], orig: [...S.sel].map((key) => [key, { ...itemOf(key) }]), before: snapshot(), moved: false };
    return;
  }
  if (!e.shiftKey) S.sel.clear();
  drag = { mode: "lasso", start: [px, py] };
  paint();
}

function onMove(e) {
  if (!drag) return;
  const [px, py] = toPlan(e), a = area();
  const dx = px - drag.start[0], dy = py - drag.start[1];
  if (!drag.moved && Math.hypot(dx, dy) < 3) return;
  drag.moved = true;
  if (drag.mode === "move") {
    for (const [k, o] of drag.orig) {
      const it = itemOf(k);
      it.x = snap(Math.max(-o.w / 2, Math.min(W - o.w / 2, o.x + dx)));
      it.y = snap(Math.max(-o.h / 2, Math.min(a.ratio - o.h / 2, o.y + dy)));
    }
  } else if (drag.mode === "se") {
    const it = itemOf(drag.k), o = drag.orig, rad = ((o.rot || 0) * Math.PI) / 180;
    const lx = dx * Math.cos(rad) + dy * Math.sin(rad), ly = -dx * Math.sin(rad) + dy * Math.cos(rad);   // in Tischrichtung
    it.w = Math.max(20, snap(o.w + lx));
    it.h = Math.max(20, snap(o.h + ly));
    if (it.shape === "round") it.h = it.w;
  } else if (drag.mode === "rot") {
    const it = itemOf(drag.k), o = drag.orig;
    const cx = o.x + o.w / 2, cy = o.y + o.h / 2;
    let deg = (Math.atan2(px - cx, -(py - cy)) * 180) / Math.PI;
    deg = e.shiftKey ? Math.round(deg) : Math.round(deg / 15) * 15;
    it.rot = deg > 180 ? deg - 360 : deg;
  } else if (drag.mode === "lasso") {
    const box = $("#pe-lasso", root), x0 = Math.min(px, drag.start[0]), y0 = Math.min(py, drag.start[1]);
    const x1 = Math.max(px, drag.start[0]), y1 = Math.max(py, drag.start[1]);
    Object.assign(box.style, { left: `${(x0 / W) * 100}%`, top: `${(y0 / a.ratio) * 100}%`, width: `${((x1 - x0) / W) * 100}%`, height: `${((y1 - y0) / a.ratio) * 100}%` });
    box.hidden = false;
    drag.box = [x0, y0, x1, y1];
    return;
  }
  paint();
}

function onUp() {
  if (!drag) return;
  const d = drag;
  drag = null;
  if (d.mode === "lasso") {
    $("#pe-lasso", root).hidden = true;
    if (d.box) {
      const [x0, y0, x1, y1] = d.box, inside = (it) => { const cx = it.x + it.w / 2, cy = it.y + it.h / 2; return cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1; };
      placed().forEach((x) => { if (inside(x)) S.sel.add(keyOf("t", x.id)); });
      area().labels.forEach((l, i) => { if (inside(l)) S.sel.add(keyOf("l", i)); });
      paint();
    }
    return;
  }
  if (!d.moved) return;
  S.undo.push(d.before);
  S.redo = [];
  barState();
  save();
}

function onKey(e) {
  if (e.target.closest("input, select, textarea")) return;
  const z = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z";
  if (z) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (!S.sel.size) return;
  const step = e.shiftKey ? 10 : 2, mv = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
  if (mv) {
    e.preventDefault();
    change(() => [...S.sel].forEach((k) => { const it = itemOf(k); it.x += mv[0]; it.y += mv[1]; }));
  } else if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    removeSelected();
  } else if (e.key === "Escape") {
    S.sel.clear(); paint();
  }
}

function undo() {
  if (!S.undo.length) return;
  S.redo.push(snapshot());
  restore(S.undo.pop());
  S.sel.clear();
  paint(); barState(); save();
}
function redo() {
  if (!S.redo.length) return;
  S.undo.push(snapshot());
  restore(S.redo.pop());
  S.sel.clear();
  paint(); barState(); save();
}

function removeSelected() {
  const keys = [...S.sel];
  change(() => {
    const labelIdx = keys.filter((k) => k[0] === "l").map((k) => +k.slice(1)).sort((a, b) => b - a);
    labelIdx.forEach((i) => area().labels.splice(i, 1));
    keys.filter((k) => k[0] === "t").forEach((k) => {
      const x = itemOf(k);
      if (x) { Object.assign(x, { placed: false, area_id: null }); S.removed.add(x.id); }
    });
    S.sel.clear();
  });
}

function align(how) {
  const its = [...S.sel].map(itemOf).filter(Boolean);
  if (its.length < 2) return;
  change(() => {
    if (how === "left") { const v = Math.min(...its.map((i) => i.x)); its.forEach((i) => { i.x = v; }); }
    if (how === "top") { const v = Math.min(...its.map((i) => i.y)); its.forEach((i) => { i.y = v; }); }
    if (how === "hspace" || how === "vspace") {
      const k = how === "hspace" ? "x" : "y", s = how === "hspace" ? "w" : "h";
      const sorted = [...its].sort((a, b) => a[k] - b[k]);
      const first = sorted[0][k], lastEnd = sorted.at(-1)[k] + sorted.at(-1)[s];
      const total = sorted.reduce((n, i) => n + i[s], 0), gap = (lastEnd - first - total) / (sorted.length - 1);
      let pos = first;
      sorted.forEach((i) => { i[k] = Math.round(pos); pos += i[s] + gap; });
    }
  });
}

// ------------------------------------------------------------------ Reihe einfügen (legt fehlende Tische an)
async function addRow(f) {
  const n = Math.max(1, Math.min(30, +f.n.value || 1)), from = f.from.value.trim(), shape = f.shape.value, dir = f.dir.value;
  const m = from.match(/^(.*?)(\d+)$/);
  if (!m) { toast(t("admin.plan.row_bad"), true); return; }
  const labels = Array.from({ length: n }, (_, i) => `${m[1]}${+m[2] + i}`);
  const missing = labels.filter((l) => !P.tables.some((x) => x.label.toLowerCase() === l.toLowerCase()));
  if (missing.length && !(await guard(() => api("POST", "/tables", { labels: missing })))) return;
  const fresh = await guard(() => api("GET", "/plan"));
  if (!fresh) return;
  P.tables = fresh.tables.map((x) => ({ ...x, ...(P.tables.find((o) => o.id === x.id) || {}) }));   // ungespeicherte Lage behalten
  const [w, h] = SIZE[shape], gap = shape === "bench" ? 70 : 30, a = area();   // Festbänke: Platz für die Bänke
  const span = n * (dir === "h" ? w : h) + (n - 1) * gap;
  let x0 = dir === "h" ? (W - span) / 2 : W / 2 - w / 2, y0 = dir === "h" ? a.ratio / 2 - h / 2 : (a.ratio - span) / 2;
  x0 = Math.max(0, x0); y0 = Math.max(0, y0);
  change(() => {
    S.sel.clear();
    labels.forEach((l, i) => {
      const x = P.tables.find((t2) => t2.label.toLowerCase() === l.toLowerCase());
      if (!x) return;
      Object.assign(x, { placed: true, area_id: S.aid, w, h, rot: 0, shape,
        x: snap(dir === "h" ? x0 + i * (w + gap) : x0), y: snap(dir === "h" ? y0 : y0 + i * (h + gap)) });
      S.removed.delete(x.id);
      S.sel.add(keyOf("t", x.id));
    });
  }, { now: true });
  toast(t("admin.plan.row_done", { count: n }));
}

// ------------------------------------------------------------------ Ereignisse
function bind() {
  root.addEventListener("pointerdown", onDown);
  root.addEventListener("pointermove", onMove);
  root.addEventListener("pointerup", onUp);
  root.addEventListener("pointercancel", onUp);
  root.addEventListener("keydown", onKey);
  root.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || !root.contains(b)) return;
    if (b.dataset.area) { await flushNow(); S.aid = +b.dataset.area; S.sel.clear(); S.undo = []; S.redo = []; draw(); return; }
    if (b.dataset.place) { const x = P.tables.find((t2) => t2.id === +b.dataset.place); change(() => { place(x, placed().length); S.sel = new Set([keyOf("t", x.id)]); }); return; }
    if (b.dataset.shape) {
      const x = itemOf([...S.sel][0]);
      change(() => { const [w, h] = SIZE[b.dataset.shape]; Object.assign(x, { shape: b.dataset.shape, w, h }); });
      return;
    }
    if (b.dataset.rot !== undefined) { change(() => { itemOf([...S.sel][0]).rot = +b.dataset.rot; }); return; }
    if (b.dataset.align) { align(b.dataset.align); return; }
    switch (b.dataset.act) {
      case "area-add": {
        const name = prompt(t("admin.plan.area_prompt"));
        if (!name?.trim()) return;
        await flushNow();
        const p = await guard(() => api("POST", "/plan/areas", { name }));
        if (p) { P = p; S.aid = P.areas.at(-1).id; S.sel.clear(); S.undo = []; S.redo = []; draw(); }
        return;
      }
      case "area-del": {
        if (!confirm(t("admin.plan.area_delete_confirm", { name: area().name }))) return;
        const p = await guard(() => api("DELETE", `/plan/areas/${S.aid}`));
        if (p) { P = p; S.aid = P.areas[0]?.id ?? null; S.sel.clear(); S.undo = []; S.redo = []; draw(); }
        return;
      }
      case "img-pick": $("#pe-file", root).click(); return;
      case "img-del": {
        if (!confirm(t("admin.plan.image_remove_confirm"))) return;
        await flushNow();
        const p = await guard(() => api("DELETE", `/plan/areas/${S.aid}/image`));
        if (p) { P = p; draw(); }
        return;
      }
      case "grid": S.grid = !S.grid; draw(); return;
      case "phone": S.phone = !S.phone; draw(); return;
      case "undo": undo(); return;
      case "redo": redo(); return;
      case "remove": removeSelected(); return;
      case "place-all": change(() => { const base = placed().length; unplaced().forEach((x, i) => place(x, base + i)); }); return;
      case "label-add": {
        const a = area();
        change(() => { a.labels.push({ t: t("admin.plan.label_new"), x: snap(W / 2 - 150), y: snap(a.ratio / 2 - 40), w: 300, h: 80 }); S.sel = new Set([keyOf("l", a.labels.length - 1)]); });
        return;
      }
      default:
    }
  });
  root.addEventListener("change", async (e) => {
    const el = e.target;
    if (el.id === "pe-file" && el.files[0]) {
      await flushNow();
      const fd = new FormData(); fd.append("file", el.files[0]);
      const p = await guard(() => api("POST", `/plan/areas/${S.aid}/image`, fd), t("admin.plan.image_saved"));
      if (p) { P = p; draw(); }
      return;
    }
    if (el.name === "pe-start") { const p = await guard(() => api("PUT", "/plan/settings", { start: el.value }), t("admin.common.saved")); if (p) P.start = p.start; return; }
    if (el.id === "pe-op") {
      const v = +el.value;
      const p = await guard(() => api("PUT", `/plan/areas/${S.aid}`, { opacity: v }));
      if (p) { area().opacity = v; $("#pe-cv", root).style.setProperty("--op", v / 100); }
      return;
    }
    if (el.id === "pe-an") {
      const name = el.value.trim();
      if (!name || name === area().name) return;
      const p = await guard(() => api("PUT", `/plan/areas/${S.aid}`, { name }), t("admin.common.saved"));
      if (p) { area().name = name; draw(); }
      return;
    }
    const it = S.sel.size === 1 ? itemOf([...S.sel][0]) : null;
    if (!it) return;
    if (el.id === "pe-w" || el.id === "pe-h") change(() => { it[el.id === "pe-w" ? "w" : "h"] = Math.max(10, Math.min(4000, Math.round(+el.value) || 10)); if (it.shape === "round") it.h = it.w; });
    if (el.id === "pe-r") change(() => { it.rot = Math.max(-180, Math.min(180, Math.round(+el.value) || 0)); });
    if (el.id === "pe-lt" && el.value.trim()) change(() => { it.t = el.value.trim().slice(0, 30); });
  });
  root.addEventListener("submit", (e) => {
    if (e.target.id !== "pe-rowform") return;
    e.preventDefault();
    addRow(e.target);
  });
}
let resizeBound = false;

async function flushNow() {
  if (!saveTimer) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  await flush();
}

export function planCard() {
  return `<div class="card pe-card" id="plancard"><p class="hint">${th("admin.common.loading")}</p></div>`;
}

export async function startPlan(word) {
  const el = $("#plancard");
  if (!el) return;
  await mountPlan(el, word);
  bind();
  if (!resizeBound) {
    resizeBound = true;
    window.addEventListener("resize", () => { if (root?.isConnected && S?.aid && area()) paint(); });
  }
}
