/* FestPOS Statusanzeige – gedrehter Rahmen (vor status.js laden). Eigene Datei wegen der
   Content-Security-Policy: keine Skripte direkt in der Seite. */
// Gedrehtes Display (Kiosk: status.html?drehung=90): Seite in einem gedrehten Rahmen zeigen.
// Der Rahmen hat die "richtige" Breite und Höhe, also passt das Layout wie bei einem quer montierten Display.
(function () {
  const q = new URLSearchParams(location.search);
  const d = [90, 180, 270].includes(Number(q.get("drehung"))) ? Number(q.get("drehung")) : 0;
  // sichtbare Fläche: aus der Einstellung (groesse=480x320), sonst höchstens so gross wie der Bildschirm.
  // Chromium zeichnet auf sehr kleinen Displays manchmal grösser als das Display – der Rest wäre unsichtbar.
  const m = /^(\d{2,4})x(\d{2,4})$/.exec(q.get("groesse") || "");
  const w = m ? +m[1] : Math.min(innerWidth, screen.width || innerWidth);
  const h = m ? +m[2] : Math.min(innerHeight, screen.height || innerHeight);
  if (window !== window.top || (!d && w === innerWidth && h === innerHeight)) return;
  window.FESTPOS_FRAME = true;
  const side = d === 90 || d === 270;
  document.documentElement.innerHTML = `<body style="margin:0;background:#0f1210;overflow:hidden;cursor:none"></body>`;
  const f = document.createElement("iframe");
  f.src = location.pathname + (/touchtest=1/.test(location.search) ? "?touchtest=1" : "");
  Object.assign(f.style, { position: "fixed", left: "0", top: "0", border: "0", transformOrigin: "0 0",
    width: (side ? h : w) + "px", height: (side ? w : h) + "px",
    transform: d === 90 ? `translate(${w}px,0) rotate(90deg)` : d === 270 ? `translate(0,${h}px) rotate(270deg)` : d === 180 ? `translate(${w}px,${h}px) rotate(180deg)` : "none" });
  document.body.appendChild(f);
  addEventListener("resize", () => location.reload());
})();
