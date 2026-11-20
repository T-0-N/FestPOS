/* FestPOS – Verwaltung › Kasse & App › Kasse › MWST (Sätze und MWST-Nummer). Der Reiter «Kasse» selbst: kassepage.js (T28). Die
   Quittung (Drucker, Kopf, Fusszeile, Gestaltung) steht unter Kasse & App › Bons & Quittung (bondesign.js). */
import { $, api, esc, fmtDate, guard, toast } from "./core.js";
import { pct, VAT_OPTS, vatName } from "./ledger.js";
import { editDialog } from "./dlg.js";

// T28: «MWST ausweisen» und die Nummer speichert der Balken der Seite (data-api="vat"); Sätze hinzufügen/löschen
// wirkt sofort (Liste). after: nach dem Neuzeichnen (Seite markiert die Felder neu).
export function renderVat(v, after) {
  const box = $("#vatbox");
  if (!box) return;
  const cur = {};                                   // heute gültiger Satz je Art: der jüngste mit «gültig ab» bis heute
  v.rates.filter((r) => r.valid_from <= v.today).forEach((r) => { if (!cur[r.code] || r.valid_from > cur[r.code].valid_from) cur[r.code] = r; });
  box.innerHTML = `<div class="form" id="vatform">
      <label class="sw"><input type="checkbox" data-k="enabled" data-api="vat" data-type="bool" data-label="admin.vat.enable" ${v.enabled ? "checked" : ""}><i aria-hidden="true"></i> ${th("admin.vat.enable")}</label>
      <label>${th("admin.vat.number")}<input data-k="number" data-api="vat" data-type="str" data-label="admin.vat.number" value="${esc(v.number)}" maxlength="40" placeholder="${th("admin.vat.number_placeholder")}" style="width:240px"></label>
    </div>
    <p class="hint">${th("admin.vat.hint")}</p>
    <div class="scroll"><table class="tbl" style="max-width:640px"><thead><tr><th>${th("admin.vat.code")}</th><th class="num">${th("admin.vat.rate")}</th>
      <th>${th("admin.vat.valid_from")}</th><th></th></tr></thead><tbody>
      ${v.rates.map((r) => `<tr class="${r.valid_from > v.today || cur[r.code] === r ? "" : "inactive"}">
        <td>${esc(vatName(r.code))}</td><td class="num">${esc(pct(r.rate_bp))}</td><td>${esc(fmtDate(r.valid_from))}${r.valid_from > v.today
          ? ` <span class="status pending">${th("admin.vat.future")}</span>` : cur[r.code] === r ? ` <span class="status ok">${th("admin.vat.current")}</span>` : ""}</td>
        <td>${r.valid_from > v.today ? `<button type="button" class="btn sm danger" data-vatdel="${r.id}">${th("admin.common.delete")}</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="form" style="margin-top:10px"><button type="button" class="btn" id="vat-add">${th("admin.vat.add_btn")}</button></div>`;
  // T28 Stufe 2: neuer Satz im Fenster (Liste wirkt sofort, wie bisher)
  $("#vat-add").addEventListener("click", () => editDialog({
    title: th("admin.vat.add"),
    saveLabel: th("admin.vat.add"),
    body: `<div class="form" id="vatadd">
      <label>${th("admin.vat.code")}<select name="code">${VAT_OPTS.map(([c, k]) => `<option value="${c}">${th(k)}</option>`).join("")}</select></label>
      <label>${th("admin.vat.rate_pct")}<input name="rate" inputmode="decimal" placeholder="8.1" style="width:90px" required></label>
      <label>${th("admin.vat.valid_from")}<input name="from" type="date" required></label></div>`,
    onSave: async (f) => {
      const raw = f.rate.value.trim().replace(",", ".").replace("%", "");
      if (!/^\d{1,2}(\.\d{1,2})?$/.test(raw)) { toast(t("admin.vat.rate_bad"), true); return false; }
      const r = await guard(() => api("POST", "/vat/rates", { code: f.code.value, rate_bp: Math.round(parseFloat(raw) * 100),
        valid_from: f.from.value }), t("admin.vat.added"));
      if (r) renderVat(r, after);
      return !!r;
    },
  }));
  box.querySelectorAll("[data-vatdel]").forEach((b) => b.addEventListener("click", async () => {
    const r = await guard(() => api("DELETE", `/vat/rates/${b.dataset.vatdel}`), t("admin.common.deleted"));
    if (r) renderVat(r, after);
  }));
  after?.();
}
