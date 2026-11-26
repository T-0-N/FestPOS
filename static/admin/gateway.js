/* FestPOS – Verwaltung › Geräte & Netz › Netz & Internet: Karte «Gateway, DNS & Zeitserver» (app/gateway.py), oben auf
   der Seite. Ein Gateway für die Pi und die Handhelds: automatisch (wie bisher), fester Router (z.B. Firmennetz) oder
   ohne Internet (geschlossenes Netz); den Zeitserver in jeder Art. Gespeichert in festpos.conf auf allen Pi; die Pi
   übernehmen es selbst (Stand pro Pi). Speichern ist kritisch (api() fragt «Bitte bestätigen»).
   Auch für die Ersteinrichtung (einrichtung.js) und Netz umziehen (umzug.js): gwChoice, gwFields, gwRead. */
import { $, api, esc, guard, piName } from "./core.js";

export const GW_MODES = { auto: ["admin.gw.m_auto", "admin.gw.s_auto"], fest: ["admin.gw.m_fest", "admin.gw.s_fest"],
  ohne: ["admin.gw.m_ohne", "admin.gw.s_ohne"] };
export const GW_ERR = { mode: "err.gw.mode", ntp: "err.gw.ntp", gw_bad: "err.gw.bad", gw_outside: "err.gw.outside",
  gw_taken: "err.gw.taken", gw_pool: "err.gw.pool", dns_bad: "err.gw.dns", dns_many: "err.gw.dns" };

// Auswahl und Felder – name-Präfix p, damit sie in andere Formulare passen (Einrichtung, Umzug)
const GW_SHORT = { auto: "admin.gw.x_auto", fest: "admin.gw.x_fest", ohne: "admin.gw.x_ohne" };   // kurz (Netz umziehen)
export function gwChoice(mode, p = "gw", short = false) {
  return `<div class="choice gw-choice" data-gw-choice="${p}">${Object.entries(GW_MODES).map(([m, k]) => `<label>
    <input type="radio" name="${p}_mode" value="${m}" ${mode === m ? "checked" : ""}><b>${th(k[0])}</b>
    <span>${th(short ? GW_SHORT[m] : k[1])}</span></label>`).join("")}</div>`;
}
export function gwFields(c, p = "gw", cls = "field") {
  const f = (n, label, v, ph = "", mode = "decimal") => `<label class="${cls}">${th(label)}<input class="in" name="${p}_${n}"
    value="${esc(v || "")}" ${ph ? `placeholder="${esc(t(ph))}"` : ""} inputmode="${mode}" autocomplete="off"></label>`;
  const dns = c.dns || [];
  return `${c.mode === "fest" ? `<div class="gw-fields">${f("gateway", "admin.gw.f_gw", c.gateway)}
      <div class="gw-row2">${f("dns1", "admin.gw.f_dns1", dns[0])}${f("dns2", "admin.gw.f_dns2", dns[1])}</div></div>` : ""}
    <div class="gw-fields">${f("ntp", "admin.gw.f_ntp", c.ntp, "admin.gw.ntp_ph", "url")}</div>`;
}
// Werte aus einem Formular -> {mode, gateway, dns, ntp}
// Leere Felder bleiben leer (DNS leer = der Router); die Vorgabe gilt nur, wo es das Feld noch nicht gibt (Art gewechselt)
export function gwRead(form, p = "gw", fallback = {}) {
  const el = (n) => form.querySelector(`[name="${p}_${n}"]`);
  const v = (n) => el(n)?.value.trim() ?? "";
  const mode = form.querySelector(`[name="${p}_mode"]:checked`)?.value || fallback.mode || "auto";
  const dns = el("dns1") ? [v("dns1"), v("dns2")].filter(Boolean) : fallback.dns || [];
  return { mode, gateway: mode === "fest" ? (el("gateway") ? v("gateway") : fallback.gateway || "") : "",
    dns: mode === "fest" ? dns : [], ntp: el("ntp") ? v("ntp") : fallback.ntp || "" };
}

let G = null, FORM = null, PROBE = null, ERRS = null, PROBED = "";   // PROBED: welche Werte geprüft wurden
const key = (c) => JSON.stringify([c.mode, c.gateway || "", c.dns || [], c.ntp || ""]);
const chip = (cls, text) => `<span class="status ${cls}">${text}</span>`;

function probeChip(v, ok, bad) {
  if (v == null) return "";
  return ` ${chip(v ? "ok" : "bad", th(v ? ok : bad))}`;
}

function nowRows(c) {
  const rows = [];
  const pr = PROBE && PROBED === key(c) ? PROBE : {};          // Ergebnis nur neben den Werten, die geprüft wurden
  if (c.mode === "fest") {
    rows.push(["admin.gw.r_gw_all", esc(c.gateway) + probeChip(pr.gateway, "admin.gw.p_ok", "admin.gw.p_bad")]);
    rows.push(["admin.gw.r_dns", (c.dns?.length ? c.dns : [c.gateway]).map((d) => esc(d) + probeChip(pr.dns?.[d], "admin.gw.dns_ok", "admin.gw.dns_bad")).join(" · ")]);
  } else if (c.mode === "ohne") {
    rows.push(["admin.gw.r_gw_phones", th("admin.gw.v_none")]);
  } else {
    const gw = G.phones_gw;
    rows.push(["admin.gw.r_gw_phones", gw ? (gw === G.vip ? th("admin.gw.v_pi", { ip: esc(gw) }) : esc(gw)) : "–"]);
  }
  const ntp = pr.ntp ? ` ${chip(pr.ntp.ok ? "ok" : "bad", pr.ntp.offset != null && !pr.ntp.ok ? th("admin.gw.ntp_off", { s: pr.ntp.offset })
    : th(pr.ntp.ok ? "admin.gw.ntp_ok" : "admin.gw.ntp_bad"))}` : "";
  rows.push(["admin.gw.r_ntp", c.ntp ? esc(c.ntp) + ntp : th(c.mode === "ohne" ? "admin.gw.v_peers" : "admin.gw.v_pool")]);
  return `<table class="gw-now"><tbody>${rows.map(([k, v]) => `<tr><td>${th(k)}</td><td>${v}</td></tr>`).join("")}</tbody></table>`;
}

function nodeLine() {
  if (!G.wanted_rev) return "";
  const parts = Object.entries(G.nodes || {}).map(([id, n]) => {
    const done = n.rev === G.wanted_rev && n.ok;
    const cls = done ? "ok" : n.rev === G.wanted_rev && n.ok === false ? "bad" : "pending";
    const text = done ? t("admin.gw.n_ok") : cls === "bad" ? t("admin.gw.n_bad", { code: n.code || "?" }) : t("admin.gw.n_wait");
    return `${esc(piName(id))}: ${chip(cls, esc(text))}`;
  });
  return parts.length ? `<p class="hint gw-nodes">${parts.join(" · ")}</p>` : "";
}

// Ergebnis der Prüfung für noch nicht gespeicherte Werte: unter den Feldern, mit der geprüften Adresse
function probeLine([mode, gw, dns, ntp]) {
  const parts = [];
  if (mode === "fest") {
    parts.push(`${esc(gw)}${probeChip(PROBE.gateway, "admin.gw.p_ok", "admin.gw.p_bad")}`);
    for (const d of dns.length ? dns : [gw]) parts.push(`DNS ${esc(d)}${probeChip(PROBE.dns?.[d], "admin.gw.dns_ok", "admin.gw.dns_bad")}`);
  }
  if (ntp && PROBE.ntp) parts.push(`${esc(ntp)} ${chip(PROBE.ntp.ok ? "ok" : "bad", th(PROBE.ntp.ok ? "admin.gw.ntp_ok" : "admin.gw.ntp_bad"))}`);
  return `<p class="hint" id="gw-probe">${th("admin.gw.probe_draft")} ${parts.join(" · ") || "–"}</p>`;
}

function cardInner() {
  const cur = G.current, c = FORM || { ...cur, dns: [...(cur.dns || [])] };
  const head = cur.mode === "fest" ? `${t(GW_MODES.fest[0])} ${cur.gateway}` : t(GW_MODES[cur.mode]?.[0] || GW_MODES.auto[0]);
  return `<div class="head-row"><h3>${th("admin.gw.title")}</h3>${chip(cur.mode === "ohne" ? "pending" : "ok", esc(head))}</div>
    <p class="hint">${th("admin.gw.hint")}</p>
    ${nowRows(cur)}${nodeLine()}
    ${gwChoice(c.mode)}
    ${gwFields(c)}
    ${c.mode === "fest" ? `<p class="hint">${th("admin.gw.fest_hint")}</p>` : ""}
    ${c.mode === "ohne" ? `<div class="warnbox"><p>${th("admin.gw.ohne_warn")}</p></div>` : ""}
    ${ERRS?.length ? `<p class="err" id="gw-err">${ERRS.map((e) => th(GW_ERR[e] || "err.gw.bad", { net: G.net })).join("<br>")}</p>` : ""}
    ${ERRS && !ERRS.length && PROBE && PROBED !== key(cur) ? probeLine(JSON.parse(PROBED)) : ""}
    ${ERRS && !ERRS.length && PROBE && PROBED === key(cur) ? `<p class="okmsg" id="gw-ok">${th("admin.gw.check_done")}</p>` : ""}
    <div class="form"><button type="button" class="btn" id="gw-check">${th("admin.gw.check")}</button>
      <button class="btn pri" id="gw-save">${th("admin.gw.save")}</button></div>
    <p class="hint">${th("admin.gw.save_hint")}</p>`;
}

export function gwCard(g) {
  G = g; FORM = null; PROBE = null; ERRS = null;
  return g ? `<form class="card" id="gwcard">${cardInner()}</form>` : "";
}

function redraw() {
  const card = $("#gwcard");
  if (card) card.innerHTML = cardInner();
}

export function bindGw(onSaved) {
  const card = $("#gwcard");
  if (!card) return;
  card.addEventListener("change", (e) => {
    if (e.target.name === "gw_mode") { FORM = gwRead(card, "gw", G.current); PROBE = null; ERRS = null; redraw(); }
  });
  card.addEventListener("input", () => { FORM = gwRead(card, "gw", G.current); });
  card.addEventListener("click", async (e) => {
    if (e.target.id !== "gw-check") return;
    FORM = gwRead(card, "gw", G.current);
    const r = await guard(() => api("POST", "/gateway/pruefen", FORM));
    if (!r) return;
    ERRS = r.errors; PROBE = r.probe; PROBED = key(FORM);
    redraw();
  });
  card.addEventListener("submit", async (e) => {
    e.preventDefault();
    FORM = gwRead(card, "gw", G.current);
    const r = await guard(() => api("PUT", "/gateway", FORM), t("admin.gw.saved"));
    if (r) { G = r; FORM = null; PROBE = null; ERRS = null; if (onSaved) onSaved(r); else redraw(); }
  });
}
