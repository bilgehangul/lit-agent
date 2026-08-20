---
name: lit-doctor
description: Re-run every lit-agent capability probe, print a status table, and repair or disable anything broken. Use when the user says "/lit-doctor", "check lit-agent", "why isn't lit-agent working", or when any lit-agent command fails in a way that looks like a capability problem. This is the first thing to try on a confused setup.
---

# lit-doctor

Universal first aid. Re-probes everything, reports honestly, and offers to fix or turn off
whatever is broken.

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere.

## Run it

```
PY "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" --apply
```

`--apply` folds the results back into `capabilities.json`, which is what demotes a
capability that used to work and no longer does. Add `--only <id> ...` to check specific
capabilities, `--json` when you need to branch on the result.

If the venv interpreter does not exist, the environment was never built: tell the user to
run `/lit-setup` and stop.

## Reading the output

| Marker | Meaning | What to do |
|---|---|---|
| `PASS` | probe did real work and it worked | nothing |
| `FAIL` | configured, or required, but not working | offer repair |
| `n/a` | not installed or not present on this machine | fine if the user does not want it |
| `BROKEN` in the table | was enabled, now failing | this is the one to lead with |

## Reporting

State plainly, in this order:

1. **Required capabilities** - are all three (`python_env`, `source`, `pdf_text`) healthy?
   If any is not, nothing else matters; lead with it and give the fix.
2. **Anything broken** - a capability that was working and now is not. Quote the probe's
   error verbatim rather than paraphrasing; the specifics are the useful part.
3. **What is off by choice** - mention it in one line. Do not nag about optional
   capabilities the user declined (P3).

Then offer, for each broken or failed capability: **repair** (`/lit-setup --reconfigure <id>`),
**turn it off** and continue without it, or **leave it and decide later**.

Turning something off is a legitimate outcome, not a failure - say so, so users do not feel
pushed into configuring things they do not need.

## Common diagnoses

- **"Zotero is not running"** vs **"local API is off"** are different problems. The first
  needs Zotero started; the second needs the checkbox at Zotero > Settings > Advanced >
  "Allow other applications on this computer to communicate with Zotero". Either way the
  `zotero_sqlite` adapter works without any of it.
- **`source` fails after working before** - the library moved, or an export directory was
  deleted. Re-run `/lit-setup --reconfigure source`.
- **`semantic_scholar` returns HTTP 429** - rate limited, not broken. Retry later or add an
  API key.
- **`pdf_text` fails** - the venv is damaged. Run
  `python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_env.py" --recreate`.
