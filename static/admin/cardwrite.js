/* FestPOS – Verwaltung › Pi & Netz › «SD-Karte schreiben» (T10): der Pi schreibt eine neue Karte im USB-Kartenleser –
   für Pi B/C (Auf 3 Pi erweitern) oder als Ersatzkarte. Schritte: Karte → Pi → Schreiben (Buchstabe eintippen) →
   Fortschritt → Fertig. Server: app/cards.py (/cluster/karten, /cluster/karte), root: deploy/festpos-karte. */
import { $, api, esc, guard, piName, toast } from "./core.js";
import { RENDER, view } from "./shell.js";

let W = null;     // { step, letter, dev, devices, info, poll, from: null | {mode, here, resume} }
const STEPS = ["admin.cardw.s_card", "admin.cardw.s_pi", "admin.cardw.s_write"];
const REASON = { system: "admin.cardw.r_system", not_usb: "admin.cardw.r_not_usb", not_removable: "admin.cardw.r_not_removable",
  no_card: "admin.cardw.r_no_card", mounted: "admin.cardw.r_mounted", too_small: "admin.cardw.r_too_small", stick: "admin.cardw.r_stick" };
const STEP_NAME = { start: "admin.cardw.p_start", pruefen: "admin.cardw.p_check", partitionen: "admin.cardw.p_parts",
  kopieren: "admin.cardw.p_copy", anpassen: "admin.cardw.p_adjust", boot: "admin.cardw.p_boot", kontrolle: "admin.cardw.p_verify",
  auswerfen: "admin.cardw.p_eject" };
const gb = (b) => (b / 1e9).toLocaleString(I18n.locale, { maximumFractionDigits: b < 1e10 ? 1 : 0 });

/** Ablauf öffnen. letter: vorgewählt (aus «Auf 3 Pi erweitern»/«Ersatzkarte»), from: wohin «Fertig» zurückführt. */
export function startCardWrite({ letter = "", from = null } = {}) {
  stop();
  W = { step: 0, letter, dev: null, devices: null, info: null, from };
  draw();
}
function stop() {
  if (W?.poll) clearInterval(W.poll);
  W = null;
}
function back() {
  const from = W?.from;
  stop();
  if (from?.resume) from.resume();
  else RENDER.system();
}

function frame(body, next, { nextOff = false, backOn = true, cancel = true, pills = true } = {}) {
  const st = W.step;
  return `<h2>${th("admin.cardw.title")}</h2><div class="card wiz" id="cwiz">
    <div class="wtop">${pills ? `<ol class="wsteps">${STEPS.map((k, i) =>
      `<li class="${i === st ? "on" : i < st ? "done" : ""}">${th(k, { n: i + 1 })}</li>`).join("")}</ol>` : "<span></span>"}
      ${cancel ? `<button type="button" class="linklike" id="c-cancel">${th("admin.common.cancel")}</button>` : ""}</div>
    ${body}
    <div class="wiz-foot">${backOn && st > 0 && st < 3 ? `<button type="button" class="btn" id="c-back">${th("admin.devices.back")}</button>` : ""}
      ${next ? `<button type="button" class="btn pri" id="c-next" ${nextOff ? "disabled" : ""}>${esc(next)}</button>` : ""}</div></div>`;
}

async function load() {
  const r = await api("GET", "/cluster/karten").catch((e) => ({ error: e }));
  if (!W) return;
  if (r.error) { W.error = r.error.message || String(r.error); return; }
  W.error = "";
  W.info = r;
  W.devices = r.devices;
  if (W.dev && !r.devices.some((d) => d.device === W.dev.device && d.allowed && d.serial === W.dev.serial && d.size === W.dev.size)) W.dev = null;
  if (r.status?.state === "running" && W.step < 3) W.step = 3;
}

function devLabel(d) {
  return [d.vendor, d.model].filter(Boolean).join(" ") || d.device;
}
function devList() {
  const ds = W.devices || [];
  if (!ds.length) return `<p class="hint" id="c-none">${th("admin.cardw.none")}</p>`;
  return `<div class="choice" id="c-devs">${ds.map((d, i) => {
    const parts = d.parts.map((p) => p.label || p.fs).filter(Boolean);
    const sub = [d.size ? `${gb(d.size)} GB` : "", parts.length ? t("admin.cardw.contains", { list: parts.join(", ") }) : "",
      d.allowed ? "" : t(REASON[d.reason] || "admin.cardw.r_other", { need: gb(d.need || 0) })].filter(Boolean).join(" · ");
    const on = W.dev && W.dev.device === d.device;
    return `<label class="${d.allowed ? "" : "off"}"><input type="radio" name="cdev" value="${i}" ${d.allowed ? "" : "disabled"} ${on ? "checked" : ""}>
      <b>${esc(devLabel(d))}</b><span>${esc(sub)}</span></label>`;
  }).join("")}</div>`;
}

function letterChoices() {
  const info = W.info || {};
  return `<div class="choice" id="c-letters">${info.letters.map((l) => {
    const spare = info.single ? l === info.node : true;
    const sub = t(spare ? (info.single ? "admin.cardw.l_spare_single" : "admin.cardw.l_spare") : "admin.cardw.l_new");
    return `<label><input type="radio" name="cl" value="${l}" ${W.letter === l ? "checked" : ""}>
      <b>${th("admin.cardw.for_pi", { pi: piName(l) })}</b><span>${esc(sub)}</span></label>`;
  }).join("")}</div>`;
}

async function draw() {
  if (!W || view !== "system") return stop();
  const main = $("#main");
  if (!W.info) {
    main.innerHTML = frame(`<p class="hint">${th("admin.common.loading")}</p>`, null);
    await load();
    if (!W || view !== "system") return;
  }
  const info = W.info || {};
  const here = piName(info.node || "a");
  if (W.error) {
    main.innerHTML = frame(`<p class="err">${esc(W.error)}</p>`, t("admin.cardw.search"));
  } else if (!info.helper) {
    main.innerHTML = frame(`<p class="err">${th("admin.expand.no_helper")}</p>`, null);
  } else if (W.step === 0) {
    main.innerHTML = frame(`<h3>${th("admin.cardw.h_card", { pi: here })}</h3>
      <p class="hint">${th("admin.cardw.card_hint", { pi: here })}</p>
      ${info.missing?.length ? `<p class="err">${th("admin.cardw.missing", { list: info.missing.join(", ") })}</p>` : ""}
      <div id="c-list">${devList()}</div>
      ${info.small_hint ? `<p class="hint">${th("admin.cardw.small_hint")}</p>` : ""}
      <p class="meta">${th("admin.cardw.searching")}</p>`, t("admin.expand.next"), { nextOff: !W.dev });
    if (!W.poll) W.poll = setInterval(refreshList, 3000);
  } else if (W.step === 1) {
    const warnRun = W.letter && (info.running || []).includes(W.letter);
    main.innerHTML = frame(`<h3>${th("admin.cardw.h_pi")}</h3>${letterChoices()}
      ${warnRun ? `<div class="warnbox alarm" id="c-runwarn">${th("admin.cardw.running_warn", { pi: piName(W.letter) })}</div>` : ""}
      <div class="warnbox ${info.busy ? "alarm" : ""}" id="c-busy">${th(info.busy ? "admin.cardw.busy" : "admin.cardw.not_in_use")}</div>`,
    t("admin.expand.next"), { nextOff: !W.letter });
  } else if (W.step === 2) {
    const L = piName(W.letter);
    main.innerHTML = frame(`<h3>${th("admin.cardw.h_write")}</h3>
      <div class="warnbox alarm"><p>${th("admin.cardw.erase", { card: devLabel(W.dev), size: gb(W.dev.size) })}</p></div>
      <div class="form col"><label>${th("admin.cardw.type_letter", { letter: L })}
        <input id="c-confirm" autocomplete="off" autocapitalize="characters" maxlength="1" inputmode="text" style="max-width:120px;font-size:24px;text-align:center"></label></div>`,
    t("admin.cardw.write", { pi: L }), { nextOff: true });
  } else if (W.step === 3) {
    main.innerHTML = frame(progressBody(info.status), null, { backOn: false, cancel: false, pills: false });
    if (!W.poll) W.poll = setInterval(refreshStatus, 2000);
  } else {
    main.innerHTML = frame(doneBody(info), doneNext(info), { backOn: false, cancel: false, pills: false });
  }
  bind();
}

function progressBody(s) {
  const pct = s && s.total ? Math.min(99, Math.floor((s.done / s.total) * 100)) : 0;
  const rest = s && s.total && s.done > 0 && s.step === "kopieren"
    ? Math.max(1, Math.round(((Date.now() / 1000 - s.started) / s.done) * (s.total - s.done) / 60)) : null;
  return `<h3>${th("admin.cardw.h_progress", { pi: piName(s?.letter || W.letter) })}</h3>
    <p class="progress" id="c-step">${th(STEP_NAME[s?.step] || "admin.cardw.p_start")}</p>
    <div class="pbar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
    <p class="meta" id="c-bytes">${s && s.total ? th("admin.cardw.bytes", { done: gb(s.done), total: gb(s.total) }) : ""}
      ${rest ? " · " + th("admin.cardw.rest", { count: rest }) : ""}</p>
    <p class="hint">${th("admin.cardw.progress_hint")}</p>
    <div class="form"><button type="button" class="btn danger" id="c-abort">${th("admin.cardw.abort")}</button></div>`;
}

function doneBody(info) {
  const s = info.status || {};
  const L = piName(s.letter || W.letter);
  if (s.state === "done") {
    const what = info.single && s.letter !== info.node ? "admin.cardw.next_expand" : info.single ? "admin.cardw.next_spare_single" : "admin.cardw.next_spare";
    return `<h3 class="okmsg" id="c-done">${th("admin.cardw.done", { pi: L })}</h3><p>${th(what, { pi: L })}</p>`;
  }
  const key = s.state === "cancelled" ? "admin.cardw.cancelled" : "admin.cardw.failed";
  return `<h3 class="err" id="c-done">${th(key, { pi: L, code: s.code || "?" })}</h3><p class="hint">${th("admin.cardw.failed_hint")}</p>`;
}
function missingLetters(info) {
  if (!W.from || W.from.mode !== "expand") return [];
  return ["a", "b", "c"].filter((l) => l !== W.from.here && !(info.written || {})[l]);
}
function doneNext(info) {
  const s = info.status || {};
  if (s.state !== "done") return t("admin.cardw.again");
  const miss = missingLetters(info);
  if (miss.length) return t("admin.cardw.write_next", { pi: piName(miss[0]) });
  return W.from?.mode === "expand" ? t("admin.cardw.to_switch") : t("admin.expand.done");
}

async function refreshList() {
  if (!W || W.step !== 0 || view !== "system") { if (W?.poll && W.step !== 0) { clearInterval(W.poll); W.poll = null; } return; }
  await load();
  if (!W || W.step !== 0) return;
  if (W.info?.status?.state === "running") { clearInterval(W.poll); W.poll = null; return draw(); }
  const box = $("#c-list");
  if (box) { box.innerHTML = devList(); bindDevs(); }
  const n = $("#c-next");
  if (n) n.disabled = !W.dev;
}

async function refreshStatus() {
  if (!W || view !== "system") return stop();
  let r;
  try { r = await api("GET", "/cluster/karte"); } catch { return; }
  if (!W) return;
  W.info = { ...W.info, ...r };
  const s = r.status;
  if (!s || s.state !== "running") {
    clearInterval(W.poll); W.poll = null;
    W.step = 4;
    return draw();
  }
  const box = $("#cwiz");
  if (box && W.step === 3) { W.step = 3; $("#main").innerHTML = frame(progressBody(s), null, { backOn: false, cancel: false, pills: false }); bind(); }
}

function bindDevs() {
  document.querySelectorAll('#c-devs input[name="cdev"]').forEach((r) => r.addEventListener("change", () => {
    W.dev = W.devices[Number(r.value)];
    const n = $("#c-next");
    if (n) n.disabled = false;
  }));
}

function bind() {
  $("#c-cancel")?.addEventListener("click", back);
  $("#c-back")?.addEventListener("click", () => { W.step -= 1; draw(); });
  bindDevs();
  document.querySelectorAll('#c-letters input[name="cl"]').forEach((r) => r.addEventListener("change", () => { W.letter = r.value; draw(); }));
  const conf = $("#c-confirm");
  if (conf) {
    conf.addEventListener("input", () => { $("#c-next").disabled = conf.value.trim().toLowerCase() !== W.letter; });
    conf.focus();
  }
  $("#c-abort")?.addEventListener("click", async () => {
    if (!confirm(t("admin.cardw.abort_q"))) return;
    await guard(() => api("POST", "/cluster/karte/abbrechen"));
  });
  $("#c-next")?.addEventListener("click", async () => {
    if (W.error) { W.info = null; return draw(); }
    if (W.step === 0) {
      clearInterval(W.poll); W.poll = null;
      W.step = 1;
      return draw();
    }
    if (W.step === 1) { W.step = 2; return draw(); }
    if (W.step === 2) {
      const btn = $("#c-next");
      btn.disabled = true;
      const r = await guard(() => api("POST", "/cluster/karte", { device: W.dev.device, serial: W.dev.serial, size: W.dev.size,
        letter: W.letter, confirm: $("#c-confirm").value.trim() }));
      if (!r) { btn.disabled = false; return; }
      toast(t("admin.cardw.started", { pi: piName(W.letter) }));
      W.info = { ...W.info, status: { state: "running", step: "start", letter: W.letter, done: 0, total: 0 } };
      W.step = 3;
      return draw();
    }
    // Fertig-Seite
    const info = W.info || {};
    if (info.status?.state !== "done") { const from = W.from; startCardWrite({ letter: W.letter, from }); return; }
    const miss = missingLetters(info);
    if (miss.length) { const from = W.from; startCardWrite({ letter: miss[0], from }); return; }
    back();
  });
}
