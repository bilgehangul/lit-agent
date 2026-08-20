"""The per-paper note: render, parse, validate (spec section 6).

The note is the source of truth for everything downstream (**P5**), and its frontmatter is
what makes grep-based retrieval work (M4). So the shape is enforced, not merely suggested:
``validate()`` is run over every note the analyzer writes, and a note that fails is not
accepted.

Two rules carry most of the weight:

* **Anything undeterminable is written as** ``Not determinable from the text`` -- never
  guessed, never silently omitted (**P4**).
* **Every claim carries a locator, or** ``[UNVERIFIED]`` (**P7**).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

UNDETERMINABLE = "Not determinable from the text"
UNVERIFIED = "[UNVERIFIED]"

#: The 12 body sections, in fixed order. Deviating breaks every downstream parser.
SECTIONS: tuple[str, ...] = (
    "One-line summary",
    "Relevance to this project",
    "Problem & motivation",
    "Approach",
    "Evaluation",
    "Key findings",
    "Limitations & threats to validity",
    "Figures & tables",
    "Your notes",
    "Citation-ready claims",
    "Connections",
    "Open questions",
)

RELEVANCE = ("high", "medium", "low", "tangential")
PAPER_TYPES = ("empirical", "systems", "theory", "survey", "position", "dataset")
CONFIDENCE = ("high", "medium", "low")
CONNECTION_KINDS = ("extends", "contradicts", "uses-method-of", "superseded-by")

#: Locators. Page form is the reliable primitive; section form is best-effort (ADR-0001).
#: Both bare (``[p. 7]``) and citation-style (``[@doe2024x, p. 7]``) forms count -- section 10
#: is written to be pasted straight into a manuscript, so it uses the citation form.
_CITE_PREFIX = r"(?:@(?P<cite>[\w:-]+)\s*,\s*)?"
PAGE_LOCATOR = re.compile(
    rf"\[{_CITE_PREFIX}pp?\.?\s*(?P<page>\d+)(?:\s*[-–]\s*\d+)?\]", re.I)
SECTION_LOCATOR = re.compile(
    rf"\[{_CITE_PREFIX}(?:§|sec\.?|section\s*)\s*(?P<sec>[\w.]+)\]", re.I)
_CITE_PREFIX_PLAIN = r"(?:@[\w:-]+\s*,\s*)?"
ANY_LOCATOR = re.compile(
    rf"\[(?:{_CITE_PREFIX_PLAIN}(?:pp?\.?\s*\d+(?:\s*[-–]\s*\d+)?"
    rf"|(?:§|sec\.?|section\s*)\s*[\w.]+)|UNVERIFIED)\]", re.I)

#: A new logical claim starts here; anything else continues the previous one. Markdown wraps
#: long bullets across physical lines, so line-by-line checking would flag every wrap.
CLAIM_START = re.compile(r"^\s*(?:[-*+•]\s+|\d+[.)]\s+|>\s*|#{1,6}\s+|\|)")

SECTION_HEADING = re.compile(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", re.M)


@dataclass
class Locator:
    """One citation in a note, with the text it is attached to."""

    kind: str              # "page" | "section" | "unverified"
    value: str             # "7" | "4.2" | ""
    claim: str             # the sentence or bullet it appears in
    section: str = ""      # which note section it came from
    line: int = 0
    #: Set when the locator names another paper, e.g. ``[@doe2024x, p. 7]``. Such a locator
    #: must be resolved against *that* paper's text, not the note's own.
    cites: str = ""

    @property
    def is_unverified(self) -> bool:
        return self.kind == "unverified"


@dataclass
class Note:
    """A per-paper note: frontmatter plus the 12 body sections."""

    citekey: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, str] = field(default_factory=dict)

    # --- rendering ---------------------------------------------------------

    def to_markdown(self) -> str:
        import yaml
        front = yaml.safe_dump(self.frontmatter, sort_keys=False, allow_unicode=True,
                               width=100, default_flow_style=False)
        parts = [f"---\n{front}---\n"]
        for index, name in enumerate(SECTIONS, start=1):
            body = (self.sections.get(name) or "").strip() or UNDETERMINABLE
            parts.append(f"## {index}. {name}\n\n{body}\n")
        return "\n".join(parts)

    # --- parsing -----------------------------------------------------------

    @classmethod
    def parse(cls, markdown: str, citekey: str = "") -> "Note":
        import yaml
        frontmatter: dict[str, Any] = {}
        body = markdown
        if markdown.startswith("---"):
            _, _, rest = markdown.partition("---")
            raw, _, body = rest.partition("\n---")
            try:
                frontmatter = yaml.safe_load(raw) or {}
            except yaml.YAMLError:
                frontmatter = {}

        sections: dict[str, str] = {}
        matches = list(SECTION_HEADING.finditer(body))
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            sections[_canonical_section(match.group(1))] = body[start:end].strip()

        return cls(citekey=citekey or str(frontmatter.get("citekey") or ""),
                   frontmatter=frontmatter, sections=sections)

    # --- locators ----------------------------------------------------------

    def locators(self) -> list[Locator]:
        """Every locator in the note, paired with the claim it supports."""
        found: list[Locator] = []
        for name, body in self.sections.items():
            for claim, line_number in _logical_claims(body):
                for match in PAGE_LOCATOR.finditer(claim):
                    found.append(Locator("page", match.group("page"), claim, name,
                                         line_number, match.group("cite") or ""))
                for match in SECTION_LOCATOR.finditer(claim):
                    found.append(Locator("section", match.group("sec"), claim, name,
                                         line_number, match.group("cite") or ""))
                if UNVERIFIED in claim:
                    found.append(Locator("unverified", "", claim, name, line_number))
        return found

    def claims_without_locators(self) -> list[tuple[str, str]]:
        """``(section, claim)`` for factual claims carrying no locator at all.

        Checked only in the sections that assert things about the paper. Sections 1, 2, 11
        and 12 are the analyzer's own framing rather than assertions about the paper's
        content, and section 9 is the user's own words.
        """
        checked = ("Key findings", "Evaluation", "Citation-ready claims")
        offenders = []
        for name in checked:
            for claim, _ in _logical_claims(self.sections.get(name) or ""):
                if len(claim) < 25 or claim.lstrip().startswith(("#", ">", "|")):
                    continue
                if UNDETERMINABLE.lower() in claim.lower():
                    continue
                # A colon-terminated lead-in ("Headline numbers:") asserts nothing on its
                # own -- the assertions are the list items beneath it, each checked
                # separately. Demanding a locator here would push analyzers toward
                # decorating structure with citations, which devalues real ones.
                if claim.rstrip().endswith(":"):
                    continue
                if not ANY_LOCATOR.search(claim):
                    offenders.append((name, claim))
        return offenders

    # --- validation --------------------------------------------------------

    def validate(self) -> list[str]:
        """Structural problems. Empty list means the note conforms to the schema."""
        problems: list[str] = []
        fm = self.frontmatter

        for required in ("citekey", "title", "scope_version", "analyzed"):
            if not fm.get(required):
                problems.append(f"frontmatter is missing '{required}'")
        if self.citekey and fm.get("citekey") and fm["citekey"] != self.citekey:
            problems.append(
                f"frontmatter citekey {fm['citekey']!r} does not match the filename "
                f"{self.citekey!r}")

        if (value := fm.get("relevance")) and value not in RELEVANCE:
            problems.append(f"relevance {value!r} is not one of {', '.join(RELEVANCE)}")
        if (value := fm.get("paper_type")) and value not in PAPER_TYPES:
            problems.append(f"paper_type {value!r} is not one of {', '.join(PAPER_TYPES)}")
        if (value := fm.get("confidence")) and value not in CONFIDENCE:
            problems.append(f"confidence {value!r} is not one of {', '.join(CONFIDENCE)}")
        if "figures_extracted" in fm and not isinstance(fm["figures_extracted"], int):
            problems.append("figures_extracted must be an integer (0 is meaningful)")

        for name in SECTIONS:
            if name not in self.sections:
                problems.append(f"missing section '{name}'")
            elif not (self.sections.get(name) or "").strip():
                # An empty section is the silent gap P4 forbids; say why instead.
                problems.append(
                    f"section '{name}' is empty; write '{UNDETERMINABLE}' or an explicit "
                    "reason rather than leaving it blank")

        if extra := set(self.sections) - set(SECTIONS):
            problems.append(f"unexpected section(s): {', '.join(sorted(extra))}")

        for section, claim in self.claims_without_locators():
            problems.append(
                f"claim in '{section}' has no locator: {claim[:70]!r}. "
                f"Add [p. N] or {UNVERIFIED}.")

        return problems


def _logical_claims(body: str) -> list[tuple[str, int]]:
    """Split a section into logical claims, joining wrapped lines.

    A bullet, numbered item, quote, or paragraph is one claim however many physical lines
    it occupies. Checking line by line would flag the wrapped tail of every long bullet as
    a claim with no locator, which is noise, not a finding.
    """
    claims: list[tuple[str, int]] = []
    buffer: list[str] = []
    start = 0

    def flush() -> None:
        if text := " ".join(b.strip() for b in buffer).strip():
            claims.append((text.lstrip("-*+•  ").strip(), start))

    for number, line in enumerate(body.splitlines(), start=1):
        if not line.strip():
            flush()
            buffer, start = [], 0
            continue
        if CLAIM_START.match(line) and buffer:
            flush()
            buffer, start = [line], number
            continue
        if not buffer:
            start = number
        buffer.append(line)
    flush()
    return claims


def _canonical_section(heading: str) -> str:
    """Map a written heading back to its canonical name, tolerating small variations."""
    cleaned = re.sub(r"\s+", " ", heading.strip()).rstrip(".")
    for name in SECTIONS:
        if cleaned.lower() == name.lower():
            return name
        # Tolerate "and" for "&" and vice versa.
        if cleaned.lower().replace(" and ", " & ") == name.lower():
            return name
    return cleaned


# --- building a note -------------------------------------------------------


def build_frontmatter(item: dict[str, Any], scope_version: str, *,
                      figures_extracted: int = 0, has_user_notes: bool = False,
                      enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Frontmatter fields that come from the corpus rather than from analysis.

    The analyzer fills in the judgement fields (``relevance``, ``paper_type``, ``methods``,
    ``datasets``, ``metrics``, ``scope_tags``, ``confidence``); everything here is known
    before analysis starts and should never be re-derived by a model.
    """
    metadata = item.get("metadata") or {}
    authors = []
    for a in metadata.get("author") or []:
        if isinstance(a, dict):
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x)
            authors.append(name or a.get("literal", ""))
    issued = (metadata.get("issued") or {}).get("date-parts") or []
    year = issued[0][0] if issued and issued[0] else None

    return {
        "citekey": item.get("citekey", ""),
        "zotero_key": item.get("source_id", ""),
        "title": metadata.get("title", ""),
        "authors": [a for a in authors if a],
        "year": year,
        "venue": (metadata.get("container-title") or metadata.get("publisher") or "") or None,
        "doi": metadata.get("DOI") or None,
        "arxiv_id": metadata.get("arxiv_id") or None,
        "item_type": metadata.get("zotero_type") or metadata.get("type") or "document",
        "tags": item.get("tags") or [],
        "scope_tags": [],
        "relevance": None,
        "paper_type": None,
        "methods": [],
        "datasets": [],
        "metrics": [],
        "figures_extracted": figures_extracted,
        "has_user_notes": has_user_notes,
        "enrichment": enrichment or {"semantic_scholar": False, "scholar_labs": False,
                                     "queries": []},
        "analyzed": date.today().isoformat(),
        "scope_version": scope_version,
        "confidence": None,
    }
