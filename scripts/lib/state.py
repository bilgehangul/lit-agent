"""Per-item pipeline checkpoints (**P6**).

Ingesting 300 papers will hit interruptions. Every stage records its outcome per item, so
re-running skips completed work and a crash at paper 187 resumes at 187.

Two rules make that actually hold:

* **Write after every item, not at the end of the run.** A checkpoint file that is only
  flushed on clean exit is worthless precisely when it is needed.
* **A skip always carries a reason.** A silently skipped item is indistinguishable from a
  processed one when reading the file later, which is the gap P4 forbids.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

DONE = "done"
ERROR = "error"
SKIPPED = "skipped"
PENDING = "pending"

STAGES = ("ingest", "text", "figures", "analyze", "enrich", "sync")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    """SHA-256 of a file, so a replaced PDF re-triggers the pipeline for that item alone."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


@dataclass
class RunRecord:
    """One invocation of a pipeline command, for the run report."""

    command: str
    started: str = field(default_factory=_utcnow)
    finished: str | None = None
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    demotions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command, "started": self.started, "finished": self.finished,
            "processed": self.processed, "skipped": self.skipped, "errors": self.errors,
            "demotions": self.demotions, "notes": self.notes,
        }


class State:
    """The ``.lit/state.json`` checkpoint store."""

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        self.data: dict[str, Any] = data or {
            "version": SCHEMA_VERSION,
            "scope_version": None,
            "citekeys": {},
            "items": {},
            "runs": [],
        }
        self.data.setdefault("citekeys", {})
        self.data.setdefault("items", {})
        self.data.setdefault("runs", [])
        self._dirty = False

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.is_file():
            return cls(path)
        try:
            return cls(path, json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            # A corrupt checkpoint file must not wipe the corpus. Preserve it for the user
            # and start clean, rather than silently re-processing over a broken record.
            backup = path.with_suffix(f".corrupt-{int(datetime.now().timestamp())}.json")
            try:
                path.replace(backup)
            except OSError:
                pass
            inst = cls(path)
            inst.load_error = f"state.json was unreadable; moved it to {backup.name}"
            return inst

    load_error: str | None = None

    def save(self) -> None:
        """Atomic write. Called after every item, so an interrupted run stays resumable."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False, sort_keys=False)
                fh.write("\n")
            os.replace(tmp, self.path)
            self._dirty = False
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # --- citekey mapping ---------------------------------------------------

    @property
    def citekeys(self) -> dict[str, str]:
        """``source_id -> citekey``. Recorded so keys stay stable across runs."""
        return self.data["citekeys"]

    def record_citekeys(self, mapping: dict[str, str]) -> None:
        self.data["citekeys"].update(mapping)
        self._dirty = True

    # --- scope -------------------------------------------------------------

    @property
    def scope_version(self) -> str | None:
        return self.data.get("scope_version")

    def set_scope_version(self, version: str | None) -> bool:
        """Record the scope. Returns True when it *changed*, which makes notes stale."""
        previous = self.data.get("scope_version")
        self.data["scope_version"] = version
        self._dirty = True
        return previous is not None and previous != version

    # --- per-item stages ---------------------------------------------------

    def item(self, citekey: str) -> dict[str, Any]:
        return self.data["items"].setdefault(citekey, {"stages": {}})

    def stage(self, citekey: str, stage: str) -> dict[str, Any]:
        return self.item(citekey).get("stages", {}).get(stage, {})

    def is_done(self, citekey: str, stage: str, source_hash: str | None = None) -> bool:
        """True when this stage completed and its input has not changed since."""
        record = self.stage(citekey, stage)
        if record.get("status") != DONE:
            return False
        if source_hash and record.get("hash") and record["hash"] != source_hash:
            return False
        return True

    def mark(self, citekey: str, stage: str, status: str, **extra: Any) -> None:
        if status == SKIPPED and not extra.get("reason"):
            raise ValueError("a skipped stage must carry a reason (P4)")
        record = {"status": status, "at": _utcnow(), **extra}
        self.item(citekey).setdefault("stages", {})[stage] = record
        self._dirty = True

    def mark_done(self, citekey: str, stage: str, **extra: Any) -> None:
        self.mark(citekey, stage, DONE, **extra)

    def mark_error(self, citekey: str, stage: str, error: str, **extra: Any) -> None:
        self.mark(citekey, stage, ERROR, error=str(error)[:600], **extra)

    def mark_skipped(self, citekey: str, stage: str, reason: str, **extra: Any) -> None:
        self.mark(citekey, stage, SKIPPED, reason=reason, **extra)

    def set_source_id(self, citekey: str, source_id: str) -> None:
        self.item(citekey)["source_id"] = source_id
        self._dirty = True

    # --- queries -----------------------------------------------------------

    def citekeys_with(self, stage: str, status: str = DONE) -> list[str]:
        return sorted(
            ck for ck, rec in self.data["items"].items()
            if rec.get("stages", {}).get(stage, {}).get("status") == status
        )

    def pending(self, citekeys: Iterable[str], stage: str) -> list[str]:
        return [ck for ck in citekeys if not self.is_done(ck, stage)]

    def errors(self, stage: str | None = None) -> list[tuple[str, str, str]]:
        """``(citekey, stage, error)`` for everything that failed. Feeds the run report."""
        out = []
        for ck, rec in sorted(self.data["items"].items()):
            for name, record in (rec.get("stages") or {}).items():
                if stage and name != stage:
                    continue
                if record.get("status") == ERROR:
                    out.append((ck, name, record.get("error", "")))
        return out

    def skips(self, stage: str | None = None) -> list[tuple[str, str, str]]:
        out = []
        for ck, rec in sorted(self.data["items"].items()):
            for name, record in (rec.get("stages") or {}).items():
                if stage and name != stage:
                    continue
                if record.get("status") == SKIPPED:
                    out.append((ck, name, record.get("reason", "")))
        return out

    def counts(self, stage: str) -> dict[str, int]:
        tally = {DONE: 0, ERROR: 0, SKIPPED: 0, PENDING: 0}
        for rec in self.data["items"].values():
            status = rec.get("stages", {}).get(stage, {}).get("status", PENDING)
            tally[status] = tally.get(status, 0) + 1
        return tally

    # --- runs --------------------------------------------------------------

    def start_run(self, command: str) -> RunRecord:
        return RunRecord(command=command)

    def finish_run(self, run: RunRecord) -> None:
        run.finished = _utcnow()
        self.data["runs"].append(run.to_dict())
        # Keep the tail only; this file is read by humans.
        self.data["runs"] = self.data["runs"][-50:]
        self._dirty = True
        self.save()
