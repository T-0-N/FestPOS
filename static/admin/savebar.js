/* FestPOS – Verwaltung › Speichern-Balken (T28, Plan Abschnitt 4): Felder mit data-k (Schlüssel), data-type
   (str | int | bool | secret | pin) und data-label werden gesammelt; ändert sich etwas, erscheint unten
   «Geändert – Verwerfen · Speichern». Gespeichert wird alles zusammen mit PUT /settings; Rückfrage beim Verlassen
   der Seite (shell.setDirty). Die Seite «Kasse» hat ihren eigenen Balken (kassepage.js, mehrere Ziele). */
import { $, $$, api, guard, toast } from "./core.js";
import { setDirty } from "./shell.js";

function value(el) {
  const ty = el.dataset.type;
  if (el.type === "radio") return el.checked ? el.value : null;
  if (ty === "bool") return el.checked;
  const v = el.value.trim();
  if (ty === "int") return Number(v || 0);
  return v;
}
function collect(root) {
  const out = {};
  $$("[data-k]", root).forEach((el) => {
    const v = value(el);
    if (el.type === "radio") { if (v !== null) out[el.dataset.k] = v; return; }
    out[el.dataset.k] = v;
  });
  return out;
}

// root: Element mit den Feldern; onSaved(body): nach dem Speichern (z.B. Seite neu zeichnen)
export function attachSaveBar(root, { onSaved, okMsg } = {}) {
  let base = collect(root);
  root.insertAdjacentHTML("beforeend", `<div class="savebar" data-savebar hidden><span><b>${th("admin.save.unsaved")}</b>
      <small data-savebar-what></small></span>
    <span class="savebar-btns"><button type="button" class="btn" data-savebar-discard>${th("admin.save.discard")}</button>
      <button type="button" class="btn pri" data-savebar-go>${th("admin.common.save")}</button></span></div>`);
  const bar = $("[data-savebar]", root);
  const changed = () => {
    const now = collect(root);
    return Object.keys(now).filter((k) => {
      const el = $(`[data-k="${k}"]`, root);
      if (el && ["secret", "pin"].includes(el.dataset.type)) return now[k] !== "";      // geheim: nur wenn eingetippt
      return JSON.stringify(now[k]) !== JSON.stringify(base[k]);
    });
  };
  const sync = () => {
    const keys = changed();
    $$("[data-k]", root).forEach((el) => el.closest("label")?.classList.toggle("k-changed", keys.includes(el.dataset.k)));
    bar.hidden = !keys.length;
    $("[data-savebar-what]", root).textContent = [...new Set(keys.map((k) => $(`[data-k="${k}"]`, root)?.dataset.label)
      .filter(Boolean))].map((l) => t(l)).join(", ");
    setDirty(keys.length > 0);
  };
  root.addEventListener("input", sync);
  root.addEventListener("change", sync);
  $("[data-savebar-discard]", root).addEventListener("click", () => { setDirty(false); onSaved?.(null); });
  $("[data-savebar-go]", root).addEventListener("click", async () => {
    const now = collect(root), body = {};
    for (const k of changed()) {
      const el = $(`[data-k="${k}"]`, root);
      if (el?.dataset.type === "pin" && !/^\d{4,8}$/.test(now[k])) { toast(t("admin.pin.digits"), true); el.focus(); return; }
      body[k] = now[k];
    }
    if (!Object.keys(body).length) return;
    if (!await guard(() => api("PUT", "/settings", body), okMsg || t("admin.common.saved"))) return;
    base = collect(root);
    setDirty(false);
    onSaved?.(body);
  });
  return { sync };
}
