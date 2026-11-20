/* FestPOS – Verwaltung › Uhrzeit (T2): Uhr des Pi in der Seitenleiste, Hinweis «Bitte Uhrzeit prüfen» über jeder
   Seite (nach der Anmeldung), Karte «Uhrzeit» unter Geräte & Netz › Pi und das Warten auf ein Handy am USB-Kabel (Zeitserver).
   Nur Festleitung und Admin sehen und stellen die Uhrzeit; die Rolle Anzeige sieht nichts davon. */
import { $, $$, api, can, esc, guard, piName, toast, TOKEN } from "./core.js";

let T = null;                  // GET /api/admin/time (Zeitstand aller Pi, Bestätigung, Gründe)
let skew = 0;                  // Uhr des Pi minus Uhr dieses Browsers (ms)
let ticker = null, reloader = null;
const LATER_KEY = "festpos.time.later";                 // «Nicht jetzt»: gilt bis zur nächsten Anmeldung (Token)
const later = () => { try { return sessionStorage.getItem(LATER_KEY) === TOKEN; } catch { return false; } };

export const timeData = () => T;
// «Selbst einstellen» ausserhalb von Geräte & Netz › Pi: dorthin wechseln (setzt shell.js, das dieses Modul lädt)
let goPi = null;
export function setGoPi(fn) { goPi = fn; }
const piNow = () => Date.now() + skew;
const tz = () => T?.pis?.[0]?.tz || undefined;
function fmt(ms, opts) {
  try { return new Intl.DateTimeFormat(I18n.locale, { ...opts, timeZone: tz() }).format(ms); }
  catch { return new Intl.DateTimeFormat(I18n.locale, opts).format(ms); }
}
const hm = (ms) => fmt(ms, { hour: "2-digit", minute: "2-digit" });
const hms = (ms) => fmt(ms, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const day = (ms) => fmt(ms, { day: "2-digit", month: "2-digit", year: "numeric" });
const dayTime = (ms) => `${day(ms)} ${hm(ms)}`;
// Abweichung in Worten: «37 Minuten», «2 Stunden», «3 Tage»
function span(sec) {
  const s = Math.abs(Math.round(sec));
  if (s < 120) return t("admin.time.u_sec", { count: s });
  if (s < 7200) return t("admin.time.u_min", { count: Math.round(s / 60) });
  if (s < 172800) return t("admin.time.u_hour", { count: Math.round(s / 3600) });
  return t("admin.time.u_day", { count: Math.round(s / 86400) });
}
const diffSec = () => skew / 1000;                      // Pi gegenüber diesem Gerät (+ = Pi geht vor)
const browserWrong = () => T && Date.now() / 1000 < T.floor - 60;   // Uhr DIESES Geräts vor der letzten Bestellung

// Gründe für den Hinweis: vom Server und – nur hier messbar – die Abweichung zu diesem Gerät
function reasons() {
  if (!T) return [];
  const out = [...T.reasons];
  if (Math.abs(diffSec()) > T.threshold) out.unshift("differs");
  return out;
}

export async function loadTime() {
  if (!can("system")) return null;
  const t0 = Date.now();
  const r = await api("GET", "/time").catch(() => null);
  if (!r) return null;
  skew = r.server_time * 1000 - (t0 + (Date.now() - t0) / 2);
  T = r;
  drawClock();
  drawAsk();
  return r;
}

// Seitenleiste: «Pi A · 15:49» (am Handy in der Kopfzeile nur «15:49»), rot solange der Hinweis offen ist
export function startClock() {
  clearInterval(ticker); clearInterval(reloader);
  if (!can("system")) return;
  loadTime();
  ticker = setInterval(drawClock, 1000);
  reloader = setInterval(loadTime, 10 * 60 * 1000);
}
function drawClock() {
  const bad = reasons().length > 0;
  for (const el of $$(".side-clock")) {
    if (!T) { el.hidden = true; continue; }
    el.hidden = false;
    el.classList.toggle("bad", bad);
    el.textContent = el.dataset.short ? hm(piNow()) : t("admin.time.clock", { node: piName(T.this_node), time: hm(piNow()) });
    el.title = t(bad ? "admin.time.clock_bad" : "admin.time.clock_title");
  }
}

// Hinweis über der Seite (#timeask, ausserhalb von #main: bleibt beim Neuzeichnen der Seite stehen)
function why() {
  const r = reasons(), c = T.confirmed, out = [];
  if (r.includes("differs")) out.push(t(diffSec() < 0 ? "admin.time.w_behind" : "admin.time.w_ahead", { node: piName(T.this_node), span: span(diffSec()) }));
  if (r.includes("never")) out.push(t("admin.time.w_never"));
  if (r.includes("old")) out.push(t("admin.time.w_old", { date: day(c.at * 1000) }));
  if (r.includes("restart")) out.push(t("admin.time.w_restart", { date: day(c.at * 1000) }));
  if (r.includes("unsure")) out.push(t("admin.time.w_unsure", { pis: T.pis.filter((p) => !p.sure).map((p) => piName(p.node)).join(", ") }));
  if (r.includes("cluster")) out.push(t("admin.time.w_cluster"));
  return out.join(" · ");
}
// Knöpfe: «Zeit stimmt» nur, wenn die Uhren höchstens 2 Minuten auseinander liegen – sonst «übernehmen» als Hauptknopf
function buttons(where) {
  if (browserWrong()) return `<p class="err">${th("admin.time.browser_wrong")}</p>
    <div class="form">${where === "ask" ? `<button type="button" class="btn" data-time="manual">${th("admin.time.manual")}</button>` : ""}</div>`;
  const same = Math.abs(diffSec()) <= T.threshold;
  return `<div class="form">
    ${same ? `<button type="button" class="btn ${where === "ask" ? "pri" : ""}" data-time="ok">${th("admin.time.ok")}</button>` : ""}
    <button type="button" class="btn ${where === "ask" && !same ? "pri" : ""}" data-time="take">${th("admin.time.take")}</button>
    ${where === "ask" ? `<button type="button" class="btn" data-time="manual">${th("admin.time.manual")}</button>
      <button type="button" class="linklike" data-time="later">${th("admin.time.later")}</button>` : ""}</div>`;
}
// T28 Stufe 2: auf der Übersicht immer; auf anderen Seiten nur bei echter Abweichung (Uhr falsch, Pi uneins)
export function drawAsk() {
  const box = $("#timeask");
  if (!box) return;
  const r = T ? reasons() : [], here = ["", "#overview"].includes(location.hash.split("/")[0]);
  const show = T && r.length && !later() && (here || r.includes("differs") || r.includes("cluster"));
  box.hidden = !show;
  // T28: eine Zeile statt einer grossen Karte (Titel, Grund, beide Uhren, Knöpfe) – bricht nur um, wenn es eng wird
  box.innerHTML = show ? `<div class="time-ask" role="status"><b class="ta-title">${th("admin.time.ask_title")}</b>
      <span class="why">${esc(why())}</span>
      <span class="cmp">${th("admin.time.cmp_pi")} <b>${esc(hm(piNow()))}</b> · ${th("admin.time.cmp_here")}
        <b>${esc(new Intl.DateTimeFormat(I18n.locale, { hour: "2-digit", minute: "2-digit" }).format(Date.now()))}</b></span>
      ${buttons("ask")}</div>` : "";
}

// Aktionen (Hinweis und Karte): data-time="ok|take|manual|later"
let onChange = () => {};
export function setTimeChanged(fn) { onChange = fn; }
async function done(r, msg) {
  if (!r) return;
  const bad = Object.entries(r.nodes || {}).filter(([, v]) => !String(v).startsWith("ok"));
  toast(bad.length ? t("admin.time.partial", { list: bad.map(([k, v]) => `${k} (${v})`).join(", ") }) : msg, !!bad.length);
  await loadTime();
  onChange();
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-time]");
  if (!b) return;
  const act = b.dataset.time;
  if (act === "later") { try { sessionStorage.setItem(LATER_KEY, TOKEN); } catch { /* nur jetzt */ } drawAsk(); drawClock(); return; }
  if (act === "manual") {
    if ($("#time-manual-d")) { $("#time-manual-d").open = true; $("#time-local").focus(); } else goPi?.();
    return;
  }
  b.disabled = true;
  try {
    if (act === "ok") await done(await guard(() => api("POST", "/time/confirm")), t("admin.time.confirmed"));
    if (act === "take") await done(await guard(() => api("POST", "/time", { epoch_ms: Date.now() })), t("admin.time.taken"));
    if (act === "set") {
      const v = $("#time-local")?.value;
      if (!v) return toast(t("admin.time.manual_empty"), true);
      if (!confirm(t("admin.time.manual_confirm", { time: v.replace("T", " ") }))) return;
      const ms = Date.parse(v);                           // nur für die Rückfrage «vor der letzten Bestellung»
      if (T && !Number.isNaN(ms) && ms / 1000 - (Date.now() - piNow()) / 1000 < T.floor - 60
        && !confirm(t("admin.time.manual_before_orders"))) return;
      await done(await guard(() => api("POST", "/time", { local: v })), t("admin.time.set_done"));
    }
  } finally { b.disabled = false; }
});

// Karte «Uhrzeit» (System › Pi & Netz)
const HOW = { ok: "admin.time.how_ok", take: "admin.time.how_take", manual: "admin.time.how_manual", ntp: "admin.time.how_ntp" };
const SINCE = { take: "admin.time.since_take", ok: "admin.time.since_ok", manual: "admin.time.since_manual",
  peer: "admin.time.since_peer" };
export const USB_ST = { wait: "admin.time.usb_st_wait", allow: "admin.time.usb_st_allow", ok: "admin.time.usb_st_ok",
  bad: "admin.time.usb_st_bad", timeout: "admin.time.usb_st_timeout" };
// T16: «lost» = beim Start nicht aus der RTC oder RTC weicht weit ab (hat die Zeit beim Stromunterbruch nicht gehalten)
const START = (p) => (!p.rtc ? ["", "admin.time.st_no_rtc"] : !p.rtc.lost ? ["", "admin.time.st_rtc"]
  : p.rtc.boot && p.rtc.drift != null ? ["bad", "admin.time.st_rtc_drift", { span: span(p.rtc.drift) }] : ["bad", "admin.time.st_rtc_empty"]);
function since(p) {
  if (p.set) return t(SINCE[p.set.source] || "admin.time.since_other", { time: hm(p.set.at * 1000) });
  if (p.ntp === "internet") return t("admin.time.since_internet");
  if (p.ntp?.startsWith("peer:")) {
    const ip = p.ntp.slice(5), other = T.pis.find((x) => x.ip === ip);
    return t("admin.time.since_follows", { node: other ? piName(other.node) : ip });
  }
  return "–";
}
function zone(p) {
  const off = p.offset_min, h = `${off < 0 ? "−" : "+"}${Math.floor(Math.abs(off) / 60)}${off % 60 ? `:${String(Math.abs(off) % 60).padStart(2, "0")}` : ""}`;
  return `<span translate="no">${esc(p.tz)}</span><small>${th(p.dst ? "admin.time.summer" : "admin.time.winter", { utc: `UTC${h}` })}</small>`;
}
function battery(p) {
  const r = p.rtc;
  if (!r || r.battery_uv == null) return "–";
  const v = `${(r.battery_uv / 1e6).toFixed(2)} V`, weak = r.battery_uv < r.weak_uv;
  let sub, cls = "";
  if (weak) { sub = "admin.time.bat_weak"; cls = "bad"; }
  else if (r.restart) { sub = r.akku ? "admin.time.bat_restart_on" : "admin.time.bat_restart_off"; cls = "warn"; }
  else if (r.akku && r.charging_uv) sub = "admin.time.bat_charging";
  else if (r.akku) { sub = "admin.time.bat_not_charging"; cls = "bad"; }
  else sub = "admin.time.bat_off";
  return `<span class="${cls}">${v}</span><small class="${cls}">${th(sub)}</small>`;
}
function localValue() {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz(), year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(piNow()).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}
// T33: die Uhrzeit steht beim Pi – in der Liste unter Pi & Netz und in der Unterseite eines Pi.
// -> {time, diff, bad, start, startBad, since, zone, battery} (HTML-Teile) oder null
export function piClock(node) {
  const p = T?.pis?.find((x) => x.node === node);
  if (!p) return null;
  const d = (p.offset * 1000 + skew) / 1000, [scls, skey, sargs] = START(p);
  return { time: esc(hms(Date.now() + skew + p.offset * 1000)), bad: Math.abs(d) > T.threshold,
    diff: esc(Math.abs(d) < 1 ? "0 s" : `${d > 0 ? "+" : "−"}${span(d)}`), start: th(skey, sargs), startBad: scls === "bad",
    since: esc(since(p)), zone: zone(p), battery: battery(p) };
}
// Bestätigt von wem und wann, «Zeit stimmt» / «übernehmen», «Von Hand setzen …» (aufklappbar)
export function timeActions() {
  if (!T) return `<p class="hint">${th("admin.time.unknown")}</p>`;
  const c = T.confirmed, howKey = c ? HOW[c.how] || HOW.ok : "";
  return `<div id="timecard"><p>${c ? th("admin.time.confirmed_at", { time: dayTime(c.at * 1000), by: c.by === "system" ? "FestPOS" : c.by, how: t(howKey) })
      + " " + th("admin.time.next", { restart: day((c.at + T.restart_after) * 1000), date: day(T.due * 1000) })
      : th("admin.time.never")}</p>
    ${buttons("card")}
    <details class="time-manual-d" id="time-manual-d"><summary>${th("admin.time.manual")} …</summary>
      <div class="form time-manual"><label>${th("admin.time.manual_label")}<input type="datetime-local" step="1" id="time-local" value="${esc(localValue())}"></label>
        <button type="button" class="btn" data-time="set">${th("admin.time.set")}</button></div>
      <p class="hint">${th("admin.time.manual_hint")}</p></details>
    <p class="hint">${th("admin.time.hint")}</p></div>`;
}

// Handy per USB am Pi: auf das Ergebnis von festpos-handy-usb warten (Einrichtung, Geräteansicht).
// onState(state, result): "wait" (noch nichts), "allow" (auf dem Handy «Zulassen» tippen), "ok", "bad". -> Ergebnis
export async function waitUsb(ip, onState, isStopped, minutes = 10) {
  const start = await api("GET", "/time/usb").catch(() => null);
  const since = start ? start.server_time - 5 : Date.now() / 1000;
  const end = Date.now() + minutes * 60 * 1000;
  onState("wait");
  while (Date.now() < end) {
    await new Promise((ok) => setTimeout(ok, 2000));
    if (isStopped()) return null;
    const r = await api("GET", `/time/usb?since=${since}`).catch(() => null);
    const list = Object.entries(r?.results || {}).map(([serial, v]) => ({ serial, ...v }));
    const mine = list.find((x) => ip && x.ip === ip) || (list.length === 1 ? list[0] : null);
    if (!mine) continue;
    if (mine.ok === null || mine.state === "unauthorized") { onState("allow", mine); continue; }
    onState(mine.ok ? "ok" : "bad", mine);
    return mine;
  }
  onState("timeout");
  return null;
}
export const usbHow = (auto) => `<b>${th("admin.time.usb_plug")}</b>
  <p>${th(auto ? "admin.time.usb_how" : "admin.time.usb_how_manual")}</p>`;
