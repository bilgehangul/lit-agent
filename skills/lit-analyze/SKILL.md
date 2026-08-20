---
name: lit-analyze
description: Read each ingested paper and write a structured, scope-aware per-paper note with verified page locators. Use when the user says "/lit-analyze", "summarize my papers", "analyze the corpus", "write notes for these papers", or after /lit-ingest.
---

# lit-analyze

You are the analyzer. `scripts/analyze.py` decides *what* to analyze and validates what you
write; the reading and the judgement are yours.

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere. Let `S` be `${CLAUDE_PLUGIN_ROOT}/scripts`.

## The rule that outranks everything else

**Never fabricate a citation (P7).** Every claim you write about a paper carries a locator
back to where you actually read it. If you cannot locate support, write `[UNVERIFIED]`.

`[UNVERIFIED]` is a *correct* answer and counts as a pass in verification. A confidently
wrong page number does not. When you are unsure between `[p. 6]` and `[p. 7]`, you are
unsure — say `[UNVERIFIED]` or widen to the section.

## 1. Plan

```
PY "S/analyze.py" --plan
```

Shows what is pending, the estimated input tokens, and which papers need map-reduce.

**Show the user the estimate before a long run** and let them bound it with `--limit`.
A full corpus can run to well over a million input tokens.

Two warnings to relay rather than skip past:

- `scope_missing` — analysis without a scope produces generic summaries. Offer `/lit-scope`.
- `scope_is_fixture` — the active scope is a development fixture, not the user's real
  research question. Say so plainly; notes written under it are not trustworthy output.

## 2. Get work units

```
PY "S/analyze.py" --next 4
```

Each unit carries everything you need: the scope block, the path to the extracted text,
prepared frontmatter, the user's own notes and annotations, figure records, and
`absence_notices`.

Analyze **up to 4 papers concurrently** (the unit payload names the cap). More than that and
a bad note becomes hard to attribute.

## 3. Read the paper

Read `text_path`. It is markdown with `<!-- page N -->` markers.

**Track the page marker as you read.** The page number you cite must be the marker the
supporting sentence actually sits under. This is the single mechanical habit that makes P7
hold — verification resolves your locator against exactly that page's text.

For a unit whose `strategy` is `map-reduce` (very long papers), work section by section and
then synthesize. **Never truncate the paper and analyze the head of it** — a note built on
the first 20 pages of a 60-page survey misrepresents it.

## 4. Write the note

Write `.lit/papers/<citekey>.md`: the unit's `frontmatter` plus the 12 body sections in the
fixed order from `references/output-schemas.md`.

Fill in the judgement fields the frontmatter leaves as `null`: `relevance`, `paper_type`,
`methods`, `datasets`, `metrics`, `scope_tags`, `confidence`. Leave the factual fields alone
— they came from the library and must not be re-derived.

Section-by-section, the parts people get wrong:

- **2. Relevance to this project** — scope-conditioned, concrete. *Why this paper matters to
  this question.* If it does not, say so plainly; a corpus contains tangential papers and
  pretending otherwise wastes the user's time.
- **5. Evaluation** and **6. Key findings** — every claim carries `[p. N]`. Headline numbers
  get the page they appear on.
- **7. Limitations** — two clearly labeled sub-blocks: what the paper *states* about its own
  limitations, and what *you* observed. Never blend them; the distinction is the whole value.
- **9. Your notes** — the user's own notes and annotations, **verbatim, never rewritten or
  summarized**. Your commentary goes in a sub-block clearly marked as yours.
- **10. Citation-ready claims** — 3–6 sentences the user could paste into a paper, each with
  its citekey and locator. This is the section that makes writing fast; make them genuinely
  paste-ready, not paraphrases of the abstract.

**Anything you cannot determine is written as `Not determinable from the text`.** Never
guess, never leave a section blank. An empty section is a silent gap (P4).

**Copy each `absence_notices` sentence verbatim into its section.** They say things like
`Figure extraction produced no assets for this paper.` — the point is that the note states
why a section is thin instead of appearing to have nothing to say.

## 5. Accept the note

```
PY "S/analyze.py" --accept <citekey>
```

This validates the schema and checks every locator. It **rejects** a note that has a
fabricated locator — one pointing at a page that does not exist — and records the error.
Fix and re-accept; do not work around it.

It returns `flagged_for_review`: locators whose cited page does not obviously contain the
claim. That is a lexical screen, not a verdict. **Go read those pages again.** If the claim
really is supported, keep the locator. If it is not, correct the page or change it to
`[UNVERIFIED]` and drop `confidence` to `medium` or `low`.

## 6. Verify the batch

```
PY "S/verify.py" --sample 40 --seed 1 --markdown docs/spikes/m3-locator-check.md
```

The M3 gate: at least 90% of sampled locators supported, and **zero fabrications**.

Report the real number. If the gate fails, say so and stop rather than proceeding to
synthesis — a review built on unverifiable notes is worse than no review.

## Reporting

Tell the user: how many papers were analyzed, the pass rate, anything marked `[UNVERIFIED]`
and why, papers whose `relevance` came out `low` or `tangential`, and any paper where
`confidence` is not `high`. The papers you were least sure about are the ones they most need
to know about.
