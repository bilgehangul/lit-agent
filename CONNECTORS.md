# Connectors

Everything lit-agent talks to, what it needs, and how to verify it works.

Placeholder convention: values that vary per user are written as `~~LIKE_THIS~~`.

> Populated during M0 spikes. Each entry lands here once its probe has actually been run.

## Zotero — local HTTP API (preferred read path)

- **Endpoint:** `http://127.0.0.1:23119/api/users/0/`
- **Requires:** Zotero 7 running, with the local API enabled.
- **Enable it:** Zotero > Settings > Advanced > check
  *"Allow other applications on this computer to communicate with Zotero"*.
- **Read-only.** Cannot write. Write-back uses the Web API (below).
- **Verify:** `curl -s http://127.0.0.1:23119/api/users/0/items?limit=1`
- Status on this machine: **see `docs/spikes/FINDINGS.md` (S1)**.

## Zotero — zotero.sqlite (fallback read path)

- **Path:** Windows `~~%USERPROFILE%~~\Zotero\zotero.sqlite`;
  macOS/Linux `~/Zotero/zotero.sqlite`.
- **Never opened live and never written to.** Copied to a temp file, opened
  `file:...?mode=ro&immutable=1`.
- Query set and schema notes: `references/zotero-internals.md`.

## Zotero — Web API v3 (write-back, optional)

- **Needs:** a synced library, your numeric user ID, and an API key with write permission
  from `https://www.zotero.org/settings/keys`.
- Key is stored in the OS keychain if available, else a `0600` file. Never in `.lit/`,
  never in the repo.

## Better BibTeX (optional, for stable citekeys)

- **Endpoint:** `http://127.0.0.1:23119/better-bibtex/json-rpc`
- Used only to read citekeys. Absent means lit-agent generates `authorYEARfirstword`.

## Browser control (optional, for Scholar Labs)

- Whatever is available in the user's Claude Code environment: the Chrome extension
  integration, a chrome-devtools MCP server, or a Playwright MCP server.
- Abstracted behind a `navigate / read_page / click / type` surface so the backend swaps out.

## Semantic Scholar / OpenAlex (optional enrichment)

- Public APIs, no scraping, no key required for low volume.
