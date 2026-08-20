"""Stage 1 (ingest) and Stage 2 (text extraction) - spec section 9.

    python scripts/ingest.py                    # ingest + extract, resuming
    python scripts/ingest.py --source zotero_sqlite
    python scripts/ingest.py --force            # redo completed work
    python scripts/ingest.py --limit 20         # bound the run
    python scripts/ingest.py --no-text          # stage 1 only
    python scripts/ingest.py --layout           # opt into pymupdf4llm extraction

Three guarantees this file exists to keep:

* **One bad item never fails the run** (spec section 9, stage 1). Errors are collected per
  item and reported at the end.
* **Every item is checkpointed as it completes** (P6), so killing the process and re-running
  resumes rather than restarting.
* **Every skip carries a reason** (P4). Nothing is dropped quietly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from adapters.base import Adapter, dedupe  # noqa: E402
from adapters.export_dir import ExportDirAdapter  # noqa: E402
from adapters.generic_pdf import GenericPdfAdapter  # noqa: E402
from adapters.zotero_api import ZoteroApiAdapter  # noqa: E402
from adapters.zotero_sqlite import ZoteroSqliteAdapter  # noqa: E402
from extract import pdf_text  # noqa: E402
from lib.capabilities import Capabilities, CapabilityError, gate  # noqa: E402
from lib.citekey import CitekeyAllocator  # noqa: E402
from lib.models import LibraryItem  # noqa: E402
from lib.paths import Corpus  # noqa: E402
from lib.state import State, file_hash  # noqa: E402

ADAPTERS: dict[str, type[Adapter]] = {
    "zotero_api": ZoteroApiAdapter,
    "zotero_sqlite": ZoteroSqliteAdapter,
    "export_dir": ExportDirAdapter,
    "generic_pdf": GenericPdfAdapter,
}


def build_adapter(name: str, config: dict[str, Any]) -> Adapter:
    if name not in ADAPTERS:
        raise SystemExit(f"unknown adapter '{name}'. Known: {', '.join(ADAPTERS)}")
    return ADAPTERS[name](config)


# --- stage 1 ---------------------------------------------------------------


def ingest(corpus: Corpus, state: State, adapter: Adapter, run,
           limit: int | None = None, force: bool = False) -> list[LibraryItem]:
    """Adapter -> ``raw/``. Resolves citekeys, dedupes, reports items with missing PDFs."""
    result = adapter.fetch(limit=limit)
    for source_id, message in result.errors:
        run.errors += 1
        run.notes.append(f"adapter error [{source_id or 'adapter'}]: {message}")
    for key in ("schema_warning", "unmatched_warning", "unidentified_warning",
                "offline_note"):
        if note := result.info.get(key):
            run.notes.append(note)

    report = dedupe(result.items)
    if report.merged:
        run.notes.append(report.summary())
        for kept, dropped, kind, value in report.merged:
            run.notes.append(f"  merged {dropped} into {kept} (matched on {kind}: {value[:60]})")

    allocator = CitekeyAllocator(state.citekeys)
    items: list[LibraryItem] = []
    for item in report.kept:
        item.citekey = allocator.allocate(item, preferred=item.citekey or None)
        items.append(item)
    state.record_citekeys(allocator.mapping())

    for item in items:
        state.set_source_id(item.citekey, item.source_id)
        raw_path = corpus.raw / f"{item.citekey}.json"

        if not force and state.is_done(item.citekey, "ingest") and raw_path.is_file():
            run.skipped += 1
            continue

        try:
            item.write_json(raw_path)
        except OSError as exc:
            state.mark_error(item.citekey, "ingest", f"could not write raw record: {exc}")
            run.errors += 1
            state.save()
            continue

        if item.has_pdf:
            state.mark_done(item.citekey, "ingest", pdfs=len(item.pdfs),
                            warnings=item.warnings or None)
            run.processed += 1
        else:
            # Not an error - plenty of library items legitimately have no PDF - but it
            # must be visible, because these items cannot be analyzed (P4).
            state.mark_skipped(item.citekey, "ingest",
                               reason="; ".join(item.warnings) or "no PDF attachment")
            run.skipped += 1
        state.save()      # after every item, so an interrupted run stays resumable (P6)

    run.notes.append(
        f"adapter '{adapter.name}' read {result.info.get('items_read', len(result.items))} "
        f"item(s); {sum(1 for i in items if i.has_pdf)} of {len(items)} have a PDF")
    if result.info.get("source_unmodified"):
        run.notes.append("verified the source library was not modified during the read")
    return items


# --- stage 2 ---------------------------------------------------------------


def extract_text(corpus: Corpus, state: State, items: list[LibraryItem], run,
                 force: bool = False, layout: bool = False) -> None:
    """``raw/`` -> ``text/<citekey>.md``, preserving page markers."""
    for item in items:
        citekey = item.citekey
        if not item.has_pdf:
            state.mark_skipped(citekey, "text", reason="no PDF attachment to extract")
            state.save()
            continue

        pdf = item.pdfs[0]
        try:
            source_hash = file_hash(pdf)
        except OSError as exc:
            state.mark_error(citekey, "text", f"could not read PDF: {exc}")
            run.errors += 1
            state.save()
            continue

        target = corpus.text / f"{citekey}.md"
        if not force and state.is_done(citekey, "text", source_hash) and target.is_file():
            run.skipped += 1
            continue

        result = pdf_text.extract(pdf, citekey=citekey, layout=layout)
        if result.error:
            state.mark_error(citekey, "text", result.error, hash=source_hash)
            run.errors += 1
            state.save()
            continue

        if result.scanned:
            # Degrade loudly: a scanned PDF yields no usable text, and a note built on it
            # would be silently empty (P4).
            state.mark_skipped(
                citekey, "text",
                reason=(f"appears to be a scanned PDF ({result.chars} characters across "
                        f"{result.pages} pages). OCR it with ocrmypdf and re-run."),
                hash=source_hash, pages=result.pages, chars=result.chars)
            run.skipped += 1
            state.save()
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result.markdown, encoding="utf-8")
        except OSError as exc:
            state.mark_error(citekey, "text", f"could not write extracted text: {exc}")
            run.errors += 1
            state.save()
            continue

        state.mark_done(citekey, "text", hash=source_hash, pages=result.pages,
                        chars=result.chars, headings=len(result.headings),
                        extractor=result.extractor)
        run.processed += 1
        state.save()


# --- reporting -------------------------------------------------------------


def format_report(state: State, run, corpus: Corpus) -> str:
    lines = ["", "lit-agent ingest report", "=" * 60, ""]
    for stage in ("ingest", "text"):
        counts = state.counts(stage)
        lines.append(f"  {stage:8} done {counts['done']:4}   skipped {counts['skipped']:4}   "
                     f"error {counts['error']:4}   pending {counts['pending']:4}")
    lines.append("")

    if errors := state.errors():
        lines.append(f"Errors ({len(errors)}) - these items were skipped, the run continued:")
        for citekey, stage, message in errors[:15]:
            lines.append(f"  [{stage}] {citekey}: {message[:100]}")
        if len(errors) > 15:
            lines.append(f"  ... and {len(errors) - 15} more (see {corpus.state})")
        lines.append("")

    if skips := state.skips():
        by_reason: dict[str, int] = {}
        for _, _, reason in skips:
            key = reason.split(";")[0][:60]
            by_reason[key] = by_reason.get(key, 0) + 1
        lines.append(f"Skipped ({len(skips)}), by reason:")
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:4}  {reason}")
        lines.append("")

    if run.notes:
        lines.append("Notes:")
        lines.extend(f"  {note}" for note in run.notes[:25])
        if len(run.notes) > 25:
            lines.append(f"  ... and {len(run.notes) - 25} more")
        lines.append("")

    ready = len(state.citekeys_with("text"))
    lines.append(f"{ready} paper(s) have extracted text and are ready for /lit-analyze.")
    lines.append("")
    return "\n".join(lines)


# --- driver ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-ingest", description="Pull a library into the corpus and extract text.")
    parser.add_argument("--source", choices=sorted(ADAPTERS),
                        help="override the configured adapter")
    parser.add_argument("--path", help="path for export_dir / generic_pdf adapters")
    parser.add_argument("--limit", type=int, help="stop after this many items")
    parser.add_argument("--force", action="store_true", help="redo completed work")
    parser.add_argument("--no-text", action="store_true", help="stage 1 only")
    parser.add_argument("--layout", action="store_true",
                        help="use pymupdf4llm (slow; see docs/decisions/0001)")
    parser.add_argument("--resolve-online", action="store_true",
                        help="generic_pdf only: resolve metadata via Crossref/arXiv/OpenAlex")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    caps = Capabilities.load()
    required = ["source"] if args.no_text else ["source", "pdf_text"]
    try:
        gate(required, caps)
    except CapabilityError as exc:
        print(exc, file=sys.stderr)
        return 2

    source_config = dict(caps.get("source").config)
    adapter_name = args.source or source_config.get("adapter")
    if not adapter_name:
        print("No source adapter configured. Run /lit-setup.", file=sys.stderr)
        return 2
    if args.path:
        source_config["path"] = args.path
    if args.resolve_online:
        source_config["resolve_online"] = True

    corpus = Corpus()
    corpus.ensure()
    state = State.load(corpus.state)
    run = state.start_run("lit-ingest")
    if state.load_error:
        run.notes.append(state.load_error)

    adapter = build_adapter(adapter_name, source_config)
    ok, reason = adapter.available()
    if not ok:
        print(f"Source '{adapter_name}' is not usable: {reason}", file=sys.stderr)
        print("Run /lit-doctor, or /lit-setup --reconfigure source.", file=sys.stderr)
        return 2

    items = ingest(corpus, state, adapter, run, limit=args.limit, force=args.force)
    if not args.no_text:
        extract_text(corpus, state, items, run, force=args.force, layout=args.layout)
    state.finish_run(run)

    if args.json:
        print(json.dumps({
            "adapter": adapter_name,
            "items": len(items),
            "processed": run.processed, "skipped": run.skipped, "errors": run.errors,
            "ready_for_analysis": len(state.citekeys_with("text")),
            "notes": run.notes,
        }, indent=2))
    else:
        print(format_report(state, run, corpus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
