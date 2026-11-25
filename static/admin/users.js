/* FestPOS – Verwaltung › Seite «Benutzer» (nur Admin). T28 Stufe 2: kompakte Liste, «Aktiv» schaltet sofort,
   Rolle, Passwort und Löschen im Fenster (dlg.js), «+ Benutzer» oben rechts. Rollen und Rechte: feste
   Rollen Anzeige, Festleitung, Admin (nicht änderbar) und eigene Rollen aus einzelnen Rechten. */
import { $, api, esc, fmtTime, guard, ME, roleName, toast } from "./core.js";
import { page, RENDER } from "./shell.js";
import { editDialog } from "./dlg.js";

// Rechte (app/auth.py PERMS): Name und Erklärung
const PERM_KEY = {
  orders: ["admin.perm.orders", "admin.perm.orders_hint"],
  jobs: ["admin.perm.jobs", "admin.perm.jobs_hint"],
  orders_edit: ["admin.perm.orders_edit", "admin.perm.orders_edit_hint"],
  tabs: ["admin.perm.tabs", "admin.perm.tabs_hint"],
  closing: ["admin.perm.closing", "admin.perm.closing_hint"],
  audit: ["admin.perm.audit", "admin.perm.audit_hint"],
  catalog: ["admin.perm.catalog", "admin.perm.catalog_hint"],
  stock: ["admin.perm.stock", "admin.perm.stock_hint"],
  tables: ["admin.perm.tables", "admin.perm.tables_hint"],
  printers: ["admin.perm.printers", "admin.perm.printers_hint"],
  devices: ["admin.perm.devices", "admin.perm.devices_hint"],
  system: ["admin.perm.system", "admin.perm.system_hint"],
  testmode: ["admin.perm.testmode", "admin.perm.testmode_hint"],
};
const permLabel = (p) => (PERM_KEY[p] ? th(PERM_KEY[p][0]) : esc(p));
const permHint = (p) => (PERM_KEY[p] ? th(PERM_KEY[p][1]) : "");

// Fenster für eine eigene Rolle: Name und Rechte (Häkchen)
function roleDialog(d, r) {
  editDialog({
    title: r ? esc(r.name) : th("admin.roles.new"),
    saveLabel: r ? th("admin.common.save") : th("admin.roles.add"),
    body: `<div class="form"><label>${th("admin.roles.name")}<input name="name" required maxlength="40" autocomplete="off"
        value="${esc(r?.name || "")}" style="width:220px"></label></div>
      <fieldset class="perm-list"><legend>${th("admin.roles.perms")}</legend>
        ${d.perms.map((p) => `<label class="check"><input type="checkbox" name="perm" value="${p}" ${r?.perms.includes(p) ? "checked" : ""}>
          <span><b>${permLabel(p)}</b><small class="hint">${permHint(p)}</small></span></label>`).join("")}
      </fieldset><p class="hint">${th("admin.roles.admin_only")}</p>`,
    extra: r ? `<button type="button" class="btn danger r-del">${th("admin.common.delete")}</button>` : "",
    onOpen: (dlg) => dlg.querySelector(".r-del")?.addEventListener("click", async () => {
      if (!confirm(t("admin.roles.delete_confirm", { name: r.name }))) return;
      if (await guard(() => api("DELETE", `/roles/${r.id}`), t("admin.roles.deleted", { name: r.name }))) { dlg.close(); RENDER.users(); }
    }),
    onSave: async (f) => {
      const body = { name: f.name.value.trim(), perms: [...f.querySelectorAll('input[name="perm"]:checked')].map((x) => x.value) };
      const ok = await guard(() => (r ? api("PUT", `/roles/${r.id}`, body) : api("POST", "/roles", body)), t("admin.common.saved"));
      if (ok) RENDER.users();
      return !!ok;
    },
  });
}

// ------------------------------------------------------------------ Benutzer (nur Admin)
// Zugriff (Punkt 3): «Verwaltung nur mit Gerätezertifikat». Einschalten geht nur, wenn diese Verbindung
// selbst ein gültiges Zertifikat zeigt (sonst sperrte man sich aus); der Server prüft das ebenso.
function accessCard(st, me, roles) {
  const on = st?.admin_cert_only === "1", cert = me?.cert, ok = !!cert?.ok;
  return `<div class="card" id="access-card"><h3>${th("admin.access.title")}</h3>
    <label class="sw"><input type="checkbox" id="cert-only" ${on ? "checked" : ""} ${!on && !ok ? "disabled" : ""}><i aria-hidden="true"></i>
      <span class="sw-txt">${th("admin.access.cert_only")}</span></label>
    <p class="hint">${th("admin.access.hint")}</p>
    <p class="${ok ? "okmsg" : "hint"}">${ok ? th("admin.access.this_ok", { name: cert.name }) : cert ? th("admin.access.this_bad", { name: cert.name })
      : th("admin.access.this_none")}</p>
    <h4>${th("admin.access.idle_h")}</h4>
    <form class="form" id="idle-form"><label>${th("admin.access.idle_label")}<span class="inline"><input type="number" name="minutes" id="idle-min"
      min="10" max="120" step="1" required value="${esc(st?.admin_idle_minutes || "30")}" style="width:90px"> ${th("admin.access.idle_unit")}</span></label>
      <button type="submit" class="btn pri">${th("admin.common.save")}</button></form>
    <p class="hint">${th("admin.access.idle_hint")}</p>
    <h4>${th("admin.totp.required_h")}</h4>
    <form class="form" id="totp-form">${roles.map((r) => `<label class="check"><input type="checkbox" name="role" value="${esc(r.id)}"
      ${totpRoles(st).includes(r.id) ? "checked" : ""}> ${esc(roleName(r.id, roles))}</label>`).join("")}
      <button type="submit" class="btn pri">${th("admin.common.save")}</button></form>
    <p class="hint">${th("admin.totp.required_hint")}</p></div>`;
}
// Rollen mit zweitem Faktor als Pflicht (Einstellung totp_required_roles, JSON; ohne Eintrag: Admin)
function totpRoles(st) {
  try { const v = JSON.parse(st?.totp_required_roles ?? '["admin"]'); return Array.isArray(v) ? v : ["admin"]; } catch { return ["admin"]; }
}

RENDER.users = async () => {
  const [d, st, me] = await Promise.all([guard(() => api("GET", "/users")), api("GET", "/settings").catch(() => null),
    api("GET", "/me").catch(() => null)]);
  if (!d || page !== "users") return;
  const nameOf = (id) => roleName(id, d.roles);
  const roleSel = (sel, dis = false) => `<select name="role" ${dis ? "disabled" : ""}>${d.roles.map((r) =>
    `<option value="${esc(r.id)}" ${r.id === sel ? "selected" : ""}>${esc(nameOf(r.id))}</option>`).join("")}</select>`;
  const isSelf = (u) => u.name.toLowerCase() === (ME?.name || "").toLowerCase() && !ME?.emergency;
  const permsOf = (r) => (r.admin ? `<span class="chip-s all">${th("admin.roles.all")}</span>`
    : r.perms.map((p) => `<span class="chip-s">${permLabel(p)}</span>`).join("") || `<span class="hint">${th("admin.roles.none")}</span>`);
  $("#main").innerHTML = `<div class="head-row"><h2>${th("admin.nav.users")}</h2>
      <button type="button" class="btn pri" id="user-add">${th("admin.users.add_btn")}</button></div>
    <div class="card"><h3>${th("admin.users.list", { count: d.users.length })}</h3>
      ${d.users.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>${th("admin.users.name")}</th><th>${th("admin.users.role")}</th>
        <th>${th("admin.users.active")}</th><th>${th("admin.totp.col")}</th><th>${th("admin.users.last_login")}</th><th></th></tr></thead><tbody>
        ${d.users.map((u) => `<tr data-uid="${u.id}" class="${u.active ? "" : "inactive"}"><td><b>${esc(u.name)}</b>${isSelf(u) ? ` <span class="hint">${th("admin.users.you")}</span>` : ""}</td>
          <td>${esc(nameOf(u.role))}</td>
          <td><label class="sw"><input type="checkbox" class="u-active" ${u.active ? "checked" : ""} ${isSelf(u) ? "disabled" : ""}><i aria-hidden="true"></i>
            <span class="sw-txt">${th("admin.users.active")}</span></label></td>
          <td>${u.totp_on ? `<span class="status ok">${th("admin.totp.col_on")}</span>` : "–"}</td>
          <td>${u.last_login ? esc(fmtTime(u.last_login)) : "–"}</td>
          <td class="acts"><button class="btn sm u-edit">${th("admin.common.edit")}</button></td></tr>`).join("")}</tbody></table></div>`
        : `<p class="hint">${th("admin.users.none")}</p>`}
    </div>
    <div class="card" id="roles-card"><div class="head-row"><h3>${th("admin.users.roles_title")}</h3>
        <button type="button" class="btn sm" id="role-add">${th("admin.roles.add_btn")}</button></div>
      <div class="scroll"><table class="tbl roles-tbl"><thead><tr><th>${th("admin.users.role")}</th><th>${th("admin.roles.perms")}</th><th></th></tr></thead><tbody>
        ${d.roles.map((r) => `<tr data-rid="${esc(r.id)}"><td><b>${esc(nameOf(r.id))}</b>${r.builtin ? `<small class="hint"> · ${th("admin.roles.fixed")}</small>` : ""}</td>
          <td class="perm-chips">${permsOf(r)}</td>
          <td class="acts">${r.builtin ? "" : `<button class="btn sm r-edit">${th("admin.common.edit")}</button>`}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="hint">${th("admin.roles.protect")}</p>
      <p class="hint">${th("admin.users.emergency")}</p></div>
    ${accessCard(st, me, d.roles)}`;
  $("#cert-only").addEventListener("change", async (e) => {
    const want = e.target.checked;
    if (want && !confirm(t("admin.access.on_confirm"))) { e.target.checked = false; return; }
    if (await guard(() => api("PUT", "/settings", { admin_cert_only: want }), t(want ? "admin.access.on_done" : "admin.access.off_done"))) RENDER.users();
    else e.target.checked = !want;
  });
  $("#idle-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const minutes = Math.round(Number($("#idle-min").value));
    if (!(minutes >= 10 && minutes <= 120)) return toast(t("admin.access.idle_range"), true);
    await guard(() => api("PUT", "/settings", { admin_idle_minutes: minutes }), t("admin.access.idle_saved", { minutes }));
  });
  $("#totp-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const roles = [...e.target.querySelectorAll('input[name="role"]:checked')].map((x) => x.value);
    await guard(() => api("PUT", "/settings", { totp_required_roles: roles }), t("admin.totp.required_saved"));
  });
  $("#role-add").addEventListener("click", () => roleDialog(d, null));
  $("#main").querySelectorAll("tr[data-rid] .r-edit").forEach((b) => b.addEventListener("click", () =>
    roleDialog(d, d.roles.find((r) => r.id === b.closest("tr").dataset.rid))));
  $("#user-add").addEventListener("click", () => editDialog({
    title: th("admin.users.new"),
    saveLabel: th("admin.users.add"),
    body: `<div class="form" id="newuser">
        <label>${th("admin.users.name")}<input name="name" required maxlength="40" autocomplete="off" style="width:200px"></label>
        <label>${th("admin.users.role")}${roleSel("view")}</label>
        <label>${th("admin.users.password")}<input name="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password" style="width:200px"></label>
      </div><p class="hint">${th("admin.users.pw_rule")}</p>`,
    onSave: async (f) => {
      const body = { name: f.name.value.trim(), role: f.role.value, password: f.password.value };
      const ok = await guard(() => api("POST", "/users", body), t("admin.users.added", { name: body.name }));
      if (ok) RENDER.users();
      return !!ok;
    },
  }));
  $("#main").querySelectorAll("tr[data-uid]").forEach((tr) => {
    const uid = tr.dataset.uid, u = d.users.find((x) => String(x.id) === uid), self = isSelf(u);
    // «Aktiv» ist ein Schalter für den laufenden Betrieb: sofort gespeichert (T28)
    tr.querySelector(".u-active").addEventListener("change", async (e) => {
      if (await guard(() => api("PUT", `/users/${uid}`, { active: e.target.checked }), t("admin.common.saved"))) RENDER.users();
      else e.target.checked = !e.target.checked;
    });
    tr.querySelector(".u-edit").addEventListener("click", () => editDialog({
      title: esc(u.name),
      body: `<div class="form u-form"><label>${th("admin.users.role")}${roleSel(u.role, self)}</label>
          <label>${th("admin.users.new_password")}<input name="password" type="password" minlength="8" maxlength="128"
            autocomplete="new-password" style="width:200px"></label></div>
        <p class="hint">${th("admin.users.pw_keep")} ${th("admin.users.pw_rule")}</p>
        ${u.totp_on ? `<label class="check"><input type="checkbox" name="totp_reset"> ${th("admin.totp.reset")}</label>` : ""}`,
      extra: self ? "" : `<button type="button" class="btn danger u-del">${th("admin.common.delete")}</button>`,
      onOpen: (dlg) => dlg.querySelector(".u-del")?.addEventListener("click", async () => {
        if (!confirm(t("admin.users.delete_confirm", { name: u.name }))) return;
        if (await guard(() => api("DELETE", `/users/${uid}`), t("admin.users.deleted", { name: u.name }))) { dlg.close(); RENDER.users(); }
      }),
      onSave: async (f) => {
        const body = {};
        if (!self && f.role.value !== u.role) body.role = f.role.value;
        if (f.password.value) body.password = f.password.value;
        if (f.totp_reset?.checked) body.totp_reset = true;
        if (!Object.keys(body).length) return true;
        const ok = await guard(() => api("PUT", `/users/${uid}`, body),
          body.password ? t("admin.users.pw_set", { name: u.name }) : body.totp_reset ? t("admin.totp.reset_done", { name: u.name })
            : t("admin.common.saved"));
        if (ok) RENDER.users();
        return !!ok;
      },
    }));
  });
};
