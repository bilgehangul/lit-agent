# lit-agent — build rules

A Claude Code plugin that turns a reference-manager library into a queryable, citable
literature knowledge base. **The source of truth for what to build is
`lit-agent-project-plan.md`.** This file is the source of truth for *how* to build it.

## Session startup protocol

1. Read `STATE.md`. It names the current milestone, the next task, and any blockers.
2. Read the relevant milestone section in `ROADMAP.md` for the task list and acceptance criterion.
3. Do the next unchecked task.
4. Tick the box in `ROADMAP.md`, rewrite `STATE.md`, commit.

Never re-derive the plan from scratch. Never skip a milestone's acceptance criterion.

## Design principles (from spec §3 — non-negotiable, enforced in review)

**P1 — No half-configured state, ever.** Every optional capability is either *verified working*
or *explicitly off*. There is no third state. A capability that was configured but now fails must
be demoted to off with a clear message, not retried silently in a loop.

**P2 — Setup gates on verification, not on user assertion.** Never accept "yes I set up the API
key" as proof. Run a real probe against the real endpoint and show the result. If the probe fails,
do not advance; offer: retry, get help, or disable this capability and continue.

**P3 — Every optional capability is skippable at any time.** The core pipeline
(local PDFs → markdown summaries → Q&A) must work with *every* optional capability disabled.
That path is the one that has to be bulletproof.

**P4 — Degrade loudly, never silently.** If figure extraction produced nothing for a paper, the
paper's note says so. If Scholar enrichment was skipped, the synthesis says which papers lack it.
Silent gaps in a literature review are a correctness hazard.

**P5 — Markdown is the source of truth.** Zotero write-back and vector indexes are *projections*
of the markdown. Both must be fully rebuildable from markdown.

**P6 — Idempotent and resumable.** Every stage checkpoints per-item. Re-running skips completed
work unless `--force`. A crash at paper 187 resumes at 187.

**P7 — Never fabricate a citation.** Every claim in generated output traces to a specific paper key
plus a locator (section or page). If the model can't locate support, it writes `[UNVERIFIED]`
rather than inventing. This is the single most important correctness rule in the project.

## Hard safety rules

- **Never write to `zotero.sqlite`.** Never open the live file. Always copy to a temp file and
  open read-only with `file:...?mode=ro&immutable=1`. Writing can corrupt a user's library.
- **Write-back only via Zotero Web API v3**, and during development only against a scratch
  library — never the user's real one.
- **Never commit PDFs from a user's library.** They are third-party copyrighted works.
  `.gitignore` blocks them; verify with `git status` before committing.
- **Never store an API key in `.lit/` or in the repo.** OS keychain if available, else a `0600` file.
- **Never fabricate a citation or a locator.** See P7.

## Conventions

- **Paths:** `${CLAUDE_PLUGIN_ROOT}` for every intra-plugin path. No absolute paths, ever.
- **Python:** one `requirements.txt`, installed into a plugin-managed venv at `~/.lit-agent/venv`.
  Core deps must be pure-pip and cross-platform. Anything needing a JVM, system package, or GPU is
  an *optional extra* behind a capability flag.
  Interpreter policy: see `docs/spikes/FINDINGS.md` (S0). Resolved default is recorded there.
- **Capability gating:** every skill begins by reading `~/.lit-agent/capabilities.json` via
  `scripts/lib/capabilities.py`. If a required capability is not `enabled`, print what's missing
  and the one command that fixes it, then exit. Do not attempt the work.
- **Errors:** never fail a whole run for one bad item. Collect per-item errors into a report.
- **Encoding:** all file I/O is UTF-8 explicit. This project is developed on Windows; do not rely
  on the platform default.
- **Cross-platform:** no shell-outs to Unix-only tools. `pathlib` everywhere.

## Commit convention

One logical change per commit. Message format:

```
M<n>: <what changed>
```

e.g. `M2: sqlite adapter resolves stored and linked attachments`.
Spikes use `M0: S<n> <what>`. Non-milestone chores use `chore: <what>`.

## Testing

- `pytest tests/` must pass before any milestone is marked complete.
- The gold set lives outside git at `tests/fixtures/goldset/` (gitignored); only its manifest
  (`tests/fixtures/goldset-manifest.json` — citekeys + sha256) is tracked, so results are reproducible.
- The **zero-optional path** (every optional capability disabled) is tested on every milestone.
