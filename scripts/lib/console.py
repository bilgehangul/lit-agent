"""Force UTF-8 on stdout/stderr.

Windows consoles default to a legacy code page (cp1252 here), and paper text is full of
characters it cannot encode - ligatures, curly quotes, dashes, accented author names. Any
command that prints extracted text or metadata would die with a UnicodeEncodeError on
exactly the papers that matter most.

Call ``setup()`` at the top of every CLI entry point.
"""

from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # A redirected or already-wrapped stream may refuse; replacement
                # characters in a log are far better than a crashed run.
                pass
