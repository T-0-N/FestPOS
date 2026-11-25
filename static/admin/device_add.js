/* FestPOS – Verwaltung › Geräte › «Gerät hinzufügen» (T5): drei Arten, jede nur mit den Feldern und Schritten, die
   sie braucht. Werksreset: Einrichtungs-QR mit den Einstellungen für alle (Einstellungen › Geräte), danach rollt die
   Verwaltung von selbst aus. Fully auf vorhandenem Handy und Nur Zugang: zuerst der WLAN-QR, dann Fully mit einem
   Einmal-Passwort bzw. das Gerätezertifikat. Ab Festleitung; die Einstellungen für alle ändert nur der Admin. */
import { $, $$, api, can, esc, fmtDate, guard, netEx } from "./core.js";
import { pairTimer, RENDER, setPairTimer, show } from "./shell.js";
import { FULLY, provisionState, runRollout, setFully, setHasTemplate, stopRollout } from "./fully.js";

export const ROLES = [["service", "admin.devices.role_service"], ["storno", "admin.devices.role_storno"], ["lead", "admin.devices.role_lead"],
  ["status", "admin.devices.role_status"]];                 // nur die Statusanzeige
export const roleSelect = (value, attrs = "") => `<select ${attrs}>${ROLES.map(([v, k]) =>
  `<option value="${v}" ${value === v ? "selected" : ""}>${th(k)}</option>`).join("")}</select>`;
const KINDS = [["reset", "admin.devices.k_reset", "admin.devices.k_reset_sub"], ["fully", "admin.devices.k_fully", "admin.devices.k_fully_sub"],
  ["access", "admin.devices.k_access", "admin.devices.k_access_sub"]];
const NAME_RE = /^[\w .\-äöüÄÖÜ]{1,30}$/;
const IP_RE = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

let W = null;                                          // laufender Ablauf (null = keiner)
export const adding = () => !!W;

// ctx: {names, ips} (Namen gültiger Geräte, klein geschrieben – gesperrte geben ihren Namen frei), nextName, nextIp
export function startAdd(ctx) {
  W = { step: 0, kind: "reset", role: "service", name: ctx.nextName, ip: ctx.nextIp, ...ctx };
  draw();
}
function stop() {
  stopRollout();
  clearInterval(pairTimer);
  W = null;
}
const steps = () => (W.kind === "reset" ? ["kind", "data", "setup"] : ["kind", "data", "wlan", "setup"]);
const STEP_KEY = { kind: "admin.devices.s_kind", data: "admin.devices.s_data", wlan: "admin.devices.s_wlan", setup: "admin.devices.s_setup" };

function frame(body, next, { back = true, nextOff = false, nextKind = "pri" } = {}) {
  const all = steps();
  return `<h2>${th("admin.devices.add_title")}</h2><div class="card wiz">
    <div class="wtop"><ol class="wsteps">${all.map((s, i) => `<li class="${i === W.step ? "on" : i < W.step ? "done" : ""}">${th(STEP_KEY[s], { n: i + 1 })}</li>`).join("")}</ol>
      <button type="button" class="linklike" id="wiz-cancel">${th("admin.common.cancel")}</button></div>
    ${body}
    <div class="wiz-foot">${back ? `<button type="button" class="btn roll-lock" id="wiz-back">${th("admin.devices.back")}</button>` : ""}
      ${next ? `<button type="button" class="btn ${nextKind}" id="wiz-next" ${nextOff ? "disabled" : ""}>${esc(next)}</button>` : ""}</div></div>`;
}
// grosser QR mit «Gross anzeigen» (bildschirmfüllend, schwarz auf weiss): der Einrichtungs-QR ist dicht (~125×125 Punkte)
const qrBlock = (svg, title) => `<div class="qrcol"><div class="qr-big">${svg}</div>
  <button type="button" class="btn qr-full-btn" data-title="${esc(title)}">${th("admin.devices.qr_big")}</button></div>`;
function bigQr(svg, title) {
  const box = document.createElement("div");
  box.className = "qrfull";
  box.setAttribute("role", "dialog");
  box.innerHTML = `<div class="qrfull-head"><b>${esc(title)}</b><button type="button" class="btn">${th("admin.devices.qr_close")}</button></div>
    <div class="qrfull-qr">${svg}</div>`;
  const close = () => { box.remove(); removeEventListener("keydown", esc_); };
  const esc_ = (e) => { if (e.key === "Escape") close(); };
  box.querySelector("button").addEventListener("click", close);
  addEventListener("keydown", esc_);
  document.body.append(box);
}
function warnBox(keys, settingsLink) {
  return `<div class="warnbox">${[].concat(keys).map((k) => `<p>${th(k)}</p>`).join("")}
    ${can("admin") ? `<button type="button" class="btn" data-goto="${settingsLink}">${th("admin.devices.open_settings")}</button>`
      : `<p>${th("admin.devices.ask_admin")}</p>`}</div>`;
}

async function draw() {
  if (!W) return;
  const s = steps()[W.step];
  clearInterval(pairTimer);
  if (s === "kind") return drawKind();
  if (s === "data") return drawData();
  if (s === "wlan") return drawWlan();
  if (W.kind === "reset") return drawReset();
  if (W.kind === "fully") return drawFully();
  return drawAccess();
}
function bind(onNext) {
  $("#wiz-cancel").addEventListener("click", () => { stop(); RENDER.devices(); });
  $("#wiz-back")?.addEventListener("click", () => { stopRollout(); W.step--; draw(); });
  $("#wiz-next")?.addEventListener("click", onNext);
  $$("#main .qr-full-btn").forEach((b) => b.addEventListener("click", () => bigQr(b.previousElementSibling.innerHTML, b.dataset.title)));
  $$("#main [data-goto]").forEach((b) => b.addEventListener("click", () => { stop(); show(b.dataset.goto); }));
}

// -- 1. Art -------------------------------------------------------------------------------------------------
function drawKind() {
  $("#main").innerHTML = frame(`<h3>${th("admin.devices.kind_q")}</h3><div class="choice">
    ${KINDS.map(([v, k, sub]) => `<label><input type="radio" name="kind" value="${v}" ${W.kind === v ? "checked" : ""}>
      <b>${th(k)}</b><span>${th(sub)}</span></label>`).join("")}</div>`, t("admin.devices.next"), { back: false });
  bind(async () => {
    W.kind = $('input[name="kind"]:checked').value;
    W.step = 1;
    draw();
  });
}

// -- 2. Angaben -------------------------------------------------------------------------------------------------
async function drawData() {
  const needFully = W.kind !== "access";
  const [fullyNow, prov, apk] = await Promise.all([needFully ? api("GET", "/fully").catch(() => null) : null,
    W.kind === "reset" ? api("GET", "/fully/provision").catch(() => null) : null,
    W.kind === "reset" ? api("GET", "/fully/apk").catch(() => null) : null]);
  if (!W) return;
  if (fullyNow) setFully(fullyNow);
  const missing = [];
  let ready = "";
  if (needFully && !FULLY.password_set) missing.push("admin.devices.need_fully_pw");
  if (W.kind === "reset") {
    const [qrOk, txt] = provisionState(prov, apk);
    W.prov = prov;
    if (!qrOk) missing.push("admin.devices.need_qr");
    else ready = `<div class="readyline"><span class="status ok">${th("admin.devices.qr_ready")}</span> <span>${esc(txt)}</span></div>`;
  }
  const ok = !missing.length;
  let warn = ok ? "" : warnBox(missing, "devices/setup");
  if (W.kind === "reset" && prov && !prov.dhcp) warn += `<p class="err">${th("admin.fully.roll_no_dhcp")}</p>`;
  const title = KINDS.find(([v]) => v === W.kind)[1];
  $("#main").innerHTML = frame(`<h3>${th(title)}</h3>${warn}${ready}
    <form class="form" id="wiz-data" novalidate>
      <label>${th("admin.devices.name")}<input name="name" required maxlength="30" value="${esc(W.name)}" style="width:200px"></label>
      ${W.kind === "reset" ? `<label>${th("admin.devices.fixed_ip")}<input name="ip" required maxlength="15" inputmode="decimal" value="${esc(W.ip)}" style="width:170px"></label>` : ""}
      <label>${th("admin.devices.role")}${roleSelect(W.role, 'name="role"')}</label>
    </form>
    <p class="hint">${th({ reset: "admin.devices.data_hint_reset", fully: "admin.devices.data_hint_fully", access: "admin.devices.data_hint_access" }[W.kind])}</p>`,
  t(W.kind === "reset" ? "admin.devices.make_qr" : "admin.devices.next"), { nextOff: !ok });
  bind(() => {
    const f = $("#wiz-data"), name = f.name.value.trim(), ip = f.ip?.value.trim();
    W.role = f.role.value;
    if (!NAME_RE.test(name)) return err(f.name, t("admin.devices.bad_name"));
    if (W.names.has(name.split(/\s+/).join(" ").toLowerCase())) return err(f.name, t("err.device.name_taken", { name }));
    if (f.ip && !IP_RE.test(ip)) return err(f.ip, t("err.device.bad_ip", { name }));
    if (f.ip && W.ips.has(ip)) return err(f.ip, t("admin.devices.ip_used", { ip }));
    Object.assign(W, { name, ip: ip || W.ip, step: W.step + 1 });
    draw();
  });
}
function err(input, msg) {
  input.setCustomValidity(msg);
  input.reportValidity();
  input.addEventListener("input", () => input.setCustomValidity(""), { once: true });
}
// Rolle: geht beim Anlegen mit (sie hängt am Gerät, nicht am Namen – T1); «Bedienung» ist der Standard
const role = () => (W.role === "service" ? {} : { role: W.role });

// -- 3. WLAN (Fully auf vorhandenem Handy, Nur Zugang) --------------------------------------------------------
async function drawWlan() {
  const r = await guard(() => api("GET", "/devices/wifi-qr"));
  if (!W) return;
  const body = r?.svg ? `<div class="qrrow">${qrBlock(r.svg, t("admin.devices.wlan_qr_title", { ssid: r.ssid }))}
      <div class="qrtxt"><p>${th("admin.devices.wlan_scan", { ssid: r.ssid })}</p>
        <p class="hint">${th("admin.devices.wlan_mac", { ssid: r.ssid })}</p><p class="hint">${th("admin.devices.qr_secret")}</p></div></div>`
    : warnBox("admin.devices.need_wlan", "devices/setup");
  $("#main").innerHTML = frame(`<h3>${th("admin.devices.wlan_step", { name: W.name })}</h3>${body}`, t("admin.devices.next"));
  bind(() => { W.step++; draw(); });
}

// -- Einrichten: Werksreset ------------------------------------------------------------------------------------
async function drawReset() {
  const [r, tpl] = await Promise.all([guard(() => api("POST", "/fully/rollout", { name: W.name, ip: W.ip, cert: W.name, ...role() })),
    api("GET", "/fully/template").catch(() => null)]);
  if (!W) return;
  if (!r) { W.step--; return draw(); }
  setHasTemplate(Object.keys(tpl?.settings || {}).length > 0);
  const sh = W.prov?.share;
  const share = !!(sh?.ready && sh.allowed !== false && W.prov?.dhcp);
  $("#main").innerHTML = frame(`<h3>${th("admin.devices.reset_step", { name: W.name })}</h3>
    <div class="qrrow"><div id="froll-qr">${qrBlock(r.svg, t("admin.devices.reset_step", { name: W.name }))}</div>
      <div class="qrtxt"><p>${th("admin.devices.reset_scan")}</p>
        <ol class="roll-steps" id="froll-steps" hidden></ol>
        <p class="hint">${th("admin.devices.reset_wait")}</p><p class="hint">${th("admin.devices.qr_secret")}</p>
        <button type="button" class="btn sm roll-lock" id="wiz-noqr">${th("admin.devices.reset_noqr")}</button></div></div>`,
  t("admin.devices.done"));
  bind(finish);
  const go = (token) => runRollout({ name: W.name, ip: W.ip, cert: W.name, token, share });
  $("#wiz-noqr").addEventListener("click", () => { stopRollout(); $("#froll-qr").hidden = true; go(undefined); });
  go(r.id);
}

// -- Einrichten: Fully auf vorhandenem Handy -------------------------------------------------------------------
async function drawFully() {
  const r = await guard(() => api("POST", "/fully/prepare", { name: W.name }));
  if (!W) return;
  if (!r) { W.step--; return draw(); }
  $("#main").innerHTML = frame(`<h3>${th("admin.devices.fully_step", { name: W.name })}</h3>
    <ol class="steps fully-steps"><li>${th("admin.devices.fully_s1")}</li><li>${th("admin.devices.fully_s2", { port: FULLY.port || 2323 })}</li></ol>
    <div class="bigpw">${esc(r.temp_pw)}</div>
    <ol class="steps fully-steps" start="3"><li>${th("admin.devices.fully_s3")}</li></ol>
    <form class="form" id="wiz-ip" novalidate>
      <label>${th("admin.devices.phone_ip")}<input name="ip" required maxlength="15" inputmode="decimal" placeholder="${esc(netEx("phone"))}" style="width:170px"></label>
      <button class="btn pri roll-lock">${th("admin.devices.fully_go")}</button></form>
    <ol class="roll-steps" id="froll-steps" hidden></ol>
    <p class="hint">${th("admin.devices.fully_hint")}</p>`, t("admin.devices.done"), { nextKind: "" });
  bind(finish);
  $("#wiz-ip").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, ip = f.ip.value.trim();
    if (!IP_RE.test(ip)) return err(f.ip, t("err.device.bad_ip", { name: W.name }));
    if (W.ips.has(ip)) return err(f.ip, t("admin.devices.ip_used", { ip }));
    if (!await guard(() => api("PUT", "/devices/phone", { name: W.name, ip, active: true, here: true, ...role() }))) return;
    W.ips.add(ip);
    runRollout({ name: W.name, ip, cert: W.name, prep: r.id, share: false });
  });
}

// -- Einrichten: Nur Zugang (Gerätezertifikat für den Browser) ---------------------------------------------------
async function drawAccess() {
  const r = await guard(() => api("POST", "/devices/cert", { name: W.name, ...role() }));
  if (!W) return;
  if (!r) { W.step--; return draw(); }
  $("#main").innerHTML = frame(`<h3>${th("admin.devices.access_step", { name: W.name })}</h3>
    <div class="qrrow">${qrBlock(r.svg, t("admin.devices.access_step", { name: W.name }))}
      <div class="qrtxt">${pairText(r, W.name)}</div></div>`, t("admin.devices.done"), { back: false });
  bind(finish);
  countdown();
}
// Text neben dem QR eines Gerätezertifikats (auch für «Neues Zertifikat» in der Geräteansicht)
export function pairText(r, name) {
  return `<p>${th("admin.devices.pair_on", { name, date: fmtDate(r.expires_at) })}</p>
    <ol class="steps"><li>${th("admin.devices.pair_step1")}</li><li>${th("admin.devices.pair_step2")}</li><li>${th("admin.devices.pair_step3")}</li></ol>
    <div class="bigpw">${esc(r.password)}</div>
    <p class="hint" id="certcount"></p>
    <p class="hint">${th("admin.devices.pair_after", { name })}</p>
    <a href="#" class="link" data-dlonce="${esc(r.url)}" data-name="${esc(r.filename)}">${th("admin.devices.pair_pc")}</a>`;
}
// Einmal-Link gilt 10 Minuten
export function countdown(onEnd) {
  const until = Date.now() + 10 * 60000;
  clearInterval(pairTimer);
  const tick = () => {
    const left = Math.round((until - Date.now()) / 1000), el = $("#certcount");
    if (!el) { clearInterval(pairTimer); return; }
    if (left <= 0) { clearInterval(pairTimer); el.textContent = t("admin.devices.link_expired"); onEnd?.(); return; }
    el.textContent = t("admin.devices.link_left", { time: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` });
  };
  setPairTimer(setInterval(tick, 1000));
  tick();
}
async function finish() {
  stop();
  await RENDER.devices();
}
