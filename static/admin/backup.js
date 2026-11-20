/* FestPOS – Verwaltung › Wartung › Sicherung (T8): Prüfliste nach dem Einspielen, Sicherungs-Passwort (anzeigen,
   drucken A4 und Bon), Sicherung herunterladen und einspielen – immer verschlüsselt. Server: app/maintenance.py,
   app/restore_check.py. Die Prüfliste wird alle 15 s neu gerechnet, solange die Seite offen ist. */
import { $, api, errLabel, esc, guard, piName, toast } from "./core.js";
import { page } from "./shell.js";

let BK = { pw: null, check: null, printers: [], file: null, needPw: false };
let timer = null;
const MIN_PW = 12;
const MAGIC = "FESTPOS-SICHERUNG\n";
const dt = (sec) => (sec ? I18n.fmtDateTime(sec * 1000) : "–");
const ago = (sec) => Math.max(0, Math.round((Date.now() / 1000 - sec) / 60));

// ------------------------------------------------------------------ Seite
export const backupCards = () => `<div data-in="backup" id="bk"></div>`;

export async function backupInit() {
  clearInterval(timer);
  await backupDraw();
  timer = setInterval(async () => {
    if (page !== "maint" || !$("#bk-check") || document.hidden) { if (page !== "maint") clearInterval(timer); return; }
    BK.check = await api("GET", "/restore-check").catch(() => BK.check);
    const box = $("#bk-check");
    if (box && BK.check?.active) box.outerHTML = checkCard(BK.check);
  }, 15000);
}

async function backupDraw() {
  const [pw, check, printers] = await Promise.all([api("GET", "/backup/password").catch(() => null),
    api("GET", "/restore-check").catch(() => null), api("GET", "/printers").catch(() => [])]);
  Object.assign(BK, { pw, check, printers: (printers || []).filter((p) => p.enabled) });
  const el = $("#bk");
  if (!el) return;
  el.innerHTML = `${check?.active ? checkCard(check) : ""}${pwCard(pw)}${downloadCard()}${restoreCard()}${autoCard()}`;
  bind(el);
}

// ------------------------------------------------------------------ Prüfliste
// Punkt -> Zustand -> [Titel, Untertitel] (ausgeschrieben, damit tools/i18n_check die Schlüssel findet)
const ITEM = {
  device_ca: { ok: ["admin.restore.dca_ok", "admin.restore.dca_ok_sub"], bad: ["admin.restore.dca_bad", "admin.restore.dca_bad_sub"],
    wait: ["admin.restore.keys_wait", "admin.restore.keys_wait_sub"], manual: ["admin.restore.dca_manual", "admin.restore.dca_manual_sub"] },
  server_ca: { ok: ["admin.restore.sca_ok", "admin.restore.sca_ok_sub"], warn: ["admin.restore.sca_warn", "admin.restore.sca_warn_sub"],
    wait: ["admin.restore.keys_wait", "admin.restore.keys_wait_sub"] },
  device_ca_key: { ok: ["admin.restore.dkey_ok", "admin.restore.dkey_ok_sub"], warn: ["admin.restore.dkey_warn", "admin.restore.dkey_warn_sub"] },
  ledger_key: { ok: ["admin.restore.lkey_ok", "admin.restore.lkey_ok_sub"], warn: ["admin.restore.lkey_warn", "admin.restore.lkey_warn_sub"],
    wait: ["admin.restore.keys_wait", "admin.restore.keys_wait_sub"] },
  keys: { warn: ["admin.restore.keys_differ", null] },
  printer: { ok: ["admin.restore.printer_ok", null], bad: ["admin.restore.printer_bad", "admin.restore.printer_bad_sub"],
    wait: ["admin.restore.printer_wait", null] },
  phones: { ok: ["admin.restore.phones_ok", null], warn: ["admin.restore.phones_warn", "admin.restore.phones_warn_sub"] },
  time: { ok: ["admin.restore.time_ok", null], bad: ["admin.restore.time_bad", "admin.restore.time_bad_sub"] },
  secrets: { ok: ["admin.restore.secrets_ok", null], manual: ["admin.restore.secrets_missing", "admin.restore.secrets_missing_sub"] },
  apk: { ok: ["admin.restore.apk_ok", null], warn: ["admin.restore.apk_warn", "admin.restore.apk_warn_sub"],
    wait: ["admin.restore.apk_wait", "admin.restore.apk_wait_sub"] },
  dhcp: { ok: ["admin.restore.dhcp_ok", "admin.restore.dhcp_ok_sub"], warn: ["admin.restore.dhcp_warn", "admin.restore.dhcp_warn_sub"] },
  usb: { ok: ["admin.restore.usb_ok", "admin.restore.usb_ok_sub"], warn: ["admin.restore.usb_warn", "admin.restore.usb_warn_sub"],
    manual: ["admin.restore.usb_warn", "admin.restore.usb_warn_sub"] },
  receipts: { ok: ["admin.restore.receipts", null], warn: ["admin.restore.receipts", null] },
};
const SECRET = { fully: "admin.restore.s_fully", wifi: "admin.restore.s_wifi", router: "admin.restore.s_router",
  update: "admin.restore.s_update" };

function timeState(i) {
  // Uhrzeit: zusätzlich mit diesem Gerät vergleichen (wie Einstellungen › Uhrzeit)
  const diff = Math.round(i.server_time - Date.now() / 1000);
  return { state: i.state === "bad" || Math.abs(diff) > 120 ? "bad" : "ok", diff };
}

function itemText(i) {
  const [title, sub] = ITEM[i.id]?.[i.state] || [null, null];
  const p = { name: i.name, total: i.total, online: i.online, count: i.count, written: i.written ?? 0,
    names: (i.missing || []).map((x) => (SECRET[x] ? t(SECRET[x]) : x)).join(", "),
    last: i.last_backup, next: i.next, pi: piName(i.pi), min: i.last_ok ? ago(i.last_ok) : "–", sec: Math.abs(i.diff || 0) };
  let s = sub ? t(sub, p) : "";
  if (i.id === "time" && i.state === "ok") s = t("admin.restore.time_ok_sub", p);
  if (i.id === "printer" && i.state === "bad") s = `${errLabel(i.reason)} – ${s}`;
  if (i.id === "receipts") s = t(i.case === "new" ? "admin.restore.receipts_new" : "admin.restore.receipts_same", p);
  if (i.id === "keys") s = t(i.cluster ? "admin.restore.keys_cluster" : "admin.restore.keys_single");
  return { title: title ? t(title, p) : i.id, sub: s };
}

function itemExtra(i) {
  if (i.id === "printer" && i.state === "bad") return `<a href="#devices/printers" data-page="devices/printers">${th("admin.nav.printers")}</a>`;
  if (i.id === "phones" && i.state !== "ok") return `<a href="#devices/list" data-page="devices/list">${th("admin.tab.phones")}</a>`;
  if (i.id === "secrets" && i.state !== "ok") {
    const [pg, key] = i.missing.some((m) => m === "fully" || m === "wifi") ? ["devices/setup", "admin.nav.dev_settings"]
      : i.missing.includes("router") ? ["devices/net", "admin.tab.net"] : ["maint/update", "admin.nav.maint"];
    return `<a href="#${pg}" data-page="${pg}">${th(key)}</a>`;
  }
  if (i.id === "time" && timeState(i).state === "bad") return `<div class="form"><button type="button" class="btn sm" data-bk="time">${th("admin.time.take")}</button></div>`;
  if (i.id === "apk" && i.state === "warn") {           // hochladen: Geräte & Netz › Einstellungen › Karte Fully-APK (T6)
    return i.helper ? `<div class="form"><button type="button" class="btn sm" data-bk="apk">${th("admin.restore.apk_take")}</button></div>`
      : `<a href="#devices/setup" data-page="devices/setup">${th("admin.nav.dev_settings")}</a>`;
  }
  if (i.id === "receipts" && i.case === "new") {
    return `<form class="form" data-bk-form="receipt"><label>${th("admin.restore.receipt_next")}<input name="next" type="number"
      min="${i.next}" value="${i.next}" style="width:120px"></label><button class="btn sm">${th("admin.restore.receipt_take")}</button></form>`;
  }
  if (i.id === "keys" && !i.cluster) {
    return `<form class="form" data-bk-form="keys"><label>${th("admin.backup.rs_file")}<input type="file" name="file" accept=".festpos" required></label>
      <label>${th("admin.backup.rs_pw")}<input name="password" autocomplete="off" style="width:240px"></label>
      <button class="btn danger sm">${th("admin.restore.keys_take")}</button></form>`;
  }
  return "";
}

const MANUAL = {
  m_test: (m) => [t("admin.restore.m_test", { state: t(m.on ? "admin.restore.on" : "admin.restore.off") }),
    `${th("admin.restore.m_test_sub")} <a href="#pos/app" data-page="pos/app">${th("admin.restore.m_test_link")}</a>`],
  m_orders: (m) => [t("admin.restore.m_orders", { at: m.backup_at ? I18n.fmtTime(m.backup_at * 1000) : "–" }), th("admin.restore.m_orders_sub")],
  m_try: () => [t("admin.restore.m_try"), ""],
  m_new: () => [t("admin.restore.m_new"), th("admin.restore.m_new_sub")],
};

function checkCard(c) {
  const items = c.items.map((i) => (i.id === "time" ? { ...i, state: timeState(i).state, diff: timeState(i).diff } : i));
  const done = items.filter((i) => i.state === "ok").length;
  const rows = items.map((i) => {
    const { title, sub } = itemText(i);
    return `<li class="${i.state}"><b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}${itemExtra(i)}</li>`;
  }).join("");
  const manual = c.manual.map((m) => {
    const [title, sub] = MANUAL[m.id](m);
    return `<li class="man"><label class="check"><input type="checkbox" data-bk-man="${m.id}"> <b>${esc(title)}</b></label>
      <span>${sub}</span></li>`;
  }).join("");
  const meta = t("admin.restore.meta", { at: dt(c.at), by: c.by || "?", backup: dt(c.backup_at), pi: piName(c.backup_node || "?") });
  const body = `<p class="meta">${esc(meta)} · <b>${th(c.case === "new" ? "admin.restore.case_new" : "admin.restore.case_same")}</b></p>
    <div class="progress">${th("admin.restore.progress", { done, total: items.length })}</div>
    <ul class="chk">${rows}<li class="chk-head">${th("admin.restore.by_hand")}</li>${manual}</ul>`;
  if (c.done) {
    return `<div class="card" id="bk-check"><h3>${th("admin.restore.title")}</h3>
      <p class="meta">${th("admin.restore.done_at", { at: dt(c.done) })}</p><details><summary>${th("admin.common.show")}</summary>${body}</details></div>`;
  }
  return `<div class="card" id="bk-check"><h3>${th("admin.restore.title")}</h3>${body}
    <div class="form"><button type="button" class="btn pri" data-bk="done">${th("admin.restore.all_done")}</button></div></div>`;
}

// ------------------------------------------------------------------ Sicherungs-Passwort
function pwCard(pw) {
  if (!pw) return "";
  const printers = BK.printers.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  return `<div class="card" id="bk-pw"><h3>${th("admin.bpw.title")}</h3>
    <p class="hint">${th("admin.bpw.hint")}</p>
    <div class="pwline"><span class="pw" id="bk-pwtext" translate="no">${esc(pw.prefix)}-••••-••••-••••-••••-••••</span>
      <button type="button" class="btn sm" data-bk="pw-show" aria-pressed="false">${th("admin.bpw.show")}</button></div>
    <p class="meta">${th("admin.bpw.created", { at: dt(pw.at) })}</p>
    ${pw.printed ? `<p class="meta">${th("admin.bpw.printed", { at: dt(pw.printed) })}</p>` : `<div class="warnbox">${th("admin.bpw.unprinted")}</div>`}
    <div class="form">
      <button type="button" class="btn pri" data-bk="pw-a4">${th("admin.bpw.print_a4")}</button>
      ${printers ? `<label>${th("admin.bpw.bon_printer")}<select id="bk-bonp">${printers}</select></label>
        <button type="button" class="btn" data-bk="pw-bon">${th("admin.bpw.print_bon")}</button>` : ""}
    </div>
    <div class="form" style="margin-top:10px">
      ${pw.printed ? "" : `<button type="button" class="btn" data-bk="pw-printed">${th("admin.bpw.confirm_printed")}</button>`}
      <button type="button" class="btn danger sm" data-bk="pw-new">${th("admin.bpw.new")}</button>
    </div></div>`;
}

async function pwFull() {
  return guard(() => api("GET", "/backup/password?show=1"));
}

function printSheet(p) {
  // Druckansicht A4: eigene Fläche über der Seite, beim Drucken nur sie (admin.css @media print)
  const groups = p.password.split("-");
  const box = document.createElement("div");
  box.className = "pwprint";
  box.innerHTML = `<div class="pwprint-bar noprint"><button type="button" class="btn pri" data-pp="print">${th("admin.bpw.p_print")}</button>
      <button type="button" class="btn" data-pp="close">${th("admin.common.close")}</button></div>
    <div class="pwsheet">
      <h1>${th("admin.bpw.p_title")}</h1>
      <p class="pwsub">${esc(t("admin.bpw.p_sub", { event: p.event || "FestPOS", at: dt(p.at), pi: piName(p.node) }))}</p>
      <div class="pwbox"><div><div class="pwbig" translate="no">${esc(groups.slice(0, 3).join("-"))}<br>${esc(groups.slice(3).join("-"))}</div>
        <p>${th("admin.bpw.p_case")}</p></div>${p.svg || ""}</div>
      <h2>${th("admin.bpw.p_what")}</h2><p>${th("admin.bpw.p_what_text")}</p>
      <h2>${th("admin.bpw.p_how")}</h2>
      <ol><li>${th("admin.bpw.p_s1")}</li><li>${th("admin.bpw.p_s2")}</li><li>${th("admin.bpw.p_s3")}</li><li>${th("admin.bpw.p_s4")}</li></ol>
      <p class="pwfoot">${th("admin.bpw.p_foot")}</p>
    </div>`;
  const close = () => { box.remove(); document.body.classList.remove("printing-pw"); };
  box.addEventListener("click", (e) => {
    const b = e.target.closest("[data-pp]");
    if (b?.dataset.pp === "print") window.print();
    if (b?.dataset.pp === "close") close();
  });
  document.body.classList.add("printing-pw");
  document.body.append(box);
}

// ------------------------------------------------------------------ Herunterladen
const downloadCard = () => `<div class="card" id="bk-dl"><h3>${th("admin.backup.dl_title")}</h3>
  <p class="hint">${th("admin.backup.dl_hint", { count: MIN_PW })}</p>
  <form class="form" id="bk-dlform">
    <label>${th("admin.backup.dl_pw")}<input type="password" name="pw" autocomplete="new-password" minlength="${MIN_PW}" required style="width:220px"></label>
    <label>${th("admin.backup.dl_pw2")}<input type="password" name="pw2" autocomplete="new-password" required style="width:220px"></label>
    <button class="btn pri">${th("admin.backup.dl_btn")}</button>
  </form></div>`;

async function downloadBackup(form) {
  const pw = form.pw.value, pw2 = form.pw2.value;
  if (pw.length < MIN_PW) return toast(t("admin.backup.dl_short", { count: MIN_PW }), true);
  if (pw !== pw2) return toast(t("admin.backup.dl_mismatch"), true);
  const btn = form.querySelector("button");
  btn.disabled = true;
  const r = await guard(() => api("POST", "/backup", { password: pw }, { raw: true }));
  btn.disabled = false;
  if (!r) return;
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "")?.[1] || "festpos-sicherung.festpos";
  const url = URL.createObjectURL(await r.blob());
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  form.reset();
}

// ------------------------------------------------------------------ Einspielen
const restoreCard = () => `<div class="card" id="bk-rs"><h3>${th("admin.backup.rs_title")}</h3>
  <p class="hint">${th("admin.backup.rs_hint")}</p>
  <p class="hint" id="bk-rsfiles">${th("admin.backup.rs_hint_files")}</p>
  <form class="form" id="bk-rsform">
    <label>${th("admin.backup.rs_file")}<input type="file" name="file" accept=".festpos,.sqlite,.db" required></label>
  </form>
  <div id="bk-rsinfo"></div></div>`;

async function fileHead(f) {
  const bytes = new Uint8Array(await f.slice(0, 4096).arrayBuffer());
  const text = String.fromCharCode(...bytes);
  if (text.startsWith(MAGIC)) {
    try { return { kind: "enc", head: JSON.parse(text.slice(MAGIC.length).split("\n")[0]) }; } catch { return { kind: "bad" }; }
  }
  return { kind: text.startsWith("SQLite format 3\u0000") ? "old" : "bad" };
}

function restoreInfo() {
  const f = BK.file, el = $("#bk-rsinfo");
  if (!el) return;
  if (!f) { el.innerHTML = ""; return; }
  if (f.kind === "bad") { el.innerHTML = `<p class="err">${th("admin.backup.rs_not")}</p>`; return; }
  const h = f.head || {};
  // T17: automatisch (USB-Stick, Sicherungs-Passwort vom Ausdruck) oder heruntergeladen (eigenes Passwort)
  const manual = h.kind ? h.kind === "manual" : /^festpos-(sicherung|manuell)-/.test(f.name);
  const info = f.kind === "enc" ? t(manual ? "admin.backup.rs_info_manual" : "admin.backup.rs_info_auto",
    { pi: piName(h.node || "?"), at: dt(h.at), app: h.app || "?" }) : t("admin.backup.rs_old");
  const pwState = f.kind !== "enc" ? "" : BK.needPw
    ? `<span class="status pending">${th(manual ? "admin.backup.rs_pw_needed_manual" : "admin.backup.rs_pw_needed")}</span>`
    : `<span class="meta">${th(manual ? "admin.backup.rs_own_manual" : "admin.backup.rs_own")}</span>`;
  const scan = "BarcodeDetector" in window && navigator.mediaDevices?.getUserMedia;
  el.innerHTML = `<div class="filebox"><b translate="no">${esc(f.name)}</b><span class="meta">${esc(info)}</span>${pwState}</div>
    <form class="form" id="bk-rsgo">
      ${BK.needPw ? `<label>${th("admin.backup.rs_pw")}<input name="password" id="bk-rspw" autocomplete="off" required style="width:260px"></label>
        ${scan ? `<button type="button" class="btn" data-bk="scan">${th("admin.backup.rs_scan")}</button>` : ""}` : ""}
      <button class="btn danger">${th("admin.backup.restore_btn")}</button>
    </form>`;
  $("#bk-rspw")?.focus();
}

async function restore(form) {
  if (!confirm(t("admin.backup.restore_confirm"))) return;
  const fd = new FormData();
  fd.append("file", BK.file.blob);
  if (form.password?.value) fd.append("password", form.password.value);
  const btn = form.querySelector(".btn.danger");
  btn.disabled = true;
  let r;
  try { r = await api("POST", "/restore?confirm=JA", fd); }
  catch (e) {
    btn.disabled = false;
    if (e.status === 428) { BK.needPw = true; restoreInfo(); }
    toast(e.message, true);
    return;
  }
  BK.file = null; BK.needPw = false;
  toast(t("admin.backup.restored", { products: r.products, orders: r.orders }));
  if (r.restart) return waitRestart();
  await backupDraw();
  $("#bk-check")?.scrollIntoView({ behavior: "smooth" });
}

function waitRestart() {
  // Der Pi spielt die Schlüssel ein und startet neu: warten, bis er wieder antwortet, dann neu laden
  const box = document.createElement("div");
  box.className = "qrfull restart";
  box.innerHTML = `<div class="qrfull-head"><b>${th("admin.backup.rs_restart_title")}</b></div><p>${th("admin.backup.rs_restart")}</p>`;
  document.body.append(box);
  const started = Date.now();
  let down = false;
  const poll = setInterval(async () => {
    let up = false;
    try { up = (await fetch("/api/health", { cache: "no-store" })).ok; } catch { up = false; }
    if (!up) down = true;
    if ((up && down) || Date.now() - started > 240000) { clearInterval(poll); location.reload(); }
  }, 3000);
}

async function scanQr() {
  // Passwort vom Ausdruck mit der Kamera (nur wo der Browser QR-Codes selbst lesen kann)
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false }); }
  catch { return toast(t("admin.backup.rs_no_camera"), true); }
  const box = document.createElement("div");
  box.className = "qrfull";
  box.innerHTML = `<div class="qrfull-head"><b>${th("admin.backup.rs_scan")}</b><button type="button" class="btn">${th("admin.common.cancel")}</button></div>
    <div class="qrfull-qr"><video playsinline muted></video></div>`;
  document.body.append(box);
  const video = box.querySelector("video");
  video.srcObject = stream;
  await video.play().catch(() => {});
  const det = new window.BarcodeDetector({ formats: ["qr_code"] });
  let stop = false;
  const end = () => { stop = true; stream.getTracks().forEach((tr) => tr.stop()); box.remove(); };
  box.querySelector("button").addEventListener("click", end);
  while (!stop) {
    try {
      const codes = await det.detect(video);
      if (codes[0]?.rawValue) { $("#bk-rspw").value = codes[0].rawValue; end(); return; }
    } catch { /* nächstes Bild */ }
    await new Promise((res) => setTimeout(res, 250));
  }
}

// ------------------------------------------------------------------ Automatisch auf USB-Stick
const autoCard = () => `<div class="card" id="bk-auto"><h3>${th("admin.backup.auto_title")}</h3>
  <p class="hint">${th("admin.backup.auto_hint")}</p></div>`;

// ------------------------------------------------------------------ Knöpfe
function bind(el) {
  el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-bk]");
    if (!b) return;
    const act = b.dataset.bk;
    if (act === "pw-show") {
      const shown = b.getAttribute("aria-pressed") === "true";
      if (shown) { $("#bk-pwtext").textContent = `${BK.pw.prefix}-••••-••••-••••-••••-••••`; }
      else { const p = await pwFull(); if (!p) return; $("#bk-pwtext").textContent = p.password; }
      b.setAttribute("aria-pressed", String(!shown));
      b.textContent = t(shown ? "admin.bpw.show" : "admin.bpw.hide");
    } else if (act === "pw-a4") {
      const p = await pwFull();
      if (p) printSheet(p);
    } else if (act === "pw-bon") {
      await guard(() => api("POST", "/backup/password/bon", { printer_id: Number($("#bk-bonp").value) }), t("admin.bpw.bon_done"));
    } else if (act === "pw-printed") {
      if (await guard(() => api("POST", "/backup/password/printed"))) await backupDraw();
    } else if (act === "pw-new") {
      if (!confirm(t("admin.bpw.new_q"))) return;
      if (await guard(() => api("POST", "/backup/password/new?confirm=JA"), t("admin.bpw.new_done"))) await backupDraw();
    } else if (act === "done") {
      if (await guard(() => api("POST", "/restore-check/done"))) await backupDraw();
    } else if (act === "apk") {
      b.disabled = true;
      const r = await guard(() => api("POST", "/restore-check/apk"), t("admin.restore.apk_done"));
      b.disabled = false;
      if (r) await backupDraw();
    } else if (act === "time") {
      if (await guard(() => api("POST", "/time", { epoch_ms: Date.now() }), t("admin.time.taken"))) await backupDraw();
    } else if (act === "scan") {
      await scanQr();
    }
  });
  el.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.id === "bk-dlform") return downloadBackup(f);
    if (f.id === "bk-rsgo") return restore(f);
    if (f.dataset.bkForm === "receipt") {
      if (await guard(() => api("POST", "/restore-check/receipt-next", { next: Number(f.next.value) }), t("admin.common.saved"))) await backupDraw();
    } else if (f.dataset.bkForm === "keys") {
      if (!confirm(t("admin.restore.keys_q"))) return;
      const fd = new FormData();
      fd.append("file", f.file.files[0]);
      if (f.password.value) fd.append("password", f.password.value);
      if (await guard(() => api("POST", "/restore/keys", fd))) waitRestart();
    }
  });
  el.addEventListener("change", async (e) => {
    if (e.target.closest("#bk-rsform") && e.target.name === "file") {
      const f = e.target.files[0];
      BK.needPw = false;
      BK.file = f ? { name: f.name, blob: f, ...(await fileHead(f)) } : null;
      restoreInfo();
    }
  });
}
