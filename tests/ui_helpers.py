"""Shared helper for UI content tests.

The dashboard was split from a monolithic ui/index.html into separate JSX
component files (ui/*.jsx, ui/*.js, ui/styles.css).  Tests that check for
patterns in the HTML/JS must read all source files, not just index.html.

Usage::

    from tests.ui_helpers import read_full_ui

    def _html() -> str:
        return read_full_ui()
"""

from __future__ import annotations

from pathlib import Path

_UI_DIR = Path(__file__).parent.parent / "ui"


def read_full_ui() -> str:
    """Read all UI source files and return them concatenated.

    Reads .html, .js, .jsx, and .css files from ui/ in sorted order.
    This works for both the legacy monolithic index.html and the current
    split-JSX architecture.
    """
    parts: list[str] = []
    for pattern in ("*.html", "*.js", "*.jsx", "*.css"):
        for f in sorted(_UI_DIR.glob(pattern)):
            try:
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)
