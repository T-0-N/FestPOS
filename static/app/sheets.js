/* FestPOS – Bestell-App › Sheets und Meldungen (Toasts). */
"use strict";

// ------------------------------------------------------------------ Sheets & Toasts
// outside: was Tippen neben das Fenster macht – "close" schliessen, "warn" erst warnen (nochmals tippen schliesst),
// "ignore" nichts (nur «Abbrechen» schliesst). Für die Optionsauswahl in der Verwaltung einstellbar.
function openSheet(html, { outside = "close" } = {}) {
  saveDevName();                                      // Einstellungen: geänderter Name gilt auch beim Wechsel
  document.querySelectorAll(".sheet-bg").forEach((e) => e.remove());
  const bg = document.createElement("div");
  bg.className = "sheet-bg";
  bg.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
  let warnedAt = 0;
  bg.addEventListener("click", (e) => {
    if (e.target !== bg) return;
    if (outside === "close" || (outside === "warn" && Date.now() - warnedAt < 4000)) return closeSheet();
    warnedAt = Date.now();
    sheetNudge(bg.querySelector(".sheet"), t(outside === "warn" ? "app.opts.outside_warn" : "app.opts.outside_ignore"));
  });
  document.body.appendChild(bg);
}
// Daneben getippt: Fenster kurz rütteln, Hinweis oben im Fenster, «Abbrechen» hervorheben
function sheetNudge(sh, msg) {
  if (!sh) return;
  buzz([30, 40, 30]);
  sh.querySelector(".sheet-warn")?.remove();
  const p = document.createElement("p");
  p.className = "sheet-warn";
  p.setAttribute("role", "alert");
  p.textContent = msg;
  sh.prepend(p);
  sh.scrollTop = 0;
  sh.classList.remove("nudge");
  void sh.offsetWidth;                                   // Animation neu starten
  sh.classList.add("nudge");
  sh.querySelector('[data-act="close-sheet"]')?.classList.add("hint");
}
function closeSheet() { saveDevName(); S.optEdit = null; S.storno = null; document.querySelectorAll(".sheet-bg").forEach((e) => e.remove()); }
function closeOverlays() { closeSheet(); stopScan(); }
let toastTimer;
function toast(msg, bad = false) {
  document.querySelectorAll(".toast").forEach((e) => e.remove());
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  el.setAttribute("role", "status");
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), bad ? 6000 : 2500);
}
