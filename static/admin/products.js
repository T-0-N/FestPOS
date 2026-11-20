/* FestPOS – Verwaltung › Seite «Produkte»: Preisliste, Kategorien, Farbe, Lieferzeit, Bestand, Optionen.
   T28 Stufe 2: kompakte Zeilen (Ziehen, Bild, Farbe und «Aktiv» direkt), alles andere im Fenster (dlg.js). */
import { $, api, can, chf, esc, flash, guard, parsePrice, toast } from "./core.js";
import { VAT_OPTS, vatName } from "./ledger.js";
import { applyTabs, page, RENDER, tabBar } from "./shell.js";
import { bindImport, importCards } from "./imports.js";
import { dragSort, gripCell } from "./dragsort.js";
import { bindDelivery, deliveryCard } from "./settings.js";
import { editDialog } from "./dlg.js";

// ------------------------------------------------------------------ Produkte
let CACHE = { products: [], categories: [], printers: [] };
async function loadBase() {
  const [products, categories, printers, settings] = await Promise.all([api("GET", "/products"), api("GET", "/categories"),
    api("GET", "/printers"), api("GET", "/settings")]);
  const times = String(settings.delivery_times || t("admin.settings.delivery_default")).split(",").map((x) => x.trim()).filter(Boolean);
  CACHE = { products, categories, printers, times, vat: settings.vat_enabled === "1", settings };
  return true;
}
const opts = (list, sel, empty = "–") => `<option value="">${esc(empty)}</option>` +
  list.map((x) => `<option value="${x.id}" ${x.id === sel ? "selected" : ""}>${esc(x.name)}</option>`).join("");
const thumbHTML = (p) => `<span class="thumbwrap"><label class="thumb" title="${th("admin.products.image_upload")}">${p.image_id ? `<img src="/media/${esc(p.image_id)}" alt="">` : "+"}
    <input type="file" accept="image/*" data-f="image" hidden></label>
    ${p.image_id ? `<button type="button" class="thumb-del" data-img-del="${p.id}" title="${th("admin.products.image_delete")}" aria-label="${th("admin.products.image_delete")}">×</button>` : ""}</span>`;

RENDER.products = async () => {
  if (!await guard(loadBase)) return;
  const { products, categories, printers, vat } = CACHE;
  const catName = (id) => categories.find((c) => c.id === id)?.name || t("admin.products.no_category");
  if (page !== "products") return;                     // inzwischen andere Seite (REV-048)
  const catVat = (id) => categories.find((c) => c.id === id)?.vat_code || "normal";
  const prodVat = (p) => `<select name="vat" data-f="vat" aria-label="${th("admin.vat.col")}">
    <option value="">${th("admin.vat.like_category", { name: vatName(catVat(p.category_id)) })}</option>
    ${VAT_OPTS.map(([v, k]) => `<option value="${v}" ${p.vat_code === v ? "selected" : ""}>${th(k)}</option>`).join("")}</select>`;
  const printerName = (id) => printers.find((x) => x.id === id)?.name || t("admin.products.default_printer");
  // T28 Stufe 2: eine kompakte Zeile pro Produkt; Kleines darunter (Optionen, Lieferzeit, Bestand, Einzelbon)
  const sub = (p) => [optSummary(p.options) !== "–" ? `${th("admin.products.options")}: ${optSummary(p.options)}` : "",
    p.delivery ? `${th("admin.products.delivery")}: ${delivSummary(p)}` : "",
    stockSummary(p) !== "–" ? `${th("admin.stock.col")}: ${stockSummary(p)}` : "",
    p.single_ticket ? th("admin.products.single") : "", vat && p.vat_code ? th(VAT_OPTS.find(([v]) => v === p.vat_code)?.[1] || "admin.vat.col") : ""]
    .filter(Boolean).join(" · ");
  $("#main").innerHTML = `<div class="head-row"><h2>${th("admin.nav.products")}</h2>
      <button type="button" class="btn pri" id="prod-add" data-in="products">${th("admin.products.add_btn")}</button></div>${tabBar("products")}
    <div class="card" data-in="products"><div class="card-head"><h3>${th("admin.products.pricelist", { count: products.length })}</h3>
      ${products.length > 1 || categories.length > 1 ? `<button type="button" class="btn sm" id="sortaz">${th("admin.products.sort_az")}</button>` : ""}</div>
      ${products.length ? `<div class="scroll"><table class="tbl ptbl" id="ptable"><thead><tr><th></th>
        <th>${th("admin.products.image")}</th><th>${th("admin.products.name")}</th><th class="num">${th("admin.products.price")}</th>
        <th>${th("admin.products.color")}</th><th>${th("admin.products.printer")}</th><th>${th("admin.products.active")}</th><th></th></tr></thead><tbody>
        ${products.map((p, i) => `${i === 0 || products[i - 1].category_id !== p.category_id ? `<tr class="grp"><td colspan="8">${esc(catName(p.category_id))}<small>${th("admin.products.grp_hint")}</small></td></tr>` : ""}
          <tr data-id="${p.id}" data-cat="${p.category_id ?? ""}" class="${p.active ? "" : "inactive"}">
          ${gripCell(th("admin.products.grip"))}
          <td class="pimg">${thumbHTML(p)}</td>
          <td class="p-cell"><b class="p-name">${esc(p.name)}</b>${sub(p) ? `<small>${sub(p)}</small>` : ""}</td>
          <td class="num p-price">${chf(p.price_cents)}</td>
          <td class="pcolor">${colorCell(p, categories)}</td>
          <td class="p-prn">${esc(printerName(p.printer_id))}</td>
          <td><label class="sw"><input type="checkbox" data-f="active" ${p.active ? "checked" : ""}><i aria-hidden="true"></i>
            <span class="sw-txt">${th("admin.products.active")}</span></label></td>
          <td class="acts"><button class="btn sm" data-edit="${p.id}">${th("admin.common.edit")}</button></td></tr>`).join("")}
        </tbody></table></div>
        <p class="hint">${th("admin.products.list_hint")}</p>`
      : `<p class="hint">${th("admin.products.none")}</p>`}
    </div>
    <div class="card" data-in="categories"><h3>${th("admin.products.categories")}</h3>
      <div class="scroll"><table class="tbl" id="ctable"><thead><tr><th></th><th>${th("admin.products.name")}</th><th>${th("admin.products.color")}</th>${vat ? `<th>${th("admin.vat.col")}</th>` : ""}<th></th></tr></thead><tbody>
        ${categories.map((c) => `<tr data-id="${c.id}">${gripCell(th("admin.products.grip"))}
          <td><input class="name" data-f="name" value="${esc(c.name)}" maxlength="32"></td>
          <td class="ccolor">${catColorCell(c, categories)}</td>
          ${vat ? `<td><select data-f="vat_code" aria-label="${th("admin.vat.col")}">${VAT_OPTS.map(([v, k]) =>
            `<option value="${v}" ${(c.vat_code || "normal") === v ? "selected" : ""}>${th(k)}</option>`).join("")}</select></td>` : ""}
          <td><button class="btn sm danger" data-delcat="${c.id}">${th("admin.common.delete")}</button></td></tr>`).join("")}
      </tbody></table></div>
      <form class="form" id="newcat" style="margin-top:12px">
        <label>${th("admin.products.new_category")}<input name="name" required maxlength="32"></label><button class="btn">${th("admin.common.add")}</button>
      </form>
      <p class="hint">${th("admin.products.categories_hint")}</p>
    </div>
    ${can("admin") ? deliveryCard(CACHE.settings) : ""}
    ${importCards()}`;
  applyTabs();
  bindImport();
  bindDelivery();                                       // T28: Produkte › Lieferzeiten (bisher Einstellungen › Kasse)

  // Produkt bearbeiten oder neu (T28 Stufe 2): alles im Fenster, gespeichert mit «Speichern»
  const editProduct = (p) => editDialog({
    title: p ? esc(p.name) : th("admin.products.new"),
    saveLabel: p ? null : th("admin.common.add"),
    body: `<div class="form" id="prodform">
        <label>${th("admin.products.name")}<input name="name" required maxlength="40" value="${esc(p?.name || "")}" style="width:220px"></label>
        <label>${th("admin.products.price_chf")}<input name="price" required inputmode="decimal" placeholder="6.50" value="${p ? chf(p.price_cents).replace("'", "") : ""}" style="width:100px"></label>
        <label>${th("admin.products.category")}<select name="category_id">${opts(categories, p ? p.category_id : categories[0]?.id)}</select></label>
        <label>${th("admin.products.printer")}<select name="printer_id">${opts(printers, p?.printer_id ?? null, t("admin.products.default_printer"))}</select></label>
        ${vat && p ? `<label>${th("admin.vat.col")}${prodVat(p)}</label>` : ""}
        <label class="check"><input type="checkbox" name="single" ${p?.single_ticket ? "checked" : ""}> ${th("admin.products.single_long")}</label>
      </div>
      ${p ? `<div class="form p-more">
        <button type="button" class="btn" data-more="opts" title="${th("admin.products.options_edit")}">${th("admin.products.options")}: ${optSummary(p.options)}</button>
        <button type="button" class="btn" data-more="deliv" title="${th("admin.products.delivery_edit")} – ${th("admin.products.delivery_title")}">${th("admin.products.delivery")}: ${delivSummary(p)}</button>
        <button type="button" class="btn" data-more="stock" title="${th("admin.stock.edit")} – ${th("admin.stock.col_title")}">${th("admin.stock.col")}: ${stockSummary(p)}</button></div>` : ""}
      <p class="hint">${th("admin.products.new_hint")}</p>`,
    extra: p ? `<button type="button" class="btn danger" data-a="del">${th("admin.common.delete")}</button>` : "",
    onOpen: (dlg) => {
      dlg.querySelectorAll("[data-more]").forEach((b) => b.addEventListener("click", () => {
        dlg.close();
        ({ opts: openOptions, deliv: openDelivery, stock: openStock })[b.dataset.more](p);
      }));
      dlg.querySelector('[data-a="del"]')?.addEventListener("click", async () => {
        if (!confirm(t("admin.products.delete_confirm", { name: p.name }))) return;
        if (await guard(() => api("DELETE", `/products/${p.id}`), t("admin.common.deleted"))) { dlg.close(); RENDER.products(); }
      });
    },
    onSave: async (f) => {
      const price = parsePrice(f.price.value);
      if (price == null) { toast(t("admin.products.price_unreadable"), true); return false; }
      const body = { name: f.name.value.trim(), price_cents: price, category_id: +f.category_id.value || null,
        printer_id: +f.printer_id.value || null, single_ticket: f.single.checked };
      if (!p) {
        const ok = await guard(() => api("POST", "/products", { ...body, sort: Math.max(0, ...products.map((x) => x.sort)) + 1 }),
          t("admin.products.created"));
        if (ok) RENDER.products();
        return !!ok;
      }
      const ok = await guard(() => api("PUT", `/products/${p.id}`, { ...body, active: !!p.active, sort: p.sort }), t("admin.common.saved"));
      if (!ok) return false;
      if (f.vat && (f.vat.value || null) !== (p.vat_code || null)) {
        if (!await guard(() => api("PUT", `/products/${p.id}/vat`, { vat_code: f.vat.value || null }))) return false;
      }
      RENDER.products();
      return true;
    },
  });
  $("#prod-add")?.addEventListener("click", () => editProduct(null));
  // T26: Kategorien und Produkte einmal alphabetisch ordnen (danach wieder frei ziehen)
  $("#sortaz")?.addEventListener("click", async () => {
    if (!confirm(t("admin.products.sort_az_q"))) return;
    if (await guard(() => api("POST", "/catalog/sort-az"), t("admin.products.sort_az_done"))) RENDER.products();
  });
  $("#newcat").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ok = await guard(() => api("POST", "/categories", { name: e.target.name.value.trim(),
      sort: Math.max(0, ...categories.map((c) => c.sort)) + 10 }), t("admin.products.category_created"));
    if (ok) RENDER.products();
  });
  const pt = $("#ptable");
  pt?.addEventListener("change", async (e) => {
    const tr = e.target.closest("tr"), id = +tr.dataset.id;
    const p = products.find((x) => x.id === id);
    if (e.target.dataset.f === "color") { setColor(p, e.target.value, categories); return; }
    if (e.target.dataset.f === "image") {
      const fd = new FormData(); fd.append("file", e.target.files[0]);
      const r = await guard(() => api("POST", `/products/${id}/image`, fd), t("admin.products.image_saved"));
      if (r) { p.image_id = r.image_id; $(".pimg", tr).innerHTML = thumbHTML(p); }
      return;
    }
    if (e.target.dataset.f === "active") {               // Betriebsschalter: sofort gespeichert (T28)
      const on = e.target.checked;
      const ok = await guard(() => api("PUT", `/products/${id}`, { name: p.name, price_cents: p.price_cents, category_id: p.category_id,
        printer_id: p.printer_id, single_ticket: !!p.single_ticket, active: on, sort: p.sort }), t("admin.common.saved"));
      if (ok) { p.active = on; tr.classList.toggle("inactive", !on); } else e.target.checked = !on;
    }
  });
  pt?.addEventListener("click", async (e) => {
    const rc = e.target.closest("[data-color-reset]");
    if (rc) { setColor(products.find((x) => x.id === +rc.dataset.colorReset), null, categories); return; }
    const di = e.target.closest("[data-img-del]");
    if (di) {
      const p = products.find((x) => x.id === +di.dataset.imgDel);
      if (!confirm(t("admin.products.image_delete_confirm", { name: p.name }))) return;
      if (await guard(() => api("DELETE", `/products/${p.id}/image`), t("admin.products.image_deleted"))) {
        p.image_id = null;
        const tr = di.closest("tr"); $(".pimg", tr).innerHTML = thumbHTML(p);
      }
      return;
    }
    const ed = e.target.closest("[data-edit]");
    if (ed) editProduct(products.find((x) => x.id === +ed.dataset.edit));
  });
  const ct = $("#ctable");
  ct.addEventListener("change", async (e) => {
    const tr = e.target.closest("tr"), id = +tr.dataset.id;
    const c = categories.find((x) => x.id === id), pick = $('[data-f="color"]', tr);
    // automatische Farbe bleibt automatisch, solange sie nicht verändert wird
    const color = e.target.closest("[data-color-auto]") ? CAT_AUTO
      : (pick.dataset.auto && e.target !== pick) ? CAT_AUTO : pick.value;
    const body = { name: $('[data-f="name"]', tr).value.trim(), color, sort: c?.sort ?? 0 };
    const vsel = $('[data-f="vat_code"]', tr);
    if (vsel) body.vat_code = vsel.value === "normal" ? null : vsel.value;
    if (await guard(() => api("PUT", `/categories/${id}`, body))) {
      flash(e.target);
      if (c) Object.assign(c, { name: body.name, color: body.color, sort: body.sort });
      refreshColors(products, categories);           // Farben hängen auch von der Reihenfolge ab
      if (vsel && c) c.vat_code = body.vat_code;
    }
  });
  // Reihenfolge per Drag & Drop (Griff ⠿) oder ↑/↓: Produkte nur innerhalb ihrer Kategorie
  if (pt) dragSort(pt, { group: (tr) => tr.dataset.cat, save: async (ids) => {
    if (!await guard(() => api("PUT", "/products/order", { ids }))) return RENDER.products();
    ids.forEach((id, i) => { const p = products.find((x) => x.id === id); if (p) p.sort = (i + 1) * 10; });
    toast(t("admin.products.order_saved"));
  } });
  dragSort(ct, { save: async (ids) => {
    if (!await guard(() => api("PUT", "/categories/order", { ids }))) return RENDER.products();
    toast(t("admin.products.order_saved"));
    RENDER.products();                               // Gruppen der Produkte und automatische Farben folgen der Reihenfolge
  } });
  ct.addEventListener("click", async (e) => {
    const ra = e.target.closest("[data-color-auto]");
    if (ra) { ra.dispatchEvent(new Event("change", { bubbles: true })); return; }
    const id = e.target.dataset.delcat;
    if (!id || !confirm(t("admin.products.category_delete_confirm"))) return;
    if (await guard(() => api("DELETE", `/categories/${id}`), t("admin.common.deleted"))) RENDER.products();
  });
};

// ---------- Kachelfarbe: erbt von der Kategorie, kann pro Produkt überschrieben werden
const CAT_AUTO = "#3a3a3a";                          // Kategorie ohne eigene Farbe (common.js)
function catColorOf(catId, categories) {            // gleiche Regel wie in der Bestell-App (common.js)
  const i = categories.findIndex((c) => c.id === catId);
  return i < 0 ? "#000000" : festposCategoryColor(categories, i);
}
// Kategorie: zeigt die Farbe, welche die Kacheln wirklich haben – auch die automatische
function catColorCell(c, categories) {
  const auto = !c.color || c.color === CAT_AUTO;
  return `<span class="colorpick${auto ? "" : " own"}" title="${th(auto ? "admin.products.color_auto_title" : "admin.products.color_own")}">
    <input type="color" data-f="color" value="${esc(catColorOf(c.id, categories))}" class="catcolor"${auto ? ' data-auto="1"' : ""}>
    ${auto ? `<small>${th("admin.products.color_auto")}</small>` : `<button type="button" class="btn sm" data-color-auto="${c.id}" title="${th("admin.products.color_auto_reset")}">↺</button>`}</span>`;
}
function refreshColors(products, categories) {
  categories.sort((a, b) => a.sort - b.sort || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  categories.forEach((c) => { const td = document.querySelector(`#ctable tr[data-id="${c.id}"] .ccolor`); if (td) td.innerHTML = catColorCell(c, categories); });
  products.forEach((p) => { const td = document.querySelector(`#ptable tr[data-id="${p.id}"] .pcolor`); if (td) td.innerHTML = colorCell(p, categories); });
}
function colorCell(p, categories) {
  const own = !!p.color, col = p.color || catColorOf(p.category_id, categories);
  return `<span class="colorpick${own ? " own" : ""}" title="${th(own ? "admin.products.color_own" : "admin.products.color_inherited")}">
    <input type="color" data-f="color" value="${esc(col)}">
    ${own ? `<button type="button" class="btn sm" data-color-reset="${p.id}" title="${th("admin.products.color_reset")}">↺</button>` : `<small>${th("admin.products.category")}</small>`}</span>`;
}
async function setColor(p, color, categories) {
  const r = await guard(() => api("PUT", `/products/${p.id}/color`, { color }), t(color ? "admin.products.color_saved" : "admin.products.color_category"));
  if (!r) return;
  p.color = r.color;
  const td = document.querySelector(`#ptable tr[data-id="${p.id}"] .pcolor`);
  if (td) td.innerHTML = colorCell(p, categories);
}

// ---------- Lieferzeit pro Produkt: aus | wählbar | Pflicht | nur bestimmte Zeitpunkte (einer = erzwungen)
function delivAllowed(p) {
  const times = CACHE.times || [];
  if (!p.delivery_allowed) return times;
  const want = p.delivery_allowed.split(",").map((x) => x.trim().toLowerCase());
  return times.filter((x) => want.includes(x.toLowerCase()));
}
function delivSummary(p) {
  if (!p.delivery) return "–";
  const a = delivAllowed(p);
  if (a.length === 1) return th("admin.delivery.only", { when: a[0] });
  return (a.length === (CACHE.times || []).length ? th("admin.delivery.choice_short") : esc(a.join("/"))) + (p.delivery_required ? "*" : "");
}
// ---------- Ausverkauft und Restmenge (app/stock.py)
function stockSummary(p) {
  if (p.soldout || p.stock === 0) return `<span class="status bad">${th("admin.stock.soldout_short")}</span>`;
  if (p.stock == null) return "–";
  const low = p.stock_warn != null && p.stock <= p.stock_warn;
  return low ? `<span class="status pending">${esc(p.stock)}</span>` : esc(p.stock);
}
function openStock(p) {
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  const counted = p.stock != null;
  dlg.innerHTML = `<form method="dialog" id="stockform">
    <h3>${th("admin.stock.title", { name: p.name })}</h3>
    <label class="check"><input type="checkbox" name="soldout" ${p.soldout ? "checked" : ""}> <b>${th("admin.stock.soldout")}</b>&nbsp;– ${th("admin.stock.soldout_hint")}</label>
    <label class="check" style="margin-top:10px"><input type="radio" name="mode" value="off" ${counted ? "" : "checked"}> <b>${th("admin.stock.off")}</b>&nbsp;– ${th("admin.stock.off_hint")}</label>
    <label class="check"><input type="radio" name="mode" value="on" ${counted ? "checked" : ""}> <b>${th("admin.stock.on")}</b>&nbsp;– ${th("admin.stock.on_hint")}</label>
    <fieldset class="form plain stockvals" id="stockvals" style="margin-top:8px">
      <label>${th("admin.stock.left")}<input name="stock" type="number" min="0" max="99999" value="${esc(p.stock ?? "")}" style="width:110px"></label>
      <label>${th("admin.stock.warn")}<input name="warn" type="number" min="0" max="99999" value="${esc(p.stock_warn ?? (counted ? "" : 10))}" style="width:110px"></label>
    </fieldset>
    <p class="hint">${th("admin.stock.hint")}</p>
    <div class="form dlg-foot">
      <button type="button" class="btn" id="stockcancel">${th("admin.common.cancel")}</button>
      <button type="submit" class="btn pri">${th("admin.common.save")}</button>
    </div></form>`;
  document.body.appendChild(dlg);
  const f = $("#stockform", dlg);
  const sync = () => { $("#stockvals", dlg).disabled = f.mode.value === "off"; };   // «Ohne Bestand»: Felder grau
  f.addEventListener("change", sync); sync();
  dlg.addEventListener("close", () => dlg.remove());
  $("#stockcancel", dlg).addEventListener("click", () => dlg.close());
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const on = f.mode.value === "on", n = (x) => (x.value.trim() === "" ? null : Math.round(+x.value));
    const stock = on ? n(f.stock) : null, warn = on ? n(f.warn) : null;
    if (on && (stock == null || !(stock >= 0))) return toast(t("admin.stock.need_left"), true);
    const r = await guard(() => api("PUT", `/products/${p.id}/stock`, { soldout: f.soldout.checked, stock, stock_warn: warn }),
      t("admin.common.saved"));
    if (!r) return;
    Object.assign(p, { soldout: r.soldout, stock: r.stock, stock_warn: r.stock_warn });
    const b = document.querySelector(`[data-stock="${p.id}"]`); if (b) b.innerHTML = stockSummary(p);
    dlg.close();
  });
  dlg.showModal();
}
function openDelivery(p) {
  const times = CACHE.times || [];
  const allowed = p.delivery ? delivAllowed(p) : times;
  const mode = !p.delivery ? "off" : p.delivery_required ? "required" : "choice";
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog" id="delform">
    <h3>${th("admin.delivery.title", { name: p.name })}</h3>
    ${[["off", "admin.delivery.off", "admin.delivery.off_hint"],
       ["choice", "admin.delivery.choice", "admin.delivery.choice_hint"],
       ["required", "admin.delivery.required", "admin.delivery.required_hint"]]
      .map(([v, l, h]) => `<label class="check"><input type="radio" name="mode" value="${v}" ${mode === v ? "checked" : ""}> <b>${th(l)}</b>&nbsp;– ${th(h, { first: times[0] || "" })}</label>`).join("")}
    <div id="delallowed" style="margin-top:10px">
      <p class="hint" style="margin-bottom:4px">${th("admin.delivery.allowed_hint")}</p>
      <div class="form">${times.map((x) => `<label class="check"><input type="checkbox" name="t" value="${esc(x)}" ${allowed.includes(x) ? "checked" : ""}> ${esc(x)}</label>`).join("")}</div>
    </div>
    <div class="form dlg-foot">
      <button type="button" class="btn" id="delcancel">${th("admin.common.cancel")}</button>
      <button type="submit" class="btn pri">${th("admin.common.save")}</button>
    </div></form>`;
  document.body.appendChild(dlg);
  const f = $("#delform", dlg);
  const sync = () => { $("#delallowed", dlg).hidden = f.mode.value === "off"; };
  f.addEventListener("change", sync); sync();
  dlg.addEventListener("close", () => dlg.remove());
  $("#delcancel", dlg).addEventListener("click", () => dlg.close());
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const sel = [...f.querySelectorAll('input[name="t"]:checked')].map((x) => x.value);
    if (f.mode.value !== "off" && !sel.length) return toast(t("admin.delivery.need_one"), true);
    const body = { enabled: f.mode.value !== "off", required: f.mode.value === "required", allowed: sel };
    const r = await guard(() => api("PUT", `/products/${p.id}/delivery`, body), t("admin.delivery.saved"));
    if (!r) return;
    Object.assign(p, { delivery: r.delivery, delivery_allowed: r.delivery_allowed, delivery_required: r.delivery_required });
    const b = document.querySelector(`[data-deliv="${p.id}"]`); if (b) b.innerHTML = delivSummary(p);
    dlg.close();
  });
  dlg.showModal();
}

// ---------- Produkt-Optionen (z.B. Zucker: ohne / 1 / 2 – Pflicht; Freitext)
function optSummary(o) {
  if (!o) return "–";
  const parts = o.groups.map((g) => esc(g.name || g.choices.slice(0, 2).join("/")) + (g.required ? "*" : ""));
  if (o.free) parts.push(th("admin.options.text_short"));
  return parts.join(", ") || "–";
}
function openOptions(p) {
  const o = p.options || { groups: [], free: false };
  const groups = o.groups.length ? o.groups : [{ name: "", choices: [], required: false, multi: false }];
  const row = (g) => `<div class="optgroup">
      <label>${th("admin.options.group")}<input data-g="name" value="${esc(g.name)}" placeholder="${th("admin.options.group_placeholder")}" maxlength="30"></label>
      <label class="wide">${th("admin.options.choices")}<input data-g="choices" value="${esc(g.choices.join(", "))}" placeholder="${th("admin.options.choices_placeholder")}"></label>
      <label class="check"><input type="checkbox" data-g="required" ${g.required ? "checked" : ""}> ${th("admin.options.required")}</label>
      <label class="check"><input type="checkbox" data-g="multi" ${g.multi ? "checked" : ""}> ${th("admin.options.multi")}</label>
      <button type="button" class="btn sm danger" data-g-del title="${th("admin.options.group_remove")}">✕</button>
    </div>`;
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog" id="optform">
    <h3>${th("admin.options.title", { name: p.name })}</h3>
    <p class="hint">${th("admin.options.hint")}</p>
    <div id="optgroups">${groups.map(row).join("")}</div>
    <button type="button" class="btn sm" id="optadd">${th("admin.options.add_group")}</button>
    <label class="check" style="margin-top:10px"><input type="checkbox" id="optfree" ${o.free ? "checked" : ""}> ${th("admin.options.free")}</label>
    <div class="form dlg-foot">
      <button type="button" class="btn" value="cancel" id="optcancel">${th("admin.common.cancel")}</button>
      <button type="submit" class="btn pri" id="optsave">${th("admin.common.save")}</button>
    </div></form>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $("#optcancel", dlg).addEventListener("click", () => dlg.close());
  $("#optadd", dlg).addEventListener("click", () => {
    $("#optgroups", dlg).insertAdjacentHTML("beforeend", row({ name: "", choices: [], required: false, multi: false }));
  });
  $("#optgroups", dlg).addEventListener("click", (e) => { if (e.target.closest("[data-g-del]")) e.target.closest(".optgroup").remove(); });
  $("#optform", dlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      groups: [...dlg.querySelectorAll(".optgroup")].map((g) => ({
        name: $('[data-g="name"]', g).value.trim(),
        choices: $('[data-g="choices"]', g).value.split(/[,|]/).map((x) => x.trim()).filter(Boolean),
        required: $('[data-g="required"]', g).checked,
        multi: $('[data-g="multi"]', g).checked,
      })).filter((g) => g.name || g.choices.length),
      free: $("#optfree", dlg).checked,
    };
    const r = await guard(() => api("PUT", `/products/${p.id}/options`, body), t("admin.options.saved"));
    if (r) { p.options = r.options; dlg.close(); const b = document.querySelector(`[data-opts="${p.id}"]`); if (b) b.innerHTML = optSummary(r.options); }
  });
  dlg.showModal();
}
