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
- **Status on this machine: DISABLED.** `/api/users/0/items` returns
  `403 Local API is not enabled`, while `/connector/ping` returns `200 Zotero is running`.
  Enable the checkbox above to unlock this adapter. Not required — the sqlite path works today.
- **Tip for probes:** `/connector/ping` needs no permission and tells you whether Zotero is
  running at all, so a probe can say *"Zotero is not running"* vs *"local API is off"*.

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
- **Status on this machine: NOT INSTALLED** (`404 No endpoint found`), so the generated
  citekey scheme is the primary path here, not a fallback.

## Browser control (optional, for Scholar Labs)

- Whatever is available in the user's Claude Code environment: the Chrome extension
  integration, a chrome-devtools MCP server, or a Playwright MCP server.
- Abstracted behind a `navigate / read_page / click / type` surface so the backend swaps out.

## Semantic Scholar / OpenAlex / Crossref / arXiv (optional enrichment + metadata)

All verified working in M0/S5:

| Service | Endpoint | Verified |
|---|---|---|
| Semantic Scholar | `api.semanticscholar.org/graph/v1/paper/DOI:~~DOI~~` | 200 — returns TLDR |
| Semantic Scholar search | `.../paper/search` | **429 unauthenticated** — needs backoff + optional key |
| OpenAlex | `api.openalex.org/works/doi:~~DOI~~?mailto=~~EMAIL~~` | 200 |
| Crossref | `api.crossref.org/works/~~DOI~~` | 200 |
| arXiv metadata | `export.arxiv.org/api/query?id_list=~~ID~~` | 200 |
| arXiv e-print | `arxiv.org/e-print/~~ID~~` | 200 — gzip tarball (M6 Tier 1) |

Send a descriptive `User-Agent` with a contact address on every call. An optional Semantic
Scholar API key raises the search rate limit; request one at
`https://www.semanticscholar.org/product/api#api-key`.

## Google Scholar Labs (stretch goal, off by default)

`https://scholar.google.com/labs` returned **404** for this user, so Scholar Labs is demoted
per ADR-0003. Automated access to Google Scholar sits outside its terms of service; if this is
ever revisited it stays off by default, opt-in only, human-paced, and hard-capped in code.
