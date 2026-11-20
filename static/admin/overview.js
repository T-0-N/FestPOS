/* FestPOS – Verwaltung › Seite «Übersicht». */
import { $, api, can, chf, errLabel, esc, guard, piName } from "./core.js";
import { page, refreshBadge, RENDER, setProblems } from "./shell.js";
import { piKpi, setSystem } from "./system.js";

// ------------------------------------------------------------------ Übersicht
// Hinweise des Servers (stats.warnings): Code -> Text; rot = etwas funktioniert nicht, sonst orange
const WARN_KEY = {
  admin_pw_placeholder: "admin.warn.admin_pw_placeholder",
  admin_pw_short: "admin.warn.admin_pw_short",
  backup_none: "admin.warn.backup_none",
  backup_old: "admin.warn.backup_old",
  backup_failed: "admin.warn.backup_failed",
  secret_derived: "admin.warn.secret_derived",
  device_names_dup: "admin.warn.device_names_dup",
};
const WARN_BAD = new Set(["admin_pw_placeholder", "backup_failed"]);
// Automatische Sicherung auf den USB-Stick: «vor 7 Min. (Pi A)»
const backupKpi = (b) => b ? `<div class="card kpi"><b>${th("admin.backup.ago", { min: Math.max(0, Math.round((Date.now() / 1000 - b.at) / 60)) })}</b>
  <span>${th("admin.backup.last", { pi: piName(b.node) })}${can("admin") ? ` – <a href="#maint/backup" data-page="maint/backup">${th("admin.common.show")}</a>` : ""}</span></div>` : "";
RENDER.overview = async () => {
  const [stats, printers, sys, system] = await Promise.all([
    guard(() => api("GET", "/stats")), guard(() => api("GET", "/printers")),
    can("admin") ? api("GET", "/system/certs").catch(() => null) : null, api("GET", "/system").catch(() => null),
  ]);
  setSystem(system);
  if (!stats) return;
  setProblems(stats.problems);
  if (page !== "overview") return;                     // inzwischen andere Seite (REV-048)
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
  refreshBadge();
};
