# lit-agent — Project Plan

**A Claude Code plugin that turns a reference-manager library into a queryable, citable literature knowledge base.**

Target audience: researchers (grad students, postdocs, PIs) who already keep papers in Zotero (or Mendeley/Paperpile/a folder of PDFs) and want to go from "200 unread PDFs" to "a drafted related-work section with correct citations" without leaving their terminal.

This document is the build spec. Hand it to Claude Code as the source of truth. Build in milestone order. Do not skip Milestone 0.

---

## 1. Goals

1. **Ingest** a reference library — metadata, PDFs, and the researcher's own annotations/notes — from multiple possible sources through one common interface.
2. **Interview** the researcher about their research scope *before* any analysis, and persist that scope so every downstream output is shaped by it.
3. **Analyze** each paper into a structured, scope-aware summary: problem, method, data, findings, limitations, and *why this paper matters to this researcher specifically*.
4. **Extract** figures and tables with their captions, so they can be referenced or reproduced during writing.
5. **Enrich** (optionally) with Google Scholar Labs query-relative summaries via browser control.
6. **Synthesize** across papers: thematic clusters, methods comparison matrices, contradiction detection, gap analysis, and a drafted literature-review section with real citekeys.
7. **Answer questions** over the corpus with citations back to specific papers and page numbers.
8. **Write back** into Zotero: per-paper child notes, plus standalone synthesis notes.

## 2. Non-goals

- Not a PDF reader/annotator UI. No GUI.
- Not a Zotero replacement or a Zotero plugin (that's a separate XPI ecosystem — this is a *Claude Code* plugin that talks to Zotero).
- Not a paper-discovery search engine. It works on a library the user already curated. (Scholar Labs enrichment may *suggest* additions, but discovery is not the core loop.)
- No ghostwriting of full papers. Output is drafting scaffolding with explicit citations the human verifies.
- Do not write custom PDF-parsing or figure-detection algorithms. Use existing libraries. If an existing library can't do it well, degrade gracefully and say so.

## 3. Design principles

These are non-negotiable and should be enforced in review.

**P1 — No half-configured state, ever.**
Every optional capability is either *verified working* or *explicitly off*. There is no third state. A capability that was configured but now fails must be demoted to off with a clear message, not retried silently in a loop.

**P2 — Setup gates on verification, not on user assertion.**
Never accept "yes I set up the API key" as proof. Run a real probe against the real endpoint and show the result. If the probe fails, do not advance; offer: retry, get help, or disable this capability and continue.

**P3 — Every optional capability is skippable at any time.**
The user can decline at setup, or turn a capability off later via `/lit-setup --reconfigure`. The core pipeline (local PDFs → markdown summaries → Q&A) must work with *every* optional capability disabled. That path is the one that has to be bulletproof.

**P4 — Degrade loudly, never silently.**
If figure extraction produced nothing for a paper, the paper's note says so. If Scholar enrichment was skipped, the synthesis says which papers lack it. Silent gaps in a literature review are a correctness hazard.

**P5 — Markdown is the source of truth.**
Zotero write-back and vector indexes are *projections* of the markdown. Both must be fully rebuildable from markdown. Never store anything only in Zotero or only in the index.

**P6 — Idempotent and resumable.**
Ingesting 300 papers will hit interruptions. Every stage checkpoints per-item. Re-running skips completed work unless `--force`. A crash at paper 187 resumes at 187.

**P7 — Never fabricate a citation.**
Every claim in generated output traces to a specific paper key plus a locator (section or page). If the model can't locate support, it writes `[UNVERIFIED]` rather than inventing. This is the single most important correctness rule in the project.

---

## 4. Plugin structure

```
lit-agent/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── lit-setup/SKILL.md            # capability wizard + scope interview
│   ├── lit-ingest/SKILL.md           # pull library → normalized corpus
│   ├── lit-analyze/SKILL.md          # per-paper summarization
│   ├── lit-figures/SKILL.md          # figure/table extraction
│   ├── lit-enrich/SKILL.md           # Scholar Labs browser pass (optional)
│   ├── lit-review/SKILL.md           # cross-paper synthesis + draft
│   ├── lit-ask/SKILL.md              # Q&A over corpus
│   └── lit-sync/SKILL.md             # Zotero write-back (optional)
├── scripts/
│   ├── adapters/                     # zotero_api.py, zotero_sqlite.py, export_dir.py, generic_pdf.py
│   ├── extract/                      # pdf_text.py, figures.py, arxiv_source.py
│   ├── sync/                         # zotero_write.py
│   ├── index/                        # build_index.py, search.py
│   └── doctor.py                     # capability probes
├── references/
│   ├── zotero-internals.md
│   ├── output-schemas.md
│   └── prompt-templates/
├── CONNECTORS.md
└── README.md
```

`plugin.json`:
```json
{
  "name": "lit-agent",
  "version": "0.1.0",
  "description": "Literature review and paper-writing assistant over a Zotero or PDF library",
  "author": { "name": "<author>" },
  "license": "MIT",
  "keywords": ["research", "zotero", "literature-review", "citations", "pdf"]
}
```

Use `${CLAUDE_PLUGIN_ROOT}` for every intra-plugin path. Never hardcode absolute paths.

**Python dependency policy:** one `requirements.txt`, installed into a plugin-managed venv at `~/.lit-agent/venv` during setup. Core deps must be pure-pip and cross-platform. Anything requiring a JVM, system package, or GPU is an *optional extra* behind a capability flag.

---

## 5. Capability model (the heart of the setup system)

Capabilities live in `~/.lit-agent/capabilities.json`. Each has: `status` (`enabled` | `disabled` | `broken`), `last_verified`, `config`, and `last_error`.

| ID | Capability | Required? | Probe |
|---|---|---|---|
| `python_env` | Venv + core deps | **Required** | Import pymupdf, run version check |
| `source` | At least one library adapter | **Required** | Adapter returns ≥1 item with a resolvable PDF path |
| `pdf_text` | Text extraction | **Required** | Extract text from a known-good sample PDF, assert >200 chars |
| `figures` | Figure/table extraction | Optional | Extract ≥1 image from bundled sample PDF |
| `arxiv_source` | arXiv LaTeX source fetch | Optional | Fetch + untar a known arXiv e-print |
| `browser` | Chrome control | Optional | Navigate to a page, read its title back |
| `scholar_labs` | Scholar Labs access | Optional | Load Scholar Labs, confirm signed in and not waitlisted |
| `zotero_write` | Write-back via Web API | Optional | Create a test note on a scratch item, read it back, delete it |
| `vector_index` | Embedding search | Optional | Embed two strings, assert cosine similarity sane |
| `grobid` | Structured parsing | Optional | Ping GROBID `/api/isalive` |

### Setup wizard flow (`/lit-setup`)

Run interactively. Phase order matters — required first, so the user reaches a working system before being asked about extras.

**Phase A — Required.** Create venv, install deps, probe `python_env` and `pdf_text`. If either fails, stop with a specific remediation message. Do not proceed.

**Phase B — Source adapter.** Auto-detect in priority order and *tell the user what was found*:
1. Zotero local HTTP API responding on `127.0.0.1:23119`
2. `zotero.sqlite` at the platform default path
3. A user-supplied export directory (CSV/BibTeX/RDF + files)
4. A plain folder of PDFs

Present findings, let the user confirm or override. Probe by pulling 3 items and resolving their attachment paths. Show the user the actual titles retrieved — this is the proof the connection works.

**Phase C — Optional capabilities.** For each: explain in one sentence what it enables and what it costs to set up. Then ask. If yes → collect config → **run the probe** → show result. On failure, offer *retry / help / skip*. Skipping sets `disabled` and moves on cleanly.

**Phase D — Research scope interview.** See §7. Runs after capabilities so the interview can adapt (don't offer figure-heavy outputs if `figures` is disabled).

**Phase E — Summary.** Print a capability table with status per row, write config, and state exactly which commands are now available and which are locked behind disabled capabilities.

### Runtime gating

Every skill begins by reading `capabilities.json`. If a required capability for that skill is not `enabled`, the skill does **not** attempt the work. It prints what's missing and the one command to fix it (`/lit-setup --reconfigure <capability>`), then exits.

If a capability probe passes at setup but fails three times during a run, mark it `broken`, finish the run without it, and report the demotion in the run summary. Never spin.

### `/lit-doctor`

Re-runs every probe, prints a status table, and offers to repair or disable anything broken. This is the first thing to tell a confused user to run.

---

## 6. Corpus layout

Per-project, in the researcher's working directory:

```
.lit/
├── config.yaml               # scope, source config, output prefs
├── state.json                # per-item pipeline checkpoints
├── raw/                      # normalized source metadata (JSON per item)
├── text/<citekey>.md         # extracted full text
├── figures/<citekey>/        # fig-01.png + figures.json (captions, pages, source)
├── papers/<citekey>.md       # THE per-paper note (source of truth)
├── synthesis/
│   ├── index.md              # master table of all papers
│   ├── themes.md
│   ├── methods-matrix.md
│   ├── gaps.md
│   └── review-draft.md
├── refs.bib                  # generated BibTeX for cited subset
└── index/                    # optional vector store
```

**Citekey** is the join key across everything. Prefer Better BibTeX citekeys when available (they're stable and match what the user types in LaTeX); otherwise generate `authorYEARfirstword` and record the mapping.

### Per-paper note schema

`papers/<citekey>.md` — YAML frontmatter + fixed section order. Frontmatter is what makes grep-based retrieval work, so it must be consistent.

```yaml
---
citekey: doe2024privacy
zotero_key: ABCD1234
title: ...
authors: [...]
year: 2024
venue: ...
doi: ...
arxiv_id: ...
item_type: conferencePaper
tags: [...]              # user's Zotero tags
scope_tags: [...]        # assigned against THIS project's scope
relevance: high          # high | medium | low | tangential
paper_type: empirical    # empirical | systems | theory | survey | position | dataset
methods: [...]
datasets: [...]
metrics: [...]
figures_extracted: 4
has_user_notes: true
enrichment: {scholar_labs: true, queries: [...]}
analyzed: 2026-08-20
confidence: high         # analyzer's confidence in its own extraction
---
```

Body sections, always in this order:

1. **One-line summary** — what the paper does, in the researcher's vocabulary.
2. **Relevance to this project** — explicit, scope-conditioned. Why it matters *here*. If it doesn't, say so plainly.
3. **Problem & motivation**
4. **Approach** — enough detail to reimplement or critique.
5. **Evaluation** — datasets, baselines, metrics, headline numbers with locators.
6. **Key findings** — bulleted, each with `[p. N]` or `[§4.2]`.
7. **Limitations & threats to validity** — the paper's stated ones *and* the analyzer's observations, labeled separately.
8. **Figures & tables** — extracted assets with captions and one line on what each shows.
9. **Your notes** — the user's own Zotero annotations/notes, preserved verbatim, never rewritten. Analyzer commentary on them goes in a clearly marked sub-block.
10. **Citation-ready claims** — 3–6 sentences the user could paste into a paper, each with its citekey and locator. This is the feature that makes writing fast.
11. **Connections** — links to other citekeys in the corpus (extends / contradicts / uses-method-of / superseded-by).
12. **Open questions** — what this paper leaves unanswered relative to the project scope.

Anything the analyzer could not determine is written as `Not determinable from the text` — never guessed, never omitted silently (P4).

---

## 7. Research scope interview

Runs in `/lit-setup` Phase D; re-runnable via `/lit-scope`. Persisted to `.lit/config.yaml`. **Every** prompt template in the project injects this block.

Ask conversationally, adapting follow-ups to answers. Do not fire all questions at once.

1. **Field & subfield** — and what venue-level vocabulary to use.
2. **Research question** — the actual question, one or two sentences. Push for specificity; a vague answer here degrades every downstream output. (This doubles as the Scholar Labs query seed — see §10.)
3. **Purpose of this pass** — related-work section / survey paper / thesis chapter / methods scouting / grant background / staying current. Different purposes need different notes.
4. **Stage** — starting cold, refining a draft, or filling gaps in an existing manuscript.
5. **What matters in a paper** — methods, empirical results, datasets, theory, limitations, reproducibility, real-world deployment. Ranked.
6. **Desired artifacts** — per-paper notes / methods matrix / thematic synthesis / gap analysis / draft related-work / figures / BibTeX subset. Multi-select, and only offer what capabilities support.
7. **Exclusion criteria** — years, venue tiers, languages, paper types to deprioritize.
8. **Vocabulary** — key terms, competing terminology for the same concept, known author groups. Materially improves clustering and contradiction detection.
9. **Voice** — for drafted prose: terse/technical vs. expository; existing manuscript to match style against.

Store as structured YAML, and also render a `scope.md` the user can hand-edit. Re-running analysis after a scope change should offer to re-analyze rather than silently mixing outputs generated under different scopes — **stamp every note with the scope version that produced it.**

---

## 8. Source adapters

Common interface. Every adapter returns the same normalized record:

```python
class LibraryItem(TypedDict):
    source_id: str          # adapter-native ID
    citekey: str
    metadata: dict          # CSL-JSON
    attachments: list[Path] # resolved local PDF paths
    notes: list[Note]       # user notes, HTML + plaintext
    annotations: list[Annotation]  # highlights/comments, with page + color
    tags: list[str]
    collections: list[str]
```

### 8a. Zotero local HTTP API — *preferred*
- Requires Zotero 7 running with the local API enabled in Advanced settings.
- Base: `http://127.0.0.1:23119/api/users/0/`. Mirrors Web API v3 shapes.
- **Read-only.** Cannot write. Write-back must use the Web API (§12).
- If Better BibTeX is installed, get citekeys from its JSON-RPC endpoint at `/better-bibtex/json-rpc`.
- Failure mode: Zotero not running → clear message, fall through to SQLite.

### 8b. `zotero.sqlite` direct read
- **Zotero locks the DB while running.** Always copy to a temp file and open read-only (`file:...?mode=ro&immutable=1`). Never open the live file. Never write to it under any circumstance — that can corrupt a library.
- Paths: macOS/Linux `~/Zotero/zotero.sqlite`, Windows `%USERPROFILE%\Zotero\zotero.sqlite`.
- Attachments at `~/Zotero/storage/<8-char-key>/`. Handle linked files (absolute paths stored) and linked-attachment base directories.
- Annotations live in `itemAnnotations` (Zotero 6+). Extract highlight text, comments, page labels, colors.
- Schema is undocumented and version-dependent: record `schema version` on read and warn if unrecognized. Put the query set in `references/zotero-internals.md` so it's maintainable.

### 8c. Export directory
- Accept Zotero CSV, BibTeX/BibLaTeX, CSL-JSON, or Zotero RDF, plus an adjacent `files/` folder.
- Match attachments by the `file` field, then by filename heuristics, then report unmatched.
- Notes come through in RDF and CSV exports; note this varies by format and tell the user what was recovered.

### 8d. Generic PDF folder
- No metadata. Extract DOI/arXiv ID from the PDF text and first-page content, then resolve metadata via Crossref / arXiv / OpenAlex. Ask before making network calls.
- Fall back to filename parsing, then to "unidentified" with a prompt for manual entry.

**Adapter selection is recorded in config, but `/lit-ingest --source <adapter>` overrides.** Multiple sources can be merged; dedupe on DOI → arXiv ID → normalized title+year.

---

## 9. Pipeline

Each stage is independently runnable, checkpointed, and resumable.

**Stage 1 — Ingest.** Adapter → `raw/`. Resolve citekeys, dedupe, report items with missing PDFs. Never fail the whole run for one bad item; collect errors into a report.

**Stage 2 — Text extraction.** `pymupdf4llm` for markdown-with-structure by default. Detect scanned PDFs (near-zero text layer) and either OCR via `ocrmypdf` if available or flag as unprocessable. Optional GROBID path for proper section/reference structure on messy PDFs. Preserve page boundaries as markers — **locators depend on this**, so do not strip them.

**Stage 3 — Figures.** See §11.

**Stage 4 — Analyze.** Per paper: text + user annotations + scope block → per-paper note. Long papers get map-reduce (per-section pass, then a synthesis pass) rather than truncation. Run papers in parallel batches with a concurrency cap. Self-check pass: verify every locator in the note actually points at content containing the claim; downgrade `confidence` and mark `[UNVERIFIED]` where it doesn't.

**Stage 5 — Enrich.** Optional Scholar Labs pass. See §10.

**Stage 6 — Synthesize.** Cross-paper. See §13.

**Stage 7 — Sync.** Optional Zotero write-back. See §12.

---

## 10. Scholar Labs enrichment (optional)

**Read this section carefully — it corrects a common assumption about how the feature works.**

Google Scholar Labs is an experimental AI mode in Google Scholar. Critically, its AI summaries are **query-relative, not per-paper**: for a natural-language research question, it returns ranked papers, each with a short generated summary explaining *how that paper addresses your question*, plus a follow-up question box. There is no per-paper summary you can fetch by DOI.

This is actually a better fit than a per-paper summarizer, because the scope interview already produced a research question. But it changes the design:

- Enrichment is **query-driven**, not per-paper-driven. Generate a small set of well-formed research questions from the scope block, run each as a Scholar Labs session, and attach the returned relevance annotations to whichever corpus papers appear in the results.
- Papers in the corpus that don't surface get **no enrichment**, and their notes must say so (P4). Do not treat absence as a signal about quality.
- Results may surface papers *not* in the library. Collect these into `synthesis/suggested-additions.md` for the user to import. Do not auto-add.

**Hard constraints to design around:**
- Access is phased/limited and may require a waitlist; it requires a signed-in Google account. The probe must distinguish "signed out" from "waitlisted" from "working."
- There is a **session question limit (~5)**. Budget queries deliberately: 3–5 high-value questions per run, not one per paper. Make the query plan visible to the user and let them edit it before execution.
- It's experimental — the DOM will change. Isolate all selectors in one config file, verify expected structure before parsing, and fail with "Scholar Labs page structure changed, enrichment skipped" rather than parsing garbage.

**Etiquette and terms (build these in, don't leave to the user):**
- Human-paced interaction only, in the user's own signed-in browser session. Meaningful delays between actions. No parallel sessions, no headless credential handling, no CAPTCHA circumvention.
- Hard-cap queries per run and per day, enforced in code.
- Automated access to Google Scholar sits outside its terms of service. The setup wizard must state this plainly, require explicit opt-in, and make clear that continued use is the user's call. Default this capability to **off**.
- Cache aggressively — never re-query for something already retrieved.

**Browser control:** use whatever is available in the user's Claude Code environment (the Chrome extension integration, a chrome-devtools MCP server, or a Playwright MCP server). Abstract behind a small interface with a `navigate / read_page / click / type` surface so the backend is swappable. The probe must confirm an actual round trip, not just that a tool exists.

**Spike this before building on it (see §16).** If Scholar Labs turns out inaccessible for the user, the fallback is Semantic Scholar's API (free, has TLDRs, no scraping) and OpenAlex — both should be implemented as `enrich_semantic_scholar`, which is honestly the more reliable capability and should probably ship first.

---

## 11. Figure and table extraction (optional)

Do not build a custom detector. Layered strategy, best available wins:

**Tier 1 — arXiv source.** If the paper has an arXiv ID, fetch the e-print tarball. It contains the *original* figure files (PDF/PNG/EPS) plus the LaTeX, which gives exact captions, labels, and `\ref` context. This is dramatically better than anything extracted from a rendered PDF. Handle: papers with no source available, single-file submissions, and non-arXiv papers.

**Tier 2 — PyMuPDF embedded images.** `page.get_images()` for raster figures. Pair with captions by locating text blocks starting with `Figure N` / `Table N` nearest the image bbox. Known limitation: **vector figures drawn with PDF operators don't appear as embedded images** — many plots will be missed. Detect this case (page has a `Figure N` caption but no image nearby) and fall back to Tier 3.

**Tier 3 — Region rasterization.** For a detected caption with no extractable image, render the surrounding page region to PNG at high DPI. Crude but usable, and clearly labeled as such in `figures.json`.

**Tables:** extract with PyMuPDF's table finder into both CSV and markdown. Tables are frequently the highest-value artifact for methods comparison — feed them into the methods matrix.

**Optional heavy path:** `pdffigures2` produces the best results but needs a JVM. Offer as an opt-in extra, never a default dependency.

Every figure record carries `extraction_method` so the user knows how much to trust it. If a paper yields nothing, the note says `Figure extraction produced no assets for this paper` — not an empty section.

---

## 12. Zotero write-back (optional)

The local API is read-only, and writing to `zotero.sqlite` is unsupported and dangerous. Write-back therefore goes through the **Zotero Web API v3**, which requires the library to be synced.

Setup collects: user ID and an API key with write permission (from the Zotero settings page). Store the key in the OS keychain if available, else a `0600` file. Never in `.lit/`, never in a repo.

**Probe:** create a note on a scratch item, read it back, delete it. Show the user the result. Only then mark enabled.

**Two note types:**

1. **Per-paper child notes** — attached to the parent item. Contains the per-paper summary rendered as clean HTML (Zotero notes are HTML; convert from markdown, keep it simple — headings, lists, bold, links).
2. **Standalone notes** — for synthesis outputs: the master index, themes, methods matrix, gap analysis, and review draft. Place them in a dedicated collection, e.g. `lit-agent / <project name>`.

**Safety rules:**
- Tag every generated note `lit-agent` plus a project tag. This makes them identifiable, filterable, and bulk-removable.
- Include a machine-readable footer with the citekey, generation timestamp, scope version, and plugin version.
- **Never modify or delete a user-authored note.** Only create, or update notes carrying the `lit-agent` tag.
- Updates match on the footer citekey + project. If a tagged note was hand-edited since generation (content hash mismatch), do not overwrite — create a new versioned note and tell the user.
- Batch writes (Web API accepts up to 50 items per request), respect `Backoff` and `Retry-After` headers, and handle 412 version conflicts by re-fetching.
- `/lit-sync --dry-run` prints exactly what would be created/updated. Make this the default first-run behavior.
- `/lit-sync --undo` removes every note tagged `lit-agent` for this project, with confirmation and a count.

---

## 13. Synthesis and Q&A

### `/lit-review`
Consumes all per-paper notes plus the scope block. Produces:
- `index.md` — sortable master table: citekey, title, year, venue, type, methods, relevance, one-liner.
- `themes.md` — clusters with membership, an argued through-line per cluster, and papers that resist clustering (often the interesting ones).
- `methods-matrix.md` — papers × (method, dataset, metric, headline result, code available). Built primarily from extracted tables and the analyzer's structured fields.
- `gaps.md` — what the corpus doesn't cover, relative to the stated research question. Distinguish "no one has done this" from "not in this library" — the second is far more likely and must be stated as such.
- `contradictions.md` — conflicting findings with both sides and locators. High value, high fabrication risk: **every entry needs two verified locators or it doesn't ship.**
- `review-draft.md` — prose related-work section in the user's stated voice, every claim carrying `[@citekey, p. N]`, with an `[UNVERIFIED]` marker anywhere support couldn't be located.
- `refs.bib` — BibTeX for exactly the cited subset, using the same citekeys.

Support `--filter` on frontmatter fields (`relevance`, `year`, `paper_type`, `scope_tags`) so the user can synthesize over a subset.

### `/lit-ask`
Conversational Q&A. Retrieval strategy by corpus size:
- **Default (grep/glob):** frontmatter-field filter → keyword search across notes → read full notes for hits → answer with citations. Fast, zero dependencies, works well under ~150 papers.
- **Optional vector index:** chunk the per-paper notes *and* full text, embed, store in `sqlite-vec` (single file, pip-installable, no server). Hybrid retrieval: BM25 + vector, then rerank. Enable this capability only when the probe passes and the user opted in.
- The index is a projection of markdown (P5). `/lit-index --rebuild` regenerates from scratch. Staleness detection on note mtime with a warning when the index lags.

Answers always cite. If the corpus doesn't support an answer, say so rather than answering from model priors — and offer to check whether the question is a gap worth noting.

---

## 14. Commands summary

| Command | Purpose | Requires |
|---|---|---|
| `/lit-setup` | Capability wizard + scope interview | — |
| `/lit-doctor` | Re-probe, report, repair | — |
| `/lit-scope` | Re-run scope interview | — |
| `/lit-ingest` | Pull library into corpus | `source` |
| `/lit-analyze` | Per-paper notes | `pdf_text` |
| `/lit-figures` | Extract figures/tables | `figures` |
| `/lit-enrich` | Scholar Labs / Semantic Scholar pass | `browser` or network |
| `/lit-review` | Cross-paper synthesis + draft | analyzed corpus |
| `/lit-ask` | Q&A with citations | analyzed corpus |
| `/lit-sync` | Zotero write-back | `zotero_write` |
| `/lit-run` | Ingest → analyze → figures → review, end to end | varies |

---

## 15. Milestones

**M0 — Spikes (do first).** Time-boxed. Resolve the unknowns in §16 before committing to architecture. Output: a short findings doc that may amend this plan.

**M1 — Skeleton + setup.** Plugin scaffold, capability system, `/lit-setup`, `/lit-doctor`, scope interview. *Acceptance:* a fresh user on macOS, Linux, and Windows can run setup, decline every optional capability, and land in a valid enabled state.

**M2 — Ingest + text.** All four adapters, dedupe, text extraction, checkpointing. *Acceptance:* a 50-item library ingests fully; killing the process mid-run and re-running resumes correctly and produces identical output.

**M3 — Analyze.** Per-paper notes, scope conditioning, locator self-check. *Acceptance:* on a 20-paper gold set, spot-check 40 locators; ≥90% resolve to supporting text, and no fabricated citations. **This gate is hard — do not proceed with a failing locator check.**

**M4 — Q&A.** Grep retrieval, `/lit-ask`. *Acceptance:* 15 test questions answered with correct citations over the gold set.

**M5 — Synthesis.** `/lit-review` and all artifacts. *Acceptance:* draft related-work section reviewed by the user against the actual papers; contradiction entries all verify.

**M6 — Figures.** Three-tier extraction. *Acceptance:* ≥1 correctly captioned figure from 80% of gold-set papers with figures; misses reported explicitly.

**M7 — Zotero write-back.** *Acceptance:* dry-run, sync, hand-edit-then-resync (no overwrite), and undo all behave correctly on a **scratch Zotero library**, never the user's real one.

**M8 — Enrichment.** Semantic Scholar first, Scholar Labs second, contingent on M0 findings.

**M9 — Vector index.** Optional, behind a flag.

**M10 — Distribution.** README with install + quickstart, sample corpus, troubleshooting table, `claude plugin validate` clean, public repo.

---

## 16. Spike tasks (resolve before building)

1. **Scholar Labs access & automation.** Does the user have access? Can the browser tooling reach it, sign-in intact? What does the result DOM look like? Is the session limit workable? *If this spike fails, cut Scholar Labs to a stretch goal and ship Semantic Scholar instead.*
2. **Browser control surface.** What browser automation is actually available in Claude Code for a typical user, and how does it authenticate against an existing Chrome profile?
3. **Zotero local API shape.** Confirm endpoints, whether annotations come through, and whether Better BibTeX citekeys are reachable.
4. **SQLite schema.** Validate the query set against current Zotero on all three platforms.
5. **Figure extraction reality check.** Run all three tiers on 20 papers from the user's own library. Measure how many are vector figures that Tier 2 misses. This determines whether Tier 3 or `pdffigures2` is worth it.
6. **Token cost per paper.** Measure end-to-end cost for a 12-page and a 40-page paper. If a 300-paper corpus is prohibitive, design a triage pass (cheap relevance screen → full analysis only on high-relevance) into M3 rather than bolting it on later.

---

## 17. Risks

| Risk | Mitigation |
|---|---|
| Fabricated citations in drafted prose | P7, locator self-check, `[UNVERIFIED]` markers, M3 hard gate |
| Scholar Labs inaccessible or breaks | Optional by default; Semantic Scholar fallback ships first |
| Scraping violates Scholar ToS | Off by default, explicit opt-in, human-paced, hard caps, documented plainly |
| Zotero library corruption | Never write to sqlite; copy-on-read; write-back only via Web API; scratch-library testing |
| Cost blowup on large corpora | Triage pass, per-run budget cap, cost estimate shown before long runs |
| Zotero schema drift | Isolated query layer, version detection, graceful warning |
| Setup complexity drives users away | Required path has zero optional deps; every extra is skippable; `/lit-doctor` as universal first aid |
| Silent quality degradation | P4 — every gap and demotion appears in output and run summary |

---

## 18. Distribution

MIT license, public repo, installable via Claude Code's plugin marketplace mechanism. README leads with a 3-minute quickstart on the zero-optional-capabilities path, then a capability matrix, then troubleshooting. Ship a small sample corpus of open-access papers so a new user can see real output before pointing it at their own library.

Since this targets researchers outside the author's institution, use `~~` placeholder conventions for any tool references that vary by user, and document them in `CONNECTORS.md`.
