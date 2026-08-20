"""The capability state machine (spec section 5).

Every optional feature in lit-agent is either *verified working* or *explicitly off*.
There is no third state a user can sit in unknowingly (**P1**). ``broken`` exists only as
a demotion: something that probed clean at setup and then failed during a run.

A capability is only ever promoted to ``enabled`` by a probe that actually ran and passed
(**P2**) -- never by a user asserting it works, and never by this module on its own.

State lives in ``~/.lit-agent/capabilities.json``:

```json
{
  "version": 1,
  "capabilities": {
    "pdf_text": {
      "status": "enabled",
      "last_verified": "2026-08-20T14:03:11Z",
      "config": {},
      "last_error": null
    }
  }
}
```
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .paths import CAPABILITIES_FILE

SCHEMA_VERSION = 1

ENABLED = "enabled"
DISABLED = "disabled"
BROKEN = "broken"
VALID_STATUSES = (ENABLED, DISABLED, BROKEN)

#: Consecutive in-run failures before a capability is demoted to ``broken``.
#: Spec section 5: "If a capability probe passes at setup but fails three times during a
#: run, mark it broken, finish the run without it, and report the demotion. Never spin."
FAILURE_BUDGET = 3


@dataclass(frozen=True)
class CapabilitySpec:
    """Static description of a capability. The registry below is the source of truth."""

    id: str
    title: str
    required: bool
    #: One sentence: what enabling this buys the user.
    enables: str
    #: One sentence: what it costs them to set up.
    cost: str
    #: What the probe actually does, in the user's words.
    probe_desc: str
    #: Commands that refuse to run without this capability.
    unlocks: tuple[str, ...] = ()


REGISTRY: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        id="python_env",
        title="Python environment",
        required=True,
        enables="Everything. The venv and core libraries the pipeline runs on.",
        cost="Nothing to configure - lit-agent builds the venv itself.",
        probe_desc="Import pymupdf and friends from the managed venv and check versions.",
        unlocks=("all",),
    ),
    CapabilitySpec(
        id="source",
        title="Library source",
        required=True,
        enables="Reading your library: metadata, PDFs, notes, annotations.",
        cost="Usually auto-detected. Otherwise you point us at a folder.",
        probe_desc="Pull 3 items from the adapter and resolve their PDF paths.",
        unlocks=("/lit-ingest",),
    ),
    CapabilitySpec(
        id="pdf_text",
        title="PDF text extraction",
        required=True,
        enables="Turning PDFs into text with page markers, which every citation depends on.",
        cost="Nothing - included in the core install.",
        probe_desc="Extract text from a generated sample PDF and assert over 200 characters.",
        unlocks=("/lit-analyze", "/lit-ask", "/lit-review"),
    ),
    CapabilitySpec(
        id="figures",
        title="Figure and table extraction",
        required=False,
        enables="Pulling figures and tables out of papers with their captions.",
        cost="Nothing - included in the core install.",
        probe_desc="Extract at least one embedded image from a generated sample PDF.",
        unlocks=("/lit-figures",),
    ),
    CapabilitySpec(
        id="layout_text",
        title="Layout-aware text extraction",
        required=False,
        enables="Markdown-with-structure extraction for messy multi-column PDFs.",
        cost="A ~57 MB extra install, and roughly 85x slower than the default extractor.",
        probe_desc="Import pymupdf4llm and convert one page of a sample PDF.",
        unlocks=("/lit-analyze --layout",),
    ),
    CapabilitySpec(
        id="arxiv_source",
        title="arXiv source fetch",
        required=False,
        enables="Original figure files and exact LaTeX captions for arXiv papers.",
        cost="Nothing, but it makes network requests to arxiv.org.",
        probe_desc="Fetch and unpack a known arXiv e-print tarball.",
        unlocks=("/lit-figures (tier 1)",),
    ),
    CapabilitySpec(
        id="browser",
        title="Browser control",
        required=False,
        enables="Driving your signed-in Chrome session for web-based enrichment.",
        cost="Needs browser automation available in your Claude Code environment.",
        probe_desc="Navigate to a page and read its title back.",
        unlocks=("/lit-enrich (browser modes)",),
    ),
    CapabilitySpec(
        id="semantic_scholar",
        title="Semantic Scholar enrichment",
        required=False,
        enables="TLDRs, citation counts, and related work from a public API. No scraping.",
        cost="Nothing. An optional API key raises the search rate limit.",
        probe_desc="Look up a known DOI and confirm the response carries a title.",
        unlocks=("/lit-enrich",),
    ),
    CapabilitySpec(
        id="scholar_labs",
        title="Google Scholar Labs",
        required=False,
        enables="Query-relative AI summaries from Google Scholar's experimental mode.",
        cost="Automated access is outside Google Scholar's terms of service. Off by default. "
             "Demoted to a stretch goal - see docs/decisions/0003.",
        probe_desc="Load Scholar Labs and distinguish working / signed-out / waitlisted.",
        unlocks=("/lit-enrich --scholar-labs",),
    ),
    CapabilitySpec(
        id="zotero_write",
        title="Zotero write-back",
        required=False,
        enables="Writing your notes and synthesis back into Zotero as real notes.",
        cost="Needs a synced library and a Web API key with write permission.",
        probe_desc="Create a note on a scratch item, read it back, then delete it.",
        unlocks=("/lit-sync",),
    ),
    CapabilitySpec(
        id="vector_index",
        title="Vector search index",
        required=False,
        enables="Semantic retrieval for corpora too large for keyword search (~150+ papers).",
        cost="One extra pip package. No server.",
        probe_desc="Embed two strings and assert the cosine similarity is sane.",
        unlocks=("/lit-index", "/lit-ask (vector mode)"),
    ),
    CapabilitySpec(
        id="grobid",
        title="GROBID structured parsing",
        required=False,
        enables="Proper section and reference structure on badly laid-out PDFs.",
        cost="Needs a running GROBID server (Docker).",
        probe_desc="Ping the GROBID server's /api/isalive endpoint.",
        unlocks=("/lit-ingest --grobid",),
    ),
)

BY_ID: dict[str, CapabilitySpec] = {c.id: c for c in REGISTRY}
REQUIRED_IDS: tuple[str, ...] = tuple(c.id for c in REGISTRY if c.required)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CapabilityState:
    """The mutable half: what we currently believe about one capability."""

    id: str
    status: str = DISABLED
    last_verified: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None

    @property
    def spec(self) -> CapabilitySpec | None:
        return BY_ID.get(self.id)

    @property
    def is_enabled(self) -> bool:
        return self.status == ENABLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "last_verified": self.last_verified,
            "config": self.config,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, cid: str, d: dict[str, Any]) -> "CapabilityState":
        status = d.get("status", DISABLED)
        if status not in VALID_STATUSES:
            # An unknown status is not a third state to live in (P1) - treat it as off.
            status = DISABLED
        return cls(
            id=cid,
            status=status,
            last_verified=d.get("last_verified"),
            config=d.get("config") or {},
            last_error=d.get("last_error"),
        )


class Capabilities:
    """Load, mutate, and persist the capability file."""

    def __init__(self, states: dict[str, CapabilityState] | None = None,
                 path: Path | None = None) -> None:
        self.path = path or CAPABILITIES_FILE
        self.states: dict[str, CapabilityState] = states or {}
        # Any capability in the registry but absent from disk is off, not missing (P1).
        for spec in REGISTRY:
            self.states.setdefault(spec.id, CapabilityState(id=spec.id))
        self._run_failures: dict[str, int] = {}
        self.demotions: list[tuple[str, str]] = []

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Capabilities":
        path = path or CAPABILITIES_FILE
        if not path.is_file():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt file must not silently look like "everything off" (P4);
            # it is reported, and every capability falls back to disabled.
            inst = cls(path=path)
            inst.load_error = f"{type(exc).__name__}: {exc}"
            return inst
        states = {
            cid: CapabilityState.from_dict(cid, d)
            for cid, d in (raw.get("capabilities") or {}).items()
        }
        return cls(states=states, path=path)

    load_error: str | None = None

    def save(self) -> None:
        """Write atomically - a half-written capability file is exactly the state P1 forbids."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "updated": _utcnow(),
            "capabilities": {
                cid: st.to_dict()
                for cid, st in sorted(self.states.items())
                if cid in BY_ID
            },
        }
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=False)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # --- access ------------------------------------------------------------

    def __getitem__(self, cid: str) -> CapabilityState:
        return self.states[cid]

    def get(self, cid: str) -> CapabilityState:
        return self.states.get(cid) or CapabilityState(id=cid)

    def is_enabled(self, cid: str) -> bool:
        return self.get(cid).is_enabled

    def enabled_ids(self) -> list[str]:
        return [c.id for c in REGISTRY if self.is_enabled(c.id)]

    # --- transitions -------------------------------------------------------

    def enable(self, cid: str, config: dict[str, Any] | None = None) -> CapabilityState:
        """Promote to enabled. Only ever call this after a probe has actually passed (P2)."""
        st = self.states.setdefault(cid, CapabilityState(id=cid))
        st.status = ENABLED
        st.last_verified = _utcnow()
        st.last_error = None
        if config is not None:
            st.config = config
        self._run_failures.pop(cid, None)
        return st

    def disable(self, cid: str, reason: str | None = None) -> CapabilityState:
        """Turn off cleanly. This is a normal outcome, not a failure."""
        st = self.states.setdefault(cid, CapabilityState(id=cid))
        st.status = DISABLED
        st.last_error = reason
        return st

    def mark_broken(self, cid: str, error: str) -> CapabilityState:
        st = self.states.setdefault(cid, CapabilityState(id=cid))
        st.status = BROKEN
        st.last_error = error
        return st

    def record_failure(self, cid: str, error: str) -> bool:
        """Count an in-run failure. Returns True once the capability has been demoted.

        Spec section 5: three strikes during a run demotes to ``broken``; the run then
        finishes without the capability and reports the demotion. Never spin.
        """
        self._run_failures[cid] = self._run_failures.get(cid, 0) + 1
        if self._run_failures[cid] >= FAILURE_BUDGET:
            self.mark_broken(cid, f"failed {FAILURE_BUDGET}x during a run: {error}")
            self.demotions.append((cid, error))
            return True
        return False

    def failure_count(self, cid: str) -> int:
        return self._run_failures.get(cid, 0)


# --- runtime gating --------------------------------------------------------


class CapabilityError(RuntimeError):
    """Raised when a skill cannot run because a capability it needs is not enabled."""

    def __init__(self, message: str, missing: list[str]) -> None:
        super().__init__(message)
        self.missing = missing


def fix_command(cid: str) -> str:
    return f"/lit-setup --reconfigure {cid}"


def gate(required: Iterable[str], caps: Capabilities | None = None) -> Capabilities:
    """Check the capabilities a skill needs before it does any work.

    Every skill calls this first. On failure it raises with a message naming exactly what
    is missing and the single command that fixes it -- and the skill exits without
    attempting the work (spec section 5, "Runtime gating").
    """
    caps = caps or Capabilities.load()
    missing = [cid for cid in required if not caps.is_enabled(cid)]
    if not missing:
        return caps

    lines = ["This command needs capabilities that are not enabled:", ""]
    for cid in missing:
        st = caps.get(cid)
        spec = BY_ID.get(cid)
        title = spec.title if spec else cid
        lines.append(f"  {title} ({cid}): {st.status}")
        if st.last_error:
            lines.append(f"      last error: {st.last_error}")
        lines.append(f"      fix: {fix_command(cid)}")
    if caps.load_error:
        lines += ["", f"  note: capabilities file unreadable ({caps.load_error})"]
    raise CapabilityError("\n".join(lines), missing)


def format_table(caps: Capabilities) -> str:
    """The status table printed by /lit-doctor and at the end of /lit-setup."""
    mark = {ENABLED: "on", DISABLED: "off", BROKEN: "BROKEN"}
    width = max(len(c.title) for c in REGISTRY)
    rows = [f"  {'CAPABILITY'.ljust(width)}  {'REQ':3}  {'STATUS':7}  LAST VERIFIED",
            f"  {'-' * width}  ---  -------  -------------"]
    for spec in REGISTRY:
        st = caps.get(spec.id)
        verified = (st.last_verified or "-")[:19].replace("T", " ")
        rows.append(
            f"  {spec.title.ljust(width)}  {'yes' if spec.required else ' - ':3}  "
            f"{mark[st.status]:7}  {verified}"
        )
        if st.status == BROKEN and st.last_error:
            rows.append(f"  {' ' * width}       -> {st.last_error[:70]}")
    return "\n".join(rows)
