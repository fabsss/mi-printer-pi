"""Druck-Warteschlange (Spool-Verzeichnis) und der schnelle `mizink-enqueue`-Befehl.

Die Photobooth-App erlaubt einem Druckbefehl nur 6 Sekunden Laufzeit, ein ZINK-Druck
dauert aber ~40-60 s. Deshalb legt `mizink-enqueue` das Bild nur in die Warteschlange
(Millisekunden) und der Dienst `mizink-worker` druckt es im Hintergrund.
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
    """Meta-Dateien aller wartenden Aufträge, älteste zuerst."""
    return sorted(spool.glob("*.json"))


def enqueue(src: str | Path, copies: int = 1, max_queue: int = 10, spool: Path | None = None) -> Path:
    spool = spool or spool_dir()
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"Datei nicht gefunden: {src}")
    if src.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Datei zu groß (> {MAX_FILE_BYTES // 1024 // 1024} MB): {src}")
    if not 1 <= copies <= 10:
        raise ValueError("copies muss zwischen 1 und 10 liegen")
    if not spool.is_dir():
        raise FileNotFoundError(f"Spool-Verzeichnis fehlt: {spool} (install.sh ausgeführt?)")
    if len(pending(spool)) >= max_queue:
        raise RuntimeError(f"Warteschlange voll ({max_queue} Aufträge) - Drucker offline oder Papier leer?")

    job = f"{time.time_ns()}-{uuid.uuid4().hex[:6]}"
    img_tmp, img = spool / f".{job}.tmp", spool / f"{job}.img"
    shutil.copyfile(src, img_tmp)
    os.replace(img_tmp, img)
    meta_tmp, meta = spool / f".{job}.meta.tmp", spool / f"{job}.json"
    meta_tmp.write_text(json.dumps({"image": img.name, "copies": copies, "attempts": 0,
                                    "source": src.name, "queued_at": time.time()}))
    os.replace(meta_tmp, meta)  # erst jetzt sichtbar für den Worker
    return meta


def _read_status() -> dict:
    try:
        return json.loads(status_path().read_text())
    except (OSError, ValueError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mizink-enqueue",
                                 description="Bild in die Druckwarteschlange des Mi-Photo-Printers legen.")
    ap.add_argument("image", nargs="?", help="Bilddatei (JPEG/PNG)")
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--max-queue", type=int, default=10)
    ap.add_argument("--status", action="store_true", help="Status des Worker-Dienstes anzeigen")
    a = ap.parse_args(argv)

    if a.status:
        st = _read_status()
        print(json.dumps(st, indent=2) if st else "Kein Status (läuft mizink-worker?)")
        print(f"wartende Aufträge: {len(pending(spool_dir())) if spool_dir().is_dir() else '?'}")
        return 0
    if not a.image:
        ap.error("Bilddatei fehlt")
    try:
        meta = enqueue(a.image, a.copies, a.max_queue)
    except Exception as e:
        print(f"mizink-enqueue: {e}", file=sys.stderr)
        return 1
    st = _read_status()
    note = "" if st.get("state") in ("connected", "printing") else \
        f" (Drucker aktuell: {st.get('state', 'unbekannt')} - Auftrag wartet)"
    print(f"queued {meta.stem}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
