---
name: lit-ingest
description: Pull a reference library (Zotero, an export directory, or a folder of PDFs) into the lit-agent corpus and extract text from every PDF. Use when the user says "/lit-ingest", "import my library", "load my papers", "re-ingest", or before any analysis when the corpus is empty.
---

# lit-ingest

Stages 1 and 2 of the pipeline: adapter → `.lit/raw/`, then PDFs → `.lit/text/<citekey>.md`.

Requires the `source` capability, plus `pdf_text` unless `--no-text`. The script gates on
these itself and exits with a clear message if they are not enabled — do not pre-check.

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere.

## Run it

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/ingest.py"
```

| Flag | Use it when |
|---|---|
| `--source <adapter>` | overriding the configured adapter for one run |
| `--path <dir>` | the adapter is `export_dir` or `generic_pdf` |
| `--limit N` | trying things out, or bounding a first run |
| `--force` | redoing completed work (after a library correction) |
| `--no-text` | stage 1 only |
| `--layout` | messy multi-column PDFs — **~85× slower**, see `docs/decisions/0001` |
| `--resolve-online` | `generic_pdf` only, and **ask the user first** — it calls Crossref/arXiv/OpenAlex |
| `--json` | you need to branch on the result |

Re-running is safe and is the normal way to pick up new papers: completed work is skipped
unless `--force`.

## Reporting the result

The script prints a report. Relay it, and lead with the things a researcher would actually
want to know:

1. **How many papers are ready for analysis** — that is the headline number.
2. **Duplicates that were merged**, and on what basis. A DOI match is strong evidence; a
   title+year match is weaker, so mention which kind. If a merge looks wrong, the fix is to
   correct the metadata in the library and re-run with `--force`.
3. **Items with no PDF.** These are skipped, not failed — a library holds plenty of
   reference-only entries. Say how many, so the count is never a mystery.
4. **Errors.** Every one names its item. One bad file never stops the run.
5. **Scanned PDFs**, if any. They are skipped with a reason and need OCR (`ocrmypdf`)
   before they can be analyzed.

Do not summarize away the skips and errors. A researcher who does not know that 15 papers
were left out will draw conclusions from an incomplete corpus, which is exactly the silent
gap P4 exists to prevent.

## Interruption

Every item is checkpointed to `.lit/state.json` as it completes, so a killed run resumes
where it stopped and produces identical output. If the user interrupts a long ingest, just
re-run the same command — there is nothing to clean up.

## Zotero safety

The sqlite adapter copies the database and reads the copy read-only; it never opens the live
file and never writes. It also verifies the original was unmodified after the read and says
so in the report. Mention this if the user seems uneasy about pointing a tool at their library.

If the local API adapter reports "Zotero is running but its local API is off", that is a
checkbox in Zotero → Settings → Advanced, not a failure — and the sqlite adapter needs
none of it.

## After ingesting

Suggest `/lit-analyze` next. If the user has not run `/lit-scope`, say so first: analysis
without a scope produces generic summaries rather than notes aimed at their question.
