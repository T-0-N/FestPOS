"""Webseite & Rechtliches zur QR-Bestellung (Weg B).

Ohne QR-Code zeigt die Gästeseite eine Startseite (Fest, Ablauf, Zahlarten, Speisekarte mit Preisen, Kontakt) und die
Rechtsseiten Impressum, AGB, Datenschutz und Rückerstattung. Payrexx verlangt sie für die Anmeldung. Jede Rechtsseite
kommt entweder aus FestPOS (Text mit Vorlage; das Impressum aus den Angaben zum Veranstalter) oder verweist auf eine
andere Webseite, z.B. die des Vereins. Dieselben Seiten gibt es als ZIP für Cloudflare Pages (das ganze Jahr online).

Eigenes CSS (nur Admin, im Protokoll) ist sicher, weil:
- der Server es beim Speichern prüft (check_css) und ablehnt, was fremde Inhalte laden oder Daten abfliessen lassen
  könnte: @import und andere At-Regeln ausser @media/@supports/@keyframes, url() ausser /media/<Bild> aus FestPOS,
  «//», Escapes «\\», «<», Steuerzeichen, image-set() und ähnliche Funktionen, mehr als 20 KB;
- es nur als eigene Datei custom.css ausgeliefert wird (Kommentare entfernt), nie in eine Seite kopiert;
- die Seiten per Content-Security-Policy nichts von fremden Servern laden dürfen (auch im ZIP: Datei _headers);
- es nur auf der Startseite und den Rechtsseiten gilt – Speisekarte, Warenkorb, AGB-Kästchen und Bezahlen
  (static/gast/index.html) binden es nicht ein.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import ledger
from . import ledger_base as lb
from .core import Config, admin, db, rt
from .i18n import normalize, t
from .store import now_iso

SETTING = "web_site"
IMAGE = "web_image"
CSS_MAX = 20 * 1024
KINDS = ("imprint", "terms", "privacy", "refund")
SLUG = {"start": "", "imprint": "impressum", "terms": "agb", "privacy": "datenschutz", "refund": "rueckerstattung"}
TITLE = {"imprint": "srv.web.imprint", "terms": "srv.web.terms", "privacy": "srv.web.privacy", "refund": "srv.web.refund"}
TEMPLATE = {"terms": "srv.web.tpl_terms", "privacy": "srv.web.tpl_privacy", "refund": "srv.web.tpl_refund"}
DEFAULT_ACCENT = "#019724"
# Strenger als die App: die Webseite braucht keine Skripte
CSP = ("default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")

TEXT = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"            # Text ohne Steuerzeichen (Zeilenumbrüche erlaubt)
LINE = r"^[^\x00-\x1f\x7f]*$"                              # eine Zeile
URL = r"^(https://[^\s<>\"'\\]{4,300})?$"
MAIL = r"^([^\s<>\"'@]{1,64}@[^\s<>\"'@]{3,190})?$"


class WebIn(BaseModel):
    """Alle Felder optional: nur, was mitkommt, wird geändert (Speichern-Balken der Seite Kasse & App)."""
    org_name: str | None = Field(default=None, max_length=120, pattern=LINE)
    org_address: str | None = Field(default=None, max_length=300, pattern=TEXT)
    org_email: str | None = Field(default=None, max_length=200, pattern=MAIL)
    org_phone: str | None = Field(default=None, max_length=40, pattern=LINE)
    org_person: str | None = Field(default=None, max_length=120, pattern=LINE)
    org_vat: str | None = Field(default=None, max_length=40, pattern=LINE)
    org_url: str | None = Field(default=None, max_length=300, pattern=URL)
    intro: str | None = Field(default=None, max_length=600, pattern=TEXT)
    when: str | None = Field(default=None, max_length=80, pattern=LINE)
    where: str | None = Field(default=None, max_length=120, pattern=LINE)
    show_menu: bool | None = None
    accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    imprint_mode: str | None = Field(default=None, pattern="^(text|link)$")
    imprint_url: str | None = Field(default=None, max_length=300, pattern=URL)
    imprint_text: str | None = Field(default=None, max_length=4000, pattern=TEXT)
    terms_mode: str | None = Field(default=None, pattern="^(text|link)$")
    terms_url: str | None = Field(default=None, max_length=300, pattern=URL)
    terms_text: str | None = Field(default=None, max_length=20000, pattern=TEXT)
    privacy_mode: str | None = Field(default=None, pattern="^(text|link)$")
    privacy_url: str | None = Field(default=None, max_length=300, pattern=URL)
    privacy_text: str | None = Field(default=None, max_length=20000, pattern=TEXT)
    refund_mode: str | None = Field(default=None, pattern="^(text|link)$")
    refund_url: str | None = Field(default=None, max_length=300, pattern=URL)
    refund_text: str | None = Field(default=None, max_length=20000, pattern=TEXT)
    css: str | None = Field(default=None, max_length=CSS_MAX * 2)


DEFAULTS: dict = {k: "" for k in WebIn.model_fields} | {
    "show_menu": True, "accent": DEFAULT_ACCENT,
    **{f"{k}_mode": "text" for k in KINDS}}


def site() -> dict:
    try:
        raw = json.loads(rt.store.setting(SETTING, "") or "{}")
    except ValueError:
        raw = {}
    return {k: raw.get(k, d) if isinstance(raw.get(k, d), type(d)) else d for k, d in DEFAULTS.items()}


# -- Eigenes CSS prüfen ------------------------------------------------------------------------------------------
AT_OK = {"media", "supports", "keyframes", "-webkit-keyframes", "container", "layer"}
FN_BAD = {"image-set", "-webkit-image-set", "src", "image", "element", "-moz-element", "paint", "expression",
          "cross-fade", "-webkit-cross-fade"}
MEDIA_URL = re.compile(r"""url\(\s*(["']?)/media/[a-z0-9_-]{6,80}\1\s*\)""")
WORD_BAD = ("javascript:", "vbscript:", "behavior", "-moz-binding", "data:")


def _strip_comments(css: str) -> tuple[str, bool]:
    """Kommentare entfernen (Zeilenumbrüche bleiben, damit die Zeilennummern stimmen) -> (Text, alle geschlossen)."""
    out = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), css, flags=re.S)
    return out, "/*" not in out


def check_css(css: str) -> list[str]:
    """Gründe, warum das CSS abgelehnt wird (leer: in Ordnung). Lieber zu streng: der Browser verzeiht nichts weg."""
    errs: list[str] = []
    if len(css.encode()) > CSS_MAX:
        return [t("err.web.css_size", kb=CSS_MAX // 1024)]
    clean, closed = _strip_comments(css)
    if not closed:
        errs.append(t("err.web.css_comment"))
    for no, line in enumerate(clean.split("\n"), 1):
        low = line.lower()
        why = None
        if any(ord(ch) < 32 and ch != "\t" for ch in line) or "\x7f" in line:
            why = t("err.web.css_control")
        elif "<" in line:
            why = t("err.web.css_lt")
        elif "\\" in line:
            why = t("err.web.css_escape")
        elif "//" in line:
            why = t("err.web.css_external")
        elif any(w in low for w in WORD_BAD):
            why = t("err.web.css_word")
        else:
            for m in re.finditer(r"(?:^|[\s;{}])@([a-z-]+)", low):
                if m.group(1) not in AT_OK:
                    why = t("err.web.css_at", rule="@" + m.group(1))
                    break
            if not why:
                for m in re.finditer(r"([a-z-]+)\s*\(", low):
                    fn = m.group(1)
                    if fn == "url" and not MEDIA_URL.match(low, m.start()):
                        why = t("err.web.css_url")
                    elif fn in FN_BAD:
                        why = t("err.web.css_fn", fn=fn + "()")
                    if why:
                        break
        if why:
            errs.append(t("err.web.css_line", line=no, why=why))
        if len(errs) >= 5:
            break
    return errs


def custom_css(s: dict | None = None) -> str:
    """custom.css: Hauptfarbe als Variable und das geprüfte eigene CSS ohne Kommentare."""
    s = s or site()
    accent = s["accent"] if re.fullmatch(r"#[0-9a-fA-F]{6}", s["accent"]) else DEFAULT_ACCENT
    r, g, b = (int(accent[i:i + 2], 16) for i in (1, 3, 5))
    ink = "#000000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 150 else "#ffffff"
    out = f":root {{ --accent: {accent}; --accent-ink: {ink}; }}\n"
    css = s["css"]
    if css and not check_css(css):                     # nie Ungeprüftes ausliefern (z.B. von Hand in der Datenbank)
        out += _strip_comments(css)[0].strip() + "\n"
    return out


# -- Vollständig? ----------------------------------------------------------------------------------------------
def missing(s: dict | None = None) -> list[str]:
    """Was für Payrexx noch fehlt: Feldnamen (org_name …) bzw. Seiten (terms …)."""
    s = s or site()
    out = [k for k in ("org_name", "org_address", "org_email") if not s[k].strip()]
    for k in KINDS:
        if s[f"{k}_mode"] == "link":
            if not s[f"{k}_url"]:
                out.append(k)
        elif k != "imprint" and not s[f"{k}_text"].strip():
            out.append(k)
    return out


def terms_required() -> bool:
    """Gibt es AGB (Text oder Verweis)? Dann muss der Gast sie vor dem Bezahlen akzeptieren."""
    s = site()
    return bool(s["terms_url"] if s["terms_mode"] == "link" else s["terms_text"].strip())


def legal_links(lang: str = "de") -> dict:
    """Für die Bestellseite (Warenkorb): Adresse jeder Rechtsseite (eigene Seite oder Verweis), None = keine."""
    s = site()
    q = "" if lang in ("de", "") else f"?l={lang}"
    out = {}
    for k in KINDS:
        if s[f"{k}_mode"] == "link":
            out[k] = s[f"{k}_url"] or None
        elif k == "imprint" or s[f"{k}_text"].strip():
            out[k] = f"/{SLUG[k]}{q}"
        else:
            out[k] = None
    return out


# -- Seiten bauen -------------------------------------------------------------------------------------------------
class Ctx:
    """Wo die Seite läuft: live (Gästedienst), Vorschau (Verwaltung) oder Export (Cloudflare Pages)."""

    def __init__(self, mode: str, lang: str = "de"):
        self.mode, self.lang = mode, lang

    def css(self, name: str) -> str:
        if name == "web.css":
            return "/gast/web.css" if self.mode == "preview" else "/web.css"
        return "/" + name

    def page(self, kind: str) -> str:
        if self.mode == "preview":
            return "#" + kind                        # Vorschau in der Verwaltung: Seiten oben umschalten
        if self.mode == "export":
            return "/" + (SLUG[kind] or "")
        q = "" if self.lang == "de" else f"?l={self.lang}"
        return "/" + SLUG[kind] + q

    def logo(self, ext: str) -> str:
        return f"/logo.{ext}" if self.mode == "export" else "/api/logo"

    def media(self, iid: str, ext: str) -> str:
        return f"/media/{iid}.{ext}" if self.mode == "export" else f"/media/{iid}"


def _e(s: str) -> str:
    return html.escape(s or "", quote=True)


def _rich(text: str) -> str:
    """Einfache Auszeichnung aus der Verwaltung: «# Titel», «- Punkt», Leerzeile = neuer Absatz. Alles escaped."""
    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
            items.clear()
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        para = []
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                flush()
                if para:
                    out.append(f"<p>{'<br>'.join(para)}</p>")
                    para = []
                out.append(f"<h3>{_e(line[2:])}</h3>")
            elif line.startswith("- "):
                if para:
                    out.append(f"<p>{'<br>'.join(para)}</p>")
                    para = []
                items.append(_e(line[2:]))
            elif line:
                flush()
                para.append(_e(line))
        flush()
        if para:
            out.append(f"<p>{'<br>'.join(para)}</p>")
    return "\n".join(out)


def _ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")


def _images() -> dict:
    """Logo und Bild oben: {"logo": ext | None, "image": (id, ext) | None}."""
    out: dict = {"logo": None, "image": None}
    for key, name in (("logo_image", "logo"), (IMAGE, "image")):
        iid = rt.store.setting(key, "") or ""
        row = rt.store.query_one("SELECT mime FROM images WHERE id=?", (iid,)) if iid else None
        if row:
            out[name] = _ext(row["mime"]) if name == "logo" else (iid, _ext(row["mime"]))
    return out


def _menu_html(lang: str) -> str:
    cats = rt.store.query("SELECT id, name FROM categories ORDER BY sort, name")
    prods = rt.store.query("SELECT name, price_cents, category_id FROM products WHERE active=1 ORDER BY sort, name")
    parts = []
    for c in [*cats, {"id": None, "name": ""}]:
        ps = [p for p in prods if p["category_id"] == c["id"]]
        if not ps:
            continue
        if c["name"]:
            parts.append(f'<div class="w-cat">{_e(c["name"])}</div>')
        parts += [f'<div class="w-item"><span>{_e(p["name"])}</span><b>{p["price_cents"] / 100:.2f}</b></div>' for p in ps]
    if not parts:
        return ""
    return (f'<section class="w-sec"><h2>{_e(t("srv.web.menu", lang))}</h2>{"".join(parts)}'
            f'<p class="w-note">{_e(t("srv.web.menu_note", lang))}</p></section>')


def _contact(s: dict, lang: str) -> str:
    lines = [f"<b>{_e(s['org_name'])}</b>"] if s["org_name"] else []
    lines += [f"<span>{_e(x.strip())}</span>" for x in s["org_address"].split("\n") if x.strip()]
    if s["org_email"]:
        lines.append(f'<a href="mailto:{_e(s["org_email"])}">{_e(s["org_email"])}</a>')
    if s["org_phone"]:
        lines.append(f'<a href="tel:{_e(re.sub(r"[^0-9+]", "", s["org_phone"]))}">{_e(s["org_phone"])}</a>')
    if s["org_url"]:
        lines.append(f'<a href="{_e(s["org_url"])}" rel="noopener">{_e(re.sub(r"^https://", "", s["org_url"]))}</a>')
    return f'<div class="w-contact">{"".join(lines)}</div>' if lines else ""


def _imprint(s: dict, lang: str) -> str:
    sec = []

    def add(title_key: str, body: str):
        if body:
            sec.append(f"<h3>{_e(t(title_key, lang))}</h3><p>{body}</p>")
    add("srv.web.imp_org", "<br>".join(_e(x) for x in [s["org_name"], *s["org_address"].split("\n")] if x.strip()))
    add("srv.web.imp_contact", "<br>".join(x for x in (
        f'<a href="mailto:{_e(s["org_email"])}">{_e(s["org_email"])}</a>' if s["org_email"] else "",
        _e(s["org_phone"])) if x))
    add("srv.web.imp_person", _e(s["org_person"]))
    add("srv.web.imp_vat", _e(s["org_vat"]) if s["org_vat"] else _e(t("srv.web.imp_no_vat", lang)))
    add("srv.web.imp_pay", _e(t("srv.web.imp_payrexx", lang)))
    return "".join(sec) + _rich(s["imprint_text"])


def render(kind: str, ctx: Ctx) -> str:
    """Eine Seite als HTML (alles escaped, kein Skript, kein Inline-Stil)."""
    s, lang = site(), ctx.lang
    img = _images()
    event = rt.store.setting("event_name", "") or s["org_name"] or t("srv.web.title", lang)
    links = []
    for k in KINDS:
        if s[f"{k}_mode"] == "link" and s[f"{k}_url"]:
            links.append(f'<a class="ext" href="{_e(s[f"{k}_url"])}" rel="noopener">{_e(t(TITLE[k], lang))}</a>')
        elif s[f"{k}_mode"] == "text" and (k == "imprint" or s[f"{k}_text"].strip()):
            links.append(f'<a href="{_e(ctx.page(k))}">{_e(t(TITLE[k], lang))}</a>')
    if s["org_url"]:
        links.append(f'<a class="ext" href="{_e(s["org_url"])}" rel="noopener">{_e(re.sub(r"^https://", "", s["org_url"]))}</a>')
    langs = ""
    if ctx.mode == "live":
        items = []
        for c in ("de", "fr", "it", "en", "rm-vallader"):
            href = "/" + SLUG[kind] + ("" if c == "de" else "?l=" + c)
            cur = ' aria-current="page"' if c == lang else ""
            items.append(f'<a href="{href}" lang="{c[:2]}"{cur}>{c[:2].upper()}</a>')
        langs = '<nav class="w-langs">' + "".join(items) + "</nav>"
    logo = f'<img class="w-logo" src="{_e(ctx.logo(img["logo"]))}" alt="">' if img["logo"] else ""
    pic = f'<img class="w-pic" src="{_e(ctx.media(*img["image"]))}" alt="">' if img["image"] else ""
    if kind == "start":
        facts = "".join(f"<span>{_e(x)}</span>" for x in (s["when"], s["where"]) if x)
        steps = "".join(f"<div><b>{i}</b>{_e(t(k, lang))}</div>" for i, k in
                        enumerate(("srv.web.step_scan", "srv.web.step_pay", "srv.web.step_bring"), 1))
        pays = "".join(f"<span>{_e(x.strip())}</span>" for x in t("srv.web.pay_methods", lang).split("·"))
        kick = f'<div class="w-kick">{_e(s["org_name"])}</div>' if s["org_name"] and s["org_name"] != event else ""
        intro = f'<p>{_e(s["intro"])}</p>' if s["intro"] else ""
        when = f'<div class="w-when">{facts}</div>' if facts else ""
        hero = f'<header class="w-hero">{pic}<div class="w-in">{logo}{kick}<h1>{_e(event)}</h1>{intro}{when}</div></header>'
        body = (f'<section class="w-sec"><h2>{_e(t("srv.web.how", lang))}</h2><div class="w-steps">{steps}</div>'
                f'<div class="w-pay">{pays}</div></section>'
                + (_menu_html(lang) if s["show_menu"] else "")
                + (f'<section class="w-sec"><h2>{_e(t("srv.web.contact", lang))}</h2>{_contact(s, lang)}</section>'
                   if s["org_name"] or s["org_email"] else ""))
        title = event
    else:
        title = t(TITLE[kind], lang)
        if s[f"{kind}_mode"] == "link" and s[f"{kind}_url"]:
            inner = (f"<p>{_e(t('srv.web.link_note', lang, page=title))}</p>"
                     f'<p><a href="{_e(s[f"{kind}_url"])}" rel="noopener">{_e(s[f"{kind}_url"])}</a></p>')
        elif kind == "imprint":
            inner = _imprint(s, lang)
        else:
            inner = _rich(s[f"{kind}_text"]) or f"<p>{_e(t('srv.web.empty', lang))}</p>"
        hero = (f'<header class="w-hero w-small"><div class="w-in">{logo}'
                f'<a class="w-home" href="{_e(ctx.page("start"))}">‹ {_e(event)}</a><h1>{_e(title)}</h1></div></header>')
        body = f'<section class="w-sec w-doc">{inner}</section>'
        title = f"{title} – {event}"
    foot = (f'<footer class="w-foot"><nav>{"".join(links)}</nav>{langs}'
            f'<small>{_e(t("srv.web.made", lang))}</small></footer>')
    return (f'<!doctype html>\n<html lang="{_e(lang[:2])}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{_e(title)}</title>'
            + "".join(f'<link rel="stylesheet" href="{_e(ctx.css(n))}">' for n in ("app.css", "web.css"))
            # Vorschau (Verwaltung, iframe ohne Skripte): custom.css direkt – geprüft, ohne «<», kann den Stil nicht verlassen
            + (f"<style>{custom_css()}</style>" if ctx.mode == "preview" else f'<link rel="stylesheet" href="{_e(ctx.css("custom.css"))}">')
            + f'</head>\n<body class="web"><div class="w">{hero}<main>{body}</main>{foot}</div></body></html>\n')


def export_zip() -> bytes:
    """Alle Seiten auf Deutsch als einfache Webseite für Cloudflare Pages (mit _headers für die CSP)."""
    ctx = Ctx("export")
    buf = io.BytesIO()
    static = Path(Config.static)
    if not (static / "app.css").is_file():                  # Entwicklung/Tests ohne eigenen static-Ordner
        static = Path(__file__).resolve().parent.parent / "static"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for kind, slug in SLUG.items():
            z.writestr(f"{slug or 'index'}.html", render(kind, ctx))
        z.writestr("custom.css", custom_css())
        for name, src in (("app.css", "app.css"), ("web.css", "gast/web.css")):
            z.write(static / src, name)
        for f in sorted((static / "fonts").glob("*.woff2")):
            z.write(f, f"fonts/{f.name}")
        import base64
        for key, name in (("logo_image", "logo"), (IMAGE, "media")):
            iid = rt.store.setting(key, "") or ""
            row = rt.store.query_one("SELECT mime, data FROM images WHERE id=?", (iid,)) if iid else None
            if row:
                z.writestr(f"logo.{_ext(row['mime'])}" if name == "logo" else f"media/{iid}.{_ext(row['mime'])}",
                           base64.b64decode(row["data"]))
        z.writestr("_headers", "/*\n" + "".join(f"  {k}: {v}\n" for k, v in (
            ("Content-Security-Policy", CSP), ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"))))
    return buf.getvalue()


def _html(text: str) -> Response:
    return Response(text, media_type="text/html; charset=utf-8",
                    headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store"})


# -- Gäste (nur über den Gästedienst) --------------------------------------------------------------------------
def _gast_only(request: Request) -> None:
    from .gast import gast_only
    gast_only(request)


router = APIRouter(prefix="/api/gast/web", dependencies=[Depends(_gast_only)])


@router.get("/custom.css")
async def gast_css():
    return Response(await db(custom_css), media_type="text/css; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.get("/{kind}")
async def gast_page(kind: str, l: str = Query("de", max_length=12)):  # noqa: E741 – kurz in der Adresse
    if kind not in SLUG:
        raise HTTPException(404)
    return _html(await db(render, kind, Ctx("live", normalize(l) or "de")))


# -- Verwaltung ---------------------------------------------------------------------------------------------------
admin_router = APIRouter(prefix="/api/admin/web", dependencies=[Depends(admin)])


def admin_view() -> dict:
    s = site()
    return {**s, "image": bool(rt.store.setting(IMAGE, "")), "missing": missing(s),
            "domain": (rt.store.setting("gast_domain", "") or "").strip()}


@admin_router.get("")
async def get_site():
    return await db(admin_view)


@admin_router.put("")
async def put_site(b: WebIn, request: Request):
    new = b.model_dump(exclude_none=True)
    if "css" in new:
        errs = check_css(new["css"])
        if errs:
            raise HTTPException(422, " ".join(errs))
    for k in [k for k in new if k.endswith("_text") or k in ("org_address", "intro", "css")]:
        new[k] = new[k].replace("\r\n", "\n").strip()

    def save():
        s = site()
        changed = sorted(k for k, v in new.items() if s.get(k) != v)
        s.update(new)
        rt.store.batch([("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (SETTING, json.dumps(s, ensure_ascii=False))),
                        lb.audit_stmt(ledger.actor_of(request), "web_settings", changed=changed)])
        return admin_view()
    return await db(save)


@admin_router.get("/template/{kind}")
async def template(kind: str, lang: str = Query("de", max_length=12), name: str = Query("", max_length=120),
                   email: str = Query("", max_length=200)):
    if kind not in TEMPLATE:
        raise HTTPException(404)

    def build():
        s = site()
        return {"text": t(TEMPLATE[kind], normalize(lang) or "de",
                          name=name.strip() or s["org_name"] or "…", event=rt.store.setting("event_name", "") or "…",
                          email=email.strip() or s["org_email"] or "…", date=now_iso()[8:10] + "." + now_iso()[5:7] + "." + now_iso()[:4])}
    return await db(build)


@admin_router.get("/preview/{kind}")
async def preview(kind: str):
    """Für die Vorschau in der Verwaltung (iframe srcdoc, sandbox): die Seite als Text."""
    if kind not in SLUG:
        raise HTTPException(404)
    return {"html": await db(render, kind, Ctx("preview"))}


@admin_router.get("/export.zip")
async def export():
    data = await db(export_zip)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="festpos-webseite.zip"', "Cache-Control": "no-store"})


@admin_router.post("/image")
async def upload_image(file: UploadFile = File(...)):
    from .catalog import DROP_UNUSED_IMAGES, MAX_UPLOAD, process_image
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, t("err.file.too_large"))
    iid, mime, b64 = await asyncio.to_thread(process_image, raw, (1600, 900))
    await db(rt.store.batch, [("INSERT OR IGNORE INTO images(id,mime,data) VALUES (?,?,?)", (iid, mime, b64)),
                              ("INSERT INTO settings(key,value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (IMAGE, iid)), DROP_UNUSED_IMAGES])
    return await db(admin_view)


@admin_router.delete("/image")
async def delete_image():
    from .catalog import DROP_UNUSED_IMAGES
    await db(rt.store.batch, [("DELETE FROM settings WHERE key=?", (IMAGE,)), DROP_UNUSED_IMAGES])
    return await db(admin_view)
