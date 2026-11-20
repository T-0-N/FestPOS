/* FestPOS – Bestell-App › Gerätezertifikat (Warnung, Erneuern), PIN-Sperre, Testmodus (Üben). */
"use strict";

// ---------- Gerätezertifikat: Warnung vor Ablauf und Erneuern per Klick
function certBanner() {
  const c = S.boot?.cert;
  if (c?.revoked) return `<div class="banner bad"><b>${th("app.cert.revoked_title")}</b>${th("app.cert.revoked_text")}</div>`;
  if (!c?.renew_due || c.auto) return "";              // erneuert der Pi selbst (Fully, T3): kein Hinweis
  const d = c.days_left;
  const when = d <= 0 ? t("app.cert.expires_today") : d === 1 ? t("app.cert.expires_tomorrow")
    : d > 60 ? t("app.cert.expires_months", { count: Math.round(d / 30) }) : t("app.cert.expires_days", { count: d });
  // Fully liest das Zertifikat aus einer Datei, nicht aus dem Android-Speicher: erneuern nur über die Verwaltung
  if (c.fully) return `<div class="banner"><b>${th("app.cert.expires_title", { when })}</b> (${esc(fmtDate(c.expires_at))}).
    ${th("app.cert.fully_text")}</div>`;
  const done = store.get("certRenewed", null);
  if (done && done.from === c.serial) {
    return `<div class="banner"><b>${th("app.cert.renewed_title", { date: fmtDate(done.expires_at) })}</b>
      ${th("app.cert.renewed_text", { name: c.name, until: `${done.expires_at.slice(5, 7)}.${done.expires_at.slice(0, 4)}` })}
      <button class="link" data-act="renew">${th("app.cert.renew_again")}</button></div>`;
  }
  return `<div class="banner"><b>${th("app.cert.expires_title", { when })}</b> (${esc(fmtDate(c.expires_at))}).
    ${th("app.cert.expires_text")}
    <button class="alt" data-act="renew" style="margin-top:10px">${th("app.cert.renew_now")}</button></div>`;
}
async function renewCert() {
  let j;
  try {
    const r = await api("/api/device/renew", { method: "POST", timeout: 30000 });
    j = await r.json();
    if (!r.ok) return toast(j.detail || t("app.cert.renew_failed"), true);
  } catch { return toast(t("app.cert.server_unreachable"), true); }
  Object.assign(document.createElement("a"), { href: j.url, download: j.filename }).click();
  store.set("certRenewed", { from: S.boot.cert.serial, expires_at: j.expires_at, at: Date.now() });
  openSheet(`<h2>${th("app.cert.new_title")}</h2>
    <p style="margin:0 0 8px">${th("app.cert.new_download", { file: j.filename, date: fmtDate(j.expires_at) })}</p>
    <ol class="steps">
      <li>${th("app.cert.new_step1")}</li>
      <li>${th("app.cert.new_step2")}</li>
    </ol>
    <div class="num bigpw">${esc(j.password)}</div>
    <p class="muted small">${th("app.cert.new_after")}</p>
    <div class="actions"><button class="go" data-act="close-sheet">${th("app.common.understood")}</button></div>`);
  const banner = $(".banner");                                // nur den Hinweis aktualisieren (render() würde das Fenster schliessen)
  if (banner) banner.outerHTML = certBanner();
}

// ---------- PIN-Sperre (optional, in der Verwaltung eingestellt)
// Die PIN wird lokal gegen einen gesalzenen Hash geprüft – funktioniert also auch ohne Verbindung.
// Sie schützt vor Fremdbedienung; die eigentliche Absicherung ist das Gerätezertifikat.
const pinCfg = () => S.boot?.pin || { enabled: false };
function touchActivity() { if (!S.locked) store.set("lastActive", Date.now()); }
function pinNeededAtStart() { return !!pinCfg().enabled && !store.get("unlocked", false); }
function lock() {
  if (!pinCfg().enabled || S.locked) return;
  S.locked = true; store.set("locked", true); store.set("unlocked", false);
  closeOverlays(); showLock();
}
function checkLock() {
  const pin = pinCfg();
  if (!pin.enabled) { if (S.locked) { S.locked = false; store.set("locked", false); hideLock(); } return; }
  if (!store.get("unlocked", false)) return lock();
  const idle = (pin.idle_minutes || 0) * 60000;
  if (idle && Date.now() - store.get("lastActive", Date.now()) > idle) lock();
}
// PIN als Punkte: ● getippt, ○ fehlt noch – so passt auch eine 8-stellige PIN auf 360 px und der Text bleibt einzeilig
const pinDots = (n, len) => "●".repeat(n) + (len > n ? `<i>${"○".repeat(len - n)}</i>` : "");
function showLock() {
  let el = $("#lock");
  if (!el) { el = document.createElement("div"); el.id = "lock"; el.className = "lock"; document.body.appendChild(el); }
  el.innerHTML = `<section class="view table-view">
    <header class="top lock-top">${logoImg()}<h1><i class="lock-ico">${ICON.lock}</i>${th("app.lock.title")}</h1><span class="net" data-s="${S.net}"><i></i><span>${esc(S.device || "")}</span></span></header>
    <div class="table-display"><span>${th("app.lock.enter")}</span><output id="pinout" class="num pindots">${pinDots(S.pinInput.length, pinCfg().length || 4)}</output></div>
    <div class="pad">
      ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-act="pin" data-d="${n}">${n}</button>`).join("")}
      <span></span><button data-act="pin" data-d="0">0</button>
      <button class="k-fn" data-act="pin-del" aria-label="${th("app.common.delete")}">${ICON.del}</button>
    </div>
    <p id="pinmsg" class="pinmsg"></p>
  </section>`;
}
function hideLock() { $("#lock")?.remove(); }
const hashPin = async (salt, pin) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(salt + pin)))]
  .map((x) => x.toString(16).padStart(2, "0")).join("");
let pinFails = 0, pinBlockedUntil = 0;
async function pinTyped() {
  const out = $("#pinout"); if (out) out.innerHTML = pinDots(S.pinInput.length, pinCfg().length || 4);
  if (S.pinInput.length < (pinCfg().length || 4)) return;
  if (Date.now() < pinBlockedUntil) { $("#pinmsg").textContent = t("app.lock.wait"); S.pinInput = ""; return; }
  const pin = pinCfg();
  if (await hashPin(pin.salt, S.pinInput) === pin.hash) {
    S.locked = false; S.pinInput = ""; pinFails = 0;
    store.set("locked", false); store.set("unlocked", true); store.set("lastActive", Date.now());
    buzz(30); hideLock();
  } else if (S.pinInput.length >= (pin.length || 8)) {
    wrongPin();
  }
}
function wrongPin() {
  pinFails += 1; S.pinInput = ""; buzz([80, 60, 80]);
  if (pinFails >= 5) pinBlockedUntil = Date.now() + 30000;
  const out = $("#pinout"); if (out) out.innerHTML = pinDots(0, pinCfg().length || 4);
  $("#pinmsg").textContent = t(pinFails >= 5 ? "app.lock.wrong_blocked" : "app.lock.wrong");
}

// ---------- Testmodus (Üben): mit PIN aus der Verwaltung (oder ohne, wenn so eingestellt), immer deutlich gekennzeichnet
// Bestellungen tragen test=true: der Server zählt sie nirgends mit und druckt sie nur, wenn das in der
// Verwaltung eingestellt ist – dann gross als TEST gekennzeichnet. Warenkorb und Tisch sind getrennt.
// Beim Start der App ist er immer aus (app.js); Statusanzeige und Verwaltung können ihn beenden (pollStatus, T7).
function applyTestLook() {
  document.body.classList.toggle("testmode", S.test);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", S.test ? "#ffcc00" : "#019724");
}
// how (fürs Protokoll): app = am Handy, tour = am Ende der Einführung, start = beim Start der App, remote = aus der
// Statusanzeige/Verwaltung, admin = in der Verwaltung ganz ausgeschaltet
function setTest(on, how = "app") {
  if (S.tour) endTour();
  if (!on) { store.del("cart.test"); store.del("cartTable.test"); }      // Übungsreste nicht aufheben
  if (S.test !== !!on) reportTest(!!on, how);
  S.test = !!on;
  store.set("test", S.test);
  S.cart = loadCart();
  S.table = null; S.input = ""; S.pay = null;
  applyTestLook();
  S.view = "table";
  history.replaceState({ view: "table" }, "");
  render();
}
function reportTest(on, how) {
  setTimeout(() => api("/api/phone/test", { method: "POST", body: JSON.stringify({ on, how }) }).catch(() => {}), 0);
}
// Testmodus starten: mit PIN, falls verlangt (ältere Server ohne «pin»: immer); ohne PIN kurz bestätigen –
// ausser am Ende der Einführung, dort ist «Ja, üben» schon die Bestätigung.
function startTest(how) {
  const cfg = testCfg();
  if (!cfg.enabled) return;
  if (cfg.pin !== false) openTestPin(how);
  else if (how === "tour") { setTest(true, how); toast(t("app.test.started")); }
  else openSheet(`<h2>${th("app.test.confirm_title")}</h2>
    <p class="muted">${th("app.test.start_text")} ${th(cfg.print ? "app.test.prints" : "app.test.no_print")} ${th("app.test.start_note")}</p>
    <div class="actions"><button class="go" data-act="test-go">${th("app.test.start_go")}</button>
      <button class="alt" data-act="close-sheet">${th("app.common.cancel")}</button></div>`);
}
function openTestPin(how = "app") {
  S.testPin = "";
  S.testHow = how;
  openSheet(`<h2>${th("app.test.start_title")}</h2>
    <p class="muted">${th("app.test.start_text")} ${th(testCfg().print ? "app.test.prints" : "app.test.no_print")}</p>
    <div class="table-display small"><span>${th("app.test.pin")}</span><output id="tpinout" class="num pindots">${pinDots(0, testCfg().length || 4)}</output></div>
    <div class="pad small">
      ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `<button data-act="tpin" data-d="${n}">${n}</button>`).join("")}
      <span></span><button data-act="tpin" data-d="0">0</button>
      <button class="k-fn" data-act="tpin-del" aria-label="${th("app.common.delete")}">${ICON.del}</button>
    </div>
    <p id="tpinmsg" class="pinmsg"></p>
    <div class="actions"><button class="alt" data-act="close-sheet">${th("app.common.cancel")}</button></div>`);
}
let tpinFails = 0, tpinBlockedUntil = 0;
async function testPinTyped() {
  const cfg = testCfg(), out = $("#tpinout"), msg = $("#tpinmsg");
  if (out) out.innerHTML = pinDots(S.testPin.length, cfg.length || 4);
  if (S.testPin.length < (cfg.length || 4)) return;
  if (Date.now() < tpinBlockedUntil) { if (msg) msg.textContent = t("app.lock.wait"); S.testPin = ""; return; }
  if (cfg.enabled && await hashPin(cfg.salt, S.testPin) === cfg.hash) {
    tpinFails = 0; S.testPin = ""; closeSheet(); buzz([30, 40, 30]);
    setTest(true, S.testHow || "app");
    toast(t("app.test.started"));
  } else if (S.testPin.length >= (cfg.length || 8)) {
    tpinFails += 1; S.testPin = ""; buzz([80, 60, 80]);
    if (tpinFails >= 5) tpinBlockedUntil = Date.now() + 30000;
    if (out) out.textContent = "";
    if (msg) msg.textContent = t(tpinFails >= 5 ? "app.lock.wrong_blocked" : "app.lock.wrong");
  }
}
