# mizink-lpstat-shim  (marker: install.sh only replaces an lpstat file that contains this line)
"""Minimal `lpstat -p NAME` replacement for the photobooth-app's "check if printer is idle".

The app runs `lpstat -p <printer_name>` and looks for the words "idle" / "disabled" / "printing".
This shim answers from the worker's status file, so the app refuses prints (and shows its
"printer not ready" message) while the printer is offline, busy or reports an error.
"""

from __future__ import annotations

import json
import sys
import time

from .spool import status_path

STALE_S = 30            # status older than this -> worker considered down
STALE_PRINTING_S = 400  # the worker does not update the file while a print is running


def render(name: str, st: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    state = st.get("state")
    age = now - st.get("updated", 0)
    if not st or age > (STALE_PRINTING_S if state == "printing" else STALE_S):
        return f"printer {name} disabled since now - print service not running"
    if state == "printing":
        return f"printer {name} now printing {name}-1.  enabled since now"
    if st.get("problem"):
        return f"printer {name} disabled since now - {st['problem']}"
    if state == "offline":
        return f"printer {name} disabled since now - printer offline or switched off"
    return f"printer {name} is idle.  enabled since now"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    name = argv[-1] if argv else "mizink"
    try:
        st = json.loads(status_path().read_text())
    except (OSError, ValueError):
        st = {}
    print(render(name, st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
