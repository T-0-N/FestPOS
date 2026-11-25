/* FestPOS – Verwaltung › Gerüst: Menü, Seiten und Reiter, Anmeldung, Logo, Links. Jede Seite trägt sich in RENDER ein. */
import { $, $$, api, can, esc, forgetLogin, guard, LANG_KEY, LANGS, ME, myLang, roleName, setLangs, setLoginView, setMe, setNet, setToken, TOKEN } from "./core.js";
import { setIdle, startIdle, stopIdle } from "./idle.js";
import { drawAsk, setGoPi, startClock } from "./clock.js";
import { checkSetup } from "./twofa.js";

// ------------------------------------------------------------------ Gerüst
// Menü in Gruppen (2026.10.10-27): Betrieb (während des Anlasses), Vorbereiten, System. [Seite, Text, Recht(e), Gruppe]
// Rechte siehe app/auth.py (Rollen und Rechte); «base» = jede Rolle, «admin» = nur die Rolle Admin.
// T34: «Kasse & App» fasst Veranstalter, Kasse, Bestell-App und Bons & Quittung zusammen, «Geräte & Netz»
// die Pi, Handhelds & PC, deren Einstellungen, Drucker und Netz & Internet – je ein Menüpunkt mit Reitern.
const PAGES = [
  ["overview", "admin.nav.overview", "base", ""],
  ["orders", "admin.nav.orders", "orders", "op"], ["opentabs", "admin.nav.opentabs", "tabs", "op"], ["jobs", "admin.nav.jobs", "jobs", "op"], ["closing", "admin.nav.closing", "closing", "op"],
  ["audit", "admin.nav.audit", "audit", "op"],          // T20: Protokoll betrifft alles, nicht nur das Kassenbuch
  ["products", "admin.nav.products", ["catalog", "stock"], "prep"], ["tables", "admin.nav.tables", "tables", "prep"], ["pos", "admin.nav.pos", ["admin", "testmode"], "prep"],
  ["devices", "admin.nav.devices", ["system", "devices", "printers"], "sys"], ["users", "admin.nav.users", "admin", "sys"], ["maint", "admin.nav.maint", "admin", "sys"],
];
const GROUPS = { op: "admin.nav.g_op", prep: "admin.nav.g_prep", sys: "admin.nav.g_sys" };
// Reiter innerhalb einer Seite: [Reiter, Text, Recht(e)]. Die Karten tragen data-in="<reiter>".
const TABS = {
  products: [["products", "admin.tab.products", ["catalog", "stock"]], ["categories", "admin.tab.categories", "catalog"], ["delivery", "admin.tab.delivery", "admin"],
    ["import", "admin.tab.import", "catalog"]],
  pos: [["event", "admin.tab.host", "admin"], ["kasse", "admin.tab.kasse", "admin"], ["app", "admin.tab.app", ["admin", "testmode"]], ["bon", "admin.tab.bons", "admin"]],
  devices: [["pi", "admin.tab.pi", "system"], ["list", "admin.tab.phones", "devices"], ["setup", "admin.tab.dev_settings", "admin"],
    ["printers", "admin.nav.printers", "printers"], ["net", "admin.tab.net", "devices"]],
  maint: [["update", "admin.tab.update", "admin"], ["backup", "admin.tab.backup", "admin"]],
};
// Seiten, deren Reiter eigene Ansichten sind (neu zeichnen statt Karten ein-/ausblenden): Reiter -> RENDER.<ansicht>
const VIEWS = {
  pos: { event: "event", kasse: "kasse", app: "app", bon: "bon" },
  devices: { pi: "system", list: "devices", setup: "devsetup", printers: "printers", net: "internet" },
};
// «Offene Tische» nur mit der Einstellung «Tisch offen lassen» (oder solange noch einer offen ist): /api/nav
const myPages = () => PAGES.filter(([id, , need]) => can(need) && (id !== "opentabs" || NAV.tabs));
const myTabs = (pg) => (TABS[pg] || []).filter(([, , need]) => can(need));
const TAB = {};                                        // gewählter Reiter pro Seite
export const curTab = (pg) => { const list = myTabs(pg); return (list.find(([id]) => id === TAB[pg]) || list[0] || [])[0]; };
export let page = location.hash.slice(1) || "overview";
// Ansicht, die gerade gezeichnet ist: bei Seiten mit VIEWS die des Reiters (z.B. «printers» unter Geräte & Netz),
// sonst gleich der Seite. Die Seiten prüfen nach dem Laden «view», ob sie noch zeichnen dürfen (REV-048).
const viewOf = (pg) => VIEWS[pg]?.[curTab(pg)] || pg;
export let view = page;
setGoPi(() => show("devices/pi"));
export let refreshTimer = null;
let problems = 0;

// Logo (Einstellungen › Logo): öffentlich unter /api/logo, damit es schon bei der Anmeldung da ist
let BRAND = { logo: null, logo_plate: true };
export async function loadBrand() {
  try { const r = await fetch("/api/logo", { cache: "no-store" }); if (r.ok) BRAND = await r.json(); } catch { /* ohne Logo */ }
}
// Links zur Bestell-App und zur Statusanzeige – vor allem für Fully Kiosk (kein Adressfeld, kein Tab-Wechsel).
// Bestell-App: am Handy-Port gleich unter «/»; vom Verwaltungs-Port aus nur in Fully (nur das Handy hat das Zertifikat).
let NAV = { phone: false, phone_port: null };
export async function loadNav() {
  try { const r = await fetch("/api/nav", { cache: "no-store" }); if (r.ok) NAV = await r.json(); } catch { /* ohne Links */ }
}
const inFully = () => typeof window.fully !== "undefined";
function appUrl() {
  if (NAV.phone) return "/";
  if (!inFully() || !NAV.phone_port) return null;
  return `https://${location.hostname}${NAV.phone_port === 443 ? "" : `:${NAV.phone_port}`}/`;
}
// Am PC öffnet die Statusanzeige einen eigenen Tab (bleibt neben der Verwaltung offen), am Handy dieselbe Seite
const sameTab = () => inFully() || (NAV.phone && !!NAV.phone_port);
function jumpLinks(cls) {
  const app = appUrl();
  return `${app ? `<a class="${cls} jump" href="${esc(app)}" data-jump="app">${th("admin.nav.app")}</a>` : ""}
    <a class="${cls} jump" href="/status.html" data-jump="status" ${sameTab() ? "" : 'target="_blank" rel="opener"'}>${th("admin.nav.status")}</a>`;
}
// Seitenleiste: Statusanzeige und Bestell-App als Knöpfe, die Statusanzeige mit Ampel (live)
const SVG_SCREEN = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>';
const SVG_PHONE = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/></svg>';
// Die Statusanzeige steht oben (oft gebraucht), die Bestell-App unten über dem Namen: so passen die Menügruppen auch
// auf einem Laptop (900 px hoch) noch offen in die Leiste.
const EXT = '<em class="ext" aria-hidden="true">↗</em>';
const statusCard = () => `<a class="jumpc jump" href="/status.html" data-jump="status" ${sameTab() ? "" : 'target="_blank" rel="opener"'}>
      <span class="ic">${SVG_SCREEN}<i class="lampdot" data-l=""></i></span><span><b>${th("admin.nav.status")}</b>
      <small class="lamptext">${th("admin.lamp.loading")}</small></span>${EXT}</a>`;
function appCard() {
  const app = appUrl();
  return app ? `<a class="jumpc jump" href="${esc(app)}" data-jump="app"><span class="ic">${SVG_PHONE}</span><span><b>${th("admin.nav.app")}</b>
      <small>${th("admin.nav.app_sub")}</small></span>${EXT}</a>` : "";
}
// Ampel: dieselben Störungen und Hinweise wie die Statusanzeige (Drucker, Pi) – GET /api/admin/lamp, alle 5 s
let lampTimer = null;
const piName = (id) => t("status.cluster.pi", { id: String(/^pi/i.test(id) ? id.replace(/^pi[-_ ]?/i, "") : id).toUpperCase() });
const lampItem = (x) => t(x.key, x.pi ? { name: piName(x.pi) } : { name: x.name });
async function refreshLamp() {
  if (document.hidden || !$(".lampdot")) return;
  const d = await api("GET", "/lamp").catch(() => null);
  const dot = $(".lampdot"), txt = $(".lamptext");
  if (!dot || !txt) return;
  if (!d) { dot.dataset.l = ""; txt.textContent = t("admin.lamp.unknown"); return; }
  festposNotice(d.notice || null, can("system") ? { onCancel: cancelPower } : {});     // T27: Balken mit Countdown
  const list = d.level === "bad" ? d.bad : d.level === "wait" ? d.wait : [];
  dot.dataset.l = d.level;
  txt.textContent = !list.length ? t("status.head.all_ok") : list.length === 1 ? lampItem(list[0])
    : d.level === "bad" ? t("status.head.problems", { count: list.length }) : t("status.head.check");
  dot.closest(".jumpc").title = [...d.bad, ...d.wait].map(lampItem).join("\n") || txt.textContent;
}
// Herunterfahren / Neustart abbrechen (T27), solange der Countdown läuft
async function cancelPower() {
  if (await guard(() => api("POST", "/system/power/cancel"), t("common.power.cancelled"))) refreshLamp();
}
export function startLamp() { clearInterval(lampTimer); refreshLamp(); lampTimer = setInterval(refreshLamp, 5000); }
export function stopLamp() { clearInterval(lampTimer); lampTimer = null; }

// Benutzermenü unten in der Seitenleiste: Name und Rolle, Klick öffnet Sprache, Passwort ändern, Überall abmelden,
// Abmelden und Version. Am PC schwebt es über der Leiste (die selbst scrollt), am Handy steht es im Menü.
function userMenu() {
  const role = ME ? roleName(ME.role) : t("admin.users.role_view"), name = ME?.name || "";
  return `<div class="usermenu" id="usermenu" role="menu" hidden>
      <div class="um-who">${th("admin.nav.signed_in_as", { name, role })}</div>
      ${LANGS.length > 1 ? `<label class="um-row">${th("admin.nav.language")}
        <select id="adminlang"><option value="">${th("admin.nav.language_auto")}</option>
          ${LANGS.map((l) => `<option value="${esc(l.code)}" ${myLang() === l.code ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select></label>` : ""}
      ${ME && !ME.emergency ? `<button type="button" role="menuitem" data-ownpw>${th("admin.users.own_password")} …</button>` : ""}
      ${ME && !ME.emergency ? `<button type="button" role="menuitem" data-totp>${th("admin.totp.menu")} …</button>` : ""}
      <hr>
      ${ME ? `<button type="button" role="menuitem" data-logoutall>${th("admin.nav.logout_all")} …</button>` : ""}
      <button type="button" role="menuitem" class="out" data-logout>${th("admin.nav.logout")}</button>
      <span class="um-ver" id="ver"></span></div>
    <button type="button" class="userbtn" data-usermenu aria-haspopup="menu" aria-expanded="false" aria-controls="usermenu">
      <span class="av" aria-hidden="true">${esc((name.trim()[0] || "?").toUpperCase())}</span>
      <span><b>${esc(name)}</b><small>${esc(role)}</small></span><em class="chev" aria-hidden="true"></em></button>`;
}
export function toggleUserMenu(open, keyboard = false) {
  const m = $("#usermenu"), b = $(".userbtn");
  if (!m || !b) return;
  const on = open ?? m.hidden;
  m.hidden = !on;
  b.setAttribute("aria-expanded", String(on));
  if (!on) return;
  if (!narrow()) {                                    // über der Leiste, direkt über dem Knopf
    const r = b.getBoundingClientRect();
    Object.assign(m.style, { left: `${r.left}px`, bottom: `${innerHeight - r.top + 6}px`, width: `${Math.max(r.width + 70, 260)}px` });
  } else {                                            // Handy: steht im Menü über dem Knopf – hinscrollen
    m.removeAttribute("style");
    b.scrollIntoView({ block: "nearest" });
  }
  if (keyboard) m.querySelector("select, button")?.focus();     // mit der Tastatur: gleich in das Menü
}

export const logoImg = (url, plate, cls = "") => `<img class="logo${plate ? " plate" : ""}${cls ? ` ${cls}` : ""}" src="${esc(url)}" alt="">`;
export const brandMark = () => (BRAND.logo ? logoImg(BRAND.logo, BRAND.logo_plate) : "<i></i>");

// Seitenleiste: am PC auf Bildschirmhöhe, scrollt selbst. T28: alle Gruppen immer offen (Name als Überschrift),
// kompakte Einträge. Am Handy: Kopfzeile mit Menü-Knopf.
const narrow = () => matchMedia("(max-width: 760px)").matches;
const pageBtn = (id, key) => `<button data-page="${id}" ${id === page ? 'aria-current="page"' : ""}>${th(key)}${id === "jobs" && problems ? `<span class="badge">${problems}</span>` : ""}</button>`;
// Menü neu zeichnen (z.B. «Offene Tische» nach dem Umschalten unter Kasse › Abrechnen); Klicks: main.js (delegiert)
export function redrawNav() {
  const m = $("#side-menu");
  if (!m) return;
  m.querySelectorAll(":scope > .grp, :scope > .grp-items, :scope > button[data-page]").forEach((e) => e.remove());
  (m.querySelector(".side-foot") || m.lastChild).insertAdjacentHTML("beforebegin", navHTML());
}
function navHTML() {
  const pages = myPages(), out = [];
  for (const [id, key, , grp] of pages.filter(([, , , g]) => !g)) out.push(pageBtn(id, key));
  for (const grp of Object.keys(GROUPS)) {
    const mine = pages.filter(([, , , g]) => g === grp);
    if (!mine.length) continue;
    out.push(`<div class="grp" data-grp="${grp}" role="heading" aria-level="2">${th(GROUPS[grp])}</div>
      <div class="grp-items" data-grp-items="${grp}">${mine.map(([id, key]) => pageBtn(id, key)).join("")}</div>`);
  }
  return out.join("");
}
export function layoutGroups() {
  const here = $(".side .here");
  if (here) here.textContent = t(PAGES.find(([id]) => id === page)?.[1] || "admin.nav.overview");
}
export function toggleMenu(open) {
  const side = $(".side");
  if (!side) return;
  const on = open ?? !side.classList.contains("open");
  side.classList.toggle("open", on);
  const b = $(".side .menu-btn");
  b?.setAttribute("aria-expanded", String(on));
  b?.setAttribute("aria-label", t(on ? "admin.nav.menu_close" : "admin.nav.menu"));
}
addEventListener("resize", () => { if (!narrow()) toggleMenu(false); toggleUserMenu(false); layoutGroups(); });

export function shell() {
  $("#app").innerHTML = `<nav class="side" aria-label="${th("admin.nav.aria")}">
      <div class="side-bar"><div class="brand">${brandMark()}<span>FestPOS</span></div><span class="here"></span>
        <span class="side-clock" data-short="1" hidden></span>
        <button type="button" class="menu-btn" aria-expanded="false" aria-controls="side-menu" aria-label="${th("admin.nav.menu")}"><i aria-hidden="true"></i></button></div>
      <div class="side-menu" id="side-menu"><span class="side-clock" hidden></span>${statusCard()}${navHTML()}
      <div class="side-foot">${appCard()}${userMenu()}</div></div>
    </nav>
    <main class="main"><div id="timeask" hidden></div><div id="main"></div></main>`;
  $("#adminlang")?.addEventListener("change", async (e) => {
    try { localStorage.setItem(LANG_KEY, e.target.value); } catch { /* egal */ }
    await I18n.load(e.target.value);
    shell(); showVersion(); show(page);
  });
  layoutGroups();
  startClock();                                        // Uhr des Pi und Hinweis «Bitte Uhrzeit prüfen» (T2)
  startLamp();                                         // Ampel beim Knopf «Statusanzeige»
  startIdle((minutes) => {                             // Abmelden ohne Bedienung (idle.js)
    forgetLogin(); clearInterval(refreshTimer); clearInterval(pairTimer);
    showLogin(t("admin.login.idle_out", { minutes }));
  });
}
export async function showVersion() {
  const h = await fetch("/api/health").then((r) => r.json()).catch(() => null);
  const el = $("#ver"); if (el && h?.version) el.textContent = t("admin.nav.version", { version: h.version });
}
export function showLogin(msg = "") {
  stopIdle();
  clearInterval(refreshTimer);
  stopLamp();
  $("#app").innerHTML = `<div class="login"><form class="card" id="login">
      ${BRAND.logo ? logoImg(BRAND.logo, false, "login-logo") : ""}
      <h2>${th("admin.login.title")}</h2>
      <label class="field">${th("admin.login.user")}<input id="user" autocomplete="username" maxlength="60" autocapitalize="off" spellcheck="false"></label>
      <label class="field">${th("admin.login.password")}<input type="password" id="pw" autocomplete="current-password" required autofocus></label>
      <p class="hint">${th("admin.login.emergency_hint")}</p>
      ${msg ? `<p class="err">${esc(msg)}</p>` : ""}
      <button class="btn pri" type="submit">${th("admin.login.submit")}</button>
      <nav class="login-jump" aria-label="${th("admin.nav.jump_aria")}">${jumpLinks("btn")}</nav>
    </form></div>`;
  $("#login").addEventListener("submit", async (e) => {
    e.preventDefault();
    let r;
    try {
      r = await fetch("/api/admin/login", { method: "POST", headers: { "Content-Type": "application/json", "X-Festpos-UI": "1", "X-Festpos-Lang": I18n.lang },
                                            body: JSON.stringify({ name: $("#user").value.trim(), password: $("#pw").value }) });
    } catch { return showLogin(t("admin.common.server_unreachable")); }
    const d = await r.json().catch(() => null);
    if (!r.ok) return showLogin(r.status === 401 ? t("admin.login.wrong") : (typeof d?.detail === "string" ? d.detail : t("admin.common.error_status", { status: r.status })));
    if (d.second_step) return showLoginCode(d.pending, d.name);   // zweiter Faktor: zweiter Schritt (T-0-N/FestPOS#9)
    await loggedIn(d);
  });
}
async function loggedIn(d) {
  setToken(d.token);
  sessionStorage.setItem("festpos.token", TOKEN);
  setIdle(d);                                          // Frist ohne Bedienung (fehlt bei der Rolle Anzeige)
  setMe({ name: d.name, role: d.role, emergency: d.emergency, role_name: d.role_name, perms: d.perms || [], totp: d.totp });
  await loadLangs();
  shell(); showVersion(); show(page);
  checkSetup();                                        // zweiter Faktor Pflicht, noch nicht eingerichtet
}
// Zweiter Schritt: Code aus der Authenticator-App oder ein Wiederherstellungs-Code (pending gilt 5 Minuten)
function showLoginCode(pending, name, msg = "", recovery = false) {
  $("#app").innerHTML = `<div class="login"><form class="card" id="login">
      ${BRAND.logo ? logoImg(BRAND.logo, false, "login-logo") : ""}
      <h2>${th("admin.login.title")}</h2>
      <p>${th("admin.login.step2", { name })}</p>
      <label class="field">${th(recovery ? "admin.login.recovery" : "admin.login.code")}<input id="code" class="code-in" required autofocus
        autocomplete="one-time-code" ${recovery ? 'maxlength="11" autocapitalize="characters"' : 'inputmode="numeric" maxlength="7"'} spellcheck="false"></label>
      <p class="hint">${recovery ? th("admin.login.recovery_hint")
        : `${th("admin.login.code_hint")} <a href="#" id="use-recovery">${th("admin.login.use_recovery")}</a>`}</p>
      ${msg ? `<p class="err">${esc(msg)}</p>` : ""}
      <button class="btn pri" type="submit">${th("admin.login.confirm")}</button>
      <nav class="login-jump" aria-label="${th("admin.nav.jump_aria")}">${jumpLinks("btn")}</nav>
    </form></div>`;
  $("#use-recovery")?.addEventListener("click", (e) => { e.preventDefault(); showLoginCode(pending, name, "", true); });
  $("#login").addEventListener("submit", async (e) => {
    e.preventDefault();
    let r;
    try {
      r = await fetch("/api/admin/login/totp", { method: "POST", headers: { "Content-Type": "application/json", "X-Festpos-UI": "1", "X-Festpos-Lang": I18n.lang },
                                                 body: JSON.stringify({ pending, code: $("#code").value }) });
    } catch { return showLoginCode(pending, name, t("admin.common.server_unreachable"), recovery); }
    const d = await r.json().catch(() => null);
    const text = typeof d?.detail === "string" ? d.detail : t("admin.common.error_status", { status: r.status });
    if (r.status === 409) return showLogin(text);                // zu lange gewartet: nochmals mit Passwort
    if (!r.ok) return showLoginCode(pending, name, text, recovery);
    await loggedIn(d);
  });
}
setLoginView(showLogin);
export async function loadLangs() {
  const [s, n] = await Promise.all([api("GET", "/settings").catch(() => null), api("GET", "/netinfo").catch(() => null)]);
  setNet(n);                                           // Netz für Vorschläge und Beispiele (#2)
  setLangs(s?.languages || []);
  I18n.setTableWord(s?.table_word);                    // «Platz» statt «Tisch» in den Texten zu Tischen
  return s;
}
let navSeq = 0, refreshing = false;
export async function show(target) {
  if (!leaveOk()) { history.replaceState(null, "", "#" + page); return; }
  let [p, tab] = String(target || "overview").split("/");
  if (!myPages().some(([id]) => id === p)) { p = "overview"; tab = ""; }    // Seite für diese Rolle nicht da
  page = p;
  if (tab) TAB[p] = tab;
  view = viewOf(p);
  const nav = ++navSeq;                            // schneller Seitenwechsel: nur die letzte Seite zählt (REV-048)
  history.replaceState(null, "", "#" + p + (myTabs(p).length > 1 ? "/" + curTab(p) : ""));
  $$(".side [data-page]").forEach((b) => {
    if (b.dataset.page === p) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  toggleMenu(false);                               // Handy: Menü nach der Wahl schliessen
  toggleUserMenu(false);
  layoutGroups();
  clearInterval(refreshTimer);
  clearInterval(pairTimer);
  await RENDER[view]();
  showTab();
  drawAsk();                                       // Uhrzeit-Hinweis: Übersicht immer, sonst nur bei Abweichung
  if (nav !== navSeq) return;                      // inzwischen woanders hin gewechselt: keinen Timer starten
  if (p === "overview" || p === "jobs" || p === "maint") {
    refreshTimer = setInterval(async () => {
      if (refreshing || nav !== navSeq) return;    // nie zwei Aktualisierungen gleichzeitig
      refreshing = true;
      try { await RENDER[p](true); } finally { refreshing = false; }
    }, p === "maint" ? 4000 : 5000);
  }
}
// Reiter: alle Karten einer Seite sind da, gezeigt werden die des gewählten Reiters (data-in). Ein Wechsel lädt nichts neu.
export function tabBar(pg) {
  const tabs = myTabs(pg);
  if (tabs.length < 2) return "";
  return `<div class="tabs" role="tablist">${tabs.map(([id, key]) =>
    `<button type="button" role="tab" data-tab="${id}" aria-selected="${id === curTab(pg)}">${th(key)}</button>`).join("")}</div>`;
}
export function applyTabs() {
  const tab = curTab(page);
  $$("#main [data-in]").forEach((el) => { el.hidden = !el.dataset.in.split(" ").includes(tab); });
  $$("#main .tabs [data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
}
// Kopf einer Seite: Titel (bei Seiten mit Reitern der Menüpunkt), daneben ein Knopf (z.B. «+ Drucker»), darunter die Reiter
export function pageHead(btn = "") {
  const title = th(PAGES.find(([id]) => id === page)?.[1] || "admin.nav.overview");
  return `${btn ? `<div class="head-row"><h2>${title}</h2>${btn}</div>` : `<h2>${title}</h2>`}${tabBar(page)}`;
}
export function selectTab(tab) {
  if (!leaveOk()) return;
  TAB[page] = tab;
  history.replaceState(null, "", `#${page}/${tab}`);
  if (VIEWS[page]) { view = viewOf(page); RENDER[view]().then(showTab); scrollTo(0, 0); return; }
  applyTabs();
  showTab();
  scrollTo(0, 0);
}
// Handy: die Reiterleiste scrollt waagrecht – den gewählten Reiter sichtbar machen
function showTab() {
  const b = $('#main .tabs [aria-selected="true"]'), bar = b?.parentElement;
  if (!b || bar.scrollWidth <= bar.clientWidth) return;
  bar.scrollLeft += b.getBoundingClientRect().left - bar.getBoundingClientRect().left - (bar.clientWidth - b.offsetWidth) / 2;
}
// Ungespeicherte Änderungen (T28, Speichern-Balken): vor dem Verlassen nachfragen. Die Seite meldet sich mit
// setDirty(true/false); beim Neuzeichnen einer anderen Seite gilt wieder «nichts geändert».
let DIRTY = false;
export function setDirty(on) { DIRTY = !!on; }
export function leaveOk() {
  if (!DIRTY) return true;
  if (!confirm(t("admin.save.leave_q"))) return false;
  DIRTY = false;
  return true;
}
addEventListener("beforeunload", (e) => { if (DIRTY) { e.preventDefault(); e.returnValue = ""; } });
// nach einer Änderung dieselbe Seite neu zeichnen (gleiche Stelle)
export async function rerender() { const y = scrollY; await RENDER[view](); scrollTo(0, y); }
export async function refreshBadge() {
  const s = await api("GET", "/stats").catch(() => null);
  if (!s) return;
  problems = s.problems;
  const b = $('.side [data-page="jobs"]');
  if (b) b.innerHTML = `${th("admin.nav.jobs")}${problems ? `<span class="badge">${problems}</span>` : ""}`;
}
export const inDays = (d) => (d > 60 ? t("admin.common.in_months", { count: Math.round(d / 30) }) : t("admin.common.in_days", { count: d }));

export const RENDER = {};                              // Seiten: RENDER.<seite>()

export let pairTimer = null;                           // Countdown beim Koppeln eines Handys (device_add.js)

// von anderen Modulen gesetzt (importierte Variablen sind dort nur lesbar)
export function setPairTimer(v) { pairTimer = v; }
export function setProblems(v) { problems = v; }
