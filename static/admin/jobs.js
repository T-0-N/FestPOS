/* FestPOS – Verwaltung › Seite «Druckaufträge». Darunter (ab Festleitung, T34) «Offene Bons umleiten» – dasselbe wie
   im Fenster eines Druckers (Geräte & Netz › Drucker), hier während des Betriebs schneller zur Hand. */
import { $, api, can, esc, fmtTime, guard, toast } from "./core.js";
import { page, refreshBadge, RENDER } from "./shell.js";

// ------------------------------------------------------------------ Offene Bons umleiten
// Auswahl bleibt beim Aktualisieren (alle 5 s) stehen: die Karte wird nur beim ersten Zeichnen gebaut.
const RR = { from: null, to: null };
function rerouteCard(printers) {
  if (!can("printers") || (printers || []).length < 2) return "";
  const ids = printers.map((p) => p.id);
  const from = ids.includes(RR.from) ? RR.from : (printers.find((p) => p.pending) || printers[0]).id;
  const to = ids.includes(RR.to) && RR.to !== from ? RR.to : ids.find((id) => id !== from);
  const opts = (sel) => printers.map((p) => `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc(p.name)}${p.pending
    ? ` (${t("admin.jobs.rr_open", { count: p.pending })})` : ""}</option>`).join("");
  return `<div class="card" id="jobs-rr"><h3>${th("admin.printers.reroute")}</h3>
    <p class="hint">${th("admin.jobs.rr_hint")}</p>
    <div class="form"><label>${th("admin.jobs.rr_from")}<select id="rr-from">${opts(from)}</select></label>
      <label>${th("admin.jobs.rr_to")}<select id="rr-to">${opts(to)}</select></label>
      <button type="button" class="btn" id="rr-go">${th("admin.printers.reroute")}</button></div></div>`;
}
function bindReroute() {
  if (!$("#jobs-rr")) return;
  const keep = () => { RR.from = +$("#rr-from").value; RR.to = +$("#rr-to").value; };
  $("#rr-from").addEventListener("change", keep);
  $("#rr-to").addEventListener("change", keep);
  $("#rr-go").addEventListener("click", async () => {
    keep();
    if (RR.from === RR.to) return toast(t("admin.jobs.rr_same"), true);
    const r = await guard(() => api("POST", `/printers/${RR.from}/reroute?to=${RR.to}`));
    if (r) { toast(t("admin.printers.rerouted", { count: r.moved })); RENDER.jobs(); }
  });
}

// ------------------------------------------------------------------ Druckaufträge
let jobFilter = "problems";
RENDER.jobs = async (refresh = false) => {
  const [jobs, printers] = await Promise.all([guard(() => api("GET", "/jobs?limit=500")),
    !refresh && can("printers") ? api("GET", "/printers").catch(() => null) : null]);
  if (!jobs) return;
  const F = {
    problems: ["admin.jobs.filter_problems", (j) => j.status === "failed" || j.status === "uncertain"],
    open: ["admin.jobs.filter_open", (j) => j.status === "pending" || j.status === "printing"],
    all: ["admin.jobs.filter_all", () => true],
  };
  const list = jobs.filter(F[jobFilter][1]);
  const ST = { pending: "admin.jobs.st_pending", printing: "admin.jobs.st_printing", done: "admin.jobs.st_done",
    failed: "admin.jobs.st_failed", uncertain: "admin.jobs.st_uncertain", cancelled: "admin.jobs.st_cancelled" };
  if (page !== "jobs" || !$("#main")) return;    // inzwischen andere Seite (REV-048) oder abgemeldet
  if (!refresh || !$("#jobs-list")) {
    $("#main").innerHTML = `<h2>${th("admin.nav.jobs")}</h2><div class="card" id="jobs-list"></div>${rerouteCard(printers)}`;
    bindReroute();
  }
  $("#jobs-list").innerHTML = `
      <div class="seg jobseg">${Object.entries(F).map(([k, [l]]) =>
        `<button data-f="${k}" aria-pressed="${k === jobFilter}">${th(l)} (${jobs.filter(F[k][1]).length})</button>`).join("")}</div>
      <p class="hint">${th("admin.jobs.hint")}</p>
      ${list.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.jobs.time")}</th><th>${th("admin.printers.printer")}</th><th>${th("admin.jobs.bon")}</th><th>${th("admin.jobs.status")}</th><th class="num">${th("admin.jobs.attempts")}</th><th></th></tr></thead><tbody>
        ${list.map((j) => `<tr class="${j.test ? "testjob" : ""}"><td>${fmtTime(j.created_at)}</td><td>${esc(j.printer)}${j.printer !== j.station ? ` <span class="hint">${th("admin.jobs.for", { station: j.station })}</span>` : ""}</td>
          <td>${esc(j.label)}${j.last_error ? `<div class="err">${esc(j.last_error)}</div>` : ""}</td>
          <td><span class="status ${j.status}">${esc(ST[j.status] ? t(ST[j.status]) : j.status)}</span></td><td class="num">${j.attempts}</td>
          <td class="nowrap">
            <button class="btn sm" data-reprint="${j.id}">${th("admin.jobs.reprint")}</button>
            ${["failed", "uncertain", "pending", "printing"].includes(j.status) ? `<button class="btn sm" data-resolve="${j.id}">${th("admin.jobs.resolve")}</button>` : ""}
          </td></tr>`).join("")}</tbody></table></div>` : `<p class="hint">${th("admin.jobs.none")}</p>`}`;
  $(".seg").addEventListener("click", (e) => { if (e.target.dataset.f) { jobFilter = e.target.dataset.f; RENDER.jobs(); } });
  refreshBadge();
};
