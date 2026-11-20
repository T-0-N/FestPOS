/* FestPOS – Bestell-App › Akku (für die Statusanzeige) und Helligkeit mit den Lautstärketasten (Fully). */
"use strict";

// ------------------------------------------------------------------ Akku (für die Statusanzeige)
// In Fully Kiosk mit eingeschalteter JavaScript-Schnittstelle direkt von Fully, sonst über den Browser
// (Chrome: Battery Status API). Geht beides nicht, meldet das Handy eben keinen Akkustand.
const BAT = { level: null, charging: false };
function readFullyBattery() {
  try {
    if (typeof window.fully?.getBatteryLevel !== "function") return false;
    const level = Number(window.fully.getBatteryLevel());
    if (!Number.isFinite(level)) return false;
    BAT.level = Math.max(0, Math.min(100, Math.round(level)));
    BAT.charging = typeof window.fully.isPlugged === "function" && !!window.fully.isPlugged();
    return true;
  } catch { return false; }
}
async function initBattery() {
  if (readFullyBattery()) { setInterval(readFullyBattery, 60000); return; }
  try {
    const b = await navigator.getBattery?.();
    if (!b) return;
    const upd = () => { BAT.level = Math.round(b.level * 100); BAT.charging = !!b.charging; };
    upd();
    b.addEventListener("levelchange", upd);
    b.addEventListener("chargingchange", upd);
  } catch { /* Browser kann es nicht: egal */ }
}

// ------------------------------------------------------------------ Helligkeit mit den Lautstärketasten (Fully)
// Fully meldet die Tasten über seine JavaScript-Schnittstelle (fully.bind); Helligkeit 0–255. Ohne Fully: nichts.
const BRIGHT = { step: 0.1, min: 0.05, bound: false };
function stepBrightness(dir) {
  try {
    const f = window.fully;
    let cur = Number(f.getScreenBrightness?.());
    if (!Number.isFinite(cur) || cur < 0) cur = 128;              // Systemstandard (-1): von der Mitte aus
    const max = cur > 0 && cur < 1 ? 1 : 255;                     // falls Fully 0…1 meldet
    const next = Math.max(max * BRIGHT.min, Math.min(max, cur + dir * max * BRIGHT.step));   // nie ganz schwarz
    f.setScreenBrightness(max === 1 ? next : Math.round(next));
    toast(t("app.common.brightness", { pct: Math.round((next / max) * 100) }));
  } catch { /* Fully kann es nicht: egal */ }
}
function initBrightnessKeys() {
  const f = window.fully;
  if (BRIGHT.bound || typeof f?.bind !== "function" || typeof f.setScreenBrightness !== "function") return;
  BRIGHT.bound = true;
  window.festposBrightness = stepBrightness;                     // fully.bind ruft Code als Text auf: braucht einen globalen Namen
  try {
    f.bind("volumeUp", "festposBrightness(1);");
    f.bind("volumeDown", "festposBrightness(-1);");
  } catch { BRIGHT.bound = false; }
}
