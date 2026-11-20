"""Webseite & Rechtliches zur QR-Bestellung: Einstellungen nur für Admins, eigenes CSS wird streng geprüft
(nichts von fremden Servern, kein Ausbruch aus dem Stil), Seiten sind escaped und ohne Skript, Verweise auf eine andere
Webseite, AGB vor dem Bezahlen, Gästedienst leitet die neuen Adressen weiter, ZIP für Cloudflare Pages mit _headers."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from tests.test_api import AUTH, setup_shop
from tests.test_gast import G, _sig, gast  # noqa: F401  (gast = Fixture)
from tests.test_gast_proxy import proxy  # noqa: F401  (Fixture)

FULL = {"org_name": "Verein Muster", "org_address": "Musterstrasse 1\n6000 Luzern", "org_email": "fest@example.ch",
        "org_person": "Maria Muster", "intro": "Theater & Bar <b>", "when": "12.–25. März", "where": "Pfarreisaal",
        "terms_text": "# 1. Geltung\nGilt für alles.\n\n- Punkt <script>", "privacy_mode": "link",
        "privacy_url": "https://www.example.ch/datenschutz", "refund_text": "Automatisch zurück."}


@pytest.mark.parametrize("css", [
    ".w-hero { background: url(https://evil.example/x.png) }",
    ".w-hero { background: url(//evil.example/x.png) }",
    "@import 'https://evil.example/a.css';",
    "@IMPORT url(a.css);",
    '@font-face { font-family: x; src: url("/media/abcdef12") }',
    ".a { background: image-set('https://evil.example/x.png' 1x) }",
    ".a { background: u\\72l(https://evil.example) }",
    "</style><script>alert(1)</script>",
    ".a { background: url(data:image/png;base64,AAAA) }",
    ".a { background: url(javascript:alert(1)) }",
    "input[value^=a] { background: url(/api/gast/menu?leak=a) }",
    ".a { color: red } /* offen",
    ".a { -moz-binding: x }",
    ".a { color: red }\n" * 1500,
])
def test_css_check_refuses_anything_that_loads_or_escapes(client, css):
    from app import web
    assert web.check_css(css), css
    r = client.put("/api/admin/web", auth=AUTH, json={"css": css})
    assert r.status_code == 422
    assert "url(" not in client.get("/api/admin/web", auth=AUTH).json()["css"]


def test_css_check_allows_normal_styling(client):
    from app import web
    css = """/* Theater */
:root { --accent: #7a1020; }
.w-hero h1 { font-family: Georgia, serif; color: #f3c969; }
@media (prefers-color-scheme: dark) { .w { background: #111 } }
@keyframes glow { from { opacity: .5 } to { opacity: 1 } }
.w-pic { background: url("/media/abcdef123456") center / cover; }
.w-hero { background: linear-gradient(160deg, #7a1020, #3d0610); }"""
    assert web.check_css(css) == []
    r = client.put("/api/admin/web", auth=AUTH, json={"css": css, "accent": "#7a1020"})
    assert r.status_code == 200, r.text
    out = web.custom_css()
    assert out.startswith(":root { --accent: #7a1020; --accent-ink: #ffffff; }") and "Theater" not in out   # ohne Kommentar
    assert "Georgia" in out


def test_settings_admin_only_complete_and_hidden(client):
    from app.core import rt
    assert client.get("/api/admin/web").status_code == 401
    v = client.get("/api/admin/web", auth=AUTH).json()
    assert set(v["missing"]) == {"org_name", "org_address", "org_email", "terms", "privacy", "refund"}
    assert client.put("/api/admin/web", auth=AUTH, json={"org_url": "javascript:alert(1)"}).status_code == 422
    assert client.put("/api/admin/web", auth=AUTH, json={"privacy_url": "http://unsicher.example"}).status_code == 422
    v = client.put("/api/admin/web", auth=AUTH, json=FULL).json()
    assert v["missing"] == []
    assert "web_site" not in client.get("/api/admin/settings", auth=AUTH).json()
    audit = json.dumps(rt.store.query("SELECT * FROM audit_log WHERE action='web_settings'"), default=str)
    assert "org_name" in audit and "Musterstrasse" not in audit                   # nur welche Felder, nicht die Texte
    t = client.get("/api/admin/web/template/terms", auth=AUTH).json()["text"]
    assert "Verein Muster" in t and "fest@example.ch" in t and "{" not in t


def test_pages_are_escaped_without_scripts(client):
    from app import web
    setup_shop(client)
    client.put("/api/admin/web", auth=AUTH, json=FULL)
    start = web.render("start", web.Ctx("live"))
    assert "Theater &amp; Bar &lt;b&gt;" in start and "<script" not in start and "style=" not in start
    assert "Speisekarte" in start and "CHF" in start and '<div class="w-item">' in start
    assert 'href="/impressum"' in start and 'href="https://www.example.ch/datenschutz"' in start   # Verweis direkt
    assert 'href="/custom.css"' in start and 'href="/web.css"' in start
    terms = web.render("terms", web.Ctx("live", "fr"))
    assert "<h3>1. Geltung</h3>" in terms and "<li>Punkt &lt;script&gt;</li>" in terms and 'lang="fr"' in terms
    imp = web.render("imprint", web.Ctx("live"))
    assert "Maria Muster" in imp and "Musterstrasse 1<br>6000 Luzern" in imp and "Payrexx" in imp
    priv = web.render("privacy", web.Ctx("live"))
    assert "https://www.example.ch/datenschutz" in priv
    prev = client.get("/api/admin/web/preview/start", auth=AUTH).json()["html"]
    assert "<style>:root { --accent:" in prev and 'href="#imprint"' in prev


def test_guest_routes_and_terms_before_paying(client, gast):  # noqa: F811
    assert client.get("/api/gast/web/start").status_code == 403                          # nur über den Gästedienst
    r = client.get("/api/gast/web/start", headers=G)
    assert r.status_code == 200 and "script-src 'none'" in r.headers["content-security-policy"]
    assert client.get("/api/gast/web/custom.css", headers=G).headers["content-type"].startswith("text/css")
    assert client.get("/api/gast/web/nix", headers=G).status_code == 404
    m = client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}, headers=G).json()
    assert m["terms"] is False and m["legal"]["imprint"] == "/impressum" and m["legal"]["terms"] is None
    client.put("/api/admin/web", auth=AUTH, json=FULL)
    m = client.get("/api/gast/menu", params={"t": "7", "s": _sig("7")}, headers=G).json()
    assert m["terms"] is True and m["legal"]["terms"] == "/agb" and m["legal"]["privacy"].startswith("https://")
    pid = m["products"][0]["id"]
    body = {"t": "7", "s": _sig("7"), "key": "key-terms-01", "lines": [{"product_id": pid, "qty": 1}]}
    assert client.post("/api/gast/order", headers=G, json=body).status_code == 422          # AGB nicht akzeptiert
    assert client.post("/api/gast/order", headers=G, json={**body, "terms": True}).status_code == 200


def test_export_zip_for_cloudflare_pages(client):
    setup_shop(client)
    client.put("/api/admin/web", auth=AUTH, json={**FULL, "css": ".w-hero h1 { color: gold }"})
    r = client.get("/api/admin/web/export.zip", auth=AUTH)
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"index.html", "impressum.html", "agb.html", "datenschutz.html", "rueckerstattung.html", "app.css", "web.css",
            "custom.css", "_headers", "fonts/barlow-condensed-latin-700-normal.woff2"} <= names
    h = z.read("_headers").decode()
    assert "Content-Security-Policy: default-src 'self'; script-src 'none'" in h
    idx = z.read("index.html").decode()
    assert 'href="/impressum"' in idx and "?l=" not in idx and "color: gold" in z.read("custom.css").decode()


def test_proxy_serves_website_paths(proxy):  # noqa: F811
    c, up = proxy
    for path, want in (("/", "/api/gast/web/start"), ("/?l=fr", "/api/gast/web/start?l=fr"),
                       ("/impressum", "/api/gast/web/imprint"), ("/agb?l=it&x=1", "/api/gast/web/terms?l=it"),
                       ("/datenschutz", "/api/gast/web/privacy"), ("/rueckerstattung", "/api/gast/web/refund"),
                       ("/custom.css", "/api/gast/web/custom.css"), ("/?l=../../x", "/api/gast/web/start")):
        assert c.get(path).status_code == 200
        assert up.seen[-1]["path"] == want, path
    r = c.get("/?t=7&s=abcdefgh")                                             # mit QR-Code: Bestellseite wie bisher
    assert b"/gast.js" in r.content
    assert c.get("/web.css").status_code == 200
    assert c.get("/api/gast/web/start").status_code == 404                     # nur über die öffentlichen Adressen
