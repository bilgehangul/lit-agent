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

#: Page markers carry both numbering systems, because papers have two.
#:
#: A PDF's physical page index runs 1..N. The number *printed on the page* is often something
#: else entirely: Del Alamo et al. is 24 physical pages labelled 2053-2076, Kelley et al. is
#: 10 pages printed 3393-3402. A reader citing "p. 2055" is using the printed number, and
#: they are not wrong -- that is what the published paper says and what every other citation
#: of it will use.
#:
#: Recording only the physical index would make a correct journal citation look like a
#: fabricated one, which is the worst error this system can make: a false accusation is
#: harder to recover from than a missed check.
PAGE_MARKER = "<!-- page {n} -->"
PAGE_MARKER_LABELLED = "<!-- page {n} label={label} -->"
PAGE_MARKER_RE = re.compile(
    r"^<!--\s*page\s+(?P<n>\d+)(?:\s+label=(?P<label>\S+))?\s*-->\s*$", re.M)

#: A printed page number detected in a header or footer must agree with its neighbours by
#: this rule before it is trusted: consecutive pages differ by exactly one.
PRINTED_NUMBER_RE = re.compile(r"\b(\d{1,5})\b")

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
    #: physical page index -> printed page label, when the two differ.
    page_labels: dict[int, str] = field(default_factory=dict)
    #: How the labels were obtained: "embedded", "detected", or "" when they match the index.
    label_source: str = ""
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

    label_note = ""
    if result.page_labels:
        first = result.page_labels.get(1, "")
        last = result.page_labels.get(result.pages, "")
        label_note = (f" printed_pages={first}-{last} "
                      f"label_source={result.label_source}")
    header = (f"<!-- lit-agent: citekey={citekey or '?'} pages={result.pages} "
              f"extractor={result.extractor} chars={result.chars}{label_note} -->")
    result.markdown = header + "\n\n" + "\n".join(body).strip() + "\n"
    return result


def _extract_fast(doc, result: ExtractionResult) -> list[str]:
    """The default path: raw text per page, plus font-size heading detection."""
    body_size = _body_font_size(doc)
    result.page_labels, result.label_source = page_labels(doc)
    out: list[str] = []
    for index, page in enumerate(doc):
        number = index + 1
        label = result.page_labels.get(number)
        out.append(PAGE_MARKER_LABELLED.format(n=number, label=label) if label
                   else PAGE_MARKER.format(n=number))
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


def page_labels(doc) -> tuple[dict[int, str], str]:
    """Map physical page index (1-based) to the number printed on that page.

    Two sources, in order of trust:

    1. **Embedded labels.** Many publisher PDFs carry a proper page-label table; PyMuPDF
       exposes it. Del Alamo et al. declares ``firstpagenum: 2053``, so its 24 physical
       pages are printed 2053-2076.
    2. **Detected from the page.** When no table exists, look for a number in the header or
       footer region and accept it only if it forms a consistent run -- consecutive pages
       differing by exactly one. A single stray number is not evidence; an arithmetic
       sequence across most of the document is.

    Returns ``({}, "")`` when the printed numbers simply are the physical index, which is
    the common case and needs no special handling.
    """
    labels: dict[int, str] = {}

    try:
        entries = doc.get_page_labels() or []
    except Exception:  # noqa: BLE001 - malformed label tables exist in the wild
        entries = []
    for entry in entries:
        start = int(entry.get("startpage", 0))
        first = int(entry.get("firstpagenum", 1))
        prefix = entry.get("prefix", "") or ""
        style = entry.get("style", "D")
        if style != "D" and not prefix:
            continue                      # roman/letter styles: leave to detection
        for offset in range(start, doc.page_count):
            labels[offset + 1] = f"{prefix}{first + offset - start}"
    if labels and any(labels.get(n) != str(n) for n in labels):
        return labels, "embedded"

    detected = _detect_printed_numbers(doc)
    if detected and any(detected.get(n) != str(n) for n in detected):
        return detected, "detected"
    return {}, ""


def _detect_printed_numbers(doc, margin: float = 0.12) -> dict[int, str]:
    """Find printed page numbers in header/footer bands, keeping only a consistent run."""
    candidates: dict[int, set[int]] = {}
    for index in range(doc.page_count):
        page = doc[index]
        height = page.rect.height
        found: set[int] = set()
        try:
            blocks = page.get_text("blocks")
        except Exception:  # noqa: BLE001
            continue
        for block in blocks:
            y0, y1, text = block[1], block[3], str(block[4] or "")
            in_header = y1 < height * margin
            in_footer = y0 > height * (1 - margin)
            if not (in_header or in_footer):
                continue
            for match in PRINTED_NUMBER_RE.finditer(text):
                value = int(match.group(1))
                # A year or a DOI fragment is not a page number.
                if 1 <= value <= 20000 and not (1900 <= value <= 2100):
                    found.add(value)
        candidates[index + 1] = found

    # Keep the offset that makes the most pages agree: label(n) == n + offset.
    offsets: dict[int, int] = {}
    for physical, values in candidates.items():
        for value in values:
            offsets[value - physical] = offsets.get(value - physical, 0) + 1
    if not offsets:
        return {}
    best_offset, agreeing = max(offsets.items(), key=lambda kv: kv[1])
    # Demand that most of the document agrees before trusting the offset.
    if agreeing < max(3, doc.page_count * 0.6) or best_offset == 0:
        return {}
    return {n: str(n + best_offset) for n in range(1, doc.page_count + 1)}


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

    **Both numbering systems are keys into the same text.** For a paper whose 24 physical
    pages are printed 2053-2076, both ``1`` and ``2053`` return page one. A locator written
    either way therefore resolves, and only a number belonging to neither system is a
    fabrication (P7).
    """
    pages: dict[int, str] = {}
    matches = list(PAGE_MARKER_RE.finditer(markdown))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        pages[int(match.group("n"))] = body
        if (label := match.group("label")) and label.isdigit():
            pages.setdefault(int(label), body)
    return pages


def page_numbering(markdown: str) -> dict[int, str]:
    """``{physical page: printed label}`` for the pages that carry one."""
    return {int(m.group("n")): m.group("label")
            for m in PAGE_MARKER_RE.finditer(markdown) if m.group("label")}


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
