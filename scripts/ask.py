"""Retrieval over the corpus for /lit-ask and /lit-review (spec section 13).

Default strategy is grep/glob, in three steps: filter on frontmatter fields, score notes by
keyword match, then hand the winners to the analyzer to read in full. Zero dependencies,
fast well past the ~150-paper mark the spec sets as the threshold for wanting a vector index.

**This module retrieves; it does not answer.** Answering means reading the notes and citing
them, which is the analyzer's job. Keeping retrieval separate is what makes it testable and
what keeps the citation trail intact.

    python scripts/ask.py "how do LLMs fail at policy analysis"
    python scripts/ask.py "contradiction detection" --filter relevance=high
    python scripts/ask.py --filter year>=2020 paper_type=empirical --list
    python scripts/ask.py "OPP-115" --json --top 5
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from lib.note import SECTIONS, Note  # noqa: E402
from lib.paths import Corpus  # noqa: E402

STOPWORDS = frozenset("""
a an the and or but for nor of on in at to from by with without into over under this that
these those is are was were be been being do does did can could will would shall should
may might must not no its it as if then than so such we our they their there here which
who whom whose what when where how why all any both each few more most other some only own
same very just also however therefore thus while during between among across per via
about does did done doing what's whats tell show give find list
""".split())

#: Sections weighted above the rest when scoring a keyword hit. A term in the summary or the
#: citation-ready claims says more about what a paper is *for* than the same term buried in
#: a limitations aside.
SECTION_WEIGHTS: dict[str, float] = {
    "One-line summary": 3.0,
    "Relevance to this project": 2.5,
    "Citation-ready claims": 2.5,
    "Key findings": 2.0,
    "Approach": 1.5,
    "Evaluation": 1.5,
    "Problem & motivation": 1.2,
    "Connections": 1.2,
    "Limitations & threats to validity": 1.0,
    "Open questions": 1.0,
    "Figures & tables": 0.5,
    "Your notes": 1.5,
}

TITLE_WEIGHT = 4.0
FRONTMATTER_WEIGHT = 2.0

COMPARISON = re.compile(r"^(?P<field>[a-z_]+)\s*(?P<op>>=|<=|!=|=|>|<)\s*(?P<value>.+)$", re.I)


@dataclass
class Hit:
    citekey: str
    title: str
    year: int | None
    relevance: str
    paper_type: str
    score: float
    #: ``(section, snippet)`` for the strongest matches, so the caller can see *why*.
    snippets: list[tuple[str, str]] = field(default_factory=list)
    matched_terms: set[str] = field(default_factory=set)
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "citekey": self.citekey, "title": self.title, "year": self.year,
            "relevance": self.relevance, "paper_type": self.paper_type,
            "score": round(self.score, 2), "path": self.path,
            "matched_terms": sorted(self.matched_terms),
            "snippets": [{"section": s, "text": t} for s, t in self.snippets],
        }


@dataclass
class Corpus_Index:
    """Every note's frontmatter and body, loaded once."""

    notes: dict[str, Note] = field(default_factory=dict)
    bodies: dict[str, str] = field(default_factory=dict)
    unreadable: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, corpus: Corpus) -> "Corpus_Index":
        index = cls()
        if not corpus.papers.is_dir():
            return index
        for path in sorted(corpus.papers.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                index.unreadable.append((path.stem, str(exc)))
                continue
            note = Note.parse(text, citekey=path.stem)
            index.notes[path.stem] = note
            index.bodies[path.stem] = text
        return index

    def __len__(self) -> int:
        return len(self.notes)


# --- frontmatter filtering -------------------------------------------------


def parse_filter(expression: str) -> tuple[str, str, str]:
    match = COMPARISON.match(expression.strip())
    if not match:
        raise ValueError(
            f"cannot parse filter {expression!r}. Use field=value, field>=value, "
            "field!=value. Example: relevance=high, year>=2020")
    return match.group("field"), match.group("op"), match.group("value").strip()


def _matches(value: Any, op: str, target: str) -> bool:
    """Compare a frontmatter value. List fields match if *any* element matches."""
    if isinstance(value, list):
        return any(_matches(v, op, target) for v in value)
    if value is None:
        return op == "!="

    if op in (">", "<", ">=", "<="):
        try:
            left, right = float(value), float(target)
        except (TypeError, ValueError):
            return False
        return {">": left > right, "<": left < right,
                ">=": left >= right, "<=": left <= right}[op]

    left_s, right_s = str(value).strip().lower(), target.strip().lower()
    if op == "=":
        # Substring for free-text fields, exact for the closed enumerations.
        return left_s == right_s or (len(right_s) > 2 and right_s in left_s)
    if op == "!=":
        return left_s != right_s
    return False


def apply_filters(index: Corpus_Index, filters: list[str]) -> tuple[list[str], list[str]]:
    """Return ``(matching citekeys, notes about fields that were absent)``."""
    if not filters:
        return sorted(index.notes), []

    parsed = [parse_filter(f) for f in filters]
    kept, missing_fields = [], set()
    for citekey, note in index.notes.items():
        ok = True
        for field_name, op, target in parsed:
            if field_name not in note.frontmatter:
                # A filter on a field a note does not carry excludes it, but the caller is
                # told -- otherwise a typo silently returns an empty corpus (P4).
                missing_fields.add(field_name)
                ok = False
                break
            if not _matches(note.frontmatter.get(field_name), op, target):
                ok = False
                break
        if ok:
            kept.append(citekey)

    notes = []
    if missing_fields:
        notes.append(
            "some notes lack these frontmatter field(s) and were excluded: "
            + ", ".join(sorted(missing_fields)))
    return sorted(kept), notes


# --- keyword scoring -------------------------------------------------------


def query_terms(query: str) -> list[str]:
    """Content words from the question, plus quoted phrases kept intact."""
    phrases = re.findall(r'"([^"]+)"', query)
    remainder = re.sub(r'"[^"]+"', " ", query)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", remainder.lower())
             if w not in STOPWORDS]
    return [p.lower() for p in phrases] + words


def stem(word: str) -> str:
    """Crude suffix stripping, so a question and a note can use different word forms.

    Without this, "policies" fails to match "policy" and "contradictions" fails to match
    "contradiction" -- which is most of the vocabulary a researcher actually types. A real
    stemmer would be better; this is deliberately small and dependency-free, and it errs
    toward over-matching because a spurious candidate costs a read while a missed one costs
    the answer.
    """
    for suffix, cut in (("ies", 3), ("ing", 3), ("ies", 3), ("es", 2), ("ed", 2), ("s", 1)):
        if word.endswith(suffix) and len(word) - cut >= 4:
            return word[: -cut]
    return word


def _count(haystack: str, term: str) -> int:
    if " " in term:                     # a quoted phrase: substring match
        return haystack.count(term)
    return len(re.findall(rf"\b{re.escape(stem(term))}\w{{0,4}}\b", haystack))


def score_note(note: Note, body: str, terms: list[str]) -> tuple[float, set[str], list[tuple[str, str]]]:
    if not terms:
        return 0.0, set(), []

    lowered_title = str(note.frontmatter.get("title") or "").lower()
    frontmatter_blob = " ".join(
        str(v) for k, v in note.frontmatter.items()
        if k in ("scope_tags", "methods", "datasets", "metrics", "tags", "venue")).lower()

    score = 0.0
    matched: set[str] = set()

    for term in terms:
        if hits := _count(lowered_title, term):
            score += TITLE_WEIGHT * hits
            matched.add(term)
        if hits := _count(frontmatter_blob, term):
            score += FRONTMATTER_WEIGHT * hits
            matched.add(term)

    snippets: list[tuple[str, str]] = []
    for name in SECTIONS:
        section_text = (note.sections.get(name) or "").lower()
        if not section_text:
            continue
        weight = SECTION_WEIGHTS.get(name, 1.0)
        section_hits = 0
        for term in terms:
            if hits := _count(section_text, term):
                score += weight * hits
                matched.add(term)
                section_hits += hits
        if section_hits:
            if snippet := _best_snippet(note.sections.get(name) or "", terms):
                snippets.append((name, snippet))

    # Reward breadth: a note matching four of the question's terms beats one matching a
    # single term four times, which is usually a passing mention.
    if matched:
        score *= 1 + 0.5 * (len(matched) - 1) / max(1, len(terms))

    snippets.sort(key=lambda s: -SECTION_WEIGHTS.get(s[0], 1.0))
    return score, matched, snippets[:3]


def _best_snippet(section_text: str, terms: list[str], width: int = 240) -> str:
    """The line in this section matching the most query terms."""
    best, best_hits = "", 0
    for raw in section_text.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        if len(line) < 30:
            continue
        lowered = line.lower()
        hits = sum(1 for t in terms if _count(lowered, t))
        if hits > best_hits:
            best, best_hits = line, hits
    return best[:width] if best_hits else ""


def search(corpus: Corpus, query: str = "", filters: list[str] | None = None,
           top: int = 8) -> tuple[list[Hit], list[str], int]:
    """``(hits, notes for the user, corpus size)``."""
    index = Corpus_Index.load(corpus)
    messages: list[str] = []
    for citekey, error in index.unreadable:
        messages.append(f"could not read note {citekey}: {error}")

    citekeys, filter_notes = apply_filters(index, filters or [])
    messages.extend(filter_notes)

    terms = query_terms(query)
    hits: list[Hit] = []
    for citekey in citekeys:
        note = index.notes[citekey]
        score, matched, snippets = score_note(note, index.bodies[citekey], terms)
        if terms and score <= 0:
            continue
        fm = note.frontmatter
        hits.append(Hit(
            citekey=citekey,
            title=str(fm.get("title") or citekey),
            year=fm.get("year"),
            relevance=str(fm.get("relevance") or "unknown"),
            paper_type=str(fm.get("paper_type") or "unknown"),
            score=score, snippets=snippets, matched_terms=matched,
            path=str(corpus.papers / f"{citekey}.md"),
        ))

    hits.sort(key=lambda h: (-h.score, h.citekey))
    if terms and not hits and citekeys:
        # The distinction matters enormously for the answer: "the corpus has nothing on
        # this" is a finding; "your filter excluded everything" is a mistake.
        messages.append(
            f"no note matched any of {', '.join(terms)} among the "
            f"{len(citekeys)} note(s) that passed the filters. The corpus may simply not "
            "cover this question - say so rather than answering from general knowledge.")
    return hits[:top], messages, len(index)


# --- driver ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-ask", description="Retrieve the notes relevant to a question.")
    parser.add_argument("query", nargs="?", default="", help="the question, in plain words")
    parser.add_argument("--filter", nargs="+", default=[], metavar="EXPR",
                        help="frontmatter filters, e.g. relevance=high year>=2020")
    parser.add_argument("--top", type=int, default=8, help="how many notes to return")
    parser.add_argument("--list", action="store_true",
                        help="list the filtered corpus without scoring")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    corpus = Corpus()
    if not corpus.papers.is_dir() or not any(corpus.papers.glob("*.md")):
        print("No analyzed notes found. Run /lit-ingest then /lit-analyze first.",
              file=sys.stderr)
        return 2

    try:
        hits, messages, total = search(
            corpus, "" if args.list else args.query, args.filter,
            top=10_000 if args.list else args.top)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "query": args.query, "filters": args.filter,
            "corpus_size": total, "returned": len(hits),
            "notes": messages,
            "hits": [h.to_dict() for h in hits],
        }, indent=2, ensure_ascii=False))
        return 0

    print(f"\n{len(hits)} of {total} note(s)"
          + (f" match {args.query!r}" if args.query else " after filtering") + "\n")
    for hit in hits:
        year = hit.year or "n.d."
        print(f"  [{hit.score:6.1f}] {hit.citekey}  ({year}, {hit.paper_type}, "
              f"relevance {hit.relevance})")
        print(f"           {hit.title[:88]}")
        for section, snippet in hit.snippets:
            print(f"           {section}: {snippet[:110]}")
        print()
    for message in messages:
        print(f"  note: {message}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
