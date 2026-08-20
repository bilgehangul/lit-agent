# STATE

Rewritten after every task. Read this first (see `CLAUDE.md` startup protocol).

```
Current:     M1 — Skeleton + setup
Next task:   capabilities.json state machine (scripts/lib/capabilities.py)
Last commit: M0 spikes complete — findings + 3 ADRs
Blocked:     none
Needs user:  (optional) Zotero local HTTP API is off. Enabling it unlocks adapter 8a.
             Zotero > Settings > Advanced > "Allow other applications on this computer
             to communicate with Zotero". Everything works without it.
Gate status: M0 done | M1 > | M2 . | M3 . | M4 . | M5 .
```

## What M0 settled (see `docs/spikes/FINDINGS.md`)

- **Python 3.14.2** runs everything. No 3.13 fallback.
- **Zotero local API is off** (403) but Zotero 7 is running (`/connector/ping` = 200).
  Better BibTeX is **not installed** — generated citekeys are the primary path here.
- **SQLite query set validated** against `userdata` schema 125; original file verified
  unmodified. Query set in `references/zotero-internals.md`.
- **0 annotations, 126 child notes** in the dev library — the notes path carries section 9.
- **No scanned PDFs**, so the OCR branch ships untested here and that limit is recorded.
- **Whole-corpus input ≈ 1.73M tokens** (88 papers, median ~16.6k each).
- **3 spec amendments** — ADR-0001 (fast extraction), ADR-0002 (Tier 3, no JVM),
  ADR-0003 (Semantic Scholar over Scholar Labs).

## Decisions locked for this build

- Harness: lightweight in-repo (`CLAUDE.md` + `ROADMAP.md` + `STATE.md`). No GSD dependency.
- Autonomy: build straight through M0 to M5; stop only on a hard-gate failure.
- Test corpus: the user's real Zotero library at `%USERPROFILE%\Zotero`, **strictly read-only**.
  88 PDFs across 117 storage folders. Gold set = 20 papers, manifest tracked, PDFs never committed.
- Dev scope fixture: `tests/fixtures/scope.dev.yaml`, derived from the library's densest cluster
  (LLM-based privacy policy analysis). A stand-in for the real `/lit-scope` interview, marked as such.
- Git: local repo, remote `https://github.com/bilgehangul/lit-agent.git`.
- M6 to M10 are queued but out of scope for this run.

## Stopping conditions

Stop and report rather than working around it if:
- the M3 locator gate falls below 90%;
- no Python interpreter resolves the core dependencies;
- the Zotero SQLite schema matches no known version;
- S4 shows per-paper cost that makes the gold-set run unreasonable.
