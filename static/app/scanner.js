/* FestPOS – Bestell-App › QR-Scanner (BarcodeDetector, sonst jsQR aus vendor/). */
"use strict";

// ------------------------------------------------------------------ QR-Scanner
let scan = null;
function parseTableCode(raw) {
  const s = String(raw || "").trim();
  let m = s.match(/^(?:table|tisch)\s*[:=]\s*(.+)$/i);
  if (m) return m[1].trim();
  // auch der QR-Code der Gäste (QR-Bestellung am Tisch: …/?t=7&s=…) – ein Code pro Tisch für Gäste und Personal
  try { const u = new URL(s); const v = u.searchParams.get("table") || u.searchParams.get("tisch") || u.searchParams.get("t"); if (v) return v.trim(); } catch { /* keine URL */ }
  m = s.match(/[?&](?:table|tisch|t)=([^&]+)/i);
  if (m) return decodeURIComponent(m[1]).trim();
  if (/^[\w ./-]{1,20}$/.test(s)) return s;                 // reine Tischnummer
  return null;
}

async function startScan() {
  if (scan) return;                                // Doppeltipp: kein zweiter Scanner, keine zweite Kamera
  if (!navigator.mediaDevices?.getUserMedia) {
    toast(t(location.protocol === "https:" ? "app.scan.no_camera" : "app.scan.needs_https"), true);
    return;
  }
  const el = document.createElement("div");
  el.className = "scan";
  el.innerHTML = `<video playsinline muted></video><div class="frame"></div>
    <div class="msg">${th("app.scan.hint", { word: W().one })}</div>
    <div class="zoom">${[1, 2, 3, 4].map((z) => `<button data-act="zoom" data-z="${z}">${z}×</button>`).join("")}</div>
    <div class="ctl"><button class="alt" data-act="torch" aria-pressed="false">${th("app.scan.light")}</button>
    <button class="alt" data-act="scan-stop">${th("app.common.cancel")}</button></div>`;
  document.body.appendChild(el);
  history.pushState({ view: S.view, overlay: "scan" }, "");
  const me = scan = { el, stream: null, stop: false, torch: false, zoom: 1, digital: 1 };   // sofort: «Zurück» schliesst
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
  } catch (e) {
    if (scan === me) { stopScan(); history.back(); }
    toast(t(e.name === "NotAllowedError" ? "app.scan.denied" : "app.scan.no_camera"), true);
    return;
  }
  if (scan !== me || me.stop) { stream.getTracks().forEach((x) => x.stop()); return; }   // inzwischen abgebrochen
  me.stream = stream;
  const video = $("video", el);
  video.srcObject = stream;
  await video.play().catch(() => {});
  await setZoom(store.get("scanZoom", 2));                    // QR in der Tischmitte: standardmässig 2× zoomen

  let detect;
  if ("BarcodeDetector" in window) {
    try {
      const formats = await BarcodeDetector.getSupportedFormats();
      if (formats.includes("qr_code")) {
        const bd = new BarcodeDetector({ formats: ["qr_code"] });
        const c = document.createElement("canvas"), ctx = c.getContext("2d");
        detect = async () => {
          if (scan.digital <= 1) return (await bd.detect(video))[0]?.rawValue;
          const w = video.videoWidth, h = video.videoHeight;          // Digitalzoom: nur die Bildmitte prüfen
          if (!w) return null;
          const cw = w / scan.digital, ch = h / scan.digital, k = Math.min(2, 1280 / cw);   // vergrössert prüfen
          c.width = cw * k; c.height = ch * k;
          ctx.drawImage(video, (w - cw) / 2, (h - ch) / 2, cw, ch, 0, 0, cw * k, ch * k);
          return (await bd.detect(c))[0]?.rawValue;
        };
      }
    } catch { /* Fallback unten */ }
  }
  if (!detect) {                                              // Fallback: jsQR (lokal, kein Internet nötig)
    await loadScript("/vendor/jsQR.js");
    const c = document.createElement("canvas"), ctx = c.getContext("2d", { willReadFrequently: true });
    detect = async () => {
      const w = video.videoWidth, h = video.videoHeight;
      if (!w) return null;
      const src = Math.min(w, h) / scan.digital, sx = (w - src) / 2, sy = (h - src) / 2;
      const s = scan.digital > 1 ? 720 : Math.min(src, 720);    // Ausschnitt vergrössert prüfen
      c.width = c.height = s;
      ctx.drawImage(video, sx, sy, src, src, 0, 0, s, s);
      return window.jsQR(ctx.getImageData(0, 0, s, s).data, s, s, { inversionAttempts: "dontInvert" })?.data;
    };
  }
  const loop = async () => {
    if (!scan || scan.stop) return;
    try {
      const raw = await detect();
      if (raw) {
        const code = parseTableCode(raw);
        if (code) { buzz(60); stopScan(); history.back(); setTimeout(() => setTable(code), 50); return; }
        $(".msg", el).textContent = t("app.scan.not_table", { word: W().one });
      }
    } catch { /* nächstes Bild */ }
    setTimeout(loop, 120);
  };
  loop();
}
function stopScan() {
  if (!scan) return;
  scan.stop = true;
  scan.stream?.getTracks().forEach((x) => x.stop());
  scan.el.remove();
  scan = null;
}
// Zoom: Kamera-Zoom des Handys, wenn vorhanden (scharf), sonst Digitalzoom (Bildmitte vergrössern)
async function setZoom(z) {
  if (!scan?.stream) return;                       // Kamera noch nicht freigegeben
  const track = scan.stream.getVideoTracks()[0];
  const cap = track.getCapabilities ? track.getCapabilities() : {};
  scan.zoom = z;
  scan.digital = z;
  if (cap.zoom) {
    try {
      const v = Math.max(cap.zoom.min || 1, Math.min(cap.zoom.max || z, z));
      await track.applyConstraints({ advanced: [{ zoom: v }] });
      scan.digital = z / v;                                      // Rest (falls Kamera weniger kann) digital
    } catch { /* digital */ }
  }
  const video = $("video", scan.el);
  if (video) video.style.transform = scan.digital > 1 ? `scale(${scan.digital})` : "";
  scan.el.querySelectorAll('[data-act="zoom"]').forEach((b) => b.setAttribute("aria-pressed", +b.dataset.z === z));
  store.set("scanZoom", z);
}
async function toggleTorch(btn) {
  if (!scan?.stream) return;
  const track = scan.stream.getVideoTracks()[0];
  try {
    scan.torch = !scan.torch;
    await track.applyConstraints({ advanced: [{ torch: scan.torch }] });
    btn.setAttribute("aria-pressed", scan.torch);
  } catch { toast(t("app.scan.no_light")); }
}
function loadScript(src) {
  return new Promise((ok, fail) => {
    if (document.querySelector(`script[src="${src}"]`)) return ok();
    const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = fail;
    document.head.appendChild(s);
  });
}
