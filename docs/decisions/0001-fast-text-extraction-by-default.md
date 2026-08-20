# ADR-0001 — Raw PyMuPDF text extraction by default; pymupdf4llm behind --layout

Date: 2026-08-20 · Status: accepted · Amends: spec section 9 (Stage 2)

## Context

The spec says: "`pymupdf4llm` for markdown-with-structure by default."

S4-a measured it on a 12-page paper from the user's library:

| Method | time | per page | chars |
|---|---|---|---|
| `pymupdf4llm.to_markdown(page_chunks=True)` | 39.9 s | 3.32 s | 26,369 |
| `pymupdf` `page.get_text()` per page | 0.5 s | 0.04 s | 25,563 |

85x the time for 3% more text. The documented tuning kwargs (`use_layout`, `ignore_images`,
`ignore_graphics`) change nothing, because `to_markdown` is declared `(*args, **kwargs)` and
silently swallows unknown options.

Extrapolated to this 88-paper / ~1,740-page library: ~100 minutes vs ~70 seconds. On a
300-paper library that is roughly six hours of extraction before a single analysis token
is spent. It also drags `pymupdf_layout` + `onnxruntime` (~57 MB) into what the spec calls
a pure-pip core dependency.

## Decision

Raw `pymupdf` extraction is the **default** path for Stage 2:

- iterate pages, `page.get_text()`, emit an explicit page marker per page;
- detect headings cheaply from font size via `page.get_text("dict")` so section locators
  (`[section 4.2]`) still work;
- `pymupdf4llm` remains available as an opt-in `--layout` mode for papers where markdown
  table/heading structure genuinely matters;
- `pymupdf4llm` moves out of the required core in `requirements.txt` and behind the
  `layout_text` capability flag.

## Consequences

- Text extraction stops being the slowest stage of the pipeline by two orders of magnitude.
- **P7 is unaffected**: page-level locators are the primitive that citation verification
  depends on, and the fast path preserves them exactly. Page markers are emitted explicitly
  and must never be stripped.
- Section locators come from heuristic heading detection rather than a parser, so they are
  best-effort. When a heading cannot be resolved, the analyzer falls back to a page locator
  rather than guessing a section number.
- The core install shrinks by ~57 MB and loses an ONNX runtime dependency.
