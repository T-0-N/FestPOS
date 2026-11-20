/* FestPOS – Verwaltung › Geräte & Netz › Reiter «Handhelds & PC» (T5, T34): jedes Gerät genau einmal. Zusammengeführt aus den Gerätezertifikaten
   (device_certs), den Handys auf der Statusanzeige (monitor_devices) und den Rollen – über die Geräte-ID (T1), nie über
   den Namen. Gleichnamige gültige Geräte (Altbestand) sind als «Name doppelt» markiert.
   Liste: Rolle und «Im Einsatz» direkt; alles andere in der Geräteansicht («Bearbeiten»). Ab Festleitung;
   entsperren und löschen nur Admin, die Einstellungen für alle stehen im Reiter «Einstellungen» (settings.js). */
import { $, $$, api, can, esc, flash, fmtDate, fmtTime, guard, toast } from "./core.js";
import { inDays, pageHead, RENDER, view } from "./shell.js";
import { apkVersion, FULLY, fullyCa, fullyCert, fullyCheck, fullyCmd, fullyDeviceCert, fullyRestart, fullySettings,
  fullyState, fullyUpdate, fullyWebUrl, fullyWhitelist, pushApk, setFully, webState, webWarn } from "./fully.js";
import { adding, countdown, pairText, roleSelect, startAdd } from "./device_add.js";
import { loadTime, USB_ST, usbHow, waitUsb } from "./clock.js";

// Vergleichsform eines Namens (wie der Server, device_ids.norm): ohne Gross-/Kleinschreibung, Leerzeichen zusammengefasst
export const nameKey = (name) => String(name || "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
let OPEN = null;                                        // Geräteansicht: Schlüssel des offenen Geräts, sonst Liste
document.addEventListener("click", (e) => {             // Menü «Geräte & Netz» oder Reiter «Handhelds & PC»: zuerst die Liste
  if (e.target.closest('.side [data-page="devices"], .tabs [data-tab="list"]')) OPEN = null;
}, true);
let DEVS = [], INFO = null, APK = null, TAP = null;   // TAP: GET /payrexx – App «FestPOS Tap» und was wohin ging              // APK: GET /fully/apk (Fully-Version pro Handy, T6)

function phonesOf(st) {
  let mon;
  try { mon = JSON.parse(st?.monitor_devices || "[]"); } catch { mon = []; }
  return (Array.isArray(mon) ? mon : []).filter((d) => d && d.kind === "phone");
}
// Ein Gerät = eine ID: Zertifikate über device_id (bleibt bei Erneuerung gleich), Handys über ihre id; ein Handy mit
// cert gehört zum Gerät mit diesem Zertifikat.
function merge(d, st) {
  const map = new Map();
  for (const c of d.certs) {                            // pro Gerät neuestes zuerst (Server sortiert)
    const k = c.device_id || c.serial;
    if (!map.has(k)) map.set(k, { key: k, dev: k, name: c.name, certs: [], phone: null });
    map.get(k).certs.push(c);
  }
  for (const p of phonesOf(st)) {
    const x = p.cert && map.get(p.cert);
    if (x && !x.phone) x.phone = p;
    else { const k = p.id || `ip:${p.ip}`; map.set(k, { key: k, dev: null, name: p.name, certs: [], phone: p }); }
  }
  for (const x of map.values()) {
    x.role = (x.dev ? d.roles?.[x.dev] : x.phone?.role) || "service";
    x.cert = x.certs[0] || null;
    // alle Zertifikate gesperrt: unter «Gesperrt», auch mit Handy-Eintrag – der bleibt fürs Entsperren (T3)
    x.blocked = x.certs.length > 0 && x.certs.every((c) => c.revoked_at);
    x.seen = (x.dev ? d.last_seen?.[x.dev] : null) ?? (x.phone ? d.last_seen_ip?.[x.phone.ip] : null) ?? null;
    x.valid = x.cert ? certValid(x.cert) : !!x.phone;
    // gerade im Testmodus (T7): mit Zertifikat über die Geräte-ID, sonst über die IP
    x.test = (d.test || []).find((e) => (x.dev && e.key === `c:${x.dev}`) || (!x.dev && x.phone && e.ip === x.phone.ip)) || null;
  }
  // Name doppelt (Altbestand vor T1): mehrere gültige Geräte gleichen Namens – gesperrte und abgelaufene zählen nicht
  const count = new Map();
  for (const x of map.values()) if (x.valid) count.set(nameKey(x.name), (count.get(nameKey(x.name)) || 0) + 1);
  for (const x of map.values()) x.dup = x.valid && count.get(nameKey(x.name)) > 1;
  return [...map.values()].sort((a, b) => a.name.localeCompare(b.name, I18n.locale, { numeric: true }) || (a.key < b.key ? -1 : 1));
}
const certValid = (c) => !c.revoked_at && !(c.days_left != null && c.days_left < 0);
// Zustand des Zertifikats -> [Klasse, kurzer Text]
function certState(x, warn) {
  const c = x.cert;
  if (!c) return ["", t("admin.devices.no_cert")];
  if (c.revoked_at) return ["bad", t("admin.devices.revoked")];
  if (c.days_left == null) return ["", t("admin.devices.no_expiry")];
  if (c.days_left < 0) return ["bad", t("admin.devices.expired")];
  if (c.days_left <= warn) return ["pending", t("admin.devices.expires_in", { count: c.days_left })];
  return ["ok", t("admin.devices.valid_until", { date: fmtDate(c.expires_at) })];
}
// Erneuern (T3): macht es der Pi selbst (Fully) oder jemand von Hand? Nur nahe am Ablauf (Warnung) oder wenn es läuft.
function renewNote(x, short = false) {
  const c = x.cert, m = x.dev && INFO.renew?.[x.dev];
  if (!c || !m || c.revoked_at || c.days_left == null || c.days_left < 0) return "";
  if (m.state !== "running" && m.mode !== "failed" && c.days_left > INFO.warn_days) return "";
  const [cls, key] = m.state === "running" ? ["pending", "admin.devices.renew_running"]
    : m.mode === "auto" ? ["ok", "admin.devices.renew_auto"]
    : m.mode === "failed" ? ["bad", "admin.devices.renew_failed"] : ["pending", "admin.devices.renew_manual"];
  if (short) return ` <span class="status ${cls}">${th(key)}</span>`;
  const how = m.mode === "failed" ? th("admin.devices.renew_failed_how", { step: m.step || "?", detail: m.detail || "?" })
    : m.mode === "auto" ? th("admin.devices.renew_auto_how") : th(x.phone?.fully_pin ? "admin.devices.renew_fully_how" : "admin.devices.renew_manual_how");
  return `<p><span class="status ${cls}">${th(key)}</span> ${how}</p>`;
}
const kindOf = (x) => t(x.phone?.fully_pin ? "admin.devices.kind_fully" : x.phone ? "admin.devices.kind_phone" : "admin.devices.kind_access");
// Fully-Version (T6): pro Handy aus GET /fully/apk – Version, Art (Play Store?), Vergleich mit der APK, Verteilung
const apkOf = (x) => (x.phone?.id && APK?.phones?.find((p) => p.id === x.phone.id)) || null;
function fullyVer(x) {
  const a = apkOf(x);
  if (!a?.version) return "";
  return `<span class="dev-ver">${th(a.variant === "play" ? "admin.devices.fully_ver_play" : "admin.devices.fully_ver", { version: a.version })}</span>`
    + (a.outdated ? ` <span class="status pending">${th("admin.devices.fully_old")}</span>` : "");
}
// Stand der Verteilung an ein Handy -> Text
function pushText(job) {
  if (!job) return t("admin.devices.apk_not_sent");
  if (job.state === "sent") return t("admin.devices.apk_sent", { time: I18n.fmtTime(job.at * 1000) });
  if (job.state === "failed") return t("admin.devices.apk_failed");
  if (job.state === "ok") return t("admin.devices.apk_done");
  if (["unreachable", "timeout"].includes(job.text)) return t("admin.devices.apk_open_unreachable");
  if (job.text === "busy") return t("admin.devices.apk_open_busy");
  return t("admin.devices.apk_open_retry", { reason: job.text === "not_installed" ? t("admin.devices.apk_not_installed") : job.text || "?" });
}
// Hinweis über der Liste: Handys mit einer anderen Version als die APK auf den Pi; «Erneut verteilen» nur Admin
function apkBox() {
  const out = (APK?.phones || []).filter((p) => p.outdated);
  if (!APK?.meta || !out.length) return "";
  return `<div class="warnbox" id="dev-apk"><p><b>${th("admin.devices.apk_warn", { version: apkVersion(APK.meta), count: out.length })}</b></p>
    <ul class="plainlist apk-list">${out.map((p) => `<li><b>${esc(p.name)}</b> – ${th("admin.devices.fully_ver", { version: p.version || t("admin.fully.apk_unknown") })}
      · ${esc(pushText(p.push))}</li>`).join("")}</ul>
    ${can("admin") ? `<button type="button" class="btn" id="dev-apk-push">${th("admin.devices.apk_repush")}</button>` : ""}</div>`;
}
// Geräteansicht, Karte Fully: Version im Vergleich zur APK auf den Pi
function apkNote(x) {
  const a = apkOf(x);
  if (!a?.version || !APK?.meta) return "";
  if (a.variant === "play") return `<p class="hint">${th("admin.devices.apk_play", { version: a.version })}</p>`;
  if (a.cmp === 1) return `<p class="hint">${th("admin.devices.apk_newer", { version: a.version, apk: apkVersion(APK.meta) })}</p>`;
  if (a.outdated) return `<div class="warnbox"><p>${th("admin.devices.apk_old", { version: a.version, apk: apkVersion(APK.meta) })} ${esc(pushText(a.push))}</p></div>`;
  return "";
}
// Fully meldet «ohne Lizenz» (nur mit Antwort von Fully; ohne Abfrage unbekannt)
const unlicensed = (x) => !!x.phone?.fully_pin && FULLY.results?.[x.phone.ip]?.data?.licensed === false;

let LOADS = 0;                                          // nur der neueste Ladevorgang zeichnet
RENDER.devices = async () => {
  if (adding()) return;                                 // Ablauf «Gerät hinzufügen» läuft: nicht überzeichnen
  const run = ++LOADS;
  const [d, st, fullyNow, apk, tap] = await Promise.all([guard(() => api("GET", "/devices")), guard(() => api("GET", "/settings")),
    api("GET", "/fully").catch(() => null), api("GET", "/fully/apk").catch(() => null),
    can("admin") ? api("GET", "/payrexx").catch(() => null) : null]);
  if (!d || !st || view !== "devices") return;         // inzwischen andere Seite/anderer Reiter (REV-048)
  if (run !== LOADS || adding()) return;               // ein neuerer Ladevorgang oder «Gerät hinzufügen» hat übernommen
  setFully(fullyNow || { password_set: false, port: 2323, results: {} });
  APK = apk;
  TAP = tap;
  INFO = d;
  DEVS = merge(d, st);
  const open = OPEN && DEVS.find((x) => x.key === OPEN);
  if (open) { drawDetail(open); return freshFully(open); }
  OPEN = null;
  drawList();
};

// ------------------------------------------------------------------ Liste
function drawList() {
  const list = DEVS.filter((x) => !x.blocked), blocked = DEVS.filter((x) => x.blocked);
  $("#main").innerHTML = `${pageHead(`<button type="button" class="btn pri" id="dev-add">${th("admin.devices.add")}</button>`)}
    ${INFO.enabled ? "" : `<div class="card"><p class="err">${th("admin.devices.certs_off")}</p></div>`}
    ${apkBox()}
    ${list.some((x) => x.dup) ? `<div class="warnbox" id="dev-dup"><p>${th("admin.devices.dup_hint", { count: list.filter((x) => x.dup).length })}</p></div>` : ""}
    <div class="card">${list.length ? `<div class="scroll"><table class="tbl devtbl"><thead><tr><th>${th("admin.devices.name_col")}</th>
        <th>${th("admin.devices.fixed_ip")}</th><th>${th("admin.devices.cert_col")}</th><th>${th("admin.devices.role")}</th>
        <th>${th("admin.common.in_use")}</th><th></th></tr></thead><tbody>
      ${list.map((x) => { const [cls, txt] = certState(x, INFO.warn_days); return `<tr data-key="${esc(x.key)}">
        <td class="d-name"><button type="button" class="linklike dev-link" data-open="${esc(x.key)}"><b>${esc(x.name)}</b></button>
          <span class="dev-sub">${esc(kindOf(x))}</span>${fullyVer(x)}${unlicensed(x) ? `<span class="status pending">${th("admin.devices.no_licence")}</span>` : ""}
          ${x.phone?.fully_pin && webWarn(x.phone.ip) ? `<span class="status bad web-warn">${th("admin.devices.web_warn")}</span>` : ""}
          ${x.dup ? `<span class="status bad dup">${th("admin.devices.dup")}</span>` : ""}${macTag(x)}${testTag(x)}</td>
        <td class="d-ip${x.phone ? "" : " none"}">${esc(x.phone?.ip || "–")}</td>
        <td class="d-cert"><span class="status ${cls}">${esc(txt)}</span>${renewNote(x, true)}</td>
        <td class="d-role">${x.cert && !x.cert.revoked_at ? roleSelect(x.role, `data-role="${esc(x.key)}" aria-label="${th("admin.devices.role")}"`) : "–"}</td>
        <td class="d-act"><label class="sw" title="${x.phone ? "" : th("admin.devices.active_needs_ip")}"><input type="checkbox" data-active="${esc(x.key)}"
          ${x.phone?.active !== false && x.phone ? "checked" : ""} ${x.phone ? "" : "disabled"}><i aria-hidden="true"></i>
          <span class="sw-txt">${th("admin.common.in_use")}</span></label></td>
        <td class="d-edit"><button type="button" class="btn sm" data-open="${esc(x.key)}">${th("admin.devices.edit")}</button></td></tr>`; }).join("")}
      </tbody></table></div>` : `<p class="hint">${th("admin.devices.none")}</p>`}
      <p class="hint">${th("admin.devices.list_hint")}</p><p class="hint">${th("admin.devices.role_hint")}</p></div>
    ${blocked.length ? `<details class="card set-card"><summary><h3>${th("admin.devices.blocked_title", { count: blocked.length })}</h3></summary>
      <p class="hint">${th("admin.devices.blocked_hint")}</p>
      <ul class="plainlist">${blocked.map((x) => `<li><b>${esc(x.name)}</b>${x.phone?.ip ? ` (${esc(x.phone.ip)})` : ""} – ${th("admin.devices.revoked_at", { time: fmtTime(x.cert.revoked_at) })}
        ${can("admin") ? `<button type="button" class="btn sm" data-unrevoke="${x.cert.id}" data-n="${esc(x.name)}">${th("admin.devices.unrevoke")}</button>
          <button type="button" class="btn sm danger" data-remove="${x.cert.id}" data-n="${esc(x.name)}">${th("admin.devices.remove")}</button>` : ""}</li>`).join("")}</ul>
      </details>` : ""}`;
  $("#dev-apk-push")?.addEventListener("click", async () => {
    const out = (APK?.phones || []).filter((p) => p.outdated);
    if (await pushApk(apkVersion(APK.meta), out.length, out.map((p) => p.id))) RENDER.devices();
  });
  $("#dev-add").addEventListener("click", () => {
    // vergeben sind nur die Namen gültiger Geräte – ein gesperrtes gibt seinen Namen frei (T1)
    const names = new Set(DEVS.filter((x) => x.valid).map((x) => nameKey(x.name))), ips = new Set(DEVS.map((x) => x.phone?.ip).filter(Boolean));
    let n = DEVS.length + 1;
    while (names.has(nameKey(t("admin.devices.next_name", { n })))) n++;
    startAdd({ names, ips, nextName: t("admin.devices.next_name", { n }), nextIp: nextIp(ips) });
  });
}
// nächste freie Adresse ab .101 (wie die Vorlage in der Anleitung)
function nextIp(ips) {
  for (let i = 101; i < 250; i++) if (!ips.has(`192.168.55.${i}`)) return `192.168.55.${i}`;
  return "";
}
async function onListChange(e) {
  const role = e.target.closest("[data-role]");
  if (role) {
    const x = DEVS.find((d) => d.key === role.dataset.role);
    if (!x) return;
    const ok = await guard(() => api("PUT", "/devices/role", { device: x.dev || x.phone?.id, role: role.value }),
      t({ storno: "admin.devices.role_saved_storno", lead: "admin.devices.role_saved_lead", status: "admin.devices.role_saved_status" }[role.value]
        || "admin.devices.role_saved_service", { name: x.name }));
    if (ok) { flash(role); if (x) x.role = role.value; } else if (x) role.value = x.role;
    return;
  }
  const sw = e.target.closest("[data-active]");
  if (sw) {
    const x = DEVS.find((d) => d.key === sw.dataset.active);
    if (!x?.phone) return;
    const ok = await guard(() => api("PUT", "/devices/phone", { id: x.phone.id, name: x.phone.name, ip: x.phone.ip, active: sw.checked }),
      t(sw.checked ? "admin.net.on" : "admin.net.off", { name: x.name }));
    if (ok) x.phone.active = sw.checked; else sw.checked = !sw.checked;
  }
}

// ------------------------------------------------------------------ Geräteansicht («Bearbeiten»)
function drawDetail(x) {
  const [cls, txt] = certState(x, INFO.warn_days), c = x.cert, older = x.certs.slice(1);
  const valid = c && !c.revoked_at && !(c.days_left != null && c.days_left < 0);
  $("#main").innerHTML = `<button type="button" class="linklike back" data-close>${th("admin.devices.back_list")}</button>
    <h2>${esc(x.name)}</h2>
    ${x.dup ? `<div class="warnbox"><p>${th("admin.devices.dup_how", { name: x.name })}</p></div>` : ""}
    ${x.test ? `<div class="warnbox"><p>${th(x.test.ending ? "admin.devices.test_ending" : "admin.devices.test_on")}</p>
      ${x.test.ending ? "" : `<button type="button" class="btn" id="dev-testexit">${th("admin.devices.test_exit")}</button>`}</div>` : ""}
    <form class="card" id="dev-data"><h3>${th("admin.devices.data_title")}</h3>
      <div class="form">
        <label>${th("admin.devices.name")}<input name="name" maxlength="30" value="${esc(x.cert ? x.name : x.phone?.name || x.name)}" ${x.cert && !valid ? "readonly" : ""} style="width:200px"></label>
        <label>${th("admin.devices.fixed_ip")}<input name="ip" maxlength="63" inputmode="decimal" value="${esc(x.phone?.ip || "")}" placeholder="192.168.55.101" style="width:170px"></label>
        <button class="btn pri">${th("admin.common.save")}</button></div>
      <p class="hint">${th(!x.cert ? "admin.devices.data_hint" : valid ? "admin.devices.name_rename_hint" : "admin.devices.name_in_cert")}</p>
      ${quick(x)}
      ${dhcpHint(x)}
    </form>
    <div class="card"><h3>${th("admin.devices.cert_title")}</h3>
      ${c ? `<p><span class="status ${cls}">${esc(txt)}</span> · ${th("admin.devices.created_at", { time: fmtTime(c.created_at) })}
        ${c.expires_at && c.days_left >= 0 ? ` · ${esc(inDays(c.days_left))}` : ""}</p>${renewNote(x)}` : `<p class="hint">${th("admin.devices.no_cert_hint")}</p>`}
      <div class="form">
        ${valid ? `<button type="button" class="btn danger" data-revoke="${c.id}">${th("admin.devices.revoke")}</button>` : ""}
        ${c?.revoked_at && can("admin") ? `<button type="button" class="btn" data-unrevoke="${c.id}" data-n="${esc(x.name)}">${th("admin.devices.unrevoke")}</button>` : ""}
        <button type="button" class="btn" id="dev-newcert">${th(!c ? "admin.devices.create" : x.phone?.fully_pin && valid ? "admin.devices.renew_now" : "admin.devices.new_cert")}</button></div>
      <div id="certout"></div>
      ${older.length ? `<details class="hist"><summary>${th("admin.devices.history", { count: older.length })}</summary>
        <table class="tbl"><tbody>${older.map((o) => `<tr><td>${fmtTime(o.created_at)}</td><td>${esc(o.expires_at ? fmtDate(o.expires_at) : "–")}</td>
          <td><span class="status ${o.revoked_at ? "bad" : ""}">${th(o.revoked_at ? "admin.devices.replaced_revoked" : "admin.devices.replaced")}</span></td></tr>`).join("")}
        </tbody></table></details>` : ""}
    </div>
    ${pinSection(x, valid)}
    ${x.phone ? fullySection(x) : ""}
    ${x.phone || x.cert ? `<div class="card" id="dev-time"><h3>${th("admin.time.dev_title")}</h3><p class="hint">${th("admin.common.loading")}</p></div>` : ""}
    ${can("admin") ? `<div class="card"><h3>${th("admin.devices.delete_title")}</h3><p class="hint">${th("admin.devices.delete_hint")}</p>
      <button type="button" class="btn danger" id="dev-delete">${th("admin.devices.delete")}</button></div>` : ""}`;
  bindDetail(x);
  devTime(x);
  $("#dev-testexit")?.addEventListener("click", async () => {
    if (await guard(() => api("POST", "/devices/test-exit", { key: x.test.key, name: x.name }), t("admin.test.exit_sent"))) RENDER.devices();
  });
}
// Uhrzeit des Handys (T2): Zeitserver per USB gesetzt? Weicht die Uhr vom Pi ab? (/api/time/sync meldet sie)
async function devTime(x) {
  const T = await loadTime();
  const box = $("#dev-time");
  if (!box || !T) return;
  const usb = Object.entries(T.usb || {}).map(([serial, v]) => ({ serial, ...v }))
    .filter((u) => x.phone?.ip && u.ip === x.phone.ip).sort((a, b) => (b.at || 0) - (a.at || 0))[0];
  const clock = (x.dev && T.phones?.[x.dev]) || T.phones?.[`dev:${x.phone?.ip}`];
  // T31: nur bei Android-Handhelds – mit Fully, oder die App meldet Android; Handheld ohne Meldung: ja
  const android = !!x.phone?.fully_pin || clock?.android === true || (!!x.phone && clock?.android == null);
  if (!android) { box.remove(); return; }
  const d = clock ? clock.offset : null;
  box.innerHTML = `<h3>${th("admin.time.dev_title")}</h3>
    <p>${usb?.ok ? `<span class="status ok">${th("admin.time.dev_ntp_ok")}</span> · ${th("admin.time.dev_ntp_at", { date: new Date(usb.at * 1000).toLocaleDateString(I18n.locale), node: (usb.node || "").toUpperCase() })}`
      : `<span class="status pending">${th("admin.time.dev_ntp_none")}</span>`}</p>
    ${d == null ? `<p class="hint">${th("admin.time.dev_clock_unknown")}</p>`
      : Math.abs(d) <= T.threshold ? `<p><span class="status ok">${th("admin.time.dev_clock_ok")}</span></p>`
        : `<p><span class="status bad">${th("admin.time.dev_clock_bad", { min: Math.round(Math.abs(d) / 60) })}</span> ${th(usb?.ok ? "admin.time.dev_clock_wait" : "admin.time.dev_clock_how")}</p>`}
    <div id="dev-usb"></div>
    <button type="button" class="btn" id="dev-usb-go">${th("admin.time.dev_usb")}</button>`;
  $("#dev-usb-go").addEventListener("click", async (e) => {
    e.currentTarget.hidden = true;
    const out = $("#dev-usb"), stopped = () => !document.body.contains(out);
    out.innerHTML = `<div class="roll-usb">${usbHow(false)}<p class="hint" id="dev-usb-st"></p></div>`;
    const r = await waitUsb(x.phone?.ip, (st, res) => {
      const el = $("#dev-usb-st");
      if (el) el.textContent = t(USB_ST[st], { model: res?.model || "" });
    }, stopped);
    if (r?.ok) { toast(t("admin.time.usb_st_ok", { model: r.model || x.name })); devTime(x); }
  });
}
const testTag = (x) => (x.test ? ` <span class="status pending">${th("admin.devices.test_tag")}</span>` : "");
// Rolle und «Im Einsatz» wie in der Liste – speichern sofort (gleiche Handler, onListChange)
function quick(x) {
  const role = x.cert && !x.cert.revoked_at, act = !!x.phone;
  if (!role && !act) return "";
  return `<div class="form dev-quick">
    ${role ? `<label>${th("admin.devices.role")}${roleSelect(x.role, `data-role="${esc(x.key)}"`)}</label>` : ""}
    ${act ? `<label class="sw"><input type="checkbox" data-active="${esc(x.key)}" ${x.phone.active !== false ? "checked" : ""}><i aria-hidden="true"></i>
      <span class="sw-txt">${th("admin.common.in_use")}</span></label>` : ""}</div>`;
}
// Feste IP und DHCP: reserviert ist sie erst mit der MAC des Handys (die lernt der Pi, sobald es sich meldet)
function dhcpHint(x) {
  if (!x.phone) return "";
  const now = x.dev ? INFO.ip_now?.[x.dev] : null, m = x.dev ? INFO.mac?.[x.dev] : null;
  return (x.phone.mac ? `<p class="hint">${th("admin.devices.mac_known", { mac: x.phone.mac })}</p>`
    : `<div class="warnbox"><p>${th(x.cert ? "admin.devices.mac_unknown" : "admin.devices.mac_unknown_nocert")}</p></div>`)
    + (m?.random ? `<p class="hint">${th("admin.devices.mac_random")}</p>` : "")
    + (macBox(x) || (now && now !== x.phone.ip ? `<p class="hint">${th("admin.devices.ip_pending", { ip: now })}</p>` : ""));
}
// MAC-Wechsel (T4): neue MAC mit demselben Zertifikat – wartet, Frage (Wartezeit aus) oder Alarm (zwei Geräte)
const macState = (x) => (x.dev && x.phone ? INFO.mac?.[x.dev]?.state : null) || null;
function macTag(x) {
  const st = macState(x);
  if (st === "alarm") return ` <span class="status bad">${th("admin.devices.mac_tag_alarm")}</span>`;
  return st === "wait" || st === "ask" ? ` <span class="status pending">${th("admin.devices.mac_tag_new")}</span>` : "";
}
function macBox(x) {
  const m = INFO.mac?.[x.dev], st = macState(x);
  if (!st) return "";
  if (st === "ignore") return `<p class="hint">${th("admin.devices.mac_ignored", { mac: m.new })}</p>`;
  const ago = (sec) => (sec == null ? t("admin.devices.mac_not_seen") : t("admin.devices.seen_ago", { time: I18n.fmtTime(Date.now() - sec * 1000) }));
  const btns = (take) => `<div class="form mac-acts">
    <button type="button" class="btn ${take ? "pri" : ""}" data-mac="take">${th("admin.devices.mac_take")}</button>
    <button type="button" class="btn" data-mac="keep">${th("admin.devices.mac_keep")}</button>
    ${st === "alarm" && x.cert && certValid(x.cert) ? `<button type="button" class="btn danger" data-revoke="${x.cert.id}">${th("admin.devices.revoke")}</button>` : ""}</div>`;
  if (st === "alarm") {
    return `<div class="warnbox alarm" id="mac-box"><p><b>${th("admin.devices.mac_alarm_title")}</b></p>
      <p>${th(m.reason === "taken" ? "admin.devices.mac_alarm_taken" : "admin.devices.mac_alarm_both", { other: m.other || "?" })}</p>
      <ul class="plainlist mac-list">
        <li><b>${th("admin.devices.mac_reserved")}:</b> <code>${esc(m.old)}</code> · ${esc(x.phone.ip)} · ${esc(ago(m.old_seen))}</li>
        <li><b>${th("admin.devices.mac_other")}:</b> <code>${esc(m.new)}</code> · ${esc(m.new_ip || "–")} · ${esc(ago(m.new_seen))}</li>
      </ul>
      <p class="hint">${th("admin.devices.mac_alarm_how")}</p>${btns(false)}</div>`;
  }
  const text = st === "ask" ? th("admin.devices.mac_ask", { mac: m.new, ip: m.new_ip || "?" })
    : th("admin.devices.mac_wait", { mac: m.new, ip: m.new_ip || "?", time: m.due ? I18n.fmtTime(m.due * 1000) : "?" });
  return `<div class="warnbox" id="mac-box"><p>${text}</p>${btns(true)}</div>`;
}
// Geräteansicht: Fully gleich neu abfragen (Kiosk-Modus, Akku, Lizenz sind sonst bis 30 Sekunden alt)
let FRESH = 0;
async function freshFully(x) {
  if (!x.phone?.fully_pin || !FULLY.password_set) return;
  const run = ++FRESH;
  const r = await api("POST", "/fully/check", { ip: x.phone.ip }).catch(() => null);
  if (!r || run !== FRESH || view !== "devices" || OPEN !== x.key) return;
  FULLY.results[x.phone.ip] = r;
  const y = scrollY;
  drawDetail(x);
  scrollTo(0, y);
}
// App «FestPOS Tap» (Karte/TWINT) auf diesem Handheld installieren oder aktualisieren. T30: der Pi
// wartet auf Fully (getInstallApkState) – «installiert» heisst bestätigt, «geschickt» ohne Bestätigung.
const when = (sec) => I18n.fmtTime(new Date(sec * 1000), { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
function tapNote(x) {
  if (!TAP || (!TAP.apk && !TAP.enabled)) return "";
  const s = TAP.sent?.[x.phone.ip], v = TAP.apk?.version;
  let txt = !TAP.apk ? t("admin.devices.tap_no_apk") : s?.installing ? t("admin.devices.tap_installing", { version: s.installing })
    : !s?.version ? t("admin.devices.tap_never")
      : t(s.state === "installed" ? "admin.devices.tap_installed" : "admin.devices.tap_sent", { version: s.version, time: when(s.at) })
        + (s.version !== v ? " " + t("admin.devices.tap_new", { apk: v }) : "");
  if (!s?.installing && s?.failed_at && s.failed_at > (s.at || 0)) txt += " " + t("admin.devices.tap_failed", { time: when(s.failed_at), why: s.failed || "?" });
  const key = !s?.version ? "admin.devices.tap_install" : s.version === v ? "admin.devices.tap_again" : "admin.devices.tap_update";
  const ok = !!TAP.apk && TAP.apk_here && !s?.installing;
  return `<div class="fa-app" id="dev-tap"><span><b>${th("admin.devices.tap_title")}</b> – <span id="dev-tap-st">${esc(txt)}</span></span>
    ${TAP.apk ? `<button type="button" class="btn sm${s?.version && s.version !== v && !s.installing ? " pri" : ""}" id="dev-tap-go" ${ok ? "" : "disabled"}>${th(key, { version: v || "" })}</button>` : ""}</div>`;
}
// T30: Fully als Liste – Zustand in zwei Spalten, Apps, Installationssperren, Knöpfe in Gruppen
function fullyFacts(x) {
  const p = x.phone, r = FULLY.results?.[p.ip], d = r?.data;
  if (!d) return "";
  const yes = (v, okText, badText, good = true) => (v == null ? null
    : `<span class="status ${v === good ? "ok" : "pending"}">${esc(t(v ? okText : badText))}</span>`);
  const row = (k, v) => (v == null || v === "" ? "" : `<dt>${th(k)}</dt><dd>${v}</dd>`);
  const a = apkOf(x);
  const ver = d.fully_version ? esc(d.fully_version) + (a?.outdated ? ` <small>${th("admin.fully.f_older")}</small>` : a ? ` <small>${th("admin.fully.f_current")}</small>` : "") : null;
  const gb = (b) => (b / 1e9).toLocaleString(I18n.locale, { maximumFractionDigits: 1 });
  const web = webState(p.ip);
  return `<div class="fa-two"><dl class="fa-kv">
      ${row("admin.fully.f_kiosk", yes(d.kiosk, "admin.fully.f_on", "admin.fully.f_off"))}
      ${row("admin.fully.f_page", d.page ? esc(d.page) : null)}
      ${row("admin.fully.f_licence", yes(d.licensed, "admin.fully.d_licensed", "admin.fully.d_unlicensed"))}
      ${row("admin.fully.f_version", ver)}
      ${row("admin.fully.f_owner", yes(d.device_owner, "admin.fully.f_owner_yes", "admin.fully.f_owner_no"))}
      ${row("admin.fully.f_web", web ? `<span class="status ${web[0]}">${esc(web[1])}</span>` : null)}
    </dl><dl class="fa-kv">
      ${row("admin.fully.f_android", d.android || d.model ? esc([d.android, d.model].filter(Boolean).join(" · ")) : null)}
      ${row("admin.fully.f_screen", d.screen_on == null ? null : esc(t(d.screen_on ? "admin.fully.f_on" : "admin.fully.f_off")))}
      ${row("admin.fully.f_battery", d.battery == null ? null : esc(`${d.battery} %${d.charging ? ` · ${t("admin.fully.f_charging")}` : ""}${d.temp != null ? ` · ${d.temp} °C` : ""}`))}
      ${row("admin.fully.f_wifi", d.ssid || d.wifi != null ? esc([d.ssid, d.wifi != null ? t("admin.fully.d_wifi", { level: d.wifi }) : ""].filter(Boolean).join(" · ")) : null)}
      ${row("admin.fully.f_storage", d.storage_free != null ? esc(t("admin.fully.d_free", { gb: gb(d.storage_free) })) : null)}
      ${row("admin.fully.f_cert", p.fully_until ? esc(t("admin.fully.f_cert_until", { date: fmtDate(p.fully_until) })) : null)}
    </dl></div>`;
}
function fullySection(x) {
  const p = x.phone, [cls, txt] = fullyState(p), r = FULLY.results?.[p.ip];
  const seen = x.seen != null ? t("admin.devices.seen_ago", { time: I18n.fmtTime(Date.now() - x.seen * 1000) }) : "";
  if (!p.fully_pin) {
    return `<div class="card"><h3>${th("admin.fully.title")}</h3>
      <p>${th("admin.devices.fully_none")}${seen ? ` · ${esc(seen)}` : ""}</p>
      <ol class="hint fully-steps"><li>${th("admin.fully.step1")}</li><li>${th("admin.fully.step4")}</li><li>${th("admin.fully.step5")}</li></ol>
      <div class="form"><button type="button" class="btn" data-fa="cert">${th("admin.fully.setup")}</button></div></div>`;
  }
  const btn = (fa, key, title = "") => `<button type="button" class="btn sm" data-fa="${fa}" ${title ? `title="${th(title)}"` : ""}>${th(key)}</button>`;
  const checked = r?.data ? t("admin.fully.d_time", { time: I18n.fmtTime(Date.now() - (r.data_seconds || 0) * 1000) }) : "";
  const locks = TAP?.sent?.[p.ip]?.locks;
  return `<div class="card" id="fullycard"><h3>${th("admin.fully.title")}</h3>
    <p><span class="status ${cls}">${esc(txt)}</span>${checked ? ` · ${esc(checked)}` : seen ? ` · ${esc(seen)}` : ""}</p>
    ${fullyFacts(x)}
    ${unlicensed(x) ? `<div class="warnbox"><p>${th("admin.devices.licence_how", { id: r.data.device_id || "?" })}</p></div>` : ""}
    ${webWarn(p.ip) ? `<div class="warnbox" id="fa-web"><p>${th("admin.fully.web_how")}</p>
      <div class="form">${btn("whitelist", "admin.fully.web_set")}</div></div>` : ""}
    ${fullyExpiry(p)}
    <div class="fa-sub"><h4>${th("admin.fully.apps")}</h4>
      ${tapNote(x)}
      <div class="fa-app"><span><b>${th("admin.fully.fully_app")}</b> – ${esc(r?.data?.fully_version || "–")}</span>${btn("update", "admin.fully.update", "admin.fully.update_title")}</div>
      ${apkNote(x)}
      <p class="hint fa-locks">${locks?.length ? th("admin.fully.locks_used", { list: locks.join(", ") }) : th("admin.fully.locks_hint")}</p></div>
    <div class="fa-grp">
      <div><b>${th("admin.fully.g_check")}</b>${btn("check", "admin.fully.check")}${btn("settings", "admin.fully.settings", "admin.fully.settings_title")}
        <a class="btn sm" href="${esc(fullyWebUrl(p.ip))}" target="_blank" rel="noopener noreferrer" title="${th("admin.fully.web_title")}">${th("admin.fully.web")}</a></div>
      <div><b>${th("admin.fully.g_kiosk")}</b><span class="status ${kioskCls(r)}">${th(kioskKey(r))}</span>
        ${btn("lockKiosk", "admin.fully.kiosk_lock", "admin.fully.kiosk_lock_title")}${btn("unlockKiosk", "admin.fully.kiosk_unlock", "admin.fully.kiosk_unlock_title")}</div>
      <div><b>${th("admin.fully.g_screen")}</b>${btn("screenOn", "admin.fully.screen_on")}${btn("screenOff", "admin.fully.screen_off")}
        <select class="sm" data-bright aria-label="${th("admin.fully.brightness")}"><option value="">${th("admin.fully.brightness")} …</option>
          ${[25, 50, 75, 100].map((p) => `<option value="${Math.round(p * 2.55)}">${p} %</option>`).join("")}</select></div>
      <div><b>${th("admin.fully.g_app")}</b>${btn("loadStartURL", "admin.fully.reload", "admin.fully.reload_title")}${btn("clearCache", "admin.fully.cache", "admin.fully.cache_title")}
        ${btn("toForeground", "admin.fully.front", "admin.fully.front_title")}</div>
      <div><b>${th("admin.fully.g_restart")}</b>${btn("restartApp", "admin.devices.restart_app")}${btn("rebootDevice", "admin.devices.reboot")}${btn("shutdownDevice", "admin.fully.shutdown")}</div>
      <div><b>${th("admin.fully.g_certs")}</b>${btn("ca", "admin.fully.ca", "admin.fully.ca_title")}${btn("devcert", "admin.fully.devcert", "admin.fully.devcert_title")}
        ${btn("renew", "admin.fully.renew", "admin.fully.renew_title")}</div>
    </div></div>`;
}
// Kiosk-Modus nach der letzten Antwort von Fully: gesperrt (ein) / entsperrt / ganz aus / unbekannt
const kioskKey = (r) => (r?.data?.kiosk === false ? "admin.fully.kiosk_off" : r?.data?.kiosk_locked === false ? "admin.fully.kiosk_unlocked"
  : r?.data?.kiosk_locked === true || r?.data?.kiosk === true ? "admin.fully.kiosk_locked" : "admin.fully.kiosk_unknown");
const kioskCls = (r) => (r?.data?.kiosk === false || r?.data?.kiosk_locked === false ? "pending" : r?.data ? "ok" : "");
// Zertifikat der Fernverwaltung (T3, F6): läuft es ab, erreicht der Pi Fully nicht mehr – auch nicht zum Erneuern
function fullyExpiry(p) {
  const left = p.fully_until ? Math.round((Date.parse(p.fully_until) - Date.now()) / 86400000) : null;
  if (left == null || Number.isNaN(left) || left > INFO.warn_days) return "";
  return `<div class="warnbox"><p>${th("admin.devices.fully_cert_expires", { date: fmtDate(p.fully_until) })}</p></div>`;
}
// PIN pro Gerät (T14): eigene PIN statt der gemeinsamen – nur Admin; angezeigt wird nur «eigene / gemeinsame / keine»
function pinSection(x, valid) {
  if (!x.dev || !valid) return "";
  const own = (INFO.pins || []).includes(x.dev);
  const state = own ? "admin.devices.pin_own" : INFO.pin_common ? "admin.devices.pin_common" : "admin.devices.pin_none";
  return `<div class="card" id="dev-pin"><h3>${th("admin.devices.pin_title")}</h3>
    <p><span class="status ${own ? "ok" : ""}" id="dev-pin-state">${th(state)}</span></p>
    ${can("admin") ? `<form class="form" id="dev-pin-form">
      <label>${th(own ? "admin.devices.pin_change" : "admin.devices.pin_set")}<input name="pin" type="password" inputmode="numeric"
        pattern="[0-9]{4,8}" minlength="4" maxlength="8" autocomplete="new-password" required style="width:140px"></label>
      <button class="btn">${th("admin.common.save")}</button>
      ${own ? `<button type="button" class="btn" id="dev-pin-del">${th("admin.devices.pin_remove")}</button>` : ""}</form>` : ""}
    <p class="hint">${th("admin.devices.pin_hint")}</p></div>`;
}
function bindPin(x) {
  $("#dev-pin-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pin = e.target.pin.value.trim();
    if (!/^\d{4,8}$/.test(pin)) return toast(t("admin.devices.pin_digits"), true);
    if (await guard(() => api("PUT", "/devices/pin", { device: x.dev, pin }), t("admin.devices.pin_saved", { name: x.name }))) RENDER.devices();
  });
  $("#dev-pin-del")?.addEventListener("click", async () => {
    if (!confirm(t("admin.devices.pin_remove_q", { name: x.name }))) return;
    if (await guard(() => api("PUT", "/devices/pin", { device: x.dev }), t("admin.devices.pin_removed"))) RENDER.devices();
  });
}
function bindDetail(x) {
  bindPin(x);
  const redraw = () => RENDER.devices();
  $("[data-close]").addEventListener("click", () => { OPEN = null; RENDER.devices(); scrollTo(0, 0); });
  $("#dev-data").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, name = f.name.value.trim(), ip = f.ip.value.trim();
    if (x.cert && name !== x.name) return renameCert(x, name, ip);
    if (!ip && !x.phone) return toast(t("admin.devices.ip_needed"), true);
    if (!ip && !confirm(t("admin.devices.ip_remove_q", { name: x.name }))) return;
    const ok = await guard(() => api("PUT", "/devices/phone", x.phone ? { id: x.phone.id, name, ip, active: x.phone.active !== false }
      : { name, ip, active: true, cert: x.dev }), t("admin.common.saved"));
    if (ok) { OPEN = x.key; redraw(); }
  });
  $("#dev-newcert").addEventListener("click", async (e) => {
    if (!(x.phone?.fully_pin && x.cert && certValid(x.cert))) return newCert(x, x.name);
    // Fully: wie die Automatik über die Fernverwaltung – nichts abtippen, das alte gilt, bis das Handy das neue benutzt
    e.target.disabled = true;
    const done = await fullyDeviceCert({ name: x.name, ip: x.phone.ip });
    e.target.disabled = false;
    if (done) RENDER.devices();
  });
  $("#dev-delete")?.addEventListener("click", () => remove(x));
  $$("[data-mac]").forEach((b) => b.addEventListener("click", async () => {
    const take = b.dataset.mac === "take", m = INFO.mac?.[x.dev];
    if (!m?.new || !confirm(t(take ? "admin.devices.mac_take_confirm" : "admin.devices.mac_keep_confirm", { mac: m.new, old: m.old, name: x.name }))) return;
    b.disabled = true;
    const ok = await guard(() => api("POST", "/devices/mac", { device: x.dev, action: b.dataset.mac, mac: m.new }),
      t(take ? "admin.devices.mac_taken_done" : "admin.devices.mac_kept_done", { name: x.name }));
    b.disabled = false;
    if (ok) RENDER.devices();
  }));
  $("#dev-tap-go")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    const r = await guard(() => api("POST", "/payrexx/apk/install", { ip: x.phone.ip }));
    e.target.disabled = false;
    if (!r) return;
    toast(t("admin.devices.tap_go_ok", { name: x.name }));
    if (TAP) TAP.sent = r.sent || TAP.sent;
    const redraw = () => { const y = scrollY; drawDetail(x); scrollTo(0, y); };
    redraw();
    // T30: der Pi wartet im Hintergrund auf Fully – den Stand nachholen, bis «wird installiert» vorbei ist
    for (let i = 0; i < 45; i++) {
      await new Promise((ok) => setTimeout(ok, 4000));
      if (view !== "devices" || OPEN !== x.key) return;
      const p = await api("GET", "/payrexx").catch(() => null);
      if (!p) continue;
      TAP = p;
      if (!p.sent?.[x.phone.ip]?.installing) { redraw(); return; }
    }
  });
  $$("[data-bright]").forEach((sel) => sel.addEventListener("change", async () => {
    if (sel.value === "") return;
    sel.disabled = true;
    await fullyCmd({ name: x.name, ip: x.phone.ip }, "brightness", +sel.value);
    sel.disabled = false; sel.value = "";
  }));
  $$("[data-fa]").forEach((b) => b.addEventListener("click", async () => {
    const fa = b.dataset.fa, d = { name: x.name, ip: x.phone.ip };
    if (fa === "renew" && !confirm(t("admin.fully.renew_confirm", { name: x.name }))) return;
    b.disabled = true;
    const run = { check: fullyCheck, settings: fullySettings, update: fullyUpdate, ca: fullyCa, devcert: fullyDeviceCert,
      cert: fullyCert, renew: fullyCert, whitelist: fullyWhitelist, restartApp: (v) => fullyRestart(v, "restartApp"), rebootDevice: (v) => fullyRestart(v, "rebootDevice") }[fa]
      || ((v) => fullyCmd(v, fa));                     // Kiosk, Bildschirm, Seite neu laden, Cache, Vordergrund, ausschalten
    const changed = await run(d);
    b.disabled = false;
    if (changed) redraw();
  }));
}
// Neues Zertifikat (gleiches Gerät, bisheriger oder neuer Name) per QR-Code; ein Handy ohne Zertifikat gehört danach dazu
async function newCert(x, name) {
  if (x.cert && name === x.name && !confirm(t("admin.devices.new_cert_confirm", { name }))) return null;
  const r = await guard(() => api("POST", "/devices/cert", x.dev ? { name, renew: true, device: x.dev }
    : { name, phone: x.phone?.id || null }));
  if (!r) return null;
  $("#certout").innerHTML = `<div class="qrrow"><div class="qrcol"><div class="qr-big">${r.svg}</div></div><div class="qrtxt">${pairText(r, name)}</div></div>`;
  countdown();
  return r;
}
// Umbenennen eines Geräts mit Zertifikat (T1): der Name steht im Zertifikat – neues Zertifikat mit dem neuen Namen als
// Erneuerung (gleiches Gerät, Rolle bleibt). Mit Fully kommt es von selbst aufs Handy, sonst per QR-Code.
async function renameCert(x, name, ip) {
  if (!confirm(t("admin.devices.rename_confirm", { old: x.name, name }))) return;
  if (x.phone && !await guard(() => api("PUT", "/devices/phone", { id: x.phone.id, name, ip: ip || x.phone.ip, active: x.phone.active !== false }))) return;
  if (x.phone?.fully_pin) {
    await fullyDeviceCert({ name, ip: ip || x.phone.ip }, false);
    return RENDER.devices();
  }
  if (await newCert(x, name)) toast(t("admin.devices.renamed_scan", { name }));
}
// Löschen (Admin): gültiges Zertifikat sperren (Rückfrage, Standard ja), abgelaufene entfernen, Eintrag mit
// Fully-Zugang aus der Statusanzeige nehmen. Ein gesperrtes Zertifikat bleibt (Sperrliste) – unter «Gesperrt».
async function remove(x) {
  const valid = x.certs.filter((c) => !c.revoked_at && !(c.days_left != null && c.days_left < 0));
  const answer = await askDelete(x, valid.length > 0);
  if (!answer) return;
  let kept = false;
  if (valid.length) {
    if (answer.revoke) {
      if (!await guard(() => api("POST", `/devices/${valid[0].id}/revoke`))) return;
    } else kept = true;
  }
  for (const c of x.certs.filter((c) => !c.revoked_at && c.days_left != null && c.days_left < 0)) {
    await api("DELETE", `/devices/${c.id}`).catch(() => null);
  }
  if (x.phone) {
    const st = await guard(() => api("GET", "/settings"));
    if (!st) return;
    let mon;
    try { mon = JSON.parse(st.monitor_devices || "[]"); } catch { mon = []; }
    const rest = mon.filter((d) => !(d.kind === "phone" && (x.phone.id ? d.id === x.phone.id : d.ip === x.phone.ip)))
      .map(({ id, name, kind, ip, port, active }) => ({ ...(id ? { id } : {}), name, kind, ip, port: port ?? null, active: active !== false }));
    if (!await guard(() => api("PUT", "/settings", { monitor_devices: rest }))) return;
  }
  toast(t(kept ? "admin.devices.deleted_cert_kept" : "admin.devices.deleted", { name: x.name }), kept);
  OPEN = null;
  RENDER.devices();
}

// Rückfrage zum Löschen (T3): ein Dialog, «Zertifikat sperren» vorgewählt -> {revoke} oder null (abgebrochen)
function askDelete(x, valid) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "dlg";
    dlg.innerHTML = `<form method="dialog" id="delform">
      <h3>${th("admin.devices.delete_confirm", { name: x.name })}</h3>
      ${valid ? `<label class="check"><input type="checkbox" name="revoke" checked> ${th("admin.devices.delete_revoke")}</label>
        <p class="hint">${th("admin.devices.delete_revoke_hint")}</p>` : ""}
      <div class="form dlg-foot">
        <button type="button" class="btn" id="delcancel">${th("admin.common.cancel")}</button>
        <button type="submit" class="btn danger">${th("admin.devices.delete")}</button>
      </div></form>`;
    document.body.appendChild(dlg);
    let result = null;
    dlg.addEventListener("close", () => { dlg.remove(); resolve(result); });
    $("#delcancel", dlg).addEventListener("click", () => dlg.close());
    $("#delform", dlg).addEventListener("submit", (e) => {
      e.preventDefault();
      result = { revoke: !!e.target.revoke?.checked };
      dlg.close();
    });
    dlg.showModal();
  });
}

// Ein Handler pro Ereignis für die Seite (sie zeichnet sich selbst neu: keine doppelten Listener)
document.addEventListener("click", async (e) => {
  if (view !== "devices") return;
  const o = e.target.closest("#main [data-open]");
  if (o) { OPEN = o.dataset.open; RENDER.devices(); scrollTo(0, 0); return; }
  const rm = e.target.closest("#main [data-remove]");
  if (rm) {
    if (!confirm(t("admin.devices.remove_confirm", { name: rm.dataset.n }))) return;
    if (await guard(() => api("POST", `/devices/${rm.dataset.remove}/remove`), t("admin.devices.removed", { name: rm.dataset.n }))) RENDER.devices();
    return;
  }
  const rv = e.target.closest("#main [data-revoke], #main [data-unrevoke]");
  if (rv) {
    const undo = !!rv.dataset.unrevoke, x = DEVS.find((d) => String(d.cert?.id) === (rv.dataset.revoke || rv.dataset.unrevoke));
    if (!undo && !confirm(t("admin.devices.revoke_confirm", { name: x?.name || "" }))) return;
    if (await guard(() => api("POST", `/devices/${rv.dataset.revoke || rv.dataset.unrevoke}/revoke${undo ? "?undo=true" : ""}`),
      t(undo ? "admin.devices.unrevoked" : "admin.devices.revoked_done"))) RENDER.devices();
  }
});
document.addEventListener("change", (e) => { if (view === "devices") onListChange(e); });   // Liste und Geräteansicht
