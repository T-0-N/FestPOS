/* FestPOS – Verwaltung › Geräte & Netz › Pi › «Netz umziehen» (T-0-N/FestPOS#2, Teil 2).
   Schritt 1 Planen (Netz, VIP, Pi, DHCP, Prüfung, Checkliste), dann Ablauf: vorbereiten, umstellen (Neustart),
   Pi im neuen Netz, Bestätigen über die neue Adresse innert 10 Minuten – sonst stellen die Pi selbst zurück. */
import { $, api, download, esc, guard, piName, toast } from "./core.js";

let DATA = null, AT = 0, TIMER = null, PLAN = null, CHECK = null, LAST = "";
const RUNNING = new Set(["prepared", "switching", "back_requested"]);
const PHASE_STEP = { prepared: [2, "admin.umzug.st_prepare"], switching: [4, "admin.umzug.st_switch"],
  back_requested: [4, "admin.umzug.st_back"] };
const ERR = {
  net: "err.setup.net_net", size: "err.setup.net_size", ip_bad: "err.setup.net_ip_bad", ip_outside: "err.setup.net_ip_outside",
  ip_twice: "err.setup.net_ip_twice", dhcp_bad: "err.setup.net_dhcp_bad", dhcp_outside: "err.setup.net_dhcp_outside",
  dhcp_small: "err.setup.net_dhcp_small", dhcp_overlap: "err.setup.net_dhcp_overlap", overlap_printer: "err.setup.net_overlap_printer",
  overlap_setup: "err.setup.net_overlap_setup", overlap_other: "admin.umzug.e_overlap_old", ip_no_room: "admin.umzug.e_no_room",
};
const BLOCK = { running: "admin.umzug.b_running", open_orders: "admin.umzug.b_open_orders", backup: "admin.umzug.b_backup",
  pis: "admin.umzug.b_pis" };
const FIELD = { a: "admin.umzug.f_a", b: "admin.umzug.f_b", c: "admin.umzug.f_c" };
const KIND = { printer: "admin.umzug.m_printer", device: "admin.umzug.m_device", router: "admin.umzug.m_router",
  admin_from: "admin.umzug.m_admin_from" };

const now = () => (DATA ? DATA.now + (Date.now() - AT) / 1000 : Date.now() / 1000);
const mmss = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const hhmm = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString(I18n.locale, { hour: "2-digit", minute: "2-digit" }) : "–");
const left = (key, field) => th(key, { time: "\u0000" }).replace("\u0000", `<b data-um-left="${field}"></b>`);
const chip = (cls, text) => `<span class="status ${cls}">${esc(text)}</span>`;

function inNet(ip, net) {
  const n = (x) => x.split(".").reduce((a, b) => a * 256 + Number(b), 0);
  const [base, bits] = String(net || "").split("/");
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(ip || "") || !base) return false;
  const size = 2 ** (32 - Number(bits));
  return Math.floor(n(ip) / size) === Math.floor(n(base) / size);
}

// ------------------------------------------------------------------ Einstieg auf der Seite «Pi»
function inner() {
  const m = DATA?.move || {};
  const recent = ["back", "aborted"].includes(m.phase) && now() - (m.back_at || m.started || 0) < 86400;
  if (RUNNING.has(m.phase) || (m.phase === "done" && now() < (m.until || 0)) || recent) return runView(m);
  return `<h3>${th("admin.umzug.title")}</h3>
    <p class="hint">${th("admin.umzug.entry_hint", { net: DATA?.old?.net || "" })}</p>
    <div class="form"><button type="button" class="btn" data-um="plan">${th("admin.umzug.open")}</button></div>`;
}

export function umzugCard(data) {
  if (!data) return "";
  DATA = data; AT = Date.now(); PLAN = null; CHECK = null;
  return `<div class="card" id="umzugcard">${inner()}</div>`;
}

// ------------------------------------------------------------------ Schritt 1: Planen
function planView() {
  const p = PLAN, c = CHECK, letters = Object.keys(DATA.old).filter((k) => /^[abc]$/.test(k));
  const f = (k, label, w = "") => `<label${w ? ` class="${w}"` : ""}>${th(label)}<input class="in" name="${k}" value="${esc(p[k] || "")}" inputmode="decimal" autocomplete="off"></label>`;
  const errs = c?.errors?.length ? c.errors : null, blocks = c?.blockers || [];
  const okRow = (ok, text) => `<li class="um-check"><span class="dot ${ok ? "ok" : "bad"}"></span>${text}</li>`;
  return `<div class="um-head"><h3>${th("admin.umzug.title")}</h3>${chip("pending", t("admin.umzug.step", { n: 1, name: t("admin.umzug.st_plan") }))}</div>
    <p class="hint">${th("admin.umzug.intro")}</p>
    <form class="um-plan" id="um-form">
      <label class="um-wide">${th("admin.umzug.f_net")}<input class="in" name="net" value="${esc(p.net)}" inputmode="decimal" autocomplete="off"></label>
      <p class="hint um-range">${c?.range ? th("admin.umzug.range", { first: c.range.first, last: c.range.last, count: c.range.hosts }) : "&nbsp;"}</p>
      ${f("vip", "admin.umzug.f_vip", "um-wide")}
      <div class="um-row">${letters.map((k) => f(k, FIELD[k])).join("")}</div>
      <div class="um-row">${f("dhcp_from", "admin.umzug.f_dhcp_from")}${f("dhcp_to", "admin.umzug.f_dhcp_to")}</div>
    </form>
    ${c ? `<ul class="um-checks">
      ${okRow(!errs, errs ? errs.map((e) => th(ERR[e] || "admin.umzug.e_other", { net: p.net, code: e })).join("<br>") : th("admin.umzug.c_ok"))}
      ${okRow(!(errs || []).some((e) => e.startsWith("overlap")), th("admin.umzug.c_overlap", { old: DATA.old.net }))}
      ${blocks.map((b) => okRow(false, th(BLOCK[b] || "admin.umzug.e_other", { code: b }))).join("")}</ul>
      <h4>${th("admin.umzug.auto")}</h4>
      <p>${th("admin.umzug.auto_line", { pis: c.auto.pis, phones: c.auto.phones })}</p>
      ${c.manual.length ? `<h4>${th("admin.umzug.manual")}</h4><ul class="um-checks">${c.manual.map((x) =>
        `<li class="um-check"><span class="dot pending"></span>${th(KIND[x.kind], { name: x.name, old: x.old, new: x.new })}</li>`).join("")}</ul>` : ""}` : ""}
    <div class="form dlg-foot"><button type="button" class="btn" data-um="cancel">${th("admin.common.cancel")}</button>
      <button type="button" class="btn pri" data-um="${c && !errs && !blocks.length ? "start" : "check"}">${th(c && !errs && !blocks.length ? "admin.umzug.start" : "admin.umzug.check")}</button></div>`;
}

function readForm() {
  const f = $("#um-form");
  if (!f) return;
  for (const el of f.elements) if (el.name) PLAN[el.name] = el.value.trim();
}

async function runCheck() {
  readForm();
  const plan = PLAN, res = await guard(() => api("POST", "/umzug/pruefen", plan));
  if (PLAN !== plan || !$("#um-form")) return;                 // inzwischen abgebrochen oder ein anderer Plan
  CHECK = res;
  draw(planView());
}

// ------------------------------------------------------------------ Ablauf
function runView(m) {
  const nodes = DATA.nodes || {}, keys = Object.keys(nodes), total = keys.length || 1;
  const prepared = keys.filter((k) => nodes[k]?.id === m.id && nodes[k]?.prepared).length;
  const moved = keys.filter((k) => nodes[k]?.id === m.id && nodes[k]?.net === m.new?.net).length;
  const phones = Object.entries(m.phones || {}), what = m.phase === "done" ? "url" : "ca";
  const phonesOk = phones.filter(([, s]) => s?.[what] === "ok").length;
  const notOk = phones.filter(([, s]) => s?.[what] !== "ok").map(([n, s]) => `${esc(n)}: ${esc(t(`admin.fully.st_${s?.[what] || "unreachable"}`))}`);
  const [n, name] = m.phase === "done" ? [5, "admin.umzug.st_done"] : m.phase === "switching" && DATA.all_new
    ? [5, "admin.umzug.st_confirm"] : PHASE_STEP[m.phase] || [5, m.phase === "back" ? "admin.umzug.st_back_done" : "admin.umzug.st_aborted"];
  const row = (label, cls, text, extra = "") => `<tr><td>${th(label)}</td><td>${chip(cls, text)} ${extra}</td></tr>`;
  const onNew = inNet(location.hostname, m.new?.net), newUrl = `https://${m.new?.vip}:8443`;
  let box = "";
  if (m.phase === "prepared") {
    box = `<div class="warnbox"><p>${left("admin.umzug.w_prepared", "at")}</p>
      <p><button type="button" class="linklike" data-um="cacrt">${th("admin.umzug.ca_pc")}</button></p></div>`;
  } else if (m.phase === "switching" && onNew && DATA.all_new) {
    box = `<div class="okbox"><p>${th("admin.umzug.w_confirm_here")}</p>
      <div class="form"><button type="button" class="btn pri" data-um="confirm">${th("admin.umzug.confirm")}</button></div></div>`;
  } else if (m.phase === "switching") {
    box = `<div class="warnbox um-urgent"><p><b>${th("admin.umzug.w_confirm_b")}</b> <a href="${esc(newUrl)}">${esc(newUrl)}</a>
      ${left("admin.umzug.w_confirm", "deadline")}</p>
      <p><button type="button" class="linklike" data-um="cacrt">${th("admin.umzug.ca_pc")}</button></p></div>`;
  } else if (m.phase === "done") {
    box = `<div class="okbox"><p>${th("admin.umzug.w_done", { until: new Date((m.until || 0) * 1000).toLocaleString(I18n.locale, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) })}</p></div>
      ${m.ca_key_saved ? "" : `<form class="form" id="um-cakey"><label>${th("admin.umzug.cakey_pw")}<input type="password" name="pw" minlength="12" autocomplete="new-password" required></label>
        <button class="btn pri">${th("admin.umzug.cakey_dl")}</button></form><p class="hint">${th("admin.umzug.cakey_hint")}</p>`}
      ${(m.manual || []).length ? `<h4>${th("admin.umzug.manual")}</h4><ul class="um-checks">${m.manual.map((x) =>
        `<li class="um-check"><span class="dot pending"></span>${th(KIND[x.kind], { name: x.name, old: x.old, new: x.new })}</li>`).join("")}</ul>` : ""}`;
  } else {
    box = `<div class="warnbox"><p>${th(m.phase === "back" ? "admin.umzug.w_back" : "admin.umzug.w_aborted",
      { pis: (m.missing || []).map(piName).join(", ") || "–" })}</p></div>`;
  }
  return `<div class="um-head"><h3>${th("admin.umzug.title_run", { old: m.old?.net, new: m.new?.net })}</h3>
      ${chip(m.phase === "done" ? "ok" : RUNNING.has(m.phase) ? "pending" : "bad", t("admin.umzug.step", { n, name: t(name) }))}</div>
    ${box}
    <div class="scroll"><table class="tbl um-steps"><tbody>
      ${row("admin.umzug.r_backup", m.backup_at ? "ok" : "pending", m.backup_at ? t("admin.umzug.made", { time: hhmm(m.backup_at) }) : "–")}
      ${row("admin.umzug.r_ca", prepared === total || m.phase === "switching" || m.phase === "done" ? "ok" : "pending",
        t("admin.umzug.of_pis", { n: m.phase === "prepared" ? prepared : total, total }))}
      ${row("admin.umzug.r_phones", phones.length && phonesOk === phones.length ? "ok" : "pending",
        phones.length ? t("admin.umzug.of", { n: phonesOk, total: phones.length }) : "–", notOk.length ? `<span class="hint">${notOk.join(" · ")}</span>` : "")}
      ${row("admin.umzug.r_pis", moved === total ? "ok" : "pending", `${moved}/${total}`)}
      ${row("admin.umzug.r_services", DATA.all_new || m.phase === "done" ? "ok" : "pending",
        t(DATA.all_new || m.phase === "done" ? "admin.umzug.switched" : "admin.umzug.not_yet"))}
    </tbody></table></div>
    <p class="hint">${th("admin.umzug.after")}</p>
    ${RUNNING.has(m.phase) && m.phase !== "back_requested" ? `<div class="form dlg-foot"><button type="button" class="btn danger" data-um="back">${th("admin.umzug.back")}</button></div>` : ""}`;
}

function draw(html) {
  const card = $("#umzugcard");
  LAST = html;
  if (card) card.innerHTML = html;
  tickLeft();
}

function tickLeft() {
  const m = DATA?.move || {};
  document.querySelectorAll("[data-um-left]").forEach((el) => { el.textContent = mmss((m[el.dataset.umLeft] || 0) - now()); });
}

async function reload() {
  const d = await api("GET", "/umzug").catch(() => null);
  if (!d || !$("#umzugcard") || PLAN) return;
  DATA = d; AT = Date.now();
  const f = $("#um-cakey");
  if (f && (f.contains(document.activeElement) || f.elements.pw?.value)) return tickLeft();   // Eingabe nicht verlieren
  const html = inner();
  if (html !== LAST) draw(html); else tickLeft();
}

// ------------------------------------------------------------------ Klicks
export function bindUmzug() {
  const card = $("#umzugcard");
  if (!card) return;
  clearInterval(TIMER);
  tickLeft();
  TIMER = setInterval(() => {
    if (!$("#umzugcard")) { clearInterval(TIMER); return; }
    tickLeft();
    if (!PLAN && Math.round(Date.now() / 1000) % 5 === 0) reload();
  }, 1000);
  card.addEventListener("input", (e) => { if (e.target.closest("#um-form")) readForm(); });
  card.addEventListener("submit", async (e) => {
    if (e.target.id !== "um-cakey") return;
    e.preventDefault();
    const r = await guard(() => api("POST", "/umzug/ca-key", { password: e.target.pw.value }, { raw: true }));
    if (!r) return;
    const url = URL.createObjectURL(await r.blob());
    Object.assign(document.createElement("a"), { href: url, download: "ca.key" }).click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    toast(t("admin.umzug.cakey_done"));
    reload();
  });
  card.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-um]");
    if (!b) return;
    const a = b.dataset.um;
    if (a === "plan") {
      PLAN = { ...DATA.defaults };
      for (const k of ["a", "b", "c"]) if (!(k in DATA.old)) delete PLAN[k];
      CHECK = null;
      draw(planView());
    } else if (a === "cancel") {
      PLAN = null; CHECK = null;
      draw(inner());
    } else if (a === "check") {
      await runCheck();
    } else if (a === "start") {
      readForm();
      if (!confirm(t("admin.umzug.start_q", { old: DATA.old.net, new: PLAN.net }))) return;
      const r = await guard(() => api("POST", "/umzug/start", PLAN), t("admin.umzug.started"));
      if (r) { PLAN = null; CHECK = null; await reload(); }
    } else if (a === "confirm") {
      if (await guard(() => api("POST", "/umzug/bestaetigen"), t("admin.umzug.confirmed"))) reload();
    } else if (a === "back") {
      if (!confirm(t("admin.umzug.back_q"))) return;
      if (await guard(() => api("POST", "/umzug/zurueck"))) reload();
    } else if (a === "cacrt") {
      download("/umzug/ca.crt", "festpos-ca-neu.crt");
    }
  });
}
