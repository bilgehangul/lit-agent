# ADR-0002 — Build Tier 3 rasterization; keep pdffigures2 opt-in

Date: 2026-08-20 · Status: accepted · Affects: spec section 11, milestone M6

## Context

Spec section 11 defines three extraction tiers and offers `pdffigures2` (needs a JVM) as an
optional heavy path. S3 asked the question the spec wanted answered: how many figures does
Tier 2 actually miss?

Measured over 20 papers from the user's library (330 pages, 202 figure captions):

```
Tier-2 caption coverage           167/202 = 83%
Tier-2 MISS rate                   35/202 = 17%
Papers where Tier 2 got >=1 fig    16/18  = 89%   (M6 bar is 80%)
```

The aggregate clears the bar, but the misses are **concentrated, not spread**. Papers whose
figures are vector plots drawn with PDF operators fail almost completely — Xie et al. 0/5,
Harkous *Polisis* 1/12, Hosseini 4/10 — while screenshot-heavy forensics papers are near
perfect (Salamh 68/68).

## Decision

- **Build Tier 3** (region rasterization around an orphaned caption). It is the only thing
  that recovers the vector-figure papers, and those are a recognizable, recurring class,
  not a long tail.
- **Do not make `pdffigures2` a dependency.** Tier 2 + Tier 3 reach the M6 acceptance bar
  with zero JVM. It stays an opt-in extra behind a capability flag, exactly as spec
  section 11 permits.
- Tables: `find_tables()` found 72 detections against 97 table captions. Table extraction is
  useful but demonstrably incomplete, so per P4 the methods matrix must mark which table
  rows came from extraction and which are absent.

## Consequences

- No Java in the dependency chain for the acceptance-passing path.
- Every figure record carries `extraction_method` (`arxiv_source` / `embedded_image` /
  `page_region`) so a consumer knows a Tier-3 asset is a crude crop, not a real figure file.
- Papers that yield nothing say so explicitly (P4), rather than rendering an empty section.
