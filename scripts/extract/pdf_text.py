"""Stage 2 - PDF text extraction (spec section 9, as amended by ADR-0001).

Raw ``pymupdf`` extraction is the default. M0/S4-a measured ``pymupdf4llm`` at 3.32 s/page
against 0.04 s/page for raw extraction, for 3% more text - two orders of magnitude of the
pipeline's wall clock for a rounding error of content. ``--layout`` still routes through
``pymupdf4llm`` for cases where markdown structure genuinely matters.

**Page markers are the primitive citation verification depends on (P7).** They are written
as ``<!-- page N -->`` on their own line and must never be stripped downstream.

Headings are detected from font size, not parsed. That is deliberately best-effort: when a
heading cannot be identified confidently, none is emitted and the analyzer falls back to a
page locator rather than inventing a section number.
"""

from __future__ import annotations

import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PAGE_MARKER = "<!-- page {n} -->"
PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$", re.M)

#: Below this many characters per page, a PDF is almost certainly scanned images.
SCANNED_CHARS_PER_PAGE = 60

#: A heading candidate must be at least this much larger than the body font.
HEADING_SIZE_RATIO = 1.12

NUMBERED_HEADING = re.compile(
    r"^\s*(\d+(?:\.\d+)*)\.?\s+([A-Z][^\n]{2,80})\s*$")

#: Front matter that is set large but is not a section heading: arXiv stamps, venue
#: banners, DOIs, emails, page furniture. Without this the detector calls the arXiv
#: identifier a section, and a bogus heading is worse than a missing one (P7).
HEADING_NOISE = re.compile(
    r"(?i)(arxiv:|doi:|https?://|www\.|@|\bissn\b|\bisbn\b|copyright"
    r"|open access|proceedings of|all rights reserved|licensed under"
    r"|^(figure|fig\.|table|algorithm|equation)\s*\d"
    r"|^page\s*\d)")

UNNUMBERED_HEADING = re.compile(
    r"^\s*(abstract|introduction|background|related work|methodology|methods?|"
    r"approach|design|implementation|evaluation|experiments?|results?|discussion|"
    r"limitations?|threats to validity|conclusions?|future work|acknowledge?ments?|"
    r"references|appendix[\s\w]*)\s*$", re.I)


@dataclass
class ExtractionResult:
    citekey: str
    pdf: Path
    markdown: str = ""
    pages: int = 0
    chars: int = 0
    extractor: str = "pymupdf"
    #: ``(page_number, level, text)`` for every heading detected.
    headings: list[tuple[int, int, str]] = field(default_factory=list)
    scanned: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.chars > 0


def extract(pdf: Path, citekey: str = "", layout: bool = False) -> ExtractionResult:
    """Extract one PDF to markdown with page markers."""
    result = ExtractionResult(citekey=citekey, pdf=pdf,
                              extractor="pymupdf4llm" if layout else "pymupdf")
    try:
        import pymupdf
    except ImportError as exc:
        result.error = f"pymupdf not importable: {exc}"
        return result

    try:
        doc = pymupdf.open(pdf)
    except Exception as exc:  # noqa: BLE001 - a corrupt PDF is one bad item, not a run failure
        result.error = f"could not open PDF: {type(exc).__name__}: {exc}"
        return result

    try:
        result.pages = doc.page_count
        if layout:
            body = _extract_layout(pdf, result)
        else:
            body = _extract_fast(doc, result)
    except Exception as exc:  # noqa: BLE001
        result.error = f"extraction failed: {type(exc).__name__}: {exc}"
        return result
    finally:
        doc.close()

    result.chars = sum(len(line) for line in body)
    # A near-empty text layer means a scanned PDF, not an empty paper (P4).
    if result.pages and result.chars / result.pages < SCANNED_CHARS_PER_PAGE:
        result.scanned = True

    header = (f"<!-- lit-agent: citekey={citekey or '?'} pages={result.pages} "
              f"extractor={result.extractor} chars={result.chars} -->")
    result.markdown = header + "\n\n" + "\n".join(body).strip() + "\n"
    return result


def _extract_fast(doc, result: ExtractionResult) -> list[str]:
    """The default path: raw text per page, plus font-size heading detection."""
    body_size = _body_font_size(doc)
    out: list[str] = []
    for index, page in enumerate(doc):
        number = index + 1
        out.append(PAGE_MARKER.format(n=number))
        headings = _detect_headings(page, body_size) if body_size else {}
        text = page.get_text() or ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                out.append("")
                continue
            if level := headings.get(stripped):
                out.append("")
                out.append(f"{'#' * min(level + 1, 6)} {stripped}")
                out.append("")
                result.headings.append((number, level, stripped))
            else:
                out.append(line.rstrip())
        out.append("")
    return out


def _extract_layout(pdf: Path, result: ExtractionResult) -> list[str]:
    """The opt-in path: pymupdf4llm markdown, still page-chunked so markers survive."""
    try:
        import pymupdf4llm
    except ImportError:
        result.error = ("--layout needs pymupdf4llm, which is not installed. Run: "
                        "python scripts/setup_env.py --extra layout_text")
        return []
    chunks = pymupdf4llm.to_markdown(str(pdf), page_chunks=True)
    out: list[str] = []
    for index, chunk in enumerate(chunks):
        number = (chunk.get("metadata") or {}).get("page_number") or index + 1
        out.append(PAGE_MARKER.format(n=number))
        text = chunk.get("text") or ""
        out.append(text.rstrip())
        for line in text.splitlines():
            if m := re.match(r"^(#{1,6})\s+(.*)$", line.strip()):
                result.headings.append((number, len(m.group(1)), m.group(2).strip()))
        out.append("")
    return out


def _body_font_size(doc, sample_pages: int = 6) -> float | None:
    """The modal font size across a sample of pages - i.e. the body text size."""
    sizes: list[float] = []
    for index in range(min(sample_pages, doc.page_count)):
        try:
            data = doc[index].get_text("dict")
        except Exception:  # noqa: BLE001
            continue
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if (span.get("text") or "").strip():
                        sizes.append(round(span.get("size", 0), 1))
    if not sizes:
        return None
    try:
        return statistics.mode(sizes)
    except statistics.StatisticsError:
        return statistics.median(sizes)


def _detect_headings(page, body_size: float) -> dict[str, int]:
    """Map heading text to a level, using font size plus shape heuristics.

    Two independent signals must agree before a line is called a heading: it is set larger
    (or bold) than the body, *and* it looks like a heading. Font size alone flags every
    figure caption and page header in a two-column paper.
    """
    found: dict[str, int] = {}
    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return found

    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not (3 <= len(text) <= 90) or text.endswith((".", ",", ";")):
                continue
            size = max(s.get("size", 0) for s in spans)
            bold = any("bold" in (s.get("font") or "").lower() for s in spans)
            larger = size >= body_size * HEADING_SIZE_RATIO
            if not (larger or bold):
                continue
            if HEADING_NOISE.search(text):
                continue
            # Digit-heavy lines are dates, identifiers, and page furniture, not headings.
            if sum(c.isdigit() for c in text) > len(text) * 0.3:
                continue
            if m := NUMBERED_HEADING.match(text):
                found[text] = min(m.group(1).count(".") + 1, 5)
            elif UNNUMBERED_HEADING.match(text) and larger:
                found[text] = 1
            elif larger and size >= body_size * 1.4 and _looks_like_a_heading(text):
                found[text] = 1
    return found


def _looks_like_a_heading(text: str) -> bool:
    """Shape check for the catch-all branch: a heading is short and title-ish."""
    words = text.split()
    if not (1 <= len(words) <= 12):
        return False
    if text.isupper():
        return True
    # Most words capitalized, and it does not read as a sentence fragment.
    capitalized = sum(1 for w in words if w[:1].isupper())
    return capitalized >= max(1, len(words) // 2)


# --- reading extracted text back -------------------------------------------


def split_pages(markdown: str) -> dict[int, str]:
    """Parse ``text/<citekey>.md`` back into ``{page_number: text}``.

    This is what the locator checker uses to verify that ``[p. 7]`` points at content that
    actually supports the claim (P7).
    """
    pages: dict[int, str] = {}
    matches = list(PAGE_MARKER_RE.finditer(markdown))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages[int(match.group(1))] = markdown[start:end].strip()
    return pages


def split_sections(markdown: str) -> dict[str, str]:
    """Parse extracted markdown into ``{section number: text}`` for ``[section 4.2]`` locators."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in markdown.splitlines():
        if m := re.match(r"^#{1,6}\s+(.*)$", line):
            if current:
                sections[current] = "\n".join(buffer).strip()
            heading = m.group(1).strip()
            number = NUMBERED_HEADING.match(heading)
            current = number.group(1) if number else heading.lower()
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections
