/* FestPOS – Verwaltung › Grundlagen: Hilfen ($, esc, chf …), Anmeldung (Token, Rolle), api(), guard(), Meldungen, Downloads. */

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const chf = (c) => {
  const fr = Math.floor(Math.abs(c) / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "'");
  return (c < 0 ? "−" : "") + fr + "." + String(Math.abs(c) % 100).padStart(2, "0");
};
export function parsePrice(s) {
  const v = String(s).replace(/chf|fr\.?|\s|'/gi, "").replace("–", "").replace("-", "").replace(",", ".").replace(/\.$/, "");
  if (!/^\d+(\.\d{1,2})?$/.test(v)) return null;
  const [fr, rp = ""] = v.split(".");
  return +fr * 100 + +((rp + "00").slice(0, 2));
}
export const fmtTime = (iso) => iso ? iso.replace("T", " ").slice(5, 16).replace(/^(\d\d)-(\d\d)/, "$2.$1.") : "";
export const fmtDate = (iso) => {
  if (!iso) return "–";
  try { return new Date(iso.slice(0, 10) + "T12:00:00").toLocaleDateString(I18n.locale, { day: "2-digit", month: "2-digit", year: "numeric" }); }
  catch { return iso.slice(0, 10).split("-").reverse().join("."); }
};
export const LANG_KEY = "festpos.admin.lang";
export const myLang = () => { try { return localStorage.getItem(LANG_KEY) || ""; } catch { return ""; } };
export let LANGS = [];                                 // verfügbare Sprachen (aus den Einstellungen)

// ------------------------------------------------------------------ API
export let TOKEN = sessionStorage.getItem("festpos.token") || "";
sessionStorage.removeItem("festpos.admin");          // ältere Versionen speicherten hier das Passwort
const auth = () => "Bearer " + TOKEN;
// Wer angemeldet ist: Name, Rolle und Rechte (auth.py: orders, jobs, … und «admin» nur für die Rolle Admin). Was
// erlaubt ist, entscheidet der Server – hier wird nur ausgeblendet, was ohnehin abgelehnt würde. can("x") = Recht x,
// can(["x", "y"]) = eines davon, can("base") = angemeldet.
export let ME = (() => { try { return JSON.parse(sessionStorage.getItem("festpos.me") || "null"); } catch { return null; } })();
export const can = (need) => !!ME && [need].flat().some((p) => p === "base" || (ME.perms || []).includes(p));
export const ROLE_KEY = { view: "admin.users.role_view", lead: "admin.users.role_lead", admin: "admin.users.role_admin" };
// Name einer Rolle: feste übersetzt, eigene wie eingetragen (roles: Liste aus GET /users, sonst die eigene Rolle)
export const roleName = (id, roles = []) => (ROLE_KEY[id] ? t(ROLE_KEY[id])
  : roles.find((r) => r.id === id)?.name || (id === ME?.role ? ME?.role_name : null) || id);
export function setMe(m) { ME = m; try { sessionStorage.setItem("festpos.me", JSON.stringify(m)); } catch { /* egal */ } }
export function forgetLogin() { TOKEN = ""; ME = null; sessionStorage.removeItem("festpos.token"); sessionStorage.removeItem("festpos.me"); }

// Zustand eines Druckers aus dem Fehlercode (nie aus dem Text – der ist übersetzt)
const ERR = { paper_out: "admin.err.paper_out", cover_open: "admin.err.cover_open", printer_error: "admin.err.printer_error",
  no_status: "admin.err.no_status",
  rerouted: "admin.err.rerouted", uncertain: "admin.err.uncertain", render: "admin.err.render" };
export const errLabel = (code) => t(ERR[code] || "admin.err.unreachable");
class ApiError extends Error {
  constructor(msg, status = 0) { super(msg); this.status = status; }    // status: z.B. 428 = Passwort nötig (Sicherung)
}
// Anmeldeformular bei «nicht angemeldet»: setzt shell.js (ein Import von dort gäbe einen Ring core <-> shell)
let showLogin = () => {};
export function setLoginView(fn) { showLogin = fn; }
export async function api(method, path, body, { raw = false } = {}) {
  const headers = { Authorization: auth(), "X-Festpos-UI": "1", "X-Festpos-Lang": I18n.lang };
  let payload = body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let r;
  try { r = await fetch("/api/admin" + path, { method, headers, body: payload }); }
  catch { throw new ApiError(t("admin.common.server_unreachable")); }
  if (r.status === 401) { forgetLogin(); showLogin(t("admin.login.again")); throw new ApiError(t("admin.login.not_logged_in")); }
  if (raw) {
    if (r.ok) return r;
    let d = null;
    try { d = (await r.json()).detail; } catch { /* keine JSON-Antwort */ }
    throw new ApiError(typeof d === "string" ? d : t("admin.common.error_status", { status: r.status }), r.status);
  }
  let data = null;
  try { data = await r.json(); } catch { /* leer */ }
  if (!r.ok && !(r.status === 422 && data?.summary)) {
    const d = data?.detail;
    throw new ApiError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join(", ") : t("admin.common.error_status", { status: r.status }), r.status);
  }
  return data;
}
export async function guard(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { if (!(e instanceof ApiError && e.message === t("admin.login.not_logged_in"))) toast(e.message, true); return undefined; }
}

let toastTimer;
export function toast(msg, bad = false) {
  $$(".toast").forEach((e) => e.remove());
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : ""); el.textContent = msg; el.setAttribute("role", "status");
  document.body.appendChild(el);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.remove(), bad ? 7000 : 2500);
}
export const flash = (el) => { el.classList.remove("saved"); void el.offsetWidth; el.classList.add("saved"); };
export const wordOf = (st) => (st?.table_word === "Platz"
  ? { one: t("admin.word.seat"), many: t("admin.word.seats") } : { one: t("admin.word.table"), many: t("admin.word.tables") });

export const piName = (id) => String(id ?? "").toUpperCase();

// Downloads laufen über fetch + Blob: funktioniert auch, solange das Zertifikat am PC
// nicht installiert ist (Chrome blockiert dann normale Download-Links stillschweigend).
export async function download(path, name) {
  const r = await guard(() => api("GET", path, null, { raw: true }));
  if (!r) return;
  const url = URL.createObjectURL(await r.blob());
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// von anderen Modulen gesetzt (importierte Variablen sind dort nur lesbar)
export function setLangs(v) { LANGS = v; }
export function setToken(v) { TOKEN = v; }
