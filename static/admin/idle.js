/* FestPOS – Verwaltung › Abmelden ohne Bedienung: Nach der eingestellten Zeit ohne Bedienung (Benutzer › Zugriff auf die
   Verwaltung, 10–120 Minuten, Standard 30) endet die Anmeldung. Eine Minute vorher fragt ein Fenster «Noch da?».
   Bedienung = Klick, Tippen, Taste, Mausrad – dann holt sich die Verwaltung höchstens einmal pro Minute ein neues
   Token mit neuer Frist (POST /me/refresh). Das automatische Aktualisieren der Seiten zählt nicht. Die Frist prüft
   der Server selbst (app/core.py): ein Tab, dessen Skript nicht mehr läuft, verliert die Anmeldung trotzdem.
   Rolle Anzeige (Statusanzeige): ohne Frist – dann bekommt die Verwaltung keine (idle_in fehlt). */
import { api, TOKEN, setToken } from "./core.js";

const KEY = "festpos.idle";
const WARN_MS = 60 * 1000, MIN_GAP_MS = 60 * 1000;
let S = (() => { try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); } catch { return null; } })();
let timer = null, lastRefresh = Date.now(), busy = false, dlg = null, onOut = () => {};

// Antwort von Anmeldung oder Verlängern: Frist merken (lokale Uhr, idle_in = Sekunden ab jetzt)
export function setIdle(d) {
  S = d?.idle_in ? { until: Date.now() + d.idle_in * 1000, minutes: d.idle_minutes || 0 } : null;
  lastRefresh = Date.now();
  try { if (S) sessionStorage.setItem(KEY, JSON.stringify(S)); else sessionStorage.removeItem(KEY); } catch { /* egal */ }
}
export const idleMinutes = () => S?.minutes || 0;
// Beobachten starten (nach der Anmeldung bzw. beim Laden mit gespeicherter Anmeldung). out(minutes): abgemeldet.
export function startIdle(out) {
  onOut = out;
  clearInterval(timer);
  timer = S ? setInterval(tick, 1000) : null;
}
export function stopIdle() {
  clearInterval(timer); timer = null;
  closeDlg();
}

async function refresh() {
  if (busy || !TOKEN || !S) return;
  busy = true;
  const tok = TOKEN, was = S;                              // Antwort nur für diese Sitzung übernehmen
  try {
    const d = await api("POST", "/me/refresh");
    if (TOKEN !== tok || S !== was || !timer) return;     // inzwischen abgemeldet oder schon erneuert: verwerfen
    setToken(d.token);
    try { sessionStorage.setItem("festpos.token", d.token); } catch { /* egal */ }
    setIdle(d);
    closeDlg();
  } catch { /* nicht angemeldet: api() zeigt die Anmeldung; sonst nächster Versuch bei der nächsten Bedienung */ }
  finally { busy = false; }
}
function activity() {
  if (!S || !timer || dlg) return;                       // offenes Fenster: nur dessen Knöpfe zählen
  if (Date.now() - lastRefresh >= MIN_GAP_MS) refresh();
}
for (const ev of ["pointerdown", "keydown", "wheel"]) addEventListener(ev, activity, { passive: true, capture: true });

function tick() {
  if (!S || !TOKEN) { stopIdle(); return; }
  const left = S.until - Date.now();
  if (left <= 0) {
    const m = S.minutes;
    stopIdle();
    setIdle(null);
    onOut(m);
    return;
  }
  if (left <= WARN_MS) showDlg(left); else closeDlg();
}
function showDlg(left) {
  const s = Math.ceil(left / 1000), time = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.className = "dlg idle-dlg";
    dlg.id = "idle-dlg";
    dlg.innerHTML = `<form method="dialog"><h3>${th("admin.idle.title")}</h3>
      <p id="idle-left"></p><p class="hint">${th("admin.idle.where", { minutes: S.minutes })}</p>
      <div class="form dlg-foot"><button type="button" class="btn" data-logout>${th("admin.nav.logout")}</button>
        <button type="submit" class="btn pri" id="idle-stay">${th("admin.idle.stay")}</button></div></form>`;
    document.body.appendChild(dlg);
    dlg.addEventListener("submit", (e) => { e.preventDefault(); refresh(); });
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); refresh(); });   // Esc = auch Bedienung
    dlg.showModal();
  }
  dlg.querySelector("#idle-left").innerHTML = th("admin.idle.text", { time });
}
function closeDlg() {
  if (!dlg) return;
  const d = dlg;
  dlg = null;
  if (d.open) d.close();
  d.remove();
}
