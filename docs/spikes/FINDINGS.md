# M0 — Spike findings

Every number here was measured on this machine against the user's real Zotero library
(88 PDFs, 103 regular items) on 2026-08-20. Nothing below is an assumption.

Amendments to `lit-agent-project-plan.md` are recorded as ADRs in `docs/decisions/`.

---

## S0 — Dependency reality on Windows ✅

**Result: Python 3.14.2 (the default `python`) resolves everything. No 3.13 fallback needed.**

| Package | Version installed | Notes |
|---|---|---|
| pymupdf | 1.28.2 | cp314 wheel |
| pymupdf4llm | 1.28.2 | pulls `pymupdf_layout` (42.9 MB) + `onnxruntime` (14.4 MB) |
| httpx | 0.28.1 | |
| PyYAML | 6.0.3 | cp314 wheel |
| bibtexparser | 1.4.4 | |
| sqlite-vec | installed OK | optional, M9 — works on 3.14 |

**Consequence:** `CLAUDE.md` records `python` (3.14.2) as the venv interpreter.

**Caveat worth knowing:** `pymupdf4llm` is no longer a thin wrapper — it drags in
`pymupdf_layout` and `onnxruntime`, ~57 MB of wheels and an ONNX runtime, for a "core"
dependency. See S4, which demotes it out of the core path entirely.

---

## S1 — Zotero local HTTP API ❌ (disabled, non-blocking)

Probed all four endpoints:

| Endpoint | Result |
|---|---|
| `/api/users/0/items?limit=1` | **403 — `Local API is not enabled`** |
| `/api/users/0/collections?limit=1` | 403 — same |
| `/connector/ping` | **200 — `Zotero is running`** |
| `/better-bibtex/json-rpc` | **404 — `No endpoint found`** |

Three findings:

1. **Zotero 7 is running**, but the local API is switched off. Fix is one checkbox:
   *Zotero → Settings → Advanced → "Allow other applications on this computer to
   communicate with Zotero"*. Documented in `CONNECTORS.md`.
2. **`/connector/ping` is a free, always-on liveness probe.** It lets `doctor.py`
   distinguish *"Zotero is not running"* from *"Zotero is running but the local API is
   off"* — two different remediation messages instead of one vague failure. This directly
   serves P2/P4 and is now part of the `source` probe.
3. **Better BibTeX is not installed.** The spec prefers BBT citekeys; they are unavailable
   here, so the generated `authorYEARfirstword` scheme is the *primary* path in this
   environment, not the fallback. It must therefore be good: stable, collision-handled,
   and its mapping recorded.

**Non-blocking** — adapter 8b (sqlite) covers all development needs. See S2.

---

## S2 — Zotero SQLite schema ✅

Copied to temp, opened `mode=ro&immutable=1`. **Verified the original file's mtime and size
were byte-identical before and after.** The full query set validated against the live DB.

**Schema versions:** `userdata 125`, `globalSchema 42`, `system 32`, `triggers 18`.
All 16 required tables present.

**Library contents:**

| | count |
|---|---|
| regular items | 103 |
| attachments | 118 |
| notes | **128** |
| **annotations (`itemAnnotations`)** | **0** |
| tags | 206 |
| collections | 25 |

Item types: journalArticle 49, conferencePaper 27, preprint 14, webpage 10, bookSection 2,
document 1.

### Finding S2-a — there are no annotations, but there are 126 child notes

`itemAnnotations` is **empty**. The user's reading trace lives in `itemNotes` instead:
126 child notes + 2 standalone. Sampling them shows they are **extracted quoted passages**
from the papers, not free-form commentary.

This matters for spec §6 body section 9 ("Your notes"). Two consequences:
- The annotation path must degrade loudly (P4) rather than silently producing an empty
  section: `No Zotero annotations found for this paper`.
- The **notes** path carries the weight and must preserve HTML→text faithfully and verbatim.
- A library with zero annotations is clearly a normal state, not an error. Do not warn about it.

### Finding S2-b — attachment path resolution, verified end to end

`itemAttachments.path` is `storage:<filename>`; the file lives at
`~/Zotero/storage/<attachment item key>/<filename>` — note the key is the **attachment's**
own key, not the parent's. Resolved 3 samples, all `exists=True`.

`linkMode` distribution: `1` (imported_url) ×114, `3` (linked_url) ×1, `4` (embedded_image) ×3.
Adapter must still handle `0` (imported_file) and `2` (linked_file, absolute path stored)
even though this library has none.

### Finding S2-c — the `date` field is two values in one column

Stored as `"<sql-date> <original string>"`, space-separated, with `00` for unknown parts:

```
'2024-08-25 2024-08-25'      '2025-04-00 2025-04'
'2011-12-05 December 5, 2011'  '2012-08-00 08/2012'
```

Year extraction rule: **take the first 4 characters.** Naive whole-field parsing produces
`"2024-08-25 2024-08-25"` as a "year", which would poison citekeys and BibTeX.

### Finding S2-d — real-world metadata mess

One title is `"(PDF) Research Trends, Challenges, and Emerging Topics in Digital Forensics…"`.
Titles need cleanup (leading `(PDF)`, trailing site names) before citekey generation.

---

## S3 — Figure extraction reality check ✅

Ran Tier-2 detection over 20 papers spread across the library (330 pages, 202 figure
captions, 97 table captions).

```
Tier-2 caption coverage           167/202 = 83%
Tier-2 MISS rate (vector figures)  35/202 = 17%
Papers with >=1 figure caption     18/20
Papers where Tier 2 got >=1        16/18  = 89%   (M6 acceptance bar is 80%)
Table captions 97  vs  find_tables() detections 72
```

**Aggregate coverage clears the M6 bar, but the distribution is bimodal** — the misses are
concentrated, not spread:

| Paper | fig captions | Tier-2 hits | misses |
|---|---|---|---|
| Xie et al. — *Evaluating Privacy Policies…* | 5 | **0** | 5 |
| Harkous et al. — *Polisis* | 12 | **1** | 11 |
| Hosseini et al. — *Lexical Similarity…* | 10 | 4 | 6 |
| Salamh et al. 2021 | 68 | 68 | 0 |
| Keim et al. 2022 | 22 | 18 | 4 |

Screenshot-heavy forensics papers are near-perfect; papers whose figures are vector plots
drawn with PDF operators are near-total misses.

**Decision:** Tier 3 (region rasterization) **is worth building** — it is the only thing that
rescues Xie (0/5) and Harkous (1/12). `pdffigures2` (JVM) is **not** justified: Tier 2 + Tier 3
already reach the acceptance bar without a Java dependency. It stays an opt-in extra, as spec §11
allows. → **ADR-0002**

---

## S4 — Cost and throughput ✅ (produced the biggest amendment)

**Corpus profile (all 88 PDFs):**

| | value |
|---|---|
| pages | min 3 · median 16 · mean 19.8 · max 85 |
| chars | median 66,380 · mean 78,625 · max 313,354 |
| est. input tokens (chars/4) | median ~16.6k · mean ~19.7k · max ~78k |
| **whole-corpus input** | **~1.73M tokens** |
| scanned / near-empty PDFs | **0** |

A full 88-paper analyze pass is roughly 1.7M input tokens — large but not prohibitive.
At 300 papers it would be ~6M, which is where the triage pass earns its place.

**No scanned PDFs in this library.** The OCR branch still gets built (other libraries will
have them) but it cannot be tested here; that limitation is recorded rather than papered over.

### Finding S4-a — `pymupdf4llm` costs 85× more time for 3% more text

Same 12-page paper, same machine:

| Method | time | per page | chars |
|---|---|---|---|
| `pymupdf4llm.to_markdown(page_chunks=True)` | 39.9 s | 3.32 s | 26,369 |
| … with `use_layout=False` | 40.5 s | 3.38 s | 26,369 |
| … with `ignore_images/ignore_graphics` | 40.5 s | 3.38 s | 26,369 |
| **`pymupdf` `page.get_text()` per page** | **0.5 s** | **0.04 s** | 25,563 |

The tuning kwargs do nothing — `to_markdown`'s signature is `(*args, **kwargs)`, so unknown
options are silently swallowed rather than rejected.

Extrapolated to this library (~1,740 pages): **~100 minutes** with `pymupdf4llm` versus
**~70 seconds** with raw extraction. On a 300-paper library that is a ~6-hour text-extraction
stage before a single token is spent on analysis.

**Decision:** raw `pymupdf` extraction with explicit page markers becomes the **default**;
`pymupdf4llm` is demoted to an opt-in `--layout` mode. Page-level locators — the primitive
P7 actually depends on — are fully preserved by the fast path. Section locators come from
cheap font-size heading detection via `get_text("dict")`. → **ADR-0001**

---

## S5 — Browser control and enrichment ⚠️ (Scholar Labs cut, as the spec anticipated)

**Browser round trip: works.** Navigated to `scholar.google.com` via the Chrome extension
integration and read the title back (`Google Scholar`). The `navigate / read_page` surface
the spec asks for is real and available.

**Scholar Labs: not reachable.** `https://scholar.google.com/labs` returns
**404 — "The requested URL /labs was not found on this server"**, and the Scholar homepage
exposes no Labs entry point.

Spec §10 pre-committed the response to this outcome: *"If this spike fails, cut Scholar Labs
to a stretch goal and ship Semantic Scholar instead."* Doing exactly that. → **ADR-0003**

**Fallback APIs — all verified working:**

| Service | Result |
|---|---|
| Semantic Scholar `/paper/DOI:…` | 200 — title, year, citations, **TLDR** all returned |
| Semantic Scholar `/paper/search` | **429 Too Many Requests** — unauthenticated search is rate-limited |
| OpenAlex `/works/doi:…` | 200 — title, year, cited_by_count |
| Crossref `/works/…` | 200 — title, type, container-title (needed by adapter 8d) |
| arXiv API `/api/query` | 200 — `<entry>` present |
| arXiv e-print tarball | 200 — 1,120,231 bytes, `application/gzip` (**M6 Tier 1 viable**) |

The 429 on the very first unauthenticated search call is a real constraint, not a fluke:
M8 needs exponential backoff, aggressive caching, and an optional API-key config field.
The per-DOI lookup path — which is what enrichment actually needs — was not rate-limited.

---

## Summary of amendments

| ADR | Change | Driver |
|---|---|---|
| [0001](../decisions/0001-fast-text-extraction-by-default.md) | Raw `pymupdf` extraction by default; `pymupdf4llm` behind `--layout` | S4-a: 85× slower for 3% more text |
| [0002](../decisions/0002-figure-tiers-without-jvm.md) | Build Tier 3; keep `pdffigures2` opt-in | S3: 89% paper coverage without a JVM |
| [0003](../decisions/0003-semantic-scholar-before-scholar-labs.md) | Semantic Scholar ships as the enrichment path; Scholar Labs is a stretch goal | S5: `/labs` 404s |

## Open items carried forward

- **User action available (optional):** enabling Zotero's local API unlocks adapter 8a.
  Everything works without it.
- **Untestable in this environment:** scanned-PDF OCR (no scanned PDFs), Better BibTeX
  citekeys (not installed), Zotero write-back (needs a scratch library — M7).
- Semantic Scholar search needs backoff + optional API key before M8.
