"""Verwaltung › Einstellungen: allgemeine Einstellungen lesen und speichern (PIN und Testmodus nur als Hash).
Eigene Seiten haben Bildschirm (display.py), Fully Kiosk (fully.py) und Mobilfunk-Router (uplink_admin.py)."""
from __future__ import annotations

import hashlib
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, backup as backup_mod, device_ids, fully, i18n, ledger, monitor as monitor_mod
from . import ledger_base as lb
from .core import admin, admin_cert_ok, current_user, db, forget_cert_only, rt
from .i18n import t

admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


# -- Einstellungen ----------------------------------------------------------
class MonitorDeviceIn(BaseModel):
    id: str | None = Field(default=None, pattern=r"^p[0-9a-f]{12}$")   # Handy: Geräte-ID (T1), sonst über IP/Name
    name: str = Field(min_length=1, max_length=30)
    kind: str = Field(pattern=r"^(phone|net)$")          # Handy oder Netzwerkgerät
    ip: str = Field(pattern=monitor_mod.HOST_RE.pattern)
    port: int | None = Field(default=None, ge=1, le=65535)   # leer = Ping
    active: bool = True                                       # im Einsatz (sonst nicht geprüft, nicht angezeigt)


class SettingsIn(BaseModel):
    event_name: str | None = Field(default=None, max_length=48)
    currency: str | None = Field(default=None, max_length=6)
    show_history: bool | None = None          # Bestellverlauf auf den Handys anzeigen
    show_recent_tables: bool | None = None    # zuletzt bediente Tische unter der Tischeingabe
    table_word: str | None = Field(default=None, pattern=r"^(Tisch|Platz)$")   # Bezeichnung in App/Bon
    tables_mode: str | None = Field(default=None, pattern=r"^(free|warn|strict)$")   # Tischwahl: frei/warnen/erzwingen
    delivery_times: str | None = Field(default=None, max_length=200)  # "Sofort, Pause, Schluss" – erster = Standard
    monitor_hosts: str | None = Field(default=None, max_length=500)   # alt: "Switch=192.168.55.30; …"
    monitor_devices: list[MonitorDeviceIn] | None = Field(default=None, max_length=40)   # überwachte Geräte
    default_printer_id: int | None = None     # 0 = automatisch (erster Drucker)
    pin_enabled: bool | None = None           # PIN-Sperre in der App
    pin: str | None = Field(default=None, pattern=r"^\d{4,8}$")
    pin_idle_minutes: int | None = Field(default=None, ge=0, le=720)   # 0 = keine Zeitsperre
    pin_on_screen_off: bool | None = None     # sperren, sobald der Bildschirm aus war
    cert_days: int | None = Field(default=None, ge=7, le=3650)        # Gültigkeit neuer Gerätezertifikate
    cert_warn_days: int | None = Field(default=None, ge=1, le=1095)   # Warnung so viele Tage vor Ablauf
    cert_renew_days: int | None = Field(default=None, ge=0, le=1095)  # automatisch erneuern so viele Tage vor Ablauf (0 = aus, T3)
    mac_change_minutes: int | None = Field(default=None, ge=0, le=240)  # feste IP bei neuer MAC nach so vielen Minuten (0 = nur von Hand, T4)
    language: str | None = Field(default=None, max_length=12)         # Standardsprache (Handys, Bons, Statusanzeige)
    product_images: str | None = Field(default=None, pattern=r"^(none|small|large)$")   # Produktbilder auf den Kacheln
    logo_plate: str | None = Field(default=None, pattern=r"^(white|none)$")   # weisser Grund hinter dem Logo (dunkle Seiten)
    options_outside: str | None = Field(default=None, pattern=r"^(close|warn|ignore)$")   # neben die Optionsauswahl getippt
    payment_mode: str | None = Field(default=None, pattern=r"^(off|optional|required)$")  # Zahlart an der Kasse
    tips_enabled: bool | None = None          # Trinkgeld an der Kasse erfassen (app/tips.py)
    tab_mode: str | None = Field(default=None, pattern=r"^(now|open)$")             # sofort abrechnen | Tisch offen lassen
    tab_who: str | None = Field(default=None, pattern=r"^(own|own_lead|any)$")      # wer einen offenen Tisch abrechnet
    tab_idle: int | None = Field(default=None, ge=0, le=600)    # Hinweis «lange nichts bestellt» nach Minuten (0 = nie)
    stock_check: str | None = Field(default=None, pattern=r"^(off|stock|always)$")   # vor dem Kassieren auf den Server warten
    stock_check_wait: int | None = Field(default=None, ge=1, le=10)                   # höchstens so viele Sekunden
    nachbuchen: str | None = Field(default=None, pattern=r"^(off|all|storno)$")       # «Nachbuchen ohne Bon»: wer darf
    swipe_categories: bool | None = None      # im Produktbereich wischen = andere Kategorie
    staff_qr: bool | None = None              # Tisch per QR-Code scannen (Personal); aus: ✕ in der Tischwahl
    admin_cert_only: bool | None = None       # Verwaltung nur mit Gerätezertifikat (main.admin_cert_gate)
    tutorial_auto: bool | None = None         # Einführung startet nach dem Eingeben/Ändern des Gerätenamens
    test_enabled: bool | None = None          # Testmodus (Üben) auf den Handys erlaubt
    test_pin: str | None = Field(default=None, pattern=r"^\d{4,8}$")
    test_print: bool | None = None            # Testbestellungen drucken (als TEST gekennzeichnet)
    test_pin_required: bool | None = None     # Testmodus nur mit Test-PIN (T7; fehlt = ja, wie bisher)
    fully_password: str | None = Field(default=None, max_length=100, pattern=r"^[^\x00-\x1f\x7f]*$")  # leer = unverändert
    fully_port: int | None = Field(default=None, ge=1, le=65535)      # Fully Remote Admin (Standard 2323)
    net_updates: bool | None = None           # Seite «Internet» (a): Internet für Updates erlaubt
    net_phones: bool | None = None            # Seite «Internet» (b): Internet für die Handys beim Einrichten erlaubt
    net_keep: bool | None = None              # Seite «Internet» (c): dauerhaft verbunden (Payrexx, QR-Bestellung)
    # Quittung für den Gast (app/receipts.py): Drucker (0 = keine Quittungen), Kopf (Verein, Adresse), Fusszeile
    receipt_printer_id: int | None = Field(default=None, ge=0)
    receipt_header: str | None = Field(default=None, max_length=400, pattern=r"^[^\x00-\x09\x0b\x0c\x0e-\x1f\x7f]*$")
    receipt_footer: str | None = Field(default=None, max_length=200, pattern=r"^[^\x00-\x09\x0b\x0c\x0e-\x1f\x7f]*$")


HIDDEN_SETTINGS = {"pin_hash", "pin_salt", "pin_len", "require_pairing", "test_pin_hash", "test_pin_salt", "test_pin_len",
                   "printer_state", "closing_head", "emergency_gen",    # intern (Druckerstand, Kassenbuch, Abmelden)
                   "cert_renew_state",    # Zertifikate automatisch erneuern (T3): steht in der Geräteliste
                   "mac_change_state",    # MAC-Wechsel (T4): steht in der Geräteliste
                   "fully_password",      # Fully-Passwort: nur setzen, nie zurückgeben
                   "fully_template",      # Fully-Vorlage: eigene Route (fully.py)
                   "fully_rollouts",      # Ausrollen: Einmal-Codes für die Einstellungsdatei (mit Fully-Passwort)
                   "fully_volume_key",    # Volumenlizenz von Fully: nur setzen, nie zurückgeben
                   "fully_kiosk_pin", "fully_kiosk_pin_enc",   # Kiosk-PIN der Vorlage: nur setzen, nie zurückgeben
                   "uplink", "uplink_password",
                   "payrexx_enabled", "payrexx_method", "payrexx_show_result",
                   "gast_open", "gast_domain", "gast_max_cents", "gast_refund_min", "gast_instance", "gast_key_enc", "gast_tunnel_enc",
                   "web_site", "web_image",   # T22   # T13: eigene Route   # Mobilfunk-Router: eigene Seite (uplink_admin.py), Passwort nie zurück
                   # Zustand und Kennungen: eigene Routen (Sicherung, Prüfliste, Uhrzeit, Testmodus, Herunterfahren)
                   "system_ids", "restore_check", "receipt_no_floor", "time_confirmed", "test_exit", "power_notice",
                   "phone_ntp", "payrexx_tap_apk", "payrexx_tap_sent",
                   # Geheimnisse (Sicherungs-Passwort, Tokens, WLAN-Schlüssel …): nie über diese Route – sie ist für jede
                   # Rolle lesbar, auch «Anzeige» (Review 2026-11, S1). Wer ein neues Geheimnis einführt, trägt es in
                   # backup.SECRET_KEYS ein: dann fehlt es hier von selbst (test_settings_secrets.py).
                   *backup_mod.SECRET_KEYS}
# … und alles, was so beginnt (pro Pi, pro Gerät oder mit Geheimnis im Namen)
HIDDEN_PREFIXES = ("display_", "update_", "fully_prov_", "device_pin.", "backup_password", "node_info.")


@admin_router.get("/settings")
async def get_settings():
    rows = {r["key"]: r["value"] for r in await db(rt.store.query, "SELECT key, value FROM settings")}
    out = {k: v for k, v in rows.items() if k not in HIDDEN_SETTINGS and not k.startswith(HIDDEN_PREFIXES)}
    out["pin_set"] = bool(rows.get("pin_hash"))
    out["test_pin_set"] = bool(rows.get("test_pin_hash"))
    out["fully_password_set"] = bool(rows.get("fully_password"))
    out["languages"] = i18n.available()
    out["language"] = i18n.normalize(rows.get("language")) or i18n.SOURCE
    return out


def _pin_hash(pin: str) -> tuple[str, str]:
    salt = secrets.token_hex(8)
    return salt, hashlib.sha256((salt + pin).encode()).hexdigest()


def _monitor_devices(new: list[dict]) -> list[dict]:
    """Geräteliste speichern: Fully-Zugang, MAC, Geräte-ID behalten (fully.keep_pins); doppelte Namen unter den
    Handys bzw. unter den Netzwerkgeräten abweisen (T1)."""
    items = fully.keep_pins(new, fully._devices_raw())
    lins = device_ids.lineages(rt.store)
    seen: set[tuple[str, str]] = set()
    for d in items:                             # Handy eines gesperrten Geräts belegt seinen Namen nicht (T1, E3)
        if d.get("kind") == "phone" and not device_ids.holds_name(d, lins):
            continue
        k = (str(d.get("kind")), device_ids.norm(d.get("name")))
        if k in seen:
            raise HTTPException(409, t("err.device.name_taken", name=d.get("name")))
        seen.add(k)
    device_ids.normalize(items, lins)
    return items


@admin_router.put("/settings")
async def put_settings(s: SettingsIn, request: Request):
    values = s.model_dump(exclude_none=True)
    if not auth.has(current_user(request), auth.ADMIN_PERM) and set(values) - auth.LEAD_SETTINGS:   # sonst nur den Testmodus
        raise HTTPException(403, t("err.login.forbidden"))
    if values.get("admin_cert_only") and not await db(admin_cert_ok, request):
        raise HTTPException(409, t("err.login.cert_first"))     # nur einschalten, wer selbst mit Zertifikat kommt
    if "delivery_times" in values:            # bereinigen: "Sofort, Pause, Schluss"
        parts = []
        for part in values["delivery_times"].replace(";", ",").split(","):
            part = " ".join(part.split())[:20]
            if part and part.lower() not in (x.lower() for x in parts):
                parts.append(part)
        if not parts:
            raise HTTPException(422, t("err.settings.delivery_empty"))
        values["delivery_times"] = ", ".join(parts[:6])
    if "monitor_devices" in values:            # eingerichtete Fully-Zertifikate bleiben (gleiche ID, IP oder Name)
        values["monitor_devices"] = json.dumps(await db(_monitor_devices, values["monitor_devices"]), ensure_ascii=False)
    if not values.get("fully_password", "x"):
        values.pop("fully_password")
    if "language" in values:
        code = i18n.normalize(values["language"])
        if not code:
            raise HTTPException(422, t("err.i18n.unknown", lang=values["language"]))
        values["language"] = code
    if values.get("receipt_printer_id") and not await db(rt.store.scalar, "SELECT id FROM printers WHERE id=?",
                                                          (values["receipt_printer_id"],)):
        raise HTTPException(422, t("err.receipt.printer_unknown"))
    for k in ("receipt_header", "receipt_footer"):           # Zeilen bereinigen, leere weglassen
        if k in values:
            values[k] = "\n".join(" ".join(x.split()) for x in values[k].splitlines() if x.strip())
    if values.get("cert_renew_days"):          # erneuern muss vor der Gültigkeit liegen (sonst gleich nach dem Ausstellen)
        days = values.get("cert_days") or int(await db(rt.store.setting, "cert_days", "3650") or 3650)
        if values["cert_renew_days"] >= days:
            raise HTTPException(422, t("err.settings.renew_too_long"))
    pin = values.pop("pin", None)
    if pin:                                   # PIN nur als gesalzener Hash speichern
        values["pin_salt"], values["pin_hash"] = _pin_hash(pin)
        values["pin_len"] = len(pin)
    if values.get("pin_enabled") and not pin and not await db(rt.store.setting, "pin_hash"):
        raise HTTPException(422, t("err.settings.pin_first"))
    test_pin = values.pop("test_pin", None)
    if test_pin:
        values["test_pin_salt"], values["test_pin_hash"] = _pin_hash(test_pin)
        values["test_pin_len"] = len(test_pin)
    if {"test_enabled", "test_pin_required"} & set(values) and not test_pin:   # ein mit PIN, aber keine gesetzt
        on = values.get("test_enabled", await db(rt.store.setting, "test_enabled", "0") == "1")
        need = values.get("test_pin_required", await db(rt.store.setting, "test_pin_required", "1") != "0")
        if on and need and not await db(rt.store.setting, "test_pin_hash"):
            raise HTTPException(422, t("err.settings.test_pin_first"))
    stmts = [("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (k, ("1" if v else "0") if isinstance(v, bool) else str(v)))
             for k, v in values.items()]
    # Protokoll: welche Einstellungen; geheime Werte (PIN, Passwörter) nie mit Inhalt
    secret = {"pin_hash": "pin", "test_pin_hash": "test_pin", "fully_password": "fully_password"}
    shown = {secret.get(k, k): ("***" if k in secret else (v if len(str(v)) <= 200 else f"{str(v)[:200]}…"))
             for k, v in values.items() if k not in ("pin_len", "test_pin_len", "pin_salt", "test_pin_salt")}
    stmts.append(lb.audit_stmt(ledger.actor_of(request), "settings", **shown))
    await db(rt.store.batch, stmts)
    await db(i18n.forget_default)
    forget_cert_only()                                  # «Verwaltung nur mit Gerätezertifikat» gilt sofort
    return await get_settings()
