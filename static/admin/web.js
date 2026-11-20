/* FestPOS – Verwaltung › Kasse & App › Karte «Webseite & Rechtliches» (nur Admin).
   Startseite und Rechtsseiten der QR-Bestellung: Veranstalter, Startseite, pro Seite Text aus FestPOS (mit Vorlage)
   oder Verweis auf eine andere Webseite, Aussehen (Hauptfarbe, Bild oben, eigenes CSS – vom Server geprüft),
   Vorschau (iframe ohne Skripte) und ZIP für Cloudflare Pages. Gespeichert wird über den Balken der Seite
   (data-api="web"); Bild, Vorlage und Vorschau wirken sofort. Vorschau: iframe mit sandbox ohne allow-scripts (nur
   allow-same-origin, damit die Schriften laden). Server: app/web.py. */
import { $, $$, api, esc, guard, toast } from "./core.js";

let W = null;
export const webState = () => W;                         // für den Zustand im Titel der Karte (kassepage.js)
const PAGES = [["imprint", "admin.web.p_imprint"], ["terms", "admin.web.p_terms"], ["privacy", "admin.web.p_privacy"], ["refund", "admin.web.p_refund"]];
const MISSING = { org_name: "admin.web.org_name", org_address: "admin.web.org_address", org_email: "admin.web.org_email",
  imprint: "admin.web.p_imprint", terms: "admin.web.p_terms", privacy: "admin.web.p_privacy", refund: "admin.web.p_refund" };

const f = (k, type, label) => `data-k="${k}" data-api="web" data-type="${type}" data-label="${label}"`;
const input = (k, label, extra = "") => `<label>${th(label)}<input ${f(k, "str", label)} value="${esc(W[k])}" autocomplete="off" ${extra}></label>`;
const area = (k, label, rows, cls = "") => `<label class="w-wide">${th(label)}<textarea ${f(k, "str", label)} rows="${rows}" class="${cls}" spellcheck="${cls ? "false" : "true"}">${esc(W[k])}</textarea></label>`;

function pageBlock([k, label]) {
  const mode = W[`${k}_mode`];
  const text = k === "imprint"
    ? `<p class="hint">${th("admin.web.imprint_auto")}</p>${area("imprint_text", "admin.web.imprint_extra", 3)}`
    : `${area(`${k}_text`, "admin.web.text", 8)}<div class="form"><button type="button" class="btn small" data-tpl="${k}">${th("admin.web.template")}</button></div>`;
  return `<div class="w-page" data-wpage="${k}"><b>${th(label)}</b>
    <div class="w-modes">${[["text", "admin.web.mode_text"], ["link", "admin.web.mode_link"]].map(([v, l]) =>
      `<label class="check"><input type="radio" name="web-${k}" value="${v}" ${f(`${k}_mode`, "str", label)} ${mode === v ? "checked" : ""}> ${th(l)}</label>`).join("")}</div>
    <div data-show="text">${text}</div>
    <div data-show="link"><div class="form">${input(`${k}_url`, "admin.web.url", `type="url" placeholder="${th("admin.web.url_ph")}"`)}</div>
      ${k === "privacy" ? `<p class="hint">${th("admin.web.privacy_link_hint")}</p>` : ""}</div></div>`;
}

export function webMissingText(v) {
  return (v.missing || []).map((k) => t(MISSING[k] || k)).join(", ");
}

export async function webCard() {
  W = await api("GET", "/web").catch(() => null);
  if (!W) return "";
  const host = W.domain || t("admin.web.no_domain");
  return `<div class="card" id="webform">
    <p class="hint">${th("admin.web.intro", { host })}</p>
    <p class="${W.missing.length ? "warn" : "ok"}" id="web-missing">${W.missing.length ? esc(t("admin.web.missing", { what: webMissingText(W) })) : th("admin.web.complete")}</p>
    <h3>${th("admin.web.h_org")}</h3>
    <div class="form">${input("org_name", "admin.web.org_name")}${area("org_address", "admin.web.org_address", 3)}
      ${input("org_email", "admin.web.org_email", 'type="email"')}${input("org_phone", "admin.web.org_phone", 'type="tel"')}
      ${input("org_person", "admin.web.org_person")}${input("org_vat", "admin.web.org_vat")}
      ${input("org_url", "admin.web.org_url", `type="url" placeholder="${th("admin.web.url_ph")}"`)}</div>
    <h3>${th("admin.web.h_start")}</h3>
    <div class="form">${area("intro", "admin.web.intro_text", 3)}${input("when", "admin.web.when", `placeholder="${th("admin.web.when_ph")}"`)}
      ${input("where", "admin.web.where")}</div>
    <label class="sw"><input type="checkbox" ${f("show_menu", "bool", "admin.web.show_menu")} ${W.show_menu ? "checked" : ""}><i aria-hidden="true"></i> ${th("admin.web.show_menu")}</label>
    <h3>${th("admin.web.h_pages")}</h3>
    ${PAGES.map(pageBlock).join("")}
    <h3>${th("admin.web.h_look")}</h3>
    <div class="form"><label>${th("admin.web.accent")}<input type="color" ${f("accent", "str", "admin.web.accent")} value="${esc(W.accent)}"></label>
      <label>${th("admin.web.image")}<span class="w-row"><input type="file" id="web-image" accept="image/*" hidden>
        <button type="button" class="btn small" id="web-image-pick">${th(W.image ? "admin.web.image_change" : "admin.web.image_pick")}</button>
        ${W.image ? `<button type="button" class="btn small" id="web-image-del">${th("admin.web.image_del")}</button>` : ""}</span></label></div>
    ${area("css", "admin.web.css", 10, "w-code")}
    <p class="hint">${th("admin.web.css_hint")}</p>
    <h3>${th("admin.web.h_publish")}</h3>
    <p class="hint">${th("admin.web.publish_hint")}</p>
    <div class="form"><button type="button" class="btn" id="web-preview">${th("admin.web.preview")}</button>
      <button type="button" class="btn" data-dl="/web/export.zip" data-name="festpos-webseite.zip">${th("admin.web.export")}</button></div>
    <div id="web-prev" hidden><nav class="w-tabs">${[["start", "admin.web.p_start"], ...PAGES].map(([k, l]) =>
      `<button type="button" class="chip" data-prev="${k}">${th(l)}</button>`).join("")}</nav>
      </div></div>`;
}

function modes() {
  $$("#webform .w-page").forEach((d) => {
    const v = d.querySelector("input[type=radio]:checked")?.value || "text";
    d.querySelectorAll("[data-show]").forEach((x) => { x.hidden = x.dataset.show !== v; });
  });
}

async function showPreview(kind) {
  const r = await guard(() => api("GET", `/web/preview/${kind}`));
  if (!r) return;
  $("#web-prev").hidden = false;
  if (!$("#web-frame")) {                                 // erst jetzt anlegen: ein leeres iframe meldet sonst Fehler
    $("#web-prev").insertAdjacentHTML("beforeend", `<iframe id="web-frame" sandbox="allow-same-origin" title="${th("admin.web.preview")}"></iframe>`);
  }
  $("#web-frame").srcdoc = r.html;
  $$("#web-prev [data-prev]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.prev === kind)));
}

export function bindWeb(rerender) {
  const box = $("#webform");
  if (!box) return;
  modes();
  box.addEventListener("change", (e) => { if (e.target.type === "radio") modes(); });
  box.addEventListener("click", async (e) => {
    const tpl = e.target.closest("[data-tpl]");
    if (tpl) {
      const ta = $(`#webform [data-k="${tpl.dataset.tpl}_text"]`);
      if (ta.value.trim() && !confirm(t("admin.web.template_q"))) return;
      const q = new URLSearchParams({ lang: I18n.lang, name: $('#webform [data-k="org_name"]').value, email: $('#webform [data-k="org_email"]').value });
      const r = await guard(() => api("GET", `/web/template/${tpl.dataset.tpl}?${q}`));   // Angaben wie gerade eingetippt
      if (r) { ta.value = r.text; ta.dispatchEvent(new Event("input", { bubbles: true })); }
    }
    const pv = e.target.closest("[data-prev]");
    if (pv) showPreview(pv.dataset.prev);
  });
  $("#web-preview").addEventListener("click", () => showPreview("start"));
  $("#web-image-pick").addEventListener("click", () => $("#web-image").click());
  $("#web-image").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    if (await guard(() => api("POST", "/web/image", fd))) { toast(t("admin.common.saved")); rerender(); }
  });
  $("#web-image-del")?.addEventListener("click", async () => {
    if (await guard(() => api("DELETE", "/web/image"))) rerender();
  });
}
