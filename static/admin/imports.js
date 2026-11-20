/* FestPOS – Verwaltung › Produkte › Import & Export (CSV, Bilder). */
import { $, api, chf, esc, guard, toast } from "./core.js";
import { RENDER } from "./shell.js";

// ------------------------------------------------------------------ Import & Export
let lastCsv = null;
export function importCards() {
  return `<div class="card" data-in="import"><h3>${th("admin.import.csv_title")}</h3>
      <p class="hint">${th("admin.import.csv_hint")} <a href="#" data-dl="/import/template.csv" data-name="preisliste-vorlage.csv">${th("admin.import.template")}</a></p>
      <form class="form" id="csv">
        <label>${th("admin.import.csv_file")}<input type="file" name="file" accept=".csv,text/csv" required></label>
        <label class="check"><input type="checkbox" name="deact"> ${th("admin.import.deactivate")}</label>
        <button class="btn pri">${th("admin.import.preview")}</button>
      </form>
      <div id="preview"></div>
    </div>
    <div class="card" data-in="import"><h3>${th("admin.import.zip_title")}</h3>
      <p class="hint">${th("admin.import.zip_hint")}</p>
      <form class="form" id="zip"><label>${th("admin.import.zip_file")}<input type="file" name="file" accept=".zip" required></label><button class="btn pri">${th("admin.import.upload")}</button></form>
      <div id="zipout"></div>
    </div>
    <div class="card" data-in="import"><h3>${th("admin.import.export")}</h3>
      <div class="form">
        <button class="btn" data-dl="/export/products.csv" data-name="preisliste.csv">${th("admin.import.export_products")}</button>
      </div>
      <p class="hint">${th("admin.import.export_hint")}</p>
    </div>`;
}
export function bindImport() {
  $("#csv").addEventListener("submit", async (e) => {
    e.preventDefault();
    lastCsv = { file: e.target.file.files[0], deact: e.target.deact.checked };
    await csvPreview(false);
  });
  $("#zip").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(); fd.append("file", e.target.file.files[0]);
    const r = await guard(() => api("POST", "/import/images", fd));
    if (!r) return;
    await RENDER.products();                          // Bilder in der Preisliste gleich sichtbar
    const el = $("#zipout");
    if (el) el.innerHTML = `<p class="okmsg">${th("admin.import.zip_matched", { count: r.matched.length })}</p>
      ${r.unmatched.length ? `<p class="err">${th("admin.import.zip_unmatched", { list: r.unmatched.join(", ") })}</p>` : ""}
      ${r.failed?.length ? `<p class="err">${th("admin.import.zip_failed", { list: r.failed.join(", ") })}</p>` : ""}`;
  });
};
async function csvPreview(commit) {
  const fd = new FormData(); fd.append("file", lastCsv.file);
  const r = await guard(() => api("POST", `/import/products?commit=${commit}&deactivate_missing=${lastCsv.deact}`, fd));
  if (!r) return;
  const s = r.summary, out = $("#preview");
  if (r.committed) {
    const msg = `<p class="okmsg">${th("admin.import.done", { new: s.new, update: s.update })}${r.deactivate.length ? th("admin.import.done_deact", { count: r.deactivate.length }) : ""}.</p>`;
    await RENDER.products();                          // Preisliste im Reiter daneben gleich aktuell
    const el = $("#preview"); if (el) el.innerHTML = msg;
    toast(t("admin.import.done_toast"));
    return;
  }
  const names = { price_cents: "admin.products.price", category: "admin.products.category", printer: "admin.products.printer",
    single: "admin.products.single", sort: "admin.products.sort", active: "admin.products.active", options: "admin.products.options",
    color: "admin.products.color", delivery: "admin.products.delivery", stock: "admin.stock.col", stock_warn: "admin.stock.warn",
    soldout: "admin.stock.soldout" };
  const fmt = (k, v) => k === "price_cents" ? chf(v) : typeof v === "boolean" ? t(v ? "admin.common.yes" : "admin.common.no")
    : k === "stock" && v === "" ? t("admin.stock.off") : (v === "" ? "–" : v ?? "–");
  // Bestand überschreiben: deutlich sagen, was jetzt gilt und was danach (der Zähler läuft inzwischen weiter)
  const stockRows = r.rows.filter((x) => x.changes?.stock);
  const act = { new: "admin.import.act_new", update: "admin.import.act_update", error: "admin.import.act_error" };
  out.innerHTML = `${s.file_errors.length ? `<p class="err">${s.file_errors.map(esc).join("<br>")}</p>` : ""}
    ${s.notes?.length ? `<p class="hint">${s.notes.map(esc).join("<br>")}</p>` : ""}
    ${stockRows.length ? `<p class="hint">${th("admin.import.stock_note", { list: stockRows.map((x) =>
      t("admin.import.stock_item", { name: x.name, now: fmt("stock", x.changes.stock[0]), new: fmt("stock", x.changes.stock[1]) })).join(", ") })}</p>` : ""}
    <p>${th("admin.import.summary", { new: s.new, update: s.update, unchanged: s.unchanged })}${s.error ? `, <b class="err">${th("admin.import.summary_errors", { count: s.error })}</b>` : ""}${r.deactivate.length ? th("admin.import.summary_deact", { count: r.deactivate.length }) : ""}.
    ${s.new_categories.length ? th("admin.import.new_categories", { list: s.new_categories.join(", ") }) : ""}</p>
    ${r.rows.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.import.line")}</th><th>${th("admin.import.product")}</th><th>${th("admin.import.action")}</th><th>${th("admin.import.details")}</th></tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td>${x.line}</td><td>${esc(x.name)}</td>
        <td class="act-${x.action}">${esc(act[x.action] ? t(act[x.action]) : x.action)}</td>
        <td>${x.errors.length ? `<span class="err">${x.errors.map(esc).join("; ")}</span>` :
          Object.entries(x.changes).map(([k, [a, b]]) => `<span class="diff">${esc(names[k] ? t(names[k]) : k)}: ${esc(fmt(k, a))} → <b>${esc(fmt(k, b))}</b></span>`).join("<br>")}</td></tr>`).join("")}
    </tbody></table></div>` : ""}
    ${r.deactivate.length ? `<p class="hint">${th("admin.import.deactivated", { list: r.deactivate.join(", ") })}</p>` : ""}
    <div class="form" style="margin-top:12px">
      ${s.ok && (s.new || s.update || r.deactivate.length) ? `<button class="btn pri" id="commit">${th("admin.import.commit")}</button>` :
        s.ok ? `<p class="okmsg">${th("admin.import.nothing")}</p>` : `<p class="err">${th("admin.import.fix_errors")}</p>`}
    </div>`;
  $("#commit")?.addEventListener("click", () => csvPreview(true));
}
