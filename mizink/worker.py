"""mizink-worker: Hintergrunddienst, der die Warteschlange abarbeitet und die
Bluetooth-Verbindung zum Drucker hält (inkl. Auto-Reconnect und Keep-Alive).

Zusätzliche Optionen in /etc/mizink/config.json (alle optional):
  "fit":        "cover" (Standard, füllt, beschneidet Ränder) | "contain" | "stretch"
  "rotate":     "auto" (Standard: Querformat -> 90° drehen) | "none"
  "keep_awake": true (Standard, Drucker bleibt an) | false (Verbindung nur bei Aufträgen)
  "max_attempts": 3
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from . import config as _config
from . import spool as _spool
from .printer import Printer, PrinterError

log = logging.getLogger("mizink.worker")
PING_INTERVAL = 5.0


class Worker:
    def __init__(self, spool: Path, settings: dict, printer_factory):
        self.spool = spool
        self.fit = settings.get("fit", "cover")
        self.rotate = settings.get("rotate", "auto")
        self.keep_awake = bool(settings.get("keep_awake", True))
        self.max_attempts = int(settings.get("max_attempts", 3))
        self.printer_factory = printer_factory
        self.state = "starting"
        self.battery = None
        self.last_error = None
        self._stop = False

    # -- Status ----------------------------------------------------------
    def set_status(self, state: str | None = None, **extra) -> None:
        if state:
            self.state = state
        data = {"state": self.state, "battery": self.battery, "last_error": self.last_error,
                "queue": len(_spool.pending(self.spool)), "updated": time.time(), **extra}
        path = _spool.status_path()
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, path)
        except OSError:
            pass

    # -- Aufträge --------------------------------------------------------
    def _prepare_image(self, path: Path):
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        if self.rotate == "auto" and im.width > im.height:
            im = im.rotate(90, expand=True)
        return im

    def _fail_or_retry(self, meta_path: Path, meta: dict, err: Exception) -> None:
        meta["attempts"] = meta.get("attempts", 0) + 1
        self.last_error = f"{type(err).__name__}: {err}"
        if meta["attempts"] >= self.max_attempts:
            failed = self.spool / "failed"
            failed.mkdir(exist_ok=True)
            log.error("Auftrag %s endgültig fehlgeschlagen: %s", meta_path.stem, err)
            for p in (meta_path, self.spool / meta["image"]):
                if p.exists():
                    os.replace(p, failed / p.name)
        else:
            log.warning("Auftrag %s Versuch %d/%d fehlgeschlagen: %s",
                        meta_path.stem, meta["attempts"], self.max_attempts, err)
            meta_path.write_text(json.dumps(meta))

    def process_queue(self, printer: Printer) -> int:
        """Druckt alle wartenden Aufträge. Wirft bei Verbindungsproblemen weiter."""
        done = 0
        for meta_path in _spool.pending(self.spool):
            if self._stop:
                break
            try:
                meta = json.loads(meta_path.read_text())
                img_path = self.spool / meta["image"]
            except (OSError, ValueError, KeyError) as e:
                log.error("Defekte Meta-Datei %s: %s", meta_path, e)
                meta_path.rename(meta_path.with_suffix(".bad"))
                continue
            self.set_status("printing")
            log.info("Drucke %s (%s, %dx)", meta_path.stem, meta.get("source"), meta.get("copies", 1))
            try:
                printer.print_image(self._prepare_image(img_path), copies=meta.get("copies", 1),
                                    fit=self.fit, wait=True)
            except Exception as e:
                self._fail_or_retry(meta_path, meta, e)
                raise
            for p in (meta_path, img_path):
                p.unlink(missing_ok=True)
            self.last_error = None
            done += 1
            log.info("Fertig: %s", meta_path.stem)
        return done

    # -- Hauptschleife ---------------------------------------------------
    def stop(self, *_):
        self._stop = True

    def run(self) -> None:
        backoff = 3.0
        while not self._stop:
            try:
                if not self.keep_awake and not _spool.pending(self.spool):
                    self.set_status("sleeping")
                    time.sleep(1)
                    continue
                with self.printer_factory() as p:
                    log.info("Verbunden mit %s", p.address)
                    backoff = 3.0
                    self.set_status("connected")
                    last_ping = 0.0
                    while not self._stop and p.conn.is_open:
                        if _spool.pending(self.spool):
                            self.process_queue(p)
                            self.set_status("connected")
                            continue
                        if not self.keep_awake:
                            break
                        if time.time() - last_ping >= PING_INTERVAL:
                            last_ping = time.time()
                            p.rpc("retime", [])
                            st = p.status()
                            self.battery = st.get("battery")
                            self.set_status("connected", printer_state=st.get("category"),
                                            printer_error=st.get("error"))
                        p.conn.pump(0.5)
                    if not p.conn.is_open and not self._stop:
                        log.warning("Verbindung vom Drucker getrennt")
            except (OSError, ConnectionError, PrinterError) as e:
                self.last_error = str(e)
                self.set_status("offline")
                log.warning("Drucker nicht erreichbar/Fehler: %s (neuer Versuch in %.0fs)", e, backoff)
                end = time.time() + backoff
                while time.time() < end and not self._stop:
                    time.sleep(0.5)
                backoff = min(backoff * 1.5, 20.0)
        self.set_status("stopped")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = _config._load_file()
    address = _config.resolve_address()
    cipher = _config.resolve_cipher()
    spool = _spool.spool_dir()
    spool.mkdir(parents=True, exist_ok=True)
    w = Worker(spool, settings, lambda: Printer(address, cipher))
    signal.signal(signal.SIGTERM, w.stop)
    signal.signal(signal.SIGINT, w.stop)
    w.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
