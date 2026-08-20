# STATE

Rewritten after every task. Read this first (see `CLAUDE.md` startup protocol).

```
Current:     M0 — Spikes
Next task:   S0 — dependency reality on Windows (venv + core wheels)
Last commit: (none — initial scaffold)
Blocked:     none
Needs user:  Zotero local HTTP API returns 403 on 127.0.0.1:23119.
             Likely fix: Zotero > Settings > Advanced > "Allow other applications on this
             computer to communicate with Zotero". Not blocking — the sqlite adapter (8b)
             covers development.
Gate status: M0 >  M1 .  M2 .  M3 .  M4 .  M5 .
```

## Decisions locked for this build

- Harness: lightweight in-repo (`CLAUDE.md` + `ROADMAP.md` + `STATE.md`). No GSD dependency.
- Autonomy: build straight through M0 to M5; stop only on a hard-gate failure.
- Test corpus: the user's real Zotero library at `%USERPROFILE%\Zotero`, **strictly read-only**.
  88 PDFs across 117 storage folders. Gold set = 20 papers, manifest tracked, PDFs never committed.
- Dev scope fixture: `tests/fixtures/scope.dev.yaml`, derived from the library's densest cluster
  (LLM-based privacy policy analysis). A stand-in for the real `/lit-scope` interview, marked as such.
- Git: local repo, remote `https://github.com/bilgehangul/lit-agent.git` (existed, empty).
- M6 to M10 are queued but out of scope for this run.

## Stopping conditions

Stop and report rather than working around it if:
- the M3 locator gate falls below 90%;
- no Python interpreter resolves the core dependencies;
- the Zotero SQLite schema matches no known version;
- S4 shows per-paper cost that makes the gold-set run unreasonable.
