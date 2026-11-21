/* FestPOS – Verwaltung › Seite «Wartung»: Update (signierte Pakete auf alle Pi), Update aus dem Internet, Sicherung
   (backup.js).
   Die Internet-Verbindung selbst: Seite «Internet» (internet.js). */
import { $, $$, api, esc, fmtDate, guard, piName, toast } from "./core.js";
import { applyTabs, page, RENDER, tabBar } from "./shell.js";
import { backupCards, backupInit } from "./backup.js";

// ------------------------------------------------------------------ Update: signierte Pakete auf alle Pi (update.py)
// Hochladen -> Prüfung (Signatur, Version) -> «Auf alle Pi verteilen». Danach arbeitet jeder Pi für sich: einer nach
// dem anderen, der druckende zuletzt. Diese Seite zeigt den Stand (alle 4 s) – das Formular bleibt dabei stehen.
let UPD = null, updLost = false;
const UPD_STATE = { ok: ["ok", "admin.update.st_ok"], running: ["pending", "admin.update.st_running"],
  next: ["pending", "admin.update.st_next"], queued: ["", "admin.update.st_queued"], failed: ["bad", "admin.update.st_failed"],
  wait: ["pending", "admin.update.st_wait"], same: ["ok", "admin.update.st_same"],
  skip_old: ["bad", "admin.update.st_skip_old"], skip_key: ["bad", "admin.update.st_skip_key"],
  skip_backup: ["", "admin.update.st_skip_backup"] };
const UPD_WARN = { jobs: "admin.update.w_jobs", orders: "admin.update.w_orders" };
const UPD_USB = { reading: "admin.update.usb_reading", handed: "admin.update.usb_handed", old: "admin.update.usb_old",
  none: "admin.update.usb_none", invalid: "admin.update.usb_invalid", unreadable: "admin.update.usb_unreadable",
  installing: "admin.update.usb_installing", installed: "admin.update.usb_installed", failed: "admin.update.usb_failed" };

const shortFp = (fp) => (fp && fp.length > 24 ? `${fp.slice(0, 15)}…${fp.slice(-6)}` : fp || "");
const fmtMB = (b) => `${((b || 0) / 1e6).toLocaleString(I18n.locale, { maximumFractionDigits: 1 })} MB`;
const atTime = (sec) => (sec ? I18n.fmtTime(sec * 1000) : "–");
const updActive = (u) => ["wait", "go", "blocked"].includes(u?.plan?.phase);

function updNodes(u) {
  const plan = u.plan && !u.plan.cancelled ? u.plan : null;
  return `<div class="scroll"><table class="tbl upd-nodes"><thead><tr><th>${th("admin.overview.node")}</th>
      <th>${th("admin.overview.reachable")}</th><th>${th("admin.overview.role")}</th><th>${th("admin.system.version")}</th>
      <th>${th("admin.update.backup")}</th>${plan ? `<th>${th("admin.update.progress")}</th>` : ""}</tr></thead><tbody>
    ${u.nodes.map((n) => {
      const [cls, key] = UPD_STATE[n.state] || ["", ""];
      const detail = [n.text, n.msg].filter(Boolean);
      return `<tr data-pi="${esc(n.id)}"><td><b>${esc(piName(n.id))}</b>${n.here ? ` <span class="hint">${th("admin.system.here")}</span>` : ""}</td>
        <td><span class="status ${n.reachable ? "ok" : "bad"}">${th(n.reachable ? "admin.common.yes" : "admin.common.no")}</span></td>
        <td class="nowrap">${th(n.leader ? "admin.overview.leader" : "admin.overview.standby")}</td>
        <td class="nowrap" translate="no">${esc(n.version || "–")}${n.helper ? "" : `<div class="hint">${th("admin.update.no_helper_short")}</div>`}</td>
        <td class="nowrap" translate="no">${esc(n.backup || "–")}</td>
        ${plan ? `<td>${key ? `<span class="status ${cls}">${th(key)}</span>` : ""}
          ${detail.length ? `<div class="${n.state === "failed" ? "err" : "hint"}" translate="no">${detail.map(esc).join("<br>")}</div>` : ""}
          ${n.log?.length && n.state === "failed" ? `<details class="up-setup"><summary>${th("admin.update.log")}</summary>
            <pre translate="no">${esc(n.log.join("\n"))}</pre></details>` : ""}</td>` : ""}</tr>`;
    }).join("")}</tbody></table></div>`;
}
function updPlan(u) {
  const p = u.plan;
  if (!p) return "";
  const v = p.kind === "rollback" ? p.to : p.version;
  const title = t(p.kind === "rollback" ? "admin.update.plan_back" : "admin.update.plan_install", { version: v });
  const who = piName(p.next);
  let line, cls = "hint";
  if (p.cancelled) line = t("admin.update.ph_cancelled");
  else if (p.phase === "blocked") { line = t(u.nodes.length > 1 ? "admin.update.ph_failed" : "admin.update.ph_failed_one", { pi: who }); cls = "err"; }
  else if (p.phase === "done") { line = t("admin.update.ph_done", { version: v }); cls = "okmsg"; }
  else if (p.reason === "unreachable") line = t("admin.update.ph_unreachable", { pi: who });
  else if (p.reason === "running" || p.reason === "lock") line = t("admin.update.ph_running", { pi: who });
  else line = t("admin.update.ph_next", { pi: who });
  const skipped = u.nodes.filter((n) => String(n.state).startsWith("skip_"));
  return `<div class="card" id="upd-plan"><h3>${esc(title)}</h3>
    <p class="${cls}">${esc(line)}</p>
    ${skipped.length ? `<p class="err">${th("admin.update.skipped", { pis: skipped.map((n) => piName(n.id)).join(", ") })}</p>` : ""}
    <p class="hint">${th(p.by === "usb" ? "admin.update.by_usb" : "admin.update.by", { name: p.by || "?", time: atTime(p.at) })}</p>
    ${updActive(u) && !p.cancelled ? `<div class="form">
      ${p.phase === "blocked" ? `<button type="button" class="btn pri" id="upd-retry">${th("admin.update.retry")}</button>` : ""}
      <button type="button" class="btn" id="upd-cancel">${th("admin.update.cancel")}</button></div>` : ""}
  </div>`;
}
function updUpload(u) {
  const up = u.upload;
  if (!up) return "";
  const due = up.due || [];
  const busy = updActive(u);
  return `<div class="upd-file"><p><b translate="no">${th("admin.update.file_version", { version: up.version })}</b> ·
      ${th("admin.update.signed_by", { name: up.signer?.comment || shortFp(up.signer?.fp) })} · ${esc(fmtMB(up.size))}</p>
    <p class="hint">${th(up.source ? "admin.update.from_net" : up.by === "usb" ? "admin.update.from_usb" : "admin.update.uploaded",
      { name: up.by || "?", time: atTime(up.at), pi: piName(up.node), source: up.source || "" })}</p>
    ${due.length ? `<p class="hint">${th("admin.update.would_update", { pis: due.map(piName).join(", "), version: up.version })}</p>
      <button type="button" class="btn pri" id="upd-start" ${busy ? "disabled" : ""}>${th("admin.update.start")}</button>
      ${busy ? `<p class="hint">${th("admin.update.start_busy")}</p>` : ""}`
      : `<p class="okmsg">${th("admin.update.all_have", { version: up.version })}</p>`}</div>`;
}
function updKeys(u) {
  const h = u.helper || {};
  if (!h.installed) return `<p class="err">${th("admin.update.no_helper")}</p>`;
  const probs = (h.problems || []).map((p) => `<p class="err" translate="no">${esc(p)}</p>`).join("");
  if (!h.keys?.length) return `<p class="err">${th("admin.update.no_key")}</p>${probs}`;
  return `<p class="hint">${th("admin.update.keys")} ${h.keys.map((k) => `<b>${esc(k.comment || "–")}</b>
    <code class="fp" translate="no" title="${esc(k.fp)}">${esc(shortFp(k.fp))}</code>`).join(", ")}</p>${probs}`;
}
function updBack(u) {
  const r = u.rollback;
  if (!r || r.from === r.to) return "";
  const older = r.older !== false;                     // vergleicht der Server (beide Versionsformate)
  return `<div class="card" id="upd-back"><h3>${th("admin.update.back_title")}</h3>
    <p class="hint">${th(older ? "admin.update.back_hint" : "admin.update.forward_hint", { from: r.from, to: r.to })}</p>
    <button type="button" class="btn danger" id="upd-rollback" ${updActive(u) ? "disabled" : ""}>${th(older ? "admin.update.back" : "admin.update.forward", { version: r.to })}</button>
  </div>`;
}
function updUsb(u) {
  const s = u.usb;
  if (!s || !UPD_USB[s.state]) return "";
  const bad = ["invalid", "unreadable", "failed"].includes(s.state);
  return `<p class="${bad ? "err" : "hint"}">${th(UPD_USB[s.state], { version: s.version || "", time: atTime(s.at) })}${s.text ? ` – ${esc(s.text)}` : ""}</p>`;
}
function updDraw() {
  const u = UPD;
  if (!u || page !== "maint") return;
  $("#upd-status").innerHTML = `${updPlan(u)}<div class="card"><h3>${th("admin.update.pis")}</h3>${updNodes(u)}
    ${updLost ? `<p class="hint">${th("admin.update.lost")}</p>` : ""}</div>${updBack(u)}`;
  $("#upd-keys").innerHTML = updKeys(u);
  $("#upd-upload").innerHTML = updUpload(u);
  $("#upd-usb").innerHTML = updUsb(u);
  netDraw();
}
// Aus dem Internet (update_net.py): neuster GitHub-Release; der Pi ist nur fürs Suchen/Herunterladen kurz online.
// Hier nur Quelle (Repo, Token), Suchen und Herunterladen; die Verbindung (wie der Pi ins Internet kommt) und der
// Schalter «Internet für Updates» stehen auf der Seite «Internet» (internet.js, T5).
let NET = null;
const NET_BUSY = { connecting: "admin.update.net_connecting", searching: "admin.update.net_searching",
  downloading: "admin.update.net_downloading", verifying: "admin.update.net_verifying" };
export const NET_VIA = { lan: "admin.update.net_via_lan", wlan: "admin.update.net_via_wlan", usb: "admin.update.net_via_usb" };
const NET_VIA_SHORT = { lan: "admin.update.net_short_lan", wlan: "admin.update.net_short_wlan", usb: "admin.update.net_short_usb" };
export function netDevices(n, via) {
  const d = n.devices || { wlan: [], usb: [] };
  if (via === "wlan") return d.wlan.length ? "" : `<p class="hint err">${th("admin.update.net_no_wlan")}</p>`;
  if (via === "usb") {
    const note = `<br><small>${th("admin.update.net_no_tether")}</small>`;          // Handy-Tethering zählt nie
    if (!d.usb.length) return `<p class="hint">${th("admin.update.net_no_usb")}${note}</p>`;
    return `<p class="hint">${d.usb.map((a) => th(a.link ? "admin.update.net_usb_link" : "admin.update.net_usb_nolink",
      { iface: a.name })).join(" · ")}${note}</p>`;
  }
  return "";
}
function netForm(n) {
  const c = n.config;
  const saved = (v) => (v ? th("admin.update.net_saved", { value: v }) : "");
  return `<form id="net-form">
    <div class="form">
      <label>${th("admin.update.net_repo")}<input name="repo" value="${esc(c.repo)}" placeholder="T-0-N/FestPOS" autocomplete="off" spellcheck="false" style="width:220px"></label>
      <label>${th("admin.update.net_token")}<input name="token" type="password" autocomplete="new-password" placeholder="${saved(c.token)}" style="width:260px"></label>
      ${c.token ? `<label class="check"><input type="checkbox" name="clear_token"> ${th("admin.update.net_token_clear")}</label>` : ""}
    </div>
    <p class="net-title">${th("admin.update.net_via_is", { via: t(NET_VIA_SHORT[c.via] || NET_VIA_SHORT.lan) })}
      <a href="#devices/net" data-page="devices/net">${th("admin.update.net_change")}</a></p>
    ${n.allowed === false ? `<p class="err">${th("admin.update.net_off")}</p>` : ""}
    <div class="form net-buttons">
      <button type="submit" class="btn" id="net-save">${th("admin.update.net_save")}</button>
      <button type="button" class="btn pri" id="net-check" ${n.allowed === false ? "data-off" : ""}>${th("admin.update.net_check")}</button>
    </div>
  </form>`;
}
// Seite «Internet»: wie der Pi ins Internet kommt (gleiche Einstellung, PUT /update/net)
export function netConnForm(n) {
  const c = n.config;
  const way = (v, fields) => `<label class="check"><input type="radio" name="via" value="${v}" ${c.via === v ? "checked" : ""}> ${th(NET_VIA[v])}</label>
    <div class="net-way" data-via="${v}" ${c.via === v ? "" : "hidden"}>${fields}<div class="net-dev">${netDevices(n, v)}</div></div>`;
  return `<form id="net-form">
    <p class="net-title"><b>${th("admin.update.net_via")}</b> ${th("admin.update.net_on_pi", { pi: piName(n.node) })}</p>
    <div class="net-ways">
      ${way("lan", `<div class="form"><label>${th("admin.update.net_gateway")}<input name="gateway" value="${esc(c.gateway)}" placeholder="192.168.55.1" inputmode="decimal" style="width:150px"></label>
        <label>${th("admin.update.net_dns")}<input name="dns" value="${esc(c.dns)}" placeholder="1.1.1.1" inputmode="decimal" style="width:150px"></label></div>`)}
      ${way("wlan", `<div class="form"><label>${th("admin.update.net_ssid")}<input name="ssid" value="${esc(c.ssid)}" autocomplete="off" style="width:180px"></label>
        <label>${th("admin.update.net_psk")}<input name="psk" type="password" autocomplete="new-password" placeholder="${c.psk_set ? th("admin.update.net_saved", { value: "••••" }) : ""}" style="width:180px"></label></div>`)}
      ${way("usb", "")}
    </div>
    <div class="form net-buttons"><button type="submit" class="btn pri">${th("admin.update.net_save")}</button></div>
  </form>`;
}
export function bindNetConn(n) {
  NET = n;
  const f = $("#net-form");
  f.addEventListener("change", (e) => {
    if (e.target.name !== "via") return;
    $$("#net-form .net-way").forEach((w) => { w.hidden = w.dataset.via !== e.target.value; });
  });
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await netSave(e.target);
    if (r) $$("#net-form .net-way").forEach((w) => { $(".net-dev", w).innerHTML = netDevices(r, w.dataset.via); });
  });
}
function netState(n) {
  const j = n.job;
  if (!j) return "";
  const f = j.found;
  if (NET_BUSY[j.state]) {
    const prog = j.state === "downloading" && j.total ? th("admin.update.net_progress", { done: fmtMB(j.done), total: fmtMB(j.total) }) : "";
    return `<p class="hint net-busy">${th(NET_BUSY[j.state], { via: t(NET_VIA_SHORT[j.via] || NET_VIA_SHORT.lan) })} ${prog}</p>`;
  }
  if (j.state === "failed") return `<p class="err">${th(j.op === "check" ? "admin.update.net_check_failed" : "admin.update.net_dl_failed")} ${esc(j.text || "")}</p>`;
  if (j.op === "download") return `<p class="okmsg">${th("admin.update.net_loaded", { version: j.version || f?.version || "" })}</p>`;
  if (!f) return "";
  if (!f.newer) return `<p class="okmsg">${th("admin.update.net_none", { tag: f.tag, version: n.running })}</p>`;
  return `<div class="upd-file"><p><b translate="no">${th("admin.update.net_found", { version: f.version })}</b>
      ${f.name && f.name !== f.tag ? ` · ${th("admin.update.net_release", { name: f.name })}` : ""}
      ${f.published ? ` · ${th("admin.update.net_date", { date: fmtDate(f.published) })}` : ""} · ${esc(fmtMB(f.size))}</p>
    <p class="hint">${th("admin.update.net_found_hint", { version: n.running, via: t(NET_VIA_SHORT[n.config.via] || NET_VIA_SHORT.lan) })}</p>
    <button type="button" class="btn pri" id="net-download" ${n.busy || updActive(UPD) ? "disabled" : ""}>${th("admin.update.net_download")}</button></div>`;
}
function netDraw() {
  if (!NET || !$("#net-state")) return;
  $("#net-state").innerHTML = netState(NET);
  $$("#net-form button").forEach((b) => { b.disabled = NET.busy || b.hasAttribute("data-off"); });
}
// beide Formulare (Update: Quelle, Internet: Verbindung) speichern über dieselbe Einstellung – was nicht im Formular
// steht, kommt aus dem gespeicherten Stand (Geheimnisse: null = unverändert)
function netBody(form) {
  const c = NET.config, v = (k) => (form.elements[k] ? form.elements[k].value : c[k] ?? "");
  return { repo: v("repo"), token: form.elements.token?.value || null, clear_token: !!form.elements.clear_token?.checked,
    via: form.elements.via ? form.elements.via.value : c.via, gateway: v("gateway"), dns: v("dns"), ssid: v("ssid"),
    psk: form.elements.psk?.value || null };
}
async function netSave(form, quiet) {
  const r = await guard(() => api("PUT", "/update/net", netBody(form)), quiet ? undefined : t("admin.update.net_saved_ok"));
  if (!r) return null;
  NET = r;
  if (form.elements.repo) form.elements.repo.value = r.config.repo;
  if (form.elements.token) {
    form.elements.token.value = "";
    form.elements.token.placeholder = r.config.token ? t("admin.update.net_saved", { value: r.config.token }) : "";
  }
  if (form.elements.psk) form.elements.psk.value = "";
  if (form.elements.clear_token && !r.config.token) form.elements.clear_token.closest("label").remove();
  if (r.config.psk_set && form.elements.psk) form.elements.psk.placeholder = t("admin.update.net_saved", { value: "••••" });
  return r;
}

async function updConfirm(path, body, question) {
  if (!confirm(question)) return null;
  let r = await guard(() => api("POST", path, body));
  if (r && r.started === false && r.warnings?.length) {        // offene Druckaufträge, eben bestellt: nochmals fragen
    const list = r.warnings.map((w) => `• ${t(UPD_WARN[w.code] || w.code, w)}`).join("\n");
    if (!confirm(`${t("admin.update.warn_title")}\n\n${list}\n\n${t("admin.update.warn_go")}`)) return null;
    r = await guard(() => api("POST", path, { ...body, confirm: true }));
  }
  return r;
}
// Wartung: Reiter Update und Sicherung (Sicherung herunterladen/einspielen, Bestellungen löschen)
RENDER.maint = async (refresh = false) => {
  let u;
  try { [u, NET] = await Promise.all([api("GET", "/update"), api("GET", "/update/net")]); updLost = false; }
  catch (e) {
    if (!refresh) { toast(e.message, true); return; }
    updLost = true;                                    // der Pi dieser Seite startet gerade neu: weiter versuchen
    if (UPD) updDraw();
    return;
  }
  if (page !== "maint") return;
  UPD = u;
  if (refresh && $("#upd-status")) return updDraw();
  $("#main").innerHTML = `<h2>${th("admin.nav.maint")}</h2>${tabBar("maint")}<div id="upd-page" data-in="update">
    <div id="upd-status"></div>
    <div class="card" id="upd-net"><h3>${th("admin.update.net_title")}</h3>
      <p class="hint">${th("admin.update.net_hint")}</p>
      ${netForm(NET)}
      <div id="net-state"></div>
    </div>
    <div class="card" id="upd-new"><h3>${th("admin.update.new_title")}</h3>
      <p class="hint">${th("admin.update.new_hint")}</p>
      <div id="upd-keys"></div>
      <form class="form" id="upd-form">
        <label class="upd-pick">${th("admin.update.pick")}<input type="file" id="upd-files" accept=".zip,.sig" multiple></label>
        <button class="btn" id="upd-check">${th("admin.update.check")}</button>
      </form>
      <div id="upd-upload"></div>
    </div>
    <div class="card"><h3>${th("admin.update.usb_title")}</h3><p class="hint">${th("admin.update.usb_hint")}</p><div id="upd-usb"></div></div>
  </div>
  ${backupCards()}
  <div class="card" data-in="backup"><h3>${th("admin.reset.title")}</h3>
    <p class="hint">${th("admin.reset.hint")}</p>
    <form class="form" id="reset">
      <label>${th("admin.reset.confirm_label", { word: t("admin.reset.word") })}<input name="c" autocomplete="off" style="width:180px"></label>
      <button class="btn danger">${th("admin.reset.button")}</button>
    </form>
  </div>`;
  applyTabs();
  updDraw();
  backupInit();
  $("#reset").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (e.target.c.value.trim().toUpperCase() !== t("admin.reset.word").toUpperCase()) return toast(t("admin.reset.type_word", { word: t("admin.reset.word") }), true);
    const r = await guard(() => api("POST", "/reset-orders?confirm=JA"));
    if (r) { toast(t("admin.reset.done", { count: r.deleted_orders })); e.target.reset(); }
  });
  $("#upd-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const files = [...$("#upd-files").files];
    const zip = files.find((f) => /\.zip$/i.test(f.name)), sig = files.find((f) => /\.sig$/i.test(f.name));
    if (!zip || !sig) return toast(t("admin.update.pick_both"), true);
    const fd = new FormData();
    fd.append("bundle", zip, zip.name);
    fd.append("sig", sig, sig.name);
    const btn = $("#upd-check");
    btn.disabled = true; btn.textContent = t("admin.update.checking");
    const r = await guard(() => api("POST", "/update/upload", fd), t("admin.update.checked"));
    btn.disabled = false; btn.textContent = t("admin.update.check");
    if (r) { $("#upd-files").value = ""; await RENDER.maint(true); }
  });
  $("#net-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (await netSave(e.target)) netDraw();
  });
  $("#upd-page").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || !UPD) return;
    if (b.id === "net-check" || b.id === "net-download") {
      if (b.id === "net-check" && !(await netSave($("#net-form"), true))) return;
      if (b.id === "net-download" && !confirm(t("admin.update.net_download_q", { version: NET.job?.found?.version || "" }))) return;
      const r = await guard(() => api("POST", b.id === "net-check" ? "/update/net/check" : "/update/net/download"));
      if (r) { NET = r; netDraw(); }
      return;
    }
    if (b.id === "upd-start") {
      const r = await updConfirm("/update/start", { sha: UPD.upload.sha }, t("admin.update.start_q", { version: UPD.upload.version }));
      if (r?.started) { toast(t("admin.update.started")); await RENDER.maint(true); }
    } else if (b.id === "upd-rollback") {
      const r = await updConfirm("/update/rollback", {}, t("admin.update.back_q", { version: UPD.rollback.to }));
      if (r?.started) { toast(t("admin.update.started")); await RENDER.maint(true); }
    } else if (b.id === "upd-cancel") {
      if (!confirm(t("admin.update.cancel_q"))) return;
      if (await guard(() => api("POST", "/update/cancel"), t("admin.update.cancelled"))) await RENDER.maint(true);
    } else if (b.id === "upd-retry") {
      if (await guard(() => api("POST", "/update/retry"), t("admin.update.retrying"))) await RENDER.maint(true);
    }
  });
};
