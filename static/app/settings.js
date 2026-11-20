/* FestPOS – Bestell-App › Verlauf und Einstellungen am Handy. */
"use strict";

// ---------- Verlauf
// Stand laut Pi (h.srv, checkHistory): abgeschlossen (Tagesabschluss) und gelöscht nur noch ansehen (grau)
const HIST_DONE = ["closed", "gone"];
const SRV_TAG = { tab_done: "app.history.tab_done", closed: "app.history.closed", gone: "app.history.gone" };
function renderHistory() {
  app.innerHTML = `<section class="view">
    ${header(th("app.history.title"), { back: true, hist: false })}
    ${HIST.ok === false && history_.some((h) => h.status === "sent") ? `<p class="muted small hist-note">${th("app.history.unchecked")}</p>` : ""}
    <div class="hist">
      ${history_.length ? history_.map((h) => {
        const st = h.status === "sent" ? `#${String(h.order_id).padStart(4, "0")}` : h.status === "rejected" ? t("app.history.rejected") : t("app.history.waiting");
        const n = h.items.reduce((s, i) => s + i.qty, 0);
        return `<button data-act="open-pay" data-id="${h.id}" data-s="${h.status}"${HIST_DONE.includes(h.srv) ? " data-old" : ""}>
          <span class="t">${esc(h.table)}</span>
          <span class="meta">${h.test ? `<span class="testtag">${th("app.test.tag")}</span> ` : ""}${h.storno || h.srv === "storno" ? `<span class="stornotag">${th("app.history.storno")}</span> ` : ""}${th("app.history.meta", { time: I18n.fmtTime(h.ts), count: n })}<br><b>${esc(st)}</b>${SRV_TAG[h.srv] ? ` <span class="paytag srvtag">${th(SRV_TAG[h.srv])}</span>` : ""}${h.payment ? ` <span class="paytag">${esc(payLabel(h.payment))}</span>` : ""}${h.receipts?.length ? ` <span class="paytag">${th("app.receipt.tag")}</span>` : ""}</span>
          <span class="s">${chf(netOf(h))}</span></button>`;
      }).join("") : `<p class="empty">${th("app.history.empty")}</p>`}
    </div>
  </section>`;
}
// Stand der Bestellungen beim Pi nachfragen: Tagesabschluss und «Bestellungen löschen» kennt der Verlauf
// sonst nicht. Nur Einträge, die der Pi schon bestätigt hat (gesendet); offline bleibt alles, wie es ist. Gelöscht:
// Übungsbestellungen gleich weg, echte nach HIST_GONE_KEEP (z.B. nach dem Einspielen einer älteren Sicherung nicht
// sofort). ids: nur diese, kurz warten (vor dem Öffnen der Kasse); sonst alle, im Hintergrund.
const HIST_GONE_KEEP = 24 * 3600 * 1000;
// chain: die Abfragen laufen nacheinander, nie gleichzeitig – so liest der Pi in derselben Reihenfolge, in der die
// Antworten hier ankommen, und eine ältere Antwort kann keinen neueren Stand überschreiben (CodeRabbit zu PR 81).
// open: Nummer des letzten Antippens; jedes Verlassen des Verlaufs zählt weiter (render) – ein verspätetes Öffnen
// nach dem Verlassen fällt weg, auch wenn der Verlauf inzwischen wieder offen ist.
const HIST = { ok: null, busy: null, chain: Promise.resolve(), open: 0 };   // ok: letzte Abfrage geklappt (null = noch nie)
function checkHistory(ids) {
  if (S.tour) return Promise.resolve(false);
  if (!ids && HIST.busy) return HIST.busy;
  const run = HIST.chain.then(() => askHistory(ids));
  HIST.chain = run.catch(() => {});
  if (!ids) {
    HIST.busy = run;
    run.finally(() => { if (HIST.busy === run) HIST.busy = null; });
  }
  return run;
}
async function askHistory(ids) {
  const want = ids || history_.filter((h) => h.status === "sent").map((h) => h.id).slice(0, 60);   // erst jetzt: aktuell
  if (!want.length || S.tour) return true;
  try {
    const wait = ids ? 2500 : 8000;
    const r = await api("/api/orders/status", { method: "POST", body: JSON.stringify({ uuids: want }), timeout: wait });
    if (!r.ok) throw new Error(r.status);
    // die Zeitgrenze von api() endet mit den Kopfzeilen – auch das Lesen der Antwort begrenzen, sonst blockierte eine
    // hängende Antwort die Warteschlange (CodeRabbit zu PR 81)
    const late = new Promise((_, no) => setTimeout(() => no(new Error("timeout")), wait));
    const states = (await Promise.race([r.json(), late])).states || {}, now = Date.now();
    for (const h of history_) {
      const st = states[h.id];
      if (!st || h.status !== "sent") continue;
      h.srv = st;
      if (st !== "gone") delete h.goneAt;
      else if (!h.goneAt) h.goneAt = now;
    }
    // nur, was DIESE Antwort als gelöscht bestätigt (ein inzwischen wieder vorhandener Eintrag bleibt)
    history_ = history_.filter((h) => !(states[h.id] === "gone" && h.srv === "gone"
      && (h.test || now - h.goneAt >= HIST_GONE_KEEP)));
    store.set("history", history_);
    HIST.ok = true;
  } catch { HIST.ok = false; }
  if (S.view === "history" && !S.tour && !document.querySelector(".sheet")) renderHistory();
  return HIST.ok;
}
// Eintrag im Verlauf antippen: vorher kurz nachfragen (offline: wie bisher gleich die Kasse). Abgeschlossen oder
// gelöscht: nur ansehen – kassieren, stornieren, Quittung gehen dort nicht mehr.
async function openHistEntry(id) {
  const h = history_.find((x) => x.id === id);
  if (!h) return;
  const tap = ++HIST.open;
  if (h.status === "sent" && !HIST_DONE.includes(h.srv) && S.net !== "off") await checkHistory([id]);
  if (tap !== HIST.open || S.view !== "history") return;            // inzwischen anderes angetippt oder Verlauf verlassen
  const g = history_.find((x) => x.id === id);
  if (!g) { if (S.view === "history") renderHistory(); return; }   // inzwischen weg (Übung gelöscht)
  if (HIST_DONE.includes(g.srv)) openHistView(g);
  else openPay(id);
}
function openHistView(h) {
  openSheet(`<h2>${esc(h.table)}${h.order_id ? ` · #${String(h.order_id).padStart(4, "0")}` : ""}</h2>
    <p class="muted small">${th(h.srv === "gone" ? "app.history.gone_hint" : "app.history.closed_hint")}</p>
    <div class="bon${h.test ? " test" : ""}"><div class="lines">${h.items.map((i) => bonLine(i, h)).join("")}</div>
      <div class="total"><span>${th("app.pay.total", { cur: cur() })}</span><b>${chf(netOf(h))}</b></div></div>
    <div class="actions"><button class="alt" data-act="close-sheet">${th("app.common.back")}</button></div>`);
}

// ---------- Einstellungen
// Erstes Fenster nur mit dem, was die Bedienung braucht (Name, Einführung, Testmodus); alles Übrige unter
// «Erweiterte Einstellungen». Kein Speichern-Knopf: der Name gilt, sobald er geändert ist (Enter, Wegtippen, Schliessen).
function openSettings() {
  const tc = testCfg();
  openSheet(`<h2>${th("app.settings.title")}</h2>
    <label class="field">${th("app.settings.name")}
      <input id="devname" value="${esc(S.device)}" placeholder="${th("app.settings.name_placeholder")}" maxlength="40" autocomplete="off" enterkeyhint="done"></label>
    <div class="actions">
      <button class="alt" data-act="tour-start">${th("app.tour.start")}</button>
      ${S.test ? `<button class="alt testbtn" data-act="test-exit">${th("app.test.exit")}</button>`
        : tc.enabled ? `<button class="alt" data-act="test-start">${th("app.test.start")}</button>` : ""}
      ${S.boot?.can_soldout ? `<button class="alt" data-act="soldout-open">${th("app.stock.manage")}</button>` : ""}
      <button class="alt" data-act="settings-more">${th("app.settings.more")}</button>
    </div>`);
  $("#devname").addEventListener("change", saveDevName);
}
// Name übernehmen, falls geändert. Auch beim Schliessen oder Wechseln des Fensters (closeSheet, openSheet).
function saveDevName() {
  const n = $("#devname");
  if (!n || n.value.trim() === S.device) return;
  const before = S.device;
  S.device = n.value.trim();
  store.set("device", S.device);
  VIEWS[S.view]();                                    // Kopf mit dem neuen Namen (Fenster bleibt offen)
  toast(t("app.common.saved"));
  // Einführung automatisch, sobald jemand das Gerät übernimmt (neuer Name) – falls in der Verwaltung so eingestellt
  if (S.device && S.device !== before && S.boot?.tutorial_auto) setTimeout(() => { if (!S.tour) startTour(); }, 300);
}
// Ausverkauft melden (nur Handys mit der Rolle «Festleitung», Verwaltung › Geräte): antippen = umschalten (sofort
// ausverkauft); lange drücken oder «Stück» = Restmenge eingeben (Punkt 5)
function soldoutBody() {
  if (S.stockEdit) return stockEditBody();
  const list = [...products()].sort((a, b) => catSort(a.category_id) - catSort(b.category_id));
  return `<h2>${th("app.stock.title")}</h2><p class="muted small">${th("app.stock.hint")}</p>
    <div class="soldlist">${list.map((p) => `<div class="soldrow-w"><button class="soldrow" data-act="soldout-toggle" data-id="${p.id}" aria-pressed="${soldOut(p)}" style="--c:${p.color || catColor(p.category_id)}">
      <span>${esc(p.name)}</span><small>${soldOut(p) ? th("app.stock.tag_soldout") : p.stock != null ? th("app.stock.tag_left", { count: p.stock }) : th("app.stock.available")}</small></button>
      <button class="soldqty" data-act="stock-edit" data-id="${p.id}" aria-label="${th("app.stock.qty_for", { name: p.name })}">${th("app.stock.qty_btn")}</button></div>`).join("")
      || `<p class="empty">${th("app.settings.no_pricelist")}</p>`}</div>
    <div class="actions"><button class="alt" data-act="settings">${th("app.common.back")}</button></div>`;
}
function openSoldout() { S.stockEdit = null; openSheet(`<div id="soldoutsheet">${soldoutBody()}</div>`); }
function refreshSoldout() { const el = $("#soldoutsheet"); if (el) el.innerHTML = soldoutBody(); }
// Restmenge eines Produkts: − / + / Zahl eintippen, schnelle Werte; grün «Noch N verfügbar» (0 = ausverkauft)
function openStockEdit(id) {
  const p = productById(id);
  if (!p) return;
  S.stockEdit = { id, v: soldOut(p) ? 0 : p.stock ?? "" };
  if (!$("#soldoutsheet")) openSheet('<div id="soldoutsheet"></div>');
  refreshSoldout();
  $("#stockin")?.focus();
}
function stockEditBody() {
  const E = S.stockEdit, p = productById(E.id), v = E.v === "" ? null : +E.v, ok = v != null && v >= 0;
  return `<h2>${esc(p?.name || "")}</h2><p class="muted small">${th("app.stock.qty_hint")}</p>
    <div class="qty stockq"><button data-act="stock-q" data-d="-1" aria-label="${th("app.common.less")}" ${!v ? "disabled" : ""}>${ICON.minus}</button>
      <input id="stockin" type="number" inputmode="numeric" min="0" max="99999" value="${esc(String(E.v))}" aria-label="${th("app.stock.qty_label")}" enterkeyhint="done">
      <button data-act="stock-q" data-d="1" aria-label="${th("app.common.more")}">${ICON.plus}</button></div>
    <div class="quick">${[5, 10, 20, 50].map((n) => `<button class="chip" data-act="stock-set" data-v="${n}" aria-pressed="${v === n}">${n}</button>`).join("")}</div>
    <div class="actions">
      <button class="go" data-act="stock-save" ${ok ? "" : "disabled"}>${!ok ? th("app.stock.qty_enter") : v === 0 ? th("app.stock.qty_zero") : th("app.stock.qty_save", { count: v })}</button>
      ${p?.stock != null || soldOut(p) ? `<button class="alt" data-act="stock-none">${th("app.stock.qty_none")}</button>` : ""}
      <button class="alt" data-act="stock-back">${th("app.common.back")}</button></div>`;
}
function stockInput() {
  const el = $("#stockin");
  if (!el || !S.stockEdit) return;
  S.stockEdit.v = el.value === "" ? "" : Math.max(0, Math.min(99999, Math.floor(+el.value) || 0));
  const btns = $("#soldoutsheet .actions");
  const fresh = document.createElement("div");
  fresh.innerHTML = stockEditBody();
  btns.replaceWith(fresh.querySelector(".actions"));
  $("#soldoutsheet [data-act='stock-q'][data-d='-1']").disabled = !S.stockEdit.v;
}
document.addEventListener("input", (e) => { if (e.target.id === "stockin") stockInput(); });
// Verlassen des Felds: es zeigt genau, was gespeichert würde (z.B. «3.7» -> 3, «-2» -> 0)
document.addEventListener("change", (e) => {
  if (e.target.id === "stockin" && S.stockEdit) e.target.value = S.stockEdit.v === "" ? "" : String(S.stockEdit.v);
});
async function saveStock(stock) {
  const E = S.stockEdit, p = E && productById(E.id), sent = E?.v;
  if (!p || soldBusy) return;
  soldBusy = true;
  try {
    const r = await api(`/api/phone/products/${p.id}/stock`, { method: "POST", body: JSON.stringify({ stock }) });
    let j = {};
    try { j = await r.json(); } catch { /* egal */ }
    if (!r.ok) { toast(typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: r.status }), true); return; }
    applyStock(j);
    // nur schliessen, wenn noch dasselbe Fenster mit derselben Zahl offen ist (inzwischen geändert: bleibt offen)
    if (S.stockEdit === E && (stock == null || E.v === sent)) { S.stockEdit = null; refreshSoldout(); }
    buzz(15);
    toast(stock === 0 ? t("app.stock.marked", { name: p.name }) : stock == null ? t("app.stock.released", { name: p.name })
      : t("app.stock.qty_saved", { name: p.name, count: stock }));
  } catch { toast(t("app.stock.offline"), true); }
  finally { soldBusy = false; }
}
let soldBusy = false;
async function toggleSoldout(id) {
  const p = productById(id);
  if (!p || soldBusy) return;
  soldBusy = true;
  const want = !soldOut(p);
  try {
    const r = await api(`/api/phone/products/${id}/soldout`, { method: "POST", body: JSON.stringify({ soldout: want }) });
    let j = {};
    try { j = await r.json(); } catch { /* egal */ }
    if (!r.ok) { toast(typeof j.detail === "string" ? j.detail : t("app.net.error_status", { status: r.status }), true); return; }
    applyStock(j);
    buzz(15);
    toast(t(want ? "app.stock.marked" : "app.stock.released", { name: p.name }));
  } catch { toast(t("app.stock.offline"), true); }
  finally { soldBusy = false; }
}
function openSettingsMore() {
  const theme = store.get("theme", "auto");
  const langs = S.boot?.languages || [];
  openSheet(`<h2>${th("app.settings.more")}</h2>
    ${langs.length > 1 ? `<label class="field">${th("app.settings.language")}<select id="devlang">
      <option value="">${th("app.settings.language_auto", { name: langs.find((l) => l.code === S.boot.language)?.name || S.boot.language })}</option>
      ${langs.map((l) => `<option value="${esc(l.code)}" ${store.get("lang", "") === l.code ? "selected" : ""}>${esc(l.name)}</option>`).join("")}
    </select></label>` : ""}
    <div class="field">${th("app.settings.theme")}
      <div class="seg">
        ${[["auto", "app.settings.theme_auto"], ["light", "app.settings.theme_light"], ["dark", "app.settings.theme_dark"]].map(([v, k]) =>
          `<button data-act="theme" data-v="${v}" aria-pressed="${theme === v}">${th(k)}</button>`).join("")}
      </div></div>
    <div class="field">${th("app.settings.screen")}
      <div class="seg">
        <button data-act="wake" data-v="1" aria-pressed="${!!store.get("wake", false)}">${th("app.settings.screen_on")}</button>
        <button data-act="wake" data-v="0" aria-pressed="${!store.get("wake", false)}">${th("app.settings.screen_normal")}</button>
      </div>${BRIGHT.bound ? `<p class="muted small" style="margin:6px 0 0">${th("app.settings.brightness_keys")}</p>` : ""}</div>
    <div class="actions">
      <button class="alt" data-act="app-reload">${th("app.settings.app_reload")}</button>
    </div>
    ${fullyBlock()}
    <p class="muted small" style="margin:14px 0 6px">
      ${th("app.settings.version", { version: APP_VERSION })}${S.boot?.version && S.boot.version !== APP_VERSION
        ? ` – <b class="bad">${th("app.settings.version_differs", { version: S.boot.version })}</b>` : ""}<br>
      ${S.boot?.cert ? `${th("app.settings.cert", { name: S.boot.cert.name, date: fmtDate(S.boot.cert.expires_at) })}<br>` : ""}
      ${S.boot ? th("app.settings.products", { count: products().length }) : th("app.settings.no_pricelist")}
      ${outbox.length ? ` ${th("app.settings.outbox", { count: outbox.length })}` : ""}</p>
    <nav class="navlinks" aria-label="${th("app.settings.go_aria")}">
      <a class="alt" href="/admin.html">${th("app.settings.admin")}</a>
      <a class="alt" href="/status.html">${th("app.settings.status")}</a>
    </nav>
    <div class="actions"><button class="alt" data-act="settings">${th("app.common.back")}</button></div>`);
  $("#devlang")?.addEventListener("change", async (e) => {
    store.set("lang", e.target.value);
    await I18n.load(e.target.value);
    VIEWS[S.view]();                                  // Ansicht dahinter in der neuen Sprache (Fenster bleibt offen)
    openSettingsMore();
  });
}
// Fully Kiosk (Punkt 7) – nur, wenn die App in Fully läuft und Fully die Funktion kennt (JavaScript-
// Schnittstelle): Helligkeit, Bildschirm aus, Fully neu starten, Cache leeren, Handheld neu starten
// («ausschalten» gibt es nicht: fully.shutdown() geht nur auf gerooteten Geräten). Mit der Rolle «Festleitung» dazu Kiosk-Modus aus/ein und die Android-Einstellungen (dafür entsperrt die App
// den Kiosk; danach «Kiosk-Modus ein»).
const fullyHas = (fn) => typeof window.fully?.[fn] === "function";
function fullyBlock() {
  if (!window.fully) return "";
  const lead = !!S.boot?.can_kiosk, b = (act, key, fn) => (fullyHas(fn) ? `<button class="alt" data-act="${act}">${th(key)}</button>` : "");
  const bright = fullyHas("setScreenBrightness")
    ? `<div class="seg fully-bright"><button data-act="fully-bright" data-d="-1" aria-label="${th("app.fully.darker")}">☾ −</button>
        <button data-act="fully-bright" data-d="1" aria-label="${th("app.fully.brighter")}">☀ +</button></div>` : "";
  const kiosk = lead ? b("fully-kiosk-off", "app.fully.kiosk_off", "unlockKiosk") + b("fully-kiosk-on", "app.fully.kiosk_on", "lockKiosk")
    + b("fully-android", "app.fully.android", "startApplication") : "";
  return `<div class="field fully-box">${th("app.fully.title")}${bright ? `<span class="muted small">${th("app.fully.brightness")}</span>${bright}` : ""}
    <div class="actions">${b("fully-screen-off", "app.fully.screen_off", "turnScreenOff")}${b("fully-cache", "app.fully.cache", "clearCache")}
      ${b("fully-restart", "app.fully.restart", "restartApp")}${b("fully-reboot", "app.fully.reboot", "reboot")}${kiosk}</div>
    ${lead ? `<p class="muted small">${th("app.fully.kiosk_hint")}</p>` : ""}</div>`;
}
function fullyAction(act, d) {
  const f = window.fully;
  if (!f) return;
  try {
    if (act === "fully-bright") stepBrightness(+d);
    else if (act === "fully-screen-off") { closeSheet(); f.turnScreenOff(); }
    else if (act === "fully-cache") { if (confirm(t("app.fully.cache_ask"))) { f.clearCache(); location.reload(); } }
    else if (act === "fully-restart") { if (confirm(t("app.fully.restart_ask"))) f.restartApp(); }
    else if (act === "fully-reboot") fullyReboot();
    else if (!S.boot?.can_kiosk) return;                     // ab hier nur Festleitung
    else if (act === "fully-kiosk-off") { if (confirm(t("app.fully.kiosk_off_ask"))) { f.unlockKiosk(); toast(t("app.fully.kiosk_off_done")); } }
    else if (act === "fully-kiosk-on") { f.lockKiosk(); toast(t("app.fully.kiosk_on_done")); }
    else if (act === "fully-android" && confirm(t("app.fully.android_ask"))) {
      if (fullyHas("unlockKiosk")) f.unlockKiosk();
      f.startApplication("com.android.settings");
    }
  } catch { toast(t("app.fully.failed"), true); }
}
// Was auf diesem Handheld noch offen ist: nicht gesendete Bestellungen und Zahlungen, abgelehnte Bestellungen, Warenkorb.
// Es bleibt alles gespeichert und geht nach dem Einschalten weiter – aber bis dahin eben nicht hinaus. Warenkorb: immer
// der echte, wie gespeichert (auch im Testmodus; der Übungs-Warenkorb fällt beim Neustart ohnehin weg, app.js)
function openHere() {
  const cart = Object.values(store.get("cart", {}) || {})
    .reduce((n, v) => n + (typeof v === "number" ? Math.max(0, v) : v?.q > 0 ? v.q : 0), 0);   // auch altes Format
  return [[outbox.length, "app.fully.open_orders"], [payq.length + partq.length + tipq.length, "app.fully.open_pays"],
    [rejected.length, "app.fully.open_rejected"], [cart, "app.fully.open_cart"]]
    .filter(([n]) => n > 0).map(([n, k]) => `• ${t(k, { count: n })}`);
}
// Handheld neu starten: Rückfrage; ist noch etwas offen, Warnung und ein zweites Mal bestätigen. Fully
// kann das nur als Geräteinhaber und meldet nichts zurück – läuft die App nach POWER_WAIT noch, sagt sie es.
let POWER_WAIT = 15000;                            // let: der Browser-Test kürzt die Wartezeit
function fullyReboot() {
  const open = openHere();
  if (open.length) {
    if (!confirm(t("app.fully.reboot_open", { list: open.join("\n") }))) return;
    if (!confirm(t("app.fully.reboot_sure"))) return;
  } else if (!confirm(t("app.fully.reboot_ask"))) return;
  closeSheet();
  toast(t("app.fully.reboot_going"));
  window.fully.reboot();
  setTimeout(() => { if (document.visibilityState === "visible") toast(t("app.fully.reboot_failed"), true); }, POWER_WAIT);
}
function applyTheme() {
  const v = store.get("theme", "auto");
  if (v === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = v;
}
let wakeLock = null;
async function applyWake() {
  try {
    if (store.get("wake", false) && "wakeLock" in navigator && document.visibilityState === "visible") {
      if (!wakeLock || wakeLock.released) {        // nur EINE Sperre halten (früher bei jedem Aufruf eine neue)
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      }
    } else if (wakeLock) { const w = wakeLock; wakeLock = null; await w.release(); }
  } catch { /* nicht unterstützt */ }
}
