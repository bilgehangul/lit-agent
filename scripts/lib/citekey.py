"""Citekey generation and stability (spec section 6).

The citekey is the join key across the whole corpus, and it ends up in the user's own
manuscript. **It must never silently change between runs.** So generation is deterministic,
collisions get a stable suffix, and every mapping is recorded so an existing assignment
always wins over a freshly computed one.

Better BibTeX citekeys are preferred when available. They are not available in the
development environment (M0/S1 - the plugin is not installed), so the generated scheme is
the primary path here, not a fallback.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .models import LibraryItem

#: Skipped when picking the title word. Kept small on purpose - an aggressive list makes
#: citekeys unrecognizable to the person who has to type them.
STOPWORDS = frozenset("""
a an the and or but for nor of on in at to from by with without into over under
this that these those is are was were be been being do does did can could will
would shall should may might must not no its it as if then than so such
toward towards via using use used about against between during through
""".split())


def _fold(text: str) -> str:
    """Strip accents and anything that is not a letter or digit."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]", "", ascii_only)


def author_part(family: str) -> str:
    """First author's family name, folded and lowercased."""
    folded = _fold(family or "").lower()
    return folded or "anon"


def year_part(year: int | None) -> str:
    return str(year) if year else "nodate"


def title_part(title: str) -> str:
    """First title word longer than 3 characters that is not a stopword."""
    from .models import clean_title
    for raw in re.split(r"[\s\-–—/:]+", clean_title(title or "")):
        word = _fold(raw).lower()
        if len(word) > 3 and word not in STOPWORDS and not word.isdigit():
            return word
    # Nothing usable: fall back to the first token of any length, then to a constant.
    for raw in re.split(r"\s+", clean_title(title or "")):
        if word := _fold(raw).lower():
            return word
    return "untitled"


def base_citekey(family: str, year: int | None, title: str) -> str:
    return f"{author_part(family)}{year_part(year)}{title_part(title)}"


def _suffixes() -> Iterable[str]:
    """``a``..``z``, then ``aa``..``az``, and so on. Stable and ordered."""
    from itertools import count, product
    from string import ascii_lowercase
    for width in count(1):
        for combo in product(ascii_lowercase, repeat=width):
            yield "".join(combo)


class CitekeyAllocator:
    """Assigns citekeys, keeping existing assignments stable across runs.

    ``existing`` maps ``source_id -> citekey`` from a previous run (``state.json``). An item
    that already has a citekey keeps it, even if its metadata has since changed -- otherwise
    a corrected author name would silently rewrite a key the user has already cited.
    """

    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.by_source: dict[str, str] = dict(existing or {})
        self.taken: set[str] = set(self.by_source.values())
        self.generated: dict[str, str] = {}

    def allocate(self, item: "LibraryItem", preferred: str | None = None) -> str:
        """Return the citekey for ``item``, assigning one if it has none."""
        if previous := self.by_source.get(item.source_id):
            return previous

        if preferred:
            candidate = _fold(preferred) or preferred.strip()
            if candidate and candidate not in self.taken:
                return self._record(item.source_id, candidate)

        base = base_citekey(item.first_author_family, item.year, item.title)
        if base not in self.taken:
            return self._record(item.source_id, base)
        for suffix in _suffixes():
            candidate = f"{base}{suffix}"
            if candidate not in self.taken:
                return self._record(item.source_id, candidate)
        raise RuntimeError("exhausted citekey suffixes")  # unreachable in practice

    def _record(self, source_id: str, citekey: str) -> str:
        self.by_source[source_id] = citekey
        self.taken.add(citekey)
        self.generated[source_id] = citekey
        return citekey

    def mapping(self) -> dict[str, str]:
        return dict(self.by_source)
