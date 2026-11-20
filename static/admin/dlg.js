/* FestPOS – Verwaltung › Bearbeiten im Fenster (T28 Stufe 2): Listen zeigen kompakte Zeilen, «Bearbeiten» oder
   «+ Neu» öffnet dieses Fenster mit «Abbrechen · Speichern». Gespeichert wird alles zusammen (onSave), erst dann
   schliesst das Fenster; bei einem Fehler bleibt es offen. */
import { $ } from "./core.js";

// title: Überschrift (Text), body: HTML der Felder, onSave(form) -> true = gespeichert (Fenster zu).
// extra: HTML links unten (z.B. «Löschen»), onOpen(dialog): Felder binden.
export function editDialog({ title, body, onSave, extra = "", onOpen, saveLabel }) {
  const dlg = document.createElement("dialog");
  dlg.className = "dlg";
  dlg.innerHTML = `<form method="dialog" class="dlg-form">
    <h3>${title}</h3>
    ${body}
    <div class="form dlg-foot">
      ${extra ? `<span class="dlg-extra">${extra}</span>` : ""}
      <button type="button" class="btn" data-dlg="cancel">${th("admin.common.cancel")}</button>
      <button type="submit" class="btn pri" data-dlg="save">${saveLabel || th("admin.common.save")}</button>
    </div></form>`;
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  $('[data-dlg="cancel"]', dlg).addEventListener("click", () => dlg.close());
  $("form", dlg).addEventListener("submit", async (e) => {
    e.preventDefault();
    const go = $('[data-dlg="save"]', dlg);
    go.disabled = true;
    try {
      if (await onSave(e.target, dlg)) dlg.close();
    } finally { go.disabled = false; }
  });
  onOpen?.(dlg);
  dlg.showModal();
  return dlg;
}
