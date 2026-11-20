/* FestPOS – Verwaltung › Kasse & App › Reiter «Kasse» (T28; T34): sechs aufklappbare Karten mit Zustand im Titel – Abrechnen,
   Zahlarten & Trinkgeld, MWST, Karte/TWINT (Payrexx), QR-Bestellung am Tisch, Webseite & Rechtliches. Hilfe in einer Zeile, der Rest unter
   «Mehr dazu». Gespeichert wird alles über EINEN Balken unten («Geändert – Verwerfen · Speichern»); Felder tragen
   data-k (Schlüssel), data-api (wohin: settings | vat | payrexx | gast), data-type und data-label. Listen (MWST-Sätze)
   und Aktionen (App hochladen, QR-PDF, Rückerstatten) wirken wie bisher sofort. */
import { $, $$, api, guard, toast } from "./core.js";
import { loadNav, pageHead, redrawNav, RENDER, setDirty, view } from "./shell.js";
import { renderVat } from "./kasse.js";
import { bindPayrexx, payrexxCard, payrexxState } from "./payrexx.js";
import { bindGast, gastCard, gastState } from "./gast.js";
import { bindWeb, webCard, webState } from "./web.js";

const OPEN = new Set();                                // aufgeklappte Karten bleiben beim Neuzeichnen offen
let BASE = {};                                         // gespeicherter Stand der Felder: "api.k" -> Wert
const PAY_ST = { off: "admin.kasse.st_pay_off", optional: "admin.kasse.st_pay_optional", required: "admin.kasse.st_pay_required" };
const PATH = { settings: "/settings", vat: "/vat", payrexx: "/payrexx", gast: "/gast", web: "/web" };

const field = (k, type, label) => `data-k="${k}" data-api="settings" data-type="${type}" data-label="${label}"`;
function select(k, type, label, cur, opts) {
  return `<label>${th(label)}<select ${field(k, type, label)}>${opts.map(([v, key]) =>
    `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${key ? th(key) : ""}</option>`).join("")}</select></label>`;
}
const more = (...keys) => `<details class="k-more"><summary>${th("admin.common.more")}</summary>${keys.map((k) => `<p class="hint">${th(k)}</p>`).join("")}</details>`;
function card(id, title, sub, body) {
  return `<details class="card set-card k-card" data-card="${id}" ${OPEN.has(id) ? "open" : ""}>
    <summary><h3>${th(title)}</h3><span class="status" id="kst-${id}"></span><small class="k-sub">${th(sub)}</small></summary>
    <div class="k-body">${body}</div></details>`;
}

// Wert eines Feldes, wie er an den Server geht
function value(el) {
  const ty = el.dataset.type;
  if (el.type === "radio") return null;                                     // Radios: über die Gruppe (checked)
  if (ty === "bool") return el.checked;
  const v = el.value.trim();
  if (ty === "int") return Number(v || 0);
  if (ty === "chf") return Math.round(Number(v || 0) * 100);
  if (ty === "lower") return v.toLowerCase();
  return v;
}
function collect() {
  const out = {};
  $$("#kpage [data-k]").forEach((el) => {
    const key = `${el.dataset.api}.${el.dataset.k}`;
    if (el.type === "radio") { if (el.checked) out[key] = el.value; return; }
    out[key] = value(el);
  });
  return out;
}
const changed = () => { const now = collect(); return Object.keys(now).filter((k) => JSON.stringify(now[k]) !== JSON.stringify(BASE[k])); };

// Zustand im Titel (zeigt auch Ungespeichertes – wie es nach dem Speichern wäre)
function statuses() {
  const v = collect(), set = (id, txt, cls = "") => { const el = $(`#kst-${id}`); if (el) { el.textContent = txt; el.className = `status ${cls}`; } };
  set("tab", t(v["settings.tab_mode"] === "open" ? "admin.kasse.st_open" : "admin.kasse.st_now"), "ok");
  const pm = v["settings.payment_mode"] || "off";
  set("pay", `${t(PAY_ST[pm] || PAY_ST.off)} · ${t(v["settings.tips_enabled"] ? "admin.kasse.st_tips_on" : "admin.kasse.st_tips_off")}`, pm === "off" ? "" : "ok");
  set("vat", t(v["vat.enabled"] ? "admin.kasse.st_on" : "admin.kasse.st_off"), v["vat.enabled"] ? "ok" : "");
  if ("payrexx.enabled" in v) set("payrexx", t(v["payrexx.enabled"] ? "admin.kasse.st_on" : "admin.kasse.st_off"), v["payrexx.enabled"] ? "ok" : "");
  const g = gastState();
  if ("gast.open" in v) set("gast", t(v["gast.open"] && g && !g.closed ? "admin.kasse.st_open_qr" : "admin.kasse.st_closed"),
    v["gast.open"] && g && !g.closed ? "ok" : v["gast.open"] ? "pending" : "");
  const w = webState();
  if (w) set("web", w.missing.length ? t("admin.web.st_missing", { count: w.missing.length }) : t("admin.web.st_ok"),
    w.missing.length ? "pending" : "ok");
}
function sync() {
  const keys = changed();
  $$("#kpage [data-k]").forEach((el) => {
    el.closest("label")?.classList.toggle("k-changed", keys.includes(`${el.dataset.api}.${el.dataset.k}`));
  });
  const bar = $("#savebar");
  if (bar) {
    bar.hidden = !keys.length;
    const labels = [...new Set(keys.map((k) => $(`#kpage [data-k="${k.split(".")[1]}"][data-api="${k.split(".")[0]}"]`)?.dataset.label).filter(Boolean))];
    $("#savebar-what").textContent = labels.map((l) => t(l)).join(", ");
  }
  setDirty(keys.length > 0);
  const wait = $('#kpage [data-k="stock_check_wait"]');
  if (wait) wait.disabled = $('#kpage [data-k="stock_check"]').value === "off";
  statuses();
}
const mark = () => { BASE = collect(); sync(); };

async function save() {
  const now = collect(), keys = changed();
  const apis = [...new Set(keys.map((k) => k.split(".")[0]))];
  let navChanged = false;
  for (const a of apis) {
    const body = {};
    for (const [k, v] of Object.entries(now)) {
      const [api_, key] = k.split(".");
      if (api_ !== a) continue;
      if (a === "settings" && !keys.includes(k)) continue;                // Einstellungen: nur Geändertes
      const el = $(`#kpage [data-api="${a}"][data-k="${key}"]`);
      if (el?.dataset.type === "secret" && !v) continue;                  // Schlüssel: nur wenn neu eingetippt
      body[key] = v;
    }
    if (a === "settings" && "tab_mode" in body) navChanged = true;
    if (!await guard(() => api("PUT", PATH[a], body))) return;            // Fehler: Rest bleibt geändert
  }
  toast(t("admin.common.saved"));
  setDirty(false);
  if (navChanged) { await loadNav(); redrawNav(); }                    // «Offene Tische» im Menü
  RENDER.kasse();
}

RENDER.kasse = async () => {
  const [s, vat, prx, gst, wb] = await Promise.all([guard(() => api("GET", "/settings")), api("GET", "/vat").catch(() => null),
    payrexxCard(), gastCard(), webCard()]);
  if (!s || view !== "kasse") return;                  // inzwischen andere Seite (REV-048)
  const mode = s.tab_mode || "now";
  const abrechnen = `
    ${[["now", "admin.tabs.mode_now", "admin.tabs.mode_now_hint"], ["open", "admin.tabs.mode_open", "admin.tabs.mode_open_hint"]].map(([v, l, h]) =>
      `<label class="check"><input type="radio" name="tabmode" value="${v}" ${field("tab_mode", "str", "admin.tabs.card_title")} ${mode === v ? "checked" : ""}> <b>${th(l)}</b>&nbsp;– ${th(h)}</label>`).join("")}
    <div class="form" style="margin-top:6px">
      ${select("tab_who", "str", "admin.tabs.who", s.tab_who || "own_lead", [["own", "admin.tabs.who_own"], ["own_lead", "admin.tabs.who_own_lead"], ["any", "admin.tabs.who_any"]])}
      ${select("tab_idle", "int", "admin.tabs.idle", String(s.tab_idle ?? "45"), ["30", "45", "60", "90", "0"].map((v) => [v, v === "0" ? "admin.tabs.idle_never" : null]))}
    </div>
    <div class="form" style="margin-top:6px">
      ${select("stock_check", "str", "admin.kasse.stock_check", s.stock_check || "always", [["always", "admin.kasse.sc_always"], ["stock", "admin.kasse.sc_stock"], ["off", "admin.kasse.sc_off"]])}
      ${select("stock_check_wait", "int", "admin.kasse.stock_wait", String(s.stock_check_wait || 2), [[2, null], [5, null]])}
      ${select("nachbuchen", "str", "admin.kasse.nachbuchen", s.nachbuchen || "all", [["all", "admin.kasse.nb_all"], ["storno", "admin.kasse.nb_storno"], ["off", "admin.kasse.nb_off"]])}
    </div>
    <p class="hint">${th("admin.kasse.h_tab")}</p>
    ${more("admin.tabs.card_hint", "admin.kasse.stock_check_hint", "admin.kasse.nachbuchen_hint")}`;
  const zahlarten = `
    <div class="form">${select("payment_mode", "str", "admin.kasse.payment_mode", s.payment_mode || "off", [["off", "admin.kasse.pm_off"], ["optional", "admin.kasse.pm_optional"], ["required", "admin.kasse.pm_required"]])}</div>
    <p class="hint">${th("admin.kasse.h_pay")}</p>
    <label class="sw"><input type="checkbox" ${field("tips_enabled", "bool", "admin.kasse.tips")} ${s.tips_enabled === "1" ? "checked" : ""}><i aria-hidden="true"></i> ${th("admin.kasse.tips")}</label>
    <p class="hint">${th("admin.kasse.h_tips")}</p>
    ${more("admin.kasse.payment_hint", "admin.kasse.tips_hint")}`;
  $("#main").innerHTML = `<div id="kpage">${pageHead()}
    <p class="hint k-lead">${th("admin.kasse.lead")}</p>
    ${card("tab", "admin.kasse.c_tab", "admin.kasse.sub_tab", abrechnen)}
    ${card("pay", "admin.kasse.c_pay", "admin.kasse.sub_pay", zahlarten)}
    ${card("vat", "admin.vat.title", "admin.kasse.sub_vat", `<div id="vatbox"><p class="hint">${th("admin.common.loading")}</p></div>`)}
    ${prx ? card("payrexx", "admin.kasse.c_payrexx", "admin.kasse.sub_payrexx", prx) : ""}
    ${gst ? card("gast", "admin.kasse.c_gast", "admin.kasse.sub_gast", gst) : ""}
    ${wb ? card("web", "admin.kasse.c_web", "admin.kasse.sub_web", wb) : ""}
    <div class="savebar" id="savebar" hidden><span><b>${th("admin.save.unsaved")}</b><small id="savebar-what"></small></span>
      <span class="savebar-btns"><button type="button" class="btn" id="save-discard">${th("admin.save.discard")}</button>
        <button type="button" class="btn pri" id="save-go">${th("admin.common.save")}</button></span></div></div>`;
  // Selects ohne Text (Minuten, Sekunden): Zahl als Text
  $$('#kpage [data-k="tab_idle"] option').forEach((o) => { if (!o.textContent) o.textContent = t("admin.tabs.idle_min", { min: o.value }); });
  $$('#kpage [data-k="stock_check_wait"] option').forEach((o) => { if (!o.textContent) o.textContent = t("admin.kasse.stock_wait_s", { count: +o.value }); });
  if (vat) renderVat(vat, () => { BASE = { ...BASE, ...Object.fromEntries(Object.entries(collect()).filter(([k]) => k.startsWith("vat."))) }; sync(); });
  if (prx) bindPayrexx();
  if (gst) bindGast();
  if (wb) bindWeb(() => RENDER.kasse());
  mark();
  $$("#kpage .k-card").forEach((d) => d.addEventListener("toggle", () => { if (d.open) OPEN.add(d.dataset.card); else OPEN.delete(d.dataset.card); }));
  $("#kpage").addEventListener("input", sync);
  $("#kpage").addEventListener("change", sync);
  $("#save-go").addEventListener("click", save);
  $("#save-discard").addEventListener("click", () => { setDirty(false); RENDER.kasse(); });
  if (payrexxState() === null) $("#kst-payrexx")?.remove();
};
