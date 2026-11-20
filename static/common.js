/* FestPOS – gemeinsam für Bestell-App und Verwaltung (eine Regel statt zwei Kopien, REV-053). */
"use strict";
// Kachelfarbe einer Kategorie: eigene Farbe, sonst (grau #3a3a3a = «automatisch») eine aus der Reihe
const FESTPOS_CAT_COLORS = ["#e4002b", "#0057b8", "#e0a800", "#ff8200", "#7a3e9d", "#00a3ad", "#8a6d3b", "#d6007e"];
function festposCategoryColor(categories, index) {
  const c = categories[index]?.color;
  return (!c || c === "#3a3a3a") ? FESTPOS_CAT_COLORS[index % FESTPOS_CAT_COLORS.length] : c;
}

// Herunterfahren / Neustart angekündigt (T27): derselbe Balken mit Countdown in Bestell-App, Verwaltung und
// Statusanzeige. n: {action: "poweroff"|"reboot", left: Sekunden, token, cancel, pis, all} vom Server oder null
// (vorbei). Nur einzelne Pi (all false, T32): «Pi B wird … – die anderen Pi laufen weiter».
// opts.overlay: über allem (Statusanzeige), sonst oben im Seitenfluss (verschiebt den klebenden Kopf mit --notice-h).
// opts.onCancel: Knopf «Abbrechen» (Verwaltung, ab Festleitung), solange n.cancel.
const FESTPOS_NOTICE = { token: null, end: 0, action: null, cancel: false, timer: null, pis: "" };
function festposNotice(n, opts = {}) {
  const N = FESTPOS_NOTICE, tr = (k, p) => (typeof I18n !== "undefined" ? I18n.t(k, p) : k);
  let bar = document.getElementById("pwnotice");
  if (!n || !n.token) {
    if (bar) bar.remove();
    clearInterval(N.timer); N.timer = null; N.token = null;
    document.documentElement.style.removeProperty("--notice-h");
    return;
  }
  const end = Date.now() + Math.max(0, n.left) * 1000;
  if (N.token !== n.token || Math.abs(end - N.end) > 2500) N.end = end;     // Countdown läuft im Browser weiter
  N.token = n.token; N.action = n.action; N.cancel = !!n.cancel;
  N.pis = n.all === false ? (n.pis || []).map((p) => String(p).replace(/^pi[-_ ]?/i, "").toUpperCase()).join(", ") : "";
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "pwnotice";
    bar.className = "pwnotice" + (opts.overlay ? " overlay" : "");
    bar.setAttribute("role", "alert");
    document.body.insertBefore(bar, document.body.firstChild);
  }
  const draw = () => {
    const left = Math.max(0, Math.round((N.end - Date.now()) / 1000));
    const time = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    const off = N.action === "poweroff";
    const text = N.pis
      ? (left > 0 ? tr(off ? "common.power.pi_off_in" : "common.power.pi_reboot_in", { time, pi: N.pis })
        : tr(off ? "common.power.pi_off_now" : "common.power.pi_reboot_now", { pi: N.pis }))
      : left > 0 ? tr(off ? "common.power.off_in" : "common.power.reboot_in", { time })
        : tr(off ? "common.power.off_now" : "common.power.reboot_now");
    const can = opts.onCancel && N.cancel && left > 12;
    if (bar.dataset.text !== text || bar.dataset.can !== String(can)) {
      bar.dataset.text = text; bar.dataset.can = String(can);
      bar.textContent = "";
      const b = document.createElement("b");
      b.textContent = text;
      bar.appendChild(b);
      if (can) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "pwcancel";
        btn.textContent = tr("common.power.cancel");
        btn.addEventListener("click", () => { btn.disabled = true; opts.onCancel(); });
        bar.appendChild(btn);
      }
      if (!opts.overlay) document.documentElement.style.setProperty("--notice-h", `${bar.offsetHeight}px`);
    }
  };
  draw();
  if (!N.timer) N.timer = setInterval(draw, 1000);
}
