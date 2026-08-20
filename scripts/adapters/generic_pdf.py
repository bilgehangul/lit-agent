"""Read a plain folder of PDFs (spec section 8d).

No metadata exists, so it has to be recovered:

1. DOI or arXiv ID found in the PDF's own text and embedded metadata,
2. resolved against Crossref / arXiv / OpenAlex,
3. failing that, parsed from the filename,
4. failing that, the item is marked ``unidentified`` and reported for manual entry.

**Network calls are opt-in.** ``resolve_online`` defaults to False; the caller asks the user
first (spec section 8d). Without it the adapter still works, just with weaker metadata --
and it says so per item rather than pretending the metadata is good.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import Adapter, AdapterResult  # noqa: E402
from lib.models import (  # noqa: E402
    LibraryItem,
    clean_title,
    normalize_arxiv,
    normalize_doi,
)

DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b", re.I)
ARXIV_IN_TEXT = re.compile(r"(?i)arxiv\s*:\s*(\d{4}\.\d{4,5})(v\d+)?")

#: "Author et al. - 2024 - Title of the paper", the shape Zotero writes.
ZOTERO_FILENAME = re.compile(r"^(?P<author>.+?)\s*-\s*(?P<year>\d{4})\s*-\s*(?P<title>.+)$")
#: "author2024title" or "Author_2024_Title"
COMPACT_FILENAME = re.compile(r"^(?P<author>[A-Za-z]+)[_\- ]?(?P<year>(?:19|20)\d{2})[_\- ]?(?P<title>.*)$")

#: Be a good citizen: these are free public APIs with no key required.
REQUEST_DELAY = 0.35


class GenericPdfAdapter(Adapter):
    name = "generic_pdf"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.root = Path(self.config.get("path") or ".").resolve()
        self.resolve_online = bool(self.config.get("resolve_online", False))
        self.contact_email = self.config.get("contact_email", "")

    def available(self) -> tuple[bool, str]:
        if not self.root.is_dir():
            return False, f"directory does not exist: {self.root}"
        count = sum(1 for _ in self.root.rglob("*.pdf"))
        if not count:
            return False, f"no PDFs found under {self.root}"
        return True, f"{count} PDF(s) under {self.root}"

    # --- reading -----------------------------------------------------------

    def fetch(self, limit: int | None = None) -> AdapterResult:
        result = AdapterResult()
        ok, reason = self.available()
        if not ok:
            result.errors.append(("", reason))
            return result

        pdfs = sorted(p for p in self.root.rglob("*.pdf") if ".lit" not in p.parts)
        if limit:
            pdfs = pdfs[:limit]

        resolved_online = unidentified = 0
        client = self._open_client() if self.resolve_online else None
        try:
            for pdf in pdfs:
                try:
                    item, used_network = self._build_item(pdf, client)
                except Exception as exc:  # noqa: BLE001 - one bad PDF is not a failed run
                    result.errors.append((str(pdf), f"{type(exc).__name__}: {exc}"))
                    continue
                resolved_online += int(used_network)
                if "unidentified" in item.tags:
                    unidentified += 1
                result.items.append(item)
        finally:
            if client is not None:
                client.close()

        result.info.update({
            "items_read": len(result.items),
            "resolve_online": self.resolve_online,
            "resolved_online": resolved_online,
            "unidentified": unidentified,
        })
        if unidentified:
            result.info["unidentified_warning"] = (
                f"{unidentified} PDF(s) could not be identified. Their notes will carry "
                "placeholder metadata until you correct it.")
        if not self.resolve_online:
            result.info["offline_note"] = (
                "Metadata came from the PDFs and their filenames only; online resolution "
                "was not enabled, so titles and venues may be imprecise.")
        return result

    def _open_client(self):
        import httpx
        contact = f"; mailto:{self.contact_email}" if self.contact_email else ""
        return httpx.Client(
            timeout=20, follow_redirects=True,
            headers={"User-Agent":
                     f"lit-agent/0.1 (+https://github.com/bilgehangul/lit-agent{contact})"})

    def _build_item(self, pdf: Path, client) -> tuple[LibraryItem, bool]:
        doi, arxiv, embedded, first_page = self._identifiers(pdf)
        metadata: dict[str, Any] = {
            "id": pdf.stem, "type": "document", "title": "", "author": [],
        }
        if doi:
            metadata["DOI"] = doi
        if arxiv:
            metadata["arxiv_id"] = arxiv

        used_network = False
        if client is not None and (doi or arxiv):
            fetched = (self._from_crossref(client, doi) if doi
                       else self._from_arxiv(client, arxiv))
            if not fetched and doi:
                fetched = self._from_openalex(client, doi)
            if fetched:
                metadata.update(fetched)
                used_network = True

        if not metadata.get("title"):
            metadata.update(self._from_embedded(embedded, first_page))
        if not metadata.get("title"):
            metadata.update(self._from_filename(pdf))

        tags: list[str] = []
        warnings: list[str] = []
        if not metadata.get("title"):
            metadata["title"] = pdf.stem
            tags.append("unidentified")
            warnings.append(
                "could not identify this PDF: no DOI, no arXiv ID, and the filename did "
                "not parse. Metadata is a placeholder and needs manual correction.")
        elif not used_network and not metadata.get("author"):
            warnings.append("metadata came from the filename only; authors are unknown")

        item = LibraryItem(source_id=str(pdf.relative_to(self.root)),
                           metadata=metadata, attachments=[pdf],
                           tags=tags, warnings=warnings)
        return item, used_network

    # --- identifier discovery ---------------------------------------------

    def _identifiers(self, pdf: Path) -> tuple[str, str, dict[str, Any], str]:
        """Look for a DOI/arXiv ID in the embedded metadata and the first two pages."""
        import pymupdf
        doi = arxiv = ""
        embedded: dict[str, Any] = {}
        text = ""
        try:
            doc = pymupdf.open(pdf)
            embedded = dict(doc.metadata or {})
            for index in range(min(2, doc.page_count)):
                text += doc[index].get_text() or ""
            doc.close()
        except Exception:  # noqa: BLE001
            return "", "", {}, ""

        haystack = " ".join(str(v) for v in embedded.values()) + " " + text
        if m := DOI_IN_TEXT.search(haystack):
            doi = normalize_doi(m.group(0).rstrip(".,;)"))
        if a := normalize_arxiv(haystack):
            arxiv = a
        elif m := ARXIV_IN_TEXT.search(haystack):
            arxiv = m.group(1)
        return doi, arxiv, embedded, text[:3000]

    def _from_embedded(self, embedded: dict[str, Any], first_page: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        title = clean_title(str(embedded.get("title") or "").strip())
        # PDF producers love writing the filename or "untitled" into the title field.
        if title and len(title) > 8 and not title.lower().startswith(("microsoft word", "untitled")):
            out["title"] = title
        if author := str(embedded.get("author") or "").strip():
            out["author"] = [{"literal": author}]
        return out

    def _from_filename(self, pdf: Path) -> dict[str, Any]:
        stem = pdf.stem.replace("_", " ").strip()
        for pattern in (ZOTERO_FILENAME, COMPACT_FILENAME):
            if m := pattern.match(stem):
                title = clean_title(m.group("title") or "")
                if not title:
                    continue
                family = re.split(r"\s+(?:et al\.?|and|&)", m.group("author"))[0].strip()
                return {
                    "title": title,
                    "author": [{"family": family, "given": ""}] if family else [],
                    "issued": {"date-parts": [[int(m.group("year"))]]},
                }
        if len(stem) > 12:
            return {"title": clean_title(stem)}
        return {}

    # --- online resolution -------------------------------------------------

    def _from_crossref(self, client, doi: str) -> dict[str, Any]:
        time.sleep(REQUEST_DELAY)
        try:
            resp = client.get(f"https://api.crossref.org/works/{doi}")
            if resp.status_code != 200:
                return {}
            message = resp.json()["message"]
        except Exception:  # noqa: BLE001
            return {}
        issued = (message.get("issued") or {}).get("date-parts") or []
        out: dict[str, Any] = {
            "title": clean_title((message.get("title") or [""])[0]),
            "author": [{"family": a.get("family", ""), "given": a.get("given", "")}
                       for a in (message.get("author") or [])],
            "container-title": (message.get("container-title") or [""])[0],
            "type": message.get("type", "document"),
            "publisher": message.get("publisher", ""),
            "volume": message.get("volume", ""),
            "page": message.get("page", ""),
            "DOI": doi,
        }
        if issued and issued[0]:
            out["issued"] = {"date-parts": [[issued[0][0]]]}
        return {k: v for k, v in out.items() if v}

    def _from_openalex(self, client, doi: str) -> dict[str, Any]:
        time.sleep(REQUEST_DELAY)
        try:
            params = {"mailto": self.contact_email} if self.contact_email else {}
            resp = client.get(f"https://api.openalex.org/works/doi:{doi}", params=params)
            if resp.status_code != 200:
                return {}
            data = resp.json()
        except Exception:  # noqa: BLE001
            return {}
        source = (data.get("primary_location") or {}).get("source") or {}
        out: dict[str, Any] = {
            "title": clean_title(data.get("title") or ""),
            "author": [{"literal": (a.get("author") or {}).get("display_name", "")}
                       for a in (data.get("authorships") or [])],
            "container-title": source.get("display_name") or "",
            "DOI": doi,
        }
        if year := data.get("publication_year"):
            out["issued"] = {"date-parts": [[year]]}
        return {k: v for k, v in out.items() if v}

    def _from_arxiv(self, client, arxiv_id: str) -> dict[str, Any]:
        time.sleep(REQUEST_DELAY)
        try:
            resp = client.get("http://export.arxiv.org/api/query",
                              params={"id_list": arxiv_id})
            if resp.status_code != 200:
                return {}
            body = resp.text
        except Exception:  # noqa: BLE001
            return {}
        from xml.etree import ElementTree
        atom = {"a": "http://www.w3.org/2005/Atom"}
        try:
            entry = ElementTree.fromstring(body).find("a:entry", atom)
        except ElementTree.ParseError:
            return {}
        if entry is None:
            return {}
        out: dict[str, Any] = {
            "title": clean_title(" ".join((entry.findtext("a:title", "", atom) or "").split())),
            "author": [{"literal": (n.findtext("a:name", "", atom) or "")}
                       for n in entry.findall("a:author", atom)],
            "abstract": " ".join((entry.findtext("a:summary", "", atom) or "").split()),
            "type": "article",
            "arxiv_id": arxiv_id,
            "container-title": "arXiv",
        }
        published = entry.findtext("a:published", "", atom) or ""
        if published[:4].isdigit():
            out["issued"] = {"date-parts": [[int(published[:4])]]}
        return {k: v for k, v in out.items() if v}
