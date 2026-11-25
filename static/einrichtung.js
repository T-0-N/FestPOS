/* FestPOS – Ersteinrichtung im Browser (T11). Läuft nur, solange der Pi keine festpos.conf hat (app/setup_mode.py).
   Pro Schritt ein grüner Hauptknopf unten; «Weiter, Weiter, Fertig» ergibt einen lauffähigen Einzel-Pi. */
"use strict";
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = I18n.escape;
  const W = { step: "code", way: "neu", token: "", info: null, lang: "", pw: "", pw2: "", takeTime: true,
    values: null, file: null, filePw: "", cards: null, letter: "", size: 1, node: "a", busy: false, done: null,
    keySaved: false, keyPw: "", keyPw2: "", keyDone: "", keyErr: "", upKey: "", upKeyName: "", error: "" };
  const KEY_PW_MIN = 12;                                   // ca.key nur verschlüsselt (app/setup_mode.py, CA_PW_MIN)
  const WAYS = { neu: ["password", "time", "net", "rtc", "sum"], sicherung: ["file", "pi", "net", "time", "sum"],
    karte: ["zip", "time", "sum"] };

  async function api(path, opts = {}) {
    const r = await fetch(path, { cache: "no-store", ...opts });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.status);
    return j;
  }
  const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const steps = () => ["code", "way", ...WAYS[W.way]];

  function frame(body, next, { nextOff = false, back = true } = {}) {
    const list = steps(), i = list.indexOf(W.step);
    const pills = i >= 0 ? `<ol class="wsteps">${list.map((s, k) => `<li class="${k === i ? "on" : k < i ? "done" : ""}">${k + 1}</li>`).join("")}</ol>` : "";
    return `<div class="card wiz" id="swiz"><div class="wtop">${pills}</div>${body}
      ${W.error ? `<p class="err" id="s-err">${esc(W.error)}</p>` : ""}
      <div class="wiz-foot">${back && i > 0 ? `<button type="button" class="btn" id="s-back">${th("admin.setup.back")}</button>` : ""}
        ${next ? `<button type="button" class="btn pri" id="s-next" ${nextOff || W.busy ? "disabled" : ""}>${esc(next)}</button>` : ""}</div></div>`;
  }
  const field = (k, label, val) => `<label>${th(label)}<input name="${k}" value="${esc(val)}" inputmode="decimal" autocomplete="off"></label>`;
  const radio = (name, v, on, title, sub) => `<label><input type="radio" name="${name}" value="${v}" ${on ? "checked" : ""}>
    <b>${th(title)}</b><span>${th(sub)}</span></label>`;

  function draw() {
    const m = $("#s-main"), v = W.values;
    if (W.done) { m.innerHTML = doneView(); bind(); return; }
    if (W.step === "code") {
      m.innerHTML = frame(`<h3>${th("admin.setup.code_h")}</h3><p class="hint">${th("admin.setup.code_hint", { ips: (W.info?.ips || []).join(", ") || "–" })}</p>
        ${location.protocol === "https:" ? `<p class="hint" id="s-certhint">${th("admin.setup.cert_hint")}</p>` : ""}
        <div class="form col"><label>${th("admin.setup.code")}<input id="s-code" autocomplete="off" autocapitalize="characters" style="max-width:240px;font-size:22px"></label></div>`,
      t("admin.setup.next"));
    } else if (W.step === "way") {
      m.innerHTML = frame(`<h3>${th("admin.setup.way_h")}</h3><div class="choice" id="s-way">
        ${radio("way", "neu", W.way === "neu", "admin.setup.way_neu", "admin.setup.way_neu_sub")}
        ${radio("way", "sicherung", W.way === "sicherung", "admin.setup.way_backup", "admin.setup.way_backup_sub")}
        ${radio("way", "karte", W.way === "karte", "admin.setup.way_cards", "admin.setup.way_cards_sub")}</div>`, t("admin.setup.next"));
    } else if (W.step === "password") {
      W.tip = W.tip || suggestion();
      m.innerHTML = frame(`<h3>${th("admin.setup.pw_h")}</h3><p class="hint">${th("admin.setup.pw_hint", { min: pwMin() })}</p>
        <div class="warnbox" id="s-tip"><p>${th("admin.setup.pw_tip")}</p><p class="pw-tip"><code>${esc(W.tip)}</code></p>
          <div class="form"><button type="button" class="btn" id="s-tip-take">${th("admin.setup.pw_tip_take")}</button>
          <button type="button" class="btn" id="s-tip-new">${th("admin.setup.pw_tip_new")}</button></div></div>
        <div class="form col"><label>${th("admin.setup.pw")}<input type="password" id="s-pw" autocomplete="new-password" minlength="${pwMin()}" value="${esc(W.pw)}"></label>
        <label>${th("admin.setup.pw2")}<input type="password" id="s-pw2" autocomplete="new-password" value="${esc(W.pw2)}"></label></div>`,
      t("admin.setup.next"), { nextOff: !pwOk() });
    } else if (W.step === "time") {
      const pi = W.info ? new Date(W.info.server_time * 1000) : null;
      m.innerHTML = frame(`<h3>${th("admin.setup.time_h")}</h3>
        <p>${th("admin.setup.time_here", { time: I18n.fmtDateTime(Date.now()) })}<br>${th("admin.setup.time_pi", { time: pi ? I18n.fmtDateTime(pi) : "–" })}</p>
        <label class="check"><input type="checkbox" id="s-take" ${W.takeTime ? "checked" : ""}> ${th("admin.setup.time_take")}</label>
        <p class="hint">${th("admin.setup.time_tz")}</p>`, t("admin.setup.next"));
    } else if (W.step === "pi") {
      m.innerHTML = frame(`<h3>${th("admin.setup.pi_h")}</h3><div class="choice" id="s-size">
        ${radio("size", "1", W.size === 1, "admin.setup.pi_one", "admin.setup.pi_one_sub")}
        ${radio("size", "3", W.size === 3, "admin.setup.pi_three", "admin.setup.pi_three_sub")}</div>
        ${W.size === 3 ? `<div class="choice" id="s-node" style="margin-top:10px">${["a", "b", "c"].map((l) => radio("node", l, W.node === l, "admin.setup.pi_letter", "admin.setup.pi_letter_sub")
          .replace("{pi}", l.toUpperCase())).join("")}</div>` : ""}`, t("admin.setup.next"));
    } else if (W.step === "net") {          // Netz mit Grösse frei wählbar (T-0-N/FestPOS#2)
      m.innerHTML = frame(`<h3>${th("admin.setup.net_h")}</h3><p class="hint">${th("admin.setup.net_hint")}</p>
        <div class="form col" id="s-net">
          ${field("net", "admin.setup.net_net", v.net)}<p class="hint" id="s-netinfo"></p>
          ${field("vip", "admin.setup.net_vip", v.vip)}${field("a", "admin.setup.net_a", v.a)}
          <details id="s-netmore"><summary>${th("admin.setup.net_more")}</summary><div class="form col" style="margin-top:8px">
            ${field("b", "admin.setup.net_b", v.b)}${field("c", "admin.setup.net_c", v.c)}
            ${field("dhcp_from", "admin.setup.net_dhcp_from", v.dhcp_from)}${field("dhcp_to", "admin.setup.net_dhcp_to", v.dhcp_to)}</div></details>
          <label class="check"><input type="checkbox" name="dhcp" ${v.dhcp ? "checked" : ""}> ${th("admin.setup.net_dhcp")}</label></div>`,
      t("admin.setup.next"));
    } else if (W.step === "rtc") {
      m.innerHTML = frame(`<h3>${th("admin.setup.rtc_h")}</h3><div class="choice" id="s-rtc">
        ${radio("rtc", "0", !v.rtc_akku, "admin.setup.rtc_no", "admin.setup.rtc_no_sub")}
        ${radio("rtc", "1", v.rtc_akku, "admin.setup.rtc_yes", "admin.setup.rtc_yes_sub")}</div>`, t("admin.setup.next"));
    } else if (W.step === "file") {
      m.innerHTML = frame(`<h3>${th("admin.setup.file_h")}</h3><p class="hint">${th("admin.setup.file_hint")}</p>
        <div class="form col"><label>${th("admin.setup.file")}<input type="file" id="s-file" accept=".festpos"></label>
        <label>${th("admin.setup.file_pw")}<input id="s-fpw" autocomplete="off" value="${esc(W.filePw)}"></label></div>`,
      t("admin.setup.next"), { nextOff: !W.file || !W.filePw });
    } else if (W.step === "zip") {
      m.innerHTML = frame(`<h3>${th("admin.setup.zip_h")}</h3><p class="hint">${th("admin.setup.zip_hint")}</p>
        <div class="form col"><label>${th("admin.setup.zip")}<input type="file" id="s-zip" accept=".zip"></label></div>
        ${W.cards ? `<div class="choice" id="s-letters">${W.cards.map((l) => radio("letter", l, W.letter === l, "admin.setup.pi_letter", "admin.setup.zip_letter_sub")
          .replace("{pi}", l.toUpperCase())).join("")}</div>` : ""}`,
      t("admin.setup.next"), { nextOff: !W.file || (W.cards && !W.letter) });
    } else if (W.step === "sum") {
      const rows = W.way === "karte" ? [["admin.setup.sum_way", t("admin.setup.way_cards")], ["admin.setup.sum_pi", `Pi ${W.letter.toUpperCase()}`]]
        : [["admin.setup.sum_way", t(W.way === "neu" ? "admin.setup.way_neu" : "admin.setup.way_backup")],
          ["admin.setup.sum_pi", W.size === 3 ? t("admin.setup.sum_three", { pi: W.node.toUpperCase() }) : t("admin.setup.pi_one")],
          ["admin.setup.sum_addr", v.vip], ["admin.setup.sum_dhcp", t(v.dhcp ? "admin.setup.yes" : "admin.setup.no")],
          ...(W.way === "neu" ? [["admin.setup.sum_rtc", t(v.rtc_akku ? "admin.setup.yes" : "admin.setup.no")]] : [])];
      rows.push(["admin.setup.sum_time", t(W.takeTime ? "admin.setup.sum_time_take" : "admin.setup.sum_time_keep")]);
      if (W.way === "neu") rows.push(["admin.setup.sum_updates", t(W.upKey ? "admin.setup.sum_upd_own" : "admin.setup.sum_upd_project")]);
      // Updates: der Projekt-Schlüssel liegt im Image; wer eigene Versionen signiert, gibt hier seinen dazu
      const upkey = W.way !== "neu" ? "" : `<details class="setup-upkey"${W.upKey ? " open" : ""}><summary>${th("admin.setup.upkey_h")}</summary>
        <p class="hint">${th("admin.setup.upkey_hint")}</p>
        <label>${th("admin.setup.upkey_file")}<input type="file" id="s-upkey" accept=".pub"></label>
        ${W.upKeyName ? `<p class="meta">${esc(W.upKeyName)}</p>` : ""}</details>`;
      m.innerHTML = frame(`<h3>${th("admin.setup.sum_h")}</h3><table class="tbl"><tbody>${rows.map(([k, x]) =>
        `<tr><th>${th(k)}</th><td>${esc(x)}</td></tr>`).join("")}</tbody></table>${upkey}
        <p class="hint">${th("admin.setup.sum_hint")}</p>`, t(W.busy ? "admin.setup.working" : "admin.setup.go"));
    }
    bind();
  }

  function doneView() {
    const d = W.done, vip = d.vip || W.values?.vip || "192.168.55.10";
    const key = d.mode === "neu" && W.hasKey;
    return `<div class="card wiz" id="swiz"><h3 class="okmsg" id="s-done">${th("admin.setup.done_h")}</h3>
      ${key ? `<div class="warnbox alarm" id="s-keybox"><p>${th("admin.setup.key_hint")}</p><p>${th("admin.setup.key_pw_hint")}</p>
        <div class="form col">
          <label class="field">${th("admin.setup.key_pw", { min: KEY_PW_MIN })}<input type="password" id="s-kpw" autocomplete="new-password" value="${esc(W.keyPw)}"></label>
          <label class="field">${th("admin.setup.key_pw2")}<input type="password" id="s-kpw2" autocomplete="new-password" value="${esc(W.keyPw2)}"></label></div>
        <div class="form"><button type="button" class="btn" id="s-key-usb" ${keyPwOk() ? "" : "disabled"}>${th("admin.setup.key_usb")}</button>
          <button type="button" class="btn" id="s-key" ${keyPwOk() ? "" : "disabled"}>${th("admin.setup.key_dl")}</button>
          <a class="btn" href="/ca.crt" download="ca.crt">${th("admin.setup.crt_dl")}</a></div>
        ${W.keyDone ? `<p class="okmsg" id="s-keydone">✓ ${esc(W.keyDone)}</p>` : ""}${W.keyErr ? `<p class="err" id="s-keyerr">${esc(W.keyErr)}</p>` : ""}
        <label class="check"><input type="checkbox" id="s-keyok" ${W.keySaved ? "checked" : ""} ${W.keyDone ? "" : "disabled"}> ${th("admin.setup.key_saved")}</label></div>` : ""}
      ${d.mode === "sicherung" ? `<p>${th("admin.setup.done_backup")}</p>` : ""}
      <ol class="steps"><li>${th("admin.setup.done_s1")}</li><li>${th("admin.setup.done_s2", { url: `https://${vip}:8443/admin.html` })}</li>
        <li>${th("admin.setup.done_s3", { url: `http://${vip}/ca.crt` })}</li></ol>
      ${W.error ? `<p class="err">${esc(W.error)}</p>` : ""}
      <div class="wiz-foot"><button type="button" class="btn pri" id="s-restart" ${key && !W.keySaved ? "disabled" : ""}>${th(W.restarting ? "admin.setup.restarting" : "admin.setup.restart")}</button></div></div>`;
  }

  const pwMin = () => W.info?.pw_min || 16;                // Notfall-Passwort: mindestens 16 Zeichen
  const pwOk = () => W.pw.length >= pwMin() && W.pw === W.pw2;
  // Vorschlag: 4 Gruppen à 4 Zeichen ohne Verwechsler (0/O, 1/I) – 80 Bit, leicht abzuschreiben (wie das
  // Sicherungs-Passwort)
  function suggestion() {
    const abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789", r = new Uint32Array(16);
    crypto.getRandomValues(r);
    const c = [...r].map((x) => abc[x % abc.length]).join("");
    return [0, 4, 8, 12].map((i) => c.slice(i, i + 4)).join("-");
  }
  const keyPwOk = () => W.keyPw.length >= KEY_PW_MIN && W.keyPw === W.keyPw2;
  // ca.key verschlüsselt sichern: als Download (fetch + Blob) oder auf den USB-Stick am Pi
  async function saveKey(usb) {
    W.keyErr = "";
    for (const b of ["#s-key", "#s-key-usb"]) $(b).disabled = true;      // USB kann ein paar Sekunden dauern
    const body = json({ token: W.token, password: W.keyPw });
    try {
      if (usb) {
        const r = await api("/api/einrichtung/ca.key/usb", body);
        W.keyDone = t("admin.setup.key_usb_done", { device: r.device || "", path: r.path || "" });
      } else {
        const r = await fetch("/api/einrichtung/ca.key", { cache: "no-store", ...body });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
        const url = URL.createObjectURL(await r.blob());
        Object.assign(document.createElement("a"), { href: url, download: "ca.key" }).click();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
        W.keyDone = t("admin.setup.key_dl_done");
      }
    } catch (e) { W.keyErr = e.message; }
    draw();
  }
  function readNet() {
    const f = $("#s-net");
    if (!f) return;
    for (const k of NET_KEYS) W.values[k] = f.querySelector(`[name=${k}]`).value.trim();
    W.values.dhcp = f.querySelector("[name=dhcp]").checked;
  }
  // Netz «10.20.0.0/22» -> {base, size} oder null (nur die Netzadresse, /16 bis /27 wie app/netz.py)
  const NET_KEYS = ["net", "vip", "a", "b", "c", "dhcp_from", "dhcp_to"];
  const ip2n = (s) => { const p = String(s).split("."); return p.length === 4 && p.every((x) => /^\d{1,3}$/.test(x) && +x < 256)
    ? p.reduce((a, x) => a * 256 + +x, 0) : null; };
  const n2ip = (n) => [24, 16, 8, 0].map((s) => Math.floor(n / 2 ** s) % 256).join(".");
  function parseNet(s) {
    const m = /^([\d.]+)\/(\d{1,2})$/.exec(String(s).trim());
    const base = m && ip2n(m[1]), bits = m && +m[2];
    if (base == null || !(bits >= 16 && bits <= 27)) return null;
    const size = 2 ** (32 - bits);
    return base % size ? null : { base, size };
  }
  function netDefaults(n) {
    const at = (o) => n2ip(n.base + Math.max(1, Math.min(o, n.size - 2)));
    const big = n.size >= 256;
    return { vip: at(10), a: at(11), b: at(12), c: at(13), dhcp_from: at(big ? 150 : n.size / 2), dhcp_to: at(big ? 189 : n.size - 2) };
  }
  function bindNet() {
    const f = $("#s-net");
    if (!f) return;
    const info = () => {
      const n = parseNet(f.querySelector("[name=net]").value);
      const el = $("#s-netinfo");
      el.className = n ? "hint" : "err";
      el.textContent = n ? t("admin.setup.net_size", { first: n2ip(n.base), last: n2ip(n.base + n.size - 1), count: n.size - 2 })
        : t("admin.setup.net_invalid");
      return n;
    };
    f.querySelector("[name=net]").addEventListener("change", () => {   // anderes Netz: Adressen ausserhalb neu vorschlagen
      const n = info();
      if (!n) return;
      const d = netDefaults(n);
      for (const k of Object.keys(d)) {
        const el = f.querySelector(`[name=${k}]`), x = ip2n(el.value);
        if (x == null || x <= n.base || x >= n.base + n.size - 1) el.value = d[k];
      }
    });
    f.querySelector("[name=net]").addEventListener("input", info);
    info();
  }
  function vals() { return { ...W.values, cluster_size: W.way === "neu" ? 1 : W.size, node: W.way === "neu" ? "a" : W.node }; }

  async function go() {
    W.error = "";
    if (W.step === "code") {
      try {
        W.token = (await api("/api/einrichtung/code", json({ code: $("#s-code").value }))).token;
        try { sessionStorage.setItem("festpos.setup.token", W.token); } catch { /* egal */ }
      }
      catch (e) { W.error = e.message; return draw(); }
    } else if (W.step === "net") readNet();
    else if (W.step === "zip" && W.file && !W.cards) {
      const r = await upload("/api/einrichtung/kartenpaket", { file: W.file });
      if (!r) return;
      W.cards = r.letters || [];
      if (W.cards.length === 1) W.letter = W.cards[0];
      return draw();
    } else if (W.step === "sum") return finish();
    const list = steps();
    W.step = list[list.indexOf(W.step) + 1];
    draw();
  }

  async function upload(path, fields) {
    const fd = new FormData();
    fd.append("token", W.token);
    fd.append("lang", W.lang);
    fd.append("time_ms", W.takeTime ? String(Date.now()) : "0");
    for (const [k, x] of Object.entries(fields)) fd.append(k, x);
    // X-Festpos-Setup: der Pi prüft den Schlüssel, BEVOR er die Datei liest (app/setup_mode.py, SetupGate)
    try { return await api(path, { method: "POST", body: fd, headers: { "X-Festpos-Setup": W.token } }); } catch (e) { W.error = e.message; W.busy = false; draw(); return null; }
  }

  async function finish() {
    W.busy = true; draw();
    let r;
    if (W.way === "neu") {
      try {
        r = await api("/api/einrichtung/neu", json({ token: W.token, lang: W.lang, time_ms: W.takeTime ? Date.now() : null,
          admin_password: W.pw, values: vals(), update_key: W.upKey || null }));
      } catch (e) { W.error = e.message; W.busy = false; return draw(); }
      W.hasKey = r.ca_key;
    } else if (W.way === "sicherung") {
      r = await upload("/api/einrichtung/sicherung", { file: W.file, password: W.filePw, values: JSON.stringify(vals()) });
    } else {
      r = await upload("/api/einrichtung/kartenpaket", { file: W.file, letter: W.letter });
    }
    W.busy = false;
    if (!r) return;
    W.done = { mode: W.way, vip: W.way === "karte" ? "" : W.values.vip, node: r.node };
    draw();
  }

  function bind() {
    $("#s-back")?.addEventListener("click", () => { const l = steps(); W.error = ""; W.step = l[Math.max(0, l.indexOf(W.step) - 1)]; draw(); });
    $("#s-next")?.addEventListener("click", go);
    $("#s-code")?.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    $("#s-code")?.focus();
    document.querySelectorAll("#s-way input").forEach((r) => r.addEventListener("change", () => { W.way = r.value; W.file = null; W.cards = null; }));
    document.querySelectorAll("#s-size input").forEach((r) => r.addEventListener("change", () => { W.size = +r.value; if (W.size === 1) W.node = "a"; draw(); }));
    document.querySelectorAll("#s-node input").forEach((r) => r.addEventListener("change", () => { W.node = r.value; }));
    document.querySelectorAll("#s-rtc input").forEach((r) => r.addEventListener("change", () => { W.values.rtc_akku = r.value === "1"; }));
    document.querySelectorAll("#s-letters input").forEach((r) => r.addEventListener("change", () => { W.letter = r.value; $("#s-next").disabled = false; }));
    const pw = () => { W.pw = $("#s-pw").value; W.pw2 = $("#s-pw2").value; $("#s-next").disabled = !pwOk(); };
    $("#s-pw")?.addEventListener("input", pw);
    $("#s-tip-take")?.addEventListener("click", () => {     // Vorschlag in beide Felder (sichtbar machen zum Abschreiben)
      W.pw = W.pw2 = W.tip;
      draw();
      for (const id of ["#s-pw", "#s-pw2"]) $(id).type = "text";
    });
    $("#s-tip-new")?.addEventListener("click", () => { W.tip = suggestion(); draw(); });
    $("#s-pw2")?.addEventListener("input", pw);
    $("#s-take")?.addEventListener("change", (e) => { W.takeTime = e.target.checked; });
    bindNet();                                           // Netz: Grösse anzeigen, Vorschläge (#2)
    $("#s-upkey")?.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      W.upKey = f && f.size < 4096 ? (await f.text()).trim() : "";
      W.upKeyName = W.upKey ? f.name : "";
      W.error = f && !W.upKey.startsWith("ssh-ed25519 ") ? t("err.setup.update_key") : "";
      if (W.error) { W.upKey = ""; W.upKeyName = ""; }
      draw();
    });
    const file = (e) => { W.file = e.target.files[0] || null; W.cards = null; W.letter = ""; $("#s-next").disabled = !W.file || (W.step === "file" && !W.filePw); };
    $("#s-file")?.addEventListener("change", file);
    $("#s-zip")?.addEventListener("change", file);
    $("#s-fpw")?.addEventListener("input", (e) => { W.filePw = e.target.value; $("#s-next").disabled = !W.file || !W.filePw; });
    $("#s-keyok")?.addEventListener("change", (e) => { W.keySaved = e.target.checked; $("#s-restart").disabled = !W.keySaved; });
    const kpw = () => {
      W.keyPw = $("#s-kpw").value; W.keyPw2 = $("#s-kpw2").value;
      for (const b of ["#s-key", "#s-key-usb"]) $(b).disabled = !keyPwOk();
    };
    $("#s-kpw")?.addEventListener("input", kpw);
    $("#s-kpw2")?.addEventListener("input", kpw);
    $("#s-key")?.addEventListener("click", () => saveKey(false));
    $("#s-key-usb")?.addEventListener("click", () => saveKey(true));
    $("#s-restart")?.addEventListener("click", async () => {
      if (!confirm(t("admin.setup.restart_q"))) return;
      try { await api("/api/einrichtung/neustart", json({ token: W.token })); W.restarting = true; W.error = ""; }
      catch (e) { W.error = e.message; }
      draw();
    });
  }

  try { W.token = sessionStorage.getItem("festpos.setup.token") || ""; } catch { /* egal */ }
  async function start() {
    const want = (() => { try { return localStorage.getItem("festpos.setup.lang") || ""; } catch { return ""; } })();
    await I18n.load(want || "auto");
    W.lang = I18n.lang;
    I18n.apply();
    try { W.info = await api("/api/einrichtung"); } catch { W.info = null; }
    W.values = { ...(W.info?.defaults || { net: "192.168.55.0/24", vip: "192.168.55.10", a: "192.168.55.11", b: "192.168.55.12",
      c: "192.168.55.13", dhcp_from: "192.168.55.150", dhcp_to: "192.168.55.189", dhcp: true, rtc_akku: false, tz: "Europe/Zurich" }) };
    const sel = $("#s-lang");
    sel.innerHTML = (W.info?.langs || [{ code: "de", name: "Deutsch" }]).map((l) =>
      `<option value="${esc(l.code)}" ${l.code === W.lang ? "selected" : ""}>${esc(l.name)}</option>`).join("");
    sel.addEventListener("change", async () => {
      try { localStorage.setItem("festpos.setup.lang", sel.value); } catch { /* egal */ }
      await I18n.load(sel.value);
      W.lang = I18n.lang;
      I18n.apply();
      draw();
    });
    if (W.info?.done) W.done = W.info.done;
    draw();
  }
  document.addEventListener("DOMContentLoaded", start);
})();
