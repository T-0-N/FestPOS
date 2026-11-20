/* FestPOS – Verwaltung › Seite «Bestellungen» (Kassenbuch): Storno, Zahlart, Quittungen. */
import { $, $$, api, can, chf, esc, fmtTime, guard, toast, wordOf } from "./core.js";
import { newId, orderNo, PAY_OPTS, payName, REASON_OPTS } from "./ledger.js";
import { page, RENDER } from "./shell.js";

// ------------------------------------------------------------------ Kassenbuch: Bestellungen, Storno, Zahlart


let ORD = { scope: "open", q: "", word: null }, ordSeq = 0, ordTimer = null;
RENDER.orders = async () => {
  const st = await api("GET", "/settings").catch(() => null);
  if (page !== "orders") return;                     // inzwischen andere Seite (REV-048)
  ORD.word = wordOf(st);
  $("#main").innerHTML = `<h2>${th("admin.nav.orders")}</h2>
    <div class="card">
      <div class="form ordbar">
        <div class="seg jobseg" role="group" aria-label="${th("admin.orders.scope")}">
          ${[["open", "admin.orders.scope_open"], ["all", "admin.orders.scope_all"]].map(([v, k]) =>
            `<button type="button" data-oscope="${v}" aria-pressed="${ORD.scope === v}">${th(k)}</button>`).join("")}
        </div>
        <label>${th("admin.orders.search")}<input id="ordq" type="search" value="${esc(ORD.q)}" maxlength="40"
          placeholder="${th("admin.orders.search_placeholder", { word: ORD.word.one })}" autocomplete="off"></label>
      </div>
      <p class="hint">${th(can("orders_edit") ? "admin.orders.hint_lead" : "admin.orders.hint_view")}</p>
      <div id="ordlist"><p class="hint">${th("admin.common.loading")}</p></div>
    </div>
    ${can("closing") ? `<div class="card"><div class="form">
      <button class="btn" data-dl="/export/orders.csv" data-name="bestellungen.csv">${th("admin.import.export_orders")}</button></div>
      <p class="hint">${th("admin.orders.export_hint")}</p></div>` : ""}`;
  $$("[data-oscope]").forEach((b) => b.addEventListener("click", () => {
    ORD.scope = b.dataset.oscope;
    $$("[data-oscope]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    loadOrders();
  }));
  $("#ordq").addEventListener("input", (e) => { ORD.q = e.target.value; clearTimeout(ordTimer); ordTimer = setTimeout(loadOrders, 250); });
  $("#ordlist").addEventListener("click", (e) => { const tr = e.target.closest("[data-order]"); if (tr) openOrder(+tr.dataset.order); });
  $("#ordlist").addEventListener("keydown", (e) => {
    const tr = e.target.closest("[data-order]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openOrder(+tr.dataset.order); }
  });
  await loadOrders();
};
async function loadOrders() {
  const seq = ++ordSeq;
  const r = await guard(() => api("GET", `/orders?scope=${ORD.scope}&q=${encodeURIComponent(ORD.q.trim())}&limit=500`));
  const el = $("#ordlist");
  if (!r || seq !== ordSeq || page !== "orders" || !el) return;
  const w = ORD.word || wordOf(null);
  el.innerHTML = r.orders.length ? `<div class="scroll"><table class="tbl ordtbl"><thead><tr>
      <th>${th("admin.orders.no")}</th><th>${th("admin.orders.time")}</th><th>${esc(capFirst(w.one))}</th><th>${th("admin.orders.device")}</th>
      <th class="num">${th("admin.orders.items")}</th><th class="num">${th("admin.orders.amount")}</th><th class="num">${th("admin.orders.storno")}</th>
      <th>${th("admin.orders.payment")}</th><th>${th("admin.orders.closing")}</th></tr></thead><tbody>
    ${r.orders.map((o) => `<tr class="rowlink${o.test ? " testjob" : ""}${o.total_cents && o.storno_cents >= o.total_cents ? " inactive" : ""}" data-order="${o.id}" tabindex="0">
      <td class="nowrap"><b>${orderNo(o.id)}</b>${o.test ? ` <span class="testtag">${th("admin.orders.test")}</span>` : ""}${o.no_print ? ` <span class="nbtag">${th("admin.orders.no_print")}</span>` : ""}</td>
      <td class="nowrap">${esc(fmtTime(o.created_at))}</td><td>${esc(o.table_label)}</td><td>${esc(o.device || "–")}</td>
      <td class="num">${o.items ?? ""}</td><td class="num">${chf(o.total_cents - o.storno_cents)}</td>
      <td class="num">${o.storno_cents ? `<span class="neg">−${chf(o.storno_cents)}</span>` : ""}</td>
      <td>${o.payment ? esc(payName(o.payment)) : `<span class="muted">${th("ledger.pay.none")}</span>`}</td>
      <td>${o.closing_id ? th("admin.orders.closed_in", { no: o.closing_id }) : o.test ? `<span class="muted">${th("admin.orders.test_never")}</span>`
        : `<span class="status pending">${th("admin.orders.open")}</span>`}</td></tr>`).join("")}
    </tbody></table></div>
    ${r.orders.length >= 500 ? `<p class="hint">${th("admin.orders.limited")}</p>` : ""}`
    : `<p class="hint">${th(ORD.q.trim() ? "admin.orders.none_found" : ORD.scope === "open" ? "admin.orders.none_open" : "admin.orders.none")}</p>`;
}
const capFirst = (s) => (s ? s.charAt(0).toLocaleUpperCase() + s.slice(1) : s);

// Eine Bestellung: Positionen, Stornos, Zahlart ändern und stornieren (ab Festleitung, nur vor dem Tagesabschluss)
async function openOrder(id) {
  const o = await guard(() => api("GET", `/orders/${id}`));
  if (!o) return;
  const w = ORD.word || wordOf(null);
  const edit = !o.closing_id && can("orders_edit");
  const left = (i) => i.qty - i.cancelled;
  const anyLeft = o.items.some((i) => left(i) > 0);
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog" id="ordform">
    <h3>${th("admin.orders.detail_title", { no: orderNo(o.id) })}${o.test ? ` <span class="testtag">${th("admin.orders.test")}</span>` : ""}${o.no_print ? ` <span class="nbtag">${th("admin.orders.no_print")}</span>` : ""}</h3>
    ${o.no_print ? `<p class="hint">${th("admin.orders.no_print_hint")}</p>` : ""}
    <p class="hint">${th("admin.orders.detail_meta", { time: fmtTime(o.created_at), word: capFirst(w.one), table: o.table_label, device: o.device || "–" })}
      ${o.closing_id ? `<br><b>${th("admin.orders.detail_closed", { no: o.closing_id })}</b>` : ""}</p>
    <div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.orders.item")}</th><th class="num">${th("admin.orders.qty")}</th>
      <th class="num">${th("admin.orders.price")}</th><th class="num">${th("admin.orders.col_cancelled")}</th>
      ${edit && anyLeft ? `<th class="num">${th("admin.orders.storno_now")}</th>` : ""}</tr></thead><tbody>
      ${o.items.map((i) => `<tr><td><b>${esc(i.name)}</b>${[i.options, i.note, i.delivery].filter(Boolean).length
          ? `<div class="hint" style="margin:0">${esc([i.options, i.note, i.delivery].filter(Boolean).join(", "))}</div>` : ""}</td>
        <td class="num">${i.qty}</td><td class="num">${chf(i.price_cents)}</td>
        <td class="num">${i.cancelled ? `<span class="neg">${i.cancelled}</span>` : ""}</td>
        ${edit && anyLeft ? `<td class="num">${left(i) > 0 ? `<input type="number" class="sq" data-line="${i.line_no}" data-price="${i.price_cents}"
          min="0" max="${left(i)}" value="0" aria-label="${th("admin.orders.storno_qty", { name: i.name })}">` : ""}</td>` : ""}</tr>`).join("")}
    </tbody></table></div>
    <div class="ordsum">
      <span>${th("admin.orders.total")}</span><b>${chf(o.total_cents)}</b>
      ${o.storno_cents ? `<span>${th("admin.orders.storno_total")}</span><b class="neg">−${chf(o.storno_cents)}</b>
        <span>${th("admin.orders.net")}</span><b>${chf(o.net_cents)}</b>` : ""}
    </div>
    ${receiptBox(o)}
    ${o.cancellations.length ? `<h4>${th("admin.orders.stornos")}</h4><ul class="plainlist">
      ${o.cancellations.map((c) => `<li>${esc(fmtTime(c.created_at))} – <b>−${chf(c.amount_cents)}</b> – ${esc(c.reason)}
        <span class="muted">(${esc(c.by_user || t("ledger.who.device", { name: c.by_device || "?" }))})</span></li>`).join("")}</ul>` : ""}
    <div class="form" style="margin-top:12px">
      <label>${th("admin.orders.payment")}<select id="ordpay" ${edit ? "" : "disabled"}>
        <option value="">${th("ledger.pay.none")}</option>
        ${PAY_OPTS.map(([v, k]) => `<option value="${v}" ${o.payment === v ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
    </div>
    ${edit && anyLeft ? `<fieldset class="plain stornobox"><legend>${th("admin.orders.storno_title")}</legend>
      <div class="form">
        <button type="button" class="btn sm" id="sqall">${th("admin.orders.storno_all")}</button>
        <label>${th("admin.orders.reason")}<select id="sreason"><option value="">${th("admin.orders.reason_pick")}</option>
          ${REASON_OPTS.map(([v, k]) => `<option value="${v}">${th(k)}</option>`).join("")}</select></label>
        <label>${th("admin.orders.note")}<input id="snote" maxlength="80" style="width:260px"></label>
        <button type="button" class="btn danger" id="sgo" disabled>${th("admin.orders.storno_btn")}</button>
      </div>
      <p class="hint">${th("admin.orders.storno_hint")}</p></fieldset>` : ""}
    <div class="form dlg-foot"><button type="button" class="btn" id="ordclose">${th("admin.common.close")}</button></div>
  </form>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $("#ordclose", dlg).addEventListener("click", () => dlg.close());
  const refresh = async () => { dlg.close(); await loadOrders(); openOrder(id); };
  bindReceiptBox(dlg, o, refresh);
  $("#ordpay", dlg).addEventListener("change", async (e) => {
    if (await guard(() => api("PUT", `/orders/${id}/payment`, { payment: e.target.value || null }), t("admin.orders.payment_saved"))) loadOrders();
    else e.target.value = o.payment || "";
  });
  if (edit && anyLeft) {
    const cid = newId();                          // gleiche Nummer bei Wiederholung: der Server bucht nur einmal
    const sum = () => $$(".sq", dlg).reduce((s, x) => s + Math.max(0, Math.min(+x.max, +x.value || 0)) * +x.dataset.price, 0);
    const sync = () => {
      const v = sum(), b = $("#sgo", dlg);
      b.disabled = !v || !$("#sreason", dlg).value;
      b.textContent = v ? t("admin.orders.storno_amount", { amount: chf(v) }) : t("admin.orders.storno_btn");
    };
    dlg.addEventListener("input", sync);
    dlg.addEventListener("change", sync);
    $("#sqall", dlg).addEventListener("click", () => { $$(".sq", dlg).forEach((x) => { x.value = x.max; }); sync(); });
    $("#sgo", dlg).addEventListener("click", async () => {
      const lines = $$(".sq", dlg).map((x) => ({ line_no: +x.dataset.line, qty: Math.min(+x.max, Math.max(0, Math.floor(+x.value || 0))) }))
        .filter((x) => x.qty > 0);
      if (!lines.length || !$("#sreason", dlg).value) return;
      if (!confirm(t("admin.orders.storno_confirm", { amount: chf(sum()), no: orderNo(id) }))) return;
      $("#sgo", dlg).disabled = true;
      const r = await guard(() => api("POST", `/orders/${id}/storno`, { client_uuid: cid, lines, reason: $("#sreason", dlg).value,
        note: $("#snote", dlg).value.trim() }));
      if (!r) { sync(); return; }
      toast(t(r.printer_ids?.length ? "admin.orders.storno_done_bon" : "admin.orders.storno_done", { amount: chf(r.amount_cents) }));
      refresh();
    });
  }
  dlg.showModal();
}

// Quittungen einer Bestellung: Liste (Nummer, Zeit, von wem, Kopien) und drucken – was noch nicht quittiert ist (sonst
// Kopien). Über CHF 400 mit MWST: Name und Adresse des Empfängers eintragen (steht dann auf der Quittung).
function receiptBox(o) {
  const list = o.receipts || [];
  const can_ = can("orders_edit") && o.receipt_enabled;
  if (!list.length && !can_) return "";
  return `<h4>${th("admin.receipt.list_title")}</h4>
    ${list.length ? `<ul class="plainlist">${list.map((r) => `<li><b>${r.no ? th("admin.receipt.no", { no: String(r.no).padStart(5, "0") }) : th("admin.orders.test")}</b>
      – ${chf(r.total_cents)} – ${esc(fmtTime(r.created_at))}${r.part ? ` – ${th("admin.receipt.part", { n: r.part })}` : ""}
      <span class="muted">(${esc(r.by_user || t("ledger.who.device", { name: r.by_device || "?" }))}${r.copies ? `, ${th("admin.receipt.copies", { count: r.copies })}` : ""})</span>
      ${can_ ? `<button type="button" class="btn sm" data-rcopy="${r.id}">${th("admin.receipt.copy_btn")}</button>` : ""}</li>`).join("")}</ul>` : ""}
    ${can_ ? `<div class="form" style="align-items:flex-end">
      <label>${th("admin.receipt.recipient")}<textarea id="rcpt-to" rows="2" maxlength="300" style="width:300px"
        placeholder="${th("admin.receipt.recipient_placeholder")}"></textarea></label>
      <button type="button" class="btn" id="rcpt-go">${th("admin.receipt.print_btn")}</button></div>
      <p class="hint">${th("admin.receipt.dialog_hint")}</p>` : ""}`;
}
function bindReceiptBox(dlg, o, refresh) {
  const done = (r) => {
    if (!r) return;
    const nos = (r.receipts || []).map((x) => (x.no ? String(x.no).padStart(5, "0") : "TEST")).join(", ");
    toast(t(r.copy ? "admin.receipt.copy_done" : "admin.receipt.done", { no: nos, printer: r.printer || "?" }));
    refresh();
  };
  const rid = newId();                                   // gleiche Nummer bei Wiederholung: nur eine Quittung
  $("#rcpt-go", dlg)?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    done(await guard(() => api("POST", `/orders/${o.id}/receipt`, { request_id: rid, recipient: $("#rcpt-to", dlg).value })));
    e.target.disabled = false;
  });
  dlg.querySelectorAll("[data-rcopy]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    done(await guard(() => api("POST", `/orders/${o.id}/receipt`, { request_id: newId(), copy_of: +b.dataset.rcopy })));
  }));
}
