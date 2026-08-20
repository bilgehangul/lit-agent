"""Source adapter interface and dedupe (spec section 8).

Every adapter yields ``LibraryItem``. Nothing downstream knows which adapter produced what.

Adapters follow two rules:

* **Never fail the whole run for one bad item.** Collect the problem into the item's
  ``warnings`` or into ``AdapterResult.errors`` and keep going (spec section 9, stage 1).
* **Never modify the source.** Every adapter here is strictly read-only.
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.models import LibraryItem  # noqa: E402


@dataclass
class AdapterResult:
    items: list[LibraryItem] = field(default_factory=list)
    #: ``(source_id, message)`` for items that could not be read at all.
    errors: list[tuple[str, str]] = field(default_factory=list)
    #: Facts about the read worth surfacing in the run report (schema version, counts).
    info: dict[str, Any] = field(default_factory=dict)


class Adapter(ABC):
    """Base class for every library source."""

    #: Stable id used in config and on the command line.
    name: str = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """``(usable, human-readable reason)``. Never raises."""

    @abstractmethod
    def fetch(self, limit: int | None = None) -> AdapterResult:
        """Read the library. Never raises for a single bad item."""

    def __iter__(self) -> Iterator[LibraryItem]:
        return iter(self.fetch().items)


# --- dedupe ----------------------------------------------------------------


def normalize_title(title: str) -> str:
    """Aggressively normalized title, for last-resort matching."""
    from lib.models import clean_title
    text = unicodedata.normalize("NFKD", clean_title(title or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dedupe_key(item: LibraryItem) -> tuple[str, str]:
    """The identity used for dedupe: DOI, then arXiv ID, then normalized title+year.

    Returns ``(kind, value)`` so the run report can say *why* two items merged -- a title
    match is far weaker evidence than a DOI match and the user deserves to know which it was.
    """
    if doi := item.doi:
        return ("doi", doi)
    if arxiv := item.arxiv_id:
        return ("arxiv", arxiv)
    title = normalize_title(item.title)
    if title:
        return ("title+year", f"{title}|{item.year or ''}")
    return ("source_id", item.source_id)


def merge_items(primary: LibraryItem, other: LibraryItem) -> LibraryItem:
    """Fold ``other`` into ``primary``, preferring whichever actually has data.

    Union rather than overwrite: two records of the same paper often carry different
    attachments, notes, or tags, and losing either would be a silent gap.
    """
    for key, value in (other.metadata or {}).items():
        if value in (None, "", [], {}):
            continue
        if primary.metadata.get(key) in (None, "", [], {}):
            primary.metadata[key] = value

    seen = {p.resolve() if p.exists() else p for p in primary.attachments}
    for path in other.attachments:
        resolved = path.resolve() if path.exists() else path
        if resolved not in seen:
            primary.attachments.append(path)
            seen.add(resolved)

    existing_notes = {n.text.strip() for n in primary.notes}
    primary.notes.extend(n for n in other.notes if n.text.strip() not in existing_notes)

    existing_annotations = {(a.text, a.comment, a.page_label) for a in primary.annotations}
    primary.annotations.extend(
        a for a in other.annotations
        if (a.text, a.comment, a.page_label) not in existing_annotations)

    primary.tags = sorted(set(primary.tags) | set(other.tags))
    primary.collections = sorted(set(primary.collections) | set(other.collections))
    primary.warnings.extend(other.warnings)
    return primary


@dataclass
class DedupeReport:
    kept: list[LibraryItem] = field(default_factory=list)
    #: ``(kept_source_id, dropped_source_id, kind, value)``
    merged: list[tuple[str, str, str, str]] = field(default_factory=list)

    def summary(self) -> str:
        if not self.merged:
            return "no duplicates found"
        by_kind: dict[str, int] = {}
        for _, _, kind, _ in self.merged:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        parts = ", ".join(f"{n} by {kind}" for kind, n in sorted(by_kind.items()))
        return f"merged {len(self.merged)} duplicate(s): {parts}"


def dedupe(items: list[LibraryItem]) -> DedupeReport:
    """Merge duplicates on DOI, then arXiv ID, then normalized title+year.

    An item with a PDF beats one without, so merging never loses the attachment.
    """
    report = DedupeReport()
    index: dict[tuple[str, str], LibraryItem] = {}
    for item in items:
        key = dedupe_key(item)
        if key[0] == "source_id":          # nothing to match on; keep as its own record
            report.kept.append(item)
            continue
        if existing := index.get(key):
            primary, secondary = (
                (item, existing) if item.has_pdf and not existing.has_pdf
                else (existing, item))
            if primary is item:            # the newcomer wins; swap it into place
                report.kept[report.kept.index(existing)] = item
                index[key] = item
            merge_items(primary, secondary)
            report.merged.append((primary.source_id, secondary.source_id, key[0], key[1]))
        else:
            index[key] = item
            report.kept.append(item)
    return report
