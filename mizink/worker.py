"""mizink-worker: background service that works through the queue and holds the
Bluetooth connection to the printer (with auto-reconnect and keep-alive).

Additional options in /etc/mizink/config.json (all optional):
  "fit":        "cover" (default, fills, crops edges) | "contain" | "stretch"
  "rotate":     "auto" (default: rotate landscape by 90°) | "none"
  "keep_awake": true (default, printer stays on) | false (connect only for jobs)
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
from .errors import printer_problem
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
        self.problem = None  # plain-text printer problem, None if healthy
        self._stop = False

    # -- Status ----------------------------------------------------------
    def set_status(self, state: str | None = None, **extra) -> None:
        if state:
            self.state = state
        data = {"state": self.state, "battery": self.battery, "last_error": self.last_error,
                "problem": self.problem, "queue": len(_spool.pending(self.spool)),
                "updated": time.time(), **extra}
        path = _spool.status_path()
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, path)
        except OSError:
            pass

    # -- Jobs --------------------------------------------------------
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
            log.error("Job %s failed permanently: %s", meta_path.stem, err)
            for p in (meta_path, self.spool / meta["image"]):
                if p.exists():
                    os.replace(p, failed / p.name)
        else:
            log.warning("Job %s attempt %d/%d failed: %s",
                        meta_path.stem, meta["attempts"], self.max_attempts, err)
            meta_path.write_text(json.dumps(meta))

    def _abort_pending(self, problem: str) -> int:
        """Move all waiting jobs to failed/ so nothing piles up and nothing prints twice later."""
        failed = self.spool / "failed"
        n = 0
        for meta_path in _spool.pending(self.spool):
            failed.mkdir(exist_ok=True)
            for p in (meta_path, meta_path.with_suffix(".img")):
                if p.exists():
                    os.replace(p, failed / p.name)
            log.error("Job %s moved to failed/ (not printed): %s", meta_path.stem, problem)
            n += 1
        return n

    def _set_problem(self, problem: str | None) -> None:
        self.problem = problem
        if problem:
            self.last_error = problem
        self.set_status()

    def process_queue(self, printer: Printer) -> int:
        """Print all waiting jobs. Re-raises on connection problems.

        Safety rules against piles of identical photos:
        * if the printer reports an error, no job is started and all waiting jobs go to failed/
        * a job whose image data was fully sent is never retried automatically
        """
        done = 0
        for meta_path in _spool.pending(self.spool):
            if self._stop:
                break
            try:
                meta = json.loads(meta_path.read_text())
                if meta["image"] != meta_path.stem + ".img":
                    raise ValueError("image name does not match job id")
                img_path = self.spool / meta["image"]
                if img_path.is_symlink() or meta_path.is_symlink() or not img_path.is_file():
                    raise ValueError("image missing or symlink")
                if not 1 <= int(meta.get("copies", 1)) <= 10:
                    raise ValueError("invalid copies")
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.error("Corrupt meta file %s: %s", meta_path, e)
                meta_path.rename(meta_path.with_suffix(".bad"))
                continue
            problem = printer_problem(printer.status())
            if problem:
                self._set_problem(problem)
                self._abort_pending(problem)
                return done
            self._set_problem(None)
            self.set_status("printing")
            log.info("Printing %s (%s, %dx)", meta_path.stem, meta.get("source"), meta.get("copies", 1))
            sent = {"all": False}

            def progress(i, n):
                if i == n:
                    sent["all"] = True

            try:
                printer.print_image(self._prepare_image(img_path), copies=meta.get("copies", 1),
                                    fit=self.fit, wait=True, on_progress=progress)
            except Exception as e:
                if sent["all"]:
                    # image fully transferred: the photo may already be printing - never retry
                    log.error("Job %s: error after sending the image, NOT retrying "
                              "(check whether it was printed): %s", meta_path.stem, e)
                    self.last_error = f"{type(e).__name__}: {e}"
                    for p in (meta_path, img_path):
                        p.unlink(missing_ok=True)
                else:
                    self._fail_or_retry(meta_path, meta, e)
                raise
            for p in (meta_path, img_path):
                p.unlink(missing_ok=True)
            done += 1
            log.info("Done: %s", meta_path.stem)
            state = printer.last_job_info.get("job_state")
            problem = printer_problem(printer.status())
            if problem:
                log.error("Job %s was sent, but the printer now reports: %s", meta_path.stem, problem)
                self._set_problem(problem)
                self._abort_pending(problem)
                return done
            if state != "finished":
                log.warning("Job %s: printer did not confirm 'finished' (job_state=%s)", meta_path.stem, state)
            self.last_error = None
        return done

    # -- Main loop ---------------------------------------------------
    def stop(self, *_):
        self._stop = True

    def run(self) -> None:
        backoff = 3.0
        while not self._stop:
            try:
                if not self.keep_awake and not _spool.pending(self.spool):
                    self.problem = None  # unknown while disconnected; re-checked on the next job
                    self.set_status("sleeping")
                    time.sleep(1)
                    continue
                with self.printer_factory() as p:
                    log.info("Connected to %s", p.address)
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
                            problem = printer_problem(st)
                            if problem and problem != self.problem:
                                log.error("%s", problem)
                            self.problem = problem
                            self.set_status("connected", printer_state=st.get("category"),
                                            printer_error=st.get("error"))
                        p.conn.pump(0.5)
                    if not p.conn.is_open and not self._stop:
                        log.warning("Connection closed by printer")
            except (OSError, ConnectionError, PrinterError) as e:
                self.last_error = str(e)
                self.problem = None
                self.set_status("offline")
                log.warning("Printer unreachable/error: %s (retrying in %.0fs)", e, backoff)
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
