/* FestPOS – Verwaltung › Netzwerkgeräte auf der Statusanzeige (Geräte & Netz › Netz & Internet). Handys: Reiter «Handhelds & PC» (devices.js). */
import { $, api, esc, guard, toast } from "./core.js";
import { rerender } from "./shell.js";

// ------------------------------------------------------------------ Geräte auf der Statusanzeige (Netzwerk)
// Eine gespeicherte Liste (monitor_devices) für Handys und Netzwerk; hier nur die Netzwerkgeräte, die Handys bleiben
// stehen (die ändert die Seite «Geräte» über PUT /devices/phone).
// «Im Einsatz» aus: bleibt eingetragen, wird aber nicht geprüft und nicht angezeigt (z.B. Access Point im Zelt).
function monList(s) {
  let mon;
  try { mon = JSON.parse(s.monitor_devices || "null"); } catch { mon = null; }
  if (!Array.isArray(mon)) mon = String(s.monitor_hosts ?? "Switch=192.168.55.30; Access Point=192.168.55.40")
    .split(/[;\n]/).map((x) => x.trim()).filter(Boolean).map((x) => {        // ganz alte Form "Name=IP; …"
      const [n, h = n] = x.split("="), [ip, port] = h.trim().split(":");
      return { name: n.trim(), kind: "net", ip, port: port ? +port : null };
    });
  return mon.map((d) => ({ name: d.name, kind: d.kind === "phone" ? "phone" : "net", ip: d.ip, port: d.port ?? null,
    active: d.active !== false, fully_pin: d.fully_pin }));
}
const monPlain = ({ name, kind, ip, port, active }) => ({ name, kind, ip, port, active });
export const switchHTML = (on, cls, label) => `<label class="sw"><input type="checkbox" class="${cls}" ${on ? "checked" : ""}><i aria-hidden="true"></i>
  <span class="${label.hidden ? "sr" : ""}">${esc(label.text)}</span></label>`;
function monRowHTML(kind, d) {
  return `<tr class="${d.active === false ? "off" : ""}">
    <td><input class="m-name" value="${esc(d.name || "")}" maxlength="30" placeholder="${th("admin.net.name_placeholder")}" style="width:150px"></td>
    <td><input class="m-ip" value="${esc(d.ip || "")}" maxlength="63" placeholder="192.168.55.30" style="width:150px"></td>
    <td><input class="m-port" type="number" min="1" max="65535" value="${d.port ?? ""}" placeholder="${th("admin.monitor.ping")}" style="width:90px"></td>
    <td>${switchHTML(d.active !== false, "m-active", { text: t("admin.common.in_use"), hidden: true })}</td>
    <td><button type="button" class="btn m-del" title="${th("admin.monitor.remove")}" aria-label="${th("admin.monitor.remove")}">✕</button></td></tr>`;
}
export function monTable(kind, id) {
  return `<div class="scroll"><table class="mon-table"><thead><tr><th>${th("admin.monitor.name")}</th><th>${th("admin.monitor.ip")}</th>
    <th>${th("admin.monitor.port")}</th><th>${th("admin.common.in_use")}</th><th></th></tr></thead>
    <tbody id="${id}"></tbody></table></div>`;
}
// Speichern: frisch lesen, nur die eigene Art ersetzen (die andere Seite könnte inzwischen etwas geändert haben)
async function monSave(kind, items) {
  const s = await api("GET", "/settings");
  const keep = monList(s).filter((d) => d.kind !== kind).map(monPlain);
  const list = [...keep, ...items];
  return api("PUT", "/settings", { monitor_devices: list });
}
export function bindMon(kind, s, rowsSel, formSel, addSel) {
  const rows = $(rowsSel), mine = monList(s).filter((d) => d.kind === kind);
  rows.innerHTML = mine.map((d) => monRowHTML(kind, { ...d, saved: true })).join("");
  [...rows.children].forEach((tr, i) => { tr._d = { ...mine[i], saved: true }; });
  $(addSel).addEventListener("click", () => {
    rows.insertAdjacentHTML("beforeend", monRowHTML(kind, { kind, active: true }));
    rows.lastElementChild._d = { kind, active: true, saved: false };
    rows.lastElementChild.querySelector(".m-name").focus();
  });
  rows.addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (e.target.closest(".m-del")) tr.remove();
  });
  // «Im Einsatz»: bei einem gespeicherten Gerät sofort übernehmen (nur dieses eine), sonst mit «Speichern»
  rows.addEventListener("change", async (e) => {
    const cb = e.target.closest(".m-active");
    if (!cb) return;
    const tr = cb.closest("tr");
    tr.classList.toggle("off", !cb.checked);
    if (!tr._d.saved) return;
    const fresh = monList(await api("GET", "/settings").catch(() => ({})));
    const hit = fresh.find((d) => d.kind === kind && d.ip === tr._d.ip && d.name === tr._d.name);
    if (!hit) return toast(t("admin.net.save_first"), true);
    hit.active = cb.checked;
    if (await guard(() => api("PUT", "/settings", { monitor_devices: fresh.map(monPlain) }),
      t(cb.checked ? "admin.net.on" : "admin.net.off", { name: tr._d.name }))) tr._d.active = cb.checked;
    else { cb.checked = !cb.checked; tr.classList.toggle("off", !cb.checked); }
  });
  $(formSel).addEventListener("submit", async (e) => {
    e.preventDefault();
    const list = [];
    for (const tr of rows.querySelectorAll("tr")) {
      const name = tr.querySelector(".m-name").value.trim(), ip = tr.querySelector(".m-ip").value.trim();
      const port = tr.querySelector(".m-port").value.trim();
      if (!name && !ip) continue;
      if (!/^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$/.test(ip)) return toast(t("admin.monitor.bad_ip", { name: name || t("admin.monitor.device") }), true);
      if (port && !(+port >= 1 && +port <= 65535)) return toast(t("admin.monitor.bad_port", { name: name || ip }), true);
      list.push({ name: name || ip, kind, ip, port: port ? +port : null, active: tr.querySelector(".m-active").checked });
    }
    if (await guard(() => monSave(kind, list), t("admin.common.saved"))) rerender();
  });
}
