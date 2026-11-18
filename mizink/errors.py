"""Plain-text descriptions for printer error states."""

from __future__ import annotations

# Known printer error codes -> plain text. Only add codes whose meaning you have verified on your
# own printer (e.g. by provoking them: open the cover, remove the paper, ...). Unknown codes get a
# generic hint.
ERROR_TEXT: dict[int, str] = {}

GENERIC_HINT = "check paper, cover/paper jam and battery, then switch the printer off and on"


def describe_error(code, category: str | None = None) -> str:
    text = ERROR_TEXT.get(code) if isinstance(code, int) else None
    return f"Printer error {code}: {text or GENERIC_HINT}"


def printer_problem(status: dict) -> str | None:
    """Return a plain-text problem description for a `mixed_status` result, or None if healthy."""
    code = status.get("error") or 0
    if code or status.get("category") == "error":
        return describe_error(code, status.get("category"))
    return None
