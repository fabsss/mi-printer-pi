"""Bluetooth-Classic-Transport für Linux (Raspberry Pi OS / BlueZ).

Ersetzt das macOS-Modul (IOBluetooth) des Upstream-Projekts. Der Drucker spricht das
Hannto-Protokoll über RFCOMM Kanal 1 (Serial Port Profile). Python bringt dafür
Kernel-Sockets mit (`socket.AF_BLUETOOTH` / `BTPROTO_RFCOMM`), es ist also weder
`rfcomm bind` noch pyserial nötig. Die Schnittstelle (`Connection`) ist identisch zur
macOS-Variante, damit `printer.py` unverändert bleibt.

Der Drucker muss vorher einmalig per `bluetoothctl` gekoppelt (pair + trust) werden.
"""

from __future__ import annotations

import select
import socket
import time

RFCOMM_CHANNEL = 1


class Connection:
    """Offener RFCOMM-Kanal zum Drucker. Als Context-Manager verwendbar."""

    def __init__(self, address: str, on_data=None, verbose: bool = False):
        self.address = address.replace("-", ":").upper()
        self.verbose = verbose
        self._external_sink = on_data
        self._rx = bytearray()
        self._sock: socket.socket | None = None
        self._closed = False

    # -- lifecycle -------------------------------------------------------
    def open(self, timeout: float = 20.0) -> "Connection":
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise ConnectionError("Dieses Python hat keine Bluetooth-Socket-Unterstützung (nur Linux).")
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(timeout)
        try:
            s.connect((self.address, RFCOMM_CHANNEL))
        except OSError as e:
            s.close()
            raise ConnectionError(
                f"RFCOMM-Verbindung zu {self.address} fehlgeschlagen: {e}. "
                "Drucker eingeschaltet? Gekoppelt (bluetoothctl pair/trust)? "
                "Nicht gleichzeitig vom Handy/Mi Home verbunden?") from e
        s.setblocking(False)
        self._sock = s
        self._closed = False
        self.pump(0.5)
        return self

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    @property
    def is_open(self) -> bool:
        return self._sock is not None and not self._closed

    # -- io --------------------------------------------------------------
    def _sink(self, data: bytes) -> None:
        self._rx.extend(data)
        if self._external_sink:
            self._external_sink(data)

    def write(self, frame: bytes) -> None:
        if self._sock is None or self._closed:
            raise ConnectionError("channel is not open")
        view = memoryview(frame)
        deadline = time.time() + 10
        while view:
            try:
                sent = self._sock.send(view)
                view = view[sent:]
            except BlockingIOError:
                if time.time() > deadline:
                    raise ConnectionError("write timeout")
                select.select([], [self._sock], [], 0.5)
            except OSError as e:
                self._closed = True
                raise ConnectionError(f"write failed: {e}") from e

    def read_available(self) -> bytes:
        data = bytes(self._rx)
        self._rx.clear()
        return data

    def pump(self, seconds: float) -> None:
        """Empfängt bis zu `seconds` lang Daten (kehrt früher zurück, wenn Daten da waren)."""
        if self._sock is None:
            time.sleep(seconds)
            return
        end = time.time() + seconds
        while True:
            remaining = max(0.0, end - time.time())
            try:
                ready, _, _ = select.select([self._sock], [], [], remaining)
            except (OSError, ValueError):
                self._closed = True
                return
            if not ready:
                return
            try:
                chunk = self._sock.recv(4096)
            except BlockingIOError:
                return
            except OSError:
                self._closed = True
                return
            if not chunk:  # Gegenstelle hat geschlossen
                self._closed = True
                return
            self._sink(chunk)
            if time.time() >= end:
                return

    def keep_alive(self, ping, interval: float = 5.0, on_status=None) -> None:
        last = 0.0
        while self.is_open:
            self.pump(0.2)
            now = time.time()
            if now - last >= interval:
                last = now
                reply = ping()
                if on_status:
                    on_status(reply)
        raise ConnectionError("link dropped")
