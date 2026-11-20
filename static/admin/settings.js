/* FestPOS – Verwaltung › Einstellungen beim Ding (T28, T34): Kasse & App › Veranstalter (Name, Sprache, Logo) und
   › Bestell-App, Geräte & Netz › Einstellungen, Produkte › Lieferzeiten, Bildschirm am Pi (Geräte & Netz › Pi), Testmodus. */
import { $, api, can, esc, guard, LANGS, myLang, setLangs, toast, wordOf } from "./core.js";
import { bindTap, tapCard } from "./payrexx.js";
import { attachSaveBar } from "./savebar.js";
import { applyTabs, brandMark, loadBrand, logoImg, pageHead, RENDER, rerender, shell, show, showVersion, view } from "./shell.js";
import { apkCard, bindApk, bindFullyCard, bindProvision, bindTemplate, bindWlan, fullyCard, fullyPhones, provisionCard, setFully,
  templateCard, wlanCard } from "./fully.js";

// -- Reiter «Geräte» (T5): was für alle Geräte gilt – Einrichtungs-QR, WLAN, Fully, Vorlage, Gültigkeit (nur Admin)
function certCard(st) {
  const days = { 365: "admin.devices.v_1y", 730: "admin.devices.v_2y", 1825: "admin.devices.v_5y", 3650: "admin.devices.v_10y" };
  return `<details class="card set-card" data-in="setup"><summary><h3>${th("admin.devices.validity")}</h3>
      <span class="status">${th(days[st?.cert_days || "3650"] || "admin.devices.v_10y")}</span></summary><form id="certcfg">
    <div class="form">
      <label>${th("admin.devices.valid_for")}<select name="days">
        ${[[365, "admin.devices.v_1y"], [730, "admin.devices.v_2y"], [1825, "admin.devices.v_5y"], [3650, "admin.devices.v_10y"]].map(([v, k]) =>
          `<option value="${v}" ${String(v) === (st?.cert_days || "3650") ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
      <label>${th("admin.devices.warn_on_phone")}<select name="warn">
        ${[[60, "admin.devices.w_2m"], [180, "admin.devices.w_6m"], [365, "admin.devices.w_1y"], [730, "admin.devices.w_2y"]].map(([v, k]) =>
          `<option value="${v}" ${String(v) === (st?.cert_warn_days || "730") ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
      <label>${th("admin.devices.renew_auto_label")}<select name="renew">
        ${[[0, "admin.devices.r_off"], [60, "admin.devices.w_2m"], [180, "admin.devices.w_6m"], [365, "admin.devices.w_1y"], [730, "admin.devices.w_2y"]].map(([v, k]) =>
          `<option value="${v}" ${String(v) === (st?.cert_renew_days ?? "730") ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
      <button class="btn pri">${th("admin.common.save")}</button>
    </div>
    <p class="hint">${th("admin.devices.validity_hint")}</p>
    <p class="hint">${th("admin.devices.renew_auto_hint")}</p>
  </form></details>`;
}
// Feste IP bei neuer MAC (T4): nach so vielen Minuten ohne die alte MAC umschreiben; «aus» = nur von Hand
function macCard(st) {
  const cur = String(st?.mac_change_minutes ?? "10");
  const label = (v) => (v ? t("admin.devices.mac_minutes_n", { count: v }) : t("admin.devices.mac_minutes_off"));
  return `<details class="card set-card" data-in="setup"><summary><h3>${th("admin.devices.mac_card")}</h3>
      <span class="status">${esc(label(+cur))}</span></summary><form id="maccfg">
    <div class="form">
      <label>${th("admin.devices.mac_minutes")}<select name="minutes">
        ${[0, 5, 10, 30, 60].map((v) => `<option value="${v}" ${String(v) === cur ? "selected" : ""}>${esc(label(v))}</option>`).join("")}</select></label>
      <button class="btn pri">${th("admin.common.save")}</button>
    </div>
    <p class="hint">${th("admin.devices.mac_minutes_hint")}</p>
  </form></details>`;
}
function bindMacCard() {
  $("#maccfg").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (await guard(() => api("PUT", "/settings", { mac_change_minutes: +e.target.minutes.value }), t("admin.common.saved"))) rerender();
  });
}
function bindCertCard() {
  $("#certcfg").addEventListener("submit", async (e) => {
    e.preventDefault();
    const days = +e.target.days.value, warn = +e.target.warn.value, renew = +e.target.renew.value;
    if (warn >= days) return toast(t("admin.devices.warn_too_long"), true);
    if (renew >= days) return toast(t("err.settings.renew_too_long"), true);
    if (await guard(() => api("PUT", "/settings", { cert_days: days, cert_warn_days: warn, cert_renew_days: renew }), t("admin.common.saved"))) rerender();
  });
}

// ------------------------------------------------------------------ Einstellungen
// -- Bildschirm am Pi ---------------------------------------------------------
const SIZES = [["", "admin.display.size_auto"], ["320x480", "320 × 480"], ["480x320", "480 × 320"], ["800x480", "800 × 480"],
  ["720x1280", "720 × 1280"], ["1280x720", "1280 × 720"], ["1920x1080", "1920 × 1080"]];
// only: nur dieser Pi (T33: Unterseite eines Pi unter Geräte & Netz › Pi) – ohne Überschrift pro Pi
export async function renderDisplay(only = null) {
  const box = $("#disp-body");
  if (!box) return;
  const d = await guard(() => api("GET", "/display"));
  if (!d || !$("#disp-body")) return;
  box.className = "";
  box.innerHTML = d.nodes.filter((n) => !only || n.node === only).map((n) => {
    const c = n.cfg, st = n.status || {}, type = c.type || "";
    const lines = [];
    if (!st.seen) lines.push(th("admin.display.no_report"));
    else {
      lines.push(st.connectors?.length ? th("admin.display.found", { list: st.connectors.join(", ") }) : th("admin.display.none_found"));
      if (type && type !== "none") lines.push(th(st.kiosk ? "admin.display.kiosk_on" : "admin.display.kiosk_off"));
      if ((!st.kiosk_installed || st.error_code === "kiosk_missing") && type && type !== "none") lines.push(`<b class="err">${th("admin.display.kiosk_missing")}</b>`);
      if (!st.helper) lines.push(`<b class="err">${th("admin.display.not_prepared")}</b>`);
      if (st.error) lines.push(`<b class="err">${esc(st.error)}</b>`);
      if (st.reboot_needed) lines.push(`<b class="err">${th("admin.display.reboot_needed")}</b>`);
      if (st.rebooting) lines.push(th("admin.display.rebooting"));
    }
    const sizeOpts = SIZES.some(([v]) => v === c.size) ? SIZES : [...SIZES, [c.size, c.size]];
    const sizeLabel = (l) => (l.startsWith("admin.") ? th(l) : esc(l));
    return `<form class="disp" data-node="${esc(n.node)}">
      ${only ? "" : `<div class="disp-h">${th(n.here ? "admin.display.pi_here" : "admin.display.pi", { node: n.node })}</div>`}
      <div class="hint" style="margin:0 0 8px">${lines.join(" · ")}</div>
      <div class="form">
        <label>${th("admin.display.screen")}<select name="type" class="disp-type">
          ${type ? "" : `<option value="" selected>${th("admin.display.choose")}</option>`}
          ${Object.entries(d.types).map(([k, l]) => `<option value="${k}" ${k === type ? "selected" : ""}>${esc(l)}</option>`).join("")}
        </select></label>
        <label>${th("admin.display.rotate")}<select name="rotate">${[0, 90, 180, 270].map((r) => `<option value="${r}" ${r === c.rotate ? "selected" : ""}>${r}°</option>`).join("")}</select></label>
        <label>${th("admin.display.size")}<select name="size">${sizeOpts.map(([v, l]) => `<option value="${v}" ${v === (c.size || "") ? "selected" : ""}>${sizeLabel(l)}</option>`).join("")}</select></label>
        <label>${th("admin.display.cursor")}<select name="cursor">${[["auto", "admin.display.cursor_auto"], ["on", "admin.display.cursor_on"], ["off", "admin.display.cursor_off"]]
          .map(([v, k]) => `<option value="${v}" ${v === (c.cursor || "auto") ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
        <button class="btn pri">${th("admin.common.save")}</button>
      </div>
      <div class="form" style="margin-top:8px">
        <button type="button" class="btn" data-act="calibrate" ${!type || type === "none" ? "disabled" : ""}>${th(c.touchtest ? "admin.display.touch_running" : "admin.display.touch_setup")}</button>
        <button type="button" class="btn" data-act="${c.touchtest ? "stop" : "reset"}" ${!type || type === "none" ? "disabled" : ""}>${th(c.touchtest ? "admin.display.touch_stop" : "admin.display.touch_reset")}</button>
        <span class="hint" style="margin:0">${th(c.cal ? "admin.display.touch_done" : "admin.display.touch_none")}</span>
        ${!only || st.reboot_needed ? `<button type="button" class="btn right ${st.reboot_needed ? "pri" : ""}" data-act="reboot">${th("admin.display.reboot")}</button>` : ""}
      </div></form>`;
  }).join("") + `<p class="hint">${th("admin.display.hint")}</p>`;
  box.querySelectorAll("form.disp").forEach((f) => {
    const node = f.dataset.node;
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!f.elements.type.value) return toast(t("admin.display.choose_first"), true);
      if (await guard(() => api("PUT", `/display/${encodeURIComponent(node)}`,
        { type: f.elements.type.value, rotate: +f.elements.rotate.value, size: f.elements.size.value, cursor: f.elements.cursor.value }),
        t("admin.display.saved"))) setTimeout(() => renderDisplay(only), 400);
    });
    f.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      const act = b.dataset.act;
      if (act === "reboot") {
        if (!confirm(t("admin.display.reboot_confirm", { node }))) return;
        if (await guard(() => api("POST", `/display/${encodeURIComponent(node)}/reboot`), t("admin.display.rebooting_soon"))) setTimeout(() => renderDisplay(only), 400);
        return;
      }
      const msg = { calibrate: "admin.display.calibrate_msg", stop: "admin.display.stop_msg", reset: "admin.display.reset_msg" }[act];
      if (await guard(() => api("POST", `/display/${encodeURIComponent(node)}/touch`, { action: act }), t(msg))) setTimeout(() => renderDisplay(only), 400);
    }));
  });
}

// Karten, die auch die Festleitung sieht (Testmodus, Uhrzeit)
function testCard(s, stats) {
  return `<div class="card" id="testform"><h3>${th("admin.test.title")}</h3>
      <p class="hint">${th("admin.test.hint")}</p>
      <label class="check"><input type="checkbox" data-k="test_enabled" data-type="bool" data-label="admin.test.enable" ${s.test_enabled === "1" ? "checked" : ""}> ${th("admin.test.enable")}</label>
      <label class="check"><input type="checkbox" data-k="test_print" data-type="bool" data-label="admin.test.print" ${s.test_print === "1" ? "checked" : ""}> ${th("admin.test.print")}</label>
      <label class="check"><input type="checkbox" data-k="test_pin_required" data-type="bool" data-label="admin.test.pin_required" ${s.test_pin_required !== "0" ? "checked" : ""}> ${th("admin.test.pin_required")}</label>
      <div class="form" style="margin-top:8px">
        <label>${th(s.test_pin_set ? "admin.test.pin_new" : "admin.test.pin")}<input data-k="test_pin" data-type="pin" data-label="admin.test.pin" type="password" inputmode="numeric" autocomplete="new-password" style="width:160px"></label>
      </div>
      ${stats?.test_devices?.length ? `<p class="hint"><b>${th("admin.test.active")}</b> ${esc(stats.test_devices.map((d) =>
        d.app_name && d.app_name !== d.name ? `${d.name} (${d.app_name})` : d.name).join(", "))}
        <button type="button" class="btn sm" id="testexitall">${th("admin.test.exit_all")}</button></p>` : ""}
      <p class="hint">${th("admin.test.orders", { count: stats?.test_orders || 0 })}
        ${stats?.test_orders ? `<button type="button" class="btn sm" id="testdel">${th("admin.test.delete")}</button>` : ""}</p>
    </div>`;
}
function bindTest(stats) {  $("#testexitall")?.addEventListener("click", async () => {
    await guard(() => api("POST", "/devices/test-exit", { all: true }), t("admin.test.exit_sent"));
  });
  $("#testdel")?.addEventListener("click", async () => {
    if (!confirm(t("admin.test.delete_confirm", { count: stats.test_orders }))) return;
    const r = await guard(() => api("POST", "/test-orders/delete?confirm=JA"));
    if (r) { toast(t("admin.test.deleted", { count: r.deleted_orders })); rerender(); }
  });
}
function logoCard(s) {
  const url = s.logo_image ? `/media/${s.logo_image}` : null, plate = s.logo_plate !== "none";
  return `<div class="card" id="logocard"><h3>${th("admin.logo.title")}</h3>
    <div class="logo-row">
      <div class="logo-prev">${url ? logoImg(url, plate) : `<i></i><span>FestPOS</span>`}</div>
      <div class="form">
        <button type="button" class="btn" id="logopick">${th(url ? "admin.logo.replace" : "admin.logo.upload")}</button>
        <input type="file" id="logofile" accept="image/png,image/jpeg,image/webp" hidden>
        ${url ? `<button type="button" class="btn danger" id="logodel">${th("admin.logo.remove")}</button>` : ""}
      </div>
    </div>
    ${url ? `<label class="check"><input type="checkbox" id="logoplate" ${plate ? "checked" : ""}> ${th("admin.logo.plate")}</label>` : ""}
    <p class="hint">${th(url ? "admin.logo.hint" : "admin.logo.none")}</p></div>`;
}
function bindLogo() {
  const done = async (msg) => { toast(msg); await loadBrand(); $(".side .brand").innerHTML = `${brandMark()}<span>FestPOS</span>`; rerender(); };
  $("#logopick")?.addEventListener("click", () => $("#logofile").click());
  $("#logofile")?.addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    if (await guard(() => api("POST", "/logo", fd))) done(t("admin.logo.saved"));
  });
  $("#logodel")?.addEventListener("click", async () => {
    if (await guard(() => api("DELETE", "/logo"))) done(t("admin.logo.removed"));
  });
  $("#logoplate")?.addEventListener("change", async (e) => {
    if (await guard(() => api("PUT", "/settings", { logo_plate: e.target.checked ? "white" : "none" }))) done(t("admin.common.saved"));
  });
}

// ------------------------------------------------------------------ T28: Einstellungen beim Ding
// Der Menüpunkt «Einstellungen» gibt es nicht mehr. T34: Kasse & App › Veranstalter (hier), › Kasse (kassepage.js),
// › Bestell-App (renderPhoneSettings), › Bons & Quittung (bondesign.js); Geräte & Netz › Einstellungen
// (renderPhoneSettings); Produkte › Lieferzeiten (deliveryCard).
RENDER.event = async () => {
  const s = await guard(() => api("GET", "/settings"));
  if (!s || view !== "event") return;                  // inzwischen andere Seite (REV-048)
  setLangs(s.languages || LANGS);
  $("#main").innerHTML = `${pageHead()}
    <div class="card" id="set"><h3>${th("admin.settings.event")}</h3>
      <div class="form">
        <label>${th("admin.settings.event_name")}<input data-k="event_name" data-type="str" data-label="admin.settings.event_name" value="${esc(s.event_name)}" maxlength="48" style="width:300px"></label>
        <label>${th("admin.settings.currency")}<input data-k="currency" data-type="str" data-label="admin.settings.currency" value="${esc(s.currency)}" maxlength="6" style="width:90px"></label>
        ${LANGS.length > 1 ? `<label>${th("admin.settings.language")}<select data-k="language" data-type="str" data-label="admin.settings.language">
          ${LANGS.map((l) => `<option value="${esc(l.code)}" ${l.code === s.language ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select></label>` : ""}
      </div>
      ${LANGS.length > 1 ? `<p class="hint">${th("admin.settings.language_hint")}</p>` : ""}
      <div class="form" style="margin-top:8px">
        <label>${th("admin.settings.table_word")}<select data-k="table_word" data-type="str" data-label="admin.settings.table_word">
          ${[["Tisch", "admin.word.table"], ["Platz", "admin.word.seat"]].map(([v, k]) => `<option value="${v}" ${v === (s.table_word || "Tisch") ? "selected" : ""}>${th(k)}</option>`).join("")}
        </select></label>
      </div>
    </div>
    ${logoCard(s)}`;
  // T28 Stufe 2: ein Speichern-Balken für Name, Währung, Sprache und Tisch/Platz (Logo wirkt sofort)
  attachSaveBar($("#set"), { onSaved: async (body) => {
    if (body?.table_word) {                           // Tisch/Platz: Menü und Texte gleich nachführen
      I18n.setTableWord(body.table_word);
      if (!(body.language && !myLang())) { shell(); showVersion(); show("pos/event"); return; }
    }
    if (body?.language && !myLang()) {
      await I18n.load("");                            // Verwaltung folgt der Standardsprache, solange hier nichts gewählt ist
      shell(); showVersion(); show("pos/event");
      return;
    }
    rerender();
  } });
  bindLogo();
};

// Produkte › Lieferzeiten (bisher Einstellungen › Kasse)
export function deliveryCard(s) {
  return `<form class="card" id="whenform" data-in="delivery"><h3>${th("admin.settings.delivery_title")}</h3>
      <p class="hint">${th("admin.settings.delivery_hint")}</p>
      <div class="form"><label>${th("admin.settings.delivery_input")}<input name="times" value="${esc(s.delivery_times || t("admin.settings.delivery_default"))}" style="width:340px" maxlength="200"></label>
        <button class="btn pri">${th("admin.common.save")}</button></div>
    </form>`;
}
export function bindDelivery() {
  $("#whenform")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (await guard(() => api("PUT", "/settings", { delivery_times: e.target.times.value }), t("admin.settings.delivery_saved"))) rerender();
  });
}

// Kasse & App › Bestell-App (Bedienung, PIN, Testmodus) und Geräte & Netz › Einstellungen (APK, QR, WLAN, Fully, Vorlage,
// Gültigkeit, MAC) – T34: je ein Reiter, eine Ansicht.
RENDER.app = () => renderPhoneSettings("app");
RENDER.devsetup = () => renderPhoneSettings("setup");
async function renderPhoneSettings(tab) {
  const adm = can("admin"), setup = tab === "setup" && adm;
  const [s, stats, prov, apk, tpl, fullyNow, tap] = await Promise.all([guard(() => api("GET", "/settings")), api("GET", "/stats").catch(() => null),
    setup ? api("GET", "/fully/provision").catch(() => null) : null, setup ? api("GET", "/fully/apk").catch(() => null) : null,
    setup ? api("GET", "/fully/template").catch(() => null) : null, setup ? api("GET", "/fully").catch(() => null) : null,
    setup ? tapCard() : ""]);
  if (!s || view !== (tab === "setup" ? "devsetup" : "app")) return;   // inzwischen anderer Reiter: nicht überzeichnen
  const w = wordOf(s), head = pageHead();
  if (tab === "setup") {
    if (!adm) return;
    $("#main").innerHTML = `${head}<p class="hint">${th("admin.devices.settings_hint")}</p>
      ${apkCard(apk)}${tap}${provisionCard(prov, apk)}${wlanCard(prov)}${fullyCard(s)}${templateCard(tpl, fullyPhones(s))}${certCard(s)}${macCard(s)}`;
    if (fullyNow) setFully(fullyNow);
    applyTabs();
    bindApk();
    bindProvision();
    bindWlan();
    bindFullyCard();
    bindTemplate();
    bindCertCard();
    bindMacCard();
    bindTap();
    return;
  }
  if (!adm) {                                          // Festleitung: nur der Testmodus
    $("#main").innerHTML = `${head}<div id="appsettings">${testCard(s, stats)}</div>`;
    applyTabs();
    bindTest(stats);
    attachSaveBar($("#appsettings"), { onSaved: () => rerender() });
    return;
  }
  $("#main").innerHTML = `${head}
    <div id="appsettings"><div class="card" data-in="app"><h3>${th("admin.settings.phones")}</h3>
      <label class="check"><input type="checkbox" class="opt-flag" data-k="show_history" data-type="bool" data-label="admin.settings.show_history" ${s.show_history !== "0" ? "checked" : ""}>
        ${th("admin.settings.show_history")}</label>
      <label class="check"><input type="checkbox" class="opt-flag" data-k="show_recent_tables" data-type="bool" data-label="admin.settings.phones" ${s.show_recent_tables !== "0" ? "checked" : ""}>
        ${th("admin.settings.show_recent", { many: w.many })}</label>
      <label class="check"><input type="checkbox" class="opt-flag" data-k="swipe_categories" data-type="bool" data-label="admin.settings.swipe" ${s.swipe_categories !== "0" ? "checked" : ""}>
        ${th("admin.settings.swipe")}</label>
      <label class="check"><input type="checkbox" class="opt-flag" data-k="tutorial_auto" data-type="bool" data-label="admin.settings.tutorial_auto" ${s.tutorial_auto !== "0" ? "checked" : ""}>
        ${th("admin.settings.tutorial_auto")}</label>
      <label class="check"><input type="checkbox" class="opt-flag" data-k="staff_qr" data-type="bool" data-label="admin.settings.staff_qr" ${s.staff_qr === "1" ? "checked" : ""}>
        ${th("admin.settings.staff_qr")}</label>
      <p class="hint">${th("admin.settings.staff_qr_hint")}</p>
      <div class="form" style="margin-top:8px">
        <label>${th("admin.settings.images")}<select data-k="product_images" data-type="str" data-label="admin.settings.images">
          ${[["none", "admin.settings.images_none"], ["small", "admin.settings.images_small"], ["large", "admin.settings.images_large"]]
            .map(([v, k]) => `<option value="${v}" ${v === (s.product_images || "small") ? "selected" : ""}>${th(k)}</option>`).join("")}
        </select></label>
        <label>${th("admin.settings.outside")}<select data-k="options_outside" data-type="str" data-label="admin.settings.outside">
          ${[["warn", "admin.settings.outside_warn"], ["ignore", "admin.settings.outside_ignore"], ["close", "admin.settings.outside_close"]]
            .map(([v, k]) => `<option value="${v}" ${v === (s.options_outside || "warn") ? "selected" : ""}>${th(k)}</option>`).join("")}
        </select></label>
      </div>
      <p class="hint">${th("admin.settings.phones_hint")}</p>
    </div>
    <div class="card" id="pinform" data-in="app"><h3>${th("admin.pin.title")}</h3>
      <p class="hint">${th("admin.pin.hint")}</p>
      <label class="check"><input type="checkbox" data-k="pin_enabled" data-type="bool" data-label="admin.pin.enable" ${s.pin_enabled === "1" ? "checked" : ""}> ${th("admin.pin.enable")}</label>
      <div class="form" style="margin-top:8px">
        <label>${th(s.pin_set ? "admin.pin.new" : "admin.pin.pin")}<input data-k="pin" data-type="pin" data-label="admin.pin.pin" type="password" inputmode="numeric" autocomplete="new-password" style="width:160px"></label>
        <label>${th("admin.pin.lock_after")}<select data-k="pin_idle_minutes" data-type="int" data-label="admin.pin.lock_after">
          ${[[0, "admin.pin.idle_never"], [5, "admin.pin.idle_5"], [15, "admin.pin.idle_15"], [30, "admin.pin.idle_30"], [60, "admin.pin.idle_60"], [120, "admin.pin.idle_120"], [240, "admin.pin.idle_240"]]
            .map(([v, k]) => `<option value="${v}" ${String(v) === (s.pin_idle_minutes || "15") ? "selected" : ""}>${th(k)}</option>`).join("")}
        </select></label>
        <label class="check"><input type="checkbox" data-k="pin_on_screen_off" data-type="bool" data-label="admin.pin.screen_off" ${s.pin_on_screen_off !== "0" ? "checked" : ""}> ${th("admin.pin.screen_off")}</label>
      </div>
    </div>
    <div data-in="app">${testCard(s, stats)}</div></div>`;
  applyTabs();
  bindTest(stats);
  // T28 Stufe 2: ein Speichern-Balken für Bedienung, PIN-Sperre und Testmodus
  attachSaveBar($("#appsettings"), { onSaved: () => rerender() });
}
