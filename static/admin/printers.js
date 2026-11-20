/* FestPOS – Verwaltung › Geräte & Netz › Reiter «Drucker» (T28 Stufe 2, T34): kompakte Liste mit Zustand, «Aktiv»
   schaltet sofort, «Bearbeiten» und «+ Drucker» öffnen ein Fenster (dlg.js). Bons & Quittung: Kasse & App (bondesign.js).
   «Offene Bons umleiten» steht auch unter Druckaufträge (jobs.js). Darunter das Drucker-Netz (Drucker in eigenem VLAN,
   app/printer_net.py; nur Admin). */
import { $, $$, api, can, errLabel, esc, guard, LANGS, piName, toast } from "./core.js";
import { pageHead, RENDER, view } from "./shell.js";
import { editDialog } from "./dlg.js";
import { switchHTML } from "./monitor.js";

let PN = null;                      // Drucker-Netz (zuletzt geladen): Vorschlag für die IP eines neuen Druckers

// ------------------------------------------------------------------ Drucker
const langOptions = (sel) => `<option value="">${th("admin.common.language_default")}</option>` +
  LANGS.map((l) => `<option value="${esc(l.code)}" ${sel === l.code ? "selected" : ""}>${esc(l.name)}</option>`).join("");
const CHARS = [[48, "admin.printers.chars_48"], [42, "admin.printers.chars_42"], [32, "admin.printers.chars_32"]];
const AFTER = [[30, "admin.printers.after_30s"], [60, "admin.printers.after_1m"], [120, "admin.printers.after_2m"],
  [300, "admin.printers.after_5m"], [600, "admin.printers.after_10m"]];
// Standard-Drucker: für Produkte ohne eigenen Drucker (bis 2026.10.10-26 unter Einstellungen)
function defaultPrinterCard(s, printers) {
  return `<div class="card" id="defprncard"><h3>${th("admin.settings.default_printer")}</h3>
      <p class="hint">${th("admin.settings.default_printer_hint")}</p>
      <div class="form"><label>${th("admin.printers.printer")}<select id="opt-defprn">
        <option value="0">${th("admin.settings.auto")}${printers?.length ? ` ${th("admin.settings.auto_now", { name: [...printers].sort((a, b) => a.id - b.id)[0].name })}` : ""}</option>
        ${(printers || []).map((p) => `<option value="${p.id}" ${String(p.id) === s.default_printer_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}
      </select></label></div>
    </div>`;
}
const stateOf = (p) => (!p.enabled ? ["", t("admin.printers.off")] : p.last_error ? ["bad", errLabel(p.last_error_code)]
  : ["ok", t("admin.printers.active_state")]);
function row(p) {
  const [cls, txt] = stateOf(p);
  const notes = [p.down ? `<span class="err">${th("admin.printers.down_now")}</span>` : "",
    p.pending || p.failed || p.uncertain ? th("admin.printers.queue", { pending: p.pending, problems: p.failed + p.uncertain }) : ""].filter(Boolean);
  return `<tr data-id="${p.id}" class="${p.enabled ? "" : "inactive"}">
    <td class="pr-name"><b>${esc(p.name)}</b>${notes.length ? `<small>${notes.join(" · ")}</small>` : ""}<p class="hint pr-out" data-out hidden></p></td>
    <td class="pr-addr">${esc(p.host)}:${p.port}<small>${th(CHARS.find(([v]) => v === p.chars_per_line)?.[1] || "admin.printers.chars_48")}</small></td>
    <td><span class="status ${cls}">${esc(txt)}</span></td>
    <td><label class="sw"><input type="checkbox" data-active ${p.enabled ? "checked" : ""}><i aria-hidden="true"></i>
      <span class="sw-txt">${th("admin.printers.active")}</span></label></td>
    <td class="acts"><button class="btn sm" type="button" data-a="test">${th("admin.printers.test")}</button>
      <button class="btn sm" type="button" data-a="status">${th("admin.printers.check")}</button>
      <button class="btn sm" type="button" data-a="edit">${th("admin.common.edit")}</button></td></tr>`;
}
function body(p, printers) {
  const others = printers.filter((x) => !p || x.id !== p.id);
  return `<div class="form printer" data-id="${p ? p.id : ""}">
      <label>${th("admin.printers.name")}<input name="name" value="${esc(p?.name || "")}" required maxlength="32"
        placeholder="${th("admin.printers.name_placeholder")}"></label>
      <label>${th("admin.printers.ip")}<input name="host" value="${esc(p?.host || "")}" required placeholder="${esc(PN?.vlan ? `${PN.net}.21` : "192.168.55.21")}" style="width:160px"></label>
      <label>${th("admin.printers.port")}<input name="port" type="number" value="${p?.port || 9100}" style="width:90px"></label>
      ${p ? `<label>${th("admin.printers.chars")}<select name="chars_per_line">${CHARS.map(([v, k]) =>
        `<option value="${v}" ${v === (p.chars_per_line ?? 48) ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
      ${LANGS.length > 1 ? `<label>${th("admin.printers.language")}<select name="language">${langOptions(p.language || "")}</select></label>` : ""}` : ""}
    </div>
    ${p && others.length ? `<div class="form failover">
      <label>${th("admin.printers.if_down")}<select name="fail_mode">
        ${[["wait", "admin.printers.fail_wait"], ["reroute", "admin.printers.fail_reroute"], ["warn", "admin.printers.fail_warn"]]
          .map(([v, k]) => `<option value="${v}" ${v === (p.fail_mode || "wait") ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
      <label data-fo>${th("admin.printers.printer")}<select name="fail_target">${others.map((x) =>
        `<option value="${x.id}" ${x.id === p.fail_target ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
      <label data-fo>${th("admin.printers.after")}<select name="fail_after">${AFTER.map(([v, k]) =>
        `<option value="${v}" ${v === (AFTER.some(([a]) => a === p.fail_after) ? p.fail_after : 60) ? "selected" : ""}>${th(k)}</option>`).join("")}</select></label>
    </div>
    <h4>${th("admin.printers.reroute")}</h4>
    <div class="form"><select data-reroute>${others.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
      <button class="btn" type="button" data-a="reroute">${th("admin.printers.reroute")}</button></div>` : ""}
    ${p?.last_error ? `<p class="err">${esc(p.last_error)}</p>` : ""}
    <p class="hint">${th("admin.printers.add_hint")}</p>`;
}
function openEdit(p, printers) {
  editDialog({
    title: p ? esc(p.name) : th("admin.printers.add"),
    body: body(p, printers),
    extra: p ? `<button type="button" class="btn danger" data-a="delete">${th("admin.common.delete")}</button>` : "",
    saveLabel: p ? null : th("admin.common.add"),
    onOpen: (dlg) => {
      const f = $("form", dlg);
      const sync = () => $$("[data-fo]", dlg).forEach((el) => { el.hidden = f.fail_mode?.value === "wait"; });
      f.fail_mode?.addEventListener("change", sync); sync();
      $('[data-a="reroute"]', dlg)?.addEventListener("click", async () => {
        const r = await guard(() => api("POST", `/printers/${p.id}/reroute?to=${+$("[data-reroute]", dlg).value}`));
        if (r) toast(t("admin.printers.rerouted", { count: r.moved }));
      });
      $('[data-a="delete"]', dlg)?.addEventListener("click", async () => {
        if (!confirm(t("admin.printers.delete_confirm"))) return;
        if (await guard(() => api("DELETE", `/printers/${p.id}`), t("admin.common.deleted"))) { dlg.close(); RENDER.printers(); }
      });
    },
    onSave: async (f) => {
      if (!p) {
        const ok = await guard(() => api("POST", "/printers", { name: f.name.value.trim(), host: f.host.value.trim(),
          port: +f.port.value || 9100 }), t("admin.printers.created"));     // Preise: Bons & Quittung
        if (ok) RENDER.printers();
        return !!ok;
      }
      const ok = await guard(() => api("PUT", `/printers/${p.id}`, { name: f.name.value.trim(), host: f.host.value.trim(),
        port: +f.port.value || 9100, chars_per_line: +f.chars_per_line.value, show_prices: !!p.show_prices, enabled: !!p.enabled,
        fail_mode: f.fail_mode?.value || "wait", fail_target: f.fail_target ? +f.fail_target.value : null,
        fail_after: f.fail_after ? +f.fail_after.value : 60, language: f.language ? f.language.value : (p.language || "") }),
      t("admin.common.saved"));
      if (ok) RENDER.printers();
      return !!ok;
    },
  });
}
async function checkStatus(id, say) {
  say(t("admin.printers.asking"));
  const s = await guard(() => api("GET", `/printers/${id}/status`));
  if (!s) return;
  if (!s.online) say(t("admin.printers.st_unreachable", { error: s.error || t("admin.printers.no_answer") }), true);
  else if (s.paper_out) say(t("admin.printers.st_paper_out"), true);
  else if (s.cover_open) say(t("admin.printers.st_cover_open"), true);
  else if (s.error) say(s.error, true);
  else if (s.paper_near_end) say(t("admin.printers.st_paper_low"), true);
  else if (s.no_answer && s.status_seen) say(t("admin.printers.st_silent"), true);    // T15: druckt nicht blind
  else if (s.no_answer) say(`${t("admin.printers.st_no_status")} ${t("admin.printers.st_setup_hint")}`);
  else say(t("admin.printers.st_ok"));
}

// Drucker-Netz: Drucker in einem eigenen VLAN, das nur die Pi erreichen (nicht das WLAN). Jeder Pi richtet sich die
// VLAN-Schnittstelle selbst ein (festpos-druckernetz) und meldet, ob er seine Adresse im Drucker-Netz hat.
let pnetSaved = 0;                  // gleich nach dem Speichern: «wird eingerichtet» statt «nicht eingerichtet»
function pnetKind(n, pn) {
  if (!n.reachable) return "down";                   // zuletzt gemeldet «bereit» gilt nicht, solange er weg ist
  const s = n.pnet;
  if (!s || s.vlan !== pn.vlan || !s.ok) return Date.now() - pnetSaved < 30000 ? "wait" : "missing";
  return "ok";
}
function pnetState(n, pn) {
  const kind = pnetKind(n, pn);
  if (kind === "ok") return `<span class="status ok">${th("admin.printers.vlan_st_ok", { dev: n.pnet.dev || "" })}</span>`;
  if (kind === "wait") return `<span class="status pending">${th("admin.printers.vlan_st_wait")}</span>`;
  return `<span class="status bad">${th(kind === "down" ? "admin.printers.vlan_st_down" : "admin.printers.vlan_st_missing")}</span>`;
}
function pnetCard(pn) {
  const on = pn.vlan > 0;
  const warn = (w) => t(w.kind === "pi" ? "admin.printers.vlan_warn_pi" : "admin.printers.vlan_warn_outside",
    { name: w.printer, host: w.host, net: `${pn.net}.x` });
  return `<form class="card" id="pnetcard"><h3>${th("admin.printers.vlan_title")}</h3>
    <p class="hint">${th("admin.printers.vlan_hint")}</p>
    <div class="form">
      ${switchHTML(on, "pn-on", { text: t("admin.printers.vlan_on") })}
      <label data-pn>${th("admin.printers.vlan_id")}<input name="vlan" type="number" min="2" max="4094" required value="${pn.vlan || 56}" style="width:90px"></label>
      <label data-pn>${th("admin.printers.vlan_net")}<span class="inline"><input name="net" required maxlength="13" value="${esc(pn.net || "192.168.56")}" style="width:130px">.x</span></label>
      <button class="btn pri">${th("admin.common.save")}</button>
    </div>
    ${on ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.overview.node")}</th><th>${th("admin.printers.vlan_address")}</th>
      <th>${th("admin.printers.state")}</th></tr></thead><tbody>
      ${pn.nodes.map((n) => `<tr><td><b>${esc(piName(n.id))}</b>${n.here ? ` <span class="hint">${th("admin.system.here")}</span>` : ""}</td>
        <td>${esc(n.pnet?.ip || "–")}</td><td>${pnetState(n, pn)}</td></tr>`).join("")}
      </tbody></table></div>
      ${pn.nodes.some((n) => pnetKind(n, pn) === "missing") ? `<p class="err">${th("admin.printers.vlan_missing_hint")}</p>` : ""}
      ${pn.warnings.map((w) => `<p class="err">${esc(warn(w))}</p>`).join("")}
      <p class="hint">${th("admin.printers.vlan_switch", { vlan: pn.vlan, pis: pn.pis.join(", ") })}</p>` : ""}
  </form>`;
}
function bindPnet(pn) {
  const f = $("#pnetcard");
  if (!f) return;
  const box = $(".pn-on", f);
  const sync = () => $$("[data-pn]", f).forEach((el) => { el.hidden = !box.checked; });
  box.addEventListener("change", sync); sync();
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const on = box.checked;
    if (!on && pn.vlan && !confirm(t("admin.printers.vlan_off_confirm"))) return;
    const body = { vlan: on ? +f.vlan.value : 0, net: f.net.value.trim().replace(/\.x?$/i, "") };
    if (!await guard(() => api("PUT", "/printer-net", body), t("admin.common.saved"))) return;
    pnetSaved = Date.now();
    RENDER.printers();
    // jeder Pi richtet es innert Sekunden ein (die anderen lesen die Einstellung alle 10 s): dann nur diese Karte
    // nochmals zeigen – Eingaben bleiben stehen
    [5000, 15000, 31000].forEach((ms) => setTimeout(refreshPnet, ms));
  });
}
async function refreshPnet() {
  const pn = view === "printers" && $("#pnetcard") ? await api("GET", "/printer-net").catch(() => null) : null;
  const card = view === "printers" && $("#pnetcard");
  if (!pn || !card) return;
  if (card.contains(document.activeElement)) { setTimeout(refreshPnet, 5000); return; }   // wird gerade bearbeitet: später
  PN = pn;
  card.outerHTML = pnetCard(pn);
  bindPnet(pn);
}

RENDER.printers = async () => {
  const [printers, st, pn] = await Promise.all([guard(() => api("GET", "/printers")), api("GET", "/settings").catch(() => null),
    can("admin") ? api("GET", "/printer-net").catch(() => null) : null]);
  if (!printers) return;
  if (view !== "printers") return;                     // inzwischen andere Seite (REV-048)
  PN = pn;
  $("#main").innerHTML = `${pageHead(`<button type="button" class="btn pri" id="prn-add">${th("admin.printers.add_btn")}</button>`)}
    <div class="card">${printers.length ? `<div class="scroll"><table class="tbl prtbl"><thead><tr>
      <th>${th("admin.printers.name")}</th><th>${th("admin.printers.ip")}</th><th>${th("admin.printers.state")}</th>
      <th>${th("admin.printers.active")}</th><th></th></tr></thead><tbody>${printers.map(row).join("")}</tbody></table></div>`
      : `<p class="hint">${th("admin.printers.none")}</p>`}</div>
    ${can("admin") && st ? defaultPrinterCard(st, printers) : ""}
    ${pn ? pnetCard(pn) : ""}`;
  bindPnet(pn);
  $("#prn-add").addEventListener("click", () => openEdit(null, printers));
  $("#opt-defprn")?.addEventListener("change", (e) =>
    guard(() => api("PUT", "/settings", { default_printer_id: +e.target.value }), t("admin.settings.default_printer_saved")));
  $$("table.prtbl tr[data-id]").forEach((tr) => {
    const p = printers.find((x) => x.id === +tr.dataset.id), out = $("[data-out]", tr);
    const say = (msg, bad) => { out.hidden = false; out.className = `pr-out ${bad ? "err" : "okmsg"}`; out.textContent = msg; };
    tr.addEventListener("click", async (e) => {
      const a = e.target.closest("[data-a]")?.dataset.a;
      if (a === "edit") openEdit(p, printers);
      else if (a === "test") { if (await guard(() => api("POST", `/printers/${p.id}/test`))) say(t("admin.printers.test_sent")); }
      else if (a === "status") checkStatus(p.id, say);
    });
    // «Aktiv» ist ein Schalter für den laufenden Betrieb: speichert sofort (T28, Plan Abschnitt 4)
    $("[data-active]", tr).addEventListener("change", async (e) => {
      const on = e.target.checked;
      const ok = await guard(() => api("PUT", `/printers/${p.id}`, { ...p, show_prices: !!p.show_prices, enabled: on,
        fail_target: p.fail_target ?? null, language: p.language || "" }), t(on ? "admin.printers.on_saved" : "admin.printers.off_saved", { name: p.name }));
      if (ok) RENDER.printers(); else e.target.checked = !on;
    });
  });
};
