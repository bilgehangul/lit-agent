# lit-agent — roadmap

Work queue. Task lists derive from `lit-agent-project-plan.md`; the acceptance criterion for each
milestone is quoted from spec §15 and is binding. Tick boxes as tasks land. `STATE.md` points at
the current position.

Legend: `[ ]` todo · `[x]` done · **HARD GATE** = do not proceed past this on a failure.

---

## M0 — Spikes

Time-boxed. Resolve unknowns before committing to architecture. Output: `docs/spikes/FINDINGS.md`,
which may amend the spec. Each amendment gets an ADR in `docs/decisions/`.

- [x] S0 — Dependency reality on Windows. Resolve `pymupdf`, `pymupdf4llm`, `httpx`, `pyyaml`,
      `bibtexparser`, `sqlite-vec` on Python 3.14 first; fall back to 3.13 on any wheel miss.
      **Decides the interpreter recorded in CLAUDE.md.**
- [x] S1 — Zotero local HTTP API. Confirm whether the 403 on `127.0.0.1:23119` is the
      disabled-API case. Document the exact toggle in `CONNECTORS.md`. Check Better BibTeX
      JSON-RPC at `/better-bibtex/json-rpc`. *Non-blocking* — S2 covers dev needs.
- [x] S2 — SQLite schema. Copy-to-temp, read-only. Validate queries for items, attachments
      (stored + linked), `itemAnnotations`, tags, collections against the live DB. Record schema
      version. Query set lands in `references/zotero-internals.md`.
- [x] S3 — Figure extraction reality check. All three tiers on 20 real papers. **Measure the
      Tier-2 vector-figure miss rate** — decides whether Tier 3 or `pdffigures2` is worth M6.
- [x] S4 — Token cost per paper. Measure a ~12-page and a ~40-page paper end to end. If a large
      corpus is prohibitive, design the triage pass **into** M3.
- [x] S5 — Browser control surface. Confirm a real navigate + read-title round trip. Determine
      Scholar Labs access state: signed-out vs. waitlisted vs. working. A failed spike cuts
      Scholar Labs to a stretch goal and promotes Semantic Scholar (spec §10).
- [x] Write `docs/spikes/FINDINGS.md`.

*Acceptance:* findings doc exists and every downstream architectural unknown above is answered
with evidence, not assumption.

---

## M1 — Skeleton + setup

- [ ] `capabilities.json` state machine: `status` (`enabled`|`disabled`|`broken`),
      `last_verified`, `config`, `last_error`. Helper at `scripts/lib/capabilities.py`.
- [ ] `scripts/doctor.py` — one probe per capability in spec §5 table.
- [ ] Runtime gating helper every skill calls first (spec §5 "Runtime gating").
      Three consecutive failures during a run means mark `broken`, finish without it, report demotion.
- [ ] `/lit-setup` phases A–E (spec §5).
- [ ] `/lit-doctor` — re-probe, status table, offer repair or disable.
- [ ] `/lit-scope` — re-runnable scope interview (spec §7), writes `.lit/config.yaml` + `scope.md`,
      stamps a scope version.
- [ ] `references/output-schemas.md` — per-paper note schema, `figures.json`, `state.json`.

*Acceptance:* a fresh user on macOS, Linux, and Windows can run setup, decline every optional
capability, and land in a valid enabled state.

---

## M2 — Ingest + text

- [ ] `LibraryItem` common interface (spec §8).
- [ ] Adapter 8a — Zotero local HTTP API (read-only; BBT citekeys if present).
- [ ] Adapter 8b — `zotero.sqlite` direct read (copy-to-temp, read-only, never write).
- [ ] Adapter 8c — export directory (CSV / BibTeX / CSL-JSON / RDF + `files/`).
- [ ] Adapter 8d — generic PDF folder (DOI/arXiv extraction then Crossref/arXiv/OpenAlex,
      ask before network calls).
- [ ] Citekey resolution: prefer Better BibTeX, else `authorYEARfirstword`, record the mapping.
- [ ] Dedupe: DOI, then arXiv ID, then normalized title+year.
- [ ] Per-item checkpointing in `.lit/state.json`; `--force` to redo.
- [ ] Stage 2 text extraction with raw `pymupdf` `page.get_text()`, **preserving page markers**
      (locators depend on them — do not strip). Heading detection from font size via
      `get_text("dict")` for section locators. `pymupdf4llm` behind an opt-in `--layout` flag.
      Amended by ADR-0001 (it is 85x slower for 3% more text).
- [ ] Scanned-PDF detection (near-zero text layer) then OCR via `ocrmypdf` if available, else flag
      unprocessable.
- [ ] Per-item error report; one bad item never fails the run.

*Acceptance:* a 50-item library ingests fully; killing the process mid-run and re-running resumes
correctly and produces identical output.

---

## M3 — Analyze — **HARD GATE**

- [ ] Per-paper note writer: full YAML frontmatter + the 12 body sections in fixed order (spec §6).
- [ ] Scope conditioning: every prompt injects the scope block; every note stamped with the
      scope version that produced it.
- [ ] Undeterminable fields written as `Not determinable from the text` — never guessed, never
      silently omitted (P4).
- [ ] Map-reduce for long papers (per-section pass, then synthesis) — never truncation.
- [ ] Parallel batches with a concurrency cap.
- [ ] Locator self-check: resolve every `[p. N]` / `[§x.y]` against extracted text, then an
      independent verifier judges whether the cited span supports the claim. Downgrade
      `confidence` and mark `[UNVERIFIED]` where it does not.
- [ ] Triage pass (cheap relevance screen, full analysis on high-relevance only) if S4 says cost
      demands it.
- [ ] User annotations preserved verbatim in section 9; analyzer commentary in a marked sub-block.

*Acceptance (**HARD GATE**):* on a 20-paper gold set, spot-check 40 locators; **at least 90% resolve to
supporting text and zero fabricated citations.** Report at `docs/spikes/m3-locator-check.md` with
per-row pass/fail. Below 90% means stop, report, do not start M4.

---

## M4 — Q&A

- [ ] Grep/glob retrieval: frontmatter-field filter, keyword search, read full notes for hits.
- [ ] `/lit-ask` answers with citations back to citekey + locator.
- [ ] Refuse to answer from model priors when the corpus lacks support; offer to record the
      question as a gap.

*Acceptance:* 15 test questions answered with correct citations over the gold set.

---

## M5 — Synthesis

- [ ] `/lit-review` reads all per-paper notes + scope block.
- [ ] `index.md` — master table: citekey, title, year, venue, type, methods, relevance, one-liner.
- [ ] `themes.md` — clusters, an argued through-line per cluster, and papers that resist clustering.
- [ ] `methods-matrix.md` — papers by (method, dataset, metric, headline result, code available).
- [ ] `gaps.md` — **must distinguish "no one has done this" from "not in this library."**
- [ ] `contradictions.md` — **every entry needs two verified locators or it does not ship.**
- [ ] `review-draft.md` — prose in the user's stated voice, every claim carrying
      `[@citekey, p. N]`, `[UNVERIFIED]` where support could not be located.
- [ ] `refs.bib` — BibTeX for exactly the cited subset, same citekeys.
- [ ] `--filter` on frontmatter fields (`relevance`, `year`, `paper_type`, `scope_tags`).

*Acceptance:* draft related-work section reviewed by the user against the actual papers;
contradiction entries all verify.

---

## M6 — Figures

- [ ] Tier 1 — arXiv e-print source (original figure files + LaTeX captions/labels).
- [ ] Tier 2 — PyMuPDF embedded images, caption pairing by nearest `Figure N`/`Table N` block.
- [ ] Tier 3 — region rasterization for captions with no extractable image.
      **Confirmed worth building** by S3: rescues vector-figure papers (Xie 0/5, Harkous 1/12).
- [ ] Tables via PyMuPDF table finder into CSV + markdown; feed into the methods matrix.
- [ ] `extraction_method` on every figure record.
- [ ] Papers yielding nothing say `Figure extraction produced no assets for this paper` (P4).
- [ ] `pdffigures2` as an opt-in extra (JVM), never a default dependency.
      ADR-0002: not needed to pass acceptance — Tier 2+3 reach 89% paper coverage.

*Acceptance:* at least 1 correctly captioned figure from 80% of gold-set papers with figures;
misses reported explicitly.

---

## M7 — Zotero write-back — **HARD GATE (scratch library only)**

- [ ] Web API v3 client; key in OS keychain or `0600` file, never in `.lit/` or the repo.
- [ ] Probe: create note on a scratch item, read back, delete.
- [ ] Per-paper child notes (markdown to simple HTML) + standalone synthesis notes in a
      `lit-agent / <project>` collection.
- [ ] Tag every generated note `lit-agent` + project tag; machine-readable footer with citekey,
      timestamp, scope version, plugin version.
- [ ] Never modify or delete a user-authored note. Content-hash mismatch means a new versioned note.
- [ ] Batch up to 50 items; respect `Backoff` / `Retry-After`; handle 412 by re-fetching.
- [ ] `--dry-run` (default on first run) and `--undo`.

*Acceptance:* dry-run, sync, hand-edit-then-resync (no overwrite), and undo all behave correctly
on a **scratch Zotero library**, never the user's real one.

---

## M8 — Enrichment

- [ ] `enrich_semantic_scholar` first (free API, TLDRs, no scraping) + OpenAlex.
- [ ] Scholar Labs **demoted to stretch goal** by ADR-0003 (`/labs` returns 404 for this user).
      Semantic Scholar `/paper/search` returned 429 unauthenticated -> needs backoff, caching,
      and an optional API-key field. If ever revisited: query-driven (not per-paper), 3–5 questions
      per run, visible editable query plan, hard caps in code, human-paced, off by default,
      explicit opt-in with the ToS statement, selectors isolated in one config file.
- [ ] Papers with no enrichment say so in their notes (P4).
- [ ] Non-library results go to `synthesis/suggested-additions.md`; never auto-add.

## M9 — Vector index

- [ ] Chunk notes + full text, embed, store in `sqlite-vec`. Hybrid BM25 + vector, rerank.
- [ ] `/lit-index --rebuild` regenerates from markdown (P5). Staleness warning on note mtime.

## M10 — Distribution

- [ ] README: 3-minute quickstart on the zero-optional path, capability matrix, troubleshooting.
- [ ] Sample corpus of open-access papers.
- [ ] `claude plugin validate` clean.
- [ ] `CONNECTORS.md` complete; public repo release.
