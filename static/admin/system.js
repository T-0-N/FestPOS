/* FestPOS – Verwaltung › Geräte & Netz › Reiter «Pi»: Pi im Cluster, Herunterfahren / Neu starten, Zertifikate; eigenes Passwort.
   «Auf 3 Pi erweitern» / «Ersatzkarte vorbereiten»: expand.js (T8). */
import { $, api, can, esc, fmtDate, forgetLogin, guard, piName } from "./core.js";
import { inDays, pageHead, refreshTimer, RENDER, showLogin, view } from "./shell.js";
import { renderDisplay } from "./settings.js";
import { loadTime, piClock, setTimeChanged, timeActions } from "./clock.js";
import { startExpand } from "./expand.js";
import { startCardWrite } from "./cardwrite.js";

// ------------------------------------------------------------------ System: Pi und Handys herunterfahren / neu starten
let SYSTEM = null;
const fmtBytes = (b) => (b == null ? "–" : b >= 1e9 ? `${(b / 1e9).toLocaleString(I18n.locale, { maximumFractionDigits: 1 })} GB`
  : `${Math.round(b / 1e6)} MB`);
function fmtUptime(boot) {
  if (!boot) return "–";
  const m = Math.max(0, Math.round((Date.now() / 1000 - boot) / 60));
  return m < 60 ? t("admin.system.minutes", { count: m }) : m < 2880 ? t("admin.system.hours", { count: Math.floor(m / 60) })
    : t("admin.system.days", { count: Math.floor(m / 1440) });
}
// Dienste eines Pi (meldet jeder selbst, siehe system.local_info): alle laufen / welche nicht
function svcCell(n) {
  if (!n.reachable || !Object.keys(n.info?.svc || {}).length) return `<span class="hint">–</span>`;
  const down = svcDown(n);
  return down.length ? `<span class="status bad">${esc(t("admin.system.svc_down", { list: down.join(", "), count: down.length }))}</span>`
    : `<span class="status ok">${th("admin.system.svc_ok")}</span>`;
}
// T33: alles auf einer Seite – die Pi mit Uhrzeit als Liste, Pi antippen öffnet seine Unterseite
// (Zustand, Bildschirm, neu starten / herunterfahren nur diesen Pi). Zertifikate darunter; das Netzwerk auf der
// Statusanzeige steht seit T34 unter Netz & Internet.
let OPEN_PI = null;
document.addEventListener("click", (e) => {             // Menü «Geräte & Netz» oder Reiter «Pi»: immer zuerst die Liste
  if (e.target.closest('.side [data-page="devices"], .tabs [data-tab="pi"]')) OPEN_PI = null;
}, true);
const nodeName = (n) => `${esc(String(n.id).toUpperCase())}${n.here ? ` <span class="hint">${th("admin.system.here")}</span>` : ""}`;
function clockCell(id) {
  const c = piClock(id);
  if (!c) return "–";
  return `<span class="nowrap">${c.time} <span class="status ${c.bad ? "bad" : "ok"}">${c.diff}</span></span>
    <div class="hint${c.startBad ? " err" : ""}">${c.start}${c.since !== "–" ? ` · ${c.since}` : ""}</div>`;
}
function roleCell(n) {
  if (!n.reachable) return `<span class="status bad">${th("admin.system.unreachable")}</span>`;
  const down = svcDown(n);
  return `${th(n.leader ? "admin.overview.leader" : "admin.overview.standby")}${down.length
    ? `<div><span class="status bad">${esc(t("admin.system.svc_down", { list: down.join(", "), count: down.length }))}</span></div>` : ""}`;
}
function systemCard(system, cluster, disp) {
  const nodes = system?.nodes || [];
  const lead = can("system"), adm = can("admin");
  const many = nodes.length > 1;
  const screen = (id) => {
    const n = disp?.nodes?.find((x) => x.node === id), type = n?.cfg?.type;
    return !n ? "–" : !type || type === "none" ? t("admin.system.no_screen") : disp.types?.[type] || type;
  };
  const rows = nodes.map((n) => {
    const i = n.info || {};
    return `<tr class="pi-row" data-pi="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(t("admin.system.pi_title", { node: piName(n.id) }))}">
      <td><b>${nodeName(n)}</b></td><td>${roleCell(n)}</td>
      ${lead ? `<td>${clockCell(n.id)}</td>` : ""}
      <td class="num col-s">${i.temp != null ? `${esc(i.temp)} °C` : "–"}</td>
      ${adm ? `<td class="col-s">${esc(screen(n.id))}</td>` : ""}
      <td class="nowrap col-s">${esc(i.version || "–")}</td><td class="go-cell">›</td></tr>`;
  }).join("");
  const title = many ? t("admin.system.title_many", { count: nodes.length,
    state: t(nodes.filter((n) => n.reachable).length >= 2 ? "admin.system.redundant_short" : "admin.system.no_quorum_short") })
    : t("admin.system.title");
  return `<div class="card" id="systemcard"><h3>${esc(title)}</h3>
    ${nodes.length ? `<div class="scroll"><table class="tbl pi-tbl"><thead><tr><th>${th("admin.overview.node")}</th><th>${th("admin.overview.role")}</th>
      ${lead ? `<th>${th("admin.time.c_time")}</th>` : ""}<th class="num col-s">${th("admin.system.temp")}</th>
      ${adm ? `<th class="col-s">${th("admin.system.screen")}</th>` : ""}<th class="col-s">${th("admin.system.version")}</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="hint">${th("admin.system.tap_pi")}</p>` : ""}
    ${cluster?.mode === "rqlite" ? `<p class="hint">${th(nodes.length === 1 ? "admin.overview.single" : nodes.filter((n) => n.reachable).length >= 2 ? "admin.overview.redundant" : "admin.overview.no_quorum")}
      ${th("admin.overview.served_by", { node: String(cluster.this_node).toUpperCase() })}</p>` : `<p class="hint">${th("admin.overview.local_db")}</p>`}
    ${lead ? `<div class="sys-sub"><h4>${th("admin.time.title")}</h4>${timeActions()}</div>` : ""}
    ${lead ? `<div class="sys-sub"><h4>${th(many ? "admin.system.all_pis" : "admin.system.the_system")}</h4><div class="form">
      <button class="btn" data-power="reboot" data-node="all">${th(many ? "admin.system.reboot_all" : "admin.system.reboot_system")}</button>
      <button class="btn danger" data-power="poweroff" data-node="all">${th(many ? "admin.system.poweroff_all" : "admin.system.poweroff_system")}</button></div>
      <p class="hint">${th("admin.system.hint")}</p></div>` : ""}
    ${adm ? `<div class="sys-sub"><h4>${th("admin.system.expand_title")}</h4><div class="form">
      <button type="button" class="btn" id="x-open" data-single="${many ? "" : "1"}">${th(many ? "admin.expand.spare_open" : "admin.expand.open")}</button>
      <button type="button" class="btn" id="c-open">${th("admin.cardw.title")}</button></div>
      <p class="hint">${th("admin.cardw.open_hint")}</p></div>` : ""}
  </div>`;
}
// Unterseite eines Pi: Zustand, Bildschirm (nur Admin), neu starten / herunterfahren (ab Festleitung)
function piPage(n, system) {
  const i = n.info || {}, lead = can("system"), adm = can("admin"), c = piClock(n.id);
  const many = (system?.nodes || []).length > 1;
  const of = (free, total) => (free == null ? "–" : total ? t("admin.system.free_of", { free: fmtBytes(free), total: fmtBytes(total) }) : fmtBytes(free));
  const bak = i.backup?.last_ok ? t("admin.system.backup_at", { time: I18n.fmtTime(i.backup.last_ok * 1000, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }) }) : null;
  const row = (k, v) => (v == null || v === "" ? "" : `<dt>${th(k)}</dt><dd>${v}</dd>`);
  return `<button type="button" class="linklike back" data-pi-back>${th("admin.system.back")}</button>
    <h2>${th("admin.system.pi_title", { node: piName(n.id) })}${n.here ? ` <span class="hint">${th("admin.system.here")}</span>` : ""}</h2>
    <div class="card"><h3>${th("admin.system.state")}</h3><div class="pi-dl2">
      <dl class="pi-kv">${row("admin.overview.reachable", `<span class="status ${n.reachable ? "ok" : "bad"}">${th(n.reachable ? "admin.common.yes" : "admin.common.no")}</span>
          · ${th(n.leader ? "admin.overview.leader" : "admin.overview.standby")}`)}
        ${row("admin.system.model", esc(i.model || "–"))}${row("admin.system.os", esc([i.os, i.kernel && `Kernel ${i.kernel}`].filter(Boolean).join(" · ") || "–"))}
        ${row("admin.system.version", esc(i.version || "–"))}${row("admin.system.uptime", esc(fmtUptime(i.boot)))}</dl>
      <dl class="pi-kv">${row("admin.system.temp", i.temp != null ? `${esc(i.temp)} °C` : "–")}
        ${row("admin.system.disk", esc(of(i.disk_free, i.disk_total)))}${row("admin.system.ram", esc(of(i.ram_free, i.ram_total)))}
        ${row("admin.system.services", svcCell(n))}
        ${c ? row("admin.time.c_time", `${c.time} <span class="status ${c.bad ? "bad" : "ok"}">${c.diff}</span> · ${c.start} · ${c.zone}`) : ""}
        ${c ? row("admin.time.c_battery", c.battery) : ""}${row("admin.system.backup", bak ? esc(bak) : null)}</dl></div></div>
    ${adm ? `<div class="card" id="dispcard"><h3>${th("admin.display.title_one")}</h3>
      <div id="disp-body" class="hint">${th("admin.common.loading")}</div></div>` : ""}
    ${lead ? `<div class="card"><h3>${th("admin.system.power_title")}</h3><div class="form">
      <button class="btn" data-power="reboot" data-node="${esc(many ? n.id : "all")}">${th("admin.system.reboot_one", { node: piName(n.id) })}</button>
      <button class="btn danger" data-power="poweroff" data-node="${esc(many ? n.id : "all")}">${th("admin.system.poweroff_one", { node: piName(n.id) })}</button></div>
      <p class="hint">${th(many ? "admin.system.one_hint" : "admin.system.hint")}</p></div>` : ""}`;
}
export function openPower(action, node) {
  const nodes = SYSTEM?.nodes || [];
  const all = node === "all" || nodes.length <= 1;
  const phonesOk = all && SYSTEM?.phones_fully > 0 && SYSTEM?.fully_ready;
  const choices = action === "reboot"
    ? [["restart_app", "admin.system.ph_restart_app"], ["reboot", "admin.system.ph_reboot"], ["none", "admin.system.ph_none"]]
    : [["shutdown", "admin.system.ph_shutdown"], ["screen_off", "admin.system.ph_screen_off"], ["none", "admin.system.ph_none"]];
  const label = node === "all" ? "" : String(node).toUpperCase();
  const title = t(action === "reboot" ? (node === "all" ? "admin.system.reboot_all_q" : "admin.system.reboot_one_q")
    : (node === "all" ? "admin.system.poweroff_all_q" : "admin.system.poweroff_one_q"), { node: label });
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog" id="powerform">
    <h3>${esc(title)}</h3>
    <p class="hint">${th(action === "reboot" ? "admin.system.reboot_text" : "admin.system.poweroff_text")}</p>
    ${phonesOk ? `<fieldset class="plain"><legend>${th("admin.system.phones", { count: SYSTEM.phones_fully })}</legend>
      ${choices.map(([v, k], i) => `<label class="check"><input type="radio" name="phones" value="${v}" ${i === 0 ? "checked" : ""}> ${th(k)}</label>`).join("")}
      <p class="hint">${th("admin.system.phones_hint")}${action === "poweroff" ? " " + th("admin.system.phones_hint_off") : ""}</p></fieldset>`
      : all ? `<p class="hint">${th("admin.system.phones_none")}</p>` : ""}
    <div id="powerres"></div>
    <div class="form dlg-foot">
      <button type="button" class="btn" id="powercancel">${th("admin.common.cancel")}</button>
      <button type="submit" class="btn danger" id="powergo">${th(action === "reboot" ? "admin.system.reboot" : "admin.system.poweroff")}</button>
    </div></form>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $("#powercancel", dlg).addEventListener("click", () => dlg.close());
  $("#powerform", dlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, go = $("#powergo", dlg);
    go.disabled = true;
    const r = await guard(() => api("POST", "/system/power", { action, node, phones: f.phones?.value || "none" }));
    if (!r) { go.disabled = false; return; }
    const when = I18n.fmtTime(r.at * 1000, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const fmt = (s) => I18n.fmtTime(s * 1000, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    // T27: zuerst die Meldung mit Countdown auf allen Handys, der Statusanzeige und hier oben (dort «Abbrechen»)
    $("#powerres", dlg).innerHTML = `<p class="okmsg">${th(action === "reboot" ? "admin.system.reboot_at" : "admin.system.poweroff_at", { time: when })}</p>
      <p class="hint">${th("admin.system.notice_sent")}${r.phones_at ? " " + th("admin.system.phones_at", { time: fmt(r.phones_at) }) : ""}</p>`;
    f.querySelector("fieldset")?.remove();
    go.remove();
    $("#powercancel", dlg).textContent = t("admin.common.close");
  });
  dlg.showModal();
}
export function openOwnPassword() {
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog" id="ownpw">
    <h3>${th("admin.users.own_password")}</h3>
    <div class="form col">
      <label>${th("admin.users.old_password")}<input type="password" name="old" autocomplete="current-password" required></label>
      <label>${th("admin.users.new_password")}<input type="password" name="new" autocomplete="new-password" minlength="8" maxlength="128" required></label>
    </div>
    <p class="hint">${th("admin.users.pw_rule")}</p>
    <div class="form dlg-foot">
      <button type="button" class="btn" id="ownpwcancel">${th("admin.common.cancel")}</button>
      <button type="submit" class="btn pri">${th("admin.common.save")}</button>
    </div></form>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $("#ownpwcancel", dlg).addEventListener("click", () => dlg.close());
  $("#ownpw", dlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (!await guard(() => api("POST", "/me/password", { old: f.old.value, new: f.new.value }))) return;
    dlg.close();
    forgetLogin(); clearInterval(refreshTimer);
    showLogin(t("admin.users.pw_changed"));            // neues Passwort: neu anmelden (alte Sitzungen enden)
  });
  dlg.showModal();
}

// ------------------------------------------------------------------ Pi
function svcDown(n) { return Object.entries(n.info?.svc || {}).filter(([, ok]) => !ok).map(([k]) => t(SVC_NAMES[k] || k)); }
const SVC_NAMES = { rqlited: "status.svc.rqlited", "festpos-redirect": "status.svc.redirect", chrony: "status.svc.chrony",
  nftables: "status.svc.nftables", keepalived: "status.svc.keepalived" };
export function piKpi(system) {
  const nodes = system?.nodes || [];
  if (!nodes.length) return "";
  const up = nodes.filter((n) => n.reachable).length;
  const issues = nodes.flatMap((n) => (!n.reachable ? [t("admin.overview.pi_down", { pi: piName(n.id) })]
    : svcDown(n).length ? [t("admin.overview.pi_svc", { pi: piName(n.id), list: svcDown(n).join(", "), count: svcDown(n).length })] : []));
  return `<div class="card kpi"><b class="${issues.length ? "bad" : ""}">${up}/${nodes.length}</b>
    <span>${th("admin.overview.pis_up")}${issues.length ? `: <span class="err">${esc(issues.join(", "))}</span>` : ""} –
      <a href="#devices/pi" data-page="devices/pi">${th("admin.nav.devices")}</a></span></div>`;
}
function certsCard(sys) {
  if (!sys?.certs?.length) return "";
  // T33: zugeklappt, Zustand im Titel – aufgeklappt, sobald eines bald abläuft
  const worst = sys.certs.some((c) => c.days_left < 0) ? ["bad", t("admin.certs.expired")]
    : sys.certs.some((c) => c.warn) ? ["pending", t("admin.certs.soon")] : ["ok", t("admin.certs.all_ok")];
  return `<details class="card set-card" id="certscard" ${worst[0] === "ok" ? "" : "open"}><summary><h3>${th("admin.certs.title")}</h3>
      <span class="status ${worst[0]}">${esc(worst[1])}</span></summary>
      <div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.certs.cert")}</th><th>${th("admin.certs.valid_until")}</th><th>${th("admin.printers.state")}</th></tr></thead><tbody>
        ${sys.certs.map((c) => `<tr><td>${esc(c.label)}</td><td>${esc(fmtDate(c.expires_at))}</td>
          <td><span class="status ${c.days_left < 0 ? "bad" : c.warn ? "pending" : "ok"}">${esc(c.days_left < 0 ? t("admin.certs.expired")
            : c.warn ? t("admin.certs.expires_in", { when: inDays(c.days_left) }) : t("admin.certs.ok"))}</span></td></tr>`).join("")}
      </tbody></table></div>
      ${sys.certs.some((c) => c.warn) ? `<p class="hint">${th("admin.certs.renew_hint")}</p>` : ""}
      ${sys.certs.filter((c) => c.fingerprint).map((c) => `<p class="hint">${th("admin.certs.fingerprint")} <code class="fp">${esc(c.fingerprint)}</code>.
        ${th("admin.certs.install")} <code>http://${esc(location.hostname)}/ca.crt</code></p>
        ${c.constrained ? "" : `<p class="err">${th("admin.certs.unconstrained")}</p>`}`).join("")}
    </details>`;
}
RENDER.system = async () => {
  const adm = can("admin");
  const [system, cluster, , certs, disp] = await Promise.all([api("GET", "/system").catch(() => null),
    guard(() => api("GET", "/cluster")), loadTime(),
    adm ? api("GET", "/system/certs").catch(() => null) : null, adm ? api("GET", "/display").catch(() => null) : null]);
  SYSTEM = system;
  if (view !== "system") return;                       // inzwischen andere Seite (REV-048)
  setTimeChanged(() => { if (view === "system") RENDER.system(); });
  const one = OPEN_PI && (system?.nodes || []).find((n) => n.id === OPEN_PI);
  if (one) {                                           // Unterseite eines Pi
    $("#main").innerHTML = piPage(one, system);
    $("[data-pi-back]").addEventListener("click", () => { OPEN_PI = null; RENDER.system(); scrollTo(0, 0); });
    if (adm) renderDisplay(one.id);
    return;
  }
  OPEN_PI = null;
  $("#main").innerHTML = `${pageHead()}
    ${systemCard(system, cluster, disp)}
    ${certsCard(certs)}`;
  $("#x-open")?.addEventListener("click", (e) => startExpand(!!e.currentTarget.dataset.single, cluster?.this_node));
  $("#c-open")?.addEventListener("click", () => startCardWrite());
  const open = (tr) => { OPEN_PI = tr.dataset.pi; RENDER.system(); scrollTo(0, 0); };
  document.querySelectorAll("#systemcard .pi-row").forEach((tr) => {
    tr.addEventListener("click", (e) => { if (!e.target.closest("button, a")) open(tr); });
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(tr); } });
  });
};

// von anderen Modulen gesetzt (importierte Variablen sind dort nur lesbar)
export function setSystem(v) { SYSTEM = v; }
