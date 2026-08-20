# lit-agent

A Claude Code plugin that turns a reference-manager library into a queryable, citable
literature knowledge base.

Point it at your Zotero library (or a folder of PDFs) and go from "200 unread PDFs" to a
drafted related-work section with correct citations, without leaving your terminal.

> **Status: in development.** See `ROADMAP.md` for what works today and `STATE.md` for
> where the build currently stands. Full spec: `lit-agent-project-plan.md`.

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

## Install

Not yet published. Installation and a 3-minute quickstart land in M10.

## License

MIT
