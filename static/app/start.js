/* FestPOS – Bestell-App › Start der App (läuft als letztes Skript, siehe index.html). */
"use strict";

// ------------------------------------------------------------------ Start
async function start() {
  applyTheme();
  applyWake();
  applyTestLook();
  await I18n.load(store.get("lang", ""));            // Sprache des Geräts (sonst Vorgabe der Verwaltung)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") { applyWake(); loadBoot(); flush(); }
    else if (scan) { stopScan(); history.back(); }   // Bildschirm aus: Kamera nicht weiterlaufen lassen
  });
  // Tisch per Link/QR mit Kamera-App: https://192.168.55.10/?table=12
  const qp = new URLSearchParams(location.search).get("table");
  history.replaceState({ view: "table" }, "", location.pathname);
  S.locked = store.get("locked", false) || pinNeededAtStart();
  render();
  if (testAtStart) { toast(t("app.test.ended_start")); reportTest(false, "start"); }
  if (qp) setTable(qp);
  await Promise.race([initBattery(), new Promise((r) => setTimeout(r, 300))]);   // Akku gleich mit der ersten Meldung
  initBrightnessKeys();
  loadBoot();
  syncTime();
  flush();
  setTimeout(checkHistory, 3000);                                   // Verlauf: Tagesabschluss/Löschen auf dem Pi?
  setInterval(() => { if (!outbox.length) loadBoot(); }, 20000);   // flush() reicht auch Zahlarten nach (payq)   // Preise und Einstellungen übernehmen
  window.addEventListener("online", flush);
  bindNetEvents();
  pollStatus();
  setInterval(pollStatus, 10000);                                   // Drucker, Quittungen, Server erreichbar?
  setInterval(() => { readFullyBattery(); netFix(); }, 5000);       // Akku (für die Statusanzeige); offline: WLAN neu verbinden
  // PIN-Sperre: Aktivität merken, Zeitsperre prüfen, beim Ausschalten des Bildschirms sperren
  ["pointerdown", "keydown"].forEach((ev) => document.addEventListener(ev, touchActivity, { passive: true }));
  document.addEventListener("visibilitychange", () => {
    const pin = S.boot?.pin;
    if (document.visibilityState === "hidden" && pin?.enabled && pin.on_screen_off) lock();
    else checkLock();
  });
  setInterval(checkLock, 20000);
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    // Neue App-Version bereit (neuer Service Worker übernimmt) -> einmal neu laden, damit die neue
    // Version läuft. Aber nicht mitten in der Arbeit (Bezahlen, Bestellen, offenes Fenster, Scanner, Einführung):
    // erst wenn die Tischansicht leer offen ist. Warenkorb, Ausgang und Verlauf bleiben ohnehin im Gerät.
    const hadController = !!navigator.serviceWorker.controller;
    let reloadWanted = false, reloaded = false;
    const idle = () => S.view === "table" && !S.input && cartCount() === 0 && !S.optEdit && !S.locked && !S.tour &&
      !document.querySelector(".sheet, .scan");
    const reloadWhenIdle = () => { if (reloadWanted && !reloaded && idle()) { reloaded = true; location.reload(); } };
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (hadController) { reloadWanted = true; reloadWhenIdle(); }
    });
    setInterval(reloadWhenIdle, 2000);
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") reg.update().catch(() => {}); });
      setInterval(() => reg.update().catch(() => {}), 10 * 60000);
    }).catch(() => {});
  }
}
start();
