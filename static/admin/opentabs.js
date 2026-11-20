/* FestPOS – Verwaltung › Betrieb › Offene Tische (app/tabs.py, Einstellung «Tisch offen lassen»): alle offenen Tische,
   übergeben (anderes Handy), abrechnen (Rest mit Zahlart), als «nicht bezahlt» ausbuchen. Alles steht im Protokoll. */
import { $, api, chf, esc, fmtTime, guard, wordOf } from "./core.js";
import { PAY_OPTS, payName } from "./ledger.js";
import { page, RENDER } from "./shell.js";

let TB = { list: [], phones: [], word: null };
const cap = (s) => (s ? s.charAt(0).toLocaleUpperCase() + s.slice(1) : s);
// Handys mit Gerätezertifikat (Verwaltung › Geräte): an eines davon lässt sich ein Tisch übergeben
function phonesOf(st) {
  try { return JSON.parse(st?.monitor_devices || "[]").filter((x) => x.kind === "phone" && x.cert); } catch { return []; }
}

RENDER.opentabs = async () => {
  const [d, st] = await Promise.all([guard(() => api("GET", "/tabs")), api("GET", "/settings").catch(() => null)]);
  if (!d || page !== "opentabs") return;
  TB = { list: d.tabs, idle: d.idle, mode: d.mode, word: wordOf(st),
    phones: phonesOf(st) };
  const sum = d.tabs.reduce((s, x) => s + x.open, 0);
  const late = (x) => d.idle > 0 && x.last_at && Date.now() - Date.parse(x.last_at) > d.idle * 60000;
  $("#main").innerHTML = `<h2>${th("admin.nav.opentabs")}</h2>
    ${d.mode !== "open" ? `<p class="hint">${th("admin.tabs.mode_off")}</p>` : ""}
    ${d.tabs.length ? `<div class="warnbox"><p>${th("admin.tabs.open_box", { count: d.tabs.length, amount: chf(sum) })}</p></div>` : ""}
    <div class="card">${d.tabs.length ? `<table class="tbl"><thead><tr><th>${esc(cap(TB.word.one))}</th><th>${th("admin.tabs.phone")}</th>
      <th>${th("admin.tabs.since")}</th><th>${th("admin.tabs.last")}</th><th class="num">${th("admin.tabs.orders")}</th>
      <th class="num">${th("admin.tabs.paid")}</th><th class="num">${th("admin.tabs.open")}</th><th></th></tr></thead><tbody>
      ${d.tabs.map((x) => `<tr class="${late(x) ? "tablate" : ""}"><td><b>${esc(x.table)}</b></td><td>${esc(x.owner_name)}</td>
        <td>${esc(fmtTime(x.opened_at))}</td><td>${esc(fmtTime(x.last_at))}</td><td class="num">${x.orders}</td>
        <td class="num">${x.paid ? chf(x.paid) : ""}</td><td class="num">${chf(x.open)}</td>
        <td class="acts"><button class="btn sm" data-tab-transfer="${x.id}">${th("admin.tabs.transfer")}</button>
          <button class="btn sm" data-tab-settle="${x.id}">${th("admin.tabs.settle")}</button>
          <button class="btn sm danger" data-tab-unpaid="${x.id}" ${x.paid ? "disabled" : ""}>${th("admin.tabs.unpaid")}</button></td></tr>`).join("")}
      </tbody></table>` : `<p class="hint">${th("admin.tabs.none")}</p>`}
      <p class="hint">${th("admin.tabs.hint")}</p></div>`;
};

function dialog(html, onOk) {
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog">${html}<div class="form" style="margin-top:12px">
    <button class="btn pri" value="ok">${th("admin.tabs.do")}</button><button type="button" class="btn" data-x>${th("admin.common.cancel")}</button></div></form>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.querySelector("[data-x]").addEventListener("click", () => dlg.close());
  dlg.querySelector("form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (await onOk(e.target)) { dlg.close(); RENDER.opentabs(); }
  });
  dlg.showModal();
}

document.addEventListener("click", (e) => {
  if (page !== "opentabs") return;
  const b = e.target.closest("button");
  if (!b) return;
  const tab = TB.list.find((x) => x.id === +(b.dataset.tabTransfer || b.dataset.tabSettle || b.dataset.tabUnpaid));
  if (!tab) return;
  const head = `<h3>${esc(cap(TB.word.one))} ${esc(tab.table)} · ${chf(tab.open)}</h3>`;
  if (b.dataset.tabTransfer) {
    dialog(`${head}<p class="hint">${th("admin.tabs.transfer_hint")}</p><div class="form"><label>${th("admin.tabs.phone")}
      <select name="dev">${TB.phones.map((p) => `<option value="${esc(p.cert)}">${esc(p.name)}</option>`).join("")}</select></label></div>`,
    (f) => guard(() => api("POST", `/tabs/${tab.id}/transfer`, { device_id: f.dev.value }), t("admin.tabs.transferred")));
  } else if (b.dataset.tabSettle) {
    dialog(`${head}<p class="hint">${th("admin.tabs.settle_hint", { amount: chf(tab.open) })}</p><div class="form"><label>${th("admin.tabs.payment")}
      <select name="pay">${PAY_OPTS.filter(([k]) => k !== "payrexx").map(([k]) => `<option value="${k}">${esc(payName(k))}</option>`).join("")}</select></label></div>`,
    (f) => guard(() => api("POST", `/tabs/${tab.id}/settle`, { payment: f.pay.value }), t("admin.tabs.settled")));
  } else if (b.dataset.tabUnpaid) {
    dialog(`${head}<p class="hint">${th("admin.tabs.unpaid_hint")}</p><div class="form"><label>${th("admin.tabs.note")}
      <input name="note" maxlength="80"></label></div>`,
    (f) => guard(() => api("POST", `/tabs/${tab.id}/unpaid`, { note: f.note.value }), t("admin.tabs.unpaid_done")));
  }
});
