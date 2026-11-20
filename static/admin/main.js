/* FestPOS – Verwaltung: Start und der eine Klick-Handler für Bereiche, die sich selbst neu zeichnen.
   Spricht nur mit /api/admin/*. Anmeldung: Passwort einmal an /api/admin/login, danach nur noch das
   Sitzungs-Token (12 Stunden) – das Passwort selbst bleibt nicht im Browser.
   Texte: nur über t()/th() aus der Sprachdatei (static/i18n.js, app/i18n/de.json).
   Aufbau: ES-Module ohne Build-Schritt (README › Aufbau); Imports prüft tools/js_module_check.py. */
import { $, api, download, forgetLogin, guard, myLang, setMe, toast, TOKEN } from "./core.js";
import { loadBrand, loadLangs, loadNav, page, pairTimer, refreshTimer, RENDER, selectTab, shell, show, showLogin, showVersion, toggleMenu, toggleUserMenu } from "./shell.js";
import { openOwnPassword, openPower } from "./system.js";
// die Seiten tragen sich beim Laden in RENDER ein
import "./overview.js";
import "./orders.js";
import "./opentabs.js";
import "./jobs.js";
import "./closing.js";
import "./products.js";
import "./tables.js";
import "./printers.js";
import "./bondesign.js";
import "./devices.js";
import "./settings.js";
import "./kassepage.js";
import "./users.js";
import "./update.js";
import "./internet.js";

// ------------------------------------------------------------------ Start
// Ein globaler Handler für Aktionen in Bereichen, die sich selbst neu zeichnen (keine doppelten Listener)
document.addEventListener("click", async (e) => {
  if (e.target.closest("[data-usermenu]")) { toggleUserMenu(undefined, e.detail === 0); return; }   // detail 0: Tastatur
  if (!e.target.closest("#usermenu") || e.target.closest("button")) toggleUserMenu(false);   // daneben oder gewählt: zu
  const p = e.target.closest("[data-page]");
  if (p) { e.preventDefault(); show(p.dataset.page); return; }
  if (e.target.closest(".side .menu-btn")) { toggleMenu(); return; }
  const tb = e.target.closest(".tabs [data-tab]");
  if (tb) { selectTab(tb.dataset.tab); return; }
  // Wechsel in die Bestell-App = abmelden: Handys teilen sich die Bedienung, die Anmeldung soll nicht liegen bleiben
  if (e.target.closest('[data-jump="app"]')) { forgetLogin(); return; }
  if (e.target.closest("[data-ownpw]")) { openOwnPassword(); return; }
  const pw = e.target.closest("[data-power]");
  if (pw) { openPower(pw.dataset.power, pw.dataset.node); return; }
  if (e.target.closest("[data-logoutall]")) {
    if (!confirm(t("admin.nav.logout_all_q"))) return;
    if (!await guard(() => api("POST", "/me/logout-all"))) return;
    forgetLogin(); clearInterval(refreshTimer); clearInterval(pairTimer);
    showLogin(t("admin.login.logged_out_all")); return;
  }
  if (e.target.closest("[data-logout]")) {
    forgetLogin(); clearInterval(refreshTimer); clearInterval(pairTimer);
    showLogin(t("admin.login.logged_out")); return;
  }
  const el = e.target, d = el.dataset;
  if (d.reprint || d.resolve) {
    el.disabled = true;
    const ok = d.reprint ? await guard(() => api("POST", `/jobs/${d.reprint}/reprint`), t("admin.jobs.reprinting"))
                         : await guard(() => api("POST", `/jobs/${d.resolve}/resolve`), t("admin.jobs.resolved"));
    if (ok) RENDER.jobs(); else el.disabled = false;
  }
  if (d.dl) { e.preventDefault(); download(d.dl, d.name); }
  if (d.dlonce) {                                          // Einmal-Link direkt laden (ohne Admin-Login nötig)
    e.preventDefault();
    const r = await fetch(d.dlonce).catch(() => null);
    if (!r || !r.ok) return toast(t("admin.devices.link_used"), true);
    const url = URL.createObjectURL(await r.blob());
    Object.assign(document.createElement("a"), { href: url, download: d.name }).click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if ($("#usermenu") && !$("#usermenu").hidden) { toggleUserMenu(false); $(".userbtn")?.focus(); return; }
  toggleMenu(false);
});
(async () => {
  await Promise.all([I18n.load(myLang()), loadBrand(), loadNav()]);   // Sprache dieses Browsers (sonst Standard aus den Einstellungen)
  document.title = t("admin.nav.title");
  if (!TOKEN) return showLogin();
  try { setMe(await api("GET", "/me")); await loadLangs(); shell(); showVersion(); show(page); }
  // 401: api() zeigt die Anmeldung schon. Sonst – z.B. 403 «nur mit Gerätezertifikat» bei noch gespeicherter
  // Anmeldung (CodeRabbit zu PR 80) – die Anmeldung mit der Meldung statt einer leeren Seite
  catch (e) { if (!$("#login")) showLogin(e.message); }
})();
