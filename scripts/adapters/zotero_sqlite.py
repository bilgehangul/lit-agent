"""Read a Zotero library directly from ``zotero.sqlite`` (spec section 8b).

**Zotero locks the database while it runs, and writing to it can corrupt a library.** This
adapter therefore copies the file and opens the *copy* read-only and immutable. It never
opens the live file and never issues a write.

The schema is undocumented and version-dependent. Every query lives in
``references/zotero-internals.md`` alongside the gotchas that motivated it; the versions
this query set was validated against are listed there.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import Adapter, AdapterResult  # noqa: E402
from lib.models import (  # noqa: E402
    Annotation,
    LibraryItem,
    Note,
    clean_title,
    normalize_arxiv,
    normalize_doi,
    parse_zotero_date,
)

#: Schema versions this query set has been validated against (M0/S2).
KNOWN_USERDATA_VERSIONS = frozenset({125})

#: Zotero's attachment link modes.
IMPORTED_FILE, IMPORTED_URL, LINKED_FILE, LINKED_URL, EMBEDDED_IMAGE = 0, 1, 2, 3, 4

#: Zotero item type -> CSL-JSON type.
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
    "manuscript": "manuscript",
    "presentation": "speech",
    "dataset": "dataset",
}


class ZoteroSqliteAdapter(Adapter):
    name = "zotero_sqlite"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.db_path = Path(self.config.get("db_path") or default_db_path())
        self.storage = Path(self.config.get("storage_path") or (self.db_path.parent / "storage"))
        #: Zotero's "Linked Attachment Base Directory" setting, for linkMode 2 relative paths.
        self.base_dir = Path(self.config["base_dir"]) if self.config.get("base_dir") else None

    # --- availability ------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        if not self.db_path.is_file():
            return False, f"no zotero.sqlite at {self.db_path}"
        if not self.storage.is_dir():
            return True, (f"found {self.db_path} but no storage/ directory at {self.storage}; "
                          "attachments may not resolve")
        return True, f"found {self.db_path} ({self.db_path.stat().st_size:,} bytes)"

    # --- reading -----------------------------------------------------------

    def fetch(self, limit: int | None = None) -> AdapterResult:
        result = AdapterResult()
        ok, reason = self.available()
        if not ok:
            result.errors.append(("", reason))
            return result

        before = (self.db_path.stat().st_mtime, self.db_path.stat().st_size)
        tmpdir = Path(tempfile.mkdtemp(prefix="litagent-zotero-"))
        try:
            con = self._open_readonly_copy(tmpdir)
            try:
                self._read(con, result, limit)
            finally:
                con.close()
        except sqlite3.DatabaseError as exc:
            result.errors.append(("", f"could not read zotero.sqlite: {exc}"))
        except OSError as exc:
            result.errors.append(("", f"could not copy zotero.sqlite: {exc}"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        after = (self.db_path.stat().st_mtime, self.db_path.stat().st_size)
        if before != after:
            # Should be impossible. If it ever happens, say so loudly rather than continue.
            result.errors.append(("", "the Zotero database changed during a read-only pass"))
        result.info["source_unmodified"] = before == after
        return result

    def _open_readonly_copy(self, tmpdir: Path) -> sqlite3.Connection:
        copy = tmpdir / "zotero.sqlite"
        shutil.copy2(self.db_path, copy)
        # Copy the sidecar files too, so the snapshot is internally consistent.
        for suffix in ("-wal", "-shm", "-journal"):
            sibling = self.db_path.with_name(self.db_path.name + suffix)
            if sibling.exists():
                shutil.copy2(sibling, copy.with_name(copy.name + suffix))
        con = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def _read(self, con: sqlite3.Connection, result: AdapterResult, limit: int | None) -> None:
        schema = con.execute(
            "SELECT version FROM version WHERE schema='userdata'").fetchone()
        version = schema["version"] if schema else None
        result.info["schema_version"] = version
        if version not in KNOWN_USERDATA_VERSIONS:
            # Warn, do not refuse: the queries use long-stable tables (P4).
            result.info["schema_warning"] = (
                f"Zotero userdata schema {version} has not been validated against this query "
                f"set (known: {sorted(KNOWN_USERDATA_VERSIONS)}). Proceeding, but check the "
                "results.")

        creators = self._creators(con)
        tags = self._tags(con)
        collections = self._collections(con)
        notes = self._notes(con)
        annotations = self._annotations(con)
        attachments = self._attachments(con, result)

        rows = con.execute(f"""
            SELECT i.itemID, i.key, it.typeName,
                   MAX(CASE WHEN f.fieldName='title'            THEN idv.value END) AS title,
                   MAX(CASE WHEN f.fieldName='date'             THEN idv.value END) AS date,
                   MAX(CASE WHEN f.fieldName='DOI'              THEN idv.value END) AS doi,
                   MAX(CASE WHEN f.fieldName='url'              THEN idv.value END) AS url,
                   MAX(CASE WHEN f.fieldName='abstractNote'     THEN idv.value END) AS abstract,
                   MAX(CASE WHEN f.fieldName='publicationTitle' THEN idv.value END) AS publication,
                   MAX(CASE WHEN f.fieldName='proceedingsTitle' THEN idv.value END) AS proceedings,
                   MAX(CASE WHEN f.fieldName='bookTitle'        THEN idv.value END) AS booktitle,
                   MAX(CASE WHEN f.fieldName='publisher'        THEN idv.value END) AS publisher,
                   MAX(CASE WHEN f.fieldName='volume'           THEN idv.value END) AS volume,
                   MAX(CASE WHEN f.fieldName='issue'            THEN idv.value END) AS issue,
                   MAX(CASE WHEN f.fieldName='pages'            THEN idv.value END) AS pages,
                   MAX(CASE WHEN f.fieldName='extra'            THEN idv.value END) AS extra,
                   MAX(CASE WHEN f.fieldName='repository'       THEN idv.value END) AS repository,
                   MAX(CASE WHEN f.fieldName='archiveID'        THEN idv.value END) AS archive_id
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            LEFT JOIN itemData d ON d.itemID = i.itemID
            LEFT JOIN fields f ON f.fieldID = d.fieldID
            LEFT JOIN itemDataValues idv ON idv.valueID = d.valueID
            WHERE it.typeName NOT IN ('attachment','note','annotation')
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            GROUP BY i.itemID
            ORDER BY i.itemID
            {f'LIMIT {int(limit)}' if limit else ''}""").fetchall()

        for row in rows:
            try:
                result.items.append(self._build_item(
                    row, creators, tags, collections, notes, annotations, attachments))
            except Exception as exc:  # noqa: BLE001 - one bad row never kills the run
                result.errors.append((row["key"], f"{type(exc).__name__}: {exc}"))

        result.info["items_read"] = len(result.items)
        result.info["annotations_read"] = sum(len(v) for v in annotations.values())
        result.info["notes_read"] = sum(len(v) for v in notes.values())

    # --- row assembly ------------------------------------------------------

    def _build_item(self, row: sqlite3.Row, creators, tags, collections,
                    notes, annotations, attachments) -> LibraryItem:
        item_id = row["itemID"]
        year, date_original = parse_zotero_date(row["date"] or "")

        authors, editors = [], []
        for creator in creators.get(item_id, []):
            entry = {"family": creator["lastName"] or "", "given": creator["firstName"] or ""}
            if not entry["family"] and not entry["given"]:
                continue
            (editors if creator["creatorType"] == "editor" else authors).append(entry)

        container = (row["publication"] or row["proceedings"]
                     or row["booktitle"] or row["publisher"] or "")

        metadata: dict[str, Any] = {
            "id": row["key"],
            "type": CSL_TYPE.get(row["typeName"], "document"),
            "zotero_type": row["typeName"],
            "title": clean_title(row["title"] or ""),
            "author": authors,
            "container-title": container,
            "DOI": normalize_doi(row["doi"] or ""),
            "URL": row["url"] or "",
            "abstract": row["abstract"] or "",
            "volume": row["volume"] or "",
            "issue": row["issue"] or "",
            "page": row["pages"] or "",
            "publisher": row["publisher"] or "",
            "date_original": date_original,
        }
        if editors:
            metadata["editor"] = editors
        if year:
            metadata["issued"] = {"date-parts": [[year]]}

        # arXiv IDs hide in several fields depending on how the item was imported.
        arxiv = ""
        for candidate in (row["archive_id"], row["extra"], row["url"],
                          row["doi"], row["repository"]):
            if arxiv := normalize_arxiv(candidate or ""):
                break
        if arxiv:
            metadata["arxiv_id"] = arxiv

        item = LibraryItem(
            source_id=row["key"],
            metadata=metadata,
            tags=sorted(tags.get(item_id, [])),
            collections=sorted(collections.get(item_id, [])),
            notes=notes.get(item_id, []),
        )

        paths, warnings = [], []
        for att in attachments.get(item_id, []):
            resolved, warning = self._resolve_attachment(att)
            if resolved:
                paths.append(resolved)
                # Annotations hang off the attachment, not the parent work.
                item.annotations.extend(annotations.get(att["itemID"], []))
            elif warning:
                warnings.append(warning)
        item.attachments = paths
        item.warnings = warnings

        if not item.pdfs:
            item.warnings.append("no PDF attachment resolved for this item")
        return item

    def _resolve_attachment(self, att: sqlite3.Row) -> tuple[Path | None, str]:
        """Turn an ``itemAttachments`` row into a real path, or explain why not."""
        path, mode = att["path"] or "", att["linkMode"]
        if mode in (LINKED_URL, EMBEDDED_IMAGE) or not path:
            return None, ""
        if path.startswith("storage:"):
            # Note: the storage folder is keyed by the *attachment's* key, not the parent's.
            candidate = self.storage / att["akey"] / path[len("storage:"):]
            if candidate.is_file():
                return candidate, ""
            return None, f"attachment file missing: {candidate.name}"
        if path.startswith("attachments:"):
            relative = path[len("attachments:"):]
            if self.base_dir:
                candidate = self.base_dir / relative
                if candidate.is_file():
                    return candidate, ""
                return None, f"linked attachment missing under base directory: {relative}"
            return None, ("linked attachment needs Zotero's Linked Attachment Base Directory; "
                          "set base_dir in the source config")
        candidate = Path(path)
        if candidate.is_file():
            return candidate, ""
        return None, f"linked file missing: {path}"

    # --- lookup tables -----------------------------------------------------

    def _creators(self, con) -> dict[int, list[sqlite3.Row]]:
        out: dict[int, list[sqlite3.Row]] = {}
        for row in con.execute("""
                SELECT ic.itemID, c.lastName, c.firstName, ic.orderIndex, ct.creatorType
                FROM itemCreators ic
                JOIN creators c ON c.creatorID = ic.creatorID
                JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
                ORDER BY ic.itemID, ic.orderIndex"""):
            out.setdefault(row["itemID"], []).append(row)
        return out

    def _tags(self, con) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for row in con.execute("""
                SELECT it.itemID, t.name FROM itemTags it
                JOIN tags t ON t.tagID = it.tagID"""):
            out.setdefault(row["itemID"], []).append(row["name"])
        return out

    def _collections(self, con) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for row in con.execute("""
                SELECT ci.itemID, c.collectionName FROM collectionItems ci
                JOIN collections c ON c.collectionID = ci.collectionID"""):
            out.setdefault(row["itemID"], []).append(row["collectionName"])
        return out

    def _notes(self, con) -> dict[int, list[Note]]:
        """Child notes, keyed by parent item.

        The development library has 126 of these and zero rows in ``itemAnnotations``
        (M0/S2-a), so this is the path that actually carries the user's reading trace.
        """
        out: dict[int, list[Note]] = {}
        for row in con.execute("""
                SELECT n.itemID, n.parentItemID, n.note, n.title, i.key
                FROM itemNotes n JOIN items i ON i.itemID = n.itemID
                WHERE n.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND n.parentItemID IS NOT NULL"""):
            html = row["note"] or ""
            out.setdefault(row["parentItemID"], []).append(Note(
                html=html,                        # verbatim, never rewritten (spec 6.9)
                text=Note.html_to_text(html),
                title=row["title"] or "",
                source_id=row["key"],
            ))
        return out

    def _annotations(self, con) -> dict[int, list[Annotation]]:
        """Annotations, keyed by the *attachment* item they belong to."""
        import json as _json
        out: dict[int, list[Annotation]] = {}
        for row in con.execute("""
                SELECT a.itemID, a.parentItemID, a.type, a.text, a.comment,
                       a.color, a.pageLabel, a.position, i.key
                FROM itemAnnotations a JOIN items i ON i.itemID = a.itemID
                WHERE a.itemID NOT IN (SELECT itemID FROM deletedItems)"""):
            page_index = None
            try:
                page_index = (_json.loads(row["position"] or "{}") or {}).get("pageIndex")
            except (ValueError, TypeError):
                pass
            out.setdefault(row["parentItemID"], []).append(Annotation(
                type=_annotation_type(row["type"]),
                text=row["text"] or "",
                comment=row["comment"] or "",
                page_label=row["pageLabel"] or "",
                page_index=page_index,
                color=row["color"] or "",
                source_id=row["key"],
            ))
        return out

    def _attachments(self, con, result: AdapterResult) -> dict[int, list[sqlite3.Row]]:
        out: dict[int, list[sqlite3.Row]] = {}
        for row in con.execute("""
                SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.contentType, ia.path,
                       ai.key AS akey
                FROM itemAttachments ia
                JOIN items ai ON ai.itemID = ia.itemID
                WHERE ai.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND ia.parentItemID IS NOT NULL
                ORDER BY CASE WHEN ia.contentType='application/pdf' THEN 0 ELSE 1 END"""):
            out.setdefault(row["parentItemID"], []).append(row)
        return out


def _annotation_type(value: Any) -> str:
    """Zotero stores the annotation type as a small integer."""
    return {1: "highlight", 2: "note", 3: "image", 4: "ink",
            5: "underline", 6: "text"}.get(value, str(value))


def default_db_path() -> Path:
    return Path.home() / "Zotero" / "zotero.sqlite"
