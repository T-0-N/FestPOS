/* FestPOS – Verwaltung: Zeilen einer Tabelle per Drag & Drop sortieren (Griff ⋮⋮, Maus und Finger) oder mit ↑/↓ am
   Griff. Ohne Bibliothek (strenge CSP). Gezogen wird nur innerhalb derselben Gruppe (z.B. Produkte einer Kategorie).
   Gleiches Verhalten wie im Bon-Baukasten (bondesign.js): Die gepackte Zeile löst sich und schwebt (Kopie mit
   Schatten), an ihrem Platz bleibt eine gestrichelte Lücke, die anderen Zeilen gleiten zur Seite, am Rand scrollt
   die Seite mit. Loslassen ruft save(ids) mit der neuen Reihenfolge aller Zeilen auf. */
export function gripCell(label) {
  return `<td class="gripcell"><button type="button" class="grip" aria-label="${label}" title="${label}">⋮⋮</button></td>`;
}

const EDGE = 80;                                     // Abstand vom Rand, ab dem die Seite mitscrollt (Punkte)
const calm = () => !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Zeilen nach einer Änderung von ihrem alten Platz an den neuen gleiten lassen (↑/↓ am Griff). */
function glide(rows, apply) {
  const before = new Map(rows.map((tr) => [tr, tr.getBoundingClientRect().top]));
  apply();
  if (calm()) return;
  rows.forEach((tr) => {
    const dy = before.get(tr) - tr.getBoundingClientRect().top;
    if (!dy) return;
    tr.classList.remove("dr-slide");
    tr.style.transform = `translateY(${dy}px)`;
    void tr.offsetHeight;                            // alter Platz zuerst zeichnen, dann gleiten
    tr.classList.add("dr-slide");
    tr.style.transform = "";
    setTimeout(() => tr.classList.remove("dr-slide"), 220);
  });
}

/** Schwebende Kopie einer Tabellenzeile: eigene kleine Tabelle mit denselben Spaltenbreiten. */
function ghostOf(table, tr, r) {
  const t = document.createElement("table"), tb = document.createElement("tbody"), g = tr.cloneNode(true);
  t.className = `${table.className} dr-ghost`;
  t.setAttribute("aria-hidden", "true");
  [...tr.cells].forEach((td, i) => { g.cells[i].style.width = `${td.getBoundingClientRect().width}px`; });
  tb.appendChild(g);
  t.appendChild(tb);
  Object.assign(t.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
  document.body.appendChild(t);
  return t;
}

export function dragSort(table, { rows = "tr[data-id]", group = () => "", save }) {
  const tb = table?.tBodies[0];
  if (!tb) return;
  const all = () => [...tb.querySelectorAll(rows)];
  const ids = () => all().map((tr) => +tr.dataset.id);
  let d = null, busy = false;

  const shift = (g, t) => {
    const R = g.rects, f = g.from;
    return t > f ? R[t].bottom - R[f].bottom : t < f ? R[t].top - R[f].top : 0;
  };
  const update = () => {
    const dy = d.y - d.y0, R = d.rects, f = d.from;
    d.ghost.style.transform = `translateY(${dy}px) scale(1.01) rotate(-0.4deg)`;
    const cy = R[f].top + R[f].h / 2 + dy + (scrollY - d.sy0);
    let t = f;
    for (let i = 0; i < f; i++) if (cy < R[i].top + R[i].h / 2) { t = i; break; }
    if (t === f) for (let i = d.items.length - 1; i > f; i--) if (cy > R[i].top + R[i].h / 2) { t = i; break; }
    if (t === d.to) return;
    d.to = t;
    d.items.forEach((x, i) => {
      let s = 0;
      if (f < t && i > f && i <= t) s = -d.h;
      if (f > t && i >= t && i < f) s = d.h;
      if (x !== d.tr) x.style.transform = s ? `translateY(${s}px)` : "";
    });
    d.tr.style.transform = `translateY(${shift(d, t)}px)`;
  };
  const tick = () => {                               // am oberen/unteren Rand mitscrollen – nur in Zugrichtung
    if (!d) return;
    const up = d.y < d.y0 - 6 && d.y < EDGE, down = d.y > d.y0 + 6 && d.y > innerHeight - EDGE;
    const v = up ? -Math.ceil((EDGE - d.y) / 5) : down ? Math.ceil((d.y - innerHeight + EDGE) / 5) : 0;
    if (v) { scrollBy(0, v); update(); }
    d.raf = requestAnimationFrame(tick);
  };
  const start = () => {
    d.items = all().filter((x) => group(x) === d.key);   // nur die eigene Gruppe bewegt sich
    d.from = d.to = d.items.indexOf(d.tr);
    d.sy0 = scrollY;
    d.rects = d.items.map((x) => { const r = x.getBoundingClientRect(); return { top: r.top + scrollY, bottom: r.bottom + scrollY, h: r.height }; });
    d.r = d.tr.getBoundingClientRect();
    d.h = d.r.height;
    d.ghost = ghostOf(table, d.tr, d.r);
    d.tr.classList.add("dr-hole");
    d.items.forEach((x) => x.classList.add("dr-slide"));
    d.raf = requestAnimationFrame(tick);
  };

  tb.addEventListener("pointerdown", (e) => {
    const g = e.target.closest(".grip");
    if (!g || e.button > 0 || busy) return;
    e.preventDefault();
    g.setPointerCapture?.(e.pointerId);
    const tr = g.closest("tr");
    d = { tr, key: group(tr), x0: e.clientX, y0: e.clientY, y: e.clientY, moved: false };
  });
  tb.addEventListener("pointermove", (e) => {
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
    const to = ok ? g.to : g.from;
    const target = g.rects[g.from].top + shift(g, to) - scrollY;
    busy = true;
    g.ghost.classList.add("land");                   // in die Lücke gleiten (abgebrochen: zurück)
    g.ghost.style.transform = `translateY(${target - g.r.top}px)`;
    if (!ok) g.items.forEach((x) => { x.style.transform = ""; });
    setTimeout(async () => {
      g.items.forEach((x) => { x.classList.remove("dr-slide", "dr-hole"); x.style.transform = ""; });
      g.ghost.remove();
      busy = false;
      if (to === g.from) return;
      const other = g.items[to];
      other[to > g.from ? "after" : "before"](g.tr);
      await save(ids(), g.tr);
    }, calm() ? 0 : 220);
  };
  tb.addEventListener("pointerup", () => end(true));
  tb.addEventListener("pointercancel", () => end(false));
  // Tastatur: Griff fokussieren, ↑/↓ verschiebt innerhalb der Gruppe
  tb.addEventListener("keydown", async (e) => {
    const g = e.target.closest(".grip");
    if (!g || busy || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    const tr = g.closest("tr"), list = all().filter((x) => group(x) === group(tr)), i = list.indexOf(tr);
    const other = list[e.key === "ArrowUp" ? i - 1 : i + 1];
    if (!other) return;
    glide(list, () => other[e.key === "ArrowUp" ? "before" : "after"](tr));
    g.focus();
    await save(ids(), tr);
  });
}
