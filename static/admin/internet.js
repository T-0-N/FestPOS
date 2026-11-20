/* FestPOS – Verwaltung › Geräte & Netz › Reiter «Netz & Internet» (T5, T34): alles zum Internet an einem Ort. FestPOS läuft ohne Internet; der Pi
   geht nur kurz online – für Updates (Schalter a) und um neuen Handys beim Einrichten Internet weiterzugeben
   (Schalter b). Dazu die Verbindung des Pi (update_net.py) und der Mobilfunk-Router. Schalter und Verbindung nur
   Admin; der Mobilfunk-Router wie bisher ab Festleitung (ansehen, prüfen). Darunter (nur Admin) die Netzwerkgeräte
   auf der Statusanzeige (monitor.js, bis T34 unter Pi & Netz). */
import { $, api, can, esc, guard } from "./core.js";
import { pageHead, RENDER, rerender, view } from "./shell.js";
import { bindMon, monTable, switchHTML } from "./monitor.js";
import { bindNetConn, netConnForm } from "./update.js";
import { bindUplink, setUplink, uplinkCard } from "./uplink.js";
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
function keepLine(k) {
  if (!k?.on) return "";
  if (k.state !== "up") {
    return `<p><span class="status ${k.state === "failed" || k.state === "no_way" ? "bad" : "pending"}">${th(KEEP_STATE[k.state] || "admin.internet.keep_wait")}</span>${k.text ? ` <small>${esc(k.text)}</small>` : ""}</p>`;
  }
  const parts = [th("admin.internet.keep_since", { time: I18n.fmtTime(k.since * 1000) })];
  if (k.peer) parts.push(th("admin.internet.keep_via_pi", { pi: esc(String(k.peer).toUpperCase()) }));
  if (k.share) parts.push(th("admin.internet.keep_shared"));
  parts.push(th(k.online ? "admin.internet.keep_online" : "admin.internet.keep_offline"));
  if (k.tunnel != null) parts.push(th(k.tunnel ? "admin.internet.keep_tunnel_on" : "admin.internet.keep_tunnel_off"));
  return `<p><span class="status ${k.online ? "ok" : "pending"}">${parts.join(" · ")}</span></p>`;
}
RENDER.internet = async () => {
  const adm = can("admin");
  const [net, prov, up, s] = await Promise.all([adm ? guard(() => api("GET", "/update/net")) : null,
    adm ? api("GET", "/fully/provision").catch(() => null) : null, api("GET", "/uplink").catch(() => null),
    adm ? api("GET", "/settings").catch(() => null) : null]);
  if (view !== "internet" || (adm && !net)) return;    // inzwischen andere Seite (REV-048)
  setUplink(up);
  const sh = prov?.share;
  $("#main").innerHTML = `${pageHead()}
    ${adm ? `<div class="card"><h3>${th("admin.internet.use_title")}</h3>
      <p class="hint">${th("admin.internet.intro")}</p>
      <div class="swrow">${switchHTML(net.allowed !== false, "net-sw", { text: t("admin.internet.updates"), hidden: true })}
        <div><b>${th("admin.internet.updates")}</b><p class="hint">${th("admin.internet.updates_hint")}</p></div></div>
      <div class="swrow">${switchHTML(sh?.allowed !== false, "share-sw", { text: t("admin.internet.phones"), hidden: true })}
        <div><b>${th("admin.internet.phones")}</b><p class="hint">${th("admin.internet.phones_hint")}</p>${shareLine(sh)}</div></div>
      <div class="swrow">${switchHTML(!!net.keep?.on, "keep-sw", { text: t("admin.internet.keep"), hidden: true })}
        <div><b>${th("admin.internet.keep")}</b><p class="hint">${th("admin.internet.keep_hint")}</p>${keepLine(net.keep)}</div></div>
    </div>
    <div class="card"><h3>${th("admin.internet.conn_title")}</h3><p class="hint">${th("admin.internet.conn_hint")}</p>${netConnForm(net)}</div>` : ""}
    ${uplinkCard()}
    ${adm && s ? `<form class="card" id="netform"><h3>${th("admin.net.title")}</h3>
      <p class="hint">${th("admin.net.hint")}</p>
      ${monTable("net", "net-rows")}
      <div class="form" style="margin-top:8px"><button type="button" class="btn" id="net-add">${th("admin.monitor.add")}</button>
        <button class="btn pri">${th("admin.common.save")}</button></div>
    </form>` : ""}`;
  if (adm && s) bindMon("net", s, "#net-rows", "#netform", "#net-add");
  if (adm) {
    bindNetConn(net);
    const flag = (sel, key, on, off) => $(sel).addEventListener("change", async (e) => {
      if (!await guard(() => api("PUT", "/settings", { [key]: e.target.checked }), t(e.target.checked ? on : off))) e.target.checked = !e.target.checked;
    });
    flag(".net-sw", "net_updates", "admin.internet.updates_on", "admin.internet.updates_off");
    flag(".share-sw", "net_phones", "admin.internet.phones_on", "admin.internet.phones_off");
    flag(".keep-sw", "net_keep", "admin.internet.keep_on", "admin.internet.keep_off");
    $("#share-off")?.addEventListener("click", async () => {
      if (await guard(() => api("POST", "/fully/internet", { on: false }), t("admin.fully.share_is_off"))) rerender();
    });
  }
  bindUplink();
};
