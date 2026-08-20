# ADR-0003 — Semantic Scholar is the enrichment path; Scholar Labs is a stretch goal

Date: 2026-08-20 · Status: accepted · Affects: spec section 10, milestone M8

## Context

Spec section 10 pre-committed the decision rule: "Spike this before building on it. If this
spike fails, cut Scholar Labs to a stretch goal and ship Semantic Scholar instead."

S5 result:

- Browser control **works** — navigated to `scholar.google.com` through the Chrome extension
  integration and read the page title back. The `navigate / read_page` surface is real.
- Scholar Labs **is not reachable**: `https://scholar.google.com/labs` returns
  `404 - The requested URL /labs was not found on this server`, and the Scholar homepage
  exposes no Labs entry point for this user.

The spike failed on its own stated criterion.

## Decision

- **M8 ships `enrich_semantic_scholar` (plus OpenAlex) first.** Verified working: per-DOI
  lookup returns title, year, citation count, and a TLDR. No scraping, no ToS question, no
  DOM fragility.
- **Scholar Labs is demoted to a stretch goal**, still off by default, still requiring
  explicit opt-in and the ToS statement if it is ever revisited.
- The browser capability itself stays in the capability table — it probes clean, and other
  features may use it.

## Consequences

- The riskiest, most fragile part of the spec is no longer on the critical path.
- **New constraint discovered:** Semantic Scholar's `/paper/search` endpoint returned
  **429 Too Many Requests** on the first unauthenticated call. M8 must implement exponential
  backoff, cache aggressively, and expose an optional API-key config field. The per-DOI
  lookup path that enrichment actually relies on was not rate-limited.
- Also verified for other milestones: Crossref (adapter 8d metadata resolution) and the
  arXiv e-print tarball endpoint (M6 Tier 1, 1.1 MB gzip for a test paper) both return 200.
