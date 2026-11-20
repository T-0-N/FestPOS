/* FestPOS – Verwaltung › Mobilfunk-Router: Empfang (app/uplink.py). */
import { $, api, can, esc, guard, toast } from "./core.js";
import { switchHTML } from "./monitor.js";

// ------------------------------------------------------------------ Mobilfunk-Router: Empfang (uplink.py)
// Modelle kommen vom Server (Treiber); Bewertung und Texte sind für alle Modelle gleich.
let UPLINK = null;
const UP_STATE = { ok: "common.uplink.st_ok", pending: "common.uplink.st_pending", cert_new: "common.uplink.st_cert_new",
  cert: "common.uplink.st_cert", no_https: "common.uplink.st_no_https", auth: "common.uplink.st_auth",
  rights: "common.uplink.st_rights", iface: "common.uplink.st_iface", unreachable: "common.uplink.st_unreachable",
  timeout: "common.uplink.st_timeout", bad: "common.uplink.st_bad" };
const UP_WORD = { excellent: "common.uplink.w_excellent", good: "common.uplink.w_good", fair: "common.uplink.w_fair",
  weak: "common.uplink.w_weak", very_weak: "common.uplink.w_very_weak", none: "common.uplink.w_none" };
const UP_PROBLEM = { no_sim: "common.uplink.p_no_sim", pin: "common.uplink.p_pin", puk: "common.uplink.p_puk",
  radio_off: "common.uplink.p_radio_off", denied: "common.uplink.p_denied", searching: "common.uplink.p_searching",
  not_registered: "common.uplink.p_not_registered", no_data: "common.uplink.p_no_data" };
const dbm = (v, unit = "dBm") => `${v < 0 ? "−" : ""}${Math.abs(v).toLocaleString(I18n.locale)} ${unit}`;
function uplinkSummary(r) {
  const d = r.data || {}, g = r.rating || {}, parts = [];
  if (d.rsrp != null) parts.push(`RSRP ${dbm(d.rsrp)}`);
  else if (d.rssi != null) parts.push(`RSSI ${dbm(d.rssi)}`);
  if (d.sinr != null) parts.push(`SINR ${dbm(d.sinr, "dB")}`);
  if (g.word && g.word !== "none") parts.push(t("common.uplink.bars", { word: t(UP_WORD[g.word]), bars: g.bars }));
  if (d.sim_status) parts.push(t("admin.uplink.sim_status", { status: d.sim_status }));
  if (d.operator) parts.push(d.operator + (d.roaming ? ` (${t("common.uplink.roaming")})` : ""));
  const net = [d.tech, [d.band, ...(d.ca || [])].filter(Boolean).join("+")].filter(Boolean).join(" ");
  if (net) parts.push(net);
  if (d.sim) parts.push(t("common.uplink.sim", { slot: d.sim }));
  if (d.model || d.version) parts.push([d.model, d.version].filter(Boolean).join(" "));
  parts.push(t("admin.uplink.as_of", { time: I18n.fmtTime(Date.now() - (r.data_seconds || 0) * 1000) }));
  return parts.join(" · ");
}
function uplinkResult() {
  const u = UPLINK || {}, c = u.config || {};
  if (!c.kind || !c.host) return `<p class="hint">${th("admin.uplink.off")}</p>`;
  if (c.active === false) {                            // nicht im Einsatz: nur der Schalter (und prüfen geht trotzdem)
    const r0 = u.result;
    if (!r0 || r0.state === "pending") {
      return `<div class="up-res">${can("devices") ? `<button type="button" class="btn sm" id="up-check">${th("admin.uplink.check")}</button>` : ""}</div>`;
    }
  }
  const r = u.result || { state: "pending" }, st = UP_STATE[r.state] ? r.state : "bad";
  const lvl = r.rating?.level, cls = st === "ok" ? (lvl === "bad" ? "err" : lvl === "wait" ? "warn" : "ok") : st === "pending" ? "" : "err";
  // Router erreicht, aber kein Netz: statt «Verbunden» den Grund (keine SIM, PIN, Funk aus …)
  const label = st === "ok" && lvl === "bad" ? t(UP_PROBLEM[r.rating.problem] || UP_WORD.none) : t(UP_STATE[st]);
  let html = `<div class="up-res"><span class="fully-st ${cls}">${esc(label)}</span>
    ${can("devices") ? `<button type="button" class="btn sm" id="up-check">${th("admin.uplink.check")}</button>` : ""}</div>`;
  if (r.text && st !== "ok") html += `<p class="hint" translate="no">${esc(r.text)}</p>`;
  if (r.offered_pin && can("admin")) {
    html += `<div class="up-pin"><p class="hint">${th(st === "cert" ? "admin.uplink.pin_changed" : "admin.uplink.pin_new")}</p>
      <code class="fp" translate="no">${esc(r.offered_pin)}</code>
      <button type="button" class="btn pri sm" id="up-trust">${th("admin.uplink.trust")}</button></div>`;
  }
  if (r.data) html += `<p class="hint up-det">${esc(uplinkSummary(r))}</p>`;
  if (r.raw) {                                         // Fehlersuche: was der Router geantwortet hat (ohne Kennungen)
    html += `<details class="up-setup up-raw"><summary>${th("admin.uplink.raw")}</summary>
      <pre translate="no">${esc(Object.entries(r.raw).map(([k, v]) => `${k}: ${v}`).join("\n"))}</pre></details>`;
  }
  if (c.pin) html += `<p class="hint">${th("admin.uplink.pin_set", { date: c.pin_at || "–" })} <code class="fp" translate="no">${esc(c.pin.slice(0, 16))}…</code></p>`;
  return html;
}
function uplinkActive(c) {
  if (!c.kind || !c.host) return "";
  const on = c.active !== false;
  return `<div class="up-active">${can("admin") ? switchHTML(on, "up-active-sw", { text: t("admin.common.in_use"), hidden: false })
      : `<span class="status ${on ? "ok" : ""}">${th(on ? "admin.uplink.in_use" : "admin.uplink.not_in_use")}</span>`}
    ${on ? "" : `<span class="hint">${th("admin.uplink.inactive_hint")}</span>`}</div>`;
}
export function uplinkCard() {
  const u = UPLINK || {}, c = u.config || {}, drivers = u.drivers || [];
  if (!can("admin")) {                                 // Festleitung: nur ansehen und prüfen
    return c.kind ? `<div class="card" id="upform"><h3>${th("admin.uplink.title")}</h3>${uplinkActive(c)}<div id="up-result">${uplinkResult()}</div></div>` : "";
  }
  const drv = drivers.find((d) => d.key === c.kind) || drivers[0] || {};
  return `<form class="card" id="upform"><h3>${th("admin.uplink.title")}</h3>
    ${uplinkActive(c)}
    <p class="hint">${th("admin.uplink.hint")}</p>
    <div class="form">
      <label>${th("admin.uplink.kind")}<select name="kind"><option value="">${th("admin.uplink.kind_none")}</option>
        ${drivers.map((d) => `<option value="${esc(d.key)}" translate="no" ${d.key === c.kind ? "selected" : ""}>${esc(d.label)}</option>`).join("")}</select></label>
      <label>${th("admin.uplink.name")}<input name="name" value="${esc(c.name || "")}" maxlength="30" placeholder="${th("admin.uplink.name_placeholder")}" style="width:150px"></label>
      <label>${th("admin.uplink.host")}<input name="host" value="${esc(c.host || "")}" maxlength="63" placeholder="192.168.55.1" style="width:150px"></label>
      <label>${th("admin.uplink.port")}<input name="port" type="number" min="1" max="65535" value="${esc(c.port ?? "")}" placeholder="${esc(drv.port || 443)}" style="width:90px"></label>
      <label>${th("admin.uplink.iface")}<input name="iface" value="${esc(c.iface || "")}" maxlength="32" placeholder="${esc(drv.iface || "")}" style="width:100px"></label>
      <label>${th("admin.uplink.user")}<input name="user" value="${esc(c.user || "")}" maxlength="40" autocomplete="off" style="width:130px"></label>
      <label>${th(c.password_set ? "admin.uplink.password_new" : "admin.uplink.password")}<input name="pw" type="password" maxlength="100" autocomplete="new-password" style="width:180px"></label>
      <button class="btn pri">${th("admin.common.save")}</button>
    </div>
    <div id="up-result">${uplinkResult()}</div>
    ${u.setup ? `<details class="up-setup"><summary>${th("admin.uplink.setup_title")}</summary>
      <p class="hint">${th("admin.uplink.setup_hint")}</p><pre translate="no">${esc(u.setup)}</pre></details>` : ""}
  </form>`;
}
export function bindUplink() {
  const card = $("#upform");
  if (!card) return;
  const redraw = (r) => { if (r) UPLINK = r; const el = $("#upform"); if (el) { el.outerHTML = uplinkCard(); bindUplink(); } };
  const check = async (btn) => {
    if (btn) btn.disabled = true;
    const r = await guard(() => api("POST", "/uplink/check"));
    if (btn) btn.disabled = false;
    if (r) { UPLINK = r; $("#up-result").innerHTML = uplinkResult(); }
  };
  card.querySelector(".up-active-sw")?.addEventListener("change", async (e) => {
    const on = e.target.checked;
    const r = await guard(() => api("POST", "/uplink/active", { active: on }), t(on ? "admin.uplink.now_on" : "admin.uplink.now_off"));
    if (r) redraw(r); else e.target.checked = !on;
  });
  card.addEventListener("click", async (e) => {
    if (e.target.closest("#up-check")) return check(e.target.closest("#up-check"));
    if (!e.target.closest("#up-trust")) return;
    if (!confirm(t("admin.uplink.trust_confirm"))) return;
    redraw(await guard(() => api("POST", "/uplink/trust", { pin: UPLINK.result.offered_pin }), t("admin.uplink.trusted")));
  });
  card.querySelector('select[name="kind"]')?.addEventListener("change", (e) => {
    const d = (UPLINK?.drivers || []).find((x) => x.key === e.target.value);
    if (d) { card.querySelector('[name="port"]').placeholder = d.port; card.querySelector('[name="iface"]').placeholder = d.iface; }
  });
  if (card.tagName !== "FORM") return;
  card.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { kind: f.kind.value, name: f.name.value.trim(), host: f.host.value.trim(),
      port: +f.port.value || null, user: f.user.value.trim(), iface: f.iface.value.trim() };
    if (body.kind && !/^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$/.test(body.host)) return toast(t("admin.uplink.bad_host"), true);
    if (f.pw.value) body.password = f.pw.value;
    const r = await guard(() => api("PUT", "/uplink", body), t("admin.uplink.saved"));
    if (!r) return;
    redraw(r);
    if (r.config.kind) await check($("#up-check"));     // gleich verbinden: zeigt das Zertifikat zum Übernehmen
  });
}

// von anderen Modulen gesetzt (importierte Variablen sind dort nur lesbar)
export function setUplink(v) { UPLINK = v; }
