"""Export the corpus as a browsable literature-review folder.

``.lit/`` is a working directory: flat, citekey-keyed, and organized for the pipeline rather
than for a person. This builds the human-facing view of the same data -- Zotero's collection
structure, with each paper's PDF and its summary sitting in the same folder.

**It is a projection, never a source (P5).** Everything here is rebuildable from ``.lit/``,
so the export can be deleted and regenerated at will. Nothing is ever read back from it.

    python scripts/export.py                          # build or refresh
    python scripts/export.py --dest ~/lit-review
    python scripts/export.py --filter relevance=high
    python scripts/export.py --no-pdfs                # summaries only

Layout::

    literature-review/
    |-- README.md                  navigable index
    |-- ToSDR LLM/
    |   `-- chen2025llms/
    |       |-- chen2025llms.pdf
    |       |-- SUMMARY.md         the per-paper note
    |       |-- fulltext.md        extracted text with page markers
    |       |-- metadata.json
    |       `-- figures/
    |-- _synthesis/                themes, gaps, contradictions, draft, refs.bib
    `-- _unfiled/                  papers in no collection
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from ask import Corpus_Index, apply_filters  # noqa: E402
from lib.models import LibraryItem  # noqa: E402
from lib.paths import Corpus  # noqa: E402
from lib.state import State  # noqa: E402

DEFAULT_DEST = "literature-review"
SYNTHESIS_DIR = "_synthesis"
UNFILED_DIR = "_unfiled"

#: Windows forbids these in a path component, and Zotero collection names contain them.
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: Windows also refuses these names outright, whatever the extension.
RESERVED = frozenset("""
CON PRN AUX NUL COM1 COM2 COM3 COM4 COM5 COM6 COM7 COM8 COM9
LPT1 LPT2 LPT3 LPT4 LPT5 LPT6 LPT7 LPT8 LPT9
""".split())


def link(path: str) -> str:
    """Percent-encode a relative path for a markdown link.

    Collection names contain spaces ("ToSDR LLM", "Social media apps"). An unescaped space
    breaks the link in most markdown viewers, which would make the navigable index
    unnavigable -- the one thing it exists to be.
    """
    from urllib.parse import quote
    return quote(path, safe="/#")


def safe_name(name: str, fallback: str = "unnamed") -> str:
    """A folder name that is legal on Windows, macOS, and Linux alike."""
    cleaned = ILLEGAL.sub("-", name).strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("- ")
    if cleaned.upper() in RESERVED:
        cleaned = f"{cleaned}-"
    return cleaned[:80] or fallback


@dataclass
class ExportReport:
    dest: Path
    papers: int = 0
    collections: int = 0
    pdfs_copied: int = 0
    pdfs_reused: int = 0
    pdfs_missing: list[str] = field(default_factory=list)
    unanalyzed: list[str] = field(default_factory=list)
    cross_filed: list[tuple[str, str]] = field(default_factory=list)
    removed: int = 0
    notes: list[str] = field(default_factory=list)


def _collections_for(item: LibraryItem) -> list[str]:
    return sorted({safe_name(c) for c in item.collections if c.strip()}) or [UNFILED_DIR]


def _copy_if_changed(source: Path, target: Path) -> bool:
    """Copy only when the target is absent or differs in size. Returns True if copied."""
    if target.exists():
        try:
            if target.stat().st_size == source.stat().st_size:
                return False
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def export(corpus: Corpus, dest: Path, filters: list[str] | None = None,
           include_pdfs: bool = True, prune: bool = True) -> ExportReport:
    report = ExportReport(dest=dest)
    index = Corpus_Index.load(corpus)
    state = State.load(corpus.state)

    # Every ingested paper is exported, not only the analyzed ones -- a paper whose summary
    # is missing still belongs in the folder, with a note saying why (P4).
    raw_files = sorted(corpus.raw.glob("*.json")) if corpus.raw.is_dir() else []
    citekeys = [p.stem for p in raw_files]
    if filters:
        kept, messages = apply_filters(index, filters)
        report.notes.extend(messages)
        citekeys = [c for c in citekeys if c in set(kept)]

    dest.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    collections: dict[str, list[str]] = {}

    for citekey in citekeys:
        try:
            item = LibraryItem.from_dict(
                json.loads((corpus.raw / f"{citekey}.json").read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            report.notes.append(f"could not read {citekey}: {exc}")
            continue

        homes = _collections_for(item)
        primary, others = homes[0], homes[1:]
        collections.setdefault(primary, []).append(citekey)

        folder = dest / primary / citekey
        folder.mkdir(parents=True, exist_ok=True)
        expected.add(folder)
        report.papers += 1

        # --- the PDF
        if include_pdfs:
            if item.pdfs and item.pdfs[0].is_file():
                target = folder / f"{citekey}.pdf"
                if _copy_if_changed(item.pdfs[0], target):
                    report.pdfs_copied += 1
                else:
                    report.pdfs_reused += 1
            else:
                report.pdfs_missing.append(citekey)

        # --- the summary
        note_path = corpus.papers / f"{citekey}.md"
        summary = folder / "SUMMARY.md"
        if note_path.is_file():
            summary.write_text(note_path.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            report.unanalyzed.append(citekey)
            summary.write_text(
                _placeholder_summary(citekey, item, state,
                                     has_text=(corpus.text / f"{citekey}.md").is_file()),
                encoding="utf-8")

        # --- extracted text and metadata
        text_path = corpus.text / f"{citekey}.md"
        if text_path.is_file():
            (folder / "fulltext.md").write_text(
                text_path.read_text(encoding="utf-8"), encoding="utf-8")
        (folder / "metadata.json").write_text(
            json.dumps(item.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8")

        figures_src = corpus.figures / citekey
        if figures_src.is_dir() and any(figures_src.iterdir()):
            shutil.copytree(figures_src, folder / "figures", dirs_exist_ok=True)

        # --- a pointer in every other collection the paper belongs to
        for other in others:
            collections.setdefault(other, [])
            pointer_dir = dest / other
            pointer_dir.mkdir(parents=True, exist_ok=True)
            pointer = pointer_dir / f"{citekey}.md"
            relative = link((Path("..") / primary / citekey).as_posix())
            pointer.write_text(
                f"# {item.title or citekey}\n\n"
                f"Also filed under **{primary}**, where the PDF and summary live:\n\n"
                f"- [Open the paper folder]({relative}/)\n"
                f"- [Read the summary]({relative}/SUMMARY.md)\n\n"
                f"*(Zotero files this paper in more than one collection. Rather than "
                f"duplicating a {item.pdfs[0].stat().st_size // 1024 if item.pdfs and item.pdfs[0].is_file() else 0} KB "
                f"PDF, lit-agent keeps one copy and points here.)*\n",
                encoding="utf-8")
            expected.add(pointer)
            report.cross_filed.append((citekey, other))

    # --- synthesis
    synthesis_dest = dest / SYNTHESIS_DIR
    if corpus.synthesis.is_dir() and any(corpus.synthesis.glob("*.md")):
        synthesis_dest.mkdir(parents=True, exist_ok=True)
        for path in sorted(corpus.synthesis.glob("*.md")):
            (synthesis_dest / path.name).write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        if corpus.refs_bib.is_file():
            (synthesis_dest / "refs.bib").write_text(
                corpus.refs_bib.read_text(encoding="utf-8"), encoding="utf-8")
    if corpus.scope_md.is_file():
        synthesis_dest.mkdir(parents=True, exist_ok=True)
        (synthesis_dest / "scope.md").write_text(
            corpus.scope_md.read_text(encoding="utf-8"), encoding="utf-8")

    report.collections = len([c for c in collections if c != UNFILED_DIR])
    (dest / "README.md").write_text(
        _build_readme(dest, index, collections, report, corpus), encoding="utf-8")

    if prune:
        report.removed = _prune(dest, expected)
    return report


def _placeholder_summary(citekey: str, item: LibraryItem, state: State,
                         has_text: bool = False) -> str:
    """A paper without a note gets an explicit reason, not an empty file (P4).

    The reason is drawn from what is actually on disk first and the checkpoint record
    second: a corpus can be copied or a state file reset, and a placeholder that contradicts
    the files sitting next to it is worse than no placeholder.
    """
    text_record = state.stage(citekey, "text")
    status = text_record.get("status", "done" if has_text else "pending")
    if status == "skipped":
        reason = text_record.get("reason", "text extraction was skipped")
        why = f"Text extraction was skipped for this paper: {reason}"
    elif status == "error":
        why = f"Text extraction failed: {text_record.get('error', 'unknown error')}"
    elif status == "done":
        why = ("The text was extracted but this paper has not been analyzed yet. "
               "Run `/lit-analyze` to write the summary.")
    else:
        why = "This paper has not been ingested past the metadata stage."

    return (f"# {item.title or citekey}\n\n"
            f"`{citekey}`"
            + (f" · {', '.join(item.authors[:3])}" if item.authors else "")
            + (f" · {item.year}" if item.year else "") + "\n\n"
            f"> **No summary yet.** {why}\n\n"
            f"The PDF and any extracted text are in this folder. This placeholder exists so "
            f"an unanalyzed paper is visibly unanalyzed, rather than an empty folder that "
            f"looks finished.\n")


def _build_readme(dest: Path, index: Corpus_Index, collections: dict[str, list[str]],
                  report: ExportReport, corpus: Corpus) -> str:
    lines = [
        "# Literature review",
        "",
        f"{report.papers} paper(s) across {report.collections} collection(s), mirroring the "
        "structure of the Zotero library.",
        "",
        "Each paper folder holds its PDF, `SUMMARY.md` (the analysis), `fulltext.md` "
        "(extracted text with page markers), and `metadata.json`.",
        "",
        "> Generated by lit-agent from `.lit/`. **This folder is a projection, not a "
        "source** — it can be deleted and rebuilt with `/lit-export` at any time. Edit the "
        "notes in `.lit/papers/`, not here.",
        "",
    ]

    if (dest / SYNTHESIS_DIR).is_dir():
        lines += ["## Synthesis", ""]
        titles = {
            "review-draft.md": "Drafted related-work section",
            "themes.md": "Thematic clusters",
            "contradictions.md": "Contradictions between papers",
            "gaps.md": "Gap analysis",
            "methods-matrix.md": "Methods matrix",
            "index.md": "Master index of all papers",
            "scope.md": "The research scope these outputs were written to",
            "refs.bib": "BibTeX for the cited subset",
        }
        for name, label in titles.items():
            if (dest / SYNTHESIS_DIR / name).is_file():
                lines.append(f"- [{label}]({SYNTHESIS_DIR}/{name})")
        lines.append("")

    pointers: dict[str, list[tuple[str, str]]] = {}
    for citekey, other in report.cross_filed:
        pointers.setdefault(other, []).append((citekey, ""))

    for collection in sorted(set(collections) | set(pointers),
                             key=lambda c: (c == UNFILED_DIR, c.lower())):
        citekeys = sorted(collections.get(collection, []))
        also_here = sorted(k for k, _ in pointers.get(collection, []))
        if not citekeys and not also_here:
            continue
        heading = "Unfiled" if collection == UNFILED_DIR else collection
        lines += ["", f"## {heading}  ({len(citekeys) + len(also_here)})", ""]
        if not citekeys:
            # Every paper here is filed primarily elsewhere; say so rather than showing
            # a collection that looks empty.
            lines.append("*Every paper in this collection is stored under another "
                         "collection. Links below go to where each one lives.*")
            lines.append("")
        lines.append("| paper | year | relevance | summary |")
        lines.append("|---|---|---|---|")
        for citekey in citekeys:
            note = index.notes.get(citekey)
            fm = note.frontmatter if note else {}
            title = str(fm.get("title") or citekey)[:70].replace("|", "\\|")
            year = fm.get("year") or "—"
            relevance = fm.get("relevance") or ("—" if note else "not analyzed")
            folder = link(f"{collection}/{citekey}")
            lines.append(f"| [{title}]({folder}/) | {year} | {relevance} "
                         f"| [SUMMARY]({folder}/SUMMARY.md) |")
        for citekey in also_here:
            note = index.notes.get(citekey)
            fm = note.frontmatter if note else {}
            title = str(fm.get("title") or citekey)[:70].replace("|", r"\|")
            pointer = link(f"{collection}/{citekey}.md")
            lines.append(f"| [{title}]({pointer}) | {fm.get('year') or '—'} "
                         f"| {fm.get('relevance') or '—'} | filed elsewhere |")

    if report.unanalyzed or report.pdfs_missing:
        lines += ["", "## Known gaps", ""]
        if report.unanalyzed:
            lines.append(f"- **{len(report.unanalyzed)} paper(s) have no summary yet.** "
                         "Each carries a placeholder saying why. Run `/lit-analyze`.")
        if report.pdfs_missing:
            lines.append(f"- **{len(report.pdfs_missing)} paper(s) have no PDF** in the "
                         "library, so only metadata was exported: "
                         + ", ".join(f"`{c}`" for c in report.pdfs_missing[:10])
                         + (" …" if len(report.pdfs_missing) > 10 else ""))
    lines.append("")
    return "\n".join(lines)


def _prune(dest: Path, expected: set[Path]) -> int:
    """Remove paper folders that no longer correspond to anything in the corpus.

    Only touches directories that look like exported paper folders -- ones containing a
    ``SUMMARY.md`` -- so a stray file a user dropped in here is never deleted.
    """
    removed = 0
    for summary in list(dest.rglob("SUMMARY.md")):
        folder = summary.parent
        if folder in expected or SYNTHESIS_DIR in folder.parts:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed


def format_report(report: ExportReport) -> str:
    lines = ["", f"Literature review folder: {report.dest}", "=" * 60, "",
             f"  papers        {report.papers}",
             f"  collections   {report.collections}",
             f"  PDFs copied   {report.pdfs_copied}"
             + (f" ({report.pdfs_reused} already current)" if report.pdfs_reused else ""),
             ]
    if report.cross_filed:
        lines.append(f"  cross-filed   {len(report.cross_filed)} pointer(s) for papers in "
                     "more than one collection")
    if report.removed:
        lines.append(f"  pruned        {report.removed} stale paper folder(s)")
    lines.append("")
    if report.unanalyzed:
        lines.append(f"  {len(report.unanalyzed)} paper(s) have no summary yet — each has a "
                     "placeholder explaining why. Run /lit-analyze.")
    if report.pdfs_missing:
        lines.append(f"  {len(report.pdfs_missing)} paper(s) have no PDF in the library: "
                     + ", ".join(report.pdfs_missing[:6])
                     + (" …" if len(report.pdfs_missing) > 6 else ""))
    for note in report.notes:
        lines.append(f"  note: {note}")
    lines += ["", f"  Start at {report.dest / 'README.md'}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-export",
        description="Build a browsable literature-review folder from the corpus.")
    parser.add_argument("--dest", default=DEFAULT_DEST,
                        help=f"where to build it (default: ./{DEFAULT_DEST})")
    parser.add_argument("--filter", nargs="+", default=[], metavar="EXPR")
    parser.add_argument("--no-pdfs", action="store_true",
                        help="write summaries and text only, no PDF copies")
    parser.add_argument("--no-prune", action="store_true",
                        help="keep paper folders that are no longer in the corpus")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    corpus = Corpus()
    if not corpus.raw.is_dir() or not any(corpus.raw.glob("*.json")):
        print("Nothing to export. Run /lit-ingest first.", file=sys.stderr)
        return 2

    try:
        report = export(corpus, Path(args.dest).expanduser().resolve(),
                        filters=args.filter, include_pdfs=not args.no_pdfs,
                        prune=not args.no_prune)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        # A traceback that still exits 0 is a silent failure (P4): a caller sees success and
        # a stale folder. Report it as an error and say the folder may be incomplete.
        print(f"export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"The folder at {args.dest} may be incomplete or stale.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "dest": str(report.dest), "papers": report.papers,
            "collections": report.collections, "pdfs_copied": report.pdfs_copied,
            "pdfs_reused": report.pdfs_reused, "pdfs_missing": report.pdfs_missing,
            "unanalyzed": report.unanalyzed, "pruned": report.removed,
            "cross_filed": [list(c) for c in report.cross_filed],
            "notes": report.notes,
        }, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
