---
name: lit-scope
description: Run or re-run the research scope interview that shapes every lit-agent output - field, research question, purpose, what matters in a paper, desired artifacts, exclusions, vocabulary, and voice. Use when the user says "/lit-scope", "set my research scope", "my research question changed", or as Phase D of /lit-setup.
---

# lit-scope

The scope block is injected into **every** downstream prompt. A vague answer here degrades
every per-paper note and every synthesis artifact, so the interview is worth doing properly.

Persisted to `.lit/config.yaml`, rendered to a hand-editable `.lit/scope.md`.

## How to ask

**Conversationally, adapting to the answers. Do not fire all nine questions at once.** Ask
two or three, listen, follow up where an answer was thin, move on. This should feel like a
colleague asking what you are working on, not a form.

The questions (spec section 7):

1. **Field and subfield** — and what venue-level vocabulary to use.
2. **Research question** — the actual question, one or two sentences. **Push for
   specificity.** If the answer is "LLMs and privacy", ask what about them: which stage of
   the pipeline, which threat, whose data, measured against what. This doubles as the
   enrichment query seed, so it needs to be a real question.
3. **Purpose of this pass** — related work / survey paper / thesis chapter / methods
   scouting / grant background / staying current. Different purposes want different notes.
4. **Stage** — starting cold, refining a draft, or filling gaps in an existing manuscript.
5. **What matters in a paper** — methods, empirical results, datasets, theory, limitations,
   reproducibility, deployment. **Ranked**, not merely selected.
6. **Desired artifacts** — multi-select, and **only offer what capabilities support**.
   Check the enabled set first; do not offer figures if `figures` is off, because that
   promises a file that will come back empty (P4). Use
   `lib.scope.available_artifacts(enabled)` to get the offerable list.
7. **Exclusion criteria** — years, venue tiers, languages, paper types to deprioritize.
8. **Vocabulary** — key terms, competing terminology for the same concept, known author
   groups. This materially improves clustering and contradiction detection, so it is worth
   a follow-up even if the user shrugs at first.
9. **Voice** — terse/technical or expository, and whether there is an existing manuscript to
   match style against.

## Persisting

```python
import sys; sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/scripts")
from lib.scope import Scope, save
from lib.paths import Corpus

corpus = Corpus(); corpus.ensure()
scope = Scope(
    field="...", subfield="...", research_question="...",
    purpose="related_work", stage="starting_cold",
    what_matters=["methods", "empirical_results"],
    artifacts=["per_paper_notes", "review_draft"],
    exclusions={"before_year": 2018},
    vocabulary={"key_terms": [...], "synonyms": {...}, "author_groups": [...]},
    voice={"tone": "terse technical"},
)
print(scope.problems())          # fix anything reported before saving
version = save(scope, corpus.config)
```

`scope.problems()` flags answers that will hurt downstream quality — an empty or three-word
research question, unrecognized enum values. **Read the problems back to the user and offer
to sharpen the answer** rather than saving something you already know is weak.

## The scope version

`save()` returns a `scope_version` — a hash of the answers. Every per-paper note is stamped
with the version that produced it.

**When re-running the interview changes the version**, say so explicitly and offer:

- **re-analyze** everything under the new scope (`/lit-analyze --force`), or
- **leave existing notes** and analyze only new papers — but then the corpus holds notes
  written to two different questions, and any synthesis over it mixes them. Say that out
  loud; do not let it happen silently.

Never quietly mix scopes. That is exactly the silent gap P4 exists to prevent.

## Hand edits

Users can edit `.lit/scope.md` directly. `/lit-scope --import` folds those edits back into
`config.yaml` and recomputes the version.
