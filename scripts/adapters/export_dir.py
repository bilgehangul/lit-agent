"""Read a Zotero export directory (spec section 8c).

Accepts BibTeX/BibLaTeX, CSL-JSON, Zotero CSV, or Zotero RDF, plus an adjacent files
folder. Attachment matching is layered: the format's own file field first, then filename
heuristics, and whatever is still unmatched is **reported, never dropped silently** (P4).

Note recovery varies by format and the adapter says which it managed:
RDF and CSV carry notes; BibTeX and CSL-JSON generally do not.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import Adapter, AdapterResult, normalize_title  # noqa: E402
from lib.models import (  # noqa: E402
    LibraryItem,
    Note,
    clean_title,
    normalize_arxiv,
    normalize_doi,
)

BIB_TYPE = {
    "article": "article-journal", "inproceedings": "paper-conference",
    "conference": "paper-conference", "incollection": "chapter",
    "book": "book", "phdthesis": "thesis", "mastersthesis": "thesis",
    "techreport": "report", "misc": "document", "unpublished": "manuscript",
}


class ExportDirAdapter(Adapter):
    name = "export_dir"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.root = Path(self.config.get("path") or ".").resolve()

    def available(self) -> tuple[bool, str]:
        if not self.root.is_dir():
            return False, f"export directory does not exist: {self.root}"
        files = self._export_files()
        if not files:
            return False, (f"no .bib / .json / .csv / .rdf export file found under {self.root}")
        return True, f"found {', '.join(f.name for f in files[:4])} under {self.root}"

    def _export_files(self) -> list[Path]:
        out = []
        for pattern in ("*.bib", "*.bibtex", "*.json", "*.csv", "*.rdf"):
            out.extend(sorted(self.root.glob(pattern)))
        return [f for f in out if f.name != "figures.json"]

    # --- reading -----------------------------------------------------------

    def fetch(self, limit: int | None = None) -> AdapterResult:
        result = AdapterResult()
        ok, reason = self.available()
        if not ok:
            result.errors.append(("", reason))
            return result

        pdf_index = self._index_pdfs()
        formats_read: list[str] = []
        for path in self._export_files():
            try:
                if path.suffix.lower() in (".bib", ".bibtex"):
                    items = self._read_bibtex(path)
                elif path.suffix.lower() == ".json":
                    items = self._read_csl_json(path)
                elif path.suffix.lower() == ".csv":
                    items = self._read_csv(path)
                else:
                    items = self._read_rdf(path)
            except Exception as exc:  # noqa: BLE001 - a bad file is not a failed run
                result.errors.append((path.name, f"{type(exc).__name__}: {exc}"))
                continue
            if items:
                formats_read.append(path.suffix.lstrip("."))
            result.items.extend(items)

        unmatched = 0
        for item in result.items:
            if not item.attachments:
                matched = self._match_pdf(item, pdf_index)
                if matched:
                    item.attachments.append(matched)
                else:
                    unmatched += 1
                    item.warnings.append("no PDF matched for this item")

        if limit:
            result.items = result.items[:limit]
        result.info.update({
            "items_read": len(result.items),
            "formats": sorted(set(formats_read)),
            "pdfs_available": len(pdf_index),
            "unmatched_items": unmatched,
            "notes_recovered": sum(len(i.notes) for i in result.items),
        })
        if unmatched:
            # Loud, not silent: an unmatched item cannot be analyzed at all.
            result.info["unmatched_warning"] = (
                f"{unmatched} item(s) have no PDF and will be skipped by /lit-analyze")
        return result

    # --- attachment matching ----------------------------------------------

    def _index_pdfs(self) -> dict[str, Path]:
        """``normalized stem -> path`` for every PDF under the export directory."""
        index: dict[str, Path] = {}
        for pdf in self.root.rglob("*.pdf"):
            index.setdefault(normalize_title(pdf.stem), pdf)
        return index

    def _match_pdf(self, item: LibraryItem, index: dict[str, Path]) -> Path | None:
        """Filename heuristics, after the format's own file field has already been tried."""
        title = normalize_title(item.title)
        if not title:
            return None
        if hit := index.get(title):
            return hit
        # Zotero exports are usually "Author et al. - YEAR - Title".
        for stem, path in index.items():
            if title and (title in stem or stem.endswith(title)):
                return path
        # Last resort: author + year + a distinctive title word.
        family = normalize_title(item.first_author_family)
        year = str(item.year or "")
        if family and year:
            for stem, path in index.items():
                if family in stem and year in stem:
                    return path
        return None

    def _resolve_file_field(self, value: str) -> list[Path]:
        """Parse a BibTeX/CSV ``file`` field, which packs entries as ``desc:path:mime``."""
        paths: list[Path] = []
        for entry in re.split(r"[;]", value or ""):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            candidates = [p for p in parts if p.lower().endswith(".pdf")] or [entry]
            for candidate in candidates:
                candidate = candidate.strip().replace("\\:", ":")
                path = Path(candidate)
                if not path.is_absolute():
                    path = self.root / candidate
                if path.is_file():
                    paths.append(path)
        return paths

    # --- format readers ----------------------------------------------------

    def _read_bibtex(self, path: Path) -> list[LibraryItem]:
        import bibtexparser
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        parser.ignore_nonstandard_types = False
        database = bibtexparser.loads(path.read_text(encoding="utf-8", errors="replace"), parser)

        items = []
        for entry in database.entries:
            key = entry.get("ID") or entry.get("id") or ""
            authors = []
            for name in re.split(r"\s+and\s+", entry.get("author", "")):
                name = name.strip().strip("{}")
                if not name:
                    continue
                if "," in name:
                    family, _, given = name.partition(",")
                    authors.append({"family": family.strip(), "given": given.strip()})
                else:
                    parts = name.split()
                    authors.append({"family": parts[-1], "given": " ".join(parts[:-1])})

            year = None
            if (raw_year := entry.get("year", "")).strip()[:4].isdigit():
                year = int(raw_year.strip()[:4])

            metadata: dict[str, Any] = {
                "id": key,
                "type": BIB_TYPE.get((entry.get("ENTRYTYPE") or "").lower(), "document"),
                "title": clean_title((entry.get("title") or "").strip("{}")),
                "author": authors,
                "container-title": (entry.get("journal") or entry.get("booktitle") or ""),
                "DOI": normalize_doi(entry.get("doi", "")),
                "URL": entry.get("url", ""),
                "abstract": entry.get("abstract", ""),
                "volume": entry.get("volume", ""),
                "issue": entry.get("number", ""),
                "page": entry.get("pages", ""),
                "publisher": entry.get("publisher", ""),
            }
            if year:
                metadata["issued"] = {"date-parts": [[year]]}
            for candidate in (entry.get("eprint"), entry.get("archiveprefix"),
                              entry.get("note"), entry.get("url"), entry.get("doi")):
                if arxiv := normalize_arxiv(candidate or ""):
                    metadata["arxiv_id"] = arxiv
                    break

            item = LibraryItem(
                source_id=key, citekey=key, metadata=metadata,
                attachments=self._resolve_file_field(entry.get("file", "")),
                tags=[t.strip() for t in re.split(r"[,;]", entry.get("keywords", "")) if t.strip()],
            )
            items.append(item)
        return items

    def _read_csl_json(self, path: Path) -> list[LibraryItem]:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        if not isinstance(payload, list):
            return []

        items = []
        for record in payload:
            if not isinstance(record, dict) or not record.get("title"):
                continue
            metadata = dict(record)
            metadata["title"] = clean_title(record.get("title", ""))
            metadata["DOI"] = normalize_doi(record.get("DOI", ""))
            if arxiv := normalize_arxiv(record.get("URL", "") or record.get("DOI", "")):
                metadata["arxiv_id"] = arxiv
            items.append(LibraryItem(
                source_id=str(record.get("id") or metadata["title"][:60]),
                citekey=str(record.get("id") or ""),
                metadata=metadata,
                tags=[k.get("keyword", "") if isinstance(k, dict) else str(k)
                      for k in (record.get("keyword") or [])],
            ))
        return items

    def _read_csv(self, path: Path) -> list[LibraryItem]:
        """Zotero CSV export. Carries notes in a ``Notes`` column."""
        items = []
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
            for row in csv.DictReader(fh):
                title = clean_title(row.get("Title") or "")
                if not title:
                    continue
                authors = []
                for name in (row.get("Author") or "").split(";"):
                    name = name.strip()
                    if not name:
                        continue
                    family, _, given = name.partition(",")
                    authors.append({"family": family.strip(), "given": given.strip()})
                year = None
                for source in (row.get("Publication Year"), row.get("Date")):
                    if (source or "").strip()[:4].isdigit():
                        year = int(source.strip()[:4])
                        break
                metadata: dict[str, Any] = {
                    "id": row.get("Key") or title[:60],
                    "type": row.get("Item Type") or "document",
                    "title": title,
                    "author": authors,
                    "container-title": row.get("Publication Title") or "",
                    "DOI": normalize_doi(row.get("DOI") or ""),
                    "URL": row.get("Url") or "",
                    "abstract": row.get("Abstract Note") or "",
                }
                if year:
                    metadata["issued"] = {"date-parts": [[year]]}
                if arxiv := normalize_arxiv(row.get("Extra") or row.get("Url") or ""):
                    metadata["arxiv_id"] = arxiv

                notes = []
                if raw := (row.get("Notes") or "").strip():
                    notes.append(Note(html=raw, text=Note.html_to_text(raw)))
                items.append(LibraryItem(
                    source_id=row.get("Key") or title[:60],
                    metadata=metadata,
                    attachments=self._resolve_file_field(row.get("File Attachments", "")),
                    notes=notes,
                    tags=[t.strip() for t in (row.get("Manual Tags") or "").split(";") if t.strip()],
                ))
        return items

    def _read_rdf(self, path: Path) -> list[LibraryItem]:
        """Zotero RDF. The richest export format - it carries notes."""
        ns = {
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcterms": "http://purl.org/dc/terms/",
            "bib": "http://purl.org/net/biblio#",
            "foaf": "http://xmlns.com/foaf/0.1/",
            "z": "http://www.zotero.org/namespaces/export#",
        }
        root = ElementTree.parse(path).getroot()
        rdf_about = f"{{{ns['rdf']}}}about"

        items = []
        for node in root:
            tag = node.tag.split("}")[-1]
            if tag in ("Memo", "Attachment", "Person", "Journal"):
                continue
            title_el = node.find("dc:title", ns)
            if title_el is None or not (title_el.text or "").strip():
                continue

            authors = []
            for author in node.findall("bib:authors/rdf:Seq/rdf:li/foaf:Person", ns):
                surname = author.findtext("foaf:surname", "", ns)
                given = author.findtext("foaf:givenName", "", ns)
                if surname or given:
                    authors.append({"family": surname, "given": given})

            date_text = node.findtext("dc:date", "", ns) or ""
            year = int(date_text[:4]) if date_text[:4].isdigit() else None

            identifiers = " ".join(
                el.text or "" for el in node.findall("dc:identifier", ns))
            doi = normalize_doi(node.findtext("dc:identifier/dcterms:URI/rdf:value", "", ns)
                                or identifiers)

            metadata: dict[str, Any] = {
                "id": node.get(rdf_about, ""),
                "type": tag.lower(),
                "title": clean_title(title_el.text or ""),
                "author": authors,
                "container-title": node.findtext("dcterms:isPartOf/bib:Journal/dc:title",
                                                 "", ns) or "",
                "DOI": doi,
                "abstract": node.findtext("dcterms:abstract", "", ns) or "",
            }
            if year:
                metadata["issued"] = {"date-parts": [[year]]}
            if arxiv := normalize_arxiv(identifiers):
                metadata["arxiv_id"] = arxiv

            notes = []
            for memo in node.findall("dcterms:isReferencedBy/bib:Memo", ns):
                html = memo.findtext("rdf:value", "", ns) or ""
                if html.strip():
                    notes.append(Note(html=html, text=Note.html_to_text(html)))

            attachments = []
            for resource in node.findall("link:link", ns) + node.findall("rdf:resource", ns):
                candidate = self.root / (resource.get(rdf_about) or "")
                if candidate.is_file():
                    attachments.append(candidate)

            items.append(LibraryItem(
                source_id=node.get(rdf_about, "") or metadata["title"][:60],
                metadata=metadata, notes=notes, attachments=attachments,
                tags=[t.text or "" for t in node.findall("dc:subject", ns) if t.text],
            ))
        return items
