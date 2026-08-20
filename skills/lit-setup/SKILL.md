---
name: lit-setup
description: Set up lit-agent - build the Python environment, connect a reference library, verify each optional capability with a real probe, and run the research scope interview. Use when the user says "set up lit-agent", "/lit-setup", "connect my Zotero library", "reconfigure <capability>", or when another lit-agent command reports a capability is not enabled.
---

# lit-setup

Interactive capability wizard and scope interview. Run the phases **in order** — required
first, so the user reaches a working system before being asked about extras.

## The rules this skill exists to enforce

- **P2 — verification, not assertion.** Never mark a capability enabled because the user
  says it is configured. Run the probe. Show the user the result. If you did not see a
  probe pass, the capability is off.
- **P1 — no half-configured state.** Every capability ends this wizard either verified
  working or explicitly off. If a probe fails and the user does not want to fix it now,
  set it to `disabled` and move on cleanly.
- **P3 — everything optional is skippable.** Declining is a normal answer, not a problem
  to solve. The core pipeline works with every optional capability off.

## Setup

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere. Let `ROOT` be `${CLAUDE_PLUGIN_ROOT}`.

Handle `--reconfigure <capability>` by jumping straight to that capability's step in Phase C
(or Phase B for `source`), then re-printing the Phase E summary. Do not re-run the whole wizard.

---

## Phase A — Required: Python environment

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_env.py" --json
```

Use any Python 3.10+ for this one command; it builds the venv. Then:

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --only python_env pdf_text --json --apply
```

- Both pass → say so with the versions, continue.
- Either fails → **stop here.** Print the specific error from the probe and the remediation
  it suggests. Do not advance to Phase B; there is nothing useful to configure without a
  working extractor.

## Phase B — Required: library source

Auto-detect, and **tell the user what was found**:

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --detect --json
```

Detection runs in priority order: Zotero local HTTP API → `zotero.sqlite` → export
directory → plain PDF folder. Present the findings as a short list showing which are
available and why the others are not, then let the user confirm the top choice or override it.

Two Zotero-specific messages you will see, which mean different things:

| Detection says | What it means | What to tell the user |
|---|---|---|
| "Zotero is not running" | nothing listening on port 23119 | Start Zotero, or use the sqlite path |
| "local API is off" | Zotero is up, API disabled | Enable it in Zotero → Settings → Advanced → "Allow other applications on this computer to communicate with Zotero" — or just use the sqlite path, which needs no setup |

Write the chosen adapter into the capability config and probe it:

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --only source --json --apply
```

**Show the user the actual titles the probe retrieved.** That is the proof the connection
works — not a green checkmark. The probe returns them in `config.sample_titles`.

The sqlite adapter never opens the live database; it copies it and opens the copy read-only.
Say this out loud when the user picks it — people are rightly nervous about tools touching
their library.

If the source probe fails, offer: **retry / help / choose a different adapter.** A required
capability cannot be skipped, so do not offer to continue without one.

## Phase C — Optional capabilities

For each capability below, in this order: state in **one sentence** what it enables and what
it costs, then ask. If yes → collect config → **run the probe** → show the result.
On failure offer **retry / help / skip**. Skipping sets `disabled` and moves on.

Run each probe with:

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --only <id> --json --apply
```

| Order | id | Ask about it like this |
|---|---|---|
| 1 | `figures` | Pull figures and tables out of papers with their captions. No setup, no extra install. |
| 2 | `semantic_scholar` | Add TLDRs and citation counts from a public API. No scraping, no key needed — an optional key raises the rate limit. |
| 3 | `arxiv_source` | For arXiv papers, fetch the original figure files and exact LaTeX captions. Makes network calls to arxiv.org. |
| 4 | `vector_index` | Semantic search for large corpora. Only worth it past roughly 150 papers; needs one extra pip package. |
| 5 | `layout_text` | Layout-aware markdown extraction for messy multi-column PDFs. ~57 MB extra and about 85× slower than the default — say this plainly; most users should decline. |
| 6 | `zotero_write` | Write notes back into Zotero. Needs a synced library and a Web API key. **See the safety note below.** |
| 7 | `browser` | Drive your signed-in Chrome session. **See the browser note below.** |
| 8 | `grobid` | Structured section/reference parsing for badly laid-out PDFs. Needs a GROBID server via Docker. |

`vector_index` and `layout_text` need their package installed first:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_env.py" --extra vector_index
```

### Browser capability — you run this probe, not Python

`doctor.py` cannot drive a browser, and it deliberately refuses to report success on its own.
Do the round trip yourself with the browser tools available in this environment: open a tab,
navigate to a simple page, read the title back, close the tab. Only if you actually saw the
title come back, record it:

```
PY -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts'); \
from lib.capabilities import Capabilities; c=Capabilities.load(); \
c.enable('browser', {'verified_by_skill': True, 'evidence': '<what you saw>'}); c.save()"
```

If the round trip fails, leave it disabled. Do not record success you did not observe.

### Google Scholar Labs — off by default, and stays off unless asked

Do **not** offer this proactively. It was unreachable when last probed (404 on `/labs`,
see `docs/decisions/0003`), and automated access to Google Scholar sits outside its terms
of service. If the user asks for it specifically: state that plainly, state that continued
use is their call, require an explicit yes, and only then attempt it. Semantic Scholar
enrichment is the supported path.

### Zotero write-back — never probe against a real library

The probe creates a note, reads it back, and deletes it. That is a real write. Before
running it, the user must designate a **scratch item** — ideally in a scratch library, not
their working one. Collect:

- numeric user ID and an API key with write permission from
  `https://www.zotero.org/settings/keys`
- the item key to use for the probe

Store the key in the OS keychain if available, else a `0600` file. **Never** write it into
`.lit/`, `config.yaml`, or anything inside a repository.

## Phase D — Research scope interview

Runs after capabilities so it can adapt: do not offer artifacts that disabled capabilities
cannot produce. Invoke the `lit-scope` skill to conduct it, then come back here for Phase E.

## Phase E — Summary

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"
```

Print the capability table, then state explicitly:

1. **Which commands are available now** — the ones whose required capabilities are enabled.
2. **Which commands are locked**, which capability each is waiting on, and the single
   command that unlocks it: `/lit-setup --reconfigure <capability>`.
3. Anything that was demoted or skipped, and why. Do not quietly omit a failure.

Close by telling the user that `/lit-doctor` re-checks everything at any time, and that a
good first move is `/lit-ingest` followed by `/lit-analyze`.
