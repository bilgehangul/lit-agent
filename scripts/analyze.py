"""Stage 4 - per-paper analysis (spec section 9).

**Claude is the analyzer; this script is the scaffolding.** It does not call a model API,
deliberately: requiring an API key would add a required capability and break the
zero-optional path P3 protects. The `lit-analyze` skill drives the reading and writing; this
module decides *what* to analyze, assembles the exact input, and validates what comes back.

    python scripts/analyze.py --plan               # what needs analyzing, and what it costs
    python scripts/analyze.py --next 5             # emit the next N work units as JSON
    python scripts/analyze.py --unit doe2024x      # one paper's full analysis input
    python scripts/analyze.py --accept doe2024x    # validate a written note, checkpoint it
    python scripts/analyze.py --status
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from extract.pdf_text import split_pages  # noqa: E402
from lib import scope as scope_mod  # noqa: E402
from lib.capabilities import Capabilities, CapabilityError, gate  # noqa: E402
from lib.models import LibraryItem  # noqa: E402
from lib.note import UNDETERMINABLE, Note, build_frontmatter  # noqa: E402
from lib.paths import Corpus  # noqa: E402
from lib.state import State  # noqa: E402

#: Papers longer than this get the map-reduce treatment rather than one pass.
#: S4 measured a median of ~16.6k tokens per paper, so most fit comfortably in one pass.
MAP_REDUCE_CHARS = 120_000

#: How many papers the skill should analyze concurrently. Kept modest: the bottleneck is
#: reading quality, not throughput, and a large fan-out makes failures hard to attribute.
DEFAULT_CONCURRENCY = 4

#: Rough chars-per-token for cost estimation (matches the S4 methodology).
CHARS_PER_TOKEN = 4


@dataclass
class WorkUnit:
    """Everything needed to analyze one paper, assembled in one place."""

    citekey: str
    title: str
    scope_block: str
    scope_version: str
    text_path: str
    chars: int
    pages: int
    strategy: str                       # "single-pass" | "map-reduce"
    frontmatter: dict[str, Any] = field(default_factory=dict)
    user_notes: list[str] = field(default_factory=list)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    figures: dict[str, Any] | None = None
    #: Capability-driven sentences the note must contain verbatim when a source is absent.
    absence_notices: dict[str, str] = field(default_factory=dict)
    est_input_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def load_scope(corpus: Corpus) -> tuple[Any, str]:
    scope = scope_mod.load(corpus.config)
    if scope is None:
        return None, ""
    return scope, scope.version


def build_unit(corpus: Corpus, citekey: str, scope, caps: Capabilities) -> WorkUnit | None:
    """Assemble one paper's analysis input, or None when its text is missing."""
    text_path = corpus.text / f"{citekey}.md"
    if not text_path.is_file():
        return None
    markdown = text_path.read_text(encoding="utf-8")
    pages = split_pages(markdown)

    raw_path = corpus.raw / f"{citekey}.json"
    item_dict: dict[str, Any] = {}
    user_notes: list[str] = []
    annotations: list[dict[str, Any]] = []
    if raw_path.is_file():
        item_dict = json.loads(raw_path.read_text(encoding="utf-8"))
        item = LibraryItem.from_dict(item_dict)
        user_notes = [n.text for n in item.notes if n.text.strip()]
        annotations = [
            {"text": a.text, "comment": a.comment, "page": a.page_label or a.page_index,
             "color": a.color, "type": a.type}
            for a in item.annotations]

    figures = None
    figures_path = corpus.figures / citekey / "figures.json"
    if figures_path.is_file():
        figures = json.loads(figures_path.read_text(encoding="utf-8"))

    # Every absent source gets an explicit sentence rather than an empty section (P4).
    absence: dict[str, str] = {}
    if not caps.is_enabled("figures"):
        absence["Figures & tables"] = (
            "Figure extraction was not enabled for this run.")
    elif figures is None:
        absence["Figures & tables"] = (
            "Figure extraction has not been run for this paper.")
    elif not (figures.get("assets") or []):
        absence["Figures & tables"] = (
            "Figure extraction produced no assets for this paper.")
    if not annotations and not user_notes:
        absence["Your notes"] = (
            "No Zotero annotations or notes found for this paper.")
    elif not annotations:
        absence["Your notes"] = (
            "No Zotero annotations found for this paper; the notes below are your "
            "saved note items.")

    chars = len(markdown)
    frontmatter = build_frontmatter(
        {**item_dict, "citekey": citekey},
        scope_version=scope.version if scope else "",
        figures_extracted=len((figures or {}).get("assets") or []),
        has_user_notes=bool(user_notes or annotations))

    return WorkUnit(
        citekey=citekey,
        title=frontmatter.get("title") or citekey,
        scope_block=scope.prompt_block() if scope else "",
        scope_version=scope.version if scope else "",
        text_path=str(text_path),
        chars=chars,
        pages=len(pages),
        strategy="map-reduce" if chars > MAP_REDUCE_CHARS else "single-pass",
        frontmatter=frontmatter,
        user_notes=user_notes,
        annotations=annotations,
        figures=figures,
        absence_notices=absence,
        est_input_tokens=chars // CHARS_PER_TOKEN,
    )


def pending_citekeys(corpus: Corpus, state: State, scope_version: str,
                     force: bool = False) -> list[str]:
    """Papers needing analysis: never analyzed, failed, or analyzed under an older scope."""
    ready = state.citekeys_with("text")
    if force:
        return ready
    out = []
    for citekey in ready:
        record = state.stage(citekey, "analyze")
        if record.get("status") != "done":
            out.append(citekey)
        elif scope_version and record.get("scope_version") != scope_version:
            # Notes written to a different research question are stale, not reusable.
            out.append(citekey)
    return out


def accept_note(corpus: Corpus, state: State, citekey: str,
                scope_version: str) -> tuple[bool, list[str]]:
    """Validate a written note and checkpoint it. Refuses to accept a malformed note."""
    path = corpus.papers / f"{citekey}.md"
    if not path.is_file():
        return False, [f"no note written at {path}"]
    note = Note.parse(path.read_text(encoding="utf-8"), citekey=citekey)
    problems = note.validate()
    if problems:
        state.mark_error(citekey, "analyze", "; ".join(problems)[:500])
        state.save()
        return False, problems

    from verify import check_note
    report = check_note(corpus, citekey)
    if report.fabricated:
        detail = "; ".join(
            f"[{c.kind} {c.value}] {c.detail}" for c in report.fabricated[:3])
        # A fabricated locator is never acceptable (P7): the note is rejected, not warned about.
        state.mark_error(citekey, "analyze", f"fabricated locator(s): {detail}"[:500])
        state.save()
        return False, [f"fabricated locator: {c.detail}" for c in report.fabricated]

    flagged = [c for c in report.checks if c.verdict in ("weak", "unsupported")]
    state.mark_done(
        citekey, "analyze",
        scope_version=scope_version,
        locators=len(report.checks),
        flagged=len(flagged),
        confidence=note.frontmatter.get("confidence"),
        relevance=note.frontmatter.get("relevance"))
    state.save()
    return True, [f"{c.claim[:70]} (overlap {c.overlap:.2f})" for c in flagged]


# --- driver ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-analyze", description="Prepare and validate per-paper analysis.")
    parser.add_argument("--plan", action="store_true",
                        help="show what needs analyzing and the estimated cost")
    parser.add_argument("--next", type=int, metavar="N",
                        help="emit the next N work units as JSON")
    parser.add_argument("--unit", help="emit one paper's full analysis input")
    parser.add_argument("--accept", help="validate a written note and checkpoint it")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-analyze completed papers")
    parser.add_argument("--limit", type=int, help="cap the plan")
    parser.add_argument("--citekey", nargs="+", help="restrict to these papers")
    args = parser.parse_args(argv)

    caps = Capabilities.load()
    try:
        gate(["pdf_text"], caps)
    except CapabilityError as exc:
        print(exc, file=sys.stderr)
        return 2

    corpus = Corpus()
    if not corpus.text.is_dir():
        print("no extracted text found. Run /lit-ingest first.", file=sys.stderr)
        return 2
    state = State.load(corpus.state)
    scope, scope_version = load_scope(corpus)

    if args.accept:
        ok, messages = accept_note(corpus, state, args.accept, scope_version)
        print(json.dumps({"citekey": args.accept, "accepted": ok,
                          "problems" if not ok else "flagged_for_review": messages}, indent=2))
        return 0 if ok else 1

    todo = pending_citekeys(corpus, state, scope_version, force=args.force)
    if args.citekey:
        todo = [c for c in args.citekey if c in state.citekeys_with("text")]
    if args.limit:
        todo = todo[:args.limit]

    if args.status:
        counts = state.counts("analyze")
        print(json.dumps({
            "scope_version": scope_version,
            "scope_is_fixture": bool(scope and scope.is_fixture),
            "text_ready": len(state.citekeys_with("text")),
            "analyzed": counts["done"], "errors": counts["error"],
            "pending": len(todo),
        }, indent=2))
        return 0

    if args.unit:
        unit = build_unit(corpus, args.unit, scope, caps)
        if unit is None:
            print(f"no extracted text for {args.unit}", file=sys.stderr)
            return 2
        print(json.dumps(unit.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.next is not None:
        units = []
        for citekey in todo[:args.next]:
            if unit := build_unit(corpus, citekey, scope, caps):
                units.append(unit.to_dict())
        print(json.dumps({"concurrency": DEFAULT_CONCURRENCY, "units": units},
                         indent=2, ensure_ascii=False))
        return 0

    # Default: the plan.
    units = [u for c in todo if (u := build_unit(corpus, c, scope, caps))]
    total_tokens = sum(u.est_input_tokens for u in units)
    plan = {
        "scope_version": scope_version,
        "scope_is_fixture": bool(scope and scope.is_fixture),
        "scope_missing": scope is None,
        "pending": len(units),
        "already_analyzed": state.counts("analyze")["done"],
        "map_reduce": sum(1 for u in units if u.strategy == "map-reduce"),
        "est_input_tokens": total_tokens,
        "est_input_tokens_per_paper": (total_tokens // len(units)) if units else 0,
        "concurrency": DEFAULT_CONCURRENCY,
        "citekeys": [u.citekey for u in units],
    }
    if scope is None:
        plan["warning"] = ("No research scope is set. Analysis will produce generic "
                           "summaries rather than notes aimed at a question. Run /lit-scope.")
    elif scope.is_fixture:
        plan["warning"] = ("The active scope is a development fixture, not a real "
                           "interview. Run /lit-scope before trusting these notes.")
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
