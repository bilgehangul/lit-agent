# Zotero internals

The `zotero.sqlite` schema is undocumented and version-dependent. Every query the sqlite
adapter uses lives here so it is maintainable in one place when the schema drifts.

**Validated against `userdata` schema version 125 (Zotero 7) on 2026-08-20** — see
`docs/spikes/FINDINGS.md` (S2).

---

## Safety rules — non-negotiable

Zotero holds a lock on the database while it runs, and writing to it can corrupt a user's
library. The adapter therefore:

1. **Copies** `zotero.sqlite` (plus any `-wal` / `-shm` / `-journal` siblings) to a temp file.
2. Opens the **copy** with `file:<path>?mode=ro&immutable=1`.
3. Never opens the live file. Never issues a write of any kind, under any circumstance.
4. Deletes the temp copy when done.

The S2 spike verified the original file's mtime and size were byte-identical before and after
a full read pass. Any change to this code path must re-verify that.

## Default paths

| Platform | Path |
|---|---|
| Windows | `%USERPROFILE%\Zotero\zotero.sqlite` |
| macOS / Linux | `~/Zotero/zotero.sqlite` |

Attachments live under `<Zotero data dir>/storage/<attachment item key>/`.

## Schema version detection

```sql
SELECT version, schema FROM version
WHERE schema IN ('userdata','system','globalSchema','triggers');
```

Known-good: `userdata 125`, `globalSchema 42`, `system 32`, `triggers 18`.

Record the value on every read. If `userdata` is unrecognized, **warn and continue** — the
queries below are conservative and use long-stable tables — but say so in the run report (P4).

## Required tables

`items`, `itemTypes`, `itemData`, `itemDataValues`, `fields`, `itemAttachments`, `itemNotes`,
`itemAnnotations`, `itemTags`, `tags`, `collections`, `collectionItems`, `creators`,
`itemCreators`, `creatorTypes`, `deletedItems`.

---

## Query set

### Regular items with metadata

Attachments, notes, and annotations are themselves rows in `items`, so they must be excluded.
Trashed items live in `deletedItems` and must be excluded too.

```sql
SELECT i.itemID, i.key, it.typeName,
       MAX(CASE WHEN f.fieldName='title'          THEN idv.value END) AS title,
       MAX(CASE WHEN f.fieldName='date'           THEN idv.value END) AS date,
       MAX(CASE WHEN f.fieldName='DOI'            THEN idv.value END) AS doi,
       MAX(CASE WHEN f.fieldName='url'            THEN idv.value END) AS url,
       MAX(CASE WHEN f.fieldName='abstractNote'   THEN idv.value END) AS abstract,
       MAX(CASE WHEN f.fieldName='publicationTitle' THEN idv.value END) AS publication,
       MAX(CASE WHEN f.fieldName='proceedingsTitle' THEN idv.value END) AS proceedings
FROM items i
JOIN itemTypes it       ON it.itemTypeID = i.itemTypeID
LEFT JOIN itemData d    ON d.itemID = i.itemID
LEFT JOIN fields f      ON f.fieldID = d.fieldID
LEFT JOIN itemDataValues idv ON idv.valueID = d.valueID
WHERE it.typeName NOT IN ('attachment','note','annotation')
  AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
GROUP BY i.itemID;
```

### Creators, in author order

`orderIndex` is the author order and must be respected — citekeys and BibTeX depend on
first-author identity.

```sql
SELECT ic.itemID, c.lastName, c.firstName, ic.orderIndex, ct.creatorType
FROM itemCreators ic
JOIN creators c      ON c.creatorID = ic.creatorID
JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
ORDER BY ic.itemID, ic.orderIndex;
```

### Attachments

```sql
SELECT ia.parentItemID, ai.key AS attachmentKey,
       ia.linkMode, ia.contentType, ia.path
FROM itemAttachments ia
JOIN items ai ON ai.itemID = ia.itemID
WHERE ai.itemID NOT IN (SELECT itemID FROM deletedItems);
```

Columns: `itemID, parentItemID, linkMode, contentType, charsetID, path, syncState,
storageModTime, storageHash, lastProcessedModificationTime, lastRead`.

### Notes

```sql
SELECT n.itemID, n.parentItemID, n.note, n.title
FROM itemNotes n
WHERE n.itemID NOT IN (SELECT itemID FROM deletedItems);
```

`note` is HTML. Strip tags for the plaintext form but **preserve the original verbatim** —
spec section 6 body item 9 requires the user's own words unrewritten.

### Annotations

```sql
SELECT a.itemID, a.parentItemID, a.type, a.authorName, a.text, a.comment,
       a.color, a.pageLabel, a.sortIndex, a.position, a.isExternal
FROM itemAnnotations a
WHERE a.itemID NOT IN (SELECT itemID FROM deletedItems);
```

`parentItemID` points at the **attachment**, not the parent work — resolve up one level.
`position` is JSON containing `pageIndex` and `rects`. `pageLabel` is the printed page label,
which may differ from the physical page index.

> **This table is empty in the development library.** A library with zero annotations is a
> normal state, not an error — do not warn. But per P4 the per-paper note must say
> `No Zotero annotations found for this paper` rather than rendering a silently empty section.

### Tags and collections

```sql
SELECT it.itemID, t.name, it.type          -- type 0 = manual, 1 = automatic
FROM itemTags it JOIN tags t ON t.tagID = it.tagID;

SELECT ci.itemID, c.collectionID, c.collectionName, c.parentCollectionID
FROM collectionItems ci JOIN collections c ON c.collectionID = ci.collectionID;
```

---

## Field-level gotchas

### `date` holds two values in one column

Format is `"<sql-date> <original string>"`, space-separated, with `00` for unknown parts:

```
'2024-08-25 2024-08-25'        '2025-04-00 2025-04'
'2011-12-05 December 5, 2011'  '2012-08-00 08/2012'
```

**Year = first 4 characters.** Parsing the whole field yields
`"2024-08-25 2024-08-25"` as a "year", which would poison citekeys and BibTeX output.

### Attachment path resolution

`path` is `storage:<filename>`. The file is at:

```
<Zotero data dir>/storage/<attachment item key>/<filename>
```

The key is the **attachment's own** item key, not the parent work's. Verified end to end
in S2.

`linkMode` values:

| value | meaning | path handling |
|---|---|---|
| 0 | imported_file | `storage:` prefix |
| 1 | imported_url | `storage:` prefix (114 of 118 in the dev library) |
| 2 | linked_file | absolute path, or relative to the linked-attachment base directory |
| 3 | linked_url | no local file — skip |
| 4 | embedded_image | note image — skip |

### Title cleanup

Real titles carry junk from web imports, e.g. a leading `"(PDF) "`. Strip known prefixes and
trailing site names before generating a citekey.

### Item types present in the dev library

`journalArticle` 49, `conferencePaper` 27, `preprint` 14, `webpage` 10, `bookSection` 2,
`document` 1. Map each to a CSL-JSON type; unknown types fall back to `document` and are
reported, not dropped.
