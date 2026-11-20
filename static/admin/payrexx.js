/* FestPOS – Verwaltung › Kasse › «Karte/TWINT mit NFC über Payrexx» (T13, nur Admin).
   PAYREXX-UNGETESTET: noch nie gegen ein echtes Payrexx-Konto gelaufen (PAYREXX_GEPRUEFT in app/payrexx_api.py,
   docs/testplan-payrexx.md).
   Ein/aus, Zahlmittel (speichert der Balken der Seite, kassepage.js), Begleit-App «FestPOS Tap» hochladen und über Fully auf die Handys installieren.
   Server: app/payrexx.py. */
import { $, api, esc, guard, toast } from "./core.js";

// Payrexx ist noch nicht mit einem echten Konto geprüft (PAYREXX-UNGETESTET): Hinweis auf der Karte und Rückfrage,
// sobald jemand den Schalter einschaltet. Abbrechen lässt ihn aus.
export const untestedBox = (v) => (v && v.tested === false ? `<div class="warnbox" data-untested>${th("admin.payrexx.untested")}</div>` : "");
export function confirmUntested(box, v) {
  if (!box || !v || v.tested !== false) return;
  box.addEventListener("change", () => {
    if (box.checked && !confirm(t("admin.payrexx.untested_q"))) box.checked = false;
  });
}

let P = null;
export const payrexxState = () => P;                // für den Zustand im Titel der Karte (kassepage.js)
export async function payrexxCard() {
  P = await api("GET", "/payrexx").catch(() => null);
  if (!P) return "";
  const apk = P.apk;
  return `<form class="card" id="prxform">
    ${untestedBox(P)}
    <p class="hint">${th("admin.payrexx.intro")}</p>
    <label class="sw"><input type="checkbox" name="enabled" data-k="enabled" data-api="payrexx" data-type="bool" data-label="admin.payrexx.enabled" ${P.enabled ? "checked" : ""}><i aria-hidden="true"></i> ${th("admin.payrexx.enabled")}</label>
    <div class="form"><label>${th("admin.payrexx.method")}<select name="method" data-k="method" data-api="payrexx" data-type="str" data-label="admin.payrexx.method">
      ${[["", "admin.payrexx.m_any"], ["CONTACTLESS", "admin.payrexx.m_card"], ["TWINT", "admin.payrexx.m_twint"]].map(([v, k]) =>
        `<option value="${v}" ${P.method === v ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label></div>
    <label class="sw"><input type="checkbox" name="show_result" data-k="show_result" data-api="payrexx" data-type="bool" data-label="admin.payrexx.show_result" ${P.show_result ? "checked" : ""}><i aria-hidden="true"></i> ${th("admin.payrexx.show_result")}</label>
    <h3 style="margin-top:16px">${th("admin.payrexx.app_h")}</h3>
    <p class="hint">${th("admin.payrexx.app_hint")}</p>
    <p id="prx-apk">${apk ? th("admin.payrexx.apk_have", { version: esc(apk.version || "?"), node: esc(String(apk.node || "").toUpperCase()) })
      : th("admin.payrexx.apk_none")}</p>
    <div class="form">
      <label>${th("admin.payrexx.apk_file")}<input type="file" id="prx-file" accept=".apk"></label>
      <button type="button" class="btn" id="prx-install" ${apk && P.apk_here ? "" : "disabled"}>${th("admin.payrexx.install")}</button></div>
    <div id="prx-res"></div>
    <p class="hint">${th("admin.payrexx.storno_hint")}</p></form>`;
}
export function bindPayrexx() {
  const f = $("#prxform");
  if (!f) return;
  f.addEventListener("submit", (e) => e.preventDefault());      // T28: gespeichert wird über den Balken der Seite
  confirmUntested(f.querySelector('[data-k="enabled"]'), P);
  $("#prx-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const r = await guard(() => api("POST", "/payrexx/apk", fd));
    e.target.value = "";
    if (!r) return;
    $("#prx-apk").innerHTML = th("admin.payrexx.apk_have", { version: esc(r.version || "?"), node: esc(String(r.node || "").toUpperCase()) });
    $("#prx-install").disabled = false;
    toast(t("admin.payrexx.apk_ok"));
  });
  $("#prx-install").addEventListener("click", async (e) => {
    if (!confirm(t("admin.payrexx.install_q"))) return;
    e.currentTarget.disabled = true;
    const r = await guard(() => api("POST", "/payrexx/apk/install"));
    e.currentTarget.disabled = false;
    if (!r) return;
    $("#prx-res").innerHTML = `<ul class="hint">${r.results.map((x) => `<li>${esc(x.name)}: ${th(x.state === "installing" ? "admin.payrexx.installing" : "admin.payrexx.not_sent")}</li>`).join("")
      || `<li>${th("admin.payrexx.no_phones")}</li>`}</ul>`;
  });
}

// Handhelds › Einrichtung: die App «FestPOS Tap» auch hier – Stand, hochladen, auf alle Handhelds
// installieren (dieselben Schnittstellen wie unter Kasse › Karte/TWINT).
export async function tapCard() {
  const p = await api("GET", "/payrexx").catch(() => null);
  if (!p) return "";
  const apk = p.apk;
  const [cls, txt] = apk ? ["ok", t("admin.tap.st_have", { version: apk.version || "?" })] : ["pending", t("admin.tap.st_none")];
  return `<details class="card set-card" id="ftap" data-in="setup"><summary><h3>${th("admin.payrexx.app_h")}</h3>
      <span class="status ${cls}">${esc(txt)}</span></summary>
    ${untestedBox(p)}
    <p class="hint">${th("admin.payrexx.app_hint")}</p>
    <p id="ftap-apk">${apk ? th("admin.payrexx.apk_have", { version: esc(apk.version || "?"), node: esc(String(apk.node || "").toUpperCase()) })
      : th("admin.payrexx.apk_none")}</p>
    ${p.enabled ? "" : `<p class="hint">${th("admin.tap.off_hint")}</p>`}
    <div class="form"><button type="button" class="btn" id="ftap-pick">${th("admin.tap.upload")}</button>
      <input type="file" id="ftap-file" accept=".apk" hidden>
      <button type="button" class="btn" id="ftap-install" ${apk && p.apk_here ? "" : "disabled"}>${th("admin.payrexx.install")}</button></div>
    <div id="ftap-res"></div></details>`;
}
export function bindTap() {
  if (!$("#ftap")) return;
  $("#ftap-pick").addEventListener("click", () => $("#ftap-file").click());
  $("#ftap-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const r = await guard(() => api("POST", "/payrexx/apk", fd));
    e.target.value = "";
    if (!r) return;
    $("#ftap-apk").innerHTML = th("admin.payrexx.apk_have", { version: esc(r.version || "?"), node: esc(String(r.node || "").toUpperCase()) });
    $("#ftap-install").disabled = false;
    $("#ftap .status").className = "status ok";
    $("#ftap .status").textContent = t("admin.tap.st_have", { version: r.version || "?" });
    toast(t("admin.payrexx.apk_ok"));
  });
  $("#ftap-install").addEventListener("click", async (e) => {
    if (!confirm(t("admin.payrexx.install_q"))) return;
    const b = e.currentTarget;
    b.disabled = true;
    const r = await guard(() => api("POST", "/payrexx/apk/install"));
    b.disabled = false;
    if (!r) return;
    $("#ftap-res").innerHTML = `<ul class="hint">${r.results.map((x) => `<li>${esc(x.name)}: ${th(x.state === "installing" ? "admin.payrexx.installing" : "admin.payrexx.not_sent")}</li>`).join("")
      || `<li>${th("admin.payrexx.no_phones")}</li>`}</ul>`;
  });
}
