# STATE

Rewritten after every task. Read this first (see `CLAUDE.md` startup protocol).

```
Current:     M0-M5 complete. Next milestone is M6 (figures).
Next task:   Finish the M3 gold set - 14 of 20 papers are ingested and extracted
             but not yet analyzed. Do this BEFORE M6: gaps.md and themes.md
             currently rest on 6 papers and say so.
Last commit: M5 - cross-paper synthesis with enforced rules
Blocked:     none
Needs user:  (optional) Zotero local HTTP API is off. Enabling it unlocks adapter 8a.
             Zotero > Settings > Advanced > "Allow other applications on this computer
             to communicate with Zotero". Everything works without it.
Gate status: M0 done | M1 done | M2 done | M3 done (PASS) | M4 done | M5 done
             M6 . | M7 . | M8 . | M9 . | M10 .
```

## Where things stand

**Working end to end** on the developer's real Zotero library, with every optional
capability disabled — the zero-optional path P3 protects:

- 103 items ingested, 93 after dedupe, 78 with extracted text
- 6 papers analyzed into full notes; **M3 gate passed** (40 sampled locators, 100%
  supported, zero fabrications)
- `/lit-ask` retrieval: 15/15 acceptance questions surfaced an expected source in the top 3
- All 7 synthesis artifacts generated and passing `review.py --check`
- 138 tests pass

## The honest gaps in what is built

1. **The M3 gate ran on 6 of the 20 gold-set papers.** They span systems / empirical /
   dataset / survey deliberately, and produced 288 locators against the 40 the criterion
   samples — but paper-level diversity is the point of a 20-paper set. The other 14 are
   ingested and extracted, waiting on analysis.
2. **Adjudication was self-review.** Locators the lexical screen could not confirm were
   judged by the same analyst who wrote the notes. Each verdict names its supporting
   sentence so it is auditable, but a second reader would be stronger evidence.
3. **The active scope is a development fixture**, not a real `/lit-scope` interview. Every
   note is stamped `scope_version: 78b59e200079` and marked as fixture-derived. Real use
   starts with `/lit-scope`.
4. **Untestable in this environment:** scanned-PDF OCR (the library has none), Better BibTeX
   citekeys (not installed), Zotero write-back (needs a scratch library — M7).

## Decisions locked for this build

- Harness: lightweight in-repo (`CLAUDE.md` + `ROADMAP.md` + `STATE.md`). No GSD dependency.
- Test corpus: the user's real Zotero library at `%USERPROFILE%\Zotero`, **strictly
  read-only**. Verified unmodified after every read.
- Working corpus for development lives outside the repo (scratchpad), so no third-party PDF
  or quoted paper text is ever committed. Only metadata-derived examples are in `docs/examples/`.
- Git: local repo, remote `https://github.com/bilgehangul/lit-agent.git`.

## Spec amendments made (see `docs/decisions/`)

- **ADR-0001** — raw `pymupdf` extraction by default; `pymupdf4llm` behind `--layout`.
  Measured 85x slower for 3% more text.
- **ADR-0002** — build Tier 3 figure rasterization; `pdffigures2`/JVM not needed to pass M6.
- **ADR-0003** — Semantic Scholar ships as the enrichment path; Scholar Labs demoted to a
  stretch goal after `/labs` returned 404.

## Stopping conditions

Stop and report rather than working around it if:
- the M3 locator gate falls below 90%;
- no Python interpreter resolves the core dependencies;
- the Zotero SQLite schema matches no known version;
- per-paper cost makes a gold-set run unreasonable.
