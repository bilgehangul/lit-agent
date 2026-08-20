---
name: lit-export
description: Build a browsable literature-review folder that mirrors the Zotero collection structure, with each paper's PDF and its summary side by side. Use when the user says "/lit-export", "put the summaries with the PDFs", "make a folder I can browse", "export my literature review", or after /lit-analyze or /lit-review produces new output.
---

# lit-export

`.lit/` is organized for the pipeline: flat, keyed by citekey. This builds the view organized
for a person - Zotero's collections, with each paper's PDF and summary in the same folder.

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere.

## Run it

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/export.py"
```

| Flag | Use |
|---|---|
| `--dest <path>` | where to build it (default `./literature-review`) |
| `--filter relevance=high year>=2020` | export a subset |
| `--no-pdfs` | summaries and text only |
| `--no-prune` | keep folders for papers no longer in the corpus |

## What it produces

```
literature-review/
  README.md                    navigable index, grouped by collection
  ToSDR LLM/
    chen2025llms/
      chen2025llms.pdf
      SUMMARY.md               the per-paper note
      fulltext.md              extracted text, page markers intact
      metadata.json
      figures/
  _synthesis/                  themes, gaps, contradictions, draft, refs.bib, scope
  _unfiled/                    papers in no collection
```

## Three things to tell the user

1. **It is a projection, not a source (P5).** Everything here is rebuilt from `.lit/`.
   Edits made in the export folder are lost on the next run. Notes are edited in
   `.lit/papers/`. Say this - people reasonably assume a folder full of markdown is
   editable.
2. **Papers with no summary get a placeholder** explaining why, not an empty folder. Report
   how many, because a folder that looks complete but is half-analyzed is the silent gap P4
   exists to prevent.
3. **Papers filed in several Zotero collections are stored once**, with a pointer file in the
   other collections. Nobody gets a duplicated PDF.

## Re-running

Safe and cheap. PDFs are only re-copied when they have changed, so a refresh after
`/lit-analyze` costs about a second. Stale paper folders are pruned - but only ones that
look like exported paper folders, so anything the user added by hand survives.

Suggest re-running after `/lit-analyze` or `/lit-review`, since those are what make the
folder out of date.
