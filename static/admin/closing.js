/* FestPOS – Verwaltung › Seite «Tagesabschluss»: Kassensturz, PDF/CSV, Prüfung, Protokoll. */
import { $, api, chf, esc, fmtTime, guard, parsePrice, toast } from "./core.js";
import { PAY_OPTS, payName, pct, vatName, whoName } from "./ledger.js";
import { page, RENDER, show } from "./shell.js";

// ------------------------------------------------------------------ Tagesabschluss (Kassensturz, PDF/CSV, Prüfung, Protokoll)
let AUDIT_Q = "", auditTimer = null;
RENDER.closing = async () => {
  const [o, list] = await Promise.all([guard(() => api("GET", "/closings/open")), guard(() => api("GET", "/closings"))]);
  if (!o || !list || page !== "closing") return;
  const s = o.summary, bp = s.by_payment || {};
  const cr = s.corrections || { count: 0 }, lp = s.late_payments || { count: 0 };
  const hasOpen = s.orders > 0 || s.stornos > 0 || cr.count > 0 || lp.count > 0;   // T45: auch nur Korrekturen früherer Tage
  const pw = s.payrexx_waiting || [], pl = s.payrexx_late || [];
  const chk = s.check || {};
  // Trinkgeld (app/tips.py): Karte, TWINT und Andere stehen MIT Trinkgeld auf deren Abrechnung -> so vorausfüllen
  const tp = s.tips || { total: 0, by_payment: {}, by_device: [] }, tbp = tp.by_payment || {};
  const tipsShown = !!tp.total || !!s.tips_enabled;
  const tipCash = (tbp.cash || 0) + (tbp.none || 0);
  const tipOf = (dev) => (tp.by_device || []).find((d) => d.device === dev)?.amount || 0;
  const prefill = (k) => { const v = (bp[k] || 0) + (tbp[k] || 0); return v ? chf(v).replace(/'/g, "") : ""; };
  $("#main").innerHTML = `<h2>${th("admin.nav.closing")}</h2>
    ${s.open_tabs ? `<div class="warnbox"><p>${th("admin.closing.open_tabs", { count: s.open_tabs })}</p>
      <button class="btn sm" id="goopentabs">${th("admin.nav.opentabs")}</button></div>` : ""}
    ${pw.length ? `<div class="warnbox" id="prxwaiting"><p>${th("admin.closing.payrexx_waiting", { count: pw.length })}</p>
      <ul>${pw.map((p) => `<li>${th("admin.closing.pw_line", { table: p.table_label || "–", amount: chf(p.amount_cents),
        device: p.device || "?", time: fmtTime(p.created_at) })}
        <button class="btn sm" data-prxcancel="${p.id}">${th("admin.closing.pw_cancel")}</button></li>`).join("")}</ul></div>` : ""}
    ${pl.length ? `<div class="warnbox" id="prxlate"><p>${th("admin.closing.late_title", { count: pl.length })}</p>
      <p class="hint">${th("admin.closing.late_hint")}</p>
      <ul>${pl.map((p) => `<li>${th("admin.closing.late_line", { order: p.order_id, table: p.table_label || "–",
        amount: chf(p.amount_cents), closing: p.closing_id, time: fmtTime(p.paid_at) })}
        <button class="btn sm" data-late="refund" data-id="${p.id}">${th("admin.closing.late_refund")}</button>
        <button class="btn sm" data-late="book" data-id="${p.id}">${th("admin.closing.late_book")}</button></li>`).join("")}</ul></div>` : ""}
    <div class="card"><h3>${th("admin.closing.open_title")}</h3>
      ${hasOpen ? `<p class="hint">${th("admin.closing.open_hint", { from: fmtTime(s.period_from), to: fmtTime(s.period_to) })}</p>
      <div class="cols kpis">
        <div class="kpi"><b>${s.orders}</b><span>${th("admin.closing.orders", { first: s.first_order ?? "–", last: s.last_order ?? "–" })}</span></div>
        <div class="kpi"><b>${chf(s.gross)}</b><span>${th("admin.closing.gross")}</span></div>
        <div class="kpi"><b class="${s.storno ? "neg" : ""}">${s.storno ? "−" : ""}${chf(s.storno)}</b><span>${th("admin.closing.storno", { count: s.stornos })}</span></div>
        <div class="kpi"><b>${chf(s.net)}</b><span>${th("admin.closing.net", { cur: o.currency })}</span></div>
      </div>
      <div class="cols closecols">
        <div><h4>${th("ledger.pdf.by_payment")}</h4><table class="tbl"><tbody>
          ${[...PAY_OPTS.map(([k]) => k), "none"].filter((k) => bp[k] != null).map((k) =>
            `<tr><td>${esc(payName(k))}</td><td class="num">${chf(bp[k])}</td></tr>`).join("")}
        </tbody></table>
        ${o.payment_mode === "off" ? `<p class="hint">${th("admin.closing.no_payment_mode")}</p>` : ""}</div>
        <div><h4>${th("ledger.pdf.by_device")}</h4><table class="tbl"><thead><tr><th>${th("ledger.pdf.device")}</th>
          <th class="num">${th("ledger.pdf.count")}</th><th class="num">${th("ledger.pdf.storno")}</th><th class="num">${th("ledger.pdf.net")}</th>
          ${tp.total ? `<th class="num">${th("admin.closing.tip_col")}</th>` : ""}</tr></thead><tbody>
          ${s.by_device.map((d) => `<tr><td>${esc(d.device)}</td><td class="num">${d.orders}</td>
            <td class="num">${d.storno ? `−${chf(d.storno)}` : ""}</td><td class="num">${chf(d.net)}</td>
            ${tp.total ? `<td class="num">${tipOf(d.device) ? chf(tipOf(d.device)) : ""}</td>` : ""}</tr>`).join("")}
        </tbody></table></div>
        ${o.vat_enabled && s.by_vat.length ? `<div><h4>${th("ledger.pdf.vat")}</h4><table class="tbl"><thead><tr><th>${th("ledger.pdf.vat_rate")}</th>
          <th class="num">${th("ledger.pdf.vat_gross")}</th><th class="num">${th("ledger.pdf.vat_amount")}</th></tr></thead><tbody>
          ${s.by_vat.map((v) => `<tr><td>${esc(vatName(v.code))} ${esc(pct(v.bp))}</td><td class="num">${chf(v.gross)}</td><td class="num">${chf(v.vat)}</td></tr>`).join("")}
        </tbody></table></div>` : ""}
      </div>
      ${s.receipts?.count || s.receipts?.copies ? `<p class="hint">${th("ledger.pdf.receipts_line", { count: s.receipts.count,
        first: s.receipts.first ?? "–", last: s.receipts.last ?? "–", amount: chf(s.receipts.amount), copies: s.receipts.copies,
        gaps: (s.receipts.gaps || []).join(", ") || "–" })}</p>` : ""}
      ${chk.bad?.length ? `<p class="err">${th("admin.closing.check_bad", { list: chk.bad.join(", ") })}</p>` : ""}
      ${chk.other_key ? `<p class="hint">${th("admin.closing.check_other_key", { count: chk.other_key })}</p>` : ""}
      ${chk.gaps?.length ? `<p class="hint">${th("ledger.pdf.check_gaps", { list: chk.gaps.join(", ") })}</p>` : ""}`
      : `<p class="hint">${th("admin.closing.nothing_open")}</p>`}
    </div>
    ${hasOpen && tipsShown ? `<div class="card" id="tipcard"><h3>${th("ledger.pdf.tips")}</h3>
      ${tp.total ? `<table class="tbl" style="max-width:640px"><tbody>
        ${[...PAY_OPTS.map(([k]) => k), "none"].filter((k) => tbp[k]).map((k) => `<tr><td>${esc(t("admin.closing.tip_kind", { kind: payName(k) }))}</td>
          <td class="num">${chf(tbp[k])}</td><td class="hint">${th(k === "cash" || k === "none" ? "admin.closing.tip_in_cash"
            : k === "payrexx" ? "admin.closing.tip_payrexx" : "admin.closing.tip_statement")}</td></tr>`).join("")}
        <tr><td><b>${th("admin.closing.tip_total")}</b></td><td class="num"><b>${chf(tp.total)}</b></td><td class="hint">${th("admin.closing.tip_no_vat")}</td></tr>
      </tbody></table>` : `<p class="hint">${th("admin.closing.tip_none")}</p>`}
      <p class="hint">${th("admin.closing.tip_formula")}</p></div>` : ""}
    ${hasOpen ? `<form class="card" id="cashform"><h3>${th("admin.closing.cash_title")}</h3>
      <p class="hint">${th("admin.closing.cash_hint")}</p>
      <div class="form">
        <label>${th("ledger.pdf.float")}<input name="float" inputmode="decimal" placeholder="0.00" style="width:130px"></label>
        <label>${th("ledger.pdf.counted")}<input name="counted" inputmode="decimal" placeholder="0.00" style="width:130px"></label>
        <label>${th("ledger.pdf.card")}<input name="card" inputmode="decimal" value="${prefill("card")}" placeholder="0.00" style="width:130px"></label>
        <label>${th("ledger.pdf.twint")}<input name="twint" inputmode="decimal" value="${prefill("twint")}" placeholder="0.00" style="width:130px"></label>
        <label>${th("ledger.pdf.other")}<input name="other" inputmode="decimal" value="${prefill("other")}" placeholder="0.00" style="width:130px"></label>
        ${tipsShown ? `<label>${th("ledger.pdf.tip_out")}<input name="tipout" inputmode="decimal" placeholder="0.00" style="width:130px"></label>` : ""}
      </div>
      ${s.payrexx?.count || cr.count || lp.count ? `<p class="hint" id="prxline">${th("ledger.pdf.payrexx", { count: s.payrexx.count })}: <b>${chf(s.payrexx.cents)}</b></p>` : ""}
      ${s.payrexx?.manual?.count ? `<div class="warnbox" id="prxmanual"><p>${th("ledger.pdf.payrexx_manual", { count: s.payrexx.manual.count })}: <b>${chf(s.payrexx.manual.cents)}</b></p></div>` : ""}
      ${lp.count ? `<p class="hint" id="lateline">${th("ledger.pdf.late_payments", { count: lp.count, tip: chf(lp.tip) })}: <b>+${chf(lp.cents)}</b></p>` : ""}
      ${cr.count ? `<p class="hint" id="corrline">${th("ledger.pdf.corrections", { count: cr.count, tip: chf(cr.tip) })}: <b>−${chf(cr.cents)}</b></p>` : ""}
      <div class="form" style="margin-top:10px"><label class="grow">${th("admin.closing.note")}<input name="note" maxlength="300"></label></div>
      <div class="cashres" id="cashres"></div>
      <div class="form" style="margin-top:12px"><button class="btn pri">${th("admin.closing.create")}</button></div>
    </form>` : ""}
    <div class="card"><h3>${th("admin.closing.list_title")}</h3>
      ${list.closings.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.closing.no")}</th><th>${th("ledger.pdf.created")}</th>
        <th>${th("admin.closing.by")}</th><th>${th("ledger.pdf.period")}</th><th class="num">${th("ledger.pdf.count")}</th>
        <th class="num">${th("ledger.pdf.net")}</th><th class="num">${th("admin.closing.difference")}</th><th></th></tr></thead><tbody>
        ${list.closings.map((c) => `<tr data-closing="${c.id}"><td><b>${c.id}</b></td><td class="nowrap">${esc(fmtTime(c.created_at))}</td>
          <td>${esc(whoName(c.by_user))}</td><td class="nowrap">${esc(fmtTime(c.period_from))} – ${esc(fmtTime(c.period_to))}</td>
          <td class="num">${c.orders}</td><td class="num">${chf(c.net_cents)}</td>
          <td class="num">${c.difference_cents == null ? "–" : `<span class="${c.difference_cents ? "neg" : ""}">${c.difference_cents > 0 ? "+" : ""}${chf(c.difference_cents)}</span>`}</td>
          <td class="acts"><button class="btn sm" data-dl="/closings/${c.id}/pdf" data-name="abschluss-${String(c.id).padStart(4, "0")}.pdf">${th("admin.closing.pdf")}</button>
            <button class="btn sm" data-dl="/closings/${c.id}/csv" data-name="abschluss-${String(c.id).padStart(4, "0")}.csv">${th("admin.closing.csv")}</button>
            <button class="btn sm" data-verify="${c.id}">${th("admin.closing.verify")}</button><div class="vres"></div></td></tr>`).join("")}
      </tbody></table></div>
      <p class="hint">${th("admin.closing.list_hint")}</p>` : `<p class="hint">${th("admin.closing.none")}</p>`}
    </div>`;
  const f = $("#cashform");
  if (f) {
    const cents = (name) => { const v = f[name]?.value.trim(); return v ? parsePrice(v) : null; };
    const calc = () => {
      const bad = ["float", "counted", "card", "twint", "other", "tipout"].filter((k) => f[k]?.value.trim() && cents(k) == null);
      const expected = (cents("float") || 0) + s.net + (tp.total || 0) - (cents("card") || 0) - (cents("twint") || 0) - (cents("other") || 0)
        - (s.payrexx?.cents || 0) - (cents("tipout") || 0);   // T13: Payrexx ist genau bekannt; Trinkgeld: app/tips.py
      const counted = cents("counted");
      const diff = counted == null ? null : counted - expected;
      $("#cashres").innerHTML = bad.length ? `<p class="err">${th("admin.closing.amount_unreadable")}</p>`
        : `<div><span>${th("ledger.pdf.expected")}</span><b>${chf(expected)}</b></div>
          ${diff == null ? `<div><span>${th("ledger.pdf.difference")}</span><b class="muted">${th("admin.closing.enter_counted")}</b></div>`
            : `<div><span>${th("ledger.pdf.difference")}</span><b class="${diff === 0 ? "pos" : "neg"}">${diff > 0 ? "+" : ""}${chf(diff)}</b></div>`}
          ${tipCash ? `<div><span>${th("admin.closing.tip_cash_in")}</span><b>${chf(tipCash - (cents("tipout") || 0))}</b></div>` : ""}`;
      return !bad.length;
    };
    f.addEventListener("input", calc); calc();
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!calc()) return toast(t("admin.closing.amount_unreadable"), true);
      if (cents("counted") == null && !confirm(t("admin.closing.confirm_no_count"))) return;
      if (!confirm(t("admin.closing.confirm", { count: s.orders, amount: chf(s.net) }))) return;
      const btn = f.querySelector("button.pri"); btn.disabled = true;
      const r = await guard(() => api("POST", "/closings", { float_cents: cents("float"), counted_cents: cents("counted"),
        card_cents: cents("card"), twint_cents: cents("twint"), other_cents: cents("other"), tip_out_cents: cents("tipout"),
        note: f.note.value.trim() }));
      if (!r) { btn.disabled = false; return; }
      toast(t("admin.closing.created", { no: r.id }));
      await RENDER.closing();
    });
  }
  $("#goopentabs")?.addEventListener("click", () => show("opentabs"));      // offene Tische zuerst abrechnen
  $("#prxwaiting")?.addEventListener("click", async (e) => {                // T45: offene Kartenzahlung abbrechen
    const b = e.target.closest("[data-prxcancel]");
    if (!b || !confirm(t("admin.closing.pw_cancel_q"))) return;
    b.disabled = true;
    if (await guard(() => api("POST", `/payrexx/payments/${b.dataset.prxcancel}/cancel`, {}))) RENDER.closing();
    else b.disabled = false;
  });
  $("#prxlate")?.addEventListener("click", async (e) => {                   // T46: Gast bar bezahlt oder nicht?
    const b = e.target.closest("[data-late]");
    if (!b || !confirm(t(b.dataset.late === "refund" ? "admin.closing.late_refund_q" : "admin.closing.late_book_q"))) return;
    b.disabled = true;
    if (await guard(() => api("POST", `/payrexx/payments/${b.dataset.id}/late/${b.dataset.late}`, {}))) RENDER.closing();
    else b.disabled = false;
  });
};

// T20: Protokoll als eigene Seite (Betrieb › Protokoll, ab Festleitung) – betrifft Geräte, Einstellungen, Uhrzeit,
// Sicherung, Testmodus …, nicht nur das Kassenbuch. Früher Reiter von Tagesabschluss (#closing/audit führt hierher).
RENDER.audit = async () => {
  $("#main").innerHTML = `<h2>${th("admin.nav.audit")}</h2>
    <div class="card"><p class="hint">${th("admin.audit.hint")}</p>
      <div class="form"><label>${th("admin.audit.search")}<input id="auditq" type="search" value="${esc(AUDIT_Q)}" maxlength="60" autocomplete="off"></label></div>
      <div id="auditlist"><p class="hint">${th("admin.common.loading")}</p></div>
    </div>`;
  $("#auditq").addEventListener("input", (e) => { AUDIT_Q = e.target.value; clearTimeout(auditTimer); auditTimer = setTimeout(loadAudit, 300); });
  loadAudit();
};

document.addEventListener("click", async (e) => {
  const v = e.target.closest("[data-verify]");
  if (!v) return;
  v.disabled = true;
  const r = await guard(() => api("GET", `/closings/${v.dataset.verify}/verify`));
  v.disabled = false;
  if (!r) return;
  const out = v.parentElement.querySelector(".vres");
  // mit einem früheren Schlüssel: nicht falsch, nur nicht prüfbar; ältere Art ohne Schlüssel: stimmt, sichert aber weniger
  const other = r.state === "other_key";
  out.className = `vres status ${r.ok && r.state !== "legacy" ? "ok" : other || r.ok ? "pending" : "bad"}`;
  out.textContent = t(other ? "admin.closing.verify_other_key" : !r.hash_ok ? "admin.closing.verify_bad"
    : !r.chain_ok ? "admin.closing.verify_chain" : r.state === "legacy" ? "admin.closing.verify_legacy" : "admin.closing.verify_ok");
});

// Protokoll: wer hat wann was geändert (Preise, Einstellungen, Stornos, Abschlüsse …)
const AUDIT_ACTIONS = {
  settings: "admin.audit.a_settings", storno: "admin.audit.a_storno", payment: "admin.audit.a_payment", closing: "admin.audit.a_closing",
  product_create: "admin.audit.a_product_create", product_update: "admin.audit.a_product_update", product_delete: "admin.audit.a_product_delete",
  product_vat: "admin.audit.a_product_vat", category_create: "admin.audit.a_category_create", category_update: "admin.audit.a_category_update",
  category_delete: "admin.audit.a_category_delete", import: "admin.audit.a_import", restore: "admin.audit.a_restore",
  reset_orders: "admin.audit.a_reset_orders", test_orders_delete: "admin.audit.a_test_orders_delete", vat: "admin.audit.a_vat",
  products_order: "admin.audit.a_products_order", catalog_sort_az: "admin.audit.a_catalog_sort_az", categories_order: "admin.audit.a_categories_order",
  vat_rate: "admin.audit.a_vat_rate", vat_rate_delete: "admin.audit.a_vat_rate_delete", device_role: "admin.audit.a_device_role",
  device_cert: "admin.audit.a_device_cert", device_revoke: "admin.audit.a_device_revoke", device_unrevoke: "admin.audit.a_device_unrevoke",
  device_phone: "admin.audit.a_device_phone", device_rollout: "admin.audit.a_device_rollout", device_remove: "admin.audit.a_device_remove",
  device_cert_auto_failed: "admin.audit.a_device_cert_auto_failed", device_mac_change: "admin.audit.a_device_mac_change",
  device_mac_conflict: "admin.audit.a_device_mac_conflict", device_mac_keep: "admin.audit.a_device_mac_keep",
  backup_download: "admin.audit.a_backup_download", backup_password_show: "admin.audit.a_backup_password_show",
  backup_password_printed: "admin.audit.a_backup_password_printed", backup_password_new: "admin.audit.a_backup_password_new",
  backup_password_bon: "admin.audit.a_backup_password_bon", receipt_gap: "admin.audit.a_receipt_gap",
  restore_keys: "admin.audit.a_restore_keys", restore_check_done: "admin.audit.a_restore_check_done",
  kartenpaket: "admin.audit.a_kartenpaket", cluster_expand: "admin.audit.a_cluster_expand",
  card_write: "admin.audit.a_card_write", card_written: "admin.audit.a_card_written", card_failed: "admin.audit.a_card_failed",
  card_cancelled: "admin.audit.a_card_cancelled", device_pin: "admin.audit.a_device_pin",
  payrexx_paid: "admin.audit.a_payrexx_paid", payrexx_settings: "admin.audit.a_payrexx_settings",
  payrexx_mismatch: "admin.audit.a_payrexx_mismatch", payrexx_tap_apk: "admin.audit.a_payrexx_tap_apk",
  payrexx_overpaid: "admin.audit.a_payrexx_overpaid", payrexx_overpaid_late: "admin.audit.a_payrexx_overpaid_late",
  payrexx_late_open: "admin.audit.a_payrexx_late_open", payrexx_late_refund: "admin.audit.a_payrexx_late_refund",
  payrexx_late_book: "admin.audit.a_payrexx_late_book",
  payrexx_unclear: "admin.audit.a_payrexx_unclear",
  payrexx_double: "admin.audit.a_payrexx_double",
  payrexx_tap_install: "admin.audit.a_payrexx_tap_install",
  gast_paid: "admin.audit.a_gast_paid", gast_refund: "admin.audit.a_gast_refund",
  gast_refund_failed: "admin.audit.a_gast_refund_failed", gast_settings: "admin.audit.a_gast_settings",
  gast_refund_unclear: "admin.audit.a_gast_refund_unclear", gast_refund_retry: "admin.audit.a_gast_refund_retry",
  gast_refund_done: "admin.audit.a_gast_refund_done", gast_paid_late: "admin.audit.a_gast_paid_late",
  payrexx_cancel_admin: "admin.audit.a_payrexx_cancel_admin",
  gast_unresolved: "admin.audit.a_gast_unresolved", order_rejected: "admin.audit.a_order_rejected",
  apk_from_stick: "admin.audit.a_apk_from_stick", fully_apk_upload: "admin.audit.a_fully_apk_upload",
  fully_apk_push: "admin.audit.a_fully_apk_push", fully_apk_push_one: "admin.audit.a_fully_apk_push_one",
  fully_apk_ok: "admin.audit.a_fully_apk_ok",
  product_stock: "admin.audit.a_product_stock", product_soldout: "admin.audit.a_product_soldout",
  test_on: "admin.audit.a_test_on", test_off: "admin.audit.a_test_off", test_exit: "admin.audit.a_test_exit",
  user_create: "admin.audit.a_user_create", user_update: "admin.audit.a_user_update", user_delete: "admin.audit.a_user_delete",
  own_password: "admin.audit.a_own_password", uplink: "admin.audit.a_uplink", uplink_trust: "admin.audit.a_uplink_trust",
  uplink_active: "admin.audit.a_uplink_active", receipt: "admin.audit.a_receipt", receipt_copy: "admin.audit.a_receipt_copy",
  update_upload: "admin.audit.a_update_upload", update_start: "admin.audit.a_update_start", update_cancel: "admin.audit.a_update_cancel",
  update_retry: "admin.audit.a_update_retry", update_rollback: "admin.audit.a_update_rollback", update_usb: "admin.audit.a_update_usb",
  update_pi: "admin.audit.a_update_pi", update_net: "admin.audit.a_update_net", update_net_config: "admin.audit.a_update_net_config",
  time_set: "admin.audit.a_time_set", time_confirmed: "admin.audit.a_time_confirmed", rtc_start: "admin.audit.a_rtc_start",
  phone_ntp: "admin.audit.a_phone_ntp", printer_net: "admin.audit.a_printer_net",
};
const MONEY_KEYS = /^(amount|net|difference|price_cents|total|gross)$/;
function auditDetails(d) {
  const cut = (x) => (x.length > 90 ? `${x.slice(0, 89)}…` : x);
  const val = (k, v, deep = 0) => {
    if (v == null || v === "") return "–";
    if (typeof v === "number" && MONEY_KEYS.test(k)) return chf(v);
    if (Array.isArray(v)) return v.every((x) => typeof x !== "object") ? cut(v.join(", ")) : `${v.length} ×`;
    if (typeof v === "object") return deep ? "…" : cut(Object.entries(v).map(([a, b]) => `${a}: ${val(a, b, 1)}`).join(", "));
    return cut(String(v));
  };
  return Object.entries(d || {}).map(([k, v]) => `<span class="kv"><i>${esc(k)}</i> ${esc(val(k, v))}</span>`).join(" ");
}
async function loadAudit() {
  const r = await guard(() => api("GET", `/audit?q=${encodeURIComponent(AUDIT_Q.trim())}&limit=300`));
  const el = $("#auditlist");
  if (!r || !el || page !== "audit") return;
  el.innerHTML = r.entries.length ? `<div class="scroll"><table class="tbl audit"><thead><tr><th>${th("admin.audit.at")}</th>
      <th>${th("admin.audit.who")}</th><th>${th("admin.audit.action")}</th><th>${th("admin.audit.details")}</th></tr></thead><tbody>
    ${r.entries.map((x) => `<tr><td class="nowrap">${esc(fmtTime(x.at))}</td><td>${esc(whoName(x.actor))}</td>
      <td class="nowrap">${esc(AUDIT_ACTIONS[x.action] ? t(AUDIT_ACTIONS[x.action]) : x.action)}</td><td class="det" translate="no">${auditDetails(x.details)}</td></tr>`).join("")}
  </tbody></table></div>` : `<p class="hint">${th("admin.audit.none")}</p>`;
}
