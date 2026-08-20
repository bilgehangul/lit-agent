"""Filesystem locations used across lit-agent.

Two roots:

* the **user root** (``~/.lit-agent``) holds machine-wide state: the venv and
  ``capabilities.json``. It is shared by every project on this machine.
* the **project root** (``<cwd>/.lit``) holds one research corpus: config, per-item
  checkpoints, extracted text, per-paper notes, synthesis. Spec section 6.

Nothing here reads or writes anything; it only computes paths.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --- user root -------------------------------------------------------------

USER_ROOT = Path(os.environ.get("LIT_AGENT_HOME", Path.home() / ".lit-agent"))
CAPABILITIES_FILE = USER_ROOT / "capabilities.json"
VENV_DIR = USER_ROOT / "venv"
CACHE_DIR = USER_ROOT / "cache"


def venv_python(venv: Path | None = None) -> Path:
    """Path to the interpreter inside a venv, on any platform."""
    venv = venv or VENV_DIR
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def in_managed_venv() -> bool:
    """True when the running interpreter is the plugin-managed venv."""
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


# --- project root ----------------------------------------------------------

PROJECT_DIRNAME = ".lit"


def project_root(start: Path | None = None) -> Path:
    """The ``.lit`` directory for the current project.

    Walks up from ``start`` looking for an existing ``.lit``; if none is found,
    returns ``<start>/.lit`` so callers can create it.
    """
    start = (start or Path.cwd()).resolve()
    for d in (start, *start.parents):
        candidate = d / PROJECT_DIRNAME
        if candidate.is_dir():
            return candidate
    return start / PROJECT_DIRNAME


class Corpus:
    """The layout of a project corpus (spec section 6)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or project_root()

    @property
    def config(self) -> Path:
        return self.root / "config.yaml"

    @property
    def scope_md(self) -> Path:
        return self.root / "scope.md"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def text(self) -> Path:
        return self.root / "text"

    @property
    def figures(self) -> Path:
        return self.root / "figures"

    @property
    def papers(self) -> Path:
        return self.root / "papers"

    @property
    def synthesis(self) -> Path:
        return self.root / "synthesis"

    @property
    def refs_bib(self) -> Path:
        return self.root / "refs.bib"

    @property
    def index(self) -> Path:
        return self.root / "index"

    def exists(self) -> bool:
        return self.config.is_file()

    def ensure(self) -> None:
        """Create the corpus directory tree. Safe to call repeatedly (P6)."""
        for d in (self.root, self.raw, self.text, self.figures, self.papers,
                  self.synthesis, self.index):
            d.mkdir(parents=True, exist_ok=True)


# --- plugin root -----------------------------------------------------------

def plugin_root() -> Path:
    """The installed plugin directory.

    Claude Code sets ``CLAUDE_PLUGIN_ROOT``; fall back to walking up from this
    file so the scripts stay runnable directly during development.
    """
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent
