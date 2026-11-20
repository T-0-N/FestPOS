/* FestPOS – Verwaltung › Seite «Tische» und Tischkarten. */
import { $, api, can, download, esc, guard, toast, wordOf } from "./core.js";
import { page, RENDER } from "./shell.js";
import { editDialog } from "./dlg.js";

// Tischkarten nützen nur mit «Tisch per QR-Code scannen» (Punkt 4): ist es aus, vor dem Drucken fragen,
// ob es jetzt eingeschaltet werden soll. -> true = drucken, false = abgebrochen. Einschalten darf nur, wer die
// Einstellungen ändern darf (Admin); die anderen sehen den Hinweis und drucken auf Wunsch trotzdem.
function askStaffQr(st) {
  if (st?.staff_qr === "1") return Promise.resolve(true);
  if (!can("admin")) return Promise.resolve(confirm(t("admin.tables.qr_off_ask_lead")));
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const dlg = editDialog({
      title: th("admin.tables.qr_off_title"),
      body: `<p>${th("admin.tables.qr_off_text")}</p>`,
      saveLabel: th("admin.tables.qr_off_on"),
      extra: `<button type="button" class="btn" data-qr-skip>${th("admin.tables.qr_off_skip")}</button>`,
      onOpen: (d) => d.querySelector("[data-qr-skip]").addEventListener("click", () => { finish(true); d.close(); }),
      onSave: async () => {
        const ok = await guard(() => api("PUT", "/settings", { staff_qr: true }), t("admin.tables.qr_off_done"));
        if (ok) { if (st) st.staff_qr = "1"; finish(true); }
        return !!ok;
      },
    });
    dlg.addEventListener("close", () => finish(false));
  });
}
import { planCard, startPlan } from "./plan.js";

// ------------------------------------------------------------------ Tische
RENDER.tables = async () => {
  const [tables, printers, st] = await Promise.all([guard(() => api("GET", "/tables")), guard(() => api("GET", "/printers")),
    guard(() => api("GET", "/settings"))]);
  if (!tables) return;
  if (page !== "tables") return;                     // inzwischen andere Seite (REV-048)
  const w = wordOf(st), staffQr = st?.staff_qr === "1";
  $("#main").innerHTML = `<h2>${th("admin.nav.tables")}</h2>
    <div class="card"><h3>${th("admin.tables.choice_title")}</h3>
      ${(() => { const mode = st?.tables_mode || (st?.tables_strict === "1" ? "strict" : "free");
        return [["strict", "admin.tables.mode_strict", "admin.tables.mode_strict_hint"],
                ["warn", "admin.tables.mode_warn", "admin.tables.mode_warn_hint"],
                ["free", "admin.tables.mode_free", "admin.tables.mode_free_hint"]]
          .map(([v, l, h]) => `<label class="check"><input type="radio" name="tblmode" value="${v}" ${mode === v ? "checked" : ""}> <b>${th(l)}</b>&nbsp;– ${th(h)}</label>`).join(""); })()}
      <p class="hint">${th("admin.tables.mode_hint")}</p>
    </div>
    ${planCard()}
    <div class="card"><h3>${th("admin.tables.create")}</h3>
      <form class="form" id="newtables">
        <label>${th("admin.tables.from")}<input name="from" type="number" min="0" style="width:90px" placeholder="1"></label>
        <label>${th("admin.tables.to")}<input name="to" type="number" min="0" style="width:90px" placeholder="30"></label>
        <label>${th("admin.tables.names")}<textarea name="labels" rows="2" style="width:220px" placeholder="${th("admin.tables.names_placeholder")}"></textarea></label>
        <label class="check"><input type="checkbox" name="replace"> ${th("admin.tables.replace")}</label>
        <button class="btn pri">${th("admin.tables.create_btn")}</button>
      </form>
      <p class="hint">${th("admin.tables.create_hint")}</p>
    </div>
    <div class="card"><h3>${th("admin.tables.count", { count: tables.length, many: w.many })}</h3>
      <div class="chips" id="tlist">${tables.map((x) => `<span>${esc(x.label)}<button data-del="${x.id}" aria-label="${th("admin.tables.delete_one", { label: x.label })}">×</button></span>`).join("") || `<p class="hint">${th("admin.tables.none")}</p>`}</div>
    </div>
    <div class="card"><h3>${th("admin.tables.pdf")}</h3>
      <p class="hint">${th("admin.tables.pdf_text")}</p>
      <p class="hint">${th(staffQr ? "admin.tables.staff_qr_on" : "admin.tables.staff_qr_off")}</p>
      <form class="form" id="qrpdf">
        <label>${th("admin.tables.only")}<input name="only" placeholder="${th("admin.tables.only_placeholder")}" style="width:220px"></label>
        <button class="btn pri">${th("admin.tables.pdf_btn")}</button>
      </form>
    </div>
    <div class="card"><h3>${th("admin.tables.bon_cards")}</h3>
      <p class="hint">${th("admin.tables.bon_cards_text")}</p>
      <form class="form" id="qr">
        <label>${th("admin.printers.printer")}<select name="printer">${(printers || []).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></label>
        <label>${th("admin.tables.only")}<input name="only" placeholder="${th("admin.tables.only_placeholder")}" style="width:220px"></label>
        <button class="btn pri" ${printers?.length ? "" : "disabled"}>${th("admin.tables.bon_cards_btn")}</button>
      </form>
    </div>`;
  startPlan(w);
  document.querySelectorAll('input[name="tblmode"]').forEach((el) => el.addEventListener("change", (e) =>
    guard(() => api("PUT", "/settings", { tables_mode: e.target.value }), t("admin.common.saved"))));
  $("#newtables").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.replace.checked && !confirm(t("admin.tables.replace_confirm"))) return;
    const body = { labels: f.labels.value.split("\n").map((s) => s.trim()).filter(Boolean), replace: f.replace.checked };
    if (f.from.value !== "" && f.to.value !== "") { body.range_from = +f.from.value; body.range_to = +f.to.value; }
    if (await guard(() => api("POST", "/tables", body), t("admin.tables.saved"))) RENDER.tables();
  });
  $("#tlist").addEventListener("click", async (e) => {
    const id = e.target.dataset.del;
    if (id && await guard(() => api("DELETE", `/tables/${id}`))) e.target.parentElement.remove();
  });
  $("#qrpdf").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!await askStaffQr(st)) return;
    const only = e.target.only.value.split(",").map((s) => s.trim()).filter(Boolean).join(",");
    download("/tables/qr.pdf" + (only ? "?labels=" + encodeURIComponent(only) : ""), t("admin.tables.pdf_file"));
  });
  $("#qr").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!await askStaffQr(st)) return;
    const labels = e.target.only.value.split(",").map((s) => s.trim()).filter(Boolean);
    const r = await guard(() => api("POST", "/tables/print-qr", { printer_id: +e.target.printer.value, labels }));
    if (r) toast(t("admin.tables.bon_cards_queued", { count: r.queued }));
  });
};
