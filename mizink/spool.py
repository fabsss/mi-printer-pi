"""Print queue (spool directory) and the fast `mizink-enqueue` command.

The photobooth-app allows a print command only 6 seconds, but a ZINK print takes ~40-60 s.
So `mizink-enqueue` only places the image in the queue (milliseconds) and the
`mizink-worker` service prints it in the background.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

DEFAULT_SPOOL = "/var/spool/mizink"
DEFAULT_STATUS = "/run/mizink/status.json"
MAX_FILE_BYTES = 40 * 1024 * 1024


def spool_dir() -> Path:
    return Path(os.environ.get("MIZINK_SPOOL", DEFAULT_SPOOL))


def status_path() -> Path:
    return Path(os.environ.get("MIZINK_STATUS", DEFAULT_STATUS))


def pending(spool: Path) -> list[Path]:
    """Meta files of all waiting jobs, oldest first."""
    return sorted(spool.glob("*.json"))


def enqueue(src: str | Path, copies: int = 1, max_queue: int = 10, spool: Path | None = None) -> Path:
    spool = spool or spool_dir()
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")
    if src.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"File too large (> {MAX_FILE_BYTES // 1024 // 1024} MB): {src}")
    if not 1 <= copies <= 10:
        raise ValueError("copies must be between 1 and 10")
    if not spool.is_dir():
        raise FileNotFoundError(f"Spool directory missing: {spool} (did you run install.sh?)")
    if len(pending(spool)) >= max_queue:
        raise RuntimeError(f"Queue full ({max_queue} jobs) - printer offline or out of paper?")

    job = f"{time.time_ns()}-{uuid.uuid4().hex[:6]}"
    img_tmp, img = spool / f".{job}.tmp", spool / f"{job}.img"
    shutil.copyfile(src, img_tmp)
    os.replace(img_tmp, img)
    meta_tmp, meta = spool / f".{job}.meta.tmp", spool / f"{job}.json"
    meta_tmp.write_text(json.dumps({"image": img.name, "copies": copies, "attempts": 0,
                                    "source": src.name, "queued_at": time.time()}))
    os.replace(meta_tmp, meta)  # only now visible to the worker
    return meta


def _read_status() -> dict:
    try:
        return json.loads(status_path().read_text())
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mizink-enqueue",
                                 description="Queue an image for the Mi Portable Photo Printer.")
    ap.add_argument("image", nargs="?", help="image file (JPEG/PNG)")
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--max-queue", type=int, default=10)
    ap.add_argument("--status", action="store_true", help="show worker service status")
    a = ap.parse_args(argv)

    if a.status:
        st = _read_status()
        print(json.dumps(st, indent=2) if st else "No status (is mizink-worker running?)")
        print(f"queued jobs: {len(pending(spool_dir())) if spool_dir().is_dir() else '?'}")
        return 0
    if not a.image:
        ap.error("image file missing")
    try:
        meta = enqueue(a.image, a.copies, a.max_queue)
    except Exception as e:
        print(f"mizink-enqueue: {e}", file=sys.stderr)
        return 1
    st = _read_status()
    note = "" if st.get("state") in ("connected", "printing") else \
        f" (printer currently: {st.get('state', 'unknown')} - job is waiting)"
    print(f"queued {meta.stem}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
