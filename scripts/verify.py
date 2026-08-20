"""Locator verification (**P7**) - the correctness backbone of the project.

Every claim in a generated note must trace to a specific paper and a locator. This module
checks that the trace actually holds, in two passes with very different characters:

**Pass 1 - structural (deterministic, free).** Does the cited page or section exist in
``text/<citekey>.md``? A locator pointing at page 40 of a 12-page paper is a fabrication,
full stop, and no judgement is needed to say so.

**Pass 2 - support (lexical, cheap).** Does the cited page contain the distinctive content
words of the claim? This is a *screen*, not a verdict: high overlap is good evidence, low
overlap means "a human or a model should look at this". It is deliberately biased toward
flagging - a false alarm costs a review, a missed fabrication costs the user's credibility.

Anything the screen flags is reported for a semantic check by the analyzer skill, which
downgrades ``confidence`` and rewrites the locator as ``[UNVERIFIED]`` when support cannot
be found. **The screen never rewrites a note itself** - it only reports.

    python scripts/verify.py                       # check every note
    python scripts/verify.py --citekey doe2024x
    python scripts/verify.py --sample 40 --seed 7  # the M3 gate sample
    python scripts/verify.py --json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.console import setup as _setup_console

_setup_console()

from extract.pdf_text import split_pages, split_sections  # noqa: E402
from lib.note import Locator, Note  # noqa: E402
from lib.paths import Corpus  # noqa: E402

#: Words too common to be evidence of anything.
STOPWORDS = frozenset("""
a an the and or but for nor of on in at to from by with without into over under this that
these those is are was were be been being do does did can could will would shall should
may might must not no its it as if then than so such we our they their there here which
who whom whose what when where how why all any both each few more most other some only own
same very just also however therefore thus while during between among across per via using
use used show shows shown found find paper work study approach method results result data
model models based new proposed propose different large small high low significant
""".split())

#: Fraction of a claim's distinctive words that must appear on the cited page.
SUPPORT_THRESHOLD = 0.30
#: Below this, the locator is reported as unsupported rather than merely weak.
WEAK_THRESHOLD = 0.15


@dataclass
class LocatorCheck:
    citekey: str
    kind: str
    value: str
    section: str
    claim: str
    resolves: bool          # does the cited page/section exist?
    overlap: float          # 0..1 lexical overlap with the cited text
    verdict: str            # "supported" | "weak" | "unsupported" | "missing" | "unverified"
    detail: str = ""
    #: A recorded semantic judgement, which overrides the lexical screen. See adjudications.
    adjudication: str = ""  # "" | "supported" | "unsupported"
    adjudication_note: str = ""

    @property
    def effective_verdict(self) -> str:
        """The lexical screen is a proxy; a recorded semantic judgement outranks it.

        A structural failure (``missing``) is never overridable -- a page that does not
        exist cannot be argued into existing.
        """
        if self.verdict == "missing":
            return "missing"
        if self.adjudication == "supported":
            return "supported"
        if self.adjudication == "unsupported":
            return "unsupported"
        return self.verdict

    @property
    def counts_as_pass(self) -> bool:
        """What the M3 gate counts. ``unverified`` is honest, so it is never a failure."""
        return self.effective_verdict in ("supported", "unverified")

    def key(self) -> str:
        """Stable identity for an adjudication, so verdicts survive re-runs."""
        import hashlib
        blob = f"{self.citekey}|{self.kind}|{self.value}|{self.claim}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --- adjudications ---------------------------------------------------------


def load_adjudications(corpus: Corpus) -> dict[str, dict]:
    """Recorded semantic judgements over locators the lexical screen could not confirm.

    The screen answers "do the claim's distinctive words appear at the locator". That is a
    poor proxy for an analyst's own observation, which is worded in the analyst's language
    and cites the page carrying the fact being observed. Rather than weaken the screen --
    which would let real fabrications through -- flagged locators get an explicit, recorded
    verdict from someone who read the page.

    Stored at ``.lit/adjudications.json``, keyed by locator identity, so the judgement is
    auditable and survives re-runs.
    """
    path = corpus.root / "adjudications.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("locators", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_adjudications(corpus: Corpus, records: dict[str, dict]) -> Path:
    path = corpus.root / "adjudications.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "locators": records},
                               indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def apply_adjudications(reports: list["NoteReport"], records: dict[str, dict]) -> int:
    applied = 0
    for report in reports:
        for check in report.checks:
            if record := records.get(check.key()):
                check.adjudication = record.get("verdict", "")
                check.adjudication_note = record.get("note", "")
                applied += 1
    return applied


@dataclass
class NoteReport:
    citekey: str
    checks: list[LocatorCheck]
    structural_problems: list[str]
    text_available: bool = True

    @property
    def fabricated(self) -> list[LocatorCheck]:
        """Locators pointing at pages or sections that do not exist. Never acceptable."""
        return [c for c in self.checks if c.verdict == "missing"]

    @property
    def needs_adjudication(self) -> list[LocatorCheck]:
        """Flagged by the screen and not yet judged by a reader."""
        return [c for c in self.checks
                if c.verdict in ("weak", "unsupported") and not c.adjudication]


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9\-]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 3}


def overlap_score(claim: str, target: str) -> float:
    """Fraction of the claim's distinctive words that appear in the cited text."""
    claim_words = content_words(claim)
    if not claim_words:
        return 1.0          # nothing distinctive to check; do not manufacture a failure
    target_words = content_words(target)
    if not target_words:
        return 0.0
    hits = sum(1 for w in claim_words if w in target_words)
    return hits / len(claim_words)


def _strip_locators(claim: str) -> str:
    """Remove locator markup so the citekey and page number are not scored as content."""
    from lib.note import ANY_LOCATOR
    cleaned = ANY_LOCATOR.sub(" ", claim)
    return re.sub(r"\[@[\w-]+[^\]]*\]", " ", cleaned)


def _load_pages(corpus: Corpus, citekey: str) -> tuple[dict[int, str], dict[str, str]] | None:
    path = corpus.text / f"{citekey}.md"
    if not path.is_file():
        return None
    markdown = path.read_text(encoding="utf-8")
    return split_pages(markdown), split_sections(markdown)


def check_locator(loc: Locator, pages: dict[int, str],
                  sections: dict[str, str], citekey: str,
                  corpus: "Corpus | None" = None) -> LocatorCheck:
    claim = _strip_locators(loc.claim)

    # ``[@other2024x, p. 7]`` means page 7 *of that paper*, so resolve it there. Checking it
    # against this note's own text would report a fabrication that is not one -- or, worse,
    # silently pass because the page numbers happen to overlap.
    if loc.cites and loc.cites != citekey:
        if corpus is None:
            return LocatorCheck(citekey, loc.kind, loc.value, loc.section, loc.claim,
                                True, 1.0, "unverified",
                                f"cross-reference to {loc.cites}; not checked here")
        other = _load_pages(corpus, loc.cites)
        if other is None:
            return LocatorCheck(
                citekey, loc.kind, loc.value, loc.section, loc.claim, True, 1.0,
                "unverified",
                f"cites {loc.cites}, which is not in this corpus, so the locator could "
                "not be checked")
        pages, sections = other
        citekey = loc.cites

    if loc.kind == "unverified":
        return LocatorCheck(
            citekey, loc.kind, "", loc.section, loc.claim, True, 1.0, "unverified",
            "the analyzer marked this claim as unsupported rather than inventing a locator")

    if loc.kind == "page":
        number = int(loc.value)
        if number not in pages:
            return LocatorCheck(
                citekey, "page", loc.value, loc.section, loc.claim, False, 0.0, "missing",
                f"page {number} does not exist in the extracted text "
                f"(pages 1-{max(pages) if pages else 0})")
        target = pages[number]
        # A claim often spans a page break, so give the neighbours partial credit.
        neighbours = " ".join(pages.get(n, "") for n in (number - 1, number + 1))
        score = max(overlap_score(claim, target),
                    overlap_score(claim, target + " " + neighbours) * 0.9)
    else:
        target = sections.get(loc.value) or sections.get(loc.value.lower())
        if target is None:
            return LocatorCheck(
                citekey, "section", loc.value, loc.section, loc.claim, False, 0.0, "missing",
                f"section {loc.value!r} was not detected in the extracted text. Section "
                "locators are best-effort (ADR-0001); prefer a page locator.")
        score = overlap_score(claim, target)

    if score >= SUPPORT_THRESHOLD:
        verdict, detail = "supported", ""
    elif score >= WEAK_THRESHOLD:
        verdict, detail = "weak", "few of the claim's distinctive words appear at the locator"
    else:
        verdict, detail = "unsupported", "the cited text does not appear to contain this claim"
    return LocatorCheck(citekey, loc.kind, loc.value, loc.section, loc.claim,
                        True, round(score, 3), verdict, detail)


def check_note(corpus: Corpus, citekey: str) -> NoteReport:
    note_path = corpus.papers / f"{citekey}.md"
    text_path = corpus.text / f"{citekey}.md"
    if not note_path.is_file():
        return NoteReport(citekey, [], [f"no note at {note_path}"], text_available=False)

    note = Note.parse(note_path.read_text(encoding="utf-8"), citekey=citekey)
    problems = note.validate()

    if not text_path.is_file():
        return NoteReport(citekey, [], problems + [
            f"no extracted text at {text_path}; locators cannot be verified"],
            text_available=False)

    markdown = text_path.read_text(encoding="utf-8")
    pages, sections = split_pages(markdown), split_sections(markdown)
    checks = [check_locator(loc, pages, sections, citekey, corpus)
              for loc in note.locators()]
    return NoteReport(citekey, checks, problems)


# --- reporting -------------------------------------------------------------


def gate_summary(reports: list[NoteReport], sample: list[LocatorCheck] | None = None) -> dict:
    checks = sample if sample is not None else [c for r in reports for c in r.checks]
    total = len(checks)
    passed = sum(1 for c in checks if c.counts_as_pass)
    fabricated = [c for c in checks if c.effective_verdict == "missing"]
    return {
        "locators_checked": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "fabricated": len(fabricated),
        "by_verdict": {
            v: sum(1 for c in checks if c.effective_verdict == v)
            for v in ("supported", "unverified", "weak", "unsupported", "missing")},
        "adjudicated": sum(1 for c in checks if c.adjudication),
        # The M3 gate: at least 90% resolve to supporting text AND zero fabrications.
        "gate_passes": total > 0 and (passed / total) >= 0.90 and not fabricated,
        "notes_checked": len(reports),
        "structural_problems": sum(len(r.structural_problems) for r in reports),
    }


def format_report(reports: list[NoteReport], summary: dict,
                  sample: list[LocatorCheck] | None = None) -> str:
    lines = ["", "lit-agent locator verification", "=" * 64, ""]
    lines.append(f"  notes checked      {summary['notes_checked']}")
    lines.append(f"  locators checked   {summary['locators_checked']}"
                 + ("  (sampled)" if sample is not None else ""))
    lines.append(f"  pass rate          {summary['pass_rate'] * 100:.1f}%")
    lines.append(f"  fabricated         {summary['fabricated']}")
    if summary.get("adjudicated"):
        lines.append(f"  adjudicated        {summary['adjudicated']} "
                     "(semantic verdicts recorded by a reader)")
    lines.append("")
    lines.append("  by verdict: " + ", ".join(
        f"{k} {v}" for k, v in summary["by_verdict"].items() if v))
    lines.append("")

    if fabrications := [c for r in reports for c in r.fabricated]:
        lines.append("FABRICATED LOCATORS - these point at content that does not exist:")
        for c in fabrications[:20]:
            lines.append(f"  {c.citekey} [{c.kind} {c.value}] in '{c.section}'")
            lines.append(f"     {c.claim[:100]}")
            lines.append(f"     {c.detail}")
        lines.append("")

    flagged = [c for r in reports for c in r.needs_adjudication]
    if flagged:
        lines.append(f"Flagged for semantic review ({len(flagged)}) - the lexical screen "
                     "could not confirm support:")
        for c in flagged[:15]:
            lines.append(f"  [{c.overlap:.2f}] {c.citekey} p.{c.value} - {c.claim[:80]}")
        if len(flagged) > 15:
            lines.append(f"  ... and {len(flagged) - 15} more")
        lines.append("")

    if problems := [(r.citekey, p) for r in reports for p in r.structural_problems]:
        lines.append(f"Schema problems ({len(problems)}):")
        for citekey, problem in problems[:15]:
            lines.append(f"  {citekey}: {problem[:100]}")
        if len(problems) > 15:
            lines.append(f"  ... and {len(problems) - 15} more")
        lines.append("")

    lines.append("M3 GATE: " + ("PASS" if summary["gate_passes"] else "FAIL")
                 + f"  (needs >=90% pass rate and zero fabrications; "
                   f"got {summary['pass_rate'] * 100:.1f}% and "
                   f"{summary['fabricated']} fabrication(s))")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-verify", description="Verify that every locator in a note resolves.")
    parser.add_argument("--citekey", nargs="+", help="check only these notes")
    parser.add_argument("--sample", type=int,
                        help="check a random sample of N locators (the M3 gate)")
    parser.add_argument("--seed", type=int, default=0, help="sample seed, for reproducibility")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", help="write a per-row report to this path")
    parser.add_argument("--pending", action="store_true",
                        help="emit the locators awaiting a semantic verdict, as JSON")
    parser.add_argument("--adjudicate", metavar="FILE",
                        help="record semantic verdicts from a JSON file: "
                             "[{key, verdict: supported|unsupported, note}]")
    args = parser.parse_args(argv)

    corpus = Corpus()
    if not corpus.papers.is_dir():
        print(f"no notes found at {corpus.papers}. Run /lit-analyze first.", file=sys.stderr)
        return 2

    citekeys = args.citekey or sorted(p.stem for p in corpus.papers.glob("*.md"))
    if not citekeys:
        print("no notes to verify.", file=sys.stderr)
        return 2

    reports = [check_note(corpus, ck) for ck in citekeys]
    records = load_adjudications(corpus)

    if args.adjudicate:
        incoming = json.loads(Path(args.adjudicate).read_text(encoding="utf-8"))
        for entry in incoming:
            verdict = entry.get("verdict")
            if verdict not in ("supported", "unsupported"):
                print(f"skipping {entry.get('key')}: verdict must be supported or "
                      "unsupported", file=sys.stderr)
                continue
            records[entry["key"]] = {"verdict": verdict, "note": entry.get("note", "")}
        path = save_adjudications(corpus, records)
        print(f"recorded {len(incoming)} adjudication(s) in {path}")

    applied = apply_adjudications(reports, records)

    if args.pending:
        pending = [c for r in reports for c in r.needs_adjudication]
        print(json.dumps([{
            "key": c.key(), "citekey": c.citekey,
            "locator": f"{c.kind} {c.value}".strip(), "section": c.section,
            "overlap": c.overlap, "claim": c.claim,
        } for c in pending], indent=2, ensure_ascii=False))
        return 0

    sample = None
    if args.sample:
        every = [c for r in reports for c in r.checks]
        rng = random.Random(args.seed)
        sample = rng.sample(every, min(args.sample, len(every)))

    summary = gate_summary(reports, sample)

    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(
            _markdown_report(reports, summary, sample, args.seed), encoding="utf-8")

    if args.json:
        print(json.dumps({
            "summary": summary,
            "sample": [asdict(c) for c in sample] if sample is not None else None,
            "notes": [{"citekey": r.citekey,
                       "structural_problems": r.structural_problems,
                       "checks": [asdict(c) for c in r.checks]} for r in reports],
        }, indent=2))
    else:
        print(format_report(reports, summary, sample))

    return 0 if summary["gate_passes"] else 1


def _markdown_report(reports, summary, sample, seed: int) -> str:
    checks = sample if sample is not None else [c for r in reports for c in r.checks]
    lines = [
        "# M3 locator check",
        "",
        f"Sampled {len(checks)} locator(s)"
        + (f" with seed {seed}" if sample is not None else " (all locators)")
        + f" across {summary['notes_checked']} note(s).",
        "",
        f"- pass rate: **{summary['pass_rate'] * 100:.1f}%** (gate needs 90%)",
        f"- fabricated locators: **{summary['fabricated']}** (gate needs 0)",
        f"- verdict: **{'PASS' if summary['gate_passes'] else 'FAIL'}**",
        "",
        "`supported` = the cited page carries the claim's distinctive words. "
        "`unverified` = the analyzer declined to invent a locator, which is the honest "
        "outcome and counts as a pass. `weak` / `unsupported` = flagged for semantic "
        "review. `missing` = the cited page or section does not exist, which is a "
        "fabrication.",
        "",
        "| # | citekey | locator | section | overlap | screen | verdict | claim |",
        "|---|---|---|---|---|---|---|---|",
    ]
    adjudicated = []
    for index, c in enumerate(checks, start=1):
        claim = c.claim.replace("|", "\\|")[:90]
        loc = f"{c.kind} {c.value}".strip()
        # Show both: what the cheap screen said, and the verdict that actually counts.
        lines.append(f"| {index} | {c.citekey} | {loc} | {c.section} | "
                     f"{c.overlap:.2f} | {c.verdict} | **{c.effective_verdict}** | {claim} |")
        if c.adjudication:
            adjudicated.append((index, c))

    if adjudicated:
        lines += ["", "### Adjudications", "",
                  "Rows the lexical screen could not confirm, with the recorded reason each "
                  "was judged. These are the rows where the verdict column differs from the "
                  "screen column.", ""]
        for index, c in adjudicated:
            lines.append(f"**{index}. {c.citekey} [{c.kind} {c.value}] "
                         f"-> {c.adjudication}**")
            lines.append("")
            lines.append(f"> {c.adjudication_note}")
            lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
