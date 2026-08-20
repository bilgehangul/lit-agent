# Output schemas

Every file lit-agent writes, and the rules that make it machine-readable.

Markdown is the source of truth (**P5**). The Zotero notes and the vector index are
projections of these files and must be fully rebuildable from them.

---

## `.lit/` corpus layout (spec section 6)

```
.lit/
├── config.yaml               # scope, source config, output prefs
├── scope.md                  # hand-editable rendering of the scope
├── state.json                # per-item pipeline checkpoints
├── raw/<citekey>.json        # normalized source metadata
├── text/<citekey>.md         # extracted full text, with page markers
├── figures/<citekey>/        # fig-01.png + figures.json
├── papers/<citekey>.md       # THE per-paper note (source of truth)
├── synthesis/
│   ├── index.md              # master table of all papers
│   ├── themes.md
│   ├── methods-matrix.md
│   ├── gaps.md
│   ├── contradictions.md
│   ├── review-draft.md
│   └── suggested-additions.md
├── refs.bib                  # BibTeX for the cited subset
└── index/                    # optional vector store
```

**Citekey is the join key across everything.** Prefer Better BibTeX citekeys when available;
otherwise generate `authorYEARfirstword` and record the mapping in `state.json`.

---

## Citekey generation

When Better BibTeX is not available (it is not, in the development environment — M0/S1):

1. First author's last name, lowercased, non-ASCII folded, non-alphanumerics stripped.
2. Four-digit year. **Take the first 4 characters of Zotero's `date` field** — it packs a
   parsed date and the original string into one column (`"2024-08-25 2024-08-25"`).
   Unknown year → `nodate`.
3. First title word longer than 3 characters that is not a stopword, lowercased.

`doe2024privacy`. On collision append `a`, `b`, `c`. Record every mapping so a citekey is
stable across runs — **citekeys appear in the user's own manuscript, so they must never
silently change** (P6).

---

## `text/<citekey>.md` — extracted full text

Page markers are the primitive that citation verification depends on (**P7**). They are
emitted on their own line, before each page's text, and **must never be stripped**:

```
<!-- page 1 label=1330 -->
Title of the paper
...
<!-- page 2 label=1331 -->
...
```

### Two page numbers, because papers have two

A PDF's physical pages run 1..N. The number **printed on the page** is frequently something
else: Polisis is 19 physical pages printed 530-548, OPP-115 is 11 pages printed 1330-1340,
Del Alamo et al. is 24 pages printed 2053-2076. Four of the six gold-set papers are numbered
this way.

A reader citing "p. 1335" is using the printed number, and they are right to -- that is what
the published paper says and what every other citation of it uses. So:

- `label=` is emitted whenever the printed number differs from the physical index.
- **Both numbers resolve to the same page** during verification.
- A locator is `missing` only when it matches *neither* numbering.

Labels come from the PDF's embedded page-label table when it has one, and otherwise from
detecting a consistent arithmetic run of numbers in the header/footer bands. A single stray
number is not evidence; a sequence across most of the document is. When the printed numbers
simply are the physical index, no label is emitted -- the common case stays simple.

Getting this wrong would make a correct journal citation look fabricated, which is the worst
error this system can make: a false accusation is harder to recover from than a missed check.

Detected headings are emitted as markdown headings so section locators can resolve. Heading
detection is font-size heuristic, not a parser (ADR-0001) — when a heading cannot be
resolved confidently, no heading is emitted and the analyzer falls back to a page locator
rather than inventing a section number.

Header block at the top of the file:

```markdown
<!-- lit-agent: citekey=doe2024privacy pages=14 extractor=pymupdf chars=48213 -->
```

`extractor` is `pymupdf` (default) or `pymupdf4llm` (`--layout`), so a consumer knows which
path produced the file.

---

## `papers/<citekey>.md` — the per-paper note

The single most important artifact. Frontmatter is what makes grep-based retrieval work
(M4), so **field names and types must be consistent across every note.**

### Frontmatter

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
tags: [...]              # the user's own Zotero tags
scope_tags: [...]        # assigned against THIS project's scope
relevance: high          # high | medium | low | tangential
paper_type: empirical    # empirical | systems | theory | survey | position | dataset
methods: [...]
datasets: [...]
metrics: [...]
figures_extracted: 4
has_user_notes: true
enrichment: {semantic_scholar: true, scholar_labs: false, queries: [...]}
analyzed: 2026-08-20
scope_version: a1b2c3d4e5f6
confidence: high         # the analyzer's confidence in its own extraction
---
```

Rules:

- **`scope_version` is mandatory.** Notes produced under different scopes are not
  comparable; the stamp is what lets `/lit-analyze` detect staleness instead of silently
  mixing them.
- A field that could not be determined is `null`, never omitted and never guessed.
- `relevance` and `paper_type` are closed enumerations. A value outside them is a bug.
- `figures_extracted: 0` is meaningful and must be written, not left off.

### Body — always these 12 sections, in this order

1. **One-line summary** — what the paper does, in the researcher's vocabulary.
2. **Relevance to this project** — scope-conditioned. Why it matters *here*. If it does
   not, say so plainly.
3. **Problem & motivation**
4. **Approach** — enough detail to reimplement or critique.
5. **Evaluation** — datasets, baselines, metrics, headline numbers with locators.
6. **Key findings** — bulleted, each with `[p. N]` or `[§4.2]`.
7. **Limitations & threats to validity** — the paper's stated ones **and** the analyzer's
   observations, in separately labeled sub-blocks.
8. **Figures & tables** — extracted assets with captions and one line on what each shows.
9. **Your notes** — the user's own Zotero annotations and notes, **verbatim, never
   rewritten**. Analyzer commentary goes in a clearly marked sub-block beneath.
10. **Citation-ready claims** — 3–6 sentences the user could paste into a paper, each with
    its citekey and locator.
11. **Connections** — links to other citekeys: extends / contradicts / uses-method-of /
    superseded-by.
12. **Open questions** — what this paper leaves unanswered relative to the scope.

Anything the analyzer could not determine is written as **`Not determinable from the text`**
— never guessed, never silently omitted (**P4**).

Sections that a disabled capability could not fill say so explicitly:

- no figures capability → `Figure extraction was not enabled for this run.`
- figures ran but found nothing → `Figure extraction produced no assets for this paper.`
- no annotations in the library → `No Zotero annotations found for this paper.`

An empty section is a silent gap. A sentence explaining the gap is not.

### Locators

- Page: `[p. 7]` — must correspond to a `<!-- page 7 -->` marker in `text/<citekey>.md`.
- Section: `[§4.2]` — must correspond to a detected heading.
- Unsupported: `[UNVERIFIED]` — written when the analyzer cannot locate support.
  **Never** replaced by a guessed locator (**P7**).

---

## `figures/<citekey>/figures.json`

```json
{
  "citekey": "doe2024privacy",
  "generated": "2026-08-20T14:03:11Z",
  "assets": [
    {
      "id": "fig-01",
      "file": "fig-01.png",
      "kind": "figure",
      "label": "Figure 1",
      "caption": "System architecture of the proposed pipeline.",
      "page": 3,
      "extraction_method": "embedded_image",
      "confidence": "high"
    }
  ],
  "misses": [
    {"label": "Figure 4", "page": 9,
     "reason": "caption found but no extractable image (likely a vector figure)"}
  ]
}
```

`extraction_method` is one of `arxiv_source` | `embedded_image` | `page_region` |
`pdffigures2`. It tells a consumer how much to trust the asset — a `page_region` crop is a
rasterized guess, not a real figure file.

**`misses` is not optional.** S3 measured a 17% Tier-2 miss rate concentrated in
vector-figure papers; a figures.json with an empty `assets` array and no `misses` would
misrepresent a paper as having no figures.

---

## `state.json` — pipeline checkpoints

Per-item, per-stage, so a crash at paper 187 resumes at 187 (**P6**).

```json
{
  "version": 1,
  "scope_version": "a1b2c3d4e5f6",
  "items": {
    "doe2024privacy": {
      "source_id": "ABCD1234",
      "stages": {
        "ingest":   {"status": "done",    "at": "...", "hash": "sha256:..."},
        "text":     {"status": "done",    "at": "...", "chars": 48213},
        "figures":  {"status": "skipped", "reason": "figures capability disabled"},
        "analyze":  {"status": "error",   "at": "...", "error": "..."}
      }
    }
  },
  "runs": [
    {"started": "...", "finished": "...", "command": "lit-ingest",
     "processed": 88, "errors": 2, "demotions": []}
  ]
}
```

`status` is `done` | `error` | `skipped` | `pending`. A stage is re-run when the status is
not `done`, or when `--force` is given. `hash` is over the source PDF, so a replaced file
re-triggers the pipeline for that item alone.

`skipped` always carries a `reason`. Silent skips are the failure mode P4 forbids.

---

## `~/.lit-agent/capabilities.json`

```json
{
  "version": 1,
  "updated": "2026-08-20T14:03:11Z",
  "capabilities": {
    "pdf_text": {
      "status": "enabled",
      "last_verified": "2026-08-20T14:03:11Z",
      "config": {},
      "last_error": null
    }
  }
}
```

`status` is `enabled` | `disabled` | `broken` — and nothing else (**P1**). An unrecognized
value is read as `disabled`. `broken` is only ever a demotion from `enabled`, never an
initial state.

Written atomically: a half-written capability file is precisely the state P1 forbids.
