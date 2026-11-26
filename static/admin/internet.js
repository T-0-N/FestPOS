/* FestPOS – Verwaltung › Geräte & Netz › Reiter «Netz & Internet» (T5, T34): alles zum Internet an einem Ort. FestPOS läuft ohne Internet; der Pi
   geht nur kurz online – für Updates (Schalter a) und um neuen Handys beim Einrichten Internet weiterzugeben
   (Schalter b). Dazu die Verbindung des Pi (update_net.py) und der Mobilfunk-Router. Schalter und Verbindung nur
   Admin; der Mobilfunk-Router wie bisher ab Festleitung (ansehen, prüfen). Darunter (nur Admin) die Netzwerkgeräte
   auf der Statusanzeige (monitor.js, bis T34 unter Pi & Netz). */
import { $, api, can, esc, guard, piName } from "./core.js";
import { pageHead, RENDER, rerender, view } from "./shell.js";
import { bindMon, monTable, switchHTML } from "./monitor.js";
import { bindNetConn, netConnForm } from "./update.js";
import { bindUplink, setUplink, uplinkCard } from "./uplink.js";
import { bindGw, gwCard } from "./gateway.js";
import { shareVia } from "./fully.js";

function shareLine(sh) {
  if (!sh) return "";
  if (sh.on) {
    return `<p class="up-active"><span class="status pending">${th("admin.fully.share_on", { time: I18n.fmtTime(sh.since * 1000) })}</span>
      <button type="button" class="btn sm" id="share-off">${th("admin.fully.share_off")}</button></p>`;
  }
  const via = shareVia(sh);
  return `<p class="hint">${via ? th("admin.internet.share_via", { via: esc(via) }) : th("admin.fully.share_setup")}</p>`;
}
// (c) Dauerhaft verbunden: Stand dieses Pi (verbunden seit, über welchen Pi, Payrexx/Tunnel)
const KEEP_STATE = { failed: "admin.internet.keep_failed", no_way: "admin.internet.keep_no_way" };
// Grund, wenn die Verbindung fehlt (update_net.py: Prüfung DNS + HTTPS bzw. Fehler beim Verbinden) – wie Statusanzeige
const INET_REASON = { dns: "status.inet.r_dns", time: "status.inet.r_time", https: "status.inet.r_https",
  wlan_not_found: "status.inet.r_wlan_not_found", wlan: "status.inet.r_wlan", no_wlan: "status.inet.r_no_wlan",
  dhcp: "status.inet.r_dhcp", no_link: "status.inet.r_no_link", no_usb: "status.inet.r_no_usb",
  gateway: "status.inet.r_gateway", helper: "status.inet.r_helper", same_net: "status.inet.r_same_net",
  connect: "status.inet.r_connect" };
const hm = (s) => I18n.fmtTime(s * 1000);
function keepWhen(k) {
  const parts = [];
  if (k.state === "up" && k.since) parts.push(th("admin.internet.keep_since", { time: hm(k.since) }));
  if (k.checked && k.state === "up") parts.push(th("admin.internet.keep_checked", { time: hm(k.checked) }));
  if (k.last && k.state !== "up") parts.push(th("admin.internet.keep_last", { time: hm(k.last) }));
  if (k.next && k.next * 1000 > Date.now() && (k.state !== "up" || (k.fails || 0) >= (k.fails_max || 3))) {
    parts.push(th("admin.internet.keep_next", { time: hm(k.next), count: k.tries || 1 }));
  } else if (k.state === "up" && !k.online && k.fails) {
    parts.push(th("admin.internet.keep_fails", { count: k.fails, max: k.fails_max || 3 }));
  }
  return parts.length ? `<p class="hint" id="keep-when">${parts.join(" · ")}</p>` : "";
}
function keepLine(k) {
  if (!k?.on) return "";
  const why = k.reason ? th(INET_REASON[k.reason] || INET_REASON.connect, { ssid: k.ssid || "" }) : k.text ? esc(k.text) : "";
  if (k.state !== "up") {
    return `<p><span class="status ${k.state === "failed" || k.state === "no_way" ? "bad" : "pending"}">${th(KEEP_STATE[k.state] || "admin.internet.keep_wait")}</span>${why ? ` <small id="keep-why">${why}</small>` : ""}</p>${keepWhen(k)}`;
  }
  if (!k.online && k.online != null) {
    return `<p><span class="status bad">${th("admin.internet.keep_offline")}</span>${why ? ` <small id="keep-why">${why}</small>` : ""}</p>${keepWhen(k)}`;
  }
  const parts = [th("admin.internet.keep_since", { time: hm(k.since) })];
  if (k.peer) parts.push(th("admin.internet.keep_via_pi", { pi: esc(String(k.peer).toUpperCase()) }));
  if (k.share) parts.push(th("admin.internet.keep_shared"));
  parts.push(th(k.online ? "admin.internet.keep_online" : "admin.internet.keep_offline"));
  if (k.tunnel != null) parts.push(th(k.tunnel ? "admin.internet.keep_tunnel_on" : "admin.internet.keep_tunnel_off"));
  return `<p><span class="status ${k.online ? "ok" : "pending"}">${parts.join(" · ")}</span></p>`;
}
// Firewall ins Internet (T-0-N/FestPOS#10, app/firewall.py): Modus für alle Pi, Stand pro Pi
const FW_MODES = { strict: ["admin.fw.m_strict", "admin.fw.s_strict", "admin.fw.set_strict"],
  watch: ["admin.fw.m_watch", "admin.fw.s_watch", "admin.fw.set_watch"], open: ["admin.fw.m_open", "admin.fw.s_open", "admin.fw.set_open"] };
function fwCard(fw, system) {
  if (!fw) return "";
  const nodes = (system?.nodes || []).filter((n) => n.info?.fw);
  const until = fw.until ? new Date(fw.until * 1000).toLocaleString(I18n.locale, { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const row = (n) => {
    const f = n.info.fw, last = f.seen?.[0];
    return `<tr><td><b>${esc(piName(n.id))}</b></td><td>${f.error ? `<span class="status bad">${th("admin.fw.error")}</span>`
      : `<span class="status ${f.mode === "strict" ? "ok" : "pending"}">${th((FW_MODES[f.mode] || FW_MODES.strict)[0])}</span>`}</td>
      <td class="num">${f.unknown ?? "–"}</td><td>${last ? esc(`${last.ip}:${last.port} (${last.proto})`) : "–"}</td></tr>`;
  };
  return `<div class="card" id="fw-card"><h3>${th("admin.fw.title")}</h3>
    <p class="hint">${th("admin.fw.hint")}</p>
    <div class="choice" id="fw-modes">${Object.entries(FW_MODES).map(([m, k]) => `<label><input type="radio" name="fwmode" value="${m}" ${fw.mode === m ? "checked" : ""}>
      <b>${th(k[0])}</b><span>${th(k[1], { hours: fw.max_hours })}</span></label>`).join("")}</div>
    ${fw.mode !== "strict" && until ? `<p class="warnbox">${th("admin.fw.until", { time: esc(until) })}</p>` : ""}
    ${nodes.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.sec.pi")}</th><th>${th("admin.fw.applied")}</th>
      <th class="num">${th("admin.fw.unknown")}</th><th>${th("admin.fw.last")}</th></tr></thead><tbody>${nodes.map(row).join("")}</tbody></table></div>` : ""}
    <details><summary>${th("admin.fw.allowed")}</summary><ul class="hint">${fw.hosts.map((h) => `<li><code translate="no">${esc(h.name)}</code> – ${th(h.what)}</li>`).join("")}
      <li>${th("admin.fw.always")}</li></ul></details></div>`;
}
RENDER.internet = async () => {
  const adm = can("admin");
  const [net, prov, up, s, fw, system, gw] = await Promise.all([adm ? guard(() => api("GET", "/update/net")) : null,
    adm ? api("GET", "/fully/provision").catch(() => null) : null, api("GET", "/uplink").catch(() => null),
    adm ? api("GET", "/settings").catch(() => null) : null, adm ? api("GET", "/firewall").catch(() => null) : null,
    adm ? api("GET", "/system").catch(() => null) : null, adm ? api("GET", "/gateway").catch(() => null) : null]);
  if (view !== "internet" || (adm && !net)) return;    // inzwischen andere Seite (REV-048)
  setUplink(up);
  const sh = prov?.share;
  // Gateway, DNS & Zeitserver oben; die Karten darunter richten sich nach der Art (gateway.js)
  const mode = net?.gw_mode || "auto", fixedGw = gw?.current?.mode === "fest" ? gw.current.gateway : "";
  const useCard = !adm || mode === "ohne" ? "" : `<div class="card" id="usecard"><h3>${th("admin.internet.use_title")}</h3>
      <p class="hint">${th("admin.internet.intro")}</p>${mode === "fest" ? `<p class="hint">${th("admin.gw.use_fest")}</p>` : ""}
      <div class="swrow">${switchHTML(net.allowed !== false, "net-sw", { text: t("admin.internet.updates"), hidden: true })}
        <div><b>${th("admin.internet.updates")}</b><p class="hint">${th("admin.internet.updates_hint")}</p></div></div>
      ${mode === "fest" ? "" : `<div class="swrow">${switchHTML(sh?.allowed !== false, "share-sw", { text: t("admin.internet.phones"), hidden: true })}
        <div><b>${th("admin.internet.phones")}</b><p class="hint">${th("admin.internet.phones_hint")}</p>${shareLine(sh)}</div></div>`}
      <div class="swrow">${switchHTML(!!net.keep?.on, "keep-sw", { text: t("admin.internet.keep"), hidden: true })}
        <div><b>${th("admin.internet.keep")}</b><p class="hint">${th("admin.internet.keep_hint")}</p>${keepLine(net.keep)}</div></div>
    </div>`;
  const connCard = !adm || mode === "ohne" ? "" : mode === "fest"
    ? `<div class="card" id="conncard"><h3>${th("admin.internet.conn_title")}</h3><p>${th("admin.gw.conn_fest", { gw: esc(fixedGw) })}</p></div>`
    : `<div class="card" id="conncard"><h3>${th("admin.internet.conn_title")}</h3><p class="hint">${th("admin.internet.conn_hint")}</p>${netConnForm(net)}</div>`;
  $("#main").innerHTML = `${pageHead()}
    ${adm ? `${gwCard(gw)}${useCard}${connCard}${fwCard(fw, system)}` : ""}
    ${uplinkCard()}
    ${adm && s ? `<form class="card" id="netform"><h3>${th("admin.net.title")}</h3>
      <p class="hint">${th("admin.net.hint")}</p>
      ${monTable("net", "net-rows")}
      <div class="form" style="margin-top:8px"><button type="button" class="btn" id="net-add">${th("admin.monitor.add")}</button>
        <button class="btn pri">${th("admin.common.save")}</button></div>
    </form>` : ""}`;
  if (adm && s) bindMon("net", s, "#net-rows", "#netform", "#net-add");
  if (adm) {
    bindGw(() => rerender());
    if (mode === "auto") bindNetConn(net);
    const flag = (sel, key, on, off) => $(sel)?.addEventListener("change", async (e) => {
      if (!await guard(() => api("PUT", "/settings", { [key]: e.target.checked }), t(e.target.checked ? on : off))) e.target.checked = !e.target.checked;
    });
    flag(".net-sw", "net_updates", "admin.internet.updates_on", "admin.internet.updates_off");
    flag(".share-sw", "net_phones", "admin.internet.phones_on", "admin.internet.phones_off");
    flag(".keep-sw", "net_keep", "admin.internet.keep_on", "admin.internet.keep_off");
    $("#fw-modes")?.addEventListener("change", async (e) => {   // kritisch: «Bitte bestätigen» (api())
      const mode = e.target.value;
      await guard(() => api("PUT", "/firewall", { mode }), t(FW_MODES[mode][2]));
      rerender();                                       // auch nach Abbruch: wieder den gültigen Modus zeigen
    });
    $("#share-off")?.addEventListener("click", async () => {
      if (await guard(() => api("POST", "/fully/internet", { on: false }), t("admin.fully.share_is_off"))) rerender();
    });
  }
  bindUplink();
};
