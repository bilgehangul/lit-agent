"""The normalized record every source adapter returns (spec section 8).

Adapters differ wildly -- a SQLite schema, a REST API, a BibTeX file, a folder of PDFs --
but everything downstream sees only ``LibraryItem``. Adding a source means writing an
adapter, never touching the pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any


@dataclass
class Note:
    """A user-authored note. ``html`` is preserved verbatim -- never rewritten (spec 6.9)."""

    html: str
    text: str
    title: str = ""
    source_id: str = ""
    is_standalone: bool = False

    @staticmethod
    def html_to_text(html: str) -> str:
        """Flatten Zotero note HTML to readable plaintext.

        Deliberately conservative: block elements become line breaks, everything else is
        stripped. The original HTML is kept alongside, so nothing is lost by being cautious.
        """
        if not html:
            return ""
        text = re.sub(r"(?i)<br\s*/?>", "\n", html)
        text = re.sub(r"(?i)</(p|div|li|h[1-6]|blockquote|tr)>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "- ", text)
        text = re.sub(r"<[^>]+>", "", text)
        for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                             ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
            text = text.replace(entity, char)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


@dataclass
class Annotation:
    """A highlight or comment from a PDF reader, with its locator."""

    type: str                      # highlight | note | underline | image | ink
    text: str = ""                 # the highlighted text
    comment: str = ""              # the user's comment on it
    page_label: str = ""           # printed page label, may differ from the index
    page_index: int | None = None  # 0-based physical page
    color: str = ""
    source_id: str = ""


@dataclass
class LibraryItem:
    """One work, normalized. Spec section 8."""

    source_id: str                                   # adapter-native ID
    citekey: str = ""
    metadata: dict[str, Any] = dc_field(default_factory=dict)   # CSL-JSON shaped
    attachments: list[Path] = dc_field(default_factory=list)    # resolved local PDFs
    notes: list[Note] = dc_field(default_factory=list)
    annotations: list[Annotation] = dc_field(default_factory=list)
    tags: list[str] = dc_field(default_factory=list)
    collections: list[str] = dc_field(default_factory=list)
    #: Anything the adapter could not resolve, carried forward so the run report can name
    #: it instead of dropping it silently (P4).
    warnings: list[str] = dc_field(default_factory=list)

    # --- convenience accessors used across the pipeline --------------------

    @property
    def title(self) -> str:
        return (self.metadata.get("title") or "").strip()

    @property
    def year(self) -> int | None:
        issued = self.metadata.get("issued") or {}
        parts = issued.get("date-parts") or []
        if parts and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                return None
        return None

    @property
    def doi(self) -> str:
        return normalize_doi(self.metadata.get("DOI") or "")

    @property
    def arxiv_id(self) -> str:
        return normalize_arxiv(
            self.metadata.get("arxiv_id")
            or self.metadata.get("note", "")
            or self.metadata.get("URL", "")
            or self.doi)

    @property
    def authors(self) -> list[str]:
        out = []
        for a in self.metadata.get("author") or []:
            if isinstance(a, dict):
                name = " ".join(x for x in (a.get("given"), a.get("family")) if x)
                out.append(name or a.get("literal", ""))
            else:
                out.append(str(a))
        return [a for a in out if a]

    @property
    def first_author_family(self) -> str:
        for a in self.metadata.get("author") or []:
            if isinstance(a, dict):
                if fam := (a.get("family") or "").strip():
                    return fam
                if lit := (a.get("literal") or "").strip():
                    return lit.split()[-1]
            elif str(a).strip():
                return str(a).strip().split()[-1]
        return ""

    @property
    def venue(self) -> str:
        for key in ("container-title", "publisher", "collection-title"):
            if value := (self.metadata.get(key) or "").strip():
                return value
        return ""

    @property
    def item_type(self) -> str:
        return self.metadata.get("type") or "document"

    @property
    def pdfs(self) -> list[Path]:
        return [p for p in self.attachments if p.suffix.lower() == ".pdf"]

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdfs)

    # --- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["attachments"] = [str(p) for p in self.attachments]
        return d

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LibraryItem":
        return cls(
            source_id=d.get("source_id", ""),
            citekey=d.get("citekey", ""),
            metadata=d.get("metadata") or {},
            attachments=[Path(p) for p in (d.get("attachments") or [])],
            notes=[Note(**n) for n in (d.get("notes") or [])],
            annotations=[Annotation(**a) for a in (d.get("annotations") or [])],
            tags=list(d.get("tags") or []),
            collections=list(d.get("collections") or []),
            warnings=list(d.get("warnings") or []),
        )


# --- identifier normalization ---------------------------------------------


def normalize_doi(value: str) -> str:
    """Strip the many prefixes a DOI arrives with, lowercase it, so dedupe can match."""
    if not value:
        return ""
    doi = value.strip()
    doi = re.sub(r"(?i)^(https?://)?(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"(?i)^doi:\s*", "", doi)
    return doi.strip().lower()


ARXIV_PATTERNS = (
    re.compile(r"(?i)arxiv[:/ ]\s*(\d{4}\.\d{4,5})(v\d+)?"),
    re.compile(r"(?i)arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?"),
    re.compile(r"(?i)10\.48550/arxiv\.(\d{4}\.\d{4,5})"),
    re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$"),
    # Pre-2007 identifiers, e.g. cs/0501001
    re.compile(r"(?i)arxiv[:/ ]\s*([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?"),
)


def normalize_arxiv(value: str) -> str:
    """Extract a bare arXiv ID (no version suffix) from any of its usual spellings."""
    if not value:
        return ""
    for pattern in ARXIV_PATTERNS:
        if m := pattern.search(value.strip()):
            return m.group(1).lower()
    return ""


TITLE_JUNK = re.compile(
    r"^\s*(\(pdf\)|\[pdf\]|pdf\s*[-:]|full text|download)\s*[-:]?\s*", re.I)


def clean_title(title: str) -> str:
    """Strip web-import junk that would otherwise poison citekeys (M0/S2-d)."""
    if not title:
        return ""
    cleaned = TITLE_JUNK.sub("", title.strip())
    cleaned = re.sub(r"\s*\|\s*[^|]{1,40}$", "", cleaned)   # trailing " | Site Name"
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def parse_zotero_date(value: str) -> tuple[int | None, str]:
    """Split Zotero's packed ``date`` column into (year, original string).

    Zotero stores ``"<sql-date> <original>"`` in one column, with ``00`` for unknown parts::

        "2024-08-25 2024-08-25"        -> (2024, "2024-08-25")
        "2011-12-05 December 5, 2011"  -> (2011, "December 5, 2011")
        "2012-08-00 08/2012"           -> (2012, "08/2012")

    Parsing the whole field yields nonsense years, which would poison citekeys and BibTeX
    (M0/S2-c).
    """
    if not value:
        return None, ""
    raw = value.strip()
    head = raw[:4]
    year = int(head) if head.isdigit() and 1000 <= int(head) <= 2999 else None
    original = raw.split(" ", 1)[1].strip() if " " in raw else raw
    if year is None:
        if m := re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", raw):
            year = int(m.group(1))
    return year, original
