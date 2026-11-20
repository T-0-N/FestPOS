/* FestPOS – Verwaltung › Fully Kiosk der Handys (nur HTTPS, siehe app/fully.py): Zustand und Aktionen pro Handy
   (Geräteansicht, devices.js), Einstellungen für alle (Einstellungen › Geräte) und der Ablauf «Neues Handy einrichten
   (Werksreset)» (device_add.js). */
import { $, $$, api, esc, fmtDate, fmtTime, guard, piName, toast } from "./core.js";
import { rerender } from "./shell.js";
import { USB_ST, usbHow, waitUsb } from "./clock.js";

// ------------------------------------------------------------------ Zustand (nur HTTPS, siehe app/fully.py)
export let FULLY = { password_set: false, port: 2323, results: {} };
const FULLY_FILE = "fully-remote-admin-ca.p12";
const FULLY_STATE = { ok: "admin.fully.st_ok", no_https: "admin.fully.st_no_https", cert: "admin.fully.st_cert",
  auth: "admin.fully.st_auth", unreachable: "admin.fully.st_unreachable", timeout: "admin.fully.st_timeout", bad: "admin.fully.st_bad" };
// Webinterface von Fully (Remote Admin) im Browser: per HTTPS mit dem Zertifikat, das FestPOS für dieses Handy
// erstellt hat – der Browser fragt beim ersten Mal nach (eigenes Zertifikat), Anmeldung mit dem Fully-Passwort
export const fullyWebUrl = (ip) => `https://${ip.includes(":") ? `[${ip}]` : ip}:${FULLY.port || 2323}/`;
// Zustand eines Handys -> [Klasse, Text] (d: Eintrag mit ip und fully_pin)
export function fullyState(d) {
  if (!d?.fully_pin) return ["", t("admin.fully.st_not_setup")];
  const r = FULLY.results?.[d.ip];
  const key = !FULLY.password_set ? "admin.fully.st_no_password" : r ? FULLY_STATE[r.state] || "admin.fully.st_bad" : "admin.fully.st_none";
  return [r?.state === "ok" && FULLY.password_set ? "ok" : r && FULLY.password_set ? "bad" : "", t(key)];
}

// JavaScript-Schnittstelle nur mit URL Whitelist (app/fully.py web_check): Fully gibt window.fully jeder
// Seite, die es lädt. Warnung, wenn die Schnittstelle an ist und die Whitelist fehlt oder mehr erlaubt als FestPOS.
const WEB_STATE = { ok: ["ok", "admin.fully.web_ok"], off: ["", "admin.fully.web_off"], missing: ["bad", "admin.fully.web_missing"],
  wide: ["bad", "admin.fully.web_wide"], unknown: ["", "admin.fully.web_unknown"] };
export const webWarn = (ip) => ["missing", "wide"].includes(FULLY.results?.[ip]?.web?.state);
// -> [Klasse, Text] oder null (noch nicht geprüft)
export function webState(ip) {
  const w = FULLY.results?.[ip]?.web, s = w && WEB_STATE[w.state];
  // nicht prüfbar: die Namen der Einstellungen dazu, die nach Schnittstelle oder Whitelist klingen (zum Nachtragen)
  const keys = w?.state === "unknown" && w.keys?.length ? ` (${w.keys.join(", ")})` : "";
  return s ? [s[0], t(s[1], { list: (w.foreign || []).join(" · ") }) + keys] : null;
}

// ------------------------------------------------------------------ Aktionen pro Handy (d = {name, ip})
// Rückgabe true, wenn sich etwas geändert hat (die Geräteansicht zeichnet sich dann neu)
export async function fullyCert(d) {
  const r = await guard(() => api("POST", "/fully/cert", { ip: d.ip }, { raw: true }));
  if (!r) return false;
  const url = URL.createObjectURL(await r.blob());
  Object.assign(document.createElement("a"), { href: url, download: FULLY_FILE }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  delete FULLY.results[d.ip];
  toast(t("admin.fully.downloaded", { file: FULLY_FILE }));
  return true;
}
export async function fullyCheck(d) {
  const r = await guard(() => api("POST", "/fully/check", { ip: d.ip }));
  if (!r) return false;
  FULLY.results[d.ip] = r;
  toast(t(FULLY_STATE[r.state] || "admin.fully.st_bad"), r.state !== "ok");
  return true;
}
// Einrichten über Fully: Ergebnis eines Schritts (hochladen, installieren, Einstellung, Neustart) als Meldung
const SETUP_STATE = { ...FULLY_STATE, sent: "admin.fully.cmd_sent", refused: "admin.fully.cmd_refused",
  no_rights: "admin.fully.cmd_no_rights", not_fetched: "admin.fully.not_fetched" };
const SETUP_STEP = { download: "admin.fully.step_download", install: "admin.fully.step_install",
  settings: "admin.fully.step_settings", restart: "admin.fully.step_restart", template: "admin.fully.step_template" };
const setupError = (r) => t("admin.fully.step_failed", { step: t(SETUP_STEP[r.step] || "admin.fully.step_download"),
  state: t(SETUP_STATE[r.state] || "admin.fully.st_bad") }) + (r.text ? ` (${r.text})` : "");
export async function fullyCa(d) {
  if (!confirm(t("admin.fully.ca_confirm", { name: d.name }))) return false;
  const r = await guard(() => api("POST", "/fully/ca", { ip: d.ip }));
  if (!r) return false;
  if (r.state === "ok") toast(t("admin.fully.ca_ok", { name: d.name }));
  else toast(setupError(r), true);
  return false;
}
// Gerätezertifikat über Fully für das Gerät dieses Handys (Erneuern; neuer Name = Umbenennen, T1)
export async function fullyDeviceCert(d, ask = true) {
  if (ask && !confirm(t("admin.fully.devcert_confirm", { name: d.name }))) return false;
  const r = await guard(() => api("POST", "/fully/device-cert", { ip: d.ip, name: d.name }));
  if (!r) return false;
  if (r.step !== "restart") { toast(setupError(r), true); return false; }
  toast(t(r.renewed ? "admin.fully.devcert_renewed" : "admin.fully.devcert_ok",
    { cert: r.name, name: d.name, date: fmtDate(r.expires_at) }), r.state !== "ok" && r.state !== "sent");
  return true;
}
// URL Whitelist auf die Adresse der Bestell-App setzen (ersetzt die bisherige; die Schnittstelle bleibt, wie sie ist)
export async function fullyWhitelist(d) {
  if (!confirm(t("admin.fully.web_confirm", { name: d.name }))) return false;
  const r = await guard(() => api("POST", "/fully/whitelist", { ip: d.ip }));
  if (!r) return false;
  if (r.state !== "ok") { toast(setupError({ ...r, step: "settings" }), true); return false; }
  if (r.web && FULLY.results[d.ip]) FULLY.results[d.ip].web = { ...r.web, seconds: 0 };
  const ok = r.web?.state === "ok";
  toast(t(ok ? "admin.fully.web_done" : "admin.fully.web_not_taken", { name: d.name }), !ok);
  return true;
}
// Einstellungen eines Handys mit der Vorlage vergleichen, Abweichungen zeigen und erst auf Bestätigung setzen
const keyList = (keys) => keys.slice(0, 20).join(", ") + (keys.length > 20 ? ` … (+${keys.length - 20})` : "");
const settingsResult = (name, r) => r.state !== "ok" ? setupError({ ...r, step: "template" })
  : r.failed.length ? t("admin.fully.settings_failed", { name, keys: keyList(r.failed.map((k) => r.errors?.[k] ? `${k} (${r.errors[k]})` : k)) })
  : r.changed.length ? t("admin.fully.settings_changed", { name, count: r.changed.length })
  : t("admin.fully.settings_same", { name });
export async function fullyUpdate(d) {
  if (!confirm(t("admin.fully.update_confirm", { name: d.name }))) return false;
  const r = await guard(() => api("POST", "/fully/update-app", { ip: d.ip }));
  if (r) toast(r.state === "ok" || r.state === "sent" ? t("admin.fully.update_sent", { name: d.name }) : setupError({ ...r, step: "download" }),
    r.state !== "ok" && r.state !== "sent");
  return false;
}
export async function fullySettings(d) {
  const r = await guard(() => api("POST", "/fully/settings", { ip: d.ip }));
  if (!r) return false;
  if (r.state !== "ok" || !r.different.length) { toast(settingsResult(d.name, r), r.state !== "ok"); return false; }
  if (!confirm(t("admin.fully.settings_confirm", { name: d.name, count: r.different.length, keys: keyList(r.different) }))) return false;
  const a = await guard(() => api("POST", "/fully/settings", { ip: d.ip, apply: true }));
  if (a) toast(settingsResult(d.name, a), a.state !== "ok" || a.failed.length > 0);
  return false;
}
// Fernsteuerung (Punkt 7): Kiosk sperren/entsperren, Bildschirm, Helligkeit, Seite neu laden, Cache,
// Vordergrund, ausschalten. Was die Bedienung stört oder das Handy verlässt, erst nach Rückfrage.
const ASK = { unlockKiosk: "admin.fully.ask_unlock", screenOff: "admin.fully.ask_screen_off", clearCache: "admin.fully.ask_cache",
  shutdownDevice: "admin.fully.ask_shutdown", loadStartURL: "admin.fully.ask_reload" };
const DONE = { lockKiosk: "admin.fully.done_lock", unlockKiosk: "admin.fully.done_unlock", screenOn: "admin.fully.done_screen_on",
  screenOff: "admin.fully.done_screen_off", brightness: "admin.fully.done_brightness", loadStartURL: "admin.fully.done_reload",
  clearCache: "admin.fully.done_cache", toForeground: "admin.fully.done_front", shutdownDevice: "admin.devices.restart_sent" };
export async function fullyCmd(d, cmd, value = null) {
  if (ASK[cmd] && !confirm(t(ASK[cmd], { name: d.name }))) return false;
  const r = await guard(() => api("POST", "/fully/command", { ip: d.ip, cmd, ...(value == null ? {} : { value }) }));
  if (!r) return false;
  const good = r.state === "ok" || r.state === "sent";
  if (good && cmd === "clearCache" && r.reload && !["ok", "sent"].includes(r.reload)) {
    toast(t("admin.fully.cache_no_reload", { name: d.name }), true);    // Cache geleert, aber nicht neu geladen
    return false;
  }
  toast(good ? t(DONE[cmd] || "admin.devices.restart_sent", { name: d.name, pct: value == null ? "" : Math.round(value / 2.55) })
    : t(SETUP_STATE[r.state] || FULLY_STATE[r.state] || "admin.fully.st_bad") + (r.text ? ` (${r.text})` : ""), !good);
  return good && (cmd === "lockKiosk" || cmd === "unlockKiosk");       // Kiosk: Zustand neu zeigen
}

// Neu starten: Fully (App) oder das ganze Handy
export async function fullyRestart(d, cmd) {
  if (!confirm(t(cmd === "rebootDevice" ? "admin.devices.reboot_confirm" : "admin.devices.restart_confirm", { name: d.name }))) return false;
  const r = await guard(() => api("POST", "/fully/command", { ip: d.ip, cmd }));
  if (r) toast(r.state === "ok" || r.state === "sent" ? t("admin.devices.restart_sent", { name: d.name }) : t(FULLY_STATE[r.state] || "admin.fully.st_bad"),
    r.state !== "ok" && r.state !== "sent");
  return false;
}

// ------------------------------------------------------------------ Einstellungen › Geräte: Fully-Passwort und Port
export function fullyCard(st) {
  return `<details class="card set-card" data-in="setup" ${st.fully_password_set ? "" : "open"}><summary><h3>${th("admin.fully.title")}</h3>
      <span class="status ${st.fully_password_set ? "ok" : "pending"}">${th(st.fully_password_set ? "admin.devices.fully_pw_set" : "admin.devices.fully_pw_none",
        { port: st.fully_port || 2323 })}</span></summary><form id="fullyform">
    <p class="hint">${th("admin.fully.hint")}</p>
    <div class="form">
      <label>${th(st.fully_password_set ? "admin.fully.password_new" : "admin.fully.password")}<input name="pw" type="password" autocomplete="new-password" maxlength="100" style="width:240px"></label>
      <label>${th("admin.fully.port")}<input name="port" type="number" min="1" max="65535" value="${esc(st.fully_port || FULLY.port || 2323)}" style="width:100px"></label>
      <button class="btn pri">${th("admin.common.save")}</button>
    </div>
  </form></details>`;
}
export function bindFullyCard() {
  $("#fullyform")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { fully_port: +f.port.value || 2323 };
    if (f.pw.value) body.fully_password = f.pw.value;
    if (await guard(() => api("PUT", "/settings", body), t("admin.fully.saved"))) rerender();
  });
}

// ------------------------------------------------------------------ Vorlage: gleiche Fully-Einstellungen auf allen Handys
export function fullyPhones(st) {
  let mon;
  try { mon = JSON.parse(st?.monitor_devices || "[]"); } catch { mon = []; }
  return (Array.isArray(mon) ? mon : []).filter((d) => d.kind === "phone" && d.fully_pin && d.active !== false && d.ip);
}
let TPL_PHONES = [];
export function templateCard(tpl, phones) {
  TPL_PHONES = phones;
  const n = Object.keys(tpl?.settings || {}).length;
  return `<details class="card set-card" id="ftpl" data-in="setup"><summary><h3>${th("admin.fully.template_title")}</h3>
      <span class="status ${n ? "ok" : ""}">${n ? th("admin.fully.template_state", { count: n, source: tpl.source || "–", time: fmtTime(tpl.at) }) : th("admin.fully.template_none")}</span></summary>
    <p class="hint">${th("admin.fully.template_hint")}</p>
    <form class="form" id="ftpl-pin">
      <label>${th(tpl?.pin_set ? "admin.fully.pin_new" : "admin.fully.pin")}<input name="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="32" pattern="[0-9A-Za-z]+" style="width:160px"></label>
      ${tpl?.pin_set ? `<label class="check"><input type="checkbox" name="clear"> ${th("admin.fully.pin_clear")}</label>` : ""}
      <button class="btn">${th("admin.common.save")}</button></form>
    ${phones.length ? `<div class="form">
      <label>${th("admin.fully.template_phone")}<select id="ftpl-src">${phones.map((p) => `<option value="${esc(p.ip)}">${esc(p.name)}</option>`).join("")}</select></label>
      <button type="button" class="btn" id="ftpl-from">${th("admin.fully.template_from")}</button></div>`
      : `<p class="hint">${th("admin.fully.template_no_phone")}</p>`}
    <div class="form" style="margin-top:8px">
      <button type="button" class="btn" id="ftpl-down" ${n ? "" : "disabled"}>${th("admin.fully.template_download")}</button>
      <button type="button" class="btn" id="ftpl-up-btn">${th("admin.fully.template_upload")}</button>
      <input type="file" id="ftpl-up" accept=".json,application/json" hidden>
      <button type="button" class="btn pri" id="ftpl-all" ${(n || tpl?.pin_set) && phones.length ? "" : "disabled"}>${th("admin.fully.template_all")}</button></div>
    <ul class="hint" id="ftpl-out"></ul></details>`;
}
export function bindTemplate() {
  const card = $("#ftpl");
  if (!card) return;
  card.querySelector("#ftpl-from")?.addEventListener("click", async (e) => {
    const sel = $("#ftpl-src"), name = sel.selectedOptions[0]?.textContent || "";
    if (!confirm(t("admin.fully.template_from_confirm", { name }))) return;
    e.target.disabled = true;
    const r = await guard(() => api("POST", "/fully/template/from", { ip: sel.value }));
    e.target.disabled = false;
    if (!r) return;
    if (r.state !== "ok") return toast(setupError({ ...r, step: "template" }), true);
    toast(t("admin.fully.template_saved", { count: r.count, source: r.source }));
    rerender();
  });
  $("#ftpl-pin").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { pin: f.pin.value.trim(), clear: !!f.clear?.checked };
    if (!body.pin && !body.clear) return;
    const r = await guard(() => api("PUT", "/fully/template/pin", body));
    if (!r) return;
    f.pin.value = "";
    toast(t(r.pin_set ? "admin.fully.pin_saved" : "admin.fully.pin_cleared"));
  });
  $("#ftpl-down").addEventListener("click", async () => {
    const tpl = await guard(() => api("GET", "/fully/template"));
    if (!tpl) return;
    const blob = new Blob([JSON.stringify({ settings: tpl.settings }, null, 1)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    Object.assign(document.createElement("a"), { href: url, download: "fully-vorlage.json" }).click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  });
  $("#ftpl-up-btn").addEventListener("click", () => $("#ftpl-up").click());
  $("#ftpl-up").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    let data;
    try { data = JSON.parse(await file.text()); } catch { return toast(t("err.fully.template_bad"), true); }
    const settings = data && typeof data.settings === "object" ? data.settings : data;   // auch reine Fully-Exportdatei
    const r = await guard(() => api("PUT", "/fully/template", { settings }));
    if (!r) return;
    toast(t("admin.fully.template_saved", { count: r.count, source: r.source }) +
      (r.dropped.length ? " " + t("admin.fully.template_dropped", { keys: keyList(r.dropped) }) : ""));
    rerender();
  });
  $("#ftpl-all").addEventListener("click", async (e) => {
    if (!confirm(t("admin.fully.template_all_confirm", { count: TPL_PHONES.length }))) return;
    const out = $("#ftpl-out");
    out.innerHTML = "";
    e.target.disabled = true;
    for (const p of TPL_PHONES) {                     // nacheinander: jedes Handy bekommt die volle Zeit
      let line;
      try { line = settingsResult(p.name, await api("POST", "/fully/settings", { ip: p.ip, apply: true })); }
      catch (err) { line = `${p.name}: ${err.message}`; }
      out.insertAdjacentHTML("beforeend", `<li>${esc(line)}</li>`);
    }
    e.target.disabled = false;
  });
}

// ------------------------------------------------------------------ Einstellungen › Geräte: Einrichtungs-QR und WLAN
// Beide Karten speichern über PUT /fully/provision (alle Felder): die andere Karte schickt ihre gespeicherten Werte mit.
export const SECURITY = { WPA: "admin.fully.roll_sec_wpa", WEP: "admin.fully.roll_sec_wep", NONE: "admin.fully.roll_open" };
let PROV = {};
// Bereit für den Werksreset? -> [ok, Text]. apk: GET /fully/apk (massgebliche APK und Stand pro Pi, T6)
export function provisionState(p, apk) {
  const local = p?.apk_local !== "0";
  if (local && !apk?.meta && !apk?.apk) return [false, t("admin.devices.qr_no_apk")];
  if (local && apk?.meta && !apk.pis?.some((x) => x.state === "ok")) return [false, t("admin.devices.qr_copying")];
  if (!local && (!p.apk_url || !p.checksum)) return [false, t("admin.devices.qr_no_url")];
  if (!p?.ssid) return [false, t("admin.devices.qr_no_wlan")];
  return [true, t(local ? "admin.devices.qr_ready_pi" : "admin.devices.qr_ready_net", { ssid: p.ssid })];
}
const provBody = (over = {}) => ({ ...Object.fromEntries(["apk_url", "checksum", "code", "ssid", "security", "apk_local"].map((k) => [k, PROV[k] ?? ""])), ...over });
const mb = (n) => (n / 1048576).toLocaleString(I18n.locale, { maximumFractionDigits: 1 });
const shortSum = (s) => (s && s.length > 16 ? `${s.slice(0, 8)}…${s.slice(-6)}` : s || "");
export const apkVersion = (m) => m?.version_name || m?.name || "?";

// ------------------------------------------------------------------ Einstellungen › Geräte: Fully-APK (T6)
// Eine APK für alle Pi: hochladen, Version und Prüfsumme (rechnet der Pi), Stand pro Pi, auf die Handys verteilen
const PI_STATE = { ok: ["ok", "admin.fully.apk_pi_ok"], copying: ["pending", "admin.fully.apk_pi_copying"],
  failed: ["bad", "admin.fully.apk_pi_failed"], missing: ["bad", "admin.fully.apk_pi_missing"], off: ["", "admin.fully.apk_pi_off"] };
export function apkCard(apk) {
  const m = apk?.meta, out = apk?.outdated || [];
  const [cls, txt] = !m ? ["pending", t("admin.fully.apk_st_none")]
    : out.length ? ["pending", t("admin.fully.apk_st_outdated", { version: apkVersion(m), count: out.length })]
      : ["ok", t(apk.phones?.length ? "admin.fully.apk_st_ok" : "admin.fully.apk_st_plain", { version: apkVersion(m) })];
  const row = (k, v) => `<tr><th>${th(k)}</th><td>${v}</td></tr>`;
  return `<details class="card set-card" id="fapk" data-in="setup" ${m && !out.length ? "" : "open"}><summary><h3>${th("admin.fully.apk_title")}</h3>
      <span class="status ${cls}">${esc(txt)}</span></summary>
    <p class="hint">${th("admin.fully.apk_hint")}</p>
    ${m ? `<div class="scroll"><table class="tbl apk-tbl"><tbody>
      ${row("admin.fully.apk_file", th("admin.fully.apk_file_val", { name: m.name || "fully.apk", mb: mb(m.size || 0), time: fmtTime(m.at), pi: piName(m.from) }))}
      ${row("admin.fully.apk_version", `${esc(m.version_name || t("admin.fully.apk_unknown"))}${m.version_code ? ` ${th("admin.fully.apk_code", { code: m.version_code })}` : ""}${m.package
        ? ` · ${th("admin.fully.apk_owner_variant")}` : ""}`)}
      ${row("admin.fully.apk_checksum", `<code class="apk-sum">${esc(m.checksum)}</code>`)}
      ${row("admin.fully.apk_pis", `<ul class="apk-pis">${(apk.pis || []).map((x) => { const [c, k] = PI_STATE[x.state] || PI_STATE.off;
        return `<li><span class="status ${c}">${th(k, { pi: piName(x.id), error: x.error || "" })}</span></li>`; }).join("")}</ul>`)}
      ${apk.phones?.length ? row("admin.fully.apk_phones", `${th("admin.fully.apk_phones_val", { current: apk.current || 0, outdated: out.length })}
        – <a href="#devices/list" data-page="devices/list">${th("admin.tab.phones")}</a>`) : ""}
    </tbody></table></div>` : ""}
    <div class="form"><button type="button" class="btn" id="fapk-btn">${th("admin.fully.apk_upload")}</button>
      <input type="file" id="fapk-file" accept=".apk,application/vnd.android.package-archive" hidden>
      ${out.length ? `<button type="button" class="btn pri" id="fapk-push">${th("admin.fully.apk_push")}</button>` : ""}</div></details>`;
}
// Verteilen (nur Admin): Frage, dann Ergebnis pro Handy. ids: nur diese Handys (Geräte › «Erneut verteilen»)
export async function pushApk(version, count, ids) {
  if (!confirm(t("admin.fully.apk_push_confirm", { version, count }))) return false;
  const r = await guard(() => api("POST", "/fully/apk/distribute", ids ? { phones: ids } : {}));
  if (!r) return false;
  const sent = r.results.filter((x) => x.state === "sent").length;
  toast(!r.results.length ? t("admin.fully.apk_push_none")
    : t("admin.fully.apk_push_done", { sent, open: r.results.length - sent }), sent < r.results.length);
  return true;
}
export function bindApk() {
  $("#fapk-btn")?.addEventListener("click", () => $("#fapk-file").click());
  $("#fapk-file")?.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const fd = new FormData();
    fd.append("apk", file);
    toast(t("admin.fully.apk_uploading"));
    const r = await guard(() => api("POST", "/fully/apk", fd), t("admin.fully.apk_saved"));
    if (!r) return;
    if (r.outdated?.length) await pushApk(apkVersion(r.meta), r.outdated.length);       // gleich verteilen?
    rerender();
  });
  $("#fapk-push")?.addEventListener("click", async (e) => {
    const r = await api("GET", "/fully/apk").catch(() => null);
    if (r?.outdated?.length && await pushApk(apkVersion(r.meta), r.outdated.length)) rerender();
    else if (r && !r.outdated?.length) { toast(t("admin.fully.apk_push_none")); e.target.remove(); }
  });
}

// ------------------------------------------------------------------ Einstellungen › Geräte: Einrichtungs-QR und WLAN
// Beide Karten speichern über PUT /fully/provision (alle Felder): die andere Karte schickt ihre gespeicherten Werte mit.
// Fully-APK von «diesem Pi»: Adresse und Prüfsumme weg (die Prüfsumme rechnet der Pi), bei «Internet» Pflicht (T6).
export function provisionCard(p, apk) {
  PROV = p || {};
  const m = apk?.meta || apk?.apk, local = PROV.apk_local !== "0", [ok, txt] = provisionState(PROV, apk);
  return `<details class="card set-card" id="fprov" data-in="setup" open><summary><h3>${th("admin.devices.qr_title")}</h3>
      <span class="status ${ok ? "ok" : "pending"}">${esc(txt)}</span></summary>
    <p class="hint">${th("admin.devices.qr_hint")}</p>
    ${PROV.dhcp ? "" : `<p class="err">${th("admin.fully.roll_no_dhcp")}</p>`}
    <form class="form" id="fprov-form">
      <label>${th("admin.fully.roll_apk_src")}<select name="apk_local">
        <option value="1" ${local ? "selected" : ""}>${th("admin.fully.roll_apk_pi")}</option>
        <option value="0" ${local ? "" : "selected"}>${th("admin.fully.roll_apk_net")}</option></select></label>
      <label class="prov-net grow" ${local ? "hidden" : ""}>${th("admin.fully.roll_apk")}<input name="apk_url" ${local ? "" : "required"} maxlength="300" value="${esc(PROV.apk_url || "")}"></label>
      <label class="prov-net grow" ${local ? "hidden" : ""}>${th("admin.fully.roll_checksum")}<input name="checksum" ${local ? "" : "required"} maxlength="100" value="${esc(PROV.checksum || "")}"></label>
      <label>${th("admin.fully.roll_code")}<input name="code" maxlength="40" value="${esc(PROV.code || "")}" style="width:120px"></label>
      <label>${th(PROV.volume_key_set ? "admin.fully.lic_volume_new" : "admin.fully.lic_volume")}<input name="volume_key" type="password" autocomplete="off" maxlength="200" style="width:240px"></label>
      ${PROV.volume_key_set ? `<label class="check"><input type="checkbox" name="volume_key_clear"> ${th("admin.fully.lic_volume_clear")}</label>` : ""}
      <button class="btn pri">${th("admin.common.save")}</button>
    </form>
    <p class="prov-pi" ${local ? "" : "hidden"}>${m ? th("admin.fully.roll_apk_pi_info", { version: apkVersion(m), sum: shortSum(m.checksum) })
      : th("admin.fully.roll_apk_pi_none")}</p>
    <p class="hint prov-pi" ${local ? "" : "hidden"}>${th("admin.fully.roll_qr_hint_pi")}</p>
    <p class="hint prov-net" ${local ? "hidden" : ""}>${th("admin.fully.roll_qr_hint_net")}</p></details>`;
}
export function bindProvision() {
  const f = $("#fprov-form");
  if (!f) return;
  f.apk_local.addEventListener("change", () => {         // gleich umschalten, gespeichert wird mit «Speichern»
    const net = f.apk_local.value === "0";
    $$("#fprov .prov-net").forEach((el) => { el.hidden = !net; });
    $$("#fprov .prov-pi").forEach((el) => { el.hidden = net; });
    f.apk_url.required = f.checksum.required = net;
  });
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = provBody(Object.fromEntries(["apk_url", "checksum", "code", "apk_local"].map((k) => [k, f[k].value.trim()])));
    if (body.apk_local === "1") Object.assign(body, { apk_url: "", checksum: "" });   // bleiben gespeichert
    if (f.volume_key.value) body.volume_key = f.volume_key.value.trim();
    if (f.volume_key_clear?.checked) body.volume_key_clear = true;
    if (await guard(() => api("PUT", "/fully/provision", body), t("admin.common.saved"))) rerender();
  });
}
// FestPOS-WLAN: steckt im Einrichtungs-QR (Werksreset) und im WLAN-QR der beiden anderen Arten
export function wlanCard(p) {
  PROV = p || {};
  return `<details class="card set-card" id="fwlan" data-in="setup" ${PROV.ssid ? "" : "open"}><summary><h3>${th("admin.devices.wlan_title")}</h3>
      <span class="status ${PROV.ssid ? "ok" : "pending"}">${esc(PROV.ssid ? t("admin.devices.wlan_state", { ssid: PROV.ssid }) : t("admin.devices.wlan_none"))}</span></summary>
    <p class="hint">${th("admin.devices.wlan_hint")}</p>
    <form class="form" id="fwlan-form">
      <label>${th("admin.fully.roll_ssid")}<input name="ssid" maxlength="32" value="${esc(PROV.ssid || "")}" style="width:200px"></label>
      <label>${th("admin.fully.roll_security")}<select name="security">${["WPA", "WEP", "NONE"].map((v) =>
        `<option value="${v}" ${v === (PROV.security || "WPA") ? "selected" : ""}>${th(SECURITY[v])}</option>`).join("")}</select></label>
      <label>${th(PROV.wifi_password_set ? "admin.fully.roll_wifi_pw_new" : "admin.fully.roll_wifi_pw")}<input name="wifi_password" type="password" autocomplete="new-password" maxlength="63" style="width:200px"></label>
      <button class="btn pri">${th("admin.common.save")}</button>
    </form></details>`;
}
export function bindWlan() {
  const f = $("#fwlan-form");
  f?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = provBody({ ssid: f.ssid.value.trim(), security: f.security.value });
    if (f.wifi_password.value) body.wifi_password = f.wifi_password.value;
    if (await guard(() => api("PUT", "/fully/provision", body), t("admin.common.saved"))) rerender();
  });
}

// ------------------------------------------------------------------ Neues Handy einrichten (Werksreset)
// Hauptweg: «QR für dieses Handy» – Android installiert Fully, Fully holt seine Einstellungen beim Pi, der Pi gibt
// ihm per DHCP die feste Adresse; danach rollt die Verwaltung von selbst aus. Ohne QR: für ein Handy, das schon im
// WLAN ist und dessen Fernverwaltung läuft. Die Seite braucht ein <ol id="froll-steps"> und ein #froll-qr.
const ROLL_STATE = { ...SETUP_STATE, already: "admin.fully.roll_already", plain_needed: "admin.fully.roll_plain_needed",
  no_https: "admin.fully.roll_no_https", cert: "admin.fully.roll_cert", pw_not_switched: "admin.fully.roll_pw_not_switched", unreachable: "admin.fully.st_unreachable",
  timeout: "admin.fully.roll_no_address" };
const ROLL_STEPS = [["net", "admin.fully.roll_s_net"], ["scan", "admin.fully.roll_s_scan"], ["addr", "admin.fully.roll_s_addr"], ["boot", "admin.fully.roll_s_boot"],
  ["ca", "admin.fully.roll_s_ca"], ["cert", "admin.fully.roll_s_cert"], ["tpl", "admin.fully.roll_s_tpl"],
  ["lic", "admin.fully.roll_s_lic"], ["usb", "admin.fully.roll_s_usb"]];
let HAS_TEMPLATE = false;
export function setHasTemplate(v) { HAS_TEMPLATE = !!v; }
let ROLL_RUN = 0;                                       // laufender Ablauf (ein neuer bricht den alten ab)
export function stopRollout() { ROLL_RUN++; }
// Internet beim Ausrollen über diesen Pi (dieselbe Verbindung wie «Update aus dem Internet»)
export function shareVia(sh) {
  if (!sh.ready) return "";
  if (sh.via === "wlan") return t("admin.fully.share_via_wlan", { ssid: sh.ssid });
  return t(sh.via === "usb" ? "admin.fully.share_via_usb" : "admin.fully.share_via_lan");
}
const rollMsg = (r) => t(ROLL_STATE[r.state] || "admin.fully.st_bad") + (r.text ? ` (${r.text})` : "");
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
export async function runRollout(opts) {
  let shared = false;
  try { await rollout(opts, () => { shared = true; }); }
  finally {
    if (shared) await api("POST", "/fully/internet", { on: false }).catch(() => null);   // immer wieder aus
  }
}
async function rollout(opts, sharedOn) {
  const { name, ip, cert, token, share } = opts;
  const run = ++ROLL_RUN;
  const list = $("#froll-steps"), btns = $$(".roll-lock");          // Knöpfe des Ablaufs, die während eines Schritts ruhen
  // «usb» (Zeitserver per USB, T2) nur beim Werksreset: nur als Geräteinhaber kann Fully ADB einschalten
  const steps = ROLL_STEPS.filter(([k]) => (token || (k !== "scan" && k !== "addr" && k !== "usb")) && (share || k !== "net"));
  list.hidden = false;
  list.innerHTML = steps.map(([k, label]) => `<li data-step="${k}"><span class="st"></span> ${th(label)} <span class="hint"></span></li>`).join("");
  const mark = (k, cls, msg = "") => {
    const li = list.querySelector(`[data-step="${k}"]`);
    li.className = cls;
    li.querySelector(".hint").textContent = msg;
  };
  const call = async (method, path, body) => { try { return await api(method, path, body); } catch (err) { return { state: "error", text: err.message }; } };
  const done = () => { btns.forEach((b) => { b.disabled = false; }); };
  const fail = (k, r) => { mark(k, "bad", r.state === "error" ? r.text : rollMsg(r)); done(); };
  const stale = () => run !== ROLL_RUN;
  btns.forEach((b) => { b.disabled = true; });
  let r;
  if (share) {
    mark("net", "run", t("admin.fully.share_starting"));
    r = await call("POST", "/fully/internet", { on: true });
    if (r.state !== "on") return fail("net", { state: "error", text: r.text || t("admin.fully.st_bad") });
    sharedOn();
    mark("net", "ok", shareVia({ ...r, ready: true }));
  }
  if (token) {
    // 0. QR scannen: warten, bis Fully seine Einstellungen abholt (höchstens 30 Minuten)
    mark("scan", "run", t("admin.fully.roll_waiting"));
    btns.forEach((b) => { b.disabled = false; });       // warten blockiert nichts: ein neuer QR bricht ab
    for (;;) {
      await sleep(2000);
      if (stale()) return;
      r = await call("GET", `/fully/rollout/${token}`);
      if (r.state === "fetched") break;
      if (r.state === "gone") return fail("scan", { state: "error", text: t("admin.fully.roll_expired") });
    }
    btns.forEach((b) => { b.disabled = true; });
    $("#froll-qr").hidden = true;
    mark("scan", "ok", t("admin.fully.roll_fetched", { ip: r.seen_ip, mac: r.mac || "?" }));
    if (!r.mac) return fail("addr", { state: "error", text: t("admin.fully.roll_no_mac") });
    // 1. feste Adresse: das Handy holt sie nach spätestens etwa 2 Minuten (DHCP), Fully antwortet dann dort
    mark("addr", "run", t("admin.fully.roll_addr_wait", { ip }));
    mark("boot", "run", t("admin.fully.roll_boot_wait"));
    const end = Date.now() + 5 * 60 * 1000;
    for (;;) {
      await sleep(5000);
      if (stale()) return;
      r = await call("POST", "/fully/bootstrap", { ip, name });   // mit dem Einmal-Passwort aus dem QR
      if (!["unreachable", "timeout"].includes(r.state) || Date.now() > end) break;
    }
    if (["unreachable", "timeout"].includes(r.state)) return fail("addr", { state: "timeout" });
    mark("addr", "ok", ip);
  } else {
    // Handy ist schon im WLAN: mit dem Einmal-Passwort aus «prepare» (opts.prep), sonst unverschlüsselt erst nach Bestätigung
    r = await call("POST", "/fully/bootstrap", { ip, name, ...(opts.prep ? { token: opts.prep } : {}) });
    if (r.state === "plain_needed") {
      if (!confirm(t("admin.fully.roll_plain_confirm", { name }))) return fail("boot", r);
      r = await call("POST", "/fully/bootstrap", { ip, name, plain_ok: true });
    }
  }
  if (r.state !== "ok" && r.state !== "already") return fail("boot", r);
  mark("boot", "ok", r.state === "already" ? t("admin.fully.roll_already") : r.rebooted ? t("admin.fully.roll_rebooted") : "");
  // CA, Gerätezertifikat
  mark("ca", "run");
  r = await call("POST", "/fully/ca", { ip });
  if (r.state !== "ok") return fail("ca", r.state === "error" ? r : { state: "error", text: setupError(r) });
  mark("ca", "ok");
  mark("cert", "run");
  r = await call("POST", "/fully/device-cert", { ip, name: cert });
  if (r.step !== "restart") return fail("cert", r.state === "error" ? r : { state: "error", text: setupError(r) });
  mark("cert", "ok", t("admin.fully.roll_cert_ok", { cert: r.name, date: fmtDate(r.expires_at) }));
  // Vorlage – nach dem Neustart der App ist Fully kurz weg: ein paar Mal versuchen
  if (!HAS_TEMPLATE) mark("tpl", "", t("admin.fully.roll_no_template"));
  else {
    mark("tpl", "run");
    for (let i = 0; i < 8; i++) {
      await sleep(4000);
      r = await call("POST", "/fully/settings", { ip, apply: true });
      if (r.state === "ok") break;
    }
    if (r.state !== "ok") return fail("tpl", r);
    mark("tpl", r.failed.length ? "bad" : "ok", settingsResult(name, r));
  }
  // Fully-Lizenz: Volumenlizenz setzt FestPOS, eine Einzellizenz weist man im Lizenzportal der Geräte-ID zu –
  // dann warten, bis Fully «lizenziert» meldet (Fully prüft online: das Internet bleibt bis dahin an)
  mark("lic", "run");
  r = await call("POST", "/fully/license", { ip });
  if (r.state === "ok" && !r.licensed && r.volume_key_set) r = await call("POST", "/fully/license", { ip, volume: true });
  if (r.state !== "ok") return fail("lic", r);
  if (!r.licensed) {
    const li = list.querySelector('[data-step="lic"]');
    li.insertAdjacentHTML("beforeend", `<div class="roll-lic">${th(r.volume_key_set ? "admin.fully.lic_wait_volume" : "admin.fully.lic_how",
      { id: r.device_id || "?" })}<div class="form">
        <button type="button" class="btn sm" id="froll-lic-restart">${th("admin.fully.lic_restart")}</button>
        <button type="button" class="btn sm" id="froll-lic-skip">${th("admin.fully.lic_skip")}</button></div></div>`);
    let skip = false;
    // Fully prüft die Lizenz beim Start: alle 2 Minuten neu starten (oder sofort per Knopf)
    const restart = async () => {
      mark("lic", "run", t("admin.fully.lic_restarting"));
      await call("POST", "/fully/command", { ip, cmd: "restartApp" });
    };
    $("#froll-lic-skip").addEventListener("click", () => { skip = true; });
    $("#froll-lic-restart").addEventListener("click", restart);
    const end = Date.now() + 25 * 60 * 1000;
    let next = Date.now() + 2 * 60 * 1000;
    while (!skip && !r.licensed && Date.now() < end) {
      mark("lic", "run", t("admin.fully.lic_waiting"));
      await sleep(10000);
      if (stale()) return;
      if (Date.now() > next) { next = Date.now() + 2 * 60 * 1000; await restart(); await sleep(8000); }
      const c = await call("POST", "/fully/license", { ip });
      if (c.state === "ok") r = c;
    }
    li.querySelector(".roll-lic")?.remove();
  }
  mark("lic", r.licensed ? "ok" : "bad", t(r.licensed ? "admin.fully.lic_ok" : "admin.fully.lic_missing", { id: r.device_id || "?" }));
  const licensed = r.licensed;
  if (token) await usbStep(list, mark, call, ip, stale);
  if (stale()) return;
  r = { licensed };
  done();
  toast(t(r.licensed ? "admin.fully.roll_done" : "admin.fully.roll_done_unlicensed", { name }), !r.licensed);
}

// Zeitserver per USB (T2): Fully schaltet ADB ein, das Handy kommt ans Kabel eines Pi, festpos-handy-usb trägt den Pi als
// Zeitserver ein und schaltet ADB wieder aus. Kann Fully ADB nicht schalten: Anleitung über die Entwickleroptionen.
async function usbStep(list, mark, call, ip, stale) {
  mark("usb", "run", t("admin.time.usb_adb_on"));
  const adb = await call("POST", "/fully/adb", { ip, on: true });
  const auto = adb.state === "ok";
  const li = list.querySelector('[data-step="usb"]');
  li.insertAdjacentHTML("beforeend", `<div class="roll-usb">${usbHow(auto)}
    <button type="button" class="btn sm" id="froll-usb-skip">${th("admin.time.usb_skip")}</button></div>`);
  let skip = false;
  $("#froll-usb-skip").addEventListener("click", () => { skip = true; });
  const res = await waitUsb(ip, (st, x) => mark("usb", st === "ok" ? "ok" : st === "bad" || st === "timeout" ? "bad" : "run",
    t(USB_ST[st], { model: x?.model || "" })), () => skip || stale());
  if (auto) await call("POST", "/fully/adb", { ip, on: false });      // Fully schaltet ADB sonst wieder ein
  li.querySelector(".roll-usb")?.remove();
  if (skip) mark("usb", "", t("admin.time.usb_skipped"));
  else if (res) mark("usb", res.ok ? "ok" : "bad", t(res.ok ? "admin.time.usb_st_ok" : "admin.time.usb_st_bad", { model: res.model || "" }));
}

// von anderen Modulen gesetzt (importierte Variablen sind dort nur lesbar)
export function setFully(v) { FULLY = v; }
