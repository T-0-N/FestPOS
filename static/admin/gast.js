/* FestPOS – Verwaltung › Kasse › «QR-Bestellung am Tisch» (T22, nur Admin).
   PAYREXX-UNGETESTET: noch nie gegen ein echtes Payrexx-Konto gelaufen (PAYREXX_GEPRUEFT in app/payrexx_api.py,
   docs/testplan-payrexx.md).
   Offen/zu, Adresse (Domain des Tunnels), Payrexx (Instanz, Schlüssel nur zum Ändern), Höchstbetrag, Rückerstattung
   nach Minuten, QR-Codes als PDF, Online-Bestellungen mit «Zurückerstatten». Cloudflare Tunnel: Token (nur zum
   Ändern, gilt für alle Pi), Stand pro Pi, «Tunnel entfernen». Server: app/gast.py, app/tunnel.py. */
import { $, api, chf, esc, guard, toast } from "./core.js";
import { rerender } from "./shell.js";
import { confirmUntested, untestedBox } from "./payrexx.js";

let G = null;
export const gastState = () => G;                    // für den Zustand im Titel der Karte (kassepage.js)
const CLOSED = { off: "admin.gast.closed_off", setup: "admin.gast.closed_setup", printer: "admin.gast.closed_printer" };
const stateText = (v) => th(v.closed ? CLOSED[v.closed] || "admin.gast.closed_off" : "admin.gast.is_open");
const STATUS = { waiting: "admin.gast.st_waiting", paid: "admin.gast.st_paid", ordered: "admin.gast.st_ordered",
  rejected: "admin.gast.st_rejected", refunded: "admin.gast.st_refunded", cancelled: "admin.gast.st_cancelled" };

// Stand des Tunnels auf einem Pi -> [Klasse, Text]
function tunnelState(p, set) {
  if (!p.reachable) return ["", t("admin.gast.tun_unreachable")];
  if (p.error) return ["bad", p.error];
  if (p.source == null && !set) return ["", t("admin.gast.tun_off")];
  if (p.cloudflared === false) return ["bad", t("admin.gast.tun_no_cf")];
  if (p.source !== "conf" && p.synced === false) return ["pending", t("admin.gast.tun_wait")];
  const from = p.source === "conf" ? ` (${t("admin.gast.tun_conf")})` : "";
  if (p.active === false) return ["bad", t("admin.gast.tun_down") + from];
  return ["ok", t(p.active ? "admin.gast.tun_up" : "admin.gast.tun_on") + from];
}
function tunnelBox(v) {
  const tu = v.tunnel || {};
  const pis = (v.tunnel_pis || []).map((p) => {
    const [cls, txt] = tunnelState(p, tu.set && tu.ok);
    return `<li>${th("admin.gast.tun_pi", { pi: p.id })} <span class="status ${cls}">${esc(txt)}</span></li>`;
  }).join("");
  return `<h3 style="margin-top:16px">${th("admin.gast.tun_title")}</h3>
    <p class="hint">${th("admin.gast.tun_hint")}</p>
    <div class="form">
      <label>${th(tu.set ? "admin.gast.tun_change" : "admin.gast.tun_token")}<input name="tunnel" data-k="tunnel" data-api="gast" data-type="secret" data-label="admin.gast.tun_token" type="password" autocomplete="new-password"
        placeholder="${tu.set ? (tu.ok ? `•••••••• (${esc(tu.id || "")}…)` : th("admin.gast.tun_bad")) : ""}"></label>
    </div>
    <ul class="gast-pis">${pis}</ul>
    ${tu.set ? `<div class="form"><button type="button" class="btn" id="gast-tunnel-off">${th("admin.gast.tun_remove")}</button></div>` : ""}`;
}

export async function gastCard() {
  G = await api("GET", "/gast").catch(() => null);
  if (!G) return "";
  const why = stateText(G);
  return `<form class="card" id="gastform">
    ${untestedBox(G)}
    <p class="hint">${th("admin.gast.intro")}</p>
    <p class="${G.closed ? "warn" : "ok"}" id="gast-state">${why}</p>
    <label class="sw"><input type="checkbox" name="open" data-k="open" data-api="gast" data-type="bool" data-label="admin.gast.open" ${G.open ? "checked" : ""}><i aria-hidden="true"></i> ${th("admin.gast.open")}</label>
    <div class="form">
      <label>${th("admin.gast.domain")}<input name="domain" data-k="domain" data-api="gast" data-type="lower" data-label="admin.gast.domain" value="${esc(G.domain)}" placeholder="${th("admin.gast.domain_ph")}" autocomplete="off"></label>
      <label>${th("admin.gast.instance")}<input name="instance" data-k="instance" data-api="gast" data-type="str" data-label="admin.gast.instance" value="${esc(G.instance)}" autocomplete="off"></label>
      <label>${th(G.key_set ? "admin.gast.key_change" : "admin.gast.key")}<input name="key" data-k="key" data-api="gast" data-type="secret" data-label="admin.gast.key" type="password" autocomplete="new-password"
        placeholder="${G.key_set ? (G.key_ok ? "••••••••" : th("admin.gast.key_bad")) : ""}"></label>
      <label>${th("admin.gast.max")}<input name="max" data-k="max_cents" data-api="gast" data-type="chf" data-label="admin.gast.max" type="number" min="5" max="2000" step="1" value="${Math.round(G.max_cents / 100)}"></label>
      <label>${th("admin.gast.refund_min")}<input name="refund_min" data-k="refund_min" data-api="gast" data-type="int" data-label="admin.gast.refund_min" type="number" min="3" max="120" value="${G.refund_min}"></label>
    </div>
    ${tunnelBox(G)}
    <div class="form"><button type="button" class="btn" id="gast-test" ${G.key_set ? "" : "disabled"}>${th("admin.gast.test")}</button>
      <a class="btn" id="gast-qr" href="/api/admin/gast/qr.pdf" target="_blank" rel="noopener">${th("admin.gast.qr")}</a>
      <button type="button" class="btn" id="gast-list">${th("admin.gast.list")}</button></div>
    <div id="gast-orders"></div>
    <p class="hint">${th("admin.gast.hint_refund")}</p></form>`;
}

const RF = { open: "admin.gast.rf_open", sending: "admin.gast.rf_open", unclear: "admin.gast.rf_unclear",
  failed: "admin.gast.rf_failed" };
// T37: Rückerstattungen, die nicht erledigt sind – unklar/gescheitert mit «Nochmals senden» und «Ist erledigt»
function refunds(o) {
  return (o.refunds || []).filter((r) => r.status !== "done").map((r) => {
    const act = r.status === "unclear" || r.status === "failed";
    return `<div class="${act ? "bad" : "hint"}">${th("admin.gast.rf_line", { amount: chf(r.amount_cents), state: t(RF[r.status] || RF.open) })}
      ${act ? `<button type="button" class="btn small" data-rretry="${r.id}">${th("admin.gast.rf_retry")}</button>
      <button type="button" class="btn small" data-rdone="${r.id}">${th("admin.gast.rf_done")}</button>` : ""}</div>`;
  }).join("");
}
function row(o) {
  const reserved = (o.refunds || []).reduce((a, r) => a + r.amount_cents, 0);
  const failed = (o.refunds || []).some((r) => r.status === "failed");
  return `<tr><td>${o.id}</td><td>${esc(o.table_label)}</td><td>${esc((o.paid_at || o.created_at || "").slice(11, 16))}</td>
    <td class="num">${chf(o.amount_cents)}</td><td>${th(STATUS[o.status] || "admin.gast.st_other")}${o.refund_open ? ` <b class="bad">${th("admin.gast.refund_open")}</b>` : ""}${refunds(o)}</td>
    <td class="num">${o.refunded_cents ? chf(o.refunded_cents) : ""}</td><td>${o.order_id || ""}</td>
    <td>${o.transaction_id && (o.amount_cents - reserved > 0 || failed) ? `<button type="button" class="btn small" data-refund="${o.id}">${th("admin.gast.refund")}</button>` : ""}</td></tr>`;
}
async function showList() {
  const r = await guard(() => api("GET", "/gast/orders?limit=200"));
  if (!r) return;
  $("#gast-orders").innerHTML = r.orders.length ? `<div class="scroll"><table class="list"><thead><tr>
    <th>#</th><th>${th("admin.gast.c_table")}</th><th>${th("admin.gast.c_time")}</th><th>${th("admin.gast.c_amount")}</th>
    <th>${th("admin.gast.c_status")}</th><th>${th("admin.gast.c_refunded")}</th><th>${th("admin.gast.c_order")}</th><th></th></tr></thead>
    <tbody>${r.orders.map(row).join("")}</tbody></table></div>` : `<p class="hint">${th("admin.gast.none")}</p>`;
}

export function bindGast() {
  const f = $("#gastform");
  if (!f) return;
  f.addEventListener("submit", (e) => e.preventDefault());      // T28: gespeichert wird über den Balken der Seite
  confirmUntested(f.querySelector('[data-k="open"]'), G);
  $("#gast-test").addEventListener("click", async () => {
    if (await guard(() => api("POST", "/gast/test"))) toast(t("admin.gast.test_ok"));
  });
  $("#gast-list").addEventListener("click", showList);
  $("#gast-tunnel-off")?.addEventListener("click", async () => {
    if (!confirm(t("admin.gast.tun_remove_q"))) return;
    if (await guard(() => api("DELETE", "/gast/tunnel"))) { toast(t("admin.gast.tun_removed")); rerender(); }
  });
  $("#gast-orders").addEventListener("click", async (e) => {
    const x = e.target.closest("[data-rretry],[data-rdone]");
    if (x) {
      const retry = "rretry" in x.dataset;
      if (!confirm(t(retry ? "admin.gast.rf_retry_q" : "admin.gast.rf_done_q"))) return;
      x.disabled = true;
      const id = retry ? x.dataset.rretry : x.dataset.rdone;
      if (await guard(() => api("POST", `/gast/refunds/${id}/${retry ? "retry" : "done"}`, {}))) toast(t("admin.gast.rf_saved"));
      showList();
      return;
    }
    const b = e.target.closest("[data-refund]");
    if (!b || !confirm(t("admin.gast.refund_q"))) return;
    b.disabled = true;
    if (await guard(() => api("POST", `/gast/orders/${b.dataset.refund}/refund`, {}))) { toast(t("admin.gast.refund_ok")); showList(); }
    else b.disabled = false;
  });
}
