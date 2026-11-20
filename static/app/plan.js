/* FestPOS – Bestell-App › 1. Tischwahl im Plan (Tischplan aus der Verwaltung, app/plan.py).
   Umschalter «Plan | Nummer» oben (das Handy merkt sich die Wahl), Bereiche als Chips, Plan mit Grundriss.
   Zoomen mit zwei Fingern, Doppeltipp oder + / −, verschieben mit einem Finger. Die Tischnummer bleibt immer ein
   Kreis von 40 px (gut antippbar); liegen mehrere unter dem Finger, fragt die App, welcher Tisch gemeint ist. */
"use strict";

const PLAN_BADGE = 40, PLAN_ZMAX = 4, PLAN_SLACK = 64;
const planCfg = () => (S.boot?.plan?.areas?.length ? S.boot.plan : null);
const planAreas = () => planCfg()?.areas || [];
// Ansicht der Tischwahl: «plan» oder «number»; zuerst wie in der Verwaltung eingestellt, danach die letzte Wahl
function tableViewMode() {
  if (!planCfg() || S.tour) return "number";
  const v = store.get("tableView", null);
  return v === "plan" || v === "number" ? v : (planCfg().start === "number" ? "number" : "plan");
}
function planArea() {
  const all = planAreas(), id = store.get("planArea", null);
  return all.find((a) => a.id === id) || all[0];
}
let PZ = { z: 1, x: 0, y: 0, area: null };          // Zoom und Verschiebung (Pixel) des gezeigten Bereichs

function viewSwitchHTML(mode) {
  if (!planCfg() || S.tour) return "";
  return `<div class="vw" role="group" aria-label="${th("app.plan.switch")}">
    <button data-act="table-view" data-v="plan" aria-pressed="${mode === "plan"}">${ICON.map}${th("app.plan.plan")}</button>
    <button data-act="table-view" data-v="number" aria-pressed="${mode === "number"}">${ICON.pad}${th("app.plan.number")}</button></div>`;
}

function renderPlanTable() {
  const w = W(), a = planArea(), areas = planAreas();
  if (PZ.area !== a.id) PZ = { z: 1, x: 0, y: 0, area: a.id };
  const pct = (v, total) => `${(v / total) * 100}%`;
  const labels = (a.labels || []).map((l) => `<div class="pl-fx" style="left:${pct(l.x, 1000)};top:${pct(l.y, a.ratio)};width:${pct(l.w, 1000)};height:${pct(l.h, a.ratio)}"><span>${esc(l.t)}</span></div>`).join("");
  const tables = a.tables.map((x) => { const st = tabClass(x.l);             // offener Tisch (tabs.js)
    return `<button class="pl-t ${x.s} ${st.cls}" data-act="plan-table" data-t="${esc(x.l)}"
      style="left:${pct(x.x, 1000)};top:${pct(x.y, a.ratio)};width:${pct(x.w, 1000)};height:${pct(x.h, a.ratio)}"
      aria-label="${esc(`${w.title} ${x.l}`)}"><i style="transform:rotate(${x.r || 0}deg)"></i><b>${esc(x.l)}</b>${st.sub ? `<small>${esc(st.sub)}</small>` : ""}</button>`; }).join("");
  app.innerHTML = `<section class="view table-view plan-view">
    ${header(esc(w.title), { menu: true })}
    ${certBanner()}
    ${viewSwitchHTML("plan")}
    ${areas.length > 1 ? `<div class="recent pl-areas">${areas.map((x) => `<button class="chip" data-act="plan-area" data-id="${x.id}" aria-pressed="${x.id === a.id}">${esc(x.name)}</button>`).join("")}</div>` : ""}
    <div class="pl-wrap" id="plwrap">
      <div class="pl" id="pl" style="--op:${(a.opacity ?? 50) / 100}">
        ${a.image ? `<img src="${esc(a.image)}" alt="" draggable="false">` : ""}${labels}${tables}
      </div>
      <div class="pl-zoom">
        <button data-act="plan-zoom" data-d="in" aria-label="${th("app.plan.zoom_in")}">${ICON.plus}</button>
        <button data-act="plan-zoom" data-d="out" aria-label="${th("app.plan.zoom_out")}">${ICON.minus}</button>
        <button data-act="plan-zoom" data-d="fit" aria-label="${th("app.plan.zoom_fit")}">${ICON.fit}</button>
      </div>
    </div>
    ${tabLegendHTML()}
    ${openTableHTML()}
    <button class="go" disabled>${th("app.plan.tap", { word: w.one })}</button>
  </section>`;
  layoutPlan();
  loadTabs();
}

// Grösse des Plans: passt ganz in die Fläche (Zoom 1), mal Zoom; Verschiebung begrenzen, damit er nie verschwindet
function planBase() {
  const wrap = $("#plwrap"), a = planArea();
  if (!wrap || !a) return null;
  const W0 = wrap.clientWidth, H0 = wrap.clientHeight;
  const k = Math.min(W0 / 1000, H0 / a.ratio);
  return { W0, H0, w: 1000 * k, h: a.ratio * k };
}
function layoutPlan() {
  const pl = $("#pl"), b = planBase();
  if (!pl || !b || !b.w) return;
  const w = b.w * PZ.z, h = b.h * PZ.z;
  // ganzer Plan (Zoom 1): zentriert. Gezoomt: frei verschieben, auch in der Richtung, in der er noch nicht die ganze
  // Fläche füllt – höchstens so weit, dass ein Rand von PLAN_SLACK bleibt (Tische unter den Zoom-Knöpfen erreichbar)
  const lim = (pos, size, room) => Math.min(Math.max(0, room - size) + PLAN_SLACK, Math.max(Math.min(0, room - size) - PLAN_SLACK, pos));
  if (PZ.z <= 1.001) { PZ.x = (b.W0 - w) / 2; PZ.y = (b.H0 - h) / 2; }
  else { PZ.x = lim(PZ.x, w, b.W0); PZ.y = lim(PZ.y, h, b.H0); }
  Object.assign(pl.style, { width: `${w}px`, height: `${h}px`, left: `${PZ.x}px`, top: `${PZ.y}px` });
  const zo = $(".pl-zoom");
  if (zo) {
    zo.querySelector('[data-d="in"]').disabled = PZ.z >= PLAN_ZMAX;
    zo.querySelector('[data-d="out"]').disabled = PZ.z <= 1;
    zo.querySelector('[data-d="fit"]').disabled = PZ.z <= 1;
  }
}
// um den Punkt (cx, cy) in der Fläche zoomen: dieser Punkt bleibt unter dem Finger
function zoomAt(z, cx, cy) {
  const b = planBase();
  if (!b) return;
  z = Math.max(1, Math.min(PLAN_ZMAX, z));
  const k = z / PZ.z;
  PZ.x = cx - (cx - PZ.x) * k;
  PZ.y = cy - (cy - PZ.y) * k;
  PZ.z = z;
  layoutPlan();
}
function planZoomBtn(d) {
  const b = planBase();
  if (!b) return;
  if (d === "fit") { PZ.z = 1; layoutPlan(); return; }
  zoomAt(d === "in" ? PZ.z * 1.6 : PZ.z / 1.6, b.W0 / 2, b.H0 / 2);
}

// Tisch antippen: alle Tische, deren Nummer oder Fläche unter dem Finger liegt; mehrere -> nachfragen
function planTap(e, btn) {
  if (Date.now() - planGesture.endedAt < 350) return;      // Ende eines Verschiebens/Zooms ist kein Antippen
  const hits = [];
  document.querySelectorAll("#pl .pl-t").forEach((el) => {
    const b = el.querySelector("b").getBoundingClientRect(), r = el.getBoundingClientRect();
    const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
    const d = Math.hypot(e.clientX - cx, e.clientY - cy);
    if (d <= PLAN_BADGE / 2 + 4 || (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom)) hits.push([d, el.dataset.t]);
  });
  hits.sort((x, y) => x[0] - y[0]);
  const labels = [...new Set(hits.map((h) => h[1]))];
  if (labels.length > 1 && hits[1][0] - hits[0][0] < 14) {  // nicht klar näher bei einer Nummer: fragen
    buzz([20, 30, 20]);
    const w = W();
    openSheet(`<h2>${th("app.plan.which", { word: w.one })}</h2>
      <div class="actions pl-which">${labels.slice(0, 6).map((l) => `<button class="alt" data-act="pick-table" data-t="${esc(l)}">${esc(`${w.title} ${l}`)}</button>`).join("")}</div>`);
    return;
  }
  setTable(labels[0] || btn.dataset.t);
}

// Gesten auf dem Plan: ein Finger verschiebt, zwei Finger zoomen, Doppeltipp zoomt (oder zurück)
const planGesture = { pts: new Map(), start: null, endedAt: 0, lastTap: null };
document.addEventListener("pointerdown", (e) => {
  const wrap = e.target.closest?.("#plwrap");
  if (!wrap || e.target.closest(".pl-zoom") || S.locked) return;
  planGesture.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const r = wrap.getBoundingClientRect();
  const pts = [...planGesture.pts.values()];
  if (pts.length === 1) planGesture.start = { mode: "pan", x: e.clientX, y: e.clientY, px: PZ.x, py: PZ.y, moved: false, r };
  else if (pts.length === 2) {
    const [p1, p2] = pts;
    planGesture.start = { mode: "pinch", d: Math.hypot(p1.x - p2.x, p1.y - p2.y) || 1, z: PZ.z, px: PZ.x, py: PZ.y, r,
      moved: true, cx: (p1.x + p2.x) / 2 - r.left, cy: (p1.y + p2.y) / 2 - r.top };
  }
});
document.addEventListener("pointermove", (e) => {
  const g = planGesture;
  if (!g.start || !g.pts.has(e.pointerId)) return;
  g.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (g.start.mode === "pan") {
    const dx = e.clientX - g.start.x, dy = e.clientY - g.start.y;
    if (!g.start.moved && Math.hypot(dx, dy) < 8) return;
    g.start.moved = true;
    PZ.x = g.start.px + dx; PZ.y = g.start.py + dy;
    layoutPlan();
  } else if (g.start.mode === "pinch" && g.pts.size >= 2) {
    // Der Punkt des Plans, der beim Aufsetzen zwischen den Fingern lag, bleibt zwischen den Fingern: Abstand = Zoom,
    // Bewegung der Mitte = Verschieben (beides zugleich, wie bei einer Karte)
    const [p1, p2] = [...g.pts.values()], s = g.start;
    const z = Math.max(1, Math.min(PLAN_ZMAX, s.z * (Math.hypot(p1.x - p2.x, p1.y - p2.y) / s.d)));
    const mx = (p1.x + p2.x) / 2 - s.r.left, my = (p1.y + p2.y) / 2 - s.r.top;
    PZ.x = mx - (s.cx - s.px) * (z / s.z);
    PZ.y = my - (s.cy - s.py) * (z / s.z);
    PZ.z = z;
    layoutPlan();
  }
});
function planGestureEnd(e) {
  const g = planGesture;
  if (!g.pts.has(e.pointerId)) return;
  g.pts.delete(e.pointerId);
  if (g.start?.moved) g.endedAt = Date.now();
  else if (g.start?.mode === "pan" && e.type === "pointerup") {          // Doppeltipp: zoomen bzw. zurück
    const now = Date.now(), last = g.lastTap;
    if (last && now - last.t < 320 && Math.hypot(e.clientX - last.x, e.clientY - last.y) < 30) {
      g.lastTap = null; g.endedAt = now;
      if (PZ.z > 1.01) { PZ.z = 1; layoutPlan(); } else zoomAt(2.2, e.clientX - g.start.r.left, e.clientY - g.start.r.top);
    } else g.lastTap = { t: now, x: e.clientX, y: e.clientY };
  }
  g.start = g.pts.size === 1 ? { mode: "pan", ...[...g.pts.values()][0], px: PZ.x, py: PZ.y, moved: true, r: g.start?.r } : null;
}
document.addEventListener("pointerup", planGestureEnd);
document.addEventListener("pointercancel", planGestureEnd);
document.addEventListener("wheel", (e) => {                 // am PC (Vorschau): Mausrad zoomt
  const wrap = e.target.closest?.("#plwrap");
  if (!wrap) return;
  e.preventDefault();
  const r = wrap.getBoundingClientRect();
  zoomAt(PZ.z * (e.deltaY < 0 ? 1.2 : 1 / 1.2), e.clientX - r.left, e.clientY - r.top);
}, { passive: false });
window.addEventListener("resize", () => { if (S.view === "table" && $("#pl")) layoutPlan(); });
