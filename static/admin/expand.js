/* FestPOS – Verwaltung › Pi & Netz › «Auf 3 Pi erweitern» (Einzelbetrieb) bzw. «Ersatzkarte vorbereiten» (Cluster), T8.
   Gleicher Rahmen wie «Gerät hinzufügen» (device_add.js). Server: app/cards.py (Kartenpaket, Pi A umstellen). */
import { $, api, esc, guard, piName, toast } from "./core.js";
import { RENDER, view } from "./shell.js";
import { startCardWrite } from "./cardwrite.js";

let X = null;           // { mode: "expand" | "spare", step, ready, letter, poll }
const STEPS = ["admin.expand.s_cards", "admin.expand.s_switch", "admin.expand.s_plug"];

export function startExpand(single, here) {
  stop();
  X = { mode: single ? "expand" : "spare", step: 0, ready: false, here, letter: "" };
  draw();
}
// «Karte hier schreiben» (T10): danach zurück an dieselbe Stelle dieses Ablaufs
function writeHere(letter) {
  const keep = { ...X, poll: null };
  stop();
  startCardWrite({ letter, from: { mode: keep.mode, here: keep.here, resume: () => { X = keep; draw(); } } });
}
// Karten, die dieser Pi in den letzten 24 h geschrieben hat (Protokoll) -> «Karten B und C sind vorbereitet» von selbst
async function writtenCards() {
  const r = await api("GET", "/cluster/karte").catch(() => null);
  return r?.written || {};
}
function writtenList(letters, written) {
  return `<ul class="chk" id="x-written">${letters.map((l) => `<li class="${written[l] ? "ok" : ""}"><b>${th("admin.cardw.for_pi", { pi: piName(l) })}</b>
    <span>${written[l] ? esc(t("admin.cardw.written_at", { at: I18n.fmtDateTime(written[l] * 1000) })) : th("admin.cardw.not_written")}</span></li>`).join("")}</ul>`;
}
function stop() {
  if (X?.poll) clearInterval(X.poll);
  X = null;
}

function frame(title, body, next, { back = true, nextOff = false } = {}) {
  const pills = X.mode === "expand" ? `<ol class="wsteps">${STEPS.map((k, i) =>
    `<li class="${i === X.step ? "on" : i < X.step ? "done" : ""}">${th(k, { n: i + 1 })}</li>`).join("")}</ol>` : "<span></span>";
  return `<h2>${th(X.mode === "expand" ? "admin.expand.title" : "admin.expand.spare_title")}</h2><div class="card wiz" id="xwiz">
    <div class="wtop">${pills}<button type="button" class="linklike" id="x-cancel">${th("admin.common.cancel")}</button></div>
    <h3>${th(title)}</h3>${body}
    <div class="wiz-foot">${back && X.step ? `<button type="button" class="btn" id="x-back">${th("admin.devices.back")}</button>` : ""}
      ${next ? `<button type="button" class="btn pri" id="x-next" ${nextOff ? "disabled" : ""}>${esc(next)}</button>` : ""}</div></div>`;
}

async function cardsZip(letters) {
  const r = await guard(() => api("POST", "/cluster/kartenpaket", { letters }, { raw: true }));
  if (!r) return false;
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "")?.[1] || "festpos-kartenpaket.zip";
  const url = URL.createObjectURL(await r.blob());
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return true;
}

const CHECK = { single: "admin.expand.c_single", backup: "admin.expand.c_backup", idle: "admin.expand.c_idle" };
function checksList(st) {
  return `<ul class="chk">${Object.entries(st.checks).map(([k, ok]) => {
    const sub = k === "backup" ? (st.backup_at ? t("admin.expand.c_backup_at", { at: I18n.fmtDateTime(st.backup_at * 1000) }) : t("admin.expand.c_backup_none"))
      : k === "idle" && !ok ? t("admin.expand.c_idle_no", { count: st.open_jobs }) : "";
    return `<li class="${ok ? "ok" : "bad"}"><b>${th(CHECK[k])}</b><span>${esc(sub)}</span></li>`;
  }).join("")}</ul>`;
}

function nodesTable(nodes) {
  return `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.overview.node")}</th><th>${th("admin.expand.state")}</th></tr></thead><tbody>
    ${["a", "b", "c"].map((id) => {
      const n = (nodes || []).find((x) => x.id === id);
      const [cls, key] = !n ? ["", "admin.expand.n_unseen"] : !n.reachable ? ["pending", "admin.expand.n_joining"]
        : n.leader ? ["ok", "admin.expand.n_leader"] : ["ok", "admin.expand.n_member"];
      return `<tr><td><b>${esc(piName(id))}</b>${id === X.here ? ` <span class="hint">${th("admin.system.here")}</span>` : ""}</td>
        <td><span class="status ${cls}">${th(key)}</span></td></tr>`;
    }).join("")}</tbody></table></div>`;
}

async function draw() {
  if (!X || view !== "system") return stop();
  const main = $("#main");
  if (X.mode === "spare") {
    const letters = ["a", "b", "c"].filter((l) => l !== X.here);
    main.innerHTML = frame("admin.expand.spare_h", `<p class="hint">${th("admin.expand.spare_hint")}</p>
      <div class="choice">${letters.map((l) => `<label><input type="radio" name="xl" value="${l}" ${X.letter === l ? "checked" : ""}>
        <b>${th("admin.expand.spare_for", { pi: piName(l) })}</b><span>${th("admin.expand.spare_folder", { folder: `karte-${l}` })}</span></label>`).join("")}</div>
      <h3 style="margin-top:16px">${th("admin.cardw.here_h")}</h3><p class="hint">${th("admin.cardw.here_hint", { pi: piName(X.here) })}</p>
      <div class="form"><button type="button" class="btn" id="x-write" ${X.letter ? "" : "disabled"}>${th("admin.cardw.here")}</button></div>
      <details class="pcway"><summary>${th("admin.cardw.pc_h")}</summary>
      <div class="form" style="margin-top:12px"><button type="button" class="btn" id="x-zip" ${X.letter ? "" : "disabled"}>${th("admin.expand.zip")}</button></div>
      <div class="warnbox">${th("admin.expand.zip_warn")}</div>
      <ol class="steps"><li>${th("admin.expand.spare_s1")}</li><li>${th("admin.expand.spare_s2")}</li><li>${th("admin.expand.spare_s3")}</li></ol></details>`,
    t("admin.expand.done"));
  } else if (X.step === 0) {
    const letters = ["a", "b", "c"].filter((l) => l !== X.here);
    const written = await writtenCards();
    if (!X || X.step !== 0 || view !== "system") return;
    const auto = letters.every((l) => written[l]);
    if (auto) X.ready = true;
    main.innerHTML = frame("admin.expand.h_cards", `<h3>${th("admin.cardw.here_h")}</h3>
      <p class="hint">${th("admin.cardw.here_hint", { pi: piName(X.here) })}</p>
      ${writtenList(letters, written)}
      <div class="form">${letters.map((l) => `<button type="button" class="btn" data-write="${l}">${th("admin.cardw.write_for", { pi: piName(l) })}</button>`).join("")}</div>
      <details class="pcway" ${X.ready && !auto ? "open" : ""}><summary>${th("admin.cardw.pc_h")}</summary>
      <ol class="steps"><li>${th("admin.expand.cards_s1")}</li><li>${th("admin.expand.cards_s2")}</li>
        <li>${th("admin.expand.cards_s3")}</li></ol>
      <div class="form"><button type="button" class="btn" id="x-zip">${th("admin.expand.zip")}</button></div>
      <div class="warnbox">${th("admin.expand.zip_warn")}</div>
      <label class="check"><input type="checkbox" id="x-ready" ${X.ready ? "checked" : ""} ${auto ? "disabled" : ""}> ${th("admin.expand.ready")}</label></details>`,
    t("admin.expand.next"), { nextOff: !X.ready });
  } else if (X.step === 1) {
    main.innerHTML = frame("admin.expand.h_switch", `<p class="hint">${th("admin.common.loading")}</p>`, null);
    const st = await api("GET", "/cluster/expand").catch(() => null);
    if (!X || X.step !== 1 || view !== "system") return;
    const ok = st && st.helper && Object.values(st.checks).every(Boolean);
    main.innerHTML = frame("admin.expand.h_switch", `<p class="hint">${th("admin.expand.switch_hint")}</p>
      ${st ? checksList(st) : ""}${st && !st.helper ? `<p class="err">${th("admin.expand.no_helper")}</p>` : ""}`,
    t("admin.expand.switch"), { nextOff: !ok });
  } else {
    main.innerHTML = frame("admin.expand.h_plug", `<p class="hint">${th("admin.expand.plug_hint")}</p>
      <div id="x-nodes">${nodesTable(X.nodes)}</div><p class="meta" id="x-msg">${th(X.restarting ? "admin.expand.restarting" : "admin.expand.plug_done_when")}</p>`,
    t("admin.expand.done"), { back: false, nextOff: !X.complete });
    if (!X.poll) X.poll = setInterval(pollNodes, 5000);
  }
  bind();
}

async function pollNodes() {
  if (!X || view !== "system") return stop();
  let st;
  try { st = await api("GET", "/cluster/expand"); } catch { return; }      // Pi A startet gerade neu
  X.restarting = false;
  X.nodes = st.nodes;
  X.complete = (st.nodes || []).filter((n) => n.reachable).length >= 3;
  const box = $("#x-nodes");
  if (box) box.innerHTML = nodesTable(X.nodes);
  const msg = $("#x-msg");
  if (msg) msg.innerHTML = th(X.complete ? "admin.expand.complete" : "admin.expand.plug_done_when");
  const next = $("#x-next");
  if (next) next.disabled = !X.complete;
}

function bind() {
  $("#x-cancel")?.addEventListener("click", () => { stop(); RENDER.system(); });
  $("#x-back")?.addEventListener("click", () => { X.step -= 1; draw(); });
  $("#x-ready")?.addEventListener("change", (e) => { X.ready = e.target.checked; $("#x-next").disabled = !X.ready; });
  $$radio();
  document.querySelectorAll("#xwiz [data-write]").forEach((b) => b.addEventListener("click", () => writeHere(b.dataset.write)));
  $("#x-write")?.addEventListener("click", () => writeHere(X.letter));
  $("#x-zip")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    await cardsZip(X.mode === "spare" ? [X.letter] : ["b", "c"].filter((l) => l !== X.here));
    e.target.disabled = false;
  });
  $("#x-next")?.addEventListener("click", async () => {
    if (X.mode === "spare" || X.step === 2) { stop(); RENDER.system(); return; }
    if (X.step === 1) {
      if (!confirm(t("admin.expand.switch_q"))) return;
      const r = await guard(() => api("POST", "/cluster/expand?confirm=JA"));
      if (!r) return;
      toast(t("admin.expand.switched"));
      X.restarting = true;
    }
    X.step += 1;
    draw();
  });
}
function $$radio() {
  document.querySelectorAll('#xwiz input[name="xl"]').forEach((r) => r.addEventListener("change", () => {
    X.letter = r.value;
    $("#x-zip").disabled = false;
    $("#x-write").disabled = false;
  }));
}
