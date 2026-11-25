/* FestPOS – Verwaltung › Tagesabschluss › Karte «Saison abschliessen» (T-0-N/FestPOS#12, app/saison.py, nur Admin).
   1. Archiv erstellen (Passwort) – 2. auf den USB-Stick (wird zurückgelesen) oder herunterladen und wieder hochladen –
   3. erst dann die Quittungen samt Name und Ort der Gäste auf dem Pi löschen (kritisch: «Bitte bestätigen»).
   Ein Archiv lässt sich jederzeit wieder hochladen und prüfen. */
import { $, api, download, esc, fmtDate, guard, toast } from "./core.js";

let ST = null;
const checkText = (c) => {
  if (!c) return "";
  if (c.ok !== true) return `<p class="err">${th("admin.season.check_bad")}</p>`;
  const other = (c.receipts?.other_key || 0) + (c.closings?.other_key || 0);
  const i = c.info || {};
  return `<p class="okmsg">✓ ${th("admin.season.check_ok", { closings: i.closings?.count ?? "?", receipts: i.receipts?.count ?? "?",
    first: fmtDate(i.period?.first), last: fmtDate(i.period?.last) })}</p>${other ? `<p class="hint">${th("admin.season.check_other", { count: other })}</p>` : ""}`;
};

function card() {
  const o = ST.open, p = ST.pending, last = ST.last;
  const status = last ? th("admin.season.last", { date: fmtDate(last.at) }) : th("admin.season.last_never");
  const ver = p?.verified;
  return `<div class="card" id="season"><div class="head-row"><h3>${th("admin.season.title")}</h3>
      <span class="status ${ST.due ? "pending" : last ? "ok" : ""}">${status}</span></div>
    <p>${th("admin.season.intro")}</p>
    <p>${o.closings ? th("admin.season.open", { closings: o.closings, receipts: o.receipts, recipients: o.recipients,
      first: fmtDate(o.first), last: fmtDate(o.last) }) : th("admin.season.nothing")}</p>
    <ol class="steps"><li>${th("admin.season.s1")}</li><li>${th("admin.season.s2")}</li><li>${th("admin.season.s3")}</li></ol>
    ${o.closings && !p ? `<form class="form" id="season-create">
        <label class="field">${th("admin.season.password")}<input type="password" name="pw" minlength="12" maxlength="200" required autocomplete="new-password"></label>
        <label class="field">${th("admin.season.password2")}<input type="password" name="pw2" minlength="12" maxlength="200" required autocomplete="new-password"></label>
        <button class="btn pri">${th("admin.season.create")}</button></form>` : ""}
    ${p ? `<p class="okmsg">✓ ${th("admin.season.created", { name: esc(p.name), closings: p.closings.count, receipts: p.receipts.count })}</p>
      <div class="form">${ST.usb ? `<button type="button" class="btn" id="season-usb">${th("admin.season.to_usb")}</button>` : ""}
        <button type="button" class="btn" id="season-dl">${th("admin.season.download")}</button>
        <button type="button" class="btn" id="season-discard">${th("admin.season.discard")}</button></div>
      <p class="${ver ? "okmsg" : "hint"}">${ver ? (ver.how === "usb" ? th("admin.season.verified_usb", { path: esc(ver.path || "") })
        : th("admin.season.verified_upload")) : th("admin.season.not_verified")}</p>` : ""}
    <form class="form" id="season-check"><label class="field">${th("admin.season.upload")}<input type="file" name="file" accept=".festpos" required></label>
      <label class="field">${th("admin.season.password")}<input type="password" name="pw" maxlength="200" required autocomplete="off"></label>
      <button class="btn">${th("admin.season.upload_btn")}</button></form>
    <div id="season-res"></div>
    ${p ? `<div class="form dlg-foot"><button type="button" class="btn danger" id="season-close" ${ver ? "" : "disabled"}>${th("admin.season.delete")}</button></div>` : ""}
    <p class="hint">${th("admin.season.hint")}</p></div>`;
}

export async function loadSeason(slot) {
  ST = await api("GET", "/season").catch(() => null);
  if (!ST || !slot.isConnected) return;
  slot.innerHTML = card();
  const again = () => loadSeason(slot);
  $("#season-create")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.pw.value !== f.pw2.value) { toast(t("admin.backup.dl_mismatch"), true); return; }
    f.querySelector("button").disabled = true;
    if (await guard(() => api("POST", "/season/archive", { password: f.pw.value }))) again();
    else f.querySelector("button").disabled = false;
  });
  $("#season-usb")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    if (await guard(() => api("POST", "/season/usb"))) again(); else e.target.disabled = false;
  });
  $("#season-dl")?.addEventListener("click", () => download("/season/archive", ST.pending.name));
  $("#season-discard")?.addEventListener("click", async () => {
    if (await guard(() => api("DELETE", "/season/archive"))) again();
  });
  $("#season-check").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("file", e.target.file.files[0]);
    fd.append("password", e.target.pw.value);
    const r = await guard(() => api("POST", "/season/verify", fd));
    if (!r) return;
    if (r.matches_pending && r.check.ok) { again(); return; }
    $("#season-res").innerHTML = checkText(r.check);
  });
  $("#season-close")?.addEventListener("click", async () => {
    if (!confirm(t("admin.season.delete_q", { count: ST.pending.receipts.count }))) return;
    const r = await guard(() => api("POST", "/season/close", { sha: ST.pending.sha }));   // kritisch: bestätigen
    if (r) { toast(t("admin.season.deleted", { count: r.receipts })); again(); }
  });
}
