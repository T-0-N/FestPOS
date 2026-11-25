/* FestPOS – Verwaltung › Seite «Übersicht». */
import { $, api, can, chf, errLabel, esc, fmtTime, guard, piName } from "./core.js";
import { page, refreshBadge, RENDER, setProblems } from "./shell.js";
import { hardenOpen, piKpi, setSystem } from "./system.js";
import { kryptOff, kryptPending } from "./krypt.js";
import { osOld } from "./update.js";

// ------------------------------------------------------------------ Übersicht
// Hinweise des Servers (stats.warnings): Code -> Text; rot = etwas funktioniert nicht, sonst orange
const WARN_KEY = {
  admin_pw_placeholder: "admin.warn.admin_pw_placeholder",
  admin_pw_short: "admin.warn.admin_pw_short",
  admin_pw_short_locked: "admin.warn.admin_pw_short_locked",
  backup_none: "admin.warn.backup_none",
  backup_old: "admin.warn.backup_old",
  backup_failed: "admin.warn.backup_failed",
  secret_derived: "admin.warn.secret_derived",
  device_names_dup: "admin.warn.device_names_dup",
  season_due: "admin.warn.season_due",                 // Saison abschliessen (#12)
};
const WARN_BAD = new Set(["admin_pw_placeholder", "admin_pw_short_locked", "backup_failed"]);
// Automatische Sicherung auf den USB-Stick: «vor 7 Min. (Pi A)»
const backupKpi = (b) => b ? `<div class="card kpi"><b>${th("admin.backup.ago", { min: Math.max(0, Math.round((Date.now() / 1000 - b.at) / 60)) })}</b>
  <span>${th("admin.backup.last", { pi: piName(b.node) })}${can("admin") ? ` – <a href="#maint/backup" data-page="maint/backup">${th("admin.common.show")}</a>` : ""}</span></div>` : "";
// Sicherheitsereignisse (T-0-N/FestPOS#11, app/sicherheit.py): Art -> Text, Einzelheiten -> Zeile; rot = abgewiesen
export const SEC_KIND = {
  sec_ssh: "admin.sec.k_ssh", sec_sudo: "admin.sec.k_sudo", sec_sudo_failed: "admin.sec.k_sudo_failed",
  sec_reboot: "admin.sec.k_reboot", sec_boot_changed: "admin.sec.k_boot_changed", sec_update_foreign: "admin.sec.k_update_foreign",
  sec_lockout: "admin.sec.k_lockout", emergency_login: "admin.sec.k_emergency",
};
const SEC_BAD = new Set(["sec_update_foreign", "sec_boot_changed", "sec_sudo_failed"]);
function secText(a, d) {
  if (a === "sec_ssh") return t("admin.sec.d_ssh", { user: d.user || "?", ip: d.ip || "?" });
  if (a === "sec_sudo") return `${d.user || "?"}: ${d.cmd || ""}`;
  if (a === "sec_sudo_failed") return t("admin.sec.d_sudo_failed", { user: d.user || "?", count: d.count || 1 });
  if (a === "sec_boot_changed") return (d.files || []).join(", ") + (d.count > (d.files || []).length ? " …" : "");
  if (a === "sec_update_foreign") return d.file || "";
  if (a === "sec_lockout" || a === "emergency_login") return t("admin.sec.d_from", { ip: d.ip || "?" }) + (d.name ? ` (${d.name})` : "");
  return "";
}
function secCard(sec) {
  if (!sec?.count) return "";
  return `<div class="card sec-card" id="sec-card"><div class="head-row"><h3>${th("admin.sec.title", { count: sec.count })}</h3>
      <button type="button" class="btn sm" id="sec-seen" data-upto="${sec.last}">${th("admin.sec.seen")}</button></div>
    <div class="warnbox"><p>${th("admin.sec.warn")}</p></div>
    <div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.sec.time")}</th><th>${th("admin.sec.pi")}</th><th>${th("admin.sec.event")}</th></tr></thead><tbody>
      ${sec.events.map((e) => `<tr><td>${esc(fmtTime(e.details?.time || e.at))}</td><td>${e.details?.node ? esc(piName(e.details.node)) : "–"}</td>
        <td><span class="status ${SEC_BAD.has(e.action) ? "bad" : "pending"}">${th(SEC_KIND[e.action] || "admin.sec.k_other")}</span>
          ${esc(secText(e.action, e.details || {}))}</td></tr>`).join("")}</tbody></table></div>
    <p class="hint">${th("admin.sec.hint")}</p></div>`;
}
RENDER.overview = async () => {
  const [stats, printers, sys, system, sec] = await Promise.all([
    guard(() => api("GET", "/stats")), guard(() => api("GET", "/printers")),
    can("admin") ? api("GET", "/system/certs").catch(() => null) : null, api("GET", "/system").catch(() => null),
    can("audit") ? api("GET", "/security").catch(() => null) : null,
  ]);
  setSystem(system);
  if (!stats) return;
  setProblems(stats.problems);
  if (page !== "overview" || !$("#main")) return;      // inzwischen andere Seite (REV-048) oder abgemeldet
  // T28 Stufe 2: oben nur Zahlen, alles mit «!» (Hinweise, Warnungen) gesammelt in «Zu erledigen»
  const show = (pg) => ` – <a href="#${pg}" data-page="${pg}">${th("admin.common.show")}</a>`;
  const todo = [
    stats.certs_expiring ? ["warn", "", th("admin.overview.certs_expiring", { count: stats.certs_expiring }) + show("devices/list")] : null,
    stats.test_devices?.length ? ["warn", "", th("admin.overview.test_devices", { count: stats.test_devices.length }) + show("devices/list")] : null,
    can("admin") && stats.restore ? [stats.restore.bad ? "bad" : stats.restore.open ? "warn" : "ok", "restore-open",
      `${stats.restore.open ? th("admin.overview.restore_open", { count: stats.restore.open }) : `${th("admin.overview.restore_ok")} ✓`} – <a href="#maint/backup" data-page="maint/backup">${th("admin.overview.restore_link")}</a>`] : null,
    can("admin") && (stats.warnings || []).includes("backup_pw_unprinted") ? ["warn", "pw-unprinted",
      `${th("admin.warn.backup_pw_unprinted")} – <a href="#maint/backup" data-page="maint/backup">${th("admin.overview.pw_print")}</a>`] : null,
    ...(stats.mac_alarms || []).map((x) => ["bad", "mac-alarm", th("admin.overview.mac_alarm", { name: x.name }) + show("devices/list")]),
    stats.fully_outdated?.length ? ["warn", "fully-old", th("admin.overview.fully_old", { count: stats.fully_outdated.length }) + show("devices/list")] : null,
    ...(stats.warnings || []).filter((w) => WARN_KEY[w]).map((w) => [WARN_BAD.has(w) ? "bad" : "warn", "", th(WARN_KEY[w])]),
    sys?.certs?.some((c) => c.warn || c.days_left < 0) ? ["warn", "", th("admin.overview.server_cert") + show("devices/pi")] : null,
    can("admin") && (system?.nodes || []).some(osOld) ? ["warn", "os-old", th("admin.os.overview", {
      pis: system.nodes.filter(osOld).map((n) => piName(n.id)).join(", ") }) + show("maint/update")] : null,
    (system?.nodes || []).some(hardenOpen) ? ["warn", "harden-open", th("admin.harden.overview", {
      pis: system.nodes.filter(hardenOpen).map((n) => piName(n.id)).join(", "), count: system.nodes.filter(hardenOpen).length }) + show("devices/pi")] : null,
    can("admin") && (system?.nodes || []).some(kryptPending) ? ["bad", "krypt-open", th("admin.krypt.overview_open", {
      pis: system.nodes.filter(kryptPending).map((n) => piName(n.id)).join(", ") }) + show("devices/pi")] : null,
    can("admin") && (system?.nodes || []).some(kryptOff) ? ["warn", "krypt-off", th("admin.krypt.overview_off", {
      pis: system.nodes.filter(kryptOff).map((n) => piName(n.id)).join(", ") }) + show("devices/pi")] : null,
  ].filter(Boolean);
  $("#main").innerHTML = `<h2>${th("admin.nav.overview")}</h2>
    <div class="cols kpis">
      <div class="card kpi"><b>${stats.orders}</b><span>${th("admin.overview.orders")}</span></div>
      <div class="card kpi"><b>${chf(stats.total_cents)}</b><span>${th("admin.overview.value")}</span></div>
      <div class="card kpi"><b class="${stats.problems ? "bad" : ""}">${stats.problems}</b>
        <span>${stats.problems ? `${th("admin.overview.check_jobs")} – <a href="#jobs" data-page="jobs">${th("admin.common.show")}</a>` : th("admin.overview.print_problems")}</span></div>
      ${can("closing") && stats.open_orders ? `<div class="card kpi"><b>${stats.open_orders}</b>
        <span>${th("admin.overview.open_orders", { count: stats.open_orders })} – <a href="#closing" data-page="closing">${th("admin.overview.to_closing")}</a></span></div>` : ""}
      ${stats.test_orders ? `<div class="card kpi"><b>${stats.test_orders}</b><span>${th("admin.overview.test_orders")} – <a href="#pos/app" data-page="pos/app">${th("admin.overview.test_manage")}</a></span></div>` : ""}
      ${piKpi(system)}
      ${backupKpi(stats.backup)}
    </div>
    ${secCard(can("audit") ? sec : null)}
    ${todo.length ? `<div class="card todo"><h3>${th("admin.overview.todo")}</h3><ul class="chk">
      ${todo.map(([cls, extra, html]) => `<li class="${cls} ${extra}"><span class="todo-t">${html}</span></li>`).join("")}</ul></div>` : ""}
    <div class="card"><h3>${th("admin.nav.printers")}</h3>
      ${printers?.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.printers.printer")}</th><th>${th("admin.printers.address")}</th><th class="num">${th("admin.printers.waiting")}</th><th class="num">${th("admin.printers.problems")}</th><th>${th("admin.printers.state")}</th></tr></thead><tbody>
        ${printers.map((p) => {
          const st = !p.enabled ? ["", t("admin.printers.off")] : p.last_error ? [p.last_error_code === "rerouted" ? "pending" : "bad", errLabel(p.last_error_code)] : p.pending ? ["pending", t("admin.printers.printing")] : ["ok", t("admin.printers.ready")];
          return `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.host)}:${p.port}</td><td class="num">${p.pending}</td>
            <td class="num">${p.failed + p.uncertain}</td><td><span class="status ${st[0]}">${esc(st[1])}</span>
            ${p.last_error ? `<div class="err">${esc(p.last_error)}</div>` : ""}</td></tr>`;
        }).join("")}</tbody></table></div>` : `<p class="hint">${th("admin.overview.no_printer")} <a href="#devices/printers" data-page="devices/printers">${th("admin.overview.add_printer")}</a></p>`}
    </div>
`;
  $("#sec-seen")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    if (await guard(() => api("POST", `/security/seen?upto=${e.target.dataset.upto}`))) RENDER.overview();
    else e.target.disabled = false;
  });
  refreshBadge();
};
