"""Read a Zotero library over the local HTTP API (spec section 8a) - the preferred path.

Requires Zotero 7 running with the local API enabled in Advanced settings. **Read-only**:
the local API cannot write, so write-back goes through the Web API (spec section 12).

The same client also serves the Web API, which is the same v3 shape at a different base URL.

In the development environment this adapter is unavailable - the local API is switched off
(M0/S1) - so ``zotero_sqlite`` carries development. The two are kept behaviourally
interchangeable so that enabling the checkbox changes nothing downstream.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import Adapter, AdapterResult  # noqa: E402
from lib.models import (  # noqa: E402
    Annotation,
    LibraryItem,
    Note,
    clean_title,
    normalize_arxiv,
    normalize_doi,
)

DEFAULT_BASE = "http://127.0.0.1:23119"
PAGE_SIZE = 100

CSL_TYPE = {
    "journalArticle": "article-journal",
    "conferencePaper": "paper-conference",
    "preprint": "article",
    "bookSection": "chapter",
    "book": "book",
    "thesis": "thesis",
    "report": "report",
    "webpage": "webpage",
    "document": "document",
}


class ZoteroApiAdapter(Adapter):
    name = "zotero_api"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.base = (self.config.get("base_url") or DEFAULT_BASE).rstrip("/")
        self.library = self.config.get("library", "users/0")
        self.storage = Path(self.config.get("storage_path")
                            or (Path.home() / "Zotero" / "storage"))
        self.api_key = self.config.get("api_key")

    # --- plumbing ----------------------------------------------------------

    def _client(self):
        import httpx
        headers = {"Zotero-API-Version": "3"}
        if self.api_key:
            headers["Zotero-API-Key"] = self.api_key
        return httpx.Client(timeout=30, headers=headers)

    def _url(self, path: str) -> str:
        return f"{self.base}/api/{self.library}/{path.lstrip('/')}"

    def available(self) -> tuple[bool, str]:
        try:
            import httpx
        except ImportError:
            return False, "httpx is not installed"
        try:
            with self._client() as client:
                try:
                    client.get(f"{self.base}/connector/ping")
                except httpx.RequestError:
                    return False, (f"Zotero is not running (nothing listening on {self.base})")
                resp = client.get(self._url("items"), params={"limit": 1})
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        if resp.status_code == 403:
            return False, ("Zotero is running but its local API is off. Enable it in "
                           "Zotero > Settings > Advanced > 'Allow other applications on "
                           "this computer to communicate with Zotero'.")
        if resp.status_code != 200:
            return False, f"local API returned HTTP {resp.status_code}"
        return True, f"local API responding at {self.base}"

    def _paged(self, client, path: str, **params) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            resp = client.get(self._url(path), params={**params, "limit": PAGE_SIZE,
                                                       "start": start})
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                return
            yield from batch
            if len(batch) < PAGE_SIZE:
                return
            start += PAGE_SIZE

    # --- reading -----------------------------------------------------------

    def fetch(self, limit: int | None = None) -> AdapterResult:
        result = AdapterResult()
        ok, reason = self.available()
        if not ok:
            result.errors.append(("", reason))
            return result

        try:
            with self._client() as client:
                raw = list(self._paged(client, "items"))
                citekeys = self._better_bibtex_citekeys(client)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(("", f"{type(exc).__name__}: {exc}"))
            return result

        by_key = {r.get("key"): r for r in raw if r.get("key")}
        children: dict[str, list[dict[str, Any]]] = {}
        works: list[dict[str, Any]] = []
        for record in raw:
            data = record.get("data") or {}
            parent = data.get("parentItem")
            if data.get("itemType") in ("attachment", "note", "annotation"):
                if parent:
                    children.setdefault(parent, []).append(record)
            else:
                works.append(record)

        for record in works[:limit] if limit else works:
            try:
                item = self._build_item(record, children, by_key)
                if key := citekeys.get(item.source_id):
                    item.citekey = key
                result.items.append(item)
            except Exception as exc:  # noqa: BLE001 - one bad item never fails the run
                result.errors.append(((record.get("key") or ""),
                                      f"{type(exc).__name__}: {exc}"))

        result.info.update({
            "items_read": len(result.items),
            "better_bibtex": bool(citekeys),
            "base_url": self.base,
        })
        return result

    def _build_item(self, record: dict[str, Any], children, by_key) -> LibraryItem:
        data = record.get("data") or {}
        key = record.get("key", "")

        authors, editors = [], []
        for creator in data.get("creators") or []:
            entry = {"family": creator.get("lastName") or "",
                     "given": creator.get("firstName") or ""}
            if not entry["family"] and not entry["given"]:
                if literal := creator.get("name"):
                    entry = {"literal": literal}
                else:
                    continue
            (editors if creator.get("creatorType") == "editor" else authors).append(entry)

        year = None
        parsed = (record.get("meta") or {}).get("parsedDate") or data.get("date") or ""
        if parsed[:4].isdigit():
            year = int(parsed[:4])

        metadata: dict[str, Any] = {
            "id": key,
            "type": CSL_TYPE.get(data.get("itemType", ""), "document"),
            "zotero_type": data.get("itemType", ""),
            "title": clean_title(data.get("title") or ""),
            "author": authors,
            "container-title": (data.get("publicationTitle") or data.get("proceedingsTitle")
                                or data.get("bookTitle") or data.get("publisher") or ""),
            "DOI": normalize_doi(data.get("DOI") or ""),
            "URL": data.get("url") or "",
            "abstract": data.get("abstractNote") or "",
            "volume": data.get("volume") or "",
            "issue": data.get("issue") or "",
            "page": data.get("pages") or "",
            "publisher": data.get("publisher") or "",
            "date_original": data.get("date") or "",
        }
        if editors:
            metadata["editor"] = editors
        if year:
            metadata["issued"] = {"date-parts": [[year]]}
        for candidate in (data.get("archiveID"), data.get("extra"),
                          data.get("url"), data.get("DOI")):
            if arxiv := normalize_arxiv(candidate or ""):
                metadata["arxiv_id"] = arxiv
                break

        item = LibraryItem(
            source_id=key,
            metadata=metadata,
            tags=sorted({t.get("tag", "") for t in (data.get("tags") or []) if t.get("tag")}),
            collections=list(data.get("collections") or []),
        )

        for child in children.get(key, []):
            cdata = child.get("data") or {}
            ctype = cdata.get("itemType")
            if ctype == "note":
                html = cdata.get("note") or ""
                item.notes.append(Note(html=html, text=Note.html_to_text(html),
                                       source_id=child.get("key", "")))
            elif ctype == "attachment":
                path, warning = self._resolve_attachment(child)
                if path:
                    item.attachments.append(path)
                elif warning:
                    item.warnings.append(warning)
                # Annotations hang off the attachment.
                for grandchild in children.get(child.get("key", ""), []):
                    gdata = grandchild.get("data") or {}
                    if gdata.get("itemType") == "annotation":
                        item.annotations.append(Annotation(
                            type=gdata.get("annotationType") or "highlight",
                            text=gdata.get("annotationText") or "",
                            comment=gdata.get("annotationComment") or "",
                            page_label=gdata.get("annotationPageLabel") or "",
                            color=gdata.get("annotationColor") or "",
                            source_id=grandchild.get("key", ""),
                        ))

        if not item.pdfs:
            item.warnings.append("no PDF attachment resolved for this item")
        return item

    def _resolve_attachment(self, record: dict[str, Any]) -> tuple[Path | None, str]:
        data = record.get("data") or {}
        mode, filename = data.get("linkMode"), data.get("filename") or ""
        if mode == "linked_url" or not filename:
            return None, ""
        if mode == "linked_file":
            candidate = Path(data.get("path") or filename)
            return (candidate, "") if candidate.is_file() else (
                None, f"linked file missing: {candidate}")
        candidate = self.storage / record.get("key", "") / filename
        if candidate.is_file():
            return candidate, ""
        return None, f"attachment file missing: {filename}"

    def _better_bibtex_citekeys(self, client) -> dict[str, str]:
        """Ask Better BibTeX for citekeys. Absent is normal, not an error (M0/S1)."""
        try:
            resp = client.post(
                f"{self.base}/better-bibtex/json-rpc",
                json={"jsonrpc": "2.0", "method": "item.export", "params": [[]]},
                headers={"Content-Type": "application/json"})
        except Exception:  # noqa: BLE001
            return {}
        if resp.status_code != 200:
            return {}
        try:
            payload = resp.json()
        except ValueError:
            return {}
        result = payload.get("result")
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if isinstance(v, str)}
        return {}
