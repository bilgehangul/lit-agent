---
name: lit-review
description: Synthesize across all analyzed papers - master index, thematic clusters, methods matrix, gap analysis, contradictions, a drafted related-work section, and BibTeX for the cited subset. Use when the user says "/lit-review", "write my related work", "synthesize my papers", "what are the themes", "where are the gaps", or "which papers disagree".
---

# lit-review

Cross-paper synthesis over `.lit/papers/`. Produces seven artifacts in `.lit/synthesis/`
plus `.lit/refs.bib`.

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere. Let `S` be `${CLAUDE_PLUGIN_ROOT}/scripts`.

## The split, and why it exists

**Mechanical — the script writes these.** `index.md`, `methods-matrix.md`, `refs.bib` are
projections of frontmatter. Generating them in code makes them exact and free, and stops a
year or a dataset name getting paraphrased on the way through.

```
PY "S/review.py" --generate
```

**Judgement — you write these.** `themes.md`, `gaps.md`, `contradictions.md`,
`review-draft.md` need reading across papers.

```
PY "S/review.py" --brief        # everything you need, as JSON
```

The brief carries the scope block, every paper's frontmatter and one-liner, all
cross-references from the notes' Connections sections, pre-collected contradiction
candidates, open questions, every `[UNVERIFIED]` claim, and which notes are below `high`
confidence.

Both accept `--filter relevance=high year>=2020` to synthesize over a subset.

## Then enforce the rules

```
PY "S/review.py" --check
```

This is not advisory. It fails the run when an artifact breaks a rule, and the rules are the
point of the milestone:

- **Every `contradictions.md` entry needs two verified locators, one per side.** The checker
  resolves each locator against the cited paper's extracted text and rejects the entry if
  fewer than two verify — or if any points at a page that does not exist.
- **Every `gaps.md` entry says which kind of gap it is** (see below).
- **Every paragraph in `review-draft.md` carries a locator or `[UNVERIFIED]`**, and every
  citekey resolves to a real note.

Mark a preamble, a methodology note, or a "rejected candidates" list with
`<!-- not-an-entry -->` so it sits in the file without being validated as a claim. Recording
what you considered and dropped is valuable — just say that is what it is.

## Writing each artifact

### `themes.md`

Clusters with an **argued through-line each**, not a list of topics. A theme that is just
"these papers mention X" is not worth writing.

**Include the papers that resist clustering.** They are usually the interesting ones — the
paper taking a different object, or asking a question the others structurally cannot. Say
what makes them not fit.

### `gaps.md`

Tag every entry with one of:

| Tag | Meaning |
|---|---|
| `not-in-this-library` | the work may well exist; this corpus does not have it |
| `not-in-the-literature` | a real research gap — claim this **only** when a paper in the corpus says so |
| `unresolved` | the corpus covers this, but the answer is disputed or unmeasured |

**`not-in-this-library` is the overwhelmingly likely case and the honest default.** A gap of
that kind is a reading-list item. A gap of the second kind is a paper the user could write.
Conflating them turns an incomplete library into a false claim of novelty, which is the most
damaging mistake this artifact can make.

If a chunk of the library is ingested but not yet analyzed, say so at the top of the file and
name the unanalyzed papers that are on topic.

### `contradictions.md`

High value, high fabrication risk. An invented disagreement between two real papers is the
worst thing this tool could emit, because it reads exactly like scholarship.

For each entry: state both sides with `[@citekey, p. N]`, give the size of the gap, say what
the two papers **agree** on (usually more than the disagreement suggests), and list candidate
explanations without pretending to settle them.

Keep a `<!-- not-an-entry -->` section for candidates you rejected and why. "Only one side
could be located" is a good reason and worth recording.

### `review-draft.md`

Prose in the voice from the scope block. Every claim carries `[@citekey, p. N]`.

Use `[UNVERIFIED]` for anything you assert that no paper states — a synthesis inference is
legitimate, but it must be marked rather than dressed in a citation. Add a
`<!-- not-an-entry -->` notes section explaining each `[UNVERIFIED]` you left.

**Say plainly at the top if the scope is a fixture or the corpus is partly analyzed.** A draft
that looks finished but rests on six of twenty papers will get used as if it rested on twenty.

## Reporting

Tell the user: how many papers went in, how many were excluded by filters, which contradiction
candidates were rejected and why, and anything below `high` confidence that the synthesis
leans on. Then point them at `review-draft.md` and say clearly that the locators are there to
be checked, not trusted.
