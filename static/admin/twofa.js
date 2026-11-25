/* FestPOS – Verwaltung › Zweiter Faktor und kritische Aktionen bestätigen (T-0-N/FestPOS#9, app/twofa.py).
   - Kritische Aktion ohne frische Bestätigung: der Server antwortet 428, api() (core.js) ruft askConfirm() – Fenster
     «Bitte bestätigen» mit Passwort (und Code) – und wiederholt danach die Anfrage. Die Bestätigung steckt im Token
     und gilt 5 Minuten auf allen Pi.
   - Einrichten: QR-Code für die Authenticator-App, ersten Code eingeben, dann 10 Wiederherstellungs-Codes. Ist der
     zweite Faktor für die Rolle Pflicht, geht es dreimal «Später», danach ist die Verwaltung bis zum Einrichten gesperrt.
   - Benutzermenü › Zweiter Faktor: Zustand, neue Wiederherstellungs-Codes, ausschalten (wenn nicht Pflicht).
   Eigene Anfragen mit fetch statt api(): ein falsches Passwort (422) darf nicht abmelden, und /me/confirm soll bei
   einer offenen Bestätigung nicht nochmals 428 auslösen. */
import { $, api, esc, ME, roleName, setMe, setToken, setTwofaViews, TOKEN, toast } from "./core.js";
import { setIdle } from "./idle.js";

async function post(path, body) {
  let r;
  try {
    r = await fetch("/api/admin" + path, { method: "POST", body: JSON.stringify(body || {}),
      headers: { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json", "X-Festpos-UI": "1", "X-Festpos-Lang": I18n.lang } });
  } catch { return { ok: false, msg: t("admin.common.server_unreachable") }; }
  const d = await r.json().catch(() => null);
  if (r.ok) return { ok: true, data: d };
  const det = d?.detail;
  return { ok: false, status: r.status, msg: typeof det === "string" ? det : det?.text || t("admin.common.error_status", { status: r.status }) };
}
function takeToken(d) {                               // neues Token (bestätigt) übernehmen, Frist ohne Bedienung neu
  if (!d?.token) return;
  setToken(d.token);
  try { sessionStorage.setItem("festpos.token", d.token); } catch { /* egal */ }
  setIdle(d);
}
async function reloadMe() {
  const me = await api("GET", "/me").catch(() => null);
  if (me) setMe({ ...ME, ...me });
  return me;
}
const codeField = (label = th("admin.login.code"), name = "code") => `<label class="field">${label}<input name="${name}"
  class="code-in" inputmode="numeric" autocomplete="one-time-code" maxlength="11" required autocapitalize="characters" spellcheck="false"></label>`;
function dialog(cls, html) {
  const dlg = document.createElement("dialog");
  dlg.className = `dlg ${cls}`;
  dlg.innerHTML = html;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  return dlg;
}
function showErr(dlg, msg) {
  const e = $(".err", dlg);
  e.textContent = msg || "";
  e.hidden = !msg;
}

// ------------------------------------------------------------------ Bitte bestätigen (kritische Aktion)
let pending = null;                                    // mehrere 428 gleichzeitig: ein Fenster für alle
export function askConfirm() {
  if (pending) return pending;
  pending = new Promise((done) => {
    const needCode = !!ME?.totp?.on;
    const dlg = dialog("confirm-dlg", `<form method="dialog">
      <h3>${th("admin.confirm.title")}</h3>
      <p>${th("admin.confirm.text")}</p>
      <div class="form col"><label class="field">${th("admin.confirm.password")}<input type="password" name="password"
        autocomplete="current-password" required></label>${needCode ? codeField() : ""}</div>
      <p class="err" hidden></p>
      <p class="hint">${th("admin.confirm.hint")}</p>
      <div class="form dlg-foot"><button type="button" class="btn" data-x>${th("admin.common.cancel")}</button>
        <button type="submit" class="btn pri">${th("admin.login.confirm")}</button></div></form>`);
    let ok = false;
    dlg.addEventListener("close", () => { pending = null; done(ok); });
    $("[data-x]", dlg).addEventListener("click", () => dlg.close());
    $("form", dlg).addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target, go = $(".btn.pri", dlg);
      go.disabled = true;
      const r = await post("/me/confirm", { password: f.password.value, code: f.code?.value || "" });
      go.disabled = false;
      if (!r.ok) { showErr(dlg, r.msg); (f.code || f.password).select(); return; }
      takeToken(r.data);
      ok = true;
      dlg.close();
    });
    dlg.showModal();
  });
  return pending;
}

// ------------------------------------------------------------------ Einrichten
// Nach der Anmeldung (Pflicht) oder aus dem Benutzermenü (freiwillig). Gesperrt (kein «Später» mehr): nur Abmelden.
let setupOpen = false;
export async function openSetup() {
  if (setupOpen || !ME || ME.emergency) return;
  setupOpen = true;
  const tf = ME.totp || {}, forced = !!tf.required, blocked = !!tf.blocked;
  const r = await post("/me/totp/start");
  if (!r.ok) { setupOpen = false; toast(r.msg, true); return; }
  const s = r.data;
  const intro = forced ? th("admin.totp.setup_required", { role: roleName(ME.role) }) : th("admin.totp.setup_free");
  const back = blocked ? `<button type="button" class="btn" data-logout>${th("admin.nav.logout")}</button>`
    : forced && tf.later > 0 ? `<button type="button" class="btn" data-later>${th("admin.totp.later", { count: tf.later })}</button>`
    : `<button type="button" class="btn" data-x>${th("admin.common.cancel")}</button>`;
  const dlg = dialog("totp-dlg", `<form method="dialog">
    <h3>${th("admin.totp.setup_title")}</h3>
    <p>${intro}</p>${blocked ? `<p class="warnbox">${th("admin.totp.setup_blocked")}</p>` : ""}
    <ol class="steps"><li>${th("admin.totp.step1")}</li><li>${th("admin.totp.step2")}</li></ol>
    <div class="totp-row"><div class="totp-qr" aria-hidden="true">${s.qr}</div>
      <div class="form col"><p class="hint">${th("admin.totp.by_hand")}<br><code class="totp-key" translate="no">${esc(s.secret)}</code></p>
        ${codeField(th("admin.totp.code"))}</div></div>
    <p class="err" hidden></p>
    <div class="form dlg-foot">${back}<button type="submit" class="btn pri">${th("admin.totp.next")}</button></div></form>`);
  dlg.addEventListener("close", () => { setupOpen = false; });
  dlg.addEventListener("cancel", (e) => {                 // gesperrt: Esc schliesst nicht; Pflicht: Esc zählt als «Später»
    if (!blocked && !(forced && tf.later > 0)) return;
    e.preventDefault();
    $("[data-later]", dlg)?.click();
  });
  $("[data-x]", dlg)?.addEventListener("click", () => dlg.close());
  $("[data-logout]", dlg)?.addEventListener("click", () => dlg.close());           // Abmelden selbst: main.js
  $("[data-later]", dlg)?.addEventListener("click", async () => {
    const x = await post("/me/totp/later");
    if (!x.ok) { showErr(dlg, x.msg); return; }
    setMe({ ...ME, totp: { ...tf, later: x.data.later } });
    dlg.close();
  });
  $("form", dlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    const go = $(".btn.pri", dlg);
    go.disabled = true;
    const x = await post("/me/totp/confirm", { code: e.target.code.value });
    go.disabled = false;
    if (!x.ok) { showErr(dlg, x.msg); e.target.code.select(); return; }
    dlg.close();
    await reloadMe();
    showCodes(x.data.codes, true);
  });
  dlg.showModal();
  $(".code-in", dlg).focus();
}

// Wiederherstellungs-Codes (nur jetzt sichtbar): drucken oder notieren, «Fertig» erst nach dem Häkchen
function showCodes(codes, reload = false) {
  const dlg = dialog("codes-dlg", `<form method="dialog">
    <h3>${th("admin.totp.codes_title")}</h3>
    <p>${th("admin.totp.codes_text")}</p>
    <ul class="totp-codes" translate="no">${codes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
    <div class="form noprint"><button type="button" class="btn" data-print>${th("admin.totp.print")}</button></div>
    <label class="check noprint"><input type="checkbox" id="codes-noted"> ${th("admin.totp.noted")}</label>
    <div class="form dlg-foot noprint"><button type="submit" class="btn pri" disabled>${th("admin.totp.done")}</button></div></form>`);
  dlg.addEventListener("cancel", (e) => e.preventDefault());                  // nicht aus Versehen weg
  $("#codes-noted", dlg).addEventListener("change", (e) => { $(".btn.pri", dlg).disabled = !e.target.checked; });
  $("[data-print]", dlg).addEventListener("click", () => {
    document.body.classList.add("printing-codes");
    addEventListener("afterprint", () => document.body.classList.remove("printing-codes"), { once: true });
    print();
  });
  $("form", dlg).addEventListener("submit", (e) => {
    e.preventDefault();
    dlg.close();
    toast(t("admin.totp.on_done"));
    if (reload) dispatchEvent(new CustomEvent("festpos:totp"));                // Seite neu (war evtl. gesperrt)
  });
  dlg.showModal();
}

// ------------------------------------------------------------------ Benutzermenü › Zweiter Faktor
export async function openTotp() {
  const me = await reloadMe();
  const tf = me?.totp || ME?.totp || {};
  if (!tf.on) { openSetup(); return; }
  const fixed = !!tf.role_required;
  const dlg = dialog("totp-dlg", `<form method="dialog">
    <h3>${th("admin.totp.menu")}</h3>
    <p class="okmsg">${th("admin.totp.status_on", { count: tf.codes })}</p>
    ${fixed ? `<p class="hint">${th("admin.totp.status_required")}</p>` : ""}
    <div class="form col"><label class="field">${th("admin.confirm.password")}<input type="password" name="password"
      autocomplete="current-password" required></label>${codeField()}</div>
    <p class="hint">${th("admin.totp.action_hint")}</p>
    <p class="err" hidden></p>
    <div class="form dlg-foot"><span class="dlg-extra">${fixed ? "" : `<button type="submit" class="btn danger" value="off">${th("admin.totp.off")}</button>`}</span>
      <button type="button" class="btn" data-x>${th("admin.common.close")}</button>
      <button type="submit" class="btn pri" value="codes">${th("admin.totp.new_codes")}</button></div></form>`);
  $("[data-x]", dlg).addEventListener("click", () => dlg.close());
  $("form", dlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, what = e.submitter?.value === "off" ? "off" : "codes";
    const x = await post(`/me/totp/${what}`, { password: f.password.value, code: f.code.value });
    if (!x.ok) { showErr(dlg, x.msg); return; }
    dlg.close();
    await reloadMe();
    if (what === "codes") showCodes(x.data.codes);
    else toast(t("admin.totp.off_done"));
  });
  dlg.showModal();
}

// Nach der Anmeldung bzw. beim Laden: Pflicht und noch nicht eingerichtet -> Einrichten anbieten
export function checkSetup() {
  const tf = ME?.totp;
  if (tf?.required && (tf.later > 0 || tf.blocked)) openSetup();      // drittes «Später»: diese Anmeldung in Ruhe
}
setTwofaViews(askConfirm, openSetup);
