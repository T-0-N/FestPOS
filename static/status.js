/* FestPOS Statusanzeige (status.html). Eigene Datei wegen der Content-Security-Policy. */
"use strict";
if (window.FESTPOS_FRAME) throw new Error("Rahmen");   // Inhalt läuft im gedrehten Rahmen
// Ansicht: klein (3,5"-Display am Pi, Handy) | pi (Bildschirm am Pi, von weitem) | tablet | pc.
// Automatisch; übersteuern mit status.html?ansicht=klein|pi|tablet|pc (bleibt in diesem Browser), ?ansicht=auto zurück.
const VIEWS = ["klein", "pi", "tablet", "pc"];
const VIEW = (() => {
  let q = "";
  try { q = new URLSearchParams(location.search).get("ansicht") || new URLSearchParams(top.location.search).get("ansicht") || ""; } catch { /* egal */ }
  try {
    if (q === "auto") localStorage.removeItem("festpos.status.ansicht");
    else if (VIEWS.includes(q)) localStorage.setItem("festpos.status.ansicht", q);
    q = localStorage.getItem("festpos.status.ansicht") || "";
  } catch { /* ohne Speicher: nur für diesen Aufruf */ }
  if (VIEWS.includes(q)) return q;
  if (matchMedia("(max-width: 560px)").matches) return "klein";
  if (["127.0.0.1", "localhost", "[::1]", "::1"].includes(location.hostname)) return "pi";   // Bildschirm am Pi
  return matchMedia("(any-pointer: fine)").matches ? "pc" : "tablet";                      // Maus da? sonst Touch
})();
// kleines Display (3,5"): kompakt, alles untereinander, mit dem Finger scrollbar
const SMALL = VIEW === "klein";
document.documentElement.classList.toggle("small", SMALL);
// Handy (nicht das kleine Display am Pi): hochkant alles untereinander, Bestellungen zuletzt (siehe status.html)
const ON_PI = ["127.0.0.1", "localhost", "[::1]", "::1"].includes(location.hostname);
document.documentElement.classList.toggle("phone", SMALL && !ON_PI);
if (!SMALL) document.documentElement.classList.add("v-" + VIEW);
// Mauszeiger nur zeigen, während die Maus bewegt wird (Berührungen zeigen keinen Pfeil)
let cursorTimer = null;
addEventListener("pointermove", (e) => {
  if (e.pointerType !== "mouse") return;
  document.documentElement.classList.remove("nocursor");
  clearTimeout(cursorTimer);
  cursorTimer = setTimeout(() => document.documentElement.classList.add("nocursor"), 3000);
}, { passive: true });
let idle = null;                                     // nach 30 s ohne Berührung wieder nach oben
addEventListener("scroll", () => { clearTimeout(idle); idle = setTimeout(() => scrollTo({ top: 0, behavior: "smooth" }), 30000); }, { passive: true });
// Touch-Korrektur: kleine Touch-Displays sind oft verdreht/gespiegelt/verschoben. Die Seite rechnet die
// Berührung selbst um (3-Punkt-Einrichtung). Ein/aus und gespeichert über die Verwaltung (Bildschirm am Pi).
const TOP = (() => { try { return top.location.search; } catch { return ""; } })();
const CAL_KEY = "festpos.touchcal." + (new URLSearchParams(TOP).get("drehung") || "0");
let CAL = [1, 0, 0, 0, 1, 0];                         // x' = a·x + b·y + c,  y' = d·x + e·y + f  (0…1)
try { const c = JSON.parse(localStorage.getItem(CAL_KEY)); if (Array.isArray(c) && c.length === 6) CAL = c; } catch { /* ohne */ }
function touchAt(e) {                                 // korrigierte Position in Pixeln
  const x = e.clientX / innerWidth, y = e.clientY / innerHeight, [a, b, c, d, f, g] = CAL;
  return { x: (a * x + b * y + c) * innerWidth, y: (d * x + f * y + g) * innerHeight };
}
let calibrating = false;
const Touch = (() => {
  let box, dot, cross, active = false, hideAt = 0;
  const TARGETS = [[0.25, 0.28], [0.75, 0.28], [0.25, 0.74]], got = [];
  const make = () => {
    if (box) return;
    box = document.createElement("div"); dot = document.createElement("div"); cross = document.createElement("div");
    box.style.cssText = "position:fixed;left:22%;right:22%;top:50%;transform:translateY(-50%);z-index:11;text-align:center;background:#000d;color:#ff0;font:15px/1.3 sans-serif;padding:6px 8px;border-radius:6px;pointer-events:none;white-space:pre-line";
    dot.style.cssText = "position:fixed;z-index:11;width:22px;height:22px;margin:-11px;border-radius:50%;background:#e5484d;pointer-events:none;display:none";
    cross.style.cssText = "position:fixed;z-index:12;width:44px;height:44px;margin:-22px;pointer-events:none;display:none;" +
      "background:linear-gradient(#ff0,#ff0) center/3px 100% no-repeat,linear-gradient(#ff0,#ff0) center/100% 3px no-repeat;border:2px solid #ff0;border-radius:50%";
    document.body.append(box, dot, cross);
  };
  const showTarget = () => {
    const [tx, ty] = TARGETS[got.length];
    cross.style.display = "block"; cross.style.left = tx * innerWidth + "px"; cross.style.top = ty * innerHeight + "px";
    box.textContent = t("status.touch.tap", { n: got.length + 1 });
  };
  const solve = (O, T) => {                           // affine Abbildung O -> T aus 3 Punkten (Cramer)
    const det = (m) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    const A = O.map(([x, y]) => [x, y, 1]), D = det(A);
    if (Math.abs(D) < 1e-3) return null;
    const col = (v, i) => A.map((r, k) => r.map((x, j) => (j === i ? v[k] : x)));
    const row = (v) => [0, 1, 2].map((i) => det(col(v, i)) / D);
    return [...row(T.map((x) => x[0])), ...row(T.map((x) => x[1]))];
  };
  addEventListener("pointerdown", (e) => {
    if (!calibrating) return;
    got.push([e.clientX / innerWidth, e.clientY / innerHeight]);   // ungekorrigiert
    if (got.length < 3) return showTarget();
    const m = solve(got, TARGETS);
    got.length = 0;
    if (!m) { box.textContent = t("status.touch.retry"); return setTimeout(showTarget, 1500); }
    CAL = m; calibrating = false; cross.style.display = "none"; hideAt = Date.now() + 20000;
    try { localStorage.setItem(CAL_KEY, JSON.stringify(m)); } catch { /* egal */ }
    box.textContent = t("status.touch.saved");
    fetch("/api/status/touchcal", { method: "POST", headers: { "Content-Type": "application/json", "X-Festpos-UI": "1" },
      body: JSON.stringify({ cal: m }) }).catch(() => {});
  }, { passive: true });
  const show = (e) => {
    if (!active || calibrating) return;
    const p = touchAt(e);
    dot.style.display = "block"; dot.style.left = p.x + "px"; dot.style.top = p.y + "px";
  };
  for (const ev of ["pointerdown", "pointermove"]) addEventListener(ev, show, { passive: true });
  setInterval(() => { if (active && !calibrating && hideAt && Date.now() > hideAt) stop(); }, 1000);
  function start() {
    make(); active = true; calibrating = true; got.length = 0; hideAt = 0;
    box.style.display = "block"; showTarget();
  }
  function stop() {
    active = false; calibrating = false; hideAt = 0;
    if (box) { box.style.display = cross.style.display = dot.style.display = "none"; }
  }
  return { start, stop, get active() { return active; }, get busy() { return active && (calibrating || hideAt > Date.now()); } };
})();
if (/[?&]touchtest=1/.test(location.search) || /[?&]touchtest=1/.test(TOP)) addEventListener("load", () => Touch.start());

// Einstellungen aus der Verwaltung (nur am Pi selbst geliefert): Drehung/Grösse -> Seite neu aufbauen, Touch
function applyKiosk(k) {
  if (!k || !k.type) return;                          // nichts gewählt: alte Einstellung am Pi gilt
  if (Array.isArray(k.cal) && k.cal.length === 6) CAL = k.cal;
  const q = new URLSearchParams(TOP);
  const want = new URLSearchParams();
  if (k.rotate) want.set("drehung", k.rotate);
  if (k.size) want.set("groesse", k.size);
  const now = new URLSearchParams();
  for (const key of ["drehung", "groesse"]) if (q.get(key)) now.set(key, q.get(key));
  if (want.toString() !== now.toString()) {
    try { top.location.replace("/status.html" + (want.toString() ? "?" + want : "")); } catch { /* egal */ }
    return;
  }
  if (k.touchtest && !Touch.active) Touch.start();
  else if (!k.touchtest && Touch.active && !Touch.busy) Touch.stop();
}

// Mit dem Finger ziehen = scrollen. Selbst gebaut, weil kleine Touch-Displays (z. B. RB-TFT3.5) oft als
// Maus statt als Touch gemeldet werden – dann würde der Browser nur markieren statt scrollen.
if (SMALL) {
  let drag = null;
  addEventListener("pointerdown", (e) => {
    if (calibrating) return;
    const panel = document.querySelector("#detail-sheet:not([hidden]) .panel");     // offene Details: die scrollen
    drag = { y: touchAt(e).y, top: panel ? panel.scrollTop : scrollY, id: e.pointerId, panel };
  }, { passive: true });
  addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id || !e.buttons && e.pointerType === "mouse") return;
    const y = drag.top - (touchAt(e).y - drag.y);
    if (drag.panel) drag.panel.scrollTop = y; else scrollTo(0, y);
  }, { passive: true });
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) addEventListener(ev, () => { drag = null; }, { passive: true });
}
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Beträge in Schweizer Schreibweise (CHF), wie auf den Handys und Bons
const money = (c) => { const v = Math.abs(Math.round(c)); return (c < 0 ? "−" : "") + Math.floor(v / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, "'") + "." + String(v % 100).padStart(2, "0"); };
let skew = 0, version = null, fails = 0;

const hhmm = (iso) => (iso ? iso.slice(11, 16) : "–");
function since(sec) {
  if (sec == null) return t("status.dev.offline");
  if (sec < 60) return t("status.dev.just_active");
  const m = Math.round(sec / 60);
  return m < 60 ? t("status.dev.minutes_ago", { count: m }) : t("status.dev.hours_ago", { count: Math.floor(m / 60) });
}
function tickClock() {
  $("clock").textContent = I18n.fmtTime(Date.now() + skew);
}

// Logo (Verwaltung › Einstellungen › Logo) statt des grünen Zeichens – nur neu zeichnen, wenn es sich ändert
let brandKey = null;
function renderBrand(s) {
  const key = `${s.logo || ""}|${s.logo_plate}`;
  if (key === brandKey) return;
  brandKey = key;
  document.querySelector("header .brand").innerHTML = s.logo
    ? `<img class="logo${s.logo_plate ? " plate" : ""}" src="${esc(s.logo)}" alt=""><span>FestPOS</span>` : "<i></i><span>FestPOS</span>";
}
// Mobilfunk-Router (Empfang): für alle Router-Modelle gleich – der Server liefert einheitliche Werte (uplink.py)
const UP_STATE = { ok: "common.uplink.st_ok", pending: "common.uplink.st_pending", cert_new: "common.uplink.st_cert_new",
  cert: "common.uplink.st_cert", no_https: "common.uplink.st_no_https", auth: "common.uplink.st_auth",
  rights: "common.uplink.st_rights", iface: "common.uplink.st_iface", unreachable: "common.uplink.st_unreachable",
  timeout: "common.uplink.st_timeout", bad: "common.uplink.st_bad" };
const UP_WORD = { excellent: "common.uplink.w_excellent", good: "common.uplink.w_good", fair: "common.uplink.w_fair",
  weak: "common.uplink.w_weak", very_weak: "common.uplink.w_very_weak", none: "common.uplink.w_none" };
// Grund für «kein Netz» (für alle Router-Modelle gleich, siehe uplink.PROBLEMS)
const UP_PROBLEM = { no_sim: "common.uplink.p_no_sim", pin: "common.uplink.p_pin", puk: "common.uplink.p_puk",
  radio_off: "common.uplink.p_radio_off", denied: "common.uplink.p_denied", searching: "common.uplink.p_searching",
  not_registered: "common.uplink.p_not_registered", no_data: "common.uplink.p_no_data" };
const dbm = (v, unit = "dBm") => `${v < 0 ? "−" : ""}${Math.abs(v).toLocaleString(I18n.locale)} ${unit}`;
const upNet = (d) => [d.tech, [d.band, ...(d.ca || [])].filter(Boolean).join("+")].filter(Boolean).join(" ");
function uplinkLook(up) {
  const name = up.name || t("status.uplink.name"), d = up.data || {}, g = up.rating || {};
  if (up.state === "pending") return { cls: "", bars: null, main: t("common.uplink.st_pending"), sub: "" };
  const stale = up.seconds != null && up.seconds > Math.max(60, (up.interval || 10) * 6);   // Abfrage hängt
  if (up.state !== "ok" || stale) {
    const why = stale ? t("status.uplink.stale") : t(UP_STATE[up.state] || UP_STATE.bad);
    if (up.state === "cert_new") return { cls: "wait", bars: null, main: why, sub: "", warn: t("status.uplink.w_setup", { name }) };
    return { cls: "bad", bars: null, main: why, sub: up.data ? t("status.uplink.last", { value: d.rsrp != null ? dbm(d.rsrp) : "–" }) : "",
      problem: t("status.uplink.p_down", { name }) };
  }
  const sub = [d.operator, upNet(d)].filter(Boolean).join(" · ");
  if (g.level === "bad") {                            // Router erreichbar, aber kein Netz: Grund nennen (keine SIM …)
    const why = t(UP_PROBLEM[g.problem] || UP_WORD.none);
    return { cls: "bad", bars: g.problem === "no_data" ? g.bars : 0, main: why, sub, problem: t("status.uplink.p_problem", { name, why }) };
  }
  const rsrp = d.rsrp ?? d.nr_rsrp, val = rsrp != null ? dbm(rsrp) : d.rssi != null ? dbm(d.rssi) : "";
  const look = { cls: g.level === "wait" ? "wait" : "ok", bars: g.bars, main: [val, g.word ? t(UP_WORD[g.word]) : ""].filter(Boolean).join(" · "), sub };
  if (g.level === "wait") look.warn = t("status.uplink.w_weak", { name, value: val });
  return look;
}
const sigBars = (n) => `<span class="sig" aria-hidden="true">${[1, 2, 3, 4].map((i) => `<i class="${n != null && i <= n ? "on" : ""}"></i>`).join("")}</span>`;

// Nur so viele Bestellungen, wie ganz in die Karte passen – nach jedem Zeichnen und jeder Grössenänderung neu (die
// übrigen Karten wachsen oft erst danach: früher blieb dann eine halbe Zeile stehen). Kleines Display: alle, es scrollt.
function fitOrders() {
  const log = document.querySelector(".log"), rows = [...log.querySelectorAll(".order")];
  rows.forEach((r, i) => r.classList.toggle("cut", SMALL && i >= 14));
  if (SMALL) return;
  for (let i = rows.length - 1; i > 0 && log.scrollHeight > log.clientHeight + 1; i--) rows[i].classList.add("cut");
}
try { new ResizeObserver(() => fitOrders()).observe(document.querySelector(".log")); } catch { /* alter Browser: nur beim Zeichnen */ }

function render(s) {
  // Handheld mit Rolle «Nur Statusanzeige»: wurde die Rolle geändert, zurück in die Bestell-App
  try {
    if (s.status_only === false && sessionStorage.getItem("festpos.statusOnly")) {
      sessionStorage.removeItem("festpos.statusOnly");
      location.replace("/");
      return;
    }
  } catch { /* ohne sessionStorage: bleibt hier */ }
  if (typeof festposNotice === "function") festposNotice(s.notice || null, { overlay: true });   // T27
  const problems = [], waits = [];
  $("event").textContent = s.event_name || "";
  renderBrand(s);
  // Links zur Bestell-App (nur Handy mit Zertifikat) und zur Verwaltung – am Bildschirm des Pi nie
  const nav = s.nav || {};
  $("jump").hidden = !(nav.app || nav.admin);
  $("j-app").hidden = !nav.app;
  $("j-admin").hidden = !nav.admin;
  if (!$("j-app").dataset.bound) {                    // Wechsel in die Bestell-App = in der Verwaltung abmelden
    $("j-app").dataset.bound = "1";
    $("j-app").addEventListener("click", () => {
      try { sessionStorage.removeItem("festpos.token"); sessionStorage.removeItem("festpos.me"); } catch { /* egal */ }
    });
  }

  // Bestellungen mit Druckstatus pro Station
  const tw = t(s.table_word === "Platz" ? "status.word.seat" : "status.word.table");
  const downNames = new Set((s.printers || []).filter((p) => p.enabled && (p.down || p.online === false)).map((p) => p.name));
  $("orders").innerHTML = (s.recent || []).length ? s.recent.map((o) => {
    const bad = (o.print || []).some((p) => p.state === "problem");
    const lamps = (o.print || []).map((p) => {
      const stuck = p.state === "wait" && downNames.has(p.station);
      const cls = p.state === "problem" || stuck ? "bad" : p.state;
      const word = stuck ? t("status.lamp.waits") : p.state === "wait" ? t("status.lamp.prints") : p.state === "problem" ? t("status.lamp.check") : "";
      return `<span class="lamp ${cls}"><span class="lbl">${esc(p.station)}${word ? " " + esc(word) : ""}</span></span>`;
    }).join("");
    return `<div class="order${bad ? " problem" : ""}${o.test ? " test" : ""}" data-order="${o.id}" role="button" tabindex="0">
      <div class="time">${hhmm(o.created_at)}</div>
      <div class="what"><div class="where">${o.test ? `<span class="testtag">${esc(t("status.order.test_tag"))}</span> ` : ""}${o.stornos ? `<span class="stornotag">${esc(t("status.order.storno_tag"))}</span> ` : ""}${esc(tw)} ${esc(o.table_label)}<small>${esc(o.device || "")}</small></div>
        <div class="lamps">${lamps || esc(o.test ? t("status.order.test_no_bon") : t("status.order.no_bon"))}</div></div>
      <div class="amt"><b>${money(o.total_cents)}</b><small>${esc(t("status.order.meta", { count: o.items, no: String(o.id).padStart(4, "0") }))}</small></div>
    </div>`;
  }).join("") : `<div class="empty">${esc(t("status.order.none"))}</div>`;

  fitOrders();

  // Handys
  $("devices").innerHTML = (s.devices || []).length ? s.devices.map((d, idx) => {
    let cls = d.online ? "ok" : "", note = d.online ? t("status.dev.online") : since(d.seconds);
    if (d.configured && !d.online) note = d.ping == null ? t("status.dev.checking") : t("status.dev.unreachable");
    if (!d.online && d.ping === true) { cls = "idle"; note = t("status.dev.app_closed"); }
    else if (!d.online && d.ping === false && d.ip && !d.configured) note = t("status.dev.no_answer");
    // Fully meldet Kiosk-Modus aus oder entsperrt: orange (nur mit frischer Antwort von Fully)
    const fd = d.fully?.state === "ok" ? d.fully.data : null;
    if (fd && (fd.kiosk === false || fd.kiosk_locked === false)) {
      cls = "kiosk"; note = t("status.dev.kiosk_off"); waits.push(t("status.dev.kiosk_off_warn", { name: d.name }));
    } else if (fd && fd.licensed === false) {      // Fully ohne Lizenz (T5): orange, bis die Einzellizenz zugewiesen ist
      cls = "kiosk"; note = t("status.dev.unlicensed"); waits.push(t("status.dev.unlicensed_warn", { name: d.name }));
    }
    if (d.outbox > 0) { cls = "wait"; note = t("status.dev.outbox", { count: d.outbox }); waits.push(t("status.dev.resending", { name: d.name })); }
    if (d.test && d.online) { cls = "wait"; note = t(d.test_ending ? "status.dev.test_ending" : "status.dev.test"); waits.push(t("status.dev.test_warn", { name: d.name })); }
    // zwei Geräte mit demselben Zertifikat (T4): rot, entscheiden in der Verwaltung › Geräte
    if (d.mac_alarm) { cls = "bad"; note = t("status.dev.mac_alarm"); problems.push(t("status.dev.mac_alarm_warn", { name: d.name })); }
    const ip = d.ip ? `<span class="ip">${esc(d.ip)}</span>` : "";
    // Akku: von der App (alle Handys) oder genauer von Fully Kiosk; wenig Akku ohne Ladekabel = Warnung
    let bat = "";
    if (d.battery != null) {
      const low = !d.charging && d.battery <= 10 ? "bad" : !d.charging && d.battery <= 20 ? "wait" : "";
      if (low === "bad") problems.push(t("status.dev.battery_low", { name: d.name, pct: d.battery }));
      else if (low) waits.push(t("status.dev.battery_low", { name: d.name, pct: d.battery }));
      bat = `<span class="bat ${low}" style="--lvl:${Math.max(0, Math.min(100, +d.battery))}%" title="${esc(t(d.charging ? "status.dev.battery_charging" : "status.dev.battery", { pct: d.battery }))}">${esc(d.battery)}&nbsp;%${d.charging ? "<i>⚡</i>" : ""}</span>`;
    }
    if (d.fully?.temp >= 45) waits.push(t("status.dev.hot", { name: d.name, temp: d.fully.temp }));
    // eingetragenes Handy: dazu der Name aus der App (so steht es auf den Bestellungen), wenn er anders lautet
    const who = d.app_name && d.app_name !== d.name ? `<small class="who">${esc(d.app_name)}</small>` : "";
    const tag = d.test && d.online ? `<span class="testtag">${esc(t("status.order.test_tag"))}</span>` : "";
    return `<div class="item tap" data-dev="${idx}" role="button" tabindex="0"><span class="lamp ${cls}">${esc(d.name)}${who}</span><span class="note ${cls === "wait" || cls === "kiosk" || cls === "bad" ? cls : ""}">${tag}${ip}${bat}<span class="${d.online && !d.outbox ? "on" : ""}">${esc(note)}</span></span></div>`;
  }).join("") : `<div class="item"><span class="note">${esc(t("status.dev.none"))}</span></div>`;
  $("testall").hidden = !testExitOk() || testing().length < 2;

  // viele Handys: kompakt nur mit Lämpchen, damit nichts abgeschnitten wird
  const dv = $("devices"), dc = dv.parentElement;
  dv.className = "list";
  // Name abgeschnitten (z.B. mit Akkuanzeige): zuerst die IP weglassen, dann «online» (das Lämpchen zeigt es)
  const cut = () => [...dv.querySelectorAll(".lamp")].some((l) => l.scrollWidth > l.clientWidth + 1);
  if (cut()) dv.classList.add("noip");
  if (cut()) dv.classList.add("nostate");
  if (cut()) dv.classList.add("nowho");             // erst ganz zuletzt: der Name von der Bestellung
  if (!SMALL && dc.scrollHeight > dc.clientHeight + 1) {
    dv.className = "chips";
    const chips = [...dv.children].sort((a, b) => (a.querySelector(".lamp.ok,.lamp.wait") ? 1 : 0) - (b.querySelector(".lamp.ok,.lamp.wait") ? 1 : 0));
    let hidden = 0;                                  // passt immer noch nicht: zuerst Offline-Handys zusammenfassen
    while (dc.scrollHeight > dc.clientHeight + 1 && chips.length > 1) { chips.shift().remove(); hidden++; }
    if (hidden) {
      dv.insertAdjacentHTML("beforeend", `<span class="note">${esc(t("status.dev.more", { count: hidden }))}</span>`);
      if (dc.scrollHeight > dc.clientHeight + 1 && chips.length) { chips.shift().remove(); dv.lastElementChild.textContent = t("status.dev.more", { count: hidden + 1 }); }
    }
  }
  // Drucker: grosse Kacheln
  $("printers").innerHTML = (s.printers || []).map((p) => {
    let cls = "", st = t("status.prn.ready"), sub = p.pending ? t("status.prn.in_work", { count: p.pending }) : t("status.prn.idle");
    if (!p.enabled) { cls = "off"; st = t("status.prn.off"); sub = t("status.prn.switched_off"); }
    else if (p.down || p.online === false) {
      cls = "bad";
      const code = p.reason_code || "unreachable", wait = p.pending ? t("status.prn.waiting", { count: p.pending }) : "";
      if (code === "paper_out") { st = t("status.prn.paper_out"); sub = t("status.prn.refill") + wait; problems.push(t("status.prn.p_paper", { name: p.name })); }
      else if (code === "cover_open") { st = t("status.prn.cover_open"); sub = t("status.prn.close_cover") + wait; problems.push(t("status.prn.p_cover", { name: p.name })); }
      else if (code === "no_status") { st = t("status.prn.no_status"); sub = t("status.prn.check_paper_cover") + wait; problems.push(t("status.prn.p_no_status", { name: p.name })); }
      else if (code === "printer_error") { st = t("status.prn.error"); sub = t("status.prn.restart") + wait; problems.push(t("status.prn.p_error", { name: p.name })); }
      else { st = t("status.prn.down"); sub = t("status.prn.unreachable") + wait; problems.push(t("status.prn.p_down", { name: p.name })); }
    }
    else if (p.paper_out) { cls = "bad"; st = t("status.prn.paper_out"); problems.push(t("status.prn.p_paper", { name: p.name })); }
    else if (p.cover_open) { cls = "bad"; st = t("status.prn.cover_open"); problems.push(t("status.prn.p_cover", { name: p.name })); }
    else if (p.no_status) { cls = "bad"; st = t("status.prn.no_status"); sub = t("status.prn.check_paper_cover"); problems.push(t("status.prn.p_no_status", { name: p.name })); }
    else if (p.error) { cls = "bad"; st = t("status.prn.error"); sub = t("status.prn.check_printer"); problems.push(t("status.prn.p_error", { name: p.name })); }
    else if (p.problems) { cls = "wait"; st = t("status.prn.check"); sub = t("status.prn.not_printed", { count: p.problems }); waits.push(t("status.prn.w_check", { name: p.name })); }
    else if (p.paper_near_end) { cls = "wait"; st = t("status.prn.paper_low"); waits.push(t("status.prn.w_paper_low", { name: p.name })); }
    return `<div class="printer ${cls}"><span class="dot"></span>
      <div><div class="nm">${esc(p.name)}</div><div class="sub">${esc(sub)}</div></div>
      <div class="st">${esc(st)}</div></div>`;
  }).join("") || `<div class="card empty">${esc(t("status.prn.none"))}</div>`;

  // Mobilfunk-Router: Empfang in Balken (antippen: Werte und Verlauf der letzten 30 Minuten)
  const up = s.uplink, ue = $("uplink");
  ue.hidden = !up;
  if (up) {
    const v = uplinkLook(up), name = up.name || t("status.uplink.name");
    // ohne Verbindung nur dann rot, wenn FestPOS den Router braucht (Karte/TWINT, QR-Bestellung, Internet darüber)
    if (v.problem && s.uplink_needed === false) { v.cls = "wait"; v.warn = v.problem; v.problem = ""; }
    if (v.problem) problems.push(v.problem); else if (v.warn) waits.push(v.warn);
    ue.className = `uplink tap ${v.cls}`;
    ue.innerHTML = `${sigBars(v.bars)}<span class="nm">${esc(name)}</span><span class="val">${esc(v.main)}</span>${v.sub ? `<span class="sub">${esc(v.sub)}</span>` : ""}`;
    ue.setAttribute("aria-label", `${name}: ${v.main}`);
  }

  // Internet: nur mit «Dauerhaft verbunden» (Verwaltung › Internet, Schalter c)
  const net = s.internet, ie = $("inet");
  ie.hidden = !net;
  if (net) {
    const routerBad = !!up && ue.classList.contains("bad");
    const v = inetLook(net, routerBad, (s.pis || []).length > 1), name = t("status.inet.name");
    if (v.problem) problems.push(v.problem); else if (v.warn) waits.push(v.warn);
    ie.className = `uplink inet tap ${v.cls}`;
    ie.innerHTML = `<span class="lamp ${v.cls}"></span><span class="nm">${esc(name)}</span><span class="val">${esc(v.main)}</span>${v.sub ? `<span class="sub">${esc(v.sub)}</span>` : ""}`;
    ie.setAttribute("aria-label", `${name}: ${v.main}`);
  }

  // Netzwerk: nur Geräte im Einsatz; ist nichts im Einsatz (auch kein Router), fällt die Karte weg
  const netCount = s.network_count ?? (s.network || []).length;
  $("network").hidden = !netCount;
  $("network").innerHTML = (s.network || []).map((h) => {
    if (!h.ok) problems.push(t("status.net.unreachable", { name: h.name }));
    return `<span class="lamp ${h.ok ? "ok" : "bad"}">${esc(h.name)}</span>`;
  }).join("") || `<span class="lamp">${esc(t("status.net.checking"))}</span>`;
  document.querySelector(".card.net").hidden = !netCount && !up && !net;

  // Server: eine Zeile pro Pi (druckt, Temperatur, Speicher) – Probleme mit dem Namen des Pi (2026.10.10-27).
  // Jeder Pi meldet seine Dienste und Werte selbst (system.publish); für diesen Pi gelten die frischen Werte.
  const many = (s.pis || []).length > 1;
  const svcName = (n) => (SVC_NAMES[n] ? t(SVC_NAMES[n]) : n);
  $("cluster").innerHTML = (s.pis || []).map((p) => {
    const i = p.info || {}, label = piLabel(p.id);
    const svc = p.here && s.services?.length ? Object.fromEntries(s.services.map((x) => [x.name, x.ok])) : (i.svc || {});
    const temp = p.here && s.system?.temp != null ? s.system.temp : i.temp;
    const free = p.here && s.system?.disk_free != null ? s.system.disk_free
      : i.disk_total ? Math.round((i.disk_free / i.disk_total) * 100) : null;
    const down = Object.entries(svc).filter(([, ok]) => !ok).map(([n]) => svcName(n));
    let cls = "ok", text;
    if (!p.reachable) {
      cls = "bad"; text = t("status.srv.unreachable");
      problems.push(t("status.cluster.p_unreachable", { name: label }));
    } else if (down.length) {
      cls = "bad"; text = t("status.srv.svc_down", { list: down.join(", "), count: down.length });
      problems.push(t("status.srv.p_svc", { pi: label, list: down.join(", "), count: down.length }));
    } else {
      if (temp >= 80) { cls = "bad"; problems.push(t("status.srv.p_hot", { pi: label, temp })); }
      else if (temp >= 70) { cls = "wait"; waits.push(t("status.srv.w_warm", { pi: label, temp })); }
      if (free != null && free < 5) { cls = "bad"; problems.push(t("status.srv.p_disk", { pi: label })); }
      else if (free != null && free < 15) { if (cls === "ok") cls = "wait"; waits.push(t("status.srv.w_disk", { pi: label })); }
      // kurz halten: Temperatur und Speicher nur, wenn warm bzw. knapp – die Werte stehen in den Details
      const bak = backupAge(i.backup);
      text = [many && p.leader ? t("status.cluster.prints") : "", temp != null && temp >= 70 ? `${temp} °C` : "",
        free != null && free < 15 ? t("status.sys.disk_free", { pct: free }) : "",
        bak != null && bak < 24 * 60 ? backupText(bak) : ""].filter(Boolean).map(nobr).join(" · ");   // nur bei « · » umbrechen
    }
    return `<div class="item tap" data-pi="${esc(p.id)}" role="button" tabindex="0"><span class="lamp ${cls}">${esc(label)}${p.here && many ? " " + esc(t("status.cluster.here")) : ""}</span><span class="note ${cls === "ok" ? "" : cls}">${esc(text)}</span></div>`;
  }).join("");
  const sum = $("cluster-sum");
  sum.hidden = !s.cluster;
  if (s.cluster) {                                  // mehrere Pi: Mehrheit und druckender Pi
    const up = s.cluster.filter((n) => n.reachable).length, total = s.cluster.length;
    const leader = s.cluster.find((n) => n.leader);
    if (!leader) problems.push(t("status.cluster.p_no_leader"));
    const quorum = up > total / 2;
    if (!quorum) problems.push(t("status.cluster.p_no_quorum"));
    sum.className = "cluster-sum" + (quorum && leader ? "" : " bad");
    sum.textContent = !quorum ? t("status.cluster.no_quorum", { up, total })
      : !leader ? t("status.cluster.electing")
      : up < total ? t("status.cluster.degraded", { up, total }) : t("status.cluster.all", { total });
  }

  // Update (update.py): läuft, wartet, angehalten – und der USB-Stick der letzten Minuten
  const upd = s.update;
  if (upd?.plan) {
    const p = upd.plan, pi = piLabel(p.node);
    if (p.phase === "blocked") problems.push(t("status.update.failed", { version: p.version, pi, back: p.back || "–" }));
    else if (p.phase === "wait" && p.reason === "unreachable") waits.push(t("status.update.waiting", { version: p.version, pi }));
    else if (p.phase === "wait") {
      waits.push(t(p.kind === "rollback" ? "status.update.back_running" : "status.update.running",
        { version: p.version, pi, done: p.done, total: p.total }));
    }
  }
  if (upd?.usb && USB_TXT[upd.usb.state]) {
    const u = upd.usb;
    (USB_BAD.has(u.state) ? problems : waits).push(t(USB_TXT[u.state], { version: u.version || "", reason: u.text || "" }));
  }

  if (s.security) problems.push(s.security);
  // Ausverkauft und Restmenge an der Warnschwelle (Verwaltung › Preisliste › Bestand)
  for (const p of s.stock || []) waits.push(p.soldout ? t("status.stock.out", { name: p.name }) : t("status.stock.low", { name: p.name, count: p.stock }));
  for (const w of s.warnings || []) if (STATUS_WARN[w]) (STATUS_WARN_BAD.has(w) ? problems : waits).push(t(STATUS_WARN[w]));
  if (s.einzelbetrieb) problems.push(t("status.cluster.einzelbetrieb"));

  // Gesamtlampe
  $("pill").className = "pill" + (problems.length ? " bad" : waits.length ? " wait" : "");
  $("state").textContent = problems.length ? t("status.head.problems", { count: problems.length })
    : waits.length ? t("status.head.check") : t("status.head.all_ok");
  $("detail").textContent = (problems.length ? problems : waits).join(", ") ||
    (s.totals?.last_at ? t("status.head.last_order", { time: hhmm(s.totals.last_at) }) : t("status.head.ready_first"));

  const tot = s.totals || {};
  $("k-orders").textContent = tot.count || 0;
  $("k-hour").textContent = tot.last_hour || 0;
  $("k-total").textContent = money(tot.total_cents || 0);
  $("k-cur").textContent = s.currency || "CHF";
  $("ver").textContent = t(s.einzelbetrieb ? "status.foot.ver_emergency" : s.cluster ? "status.foot.ver_cluster" : "status.foot.ver_single",
    { version: s.version, count: s.cluster?.length || 1 });
}

const nobr = (s) => String(s).replace(/ /g, "\u00a0");
const backupAge = (b) => (b?.last_ok ? Math.max(0, Math.round((Date.now() / 1000 - b.last_ok) / 60)) : null);
const backupText = (min) => (min < 60 ? t("status.srv.backup", { min }) : t("status.srv.backup_h", { h: Math.floor(min / 60) }));

// Internet (dauerhaft verbunden): Weg, erreichbar, Tunnel. Rot, wenn Karte/TWINT oder QR-Bestellung es
// brauchen – über den Router aber nicht doppelt: ist der Mobilfunk-Router schon rot, bleibt diese Zeile gelb.
const INET_VIA = { lan: "status.inet.via_lan", wlan: "status.inet.via_wlan", usb: "status.inet.via_usb" };
// Grund, wenn die Verbindung fehlt (update_net.py): Prüfung DNS + HTTPS bzw. Fehler beim Verbinden
const INET_REASON = { dns: "status.inet.r_dns", time: "status.inet.r_time", https: "status.inet.r_https",
  wlan_not_found: "status.inet.r_wlan_not_found", wlan: "status.inet.r_wlan", no_wlan: "status.inet.r_no_wlan",
  dhcp: "status.inet.r_dhcp", no_link: "status.inet.r_no_link", no_usb: "status.inet.r_no_usb",
  gateway: "status.inet.r_gateway", helper: "status.inet.r_helper", same_net: "status.inet.r_same_net",
  connect: "status.inet.r_connect" };
const inetReason = (r) => (r?.reason ? t(INET_REASON[r.reason] || INET_REASON.connect, { ssid: r.ssid || "" }) : "");
function inetLook(net, routerBad, many) {
  const rows = net.pis || [], online = rows.some((r) => r.state === "up" && r.online);
  const checked = rows.some((r) => ["up", "failed", "no_way"].includes(r.state) && (r.state !== "up" || r.online != null));
  const dev = net.via !== "lan" && rows.find((r) => r.device);
  const way = t(INET_VIA[net.via] || INET_VIA.lan) + (dev && many ? ` (${piLabel(dev.id)})` : "");
  const tun = rows.map((r) => r.tunnel).filter((x) => x != null);
  const tunnel = !tun.length ? null : tun.some(Boolean);
  const sub = [way, tunnel == null ? "" : t(tunnel ? "status.inet.tunnel_on" : "status.inet.tunnel_off")].filter(Boolean).join(" · ");
  if (!checked) return { cls: "", main: t("status.net.checking"), sub };
  const look = { cls: "ok", main: t("status.inet.online"), sub };
  const quiet = net.via === "lan" && routerBad;                // der Router meldet es schon
  if (!online) {
    look.main = t("status.inet.offline");
    const why = inetReason(rows.find((r) => r.reason));                // z.B. «WLAN nicht gefunden», «kein DNS»
    if (why) look.sub = [way, why].join(" · ");
    look.cls = net.needed && !quiet ? "bad" : "wait";
    if (!quiet) look[net.needed ? "problem" : "warn"] = t("status.inet.p_down");
  } else if (net.qr && tunnel === false) {
    look.cls = "bad"; look.problem = t("status.inet.p_tunnel");
  }
  return look;
}

const SVC_NAMES = { rqlited: "status.svc.rqlited", "festpos-redirect": "status.svc.redirect", chrony: "status.svc.chrony",
  nftables: "status.svc.nftables", keepalived: "status.svc.keepalived" };
const USB_TXT = { reading: "status.update.usb_reading", handed: "status.update.usb_handed", old: "status.update.usb_old",
  none: "status.update.usb_none", invalid: "status.update.usb_invalid", unreadable: "status.update.usb_unreadable",
  installing: "status.update.usb_installing", installed: "status.update.usb_installed", failed: "status.update.usb_failed" };
const USB_BAD = new Set(["invalid", "unreadable", "failed"]);
// Hinweise des Servers (warnings): Code -> Text. Für alle sichtbar, darum ohne Einzelheiten (z.B. wie kurz ein Passwort ist)
const STATUS_WARN = { admin_pw_placeholder: "status.warn.admin_pw", admin_pw_short: "status.warn.admin_pw",
  admin_pw_short_locked: "status.warn.admin_pw",
  backup_none: "status.warn.backup_none", backup_old: "status.warn.backup_old", backup_failed: "status.warn.backup_failed" };
const STATUS_WARN_BAD = new Set(["admin_pw_placeholder", "backup_failed"]);
let pollTimer = null, polling = false, LAST = null;
const piLabel = (id) => t("status.cluster.pi", { id: String(/^pi/i.test(id) ? id.replace(/^pi[-_ ]?/i, "") : id).toUpperCase() });
const authHeaders = () => {
  const h = { "X-Festpos-UI": "1", "X-Festpos-Lang": I18n.lang };
  try { const tok = sessionStorage.getItem("festpos.token"); if (tok) h.Authorization = "Bearer " + tok; } catch { /* egal */ }
  return h;
};

// Testmodus beenden (T7): ab Festleitung, am Bildschirm des Pi auch ohne Anmeldung (beenden ist die sichere Richtung)
const testExitOk = () => ["testmode", "devices"].some((p) => (LAST?.me?.perms || []).includes(p)) || LAST?.viewer === "kiosk";
const testing = () => (LAST?.devices || []).filter((d) => d.test && d.online);

// ---------- Gerät antippen: Details (Temperatur, Android-/OS-Version …), ab Festleitung Neustart-Knöpfe ----------
const FSTATE = { ok: "status.detail.fs_ok", no_https: "status.detail.fs_no_https", cert: "status.detail.fs_cert",
  auth: "status.detail.fs_auth", unreachable: "status.detail.fs_unreachable", timeout: "status.detail.fs_timeout", bad: "status.detail.fs_bad" };
const CMDSTATE = { ok: "status.detail.cmd_ok", sent: "status.detail.cmd_sent", refused: "status.detail.cmd_refused",
  no_rights: "status.detail.cmd_no_rights" };
const Detail = (() => {
  let open = null, confirmAct = null, closeTimer = null, busy = false, note = null, listY = 0;
  const el = () => $("detail-sheet");
  // Knöpfe je nach Recht (app/auth.py): Handhelds/Fully «devices», Pi «system», Testmodus «testmode»
  const can = (p = "devices") => (LAST?.me?.perms || []).includes(p);
  const row = (k, v) => (v == null || v === "" ? "" : `<dt>${esc(t(k))}</dt><dd>${esc(v)}</dd>`);
  const bytes = (b) => (b == null ? null : b >= 1e9 ? `${(b / 1e9).toLocaleString(I18n.locale, { maximumFractionDigits: 1 })} GB`
    : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.round(b / 1e3)} kB`);
  const of = (a, b) => (a == null ? null : b ? t("status.detail.of", { a: bytes(a), b: bytes(b) }) : bytes(a));
  const ago = (sec) => (sec == null ? null : since(sec));
  const uptime = (boot) => {
    if (!boot) return null;
    const m = Math.max(0, Math.round((Date.now() + skew) / 60000 - boot / 60));
    return m < 60 ? t("status.detail.minutes", { count: m }) : m < 2880 ? t("status.detail.hours", { count: Math.floor(m / 60) })
      : t("status.detail.days", { count: Math.floor(m / 1440) });
  };
  function phone(d) {
    const f = d.fully, x = f?.data || {};
    const bat = d.battery == null ? null : t(d.charging ? "status.detail.battery_charging" : "status.detail.battery_value", { pct: d.battery }) +
      (d.battery_src === "fully" ? " · Fully" : "");
    const state = d.online ? t("status.dev.online") : d.seconds != null ? since(d.seconds) : t("status.dev.offline");
    const body = `<dl>${row("status.detail.state", state)}${row("status.detail.app_name", d.app_name)}${row("status.detail.ip", d.ip)}
      ${row("status.detail.battery", bat)}
      ${d.test && d.online ? row("status.detail.test", t(d.test_ending ? "status.dev.test_ending" : "status.detail.test_on")) : ""}
      ${f ? `${row("status.detail.temp", x.temp != null ? `${x.temp} °C` : null)}
        ${row("status.detail.screen", x.screen_on == null ? null : t(x.screen_on ? "status.detail.on" : "status.detail.off"))}
        ${row("status.detail.kiosk", x.kiosk == null ? null
          : t(!x.kiosk ? "status.detail.off" : x.kiosk_locked === false ? "status.detail.kiosk_unlocked" : "status.detail.on"))}
        ${row("status.detail.wifi", x.wifi != null ? `${x.wifi}${x.ssid ? ` (${x.ssid})` : ""}` : null)}
        ${row("status.detail.storage", of(x.storage_free, x.storage_total))}${row("status.detail.ram", bytes(x.ram_free))}
        ${row("status.detail.licence", x.licensed == null ? null : t(x.licensed ? "status.detail.licensed" : "status.detail.unlicensed"))}
        ${row("status.detail.android", x.android)}${row("status.detail.fully", x.fully_version)}${row("status.detail.model", x.model)}
        ${row("status.detail.page", x.page)}${row("status.detail.fully_state", t(FSTATE[f.state] || FSTATE.bad))}
        ${row("status.detail.as_of", ago(f.data_seconds))}` : ""}</dl>
      ${f ? "" : `<p class="hint">${esc(t("status.detail.no_fully"))}</p>`}`;
    // Kiosk: nur den passenden Knopf; ist der Zustand unbekannt, beide (Fully meldet «kioskLocked» nicht immer)
    const kiosk = [x.kiosk_locked !== false && ["fully", "unlockKiosk", "status.detail.kiosk_unlock"],
      x.kiosk_locked !== true && ["fully", "lockKiosk", "status.detail.kiosk_lock"]].filter(Boolean);
    const acts = can() && f ? [["fully", "loadStartURL", "status.detail.reload_page"], ["fully", "restartApp", "status.detail.restart_app"], ["fully", "rebootDevice", "status.detail.reboot_phone"],
      ["fully", "shutdownDevice", "status.detail.shutdown_phone"], ...kiosk] : [];
    if (d.test && d.online && !d.test_ending && testExitOk()) acts.unshift(["test", d.exit, "status.detail.test_exit", d.name]);
    return { title: d.name, body, acts, key: d.ip };
  }
  function allTest() {                                // «Alle aus dem Testmodus»
    const list = testing();
    if (!list.length) return null;
    const body = `<p class="hint">${esc(list.map((d) => d.name + (d.app_name && d.app_name !== d.name ? ` (${d.app_name})` : "")).join(", "))}</p>`;
    return { title: t("status.dev.test_all_btn"), body, acts: testExitOk() && list.some((d) => !d.test_ending) ? [["test", "*", "status.detail.test_exit_all", null]] : [], key: "*" };
  }
  function pi(n) {
    const i = n.info || {};
    const body = `<dl>${row("status.detail.reachable", t(n.reachable ? "status.detail.yes" : "status.detail.no"))}
      ${row("status.detail.role", n.leader ? t("status.cluster.prints") : t("status.detail.standby"))}
      ${row("status.detail.model", i.model)}${row("status.detail.os", i.os)}${row("status.detail.kernel", i.kernel)}
      ${row("status.detail.uptime", uptime(i.boot))}${row("status.detail.temp", i.temp != null ? `${i.temp} °C` : null)}
      ${row("status.detail.storage", of(i.disk_free, i.disk_total))}${row("status.detail.ram", of(i.ram_free, i.ram_total))}
      ${row("status.detail.backup", i.backup?.last_ok ? backupText(backupAge(i.backup)) : i.backup ? t("status.detail.backup_none") : null)}
      ${row("status.inet.name", i.net ? inetPi(i.net) : null)}
      ${row("status.detail.version", i.version)}${row("status.detail.as_of", ago(n.info_age))}</dl>`;
    const acts = can("system") ? [["pi", "reboot", "status.detail.reboot_pi"], ["pi", "poweroff", "status.detail.poweroff_pi"]] : [];
    return { title: piLabel(n.id) + (n.here ? " " + t("status.cluster.here") : ""), body, acts, key: n.id };
  }
  const dur = (sec) => (sec < 3600 ? t("status.detail.minutes", { count: Math.round(sec / 60) })
    : sec < 172800 ? t("status.detail.hours", { count: Math.floor(sec / 3600) }) : t("status.detail.days", { count: Math.floor(sec / 86400) }));
  // Verlauf RSRP (5 bis 30 Minuten, je nachdem wie lange schon gemessen wird); Lücken (keine Antwort) bleiben offen.
  // Gelb/rot hinterlegt wie die Balken; der Punkt rechts ist die neueste Messung.
  function chart(up) {
    const all = (up.hist || []).filter((p) => p[0] <= 1800).sort((a, b) => b[0] - a[0]);
    const pts = all.filter((p) => p[1] != null);
    if (pts.length < 2) return up.rating?.level === "bad" ? "" : `<p class="hint">${esc(t("status.uplink.chart_wait"))}</p>`;
    const span = Math.min(1800, Math.max(300, Math.ceil(all[0][0] / 300) * 300));
    const W = 300, H = 100, lo = -125, hi = -65;
    const x = (age) => (W - (age / span) * W).toFixed(1), y = (v) => (H - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * H).toFixed(1);
    let path = "", pen = false;
    for (const [age, v] of all) {
      if (v == null) { pen = false; continue; }
      path += `${pen ? "L" : "M"}${x(age)} ${y(v)}`;
      pen = true;
    }
    // Punkt nur, wenn die neueste Abfrage einen Wert hat (sonst wäre er von früher und täuscht)
    const grid = [-80, -90, -100, -110], newest = all[all.length - 1], last = newest[1] != null ? newest : null;
    return `<figure class="upchart"><div class="plot">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(t("status.uplink.chart"))}">
          <rect class="zw" x="0" width="${W}" y="${y(-100)}" height="${(y(-110) - y(-100)).toFixed(1)}"/>
          <rect class="zb" x="0" width="${W}" y="${y(-110)}" height="${(H - y(-110)).toFixed(1)}"/>
          ${grid.map((v) => `<line class="g" x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" vector-effect="non-scaling-stroke"/>`).join("")}
          <path class="l" d="${path}" vector-effect="non-scaling-stroke"/></svg>
        ${grid.map((v) => `<span class="yl" style="top:${(y(v) / H * 100).toFixed(1)}%">${dbm(v, "")}</span>`).join("")}
        ${last ? `<span class="now ${last[1] < -110 ? "bad" : last[1] < -100 ? "wait" : ""}" style="left:${(x(last[0]) / W * 100).toFixed(1)}%;top:${(y(last[1]) / H * 100).toFixed(1)}%"></span>` : ""}</div>
      <figcaption><span>${esc(t("status.uplink.chart_from", { min: span / 60 }))}</span><span>${esc(t("status.uplink.chart"))}</span><span>${esc(t("status.uplink.chart_now"))}</span></figcaption></figure>`;
  }
  function uplink(up) {
    const d = up.data || {}, g = up.rating || {}, look = uplinkLook(up);
    // oben das Wichtigste und der Verlauf (Antenne ausrichten), darunter die übrigen Angaben in zwei Spalten
    const body = `<dl>${row("status.detail.state", up.state === "ok" && g.level !== "bad" ? t("common.uplink.st_ok") : look.main)}
      ${row("status.uplink.d_signal", up.state !== "ok" || !g.word || g.word === "none" ? null
        : t("common.uplink.bars", { word: t(UP_WORD[g.word]), bars: g.bars }))}
      ${row("status.uplink.d_rsrp", d.rsrp != null ? dbm(d.rsrp) : null)}${row("status.uplink.d_sinr", d.sinr != null ? dbm(d.sinr, "dB") : null)}</dl>
      ${chart(up)}
      <dl class="more">${row("status.uplink.d_rsrq", d.rsrq != null ? dbm(d.rsrq, "dB") : null)}${row("status.uplink.d_rssi", d.rssi != null ? dbm(d.rssi) : null)}
      ${row("status.uplink.d_operator", d.operator ? d.operator + (d.roaming ? ` (${t("common.uplink.roaming")})` : "") : null)}
      ${row("status.uplink.d_network", upNet(d) + (d.bw ? ` · ${d.bw} MHz` : ""))}${row("status.uplink.d_cell", d.cell)}
      ${row("status.uplink.d_sim", d.sim)}${row("status.uplink.d_sim_status", d.sim_status)}
      ${row("status.uplink.d_link", d.link == null ? null : t(d.link ? "status.detail.yes" : "status.detail.no"))}
      ${row("status.uplink.d_data", d.rx_bytes != null ? `↓ ${bytes(d.rx_bytes)} · ↑ ${bytes(d.tx_bytes || 0)}` : null)}
      ${row("status.uplink.d_router", [d.model || up.model, d.version].filter(Boolean).join(" · "))}
      ${row("status.detail.uptime", d.uptime != null ? dur(d.uptime) : null)}${row("status.detail.ip", up.host)}
      ${row("status.detail.as_of", up.data_seconds != null ? t("status.uplink.ago", { sec: up.data_seconds }) : null)}</dl>
      <p class="hint">${esc(t("status.uplink.hint", { sec: up.interval || 10 }))}</p>`;
    return { title: up.name || t("status.uplink.name"), body, acts: [], key: "uplink" };
  }
  function inetPi(r) {
    if (r.state !== "up") {
      return [t(r.state === "failed" ? "status.inet.d_failed" : r.state === "no_way" ? "status.inet.d_no_way" : "status.inet.d_wait"),
        inetReason(r)].filter(Boolean).join(" – ");
    }
    return [r.since ? t("status.inet.d_since", { time: I18n.fmtTime(r.since * 1000) }) : "",
      r.peer ? t("status.inet.d_via_pi", { pi: piLabel(r.peer) }) : "", r.share ? t("status.inet.d_share") : "",
      r.online == null ? "" : t(r.online ? "status.inet.online" : "status.inet.offline"), r.online === false ? inetReason(r) : "",
      r.tunnel == null ? "" : t(r.tunnel ? "status.inet.tunnel_on" : "status.inet.tunnel_off")].filter(Boolean).join(" · ");
  }
  function inet(net) {
    const need = [net.pay ? t("status.inet.need_pay") : "", net.qr ? t("status.inet.need_qr") : ""].filter(Boolean).join(", ");
    const pis = (net.pis || []).map((r) => `<dt>${esc(piLabel(r.id))}</dt><dd>${esc(inetPi(r))}</dd>`).join("");
    const body = `<dl>${row("status.inet.d_way", t(INET_VIA[net.via] || INET_VIA.lan))}
      ${row("status.inet.d_needed", need || t("status.inet.need_none"))}${pis}</dl>
      <p class="hint">${esc(t("status.inet.hint"))}</p>`;
    return { title: t("status.inet.name"), body, acts: [], key: "inet" };
  }
  // T29: alle Bestellungen seit dem Tagesabschluss – Liste (seitenweise) und eine Bestellung
  const ORD = { list: [], more: false, count: 0, total: 0, state: "", one: null };
  const PAY = { cash: "status.order.pay_cash", card: "status.order.pay_card", twint: "status.order.pay_twint",
    other: "status.order.pay_other" };
  const tword = () => t(LAST?.table_word === "Platz" ? "status.word.seat" : "status.word.table");
  async function loadOrders(more) {
    ORD.state = "loading";
    if (!more) { ORD.list = []; ORD.more = false; }
    const before = more && ORD.list.length ? `&before=${ORD.list[ORD.list.length - 1].id}` : "";
    try {
      const r = await fetch(`/api/status/orders?limit=50${before}`, { headers: authHeaders() });
      if (!r.ok) throw new Error(r.status);
      const d = await r.json();
      ORD.list = more ? ORD.list.concat(d.orders) : d.orders;
      Object.assign(ORD, { more: d.more, count: d.count, total: d.total_cents, state: "" });
    } catch { ORD.state = "failed"; }
    if (open?.kind === "orders") keepScroll(draw);
  }
  async function loadOrder(id) {
    ORD.one = null;
    try {
      const r = await fetch(`/api/status/orders/${id}`, { headers: authHeaders() });
      ORD.one = r.ok ? await r.json() : { failed: true };
    } catch { ORD.one = { failed: true }; }
    if (open?.kind === "order" && open.id === id) draw();
  }
  function keepScroll(fn) {
    const p = el()?.querySelector(".panel"), y = p ? p.scrollTop : 0;
    fn();
    const q = el()?.querySelector(".panel");
    if (q) q.scrollTop = y;
  }
  const lampsOf = (o) => (o.print || []).map((p) => `<span class="lamp ${p.state}">${esc(p.station)}</span>`).join("")
    || `<span>${esc(t(o.test ? "status.order.test_no_bon" : o.no_print ? "status.order.nb_tag" : "status.order.no_bon"))}</span>`;
  function ordersView() {
    const rows = ORD.list.map((o) => `<button class="ol-row${(o.print || []).some((p) => p.state === "problem") ? " problem" : ""}${o.test ? " test" : ""}" data-act="order" data-id="${o.id}">
        <span class="t">${hhmm(o.created_at)}</span>
        <span class="w">${o.test ? `<span class="testtag">${esc(t("status.order.test_tag"))}</span> ` : ""}${o.stornos ? `<span class="stornotag">${esc(t("status.order.storno_tag"))}</span> ` : ""}${esc(tword())} ${esc(o.table_label)}<small>${esc(o.device || "")}</small></span>
        <span class="a">${money(o.total_cents)}</span>
        <span class="st">${lampsOf(o)}</span></button>`).join("");
    const body = `<p class="ol-sum">${esc(t("status.orders.sum", { count: ORD.count, total: money(ORD.total), cur: LAST?.currency || "CHF" }))}</p>
      ${ORD.state === "failed" ? `<p class="hint">${esc(t("status.orders.failed"))}</p>` : ""}
      ${!ORD.list.length && !ORD.state ? `<p class="hint">${esc(t("status.order.none"))}</p>` : ""}
      <div class="ol">${rows}</div>
      ${ORD.state === "loading" ? `<p class="hint">${esc(t("status.orders.loading"))}</p>`
        : ORD.more ? `<div class="acts"><button data-act="more">${esc(t("status.orders.more"))}</button></div>` : ""}`;
    return { title: t("status.orders.title"), body, acts: [], key: "orders" };
  }
  function orderView() {
    const o = ORD.one, back = `<button class="back" data-act="orders-back">${esc(t("status.orders.back"))}</button>`;
    if (!o) return { title: t("status.orders.title"), body: `${back}<p class="hint">${esc(t("status.orders.loading"))}</p>`, acts: [], key: "order" };
    if (o.failed) return { title: t("status.orders.title"), body: `${back}<p class="hint">${esc(t("status.orders.failed"))}</p>`, acts: [], key: "order" };
    const pay = o.payment ? t(PAY[o.payment] || PAY.other) : o.tab_id ? t("status.order.pay_tab") : t("status.order.pay_none");
    const lines = o.lines.map((l) => {
      const left = l.qty - (l.cancelled || 0);
      return `<div class="l${left <= 0 ? " gone" : ""}"><span>${left < l.qty && left > 0 ? `${left}/` : ""}${l.qty}× ${esc(l.name)}${l.options ? `<small>${esc(l.options)}</small>` : ""}${l.note ? `<small>${esc(l.note)}</small>` : ""}</span><span>${money(l.qty * l.price_cents)}</span></div>`;
    }).join("");
    const stornos = (o.cancellations || []).map((c) => `<div class="l"><span>${hhmm(c.created_at)} · ${esc(c.reason)}${c.who ? `<small>${esc(c.who)}</small>` : ""}</span><span>−${money(c.amount_cents)}</span></div>`).join("");
    const jobs = (o.jobs || []).map((j) => `<div class="l"><span><span class="lamp ${j.state}">${esc(j.station)}</span>${j.printer !== j.station ? `<small>${esc(t("status.order.via", { printer: j.printer }))}</small>` : ""}</span>
        <span>${esc(j.state === "ok" ? t("status.order.job_ok", { time: hhmm(j.printed_at || j.created_at) }) : t(j.state === "wait" ? "status.order.job_wait" : "status.order.job_problem"))}${j.reprint ? ` · ${esc(t("status.order.job_reprint"))}` : ""}</span></div>`).join("");
    const body = `${back}<dl>${row("status.order.d_time", hhmm(o.created_at))}${row("status.order.d_device", o.device)}
        ${row("status.order.d_payment", pay)}${row("status.order.d_tip", o.tip_cents ? money(o.tip_cents) : null)}
        ${row("status.order.d_total", money(o.total_cents))}</dl>
      ${o.test ? `<p class="hint">${esc(t("status.order.test_tag"))}</p>` : ""}${o.no_print ? `<p class="hint">${esc(t("status.order.nb_hint"))}</p>` : ""}
      <h3>${esc(t("status.order.d_items"))}</h3><div class="od-lines">${lines}</div>
      ${stornos ? `<h3>${esc(t("status.order.d_storno"))}</h3><div class="od-lines">${stornos}</div>` : ""}
      ${jobs ? `<h3>${esc(t("status.order.d_bons"))}</h3><div class="od-lines">${jobs}</div>` : ""}`;
    return { title: t("status.order.title", { word: tword(), table: o.table_label, no: String(o.id).padStart(4, "0") }), body, acts: [], key: "order" };
  }
  function view() {
    if (!open || !LAST) return null;
    if (open.kind === "orders") return ordersView();
    if (open.kind === "order") return orderView();
    if (open.kind === "inet") return LAST.internet ? inet(LAST.internet) : null;
    if (open.kind === "dev") { const d = (LAST.devices || []).find((x) => x.id === open.id); return d && phone(d); }
    if (open.kind === "up") return LAST.uplink ? uplink(LAST.uplink) : null;
    if (open.kind === "all") return allTest();
    const n = (LAST.pis || []).find((x) => x.id === open.id);
    return n && pi(n);
  }
  function draw(msg = "") {
    if (msg) note = { msg, until: Date.now() + 10000 };
    else if (note && note.until > Date.now() && !confirmAct) msg = note.msg;
    const v = view(), box = el();
    if (!v) return close();
    const act = confirmAct ? `<div class="acts"><span>${esc(t("status.detail.sure", { what: t(confirmAct[2]) }))}</span>
        <button data-act="yes" class="danger">${esc(t("status.detail.yes_do"))}</button><button data-act="no">${esc(t("status.detail.cancel"))}</button></div>`
      : v.acts.length ? `<div class="acts">${v.acts.map((a, i) => `<button data-act="go" data-i="${i}">${esc(t(a[2]))}</button>`).join("")}</div>` : "";
    box.innerHTML = `<div class="panel" role="dialog" aria-modal="true" aria-label="${esc(v.title)}">
      <div class="head"><b>${esc(v.title)}</b><button data-act="close" aria-label="${esc(t("status.detail.close"))}">✕</button></div>
      ${v.body}${act}${msg ? `<p class="msg">${esc(msg)}</p>` : ""}</div>`;
    box.hidden = false;
  }
  function show(kind, id) {
    open = { kind, id }; confirmAct = null; note = null; draw();
    if (kind === "dev") refresh(id);
    if (kind === "orders" && !ORD.list.length) loadOrders(false);
    if (kind === "order") loadOrder(id);
    // am Pi-Bildschirm nicht ewig offen; Router länger (beim Ausrichten der Antenne schaut man zu)
    clearTimeout(closeTimer); closeTimer = setTimeout(close, kind === "up" || kind === "orders" || kind === "order" ? 600000 : 90000);
  }
  // Handy mit Fully: beim Öffnen gleich neu abfragen (Kiosk-Modus, Lizenz, Akku sonst bis 30 Sekunden alt; ab Festleitung)
  async function refresh(id) {
    const d = (LAST?.devices || []).find((x) => x.id === id);
    if (!d?.fully || !d.ip || !can()) return;
    try {
      const r = await fetch("/api/admin/fully/check", { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ ip: d.ip }) });
      const f = r.ok ? await r.json() : null;
      if (!f?.state || open?.kind !== "dev" || open.id !== id) return;
      Object.assign(d.fully, { state: f.state, seconds: f.seconds ?? 0 }, f.data ? { data: f.data, data_seconds: f.data_seconds ?? 0 } : {});
      if (!confirmAct) draw();
    } catch { /* letzter Stand bleibt */ }
  }
  function close() { open = null; confirmAct = null; const box = el(); if (box) { box.hidden = true; box.innerHTML = ""; } }
  async function run(a) {
    const v = view();
    if (!v || busy) return;
    busy = true;
    let msg = "";
    try {
      const [kind, cmd] = a;
      const url = kind === "test" ? (can("testmode") || can("devices") ? "/api/admin/devices/test-exit" : "/api/status/test-exit")
        : kind === "fully" ? "/api/admin/fully/command" : "/api/admin/system/power";
      const body = kind === "test" ? (cmd === "*" ? { all: true } : { key: cmd, name: a[3] })
        : kind === "fully" ? { ip: v.key, cmd } : { action: cmd, node: v.key, phones: "none" };
      const r = await fetch(url, { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) msg = typeof d.detail === "string" ? d.detail : t("status.detail.failed", { status: r.status });
      else if (kind === "pi") msg = t(cmd === "reboot" ? "status.detail.pi_reboot_at" : "status.detail.pi_poweroff_at", { time: I18n.fmtTime(d.at * 1000) });
      else if (kind === "test") msg = t("status.detail.test_exit_sent");
      else msg = t(CMDSTATE[d.state] || FSTATE[d.state] || FSTATE.bad) + (d.text ? ` (${d.text})` : "");
    } catch { msg = t("status.detail.failed", { status: "–" }); }
    busy = false; confirmAct = null; draw(msg);
  }
  function tap(target) {
    const b = target.closest("#detail-sheet [data-act]");
    if (b) {
      const v = view(), act = b.dataset.act;
      if (act === "close") return close();
      if (act === "no") { confirmAct = null; return draw(); }
      if (act === "yes" && confirmAct) return run(confirmAct);
      if (act === "go" && v) { confirmAct = v.acts[+b.dataset.i]; return draw(); }
      if (act === "more") return loadOrders(true);
      if (act === "order") { listY = el()?.querySelector(".panel")?.scrollTop || 0; ORD.one = null; return show("order", +b.dataset.id); }
      if (act === "orders-back") { show("orders", "orders"); const p = el()?.querySelector(".panel"); if (p) p.scrollTop = listY; return; }
      return;
    }
    if (target.closest("#detail-sheet .panel")) return;
    if (target.closest("#detail-sheet")) return close();                 // daneben getippt
    const dev = target.closest("[data-dev]"), p = target.closest("[data-pi]"), up = target.closest("[data-up]");
    if (target.closest("[data-inet]") && LAST?.internet) return show("inet", "inet");
    const one = target.closest("[data-order]");                         // eine Bestellung: gleich ihre Details
    if (one) { ORD.list = []; ORD.one = null; listY = 0; return show("order", +one.dataset.order); }   // nie die vorige zeigen
    if (target.closest("[data-orders]")) { ORD.list = []; return show("orders", "orders"); }    // jedes Mal frisch
    if (target.closest("[data-testall]")) show("all", "*");
    else if (dev) { const d = LAST?.devices?.[+dev.dataset.dev]; if (d) show("dev", d.id); }        // Gerät, nie Name (T1)
    else if (p) show("pi", p.dataset.pi);
    else if (up && LAST?.uplink) show("up", "uplink");
  }
  // Liste und Bestellung nicht bei jeder Abfrage neu zeichnen (sonst springt sie beim Scrollen nach oben)
  return { tap, refresh: () => { if (open && !confirmAct && !busy && open.kind !== "orders" && open.kind !== "order") draw(); }, close, get open() { return !!open; } };
})();
// Antippen: kurze Berührung ohne Ziehen. Am kleinen Display mit Touch-Korrektur die korrigierte Stelle nehmen.
(() => {
  let down = null;
  addEventListener("pointerdown", (e) => { if (!calibrating) down = { x: e.clientX, y: e.clientY, t: Date.now() }; }, { passive: true });
  addEventListener("pointerup", (e) => {
    if (!down || calibrating) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y), long = Date.now() - down.t;
    down = null;
    if (moved > 12 || long > 800) return;
    const corrected = CAL.join() !== "1,0,0,0,1,0";
    const pt = corrected ? touchAt(e) : null;
    const target = pt ? document.elementFromPoint(pt.x, pt.y) : e.target;
    if (target) Detail.tap(target);
  }, { passive: true });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") Detail.close();
    else if ((e.key === "Enter" || e.key === " ") && document.activeElement?.matches("[data-dev], [data-pi], [data-up], [data-inet], [data-order], [data-orders], [data-testall], #detail-sheet [data-act]")) {
      e.preventDefault(); Detail.tap(document.activeElement);
    }
  });
})();
async function poll() {
  if (polling) return;                               // nie zwei Abfragen gleichzeitig (sonst überholen sie sich)
  polling = true;
  try {
    const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), 4000);
    const headers = { "X-Festpos-UI": "1", "X-Festpos-Lang": I18n.lang };
    let tok = null;                                  // aus der Verwaltung geöffnet: deren Anmeldung (Token) nutzen
    try { tok = sessionStorage.getItem("festpos.token"); } catch { /* egal */ }
    if (tok) headers.Authorization = "Bearer " + tok;
    const r = await fetch("/api/status", { signal: ctrl.signal, cache: "no-store", headers });
    clearTimeout(timer);
    if (r.status === 401 || r.status === 429) {
      // Nicht weiter fragen: jede Abfrage mit abgelaufener Anmeldung würde sonst als Fehlversuch zählen
      clearInterval(pollTimer);
      document.body.classList.add("offline");
      $("alert-title").textContent = t(r.status === 429 ? "status.login.locked" : "status.login.not_logged_in");
      $("alert-sub").textContent = t("status.login.hint");
      $("alert-link").hidden = false;                 // zur Anmeldung (nicht am Pi: dort braucht es keine)
      return;
    }
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    skew = s.server_time * 1000 - Date.now();
    if (version && s.version !== version) { location.reload(); return; }   // neue Version eingespielt
    version = s.version;
    fails = 0;
    document.body.classList.remove("offline");
    LAST = s;
    render(s);
    Detail.refresh();
    applyKiosk(s.kiosk);
  } catch {
    if (++fails >= 2) document.body.classList.add("offline");             // einmal ruckeln ist kein Ausfall
  } finally {
    polling = false;
    tickClock();
  }
}
// Sprache: wie die Verwaltung in diesem Browser (aus der Verwaltung geöffnet), sonst die Standardsprache
(async () => {
  let lang = "";
  try { lang = localStorage.getItem("festpos.admin.lang") || ""; } catch { /* egal */ }
  await I18n.load(lang);
  I18n.apply();
  document.title = t("status.head.title");
  pollTimer = setInterval(poll, 3000);
  poll();
  setInterval(tickClock, 1000);
})();
