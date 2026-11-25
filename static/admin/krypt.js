/* FestPOS – Verwaltung › Geräte & Netz › Pi: Karte «Daten verschlüsselt (Schlüssel im Chip)» (T-0-N/FestPOS#3).
   Pro Pi: verschlüsselt ja/nein, Wiederherstellungs-Schlüssel gesichert. «Umstellen …»: Schlüssel anzeigen und
   drucken, erst nach dem Häkchen «ausgedruckt» umstellen. Neuer Pi: «Schlüssel sichern …» (vorläufig verschlüsselt). */
import { $, api, esc, guard, piName, toast } from "./core.js";
import { editDialog } from "./dlg.js";

const STATE = {
  aktiv: ["ok", "admin.krypt.s_aktiv"], kein_chip: ["pending", "admin.krypt.s_kein_chip"],
  vorlaeufig: ["pending", "admin.krypt.s_vorlaeufig"], umstellen: ["pending", "admin.krypt.s_umstellen"],
  gesperrt: ["bad", "admin.krypt.s_gesperrt"], aus: ["pending", "admin.krypt.s_aus"],
};
const ERR = {
  verify: "admin.krypt.e_verify", health: "admin.krypt.e_health", space: "admin.krypt.e_space", otp: "admin.krypt.e_otp",
  no_tool: "admin.krypt.e_no_tool",
};

export const kryptOf = (n) => n.info?.krypt || null;
// Übersicht: neuer Pi mit noch nicht gesichertem Schlüssel; Pi, die (mit Werkzeug) noch nicht verschlüsselt sind
export const kryptPending = (n) => kryptOf(n)?.offen === true;
export const kryptOff = (n) => kryptOf(n)?.state === "aus" && kryptOf(n)?.tool === true;

const day = (d) => (d ? new Date(`${d}T12:00:00`).toLocaleDateString(I18n.locale, { day: "2-digit", month: "2-digit", year: "numeric" }) : "");

function keyCell(n) {
  const k = kryptOf(n);
  if (!k) return "–";
  if (k.offen) return `<button type="button" class="btn pri sm" data-kr-save="${esc(n.id)}">${th("admin.krypt.save_open")}</button>`;
  if (k.state === "aus") {
    return k.tool ? `<button type="button" class="btn pri sm" data-kr-on="${esc(n.id)}">${th("admin.krypt.switch_open")}</button>`
      : `<span class="hint">${th("admin.krypt.no_tool")}</span>`;
  }
  if (k.state === "umstellen") return `<span class="hint">${th("admin.krypt.running")}</span>`;
  return k.bestaetigt ? esc(t("admin.krypt.saved_at", { date: day(k.bestaetigt) })) : "–";
}

export function kryptCard(system) {
  const nodes = system?.nodes || [];
  if (!nodes.length) return "";
  const rows = nodes.map((n) => {
    const k = kryptOf(n), [cls, key] = STATE[k?.state] || ["", "admin.krypt.s_unknown"];
    const err = k?.error && k.state === "aus" ? `<div class="err">${th(ERR[k.error] || "admin.krypt.e_other", { code: k.error })}</div>` : "";
    return `<tr><td><b>${esc(piName(n.id))}</b></td><td><span class="status ${cls}">${th(key)}</span>${err}</td>
      <td>${keyCell(n)}</td></tr>`;
  }).join("");
  const noChip = nodes.some((n) => kryptOf(n)?.state === "kein_chip");
  return `<div class="card" id="kryptcard"><h3>${th("admin.krypt.title")}</h3>
    <div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.overview.node")}</th><th>${th("admin.krypt.c_data")}</th>
      <th>${th("admin.krypt.c_key")}</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">${th("admin.krypt.hint")}</p>
    ${noChip ? `<p class="hint">${th("admin.krypt.no_chip_hint")}</p>` : ""}</div>`;
}

// Druckansicht (wie das Sicherungs-Passwort, admin.css @media print): nur das Blatt
function printKey(node, key) {
  const g = key.split("-");
  const box = document.createElement("div");
  box.className = "pwprint";
  box.innerHTML = `<div class="pwsheet"><h1>${th("admin.krypt.p_title", { pi: piName(node) })}</h1>
    <p class="pwsub">${esc(new Date().toLocaleDateString(I18n.locale))}</p>
    <div class="pwbox"><div><div class="pwbig" translate="no">${esc(g.slice(0, 4).join("-"))}<br>${esc(g.slice(4).join("-"))}</div></div></div>
    <h2>${th("admin.krypt.p_what")}</h2><p>${th("admin.krypt.p_what_text")}</p>
    <h2>${th("admin.krypt.p_how")}</h2><p>${th("admin.krypt.p_how_text")}</p></div>`;
  document.body.classList.add("printing-pw");
  document.body.append(box);
  const done = () => { box.remove(); document.body.classList.remove("printing-pw"); };
  addEventListener("afterprint", done, { once: true });
  window.print();
  setTimeout(() => { if (box.isConnected && !matchMedia("print").matches) done(); }, 60000);
}

function keyDialog({ node, key, title, lead, warn, saveLabel, onSave }) {
  const g = key.split("-");
  return editDialog({
    title: esc(title),
    saveLabel,
    body: `<p>${lead}</p>
      <div class="kr-key" translate="no">${esc(g.slice(0, 4).join("-"))}<br>${esc(g.slice(4).join("-"))}</div>
      <div class="form"><button type="button" class="btn" data-kr-print>${th("admin.krypt.print")}</button></div>
      <label class="check"><input type="checkbox" name="printed"> ${th("admin.krypt.printed")}</label>
      ${warn ? `<div class="warnbox"><p>${warn}</p></div>` : ""}`,
    onOpen: (dlg) => {
      const go = $('[data-dlg="save"]', dlg), box = $('[name="printed"]', dlg);
      go.disabled = true;
      box.addEventListener("change", () => { go.disabled = !box.checked; });
      $("[data-kr-print]", dlg).addEventListener("click", () => printKey(node, key));
    },
    onSave: (form) => (form.printed.checked ? onSave() : (toast(t("admin.krypt.tick_first"), true), false)),
  });
}

async function switchOn(node, rerender) {
  const r = await guard(() => api("POST", "/krypt/schluessel"));
  if (!r?.recovery) return;
  keyDialog({
    node, key: r.recovery, title: t("admin.krypt.d_title", { pi: piName(node) }),
    lead: th("admin.krypt.d_lead", { pi: piName(node) }), warn: th("admin.krypt.d_warn"), saveLabel: th("admin.krypt.d_go"),
    onSave: async () => {
      const ok = await guard(() => api("POST", `/krypt/${encodeURIComponent(node)}/umstellen`, { recovery: r.recovery, printed: true }),
        t("admin.krypt.started", { pi: piName(node) }));
      if (ok) setTimeout(rerender, 3000);
      return !!ok;
    },
  });
}

async function saveOpen(node, rerender) {
  const r = await guard(() => api("GET", `/krypt/${encodeURIComponent(node)}/offen`));
  if (!r?.recovery) return;
  keyDialog({
    node, key: r.recovery, title: t("admin.krypt.o_title", { pi: piName(node) }),
    lead: th("admin.krypt.o_lead", { pi: piName(node) }), saveLabel: th("admin.krypt.o_go"),
    onSave: async () => {
      const ok = await guard(() => api("POST", `/krypt/${encodeURIComponent(node)}/bestaetigen`, { printed: true }),
        t("admin.krypt.confirmed", { pi: piName(node) }));
      if (ok) rerender();
      return !!ok;
    },
  });
}

export function bindKrypt(rerender) {
  document.querySelectorAll("[data-kr-on]").forEach((b) => b.addEventListener("click", () => switchOn(b.dataset.krOn, rerender)));
  document.querySelectorAll("[data-kr-save]").forEach((b) => b.addEventListener("click", () => saveOpen(b.dataset.krSave, rerender)));
}
