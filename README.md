# lit-agent

A Claude Code plugin that turns a reference-manager library into a queryable, citable
literature knowledge base.

Point it at your Zotero library (or a folder of PDFs) and go from "200 unread PDFs" to a
drafted related-work section with correct citations, without leaving your terminal.

> **Status: M0–M5 complete and working end to end.** Ingest, per-paper analysis with
> verified citations, question answering, and cross-paper synthesis all run against a real
> Zotero library. Figures (M6), Zotero write-back (M7), enrichment (M8), and vector search
> (M9) are queued. See `ROADMAP.md` for the plan and `STATE.md` for exactly where the build
> stands, including its known gaps. Full spec: `lit-agent-project-plan.md`.

## What it does

- **Ingest** metadata, PDFs, and your own annotations from Zotero (local API, sqlite,
  export directory) or a plain PDF folder.
- **Interview** you about your research scope first, so every output is shaped by it.
- **Analyze** each paper into a structured note: problem, method, data, findings,
  limitations, and why this paper matters to *your* question.
- **Answer questions** over the corpus with citations back to specific papers and pages.
- **Synthesize** across papers: themes, methods matrices, contradictions, gaps, and a
  drafted literature-review section with real citekeys.
- Optionally extract figures and tables, enrich via Scholar/Semantic Scholar, and write
  notes back into Zotero.

## The one rule

Every claim in generated output traces to a specific paper and a locator (page or section).
When support cannot be located, the output says `[UNVERIFIED]` rather than inventing a
citation. See P7 in `CLAUDE.md`.

## How it works today

```
/lit-setup      build the environment, connect your library, verify each capability
/lit-scope      the research-scope interview that shapes every output
/lit-ingest     pull the library in and extract text with page markers
/lit-analyze    write a structured note per paper, then verify every locator
/lit-ask        ask questions, get answers with citations
/lit-review     themes, methods matrix, gaps, contradictions, drafted related work
/lit-doctor     re-probe everything and repair or disable what is broken
```

Example output from a real six-paper run is in [`docs/examples/`](docs/examples/), and
worked question answering — including a question the corpus cannot answer, and the system
declining it — is in [`docs/m4-qa-examples.md`](docs/m4-qa-examples.md).

## Verification, not vibes

Two things are checked mechanically rather than trusted:

- **Every locator resolves.** A citation naming a page that does not exist in the extracted
  text is a fabrication, and the note is rejected. The M3 gate report is at
  [`docs/spikes/m3-locator-check.md`](docs/spikes/m3-locator-check.md).
- **Every contradiction has two verified locators**, one per side, each resolved against the
  cited paper's own text. An entry that cannot meet that bar does not ship.

## Install

Not yet published. Installation and a 3-minute quickstart land in M10.

## License

MIT
