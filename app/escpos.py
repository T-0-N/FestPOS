"""ESC/POS für Epson TM-T20 / TM-T88 – Byte-Builder und Raw-TCP-Transport (Port 9100).

Keine externe Bibliothek: die paar Befehle, die ein Bon braucht, sind hier explizit,
damit jedes Byte nachvollziehbar ist.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .i18n import t

ESC = b"\x1b"
GS = b"\x1d"
DLE = b"\x10"
EOT = b"\x04"

# PC858 = PC850 + €-Zeichen; deckt ä ö ü é è à ç ab. Epson: ESC t 19.
CODEPAGE_ESC_T = 19
CODEPAGE_PY = "cp858"

# Zeilenabstand (ESC 3) und Vorschub (ESC J) zählt der TM-T20III in seiner vertikalen Bewegungseinheit 1/360 Zoll,
# der Druckkopf hat aber 203 dpi (Punkte). Im Code stehen Abstände in Punkten (wie Schrift A: 12 × 24) und werden
# beim Senden umgerechnet – sonst druckt «40 Punkte» nur 22,6 Punkte (enger als die Schrift: Zeilen ohne Luft).
# Nachgemessen an einem echten Bon (Rückmeldung: Vorschau und Druck stimmten nicht überein).
DPI = 203
MOTION_PER_INCH = 360
DEFAULT_SPACING_DOTS = 34                    # Standard des Druckers (ESC 2): 1/6 Zoll


def motion_units(dots: float) -> int:
    """Punkte (203 dpi) -> Bewegungseinheiten (1/360 Zoll) für ESC 3 und ESC J (0..255)."""
    return max(0, min(255, round(dots * MOTION_PER_INCH / DPI)))


def motion_dots(units: int) -> float:
    """Umgekehrt (Vorschau): Bewegungseinheiten -> Punkte."""
    return units * DPI / MOTION_PER_INCH


class Bon:
    """Fluent Builder für einen Bon. Alle Methoden geben self zurück."""

    def __init__(self, width: int = 48):
        self.width = width
        self._buf = bytearray()
        self.raw(ESC + b"@")                                   # Initialisieren (Reset Formatierung)
        self.raw(ESC + b"t" + bytes([CODEPAGE_ESC_T]))         # Zeichensatz

    # --- Low level -------------------------------------------------------
    def raw(self, data: bytes) -> "Bon":
        self._buf += data
        return self

    # Zeichen, die es in PC858 nicht gibt, sinnvoll ersetzen statt "?" (z.B. aus Handy-Tastaturen)
    _SUBST = str.maketrans({"–": "-", "—": "-", "„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'",
                            "…": "...", "\u00a0": " ", "\u202f": " ", "œ": "oe", "Œ": "OE"})

    # Steuerzeichen aus Eingaben (Notiz, Tisch, Gerätename) dürfen nie als Druckerbefehl ankommen
    _CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

    def text(self, s: str) -> "Bon":
        s = self._CTRL.sub("", s.replace("\t", " "))
        return self.raw(s.translate(self._SUBST).encode(CODEPAGE_PY, errors="replace"))

    def line(self, s: str = "") -> "Bon":
        return self.text(s + "\n")

    # --- Formatierung ----------------------------------------------------
    def align(self, where: str) -> "Bon":
        return self.raw(ESC + b"a" + bytes([{"left": 0, "center": 1, "right": 2}[where]]))

    def bold(self, on: bool = True) -> "Bon":
        return self.raw(ESC + b"E" + bytes([1 if on else 0]))

    def size(self, w: int = 1, h: int = 1) -> "Bon":
        """Zeichenvergrösserung 1..8 (GS !)."""
        w, h = max(1, min(w, 8)), max(1, min(h, 8))
        return self.raw(GS + b"!" + bytes([((w - 1) << 4) | (h - 1)]))

    def font(self, small: bool = False) -> "Bon":
        """Schrift B (klein, 9 × 17 Punkte: 64 statt 48 Zeichen auf 80 mm) oder Schrift A (ESC M)."""
        return self.raw(ESC + b"M" + bytes([1 if small else 0]))

    def underline(self, on: bool = True) -> "Bon":
        return self.raw(ESC + b"-" + bytes([1 if on else 0]))

    def invert(self, on: bool = True) -> "Bon":
        return self.raw(GS + b"B" + bytes([1 if on else 0]))

    def rule(self, char: str = "-") -> "Bon":
        return self.line(char * self.width)

    def columns(self, left: str, right: str, width: int | None = None) -> "Bon":
        """Links-/rechtsbündige Zeile; zu langer linker Text wird umgebrochen."""
        w = width or self.width
        space = w - len(right) - 1
        if len(left) <= space:
            return self.line(left + " " * (w - len(left) - len(right)) + right)
        # Umbruch: erste Zeile(n) voll, rechte Spalte auf der letzten Zeile
        chunks = [left[i:i + w] for i in range(0, len(left), w)]
        last = chunks.pop()
        for c in chunks:
            self.line(c)
        if len(last) > space:
            self.line(last)
            last = ""
        return self.line(last + " " * (w - len(last) - len(right)) + right)

    def line_spacing(self, dots: int | None = None) -> "Bon":
        """Zeilenabstand in Punkten (ESC 3 n, umgerechnet in 1/360 Zoll); None = Standard des Druckers (ESC 2, 1/6 Zoll).
        Ist die Schrift höher (doppelt hoch), nimmt der Drucker die Schrifthöhe."""
        return self.raw(ESC + b"2" if dots is None else ESC + b"3" + bytes([motion_units(dots)]))

    def gap(self, dots: int = 16) -> "Bon":
        """Kleiner Abstand in Punkten (ESC J n, umgerechnet in 1/360 Zoll; 24 Punkte = Höhe einer Zeile)."""
        return self.raw(ESC + b"J" + bytes([motion_units(dots)]))

    def feed(self, n: int = 1) -> "Bon":
        return self.raw(ESC + b"d" + bytes([max(0, min(n, 255))]))

    def cut(self, partial: bool = True) -> "Bon":
        """GS V 66 n: Papier um n Punkte vorschieben, dann schneiden (B=Teilschnitt, A=Vollschnitt).
        Der Vorschub bringt die letzte Zeile über das Messer hinaus."""
        return self.raw(GS + b"V" + (b"B" if partial else b"A") + b"\x03")

    def qr(self, data: str, module: int = 8, ecc: str = "M") -> "Bon":
        """Native QR-Code (GS ( k), unterstützt von TM-T20III/TM-T88. module = Punktgrösse 1..16."""
        payload = self._CTRL.sub("", data).encode("utf-8")      # UTF-8: "Bühne" bleibt "Bühne"
        def fn(cn: int, fnc: int, params: bytes) -> bytes:
            n = len(params) + 2
            return GS + b"(k" + bytes([n & 0xFF, n >> 8, cn, fnc]) + params
        self.raw(fn(49, 65, b"\x32\x00"))                                   # Modell 2
        self.raw(fn(49, 67, bytes([max(1, min(module, 16))])))              # Grösse
        self.raw(fn(49, 69, bytes([{"L": 48, "M": 49, "Q": 50, "H": 51}[ecc]])))  # Fehlerkorrektur
        self.raw(fn(49, 80, b"\x30" + payload))                             # Daten speichern
        return self.raw(fn(49, 81, b"\x30"))                                # drucken

    def raster(self, img) -> "Bon":
        """Bild (PIL) als Rasterbild drucken (GS v 0), schwarz = drucken. Breite höchstens Papierbreite in Punkten
        (80 mm: 576). Die Ausrichtung (ESC a) gilt auch für das Bild."""
        from PIL import ImageOps
        bw = ImageOps.invert(img.convert("L")).convert("1")    # Modus «1»: Bit 1 = weiss -> umkehren
        w, h = bw.size
        wb = (w + 7) // 8                                       # Zeilen auf ganze Bytes aufgefüllt (wie tobytes)
        return self.raw(GS + b"v0\x00" + bytes([wb & 0xFF, wb >> 8, h & 0xFF, h >> 8]) + bw.tobytes())

    def beep(self) -> "Bon":
        """Optional für Küchendrucker mit Buzzer (TM-T20III mit Summer): ESC ( A."""
        return self.raw(ESC + b"(A\x04\x00\x30\x33\x03\x0a")

    @property
    def pos(self) -> int:
        """Bisherige Länge der Druckerdaten (Bon-Baukasten: wo ein Baustein anfängt und aufhört)."""
        return len(self._buf)

    def truncate(self, pos: int) -> "Bon":
        """Druckerdaten ab pos verwerfen (Baustein hat nichts gedruckt: auch kein Abstand davor)."""
        del self._buf[pos:]
        return self

    def build(self) -> bytes:
        return bytes(self._buf)


# --- Transport -------------------------------------------------------------

class PrinterError(Exception):
    """Drucker nicht erreichbar, Timeout oder Verbindungsabbruch.

    code: unreachable | paper_out | cover_open | printer_error | no_status – danach richtet sich das Programm (nie nach dem
    Text, der ist übersetzt)."""

    def __init__(self, message: str, code: str = "unreachable", sent: bool = False):
        super().__init__(message)
        self.code = code
        self.sent = sent          # Abbruch NACH Beginn des Sendens: der Bon kann (teilweise) gedruckt sein


@dataclass(frozen=True)
class PrinterAddr:
    host: str
    port: int = 9100


async def _connect(addr: PrinterAddr, timeout: float):
    try:
        return await asyncio.wait_for(asyncio.open_connection(addr.host, addr.port), timeout=timeout)
    except (OSError, asyncio.TimeoutError) as e:
        raise PrinterError(t("srv.printer.connect_failed", host=addr.host, port=addr.port,
                             error=f"{type(e).__name__} {e}".strip())) from e


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
    except (OSError, asyncio.TimeoutError):
        pass


async def send_raw(addr: PrinterAddr, data: bytes, *,
                   connect_timeout: float = 3.0, write_timeout: float = 10.0,
                   check_status: bool = True, status_timeout: float = 1.5, require_status: bool = False) -> bool:
    """Sendet Bytes per Raw-TCP. Wirft PrinterError bei jedem Problem.

    Vor dem Senden wird der Echtzeit-Status abgefragt (DLE EOT 2 und 4): Bei leerem Papier, offenem
    Deckel oder Druckerfehler wird NICHT gesendet, sondern PrinterError geworfen – sonst nähme der Drucker
    den Bon in seinen Puffer, ohne ihn zu drucken, und er gälte als erledigt.
    Antwortet der Drucker nicht (Modell ohne Echtzeit-Status, oder noch mit einem grossen Auftrag
    beschäftigt), wird über eine NEUE Verbindung gesendet: eine verspätete Antwort auf der ersten
    Verbindung könnte sonst beim Schliessen den Rest des Bons abschneiden (TCP-Reset).
    Papierende während des Drucks lässt sich so nicht erkennen.
    require_status (T15): Der Drucker hat früher schon geantwortet – schweigt er jetzt, wird NICHT gesendet
    (PrinterError «no_status»): ein Epson mit Werkseinstellung «Zustand BUSY = Offline oder Empfangspuffer voll»
    schweigt bei leerem Papier und nähme den Bon sonst in den Speicher.
    -> True, wenn der Drucker gültig geantwortet hat.
    """
    answered = False
    reader, writer = await _connect(addr, connect_timeout)
    started = False
    try:
        if check_status:
            try:
                st = await _read_status(reader, writer, status_timeout)
            except OSError as e:
                raise PrinterError(t("srv.printer.status_aborted", host=addr.host, error=type(e).__name__)) from e
            if st is None and require_status:                 # hat früher geantwortet: jetzt nicht blind senden
                raise PrinterError(t("srv.printer.no_status"), "no_status")
            if st is None:                                    # keine (gültige) Antwort
                await _close(writer)
                reader, writer = await _connect(addr, connect_timeout)
            elif st.paper_out:
                raise PrinterError(t("srv.printer.paper_out"), "paper_out")
            elif st.cover_open:
                raise PrinterError(t("srv.printer.cover_open"), "cover_open")
            elif st.error:
                raise PrinterError(st.error, "printer_error")
            answered = st is not None
        started = True
        writer.write(data)
        await asyncio.wait_for(writer.drain(), timeout=write_timeout)
    except (OSError, asyncio.TimeoutError) as e:
        raise PrinterError(t("srv.printer.send_aborted", host=addr.host,
                             error=f"{type(e).__name__} {e}".strip()), sent=started) from e
    finally:
        await _close(writer)
    return answered


def _valid_status(b: int) -> bool:
    """Epson-Statusbyte: Bits 0, 1, 4, 7 haben feste Werte (0,1,1,0) – sonst ist es kein Status."""
    return (b & 0x93) == 0x12


async def _read_status(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       timeout: float) -> "PrinterStatus | None":
    """DLE EOT 2 (Offline-Ursache) und 4 (Papiersensor) auf einer offenen Verbindung.
    None = keine gültige Antwort (Drucker kann das nicht oder antwortet zu langsam)."""
    try:
        writer.write(DLE + EOT + b"\x02")
        await writer.drain()
        offline = (await asyncio.wait_for(reader.readexactly(1), timeout))[0]
        writer.write(DLE + EOT + b"\x04")
        await writer.drain()
        paper = (await asyncio.wait_for(reader.readexactly(1), timeout))[0]
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        return None
    if not (_valid_status(offline) and _valid_status(paper)):
        return None
    return PrinterStatus(
        online=True,
        cover_open=bool(offline & 0x04),
        paper_out=bool(paper & 0x60) or bool(offline & 0x20),   # Papiersensor bzw. "Stopp wegen Papierende"
        paper_near_end=bool(paper & 0x0C),
        error=t("srv.printer.error") if offline & 0x40 else None,
    )


@dataclass
class PrinterStatus:
    online: bool
    cover_open: bool = False
    paper_near_end: bool = False
    paper_out: bool = False
    error: str | None = None          # vom Drucker gemeldeter Fehler bzw. warum nicht erreichbar
    no_answer: bool = False           # erreichbar, aber keine Statusantwort

    @property
    def ok(self) -> bool:
        return self.online and not (self.cover_open or self.paper_out or self.error)


async def query_status(addr: PrinterAddr, timeout: float = 2.0) -> PrinterStatus:
    """Echtzeit-Status via DLE EOT n (n=2 Offline-Ursache, n=4 Papiersensor).
    Epson antwortet sofort, auch wenn der Druckpuffer voll ist."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(addr.host, addr.port), timeout=timeout)
    except (OSError, asyncio.TimeoutError) as e:
        return PrinterStatus(online=False, error=t("srv.printer.unreachable", error=type(e).__name__))
    try:
        st = await _read_status(reader, writer, timeout)
        return st if st is not None else PrinterStatus(online=True, no_answer=True)
    except OSError:
        return PrinterStatus(online=True, no_answer=True)
    finally:
        writer.close()
