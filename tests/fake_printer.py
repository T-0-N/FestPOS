"""Simulierter Epson-Netzwerkdrucker für Tests und Entwicklung ohne Hardware.

    python -m tests.fake_printer 9100     # druckt jeden Bon als Text in die Konsole
"""
from __future__ import annotations

import asyncio
import sys

from app.escpos import CODEPAGE_PY

# Befehl -> Anzahl Parameterbytes
_ESC = {b"@": 0, b"t": 1, b"a": 1, b"E": 1, b"d": 1, b"!": 1, b"-": 1, b"J": 1, b"3": 1, b"2": 0}
_GS = {b"!": 1, b"B": 1}


def to_text(data: bytes) -> list[str]:
    """Entfernt ESC/POS-Steuersequenzen; liefert eine Liste von Bons (getrennt am Schnitt)."""
    bons, cur, i = [], bytearray(), 0
    while i < len(data):
        c = data[i:i + 1]
        if c == b"\x1b":
            cmd = data[i + 1:i + 2]
            if cmd == b"(":                                   # ESC ( A pL pH ...
                n = data[i + 3] + 256 * data[i + 4]
                i += 5 + n
                continue
            if cmd == b"d":
                cur += b"\n" * data[i + 2]
            i += 2 + _ESC.get(cmd, 0)
        elif c == b"\x1d":
            cmd = data[i + 1:i + 2]
            if cmd == b"(":                                   # GS ( k pL pH cn fn ...
                n = data[i + 3] + 256 * data[i + 4]
                if data[i + 2:i + 3] == b"k" and data[i + 6] == 80:   # QR-Daten speichern
                    cur += b"[QR:" + data[i + 8:i + 5 + n] + b"]\n"
                i += 5 + n
                continue
            if cmd == b"v" and data[i + 2:i + 3] == b"0":     # Rasterbild GS v 0 m xL xH yL yH Daten
                wb, h = data[i + 4] + 256 * data[i + 5], data[i + 6] + 256 * data[i + 7]
                cur += b"[Bild]\n"
                i += 8 + wb * h
                continue
            if cmd == b"V":                                   # Schnitt
                fn = data[i + 2]
                i += 4 if fn in (65, 66, 97, 98) else 3
                bons.append(cur.decode(CODEPAGE_PY).rstrip("\n"))
                cur = bytearray()
                continue
            i += 2 + _GS.get(cmd, 0)
        else:
            cur += c
            i += 1
    if cur.strip():
        bons.append(cur.decode(CODEPAGE_PY).rstrip("\n"))
    return bons


class FakePrinter:
    """TCP-Server auf 127.0.0.1; sammelt pro Verbindung die empfangenen Bytes.
    Beantwortet DLE EOT 2/4 wie ein TM-T20III. Zustände zum Testen:
      paper_out  – Papier leer: Status meldet es, angenommene Daten landen nur im Puffer (nicht gedruckt)
      cover_open – Deckel offen
      silent     – antwortet nicht auf Statusabfragen (Modell ohne Echtzeit-Status)"""

    def __init__(self, port: int = 0, echo: bool = False):
        self.port = port
        self.echo = echo
        self.received: list[bytes] = []
        self.buffered: list[bytes] = []          # angenommen, aber wegen Störung nicht gedruckt
        self.paper_out = self.cover_open = self.silent = False
        self._server: asyncio.base_events.Server | None = None

    def _status(self, n: int) -> bytes:
        if n == 2:
            return bytes([0x12 | (0x04 if self.cover_open else 0) | (0x20 if self.paper_out else 0)])
        if n == 4:
            return bytes([0x12 | (0x60 if self.paper_out else 0)])
        return b"\x12"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buf = bytearray()
        while chunk := await reader.read(4096):
            buf += chunk
            while b"\x10\x04" in buf and buf.index(b"\x10\x04") + 2 < len(buf):   # Statusabfrage komplett
                k = buf.index(b"\x10\x04")
                n = buf[k + 2]
                del buf[k:k + 3]
                if not self.silent:
                    writer.write(self._status(n))
                    await writer.drain()
        writer.close()
        if buf and (self.paper_out or self.cover_open):
            self.buffered.append(bytes(buf))
        elif buf:
            self.received.append(bytes(buf))
            if self.echo:
                for bon in to_text(bytes(buf)):
                    print(bon + "\n" + "✂ " + "-" * 46, flush=True)

    async def start(self) -> "FakePrinter":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def bons(self) -> list[str]:
        return [b for data in self.received for b in to_text(data)]


if __name__ == "__main__":
    async def main(port: int):
        p = await FakePrinter(port, echo=True).start()
        print(f"Fake-Drucker lauscht auf 127.0.0.1:{p.port}")
        await asyncio.Event().wait()
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 9100))
