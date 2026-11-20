"""ES-Module einer Seite prüfen (z.B. static/admin/), was node --check nicht kann.

  python3 tools/js_module_check.py static/admin

Fehler, die sonst erst im Browser auffallen (beim Laden oder erst beim Klick):
  - importierter Name, den das andere Modul nicht exportiert (oder Datei fehlt)
  - Name aus einem anderen Modul benutzt, aber nicht importiert (ReferenceError – oft erst beim Klick,
    und guard() zeigt ihn nur als rote Meldung)
  - Zuweisung an einen importierten Namen (dort nur lesbar: dafür gibt es setXyz() im besitzenden Modul)
  - importiert, aber nicht benutzt
Der Tokenizer kennt Strings, Kommentare, Regex und Template-Literale (${…}); er ist kein vollständiger Parser,
reicht aber für den Stil dieses Projekts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

KEYWORDS = set("""break case catch class const continue debugger default delete do else export extends finally for
function if import in instanceof new return super switch this throw try typeof var void while with yield let static
async await of true false null undefined get set from as""".split())
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
NUMBER = re.compile(r"\d[\w.]*")
# nach diesen Zeichen/Wörtern beginnt ein / einen Regex, sonst ist es eine Division
REGEX_AFTER = set("( , = : [ ! & | ? { } ; + - * % < > ~ ^ return typeof case do else in of delete void throw new => "
                  "&& || ?? ...".split())
PUNCT = ("...", "===", "!==", "||=", "&&=", "??=", "=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--",
         "+=", "-=", "*=", "/=")
ASSIGN = {"=", "+=", "-=", "*=", "/=", "||=", "&&=", "??=", "++", "--"}


def tokens(src: str) -> list[tuple[str, str, int]]:
    """-> [(art, text, pos)], art: id | punct | str | num | regex | tmpl"""
    out: list[tuple[str, str, int]] = []
    i, n, depth, templates = 0, len(src), 0, []

    def template_part(j: int) -> tuple[int, bool]:          # bis ` (Ende) oder ${ (Ausdruck folgt)
        while j < n:
            if src[j] == "\\":
                j += 2
            elif src[j] == "`":
                return j + 1, False
            elif src.startswith("${", j):
                return j + 2, True
            else:
                j += 1
        raise ValueError("Template-Literal nicht geschlossen")

    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            i = src.index("*/", i) + 2
        elif c in "\"'":
            j = i + 1
            while src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(("str", src[i:j + 1], i))
            i = j + 1
        elif c == "`" or (c == "}" and templates and templates[-1] == depth - 1):
            if c == "}":
                templates.pop()
                depth -= 1
            out.append(("tmpl", c, i))
            i, expr = template_part(i + 1)
            if expr:
                templates.append(depth)
                depth += 1
        elif c == "/" and (not out or out[-1][1] in REGEX_AFTER and out[-1][0] in ("punct", "id")):
            j, in_class = i + 1, False
            while src[j] != "/" or in_class:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "\n":
                    raise ValueError(f"Regex nicht geschlossen (Zeichen {i})")
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            out.append(("regex", src[i:j], i))
            i = j
        elif m := IDENT.match(src, i):
            out.append(("id", m.group(), i))
            i = m.end()
        elif m := NUMBER.match(src, i):
            out.append(("num", m.group(), i))
            i = m.end()
        else:
            p = next((p for p in PUNCT if src.startswith(p, i)), c)
            depth += (p in "{([") - (p in "})]") if len(p) == 1 else 0
            out.append(("punct", p, i))
            i += len(p)
    return out


def group(toks, k: int) -> tuple[int, list[str]]:
    """Ab der Klammer bei k bis zur passenden: (Index danach, Bezeichner darin)."""
    d, names = 0, []
    while True:
        kind, text, _ = toks[k]
        if kind == "punct" and text in "{([":
            d += 1
        elif kind == "punct" and text in "})]":
            d -= 1
            if d == 0:
                return k + 1, names
        elif kind == "id":
            names.append(text)
        k += 1


class Module:
    def __init__(self, path: Path):
        self.path, self.src = path, path.read_text(encoding="utf-8")
        self.toks = tokens(self.src)
        self.imports: dict[str, tuple[str, int]] = {}          # Name -> (Datei, Zeile)
        self.exports: set[str] = set()
        self.bound: set[str] = set()                           # irgendwo im Modul gebunden (auch Parameter)
        self.uses: list[tuple[str, int, int]] = []             # (Name, Zeile, Token-Index)
        self._scan()

    def line(self, pos: int) -> int:
        return self.src.count("\n", 0, pos) + 1

    def _scan(self) -> None:
        t = self.toks
        skip: set[int] = set()
        k = 0
        while k < len(t):
            kind, text, pos = t[k]
            if kind == "id" and text == "import" and (k == 0 or t[k - 1][1] in (";", "}")):
                if t[k + 1][0] == "str":                            # import "./x.js"; (nur ausführen)
                    k += 2
                    continue
                end, names = group(t, k + 1)
                src = t[end + 1][1].strip("\"'")
                for nm in names:
                    self.imports[nm] = (src, self.line(pos))
                skip.update(range(k, end + 2))
                k = end + 2
                continue
            if kind == "id" and text == "export":
                nxt = 2 if t[k + 1][1] == "async" else 1
                decl, name = t[k + nxt][1], t[k + nxt + 1]
                if decl in ("function", "class", "const", "let") and name[0] == "id":
                    self.exports.add(name[1])
                    if decl in ("const", "let"):                 # export let a = 1, b = 2;
                        j, d = k + nxt + 2, 0
                        while not (d == 0 and t[j][1] == ";"):
                            if t[j][0] == "punct" and t[j][1] in "{([":
                                d += 1
                            elif t[j][0] == "punct" and t[j][1] in "})]":
                                d -= 1
                            elif d == 0 and t[j][1] == "," and t[j + 1][0] == "id":
                                self.exports.add(t[j + 1][1])
                            j += 1
            if kind == "id" and text in ("const", "let", "var", "function", "class", "catch"):
                j = k + 1
                if t[j][1] == "(" or t[j][1] in "[{":                # catch (e), const [a, b] / { a, b }
                    _, names = group(t, j)
                    self.bound.update(names)
                elif t[j][0] == "id":
                    self.bound.add(t[j][1])
                    if text in ("const", "let", "var"):              # let a = 1, b = 2
                        jj, d = j + 1, 0
                        while jj < len(t) and not (d == 0 and t[jj][1] in (";", ")")):
                            if t[jj][0] == "punct" and t[jj][1] in "{([":
                                d += 1
                            elif t[jj][0] == "punct" and t[jj][1] in "})]":
                                d -= 1
                            elif d == 0 and t[jj][1] == "," and t[jj + 1][0] == "id":
                                self.bound.add(t[jj + 1][1])
                            jj += 1
                if text == "function":                               # Parameter
                    j = k + 1 + (t[k + 1][0] == "id")
                    if t[j][1] == "(":
                        self.bound.update(group(t, j)[1])
            if kind == "punct" and text == "=>":                     # Parameter einer Pfeilfunktion
                p = t[k - 1]
                if p[0] == "id":
                    self.bound.add(p[1])
                elif p[1] == ")":
                    j, d = k - 1, 0
                    while True:
                        if t[j][1] == ")":
                            d += 1
                        elif t[j][1] == "(":
                            d -= 1
                            if d == 0:
                                break
                        j -= 1
                    self.bound.update(group(t, j)[1])
            k += 1
        for k, (kind, text, pos) in enumerate(t):
            if kind != "id" or text in KEYWORDS or k in skip:
                continue
            prev = t[k - 1] if k else ("", "", 0)
            nxt = t[k + 1] if k + 1 < len(t) else ("", "", 0)
            if prev[0] == "punct" and prev[1] in (".", "?."):        # Eigenschaft
                continue
            if nxt[1] == ":" and prev[1] in ("{", ","):              # Schlüssel in { a: … }
                continue
            self.uses.append((text, self.line(pos), k))


def check(folder: Path) -> list[str]:
    mods = {p.name: Module(p) for p in sorted(folder.glob("*.js"))}
    owner: dict[str, str] = {}
    for name, m in mods.items():
        for e in m.exports:
            owner.setdefault(e, name)
    errors = []
    for name, m in mods.items():
        where = f"{folder}/{name}"
        for nm, (src, ln) in m.imports.items():
            target = mods.get(src.removeprefix("./"))
            if target is None:
                errors.append(f"{where}:{ln}: Import aus {src}: Datei fehlt")
            elif nm not in target.exports:
                errors.append(f"{where}:{ln}: {nm} wird von {src} nicht exportiert")
        used = set()
        for nm, ln, k in m.uses:
            used.add(nm)
            if nm in m.imports:
                nxt = m.toks[k + 1][1] if k + 1 < len(m.toks) else ""
                prv = m.toks[k - 1][1] if k else ""
                if nxt in ASSIGN or prv in ("++", "--"):
                    errors.append(f"{where}:{ln}: Zuweisung an den importierten Namen {nm} – im Modul {m.imports[nm][0]} "
                                  f"einen Setter verwenden")
            elif nm in owner and owner[nm] != name and nm not in m.bound and nm not in m.exports:
                errors.append(f"{where}:{ln}: {nm} stammt aus {owner[nm]}, ist aber nicht importiert")
        for nm, (_src, ln) in m.imports.items():
            if nm not in used:
                errors.append(f"{where}:{ln}: {nm} importiert, aber nicht benutzt")
    return sorted(set(errors))


def main() -> int:
    errors = []
    for arg in sys.argv[1:] or ["static/admin"]:
        errors += check(Path(arg))
    for e in errors:
        print("FEHLER:", e)
    if not errors:
        print("ES-Module: alles in Ordnung")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
