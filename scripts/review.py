"""Stage 6 - cross-paper synthesis (spec section 13).

Two kinds of artifact, and the split is deliberate.

**Mechanical** - ``index.md``, ``methods-matrix.md``, ``refs.bib``. These are projections of
frontmatter. Generating them in Python makes them exact, reproducible, and free, and stops a
model from paraphrasing a year or dropping a dataset.

**Judgement** - ``themes.md``, ``gaps.md``, ``contradictions.md``, ``review-draft.md``. These
need reading across papers, so the `lit-review` skill writes them. This module supplies the
inputs and then **enforces the rules the spec puts on them**:

* every ``contradictions.md`` entry needs **two verified locators or it does not ship**;
* ``gaps.md`` must distinguish "no one has done this" from "not in this library";
* every claim in ``review-draft.md`` carries a locator or ``[UNVERIFIED]``.

    python scripts/review.py --generate            # the mechanical artifacts
    python scripts/review.py --brief               # inputs for the judgement artifacts
    python scripts/review.py --check               # enforce the rules on what was written
    python scripts/review.py --generate --filter relevance=high year>=2020
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from ask import Corpus_Index, apply_filters  # noqa: E402
from lib.note import ANY_LOCATOR, PAGE_LOCATOR, UNVERIFIED, Note  # noqa: E402
from lib.paths import Corpus  # noqa: E402

#: Files the skill writes rather than this module.
JUDGEMENT_ARTIFACTS = ("themes.md", "gaps.md", "contradictions.md", "review-draft.md")

#: A section carrying this marker is prose about the artifact rather than an entry in it --
#: a "Rejected candidates" list, a methodology note, a preamble. Recording what was
#: considered and dropped is valuable (P4), so it needs a way to sit in the file without
#: being validated as a claim.
NOT_AN_ENTRY = "<!-- not-an-entry -->"

#: A gaps.md entry must be attributable to one of these. Spec section 13: "Distinguish
#: 'no one has done this' from 'not in this library' - the second is far more likely."
GAP_KINDS = ("not-in-this-library", "not-in-the-literature", "unresolved")

#: ``item_type`` in a note's frontmatter may be a CSL type or the Zotero type it came from,
#: depending on which adapter produced it, so both spellings map here. Getting this wrong
#: silently downgrades conference papers to @misc and files their venue under `journal`.
BIB_TYPE = {
    # CSL-JSON
    "article-journal": "article", "paper-conference": "inproceedings",
    "chapter": "incollection", "book": "book", "thesis": "phdthesis",
    "report": "techreport", "article": "misc", "webpage": "misc",
    "document": "misc", "dataset": "misc", "manuscript": "unpublished",
    # Zotero
    "journalArticle": "article", "conferencePaper": "inproceedings",
    "bookSection": "incollection", "preprint": "misc", "presentation": "misc",
}

#: BibTeX entry types whose venue field is ``booktitle`` rather than ``journal``.
BOOKTITLE_TYPES = frozenset({"inproceedings", "incollection", "conference"})


# --- mechanical artifacts --------------------------------------------------


def _escape(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _first_line(note: Note) -> str:
    body = (note.sections.get("One-line summary") or "").strip()
    for line in body.splitlines():
        if stripped := line.strip():
            return ANY_LOCATOR.sub("", stripped).strip()
    return ""


def build_index(index: Corpus_Index, citekeys: list[str]) -> str:
    """The master table (spec section 13)."""
    rows = [
        "# Corpus index",
        "",
        f"{len(citekeys)} paper(s). Generated from note frontmatter; edit the notes, not "
        "this file.",
        "",
        "| citekey | title | year | venue | type | methods | relevance | one-liner |",
        "|---|---|---|---|---|---|---|---|",
    ]
    def sort_key(ck: str):
        fm = index.notes[ck].frontmatter
        order = {"high": 0, "medium": 1, "low": 2, "tangential": 3}
        return (order.get(str(fm.get("relevance")), 4), -(fm.get("year") or 0), ck)

    for citekey in sorted(citekeys, key=sort_key):
        note = index.notes[citekey]
        fm = note.frontmatter
        methods = ", ".join(str(m) for m in (fm.get("methods") or [])[:3])
        rows.append(
            f"| `{citekey}` | {_escape(fm.get('title'))[:70]} | {fm.get('year') or 'n.d.'} "
            f"| {_escape(fm.get('venue'))[:30] or '—'} | {fm.get('paper_type') or '—'} "
            f"| {_escape(methods)[:60]} | {fm.get('relevance') or '—'} "
            f"| {_escape(_first_line(note))[:110]} |")

    rows += ["", "## Coverage", ""]
    for label, key in (("Relevance", "relevance"), ("Paper type", "paper_type"),
                       ("Confidence", "confidence")):
        counts = Counter(str(index.notes[c].frontmatter.get(key) or "unset")
                         for c in citekeys)
        rows.append(f"- **{label}:** "
                    + ", ".join(f"{v} {k}" for k, v in counts.most_common()))

    years = [index.notes[c].frontmatter.get("year") for c in citekeys]
    if valid := [y for y in years if isinstance(y, int)]:
        rows.append(f"- **Years:** {min(valid)}–{max(valid)}")
    if missing := [c for c in citekeys if not isinstance(
            index.notes[c].frontmatter.get("year"), int)]:
        # Stated, not hidden: a missing year silently skews any temporal claim (P4).
        rows.append(f"- **No year recorded:** {', '.join(f'`{c}`' for c in missing)}")
    return "\n".join(rows) + "\n"


def build_methods_matrix(index: Corpus_Index, citekeys: list[str]) -> str:
    """Papers by method, dataset, metric, headline result (spec section 13)."""
    rows = [
        "# Methods matrix",
        "",
        "Built from note frontmatter plus the Evaluation section. A blank cell means the "
        "field was not recorded in the note, **not** that the paper lacks it.",
        "",
        "| citekey | type | methods | datasets | metrics | headline result |",
        "|---|---|---|---|---|---|",
    ]
    for citekey in sorted(citekeys):
        note = index.notes[citekey]
        fm = note.frontmatter
        rows.append(
            f"| `{citekey}` | {fm.get('paper_type') or '—'} "
            f"| {_escape('; '.join(str(m) for m in (fm.get('methods') or [])))[:80] or '—'} "
            f"| {_escape(', '.join(str(d) for d in (fm.get('datasets') or [])))[:50] or '—'} "
            f"| {_escape(', '.join(str(m) for m in (fm.get('metrics') or [])))[:50] or '—'} "
            f"| {_escape(_headline(note))[:110] or '—'} |")

    rows += ["", "## Datasets across the corpus", ""]
    datasets: dict[str, list[str]] = defaultdict(list)
    for citekey in citekeys:
        for dataset in index.notes[citekey].frontmatter.get("datasets") or []:
            datasets[str(dataset)].append(citekey)
    for dataset, users in sorted(datasets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rows.append(f"- **{dataset}** — {', '.join(f'`{u}`' for u in sorted(users))}")

    if unrecorded := [c for c in citekeys
                      if not (index.notes[c].frontmatter.get("methods") or [])]:
        rows += ["", f"**No methods recorded:** {', '.join(f'`{c}`' for c in unrecorded)}. "
                     "Re-run `/lit-analyze --force` on these to fill the field."]
    return "\n".join(rows) + "\n"


def _headline(note: Note) -> str:
    """The first bulleted, locator-carrying line from Evaluation or Key findings."""
    for section in ("Evaluation", "Key findings"):
        for raw in (note.sections.get(section) or "").splitlines():
            line = raw.strip()
            if line.startswith(("-", "*")) and PAGE_LOCATOR.search(line):
                return re.sub(r"\*\*", "", line.lstrip("-* ").strip())
    return ""


def build_bibtex(index: Corpus_Index, citekeys: list[str]) -> str:
    """BibTeX for exactly the cited subset, using the same citekeys (spec section 13)."""
    entries = []
    for citekey in sorted(citekeys):
        fm = index.notes[citekey].frontmatter
        kind = BIB_TYPE.get(str(fm.get("item_type")), "misc")
        fields: list[tuple[str, str]] = []
        if title := fm.get("title"):
            fields.append(("title", f"{{{title}}}"))
        if authors := fm.get("authors"):
            fields.append(("author", " and ".join(str(a) for a in authors)))
        if year := fm.get("year"):
            fields.append(("year", str(year)))
        if venue := fm.get("venue"):
            key = "booktitle" if kind in BOOKTITLE_TYPES else "journal"
            fields.append((key, str(venue)))
        if doi := fm.get("doi"):
            fields.append(("doi", str(doi)))
        if arxiv := fm.get("arxiv_id"):
            fields.append(("eprint", str(arxiv)))
            fields.append(("archiveprefix", "arXiv"))
        body = ",\n".join(f"  {k} = {{{v}}}" if not v.startswith("{") else f"  {k} = {v}"
                          for k, v in fields)
        entries.append(f"@{kind}{{{citekey},\n{body}\n}}")
    header = ("% Generated by lit-agent from .lit/papers/ frontmatter.\n"
              "% Citekeys match the notes and the drafted prose exactly.\n")
    return header + "\n\n".join(entries) + "\n"


# --- the brief for judgement artifacts -------------------------------------


def build_brief(index: Corpus_Index, citekeys: list[str],
                scope_block: str) -> dict[str, Any]:
    """Everything the skill needs to write the judgement artifacts."""
    connections: list[dict[str, str]] = []
    contradiction_candidates: list[dict[str, Any]] = []
    open_questions: list[dict[str, str]] = []
    unverified: list[dict[str, str]] = []

    for citekey in sorted(citekeys):
        note = index.notes[citekey]
        for raw in (note.sections.get("Connections") or "").splitlines():
            line = raw.strip().lstrip("-* ").strip()
            if not line:
                continue
            kind = next((k for k in ("contradicts", "extends", "uses-method-of",
                                     "superseded-by") if line.lower().startswith(f"**{k}")
                         or line.lower().startswith(k)), "")
            targets = re.findall(r"\[\[([\w:-]+)\]\]", line)
            entry = {"from": citekey, "kind": kind or "unclassified",
                     "targets": targets, "text": line[:400]}
            connections.append(entry)
            if kind == "contradicts" and targets:
                contradiction_candidates.append(entry)

        for raw in (note.sections.get("Open questions") or "").splitlines():
            line = raw.strip().lstrip("-* ").strip()
            if len(line) > 30:
                open_questions.append({"citekey": citekey, "text": line[:300]})

        for name, body in note.sections.items():
            for raw in body.splitlines():
                if UNVERIFIED in raw:
                    unverified.append({"citekey": citekey, "section": name,
                                       "text": raw.strip()[:200]})

    tags = Counter()
    for citekey in citekeys:
        for tag in index.notes[citekey].frontmatter.get("scope_tags") or []:
            tags[str(tag)] += 1

    return {
        "scope_block": scope_block,
        "papers": [{
            "citekey": c,
            "title": index.notes[c].frontmatter.get("title"),
            "year": index.notes[c].frontmatter.get("year"),
            "paper_type": index.notes[c].frontmatter.get("paper_type"),
            "relevance": index.notes[c].frontmatter.get("relevance"),
            "confidence": index.notes[c].frontmatter.get("confidence"),
            "scope_tags": index.notes[c].frontmatter.get("scope_tags") or [],
            "one_liner": _first_line(index.notes[c]),
        } for c in sorted(citekeys)],
        "scope_tag_counts": tags.most_common(),
        "connections": connections,
        "contradiction_candidates": contradiction_candidates,
        "open_questions": open_questions,
        "unverified_claims": unverified,
        "low_confidence": [c for c in citekeys
                           if index.notes[c].frontmatter.get("confidence") != "high"],
    }


# --- enforcement -----------------------------------------------------------


@dataclass
class CheckResult:
    artifact: str
    problems: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def check_contradictions(corpus: Corpus, index: Corpus_Index) -> CheckResult:
    """**Every entry needs two verified locators or it does not ship** (spec section 13).

    High value, high fabrication risk: an invented contradiction between two real papers is
    the most damaging thing this project could emit, because it looks like scholarship.
    """
    result = CheckResult("contradictions.md")
    path = corpus.synthesis / "contradictions.md"
    if not path.is_file():
        return result

    from verify import check_locator, load_adjudications, split_pages, split_sections

    adjudications = load_adjudications(corpus)
    text = path.read_text(encoding="utf-8")
    # Entries are level-2 or level-3 headings; everything under one is that entry.
    entries = re.split(r"^#{2,3}\s+", text, flags=re.M)[1:]
    for entry in entries:
        if NOT_AN_ENTRY in entry:
            continue
        title = entry.splitlines()[0].strip() if entry.strip() else "(untitled)"
        result.checked += 1

        note = Note(citekey="", sections={"Key findings": entry})
        locators = note.locators()
        cited = [loc for loc in locators if loc.kind in ("page", "section")]

        if len(cited) < 2:
            result.problems.append(
                f"'{title[:60]}' has {len(cited)} locator(s); an entry needs two verified "
                "locators, one for each side of the contradiction. Remove it or locate it.")
            continue

        sides = {loc.cites for loc in cited if loc.cites}
        if len(sides) < 2:
            result.problems.append(
                f"'{title[:60]}' cites {len(sides) or 'no'} distinct paper(s) by citekey. "
                "Each side of a contradiction must name its source as [@citekey, p. N].")
            continue

        verified = 0
        for loc in cited:
            target = loc.cites
            if target not in index.notes:
                result.problems.append(
                    f"'{title[:60]}' cites `{target}`, which is not in the corpus.")
                continue
            text_path = corpus.text / f"{target}.md"
            if not text_path.is_file():
                result.problems.append(
                    f"'{title[:60]}' cites `{target}`, whose extracted text is missing, so "
                    "the locator cannot be verified.")
                continue
            markdown = text_path.read_text(encoding="utf-8")
            check = check_locator(loc, split_pages(markdown), split_sections(markdown),
                                  target, corpus)
            if record := adjudications.get(check.key()):
                check.adjudication = record.get("verdict", "")
            if check.effective_verdict == "missing":
                result.problems.append(
                    f"'{title[:60]}' cites [{target} {loc.kind} {loc.value}], which does "
                    "not exist. This is a fabricated locator.")
            elif check.counts_as_pass:
                verified += 1

        if verified < 2:
            result.problems.append(
                f"'{title[:60]}' has {verified} verified locator(s) of {len(cited)}. "
                "Two must verify before this entry ships. If a locator is correct but the "
                "wording differs from the paper's, record a verdict with "
                "`verify.py --adjudicate` rather than loosening the entry.")
    return result


def check_gaps(corpus: Corpus) -> CheckResult:
    """Gaps must say which kind they are (spec section 13)."""
    result = CheckResult("gaps.md")
    path = corpus.synthesis / "gaps.md"
    if not path.is_file():
        return result
    text = path.read_text(encoding="utf-8")
    entries = re.split(r"^#{2,3}\s+", text, flags=re.M)[1:]
    for entry in entries:
        if NOT_AN_ENTRY in entry:
            continue
        title = entry.splitlines()[0].strip() if entry.strip() else "(untitled)"
        result.checked += 1
        if not any(kind in entry.lower() for kind in GAP_KINDS):
            result.problems.append(
                f"'{title[:60]}' does not say which kind of gap it is. Tag it with one of "
                f"{', '.join(GAP_KINDS)} - 'not in this library' is far more likely than "
                "'no one has done this', and conflating them misleads.")
    return result


def check_draft(corpus: Corpus, index: Corpus_Index) -> CheckResult:
    """Every claim in the drafted prose carries a locator or [UNVERIFIED] (P7)."""
    result = CheckResult("review-draft.md")
    path = corpus.synthesis / "review-draft.md"
    if not path.is_file():
        return result

    text = path.read_text(encoding="utf-8")
    # Section by section, so a `not-an-entry` marker can exempt a preamble or a
    # revision-notes block without exempting the prose that matters.
    for chunk in re.split(r"^#{1,3}\s+", text, flags=re.M):
        if NOT_AN_ENTRY in chunk:
            continue
        body = re.sub(r"^#.*$", "", chunk, flags=re.M)
        for paragraph in re.split(r"\n\s*\n", body):
            paragraph = paragraph.strip()
            if len(paragraph) < 80 or paragraph.startswith((">", "|", "```", "<!--")):
                continue
            result.checked += 1
            if not ANY_LOCATOR.search(paragraph):
                result.problems.append(
                    f"paragraph with no locator: {paragraph[:90]!r}. Every claim needs "
                    f"[@citekey, p. N] or {UNVERIFIED}.")

    for match in re.finditer(r"\[@([\w:-]+)[^\]]*\]", text):
        if match.group(1) not in index.notes:
            result.problems.append(
                f"cites `{match.group(1)}`, which is not in the corpus. Citekeys in the "
                "draft must match notes exactly, or refs.bib will not resolve.")
    return result


# --- driver ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-review", description="Cross-paper synthesis artifacts.")
    parser.add_argument("--generate", action="store_true",
                        help="write index.md, methods-matrix.md and refs.bib")
    parser.add_argument("--brief", action="store_true",
                        help="emit the inputs for the judgement artifacts, as JSON")
    parser.add_argument("--check", action="store_true",
                        help="enforce the rules on the judgement artifacts")
    parser.add_argument("--filter", nargs="+", default=[], metavar="EXPR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    corpus = Corpus()
    index = Corpus_Index.load(corpus)
    if not index.notes:
        print("No analyzed notes found. Run /lit-analyze first.", file=sys.stderr)
        return 2

    try:
        citekeys, messages = apply_filters(index, args.filter)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not citekeys:
        print(f"No notes matched the filter. {' '.join(messages)}", file=sys.stderr)
        return 2

    if args.check:
        results = [check_contradictions(corpus, index), check_gaps(corpus),
                   check_draft(corpus, index)]
        missing = [name for name in JUDGEMENT_ARTIFACTS
                   if not (corpus.synthesis / name).is_file()]
        if args.json:
            print(json.dumps({
                "results": [{"artifact": r.artifact, "checked": r.checked,
                             "problems": r.problems} for r in results],
                "not_written": missing,
                "ok": all(r.ok for r in results)}, indent=2))
        else:
            for r in results:
                status = "OK" if r.ok else f"{len(r.problems)} PROBLEM(S)"
                print(f"\n{r.artifact}: {r.checked} entr(ies) checked — {status}")
                for problem in r.problems:
                    print(f"  - {problem}")
            if missing:
                print(f"\nNot yet written: {', '.join(missing)}")
            print()
        return 0 if all(r.ok for r in results) else 1

    if args.brief:
        from lib import scope as scope_mod
        scope = scope_mod.load(corpus.config)
        brief = build_brief(index, citekeys, scope.prompt_block() if scope else "")
        brief["notes"] = messages
        if scope and scope.is_fixture:
            brief["warning"] = ("The active scope is a development fixture, not a real "
                                "interview. Say so before presenting any synthesis.")
        print(json.dumps(brief, indent=2, ensure_ascii=False))
        return 0

    if args.generate:
        corpus.synthesis.mkdir(parents=True, exist_ok=True)
        written = []
        for name, content in (("index.md", build_index(index, citekeys)),
                              ("methods-matrix.md", build_methods_matrix(index, citekeys))):
            (corpus.synthesis / name).write_text(content, encoding="utf-8")
            written.append(str(corpus.synthesis / name))
        corpus.refs_bib.write_text(build_bibtex(index, citekeys), encoding="utf-8")
        written.append(str(corpus.refs_bib))

        if args.json:
            print(json.dumps({"written": written, "papers": len(citekeys),
                              "notes": messages}, indent=2))
        else:
            print(f"\nGenerated for {len(citekeys)} paper(s):")
            for path in written:
                print(f"  {path}")
            for message in messages:
                print(f"  note: {message}")
            print(f"\nStill to write (they need judgement): "
                  f"{', '.join(JUDGEMENT_ARTIFACTS)}")
            print("Run with --brief for the inputs, then --check to enforce the rules.\n")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
