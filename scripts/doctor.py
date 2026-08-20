"""Capability probes (spec section 5) and the /lit-doctor entry point.

Every probe here does real work against the real thing. Nothing in this file promotes a
capability on the strength of configuration existing, a package being importable when the
feature needs a server, or a user saying it is set up (**P2**).

Each probe returns a ``ProbeResult``. A probe never raises: a failure is a result, because
callers need the reason to show the user (**P4**).

Usage::

    python scripts/doctor.py                # probe everything, print the table
    python scripts/doctor.py --json         # machine-readable, for skills
    python scripts/doctor.py --only pdf_text figures
    python scripts/doctor.py --apply        # write results back to capabilities.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import paths  # noqa: E402
from lib.capabilities import (  # noqa: E402
    BY_ID,
    REGISTRY,
    Capabilities,
    format_table,
)
from lib.sample_pdf import ensure_sample_pdf  # noqa: E402


@dataclass
class ProbeResult:
    ok: bool
    #: One line the user reads. On success it should be *evidence*, not "OK" -- the point
    #: of a probe is that the user sees proof (spec section 5, phase B).
    detail: str
    config: dict[str, Any] = field(default_factory=dict)
    #: Set when the capability cannot work here at all, as opposed to not being set up yet.
    unavailable: bool = False
    elapsed: float = 0.0


def _ok(detail: str, **config: Any) -> ProbeResult:
    return ProbeResult(True, detail, config)


def _fail(detail: str, unavailable: bool = False) -> ProbeResult:
    return ProbeResult(False, detail, unavailable=unavailable)


# ---------------------------------------------------------------- required


def probe_python_env(cfg: dict[str, Any]) -> ProbeResult:
    """Import the core libraries and report their versions."""
    try:
        import pymupdf
    except ImportError as exc:
        return _fail(f"pymupdf not importable: {exc}. Run /lit-setup to build the venv.")
    versions = {"python": sys.version.split()[0], "pymupdf": getattr(pymupdf, "__version__", "?")}
    for name in ("httpx", "yaml", "bibtexparser"):
        try:
            mod = __import__(name)
            versions[name] = getattr(mod, "__version__", "present")
        except ImportError:
            return _fail(f"core dependency '{name}' is missing. Run /lit-setup to repair the venv.")
    # A functional check, not just an import: open a document and read a page.
    try:
        doc = pymupdf.open()
        doc.new_page()
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"pymupdf imported but cannot create a document: {exc}")
    return _ok(
        f"python {versions['python']}, pymupdf {versions['pymupdf']}, "
        f"httpx {versions['httpx']}, yaml {versions['yaml']}",
        versions=versions,
        executable=sys.executable,
    )


def probe_pdf_text(cfg: dict[str, Any]) -> ProbeResult:
    """Extract text from the generated sample PDF and assert over 200 characters."""
    try:
        import pymupdf
    except ImportError as exc:
        return _fail(f"pymupdf not importable: {exc}")
    try:
        sample = ensure_sample_pdf(paths.CACHE_DIR)
        doc = pymupdf.open(sample)
        pages = [page.get_text() for page in doc]
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"extraction failed on the sample PDF: {type(exc).__name__}: {exc}")
    total = sum(len(p) for p in pages)
    if total <= 200:
        return _fail(f"extracted only {total} characters from the sample PDF; expected over 200")
    return _ok(f"extracted {total} characters across {len(pages)} page(s) of the sample PDF",
               sample=str(sample), chars=total)


def probe_source(cfg: dict[str, Any]) -> ProbeResult:
    """Pull items from the configured adapter and resolve their PDF paths.

    Auto-detects in the priority order of spec section 8b when nothing is configured yet.
    """
    adapter = cfg.get("adapter")
    if not adapter:
        detected = detect_sources()
        available = [d for d in detected if d["available"]]
        if not available:
            return _fail("no library source found. Point lit-agent at a Zotero library, an "
                         "export directory, or a folder of PDFs via /lit-setup.")
        return _fail(f"source not configured yet; detected: "
                     f"{', '.join(d['adapter'] for d in available)}. Choose one in /lit-setup.")

    if adapter == "zotero_sqlite":
        return _probe_zotero_sqlite(cfg.get("db_path"))
    if adapter == "zotero_api":
        return _probe_zotero_api(cfg.get("base_url"))
    if adapter in ("export_dir", "generic_pdf"):
        root = Path(cfg.get("path", ""))
        if not root.is_dir():
            return _fail(f"configured path does not exist: {root}")
        pdfs = sorted(root.rglob("*.pdf"))[:3]
        if not pdfs:
            return _fail(f"no PDFs found under {root}")
        return _ok(f"found {len(pdfs)} sample PDF(s) under {root}, e.g. {pdfs[0].name}",
                   adapter=adapter, path=str(root))
    return _fail(f"unknown adapter '{adapter}'")


def _probe_zotero_api(base_url: str | None) -> ProbeResult:
    """Distinguish 'Zotero is not running' from 'the local API is switched off'.

    ``/connector/ping`` needs no permission and answers whenever Zotero is up, so the two
    failures get two different remediations instead of one vague one (M0/S1).
    """
    base = (base_url or "http://127.0.0.1:23119").rstrip("/")
    try:
        import httpx
    except ImportError:
        return _fail("httpx is missing; run /lit-setup to repair the venv")
    try:
        with httpx.Client(timeout=5) as client:
            try:
                ping = client.get(f"{base}/connector/ping")
            except httpx.RequestError:
                return _fail("Zotero is not running (nothing is listening on "
                             f"{base}). Start Zotero and try again.", unavailable=True)
            resp = client.get(f"{base}/api/users/0/items", params={"limit": 3})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{type(exc).__name__}: {exc}")

    if resp.status_code == 403:
        return _fail(
            "Zotero is running but its local API is off. Enable it in "
            "Zotero > Settings > Advanced > 'Allow other applications on this computer "
            "to communicate with Zotero'.")
    if resp.status_code != 200:
        return _fail(f"local API returned HTTP {resp.status_code}: {resp.text[:100]}")
    try:
        items = resp.json()
    except ValueError:
        return _fail("local API responded but the body was not JSON")
    if not items:
        return _fail("local API works but the library is empty")
    titles = [i.get("data", {}).get("title", "(untitled)") for i in items[:3]]
    # Showing real titles back is the proof the connection works (spec section 5, phase B).
    return _ok(f"retrieved {len(items)} item(s), e.g. {titles[0][:60]!r}",
               adapter="zotero_api", base_url=base, sample_titles=titles,
               ping=ping.status_code)


def _probe_zotero_sqlite(db_path: str | None) -> ProbeResult:
    """Read 3 items from a *copy* of zotero.sqlite and resolve their attachments.

    The live database is never opened. See references/zotero-internals.md.
    """
    db = Path(db_path) if db_path else default_zotero_db()
    if not db.is_file():
        return _fail(f"zotero.sqlite not found at {db}", unavailable=True)

    before = (db.stat().st_mtime, db.stat().st_size)
    tmpdir = Path(tempfile.mkdtemp(prefix="litagent-probe-"))
    try:
        copy = tmpdir / "zotero.sqlite"
        shutil.copy2(db, copy)
        for suffix in ("-wal", "-shm", "-journal"):
            sibling = db.with_name(db.name + suffix)
            if sibling.exists():
                shutil.copy2(sibling, copy.with_name(copy.name + suffix))
        con = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        try:
            schema = con.execute(
                "SELECT version FROM version WHERE schema='userdata'").fetchone()
            rows = con.execute("""
                SELECT i.itemID, i.key,
                       MAX(CASE WHEN f.fieldName='title' THEN idv.value END) AS title
                FROM items i
                JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
                LEFT JOIN itemData d ON d.itemID = i.itemID
                LEFT JOIN fields f ON f.fieldID = d.fieldID
                LEFT JOIN itemDataValues idv ON idv.valueID = d.valueID
                WHERE it.typeName NOT IN ('attachment','note','annotation')
                  AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
                GROUP BY i.itemID
                HAVING title IS NOT NULL
                ORDER BY i.itemID LIMIT 3""").fetchall()
            resolved = 0
            storage = db.parent / "storage"
            for row in rows:
                for att in con.execute("""
                        SELECT ai.key AS akey, ia.path, ia.contentType
                        FROM itemAttachments ia JOIN items ai ON ai.itemID = ia.itemID
                        WHERE ia.parentItemID = ?""", (row["itemID"],)):
                    if att["contentType"] == "application/pdf" and (att["path"] or "").startswith("storage:"):
                        if (storage / att["akey"] / att["path"][8:]).is_file():
                            resolved += 1
                            break
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return _fail(f"could not read zotero.sqlite: {exc}")
    except OSError as exc:
        return _fail(f"could not copy zotero.sqlite: {exc}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    after = (db.stat().st_mtime, db.stat().st_size)
    if before != after:
        # Should be impossible - but if it ever happens, say so loudly rather than pass.
        return _fail("the Zotero database changed during a read-only probe; aborting")
    if not rows:
        return _fail("zotero.sqlite opened but contains no regular items")

    titles = [str(r["title"])[:60] for r in rows]
    schema_v = schema["version"] if schema else "unknown"
    return _ok(
        f"read {len(rows)} item(s) from a read-only copy (schema {schema_v}), "
        f"{resolved} with a resolvable PDF; e.g. {titles[0]!r}",
        adapter="zotero_sqlite", db_path=str(db), schema_version=schema_v,
        sample_titles=titles, resolved_pdfs=resolved)


# ---------------------------------------------------------------- optional


def probe_figures(cfg: dict[str, Any]) -> ProbeResult:
    """Extract at least one embedded image from the generated sample PDF."""
    try:
        import pymupdf
    except ImportError as exc:
        return _fail(f"pymupdf not importable: {exc}")
    try:
        sample = ensure_sample_pdf(paths.CACHE_DIR)
        doc = pymupdf.open(sample)
        images = [img for page in doc for img in page.get_images(full=True)]
        extracted = 0
        for xref, *_ in images:
            if doc.extract_image(xref).get("image"):
                extracted += 1
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"image extraction failed on the sample PDF: {type(exc).__name__}: {exc}")
    if not extracted:
        return _fail("no embedded images could be extracted from the sample PDF")
    return _ok(f"extracted {extracted} embedded image(s) from the sample PDF", images=extracted)


def probe_layout_text(cfg: dict[str, Any]) -> ProbeResult:
    """pymupdf4llm is an opt-in extra (ADR-0001), so absence is 'not installed', not a failure."""
    try:
        import pymupdf4llm
    except ImportError:
        return _fail("pymupdf4llm is not installed. It is optional and slow "
                     "(see docs/decisions/0001); install it only if you need layout-aware "
                     "markdown extraction.", unavailable=True)
    try:
        sample = ensure_sample_pdf(paths.CACHE_DIR)
        started = time.time()
        chunks = pymupdf4llm.to_markdown(str(sample), page_chunks=True)
        elapsed = time.time() - started
    except Exception as exc:  # noqa: BLE001
        return _fail(f"pymupdf4llm imported but conversion failed: {type(exc).__name__}: {exc}")
    if not chunks:
        return _fail("pymupdf4llm returned no content for the sample PDF")
    return _ok(f"converted the sample PDF in {elapsed:.1f}s "
               f"({getattr(pymupdf4llm, '__version__', '?')})", seconds=round(elapsed, 2))


def probe_arxiv_source(cfg: dict[str, Any]) -> ProbeResult:
    """Fetch a known e-print tarball and confirm it unpacks."""
    import io
    import tarfile
    try:
        import httpx
    except ImportError:
        return _fail("httpx is missing; run /lit-setup to repair the venv")
    arxiv_id = cfg.get("probe_id", "2501.13958")
    try:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": _user_agent(cfg)}) as client:
            resp = client.get(f"https://arxiv.org/e-print/{arxiv_id}")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not reach arxiv.org: {type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        return _fail(f"arxiv.org returned HTTP {resp.status_code} for e-print/{arxiv_id}")
    try:
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:*") as tar:
            names = tar.getnames()[:400]
    except tarfile.TarError as exc:
        return _fail(f"downloaded {len(resp.content)} bytes but could not unpack it: {exc}")
    tex = sum(1 for n in names if n.endswith(".tex"))
    return _ok(f"fetched and unpacked e-print {arxiv_id}: {len(resp.content):,} bytes, "
               f"{len(names)} entries, {tex} .tex file(s)", probe_id=arxiv_id)


def probe_semantic_scholar(cfg: dict[str, Any]) -> ProbeResult:
    """Look up a known DOI and confirm a title comes back."""
    try:
        import httpx
    except ImportError:
        return _fail("httpx is missing; run /lit-setup to repair the venv")
    doi = cfg.get("probe_doi", "10.1145/3637528.3671460")
    headers = {"User-Agent": _user_agent(cfg)}
    if cfg.get("api_key"):
        headers["x-api-key"] = cfg["api_key"]
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
            resp = client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                params={"fields": "title,year,tldr,citationCount"})
    except Exception as exc:  # noqa: BLE001
        return _fail(f"could not reach api.semanticscholar.org: {type(exc).__name__}: {exc}")
    if resp.status_code == 429:
        return _fail("rate limited (HTTP 429). Semantic Scholar throttles unauthenticated "
                     "clients; add an API key in /lit-setup or retry later.")
    if resp.status_code != 200:
        return _fail(f"HTTP {resp.status_code}: {resp.text[:120]}")
    data = resp.json()
    if not data.get("title"):
        return _fail("response carried no title; the API shape may have changed")
    has_tldr = bool((data.get("tldr") or {}).get("text"))
    return _ok(f"resolved DOI {doi} to {data['title'][:50]!r} "
               f"({data.get('year')}), TLDR {'present' if has_tldr else 'absent'}",
               has_key=bool(cfg.get("api_key")))


def probe_browser(cfg: dict[str, Any]) -> ProbeResult:
    """Browser control lives in the Claude Code environment, not in Python.

    This probe cannot drive the browser itself, so it never reports success on its own --
    that would be exactly the user-assertion shortcut P2 forbids. The /lit-setup skill runs
    the real round trip (navigate, read the title back) and records the result.
    """
    if cfg.get("verified_by_skill"):
        return _ok(f"round trip verified by /lit-setup: {cfg.get('evidence', 'title read back')}")
    return _fail("not verified from Python. /lit-setup performs the browser round trip "
                 "(navigate to a page, read its title back) and records the result here.")


def probe_scholar_labs(cfg: dict[str, Any]) -> ProbeResult:
    """Demoted to a stretch goal by ADR-0003 - /labs returned 404 during M0/S5."""
    if cfg.get("verified_by_skill"):
        return _ok(f"verified by /lit-setup: {cfg.get('evidence')}")
    return _fail("Google Scholar Labs was unreachable when last probed (404 on /labs) and is "
                 "off by default. Automated access is outside Google Scholar's terms of "
                 "service. Use Semantic Scholar enrichment instead.", unavailable=True)


def probe_zotero_write(cfg: dict[str, Any]) -> ProbeResult:
    """Create a note on a scratch item, read it back, delete it.

    Deliberately refuses to run without an explicitly configured scratch item: a write probe
    must never touch a real library by default.
    """
    user_id, api_key = cfg.get("user_id"), cfg.get("api_key")
    if not (user_id and api_key):
        return _fail("not configured. Needs a Zotero user ID and a Web API key with write "
                     "permission from https://www.zotero.org/settings/keys")
    scratch = cfg.get("scratch_item_key")
    if not scratch:
        return _fail("no scratch item configured. The write probe creates and deletes a real "
                     "note, so it requires an item you designate for testing.")
    try:
        import httpx
    except ImportError:
        return _fail("httpx is missing; run /lit-setup to repair the venv")
    base = f"https://api.zotero.org/users/{user_id}"
    headers = {"Zotero-API-Version": "3", "Zotero-API-Key": api_key,
               "User-Agent": _user_agent(cfg)}
    marker = f"lit-agent probe {int(time.time())}"
    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            created = client.post(f"{base}/items", json=[{
                "itemType": "note", "parentItem": scratch,
                "note": f"<p>{marker}</p>", "tags": [{"tag": "lit-agent"}]}])
            if created.status_code not in (200, 201):
                return _fail(f"create failed: HTTP {created.status_code} {created.text[:120]}")
            successful = (created.json().get("successful") or {})
            if not successful:
                return _fail(f"create returned no item: {created.text[:150]}")
            key = next(iter(successful.values()))["key"]

            read = client.get(f"{base}/items/{key}")
            if read.status_code != 200 or marker not in read.text:
                return _fail(f"created note {key} but could not read it back "
                             f"(HTTP {read.status_code})")
            version = read.json()["version"]
            deleted = client.delete(f"{base}/items/{key}",
                                    headers={"If-Unmodified-Since-Version": str(version)})
            if deleted.status_code not in (204, 200):
                return _fail(f"created and read note {key} but could not delete it "
                             f"(HTTP {deleted.status_code}). Remove it manually.")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{type(exc).__name__}: {exc}")
    return _ok(f"created note on scratch item {scratch}, read it back, and deleted it",
               user_id=user_id, scratch_item_key=scratch)


def probe_vector_index(cfg: dict[str, Any]) -> ProbeResult:
    """Embed two strings and assert the cosine similarity is sane."""
    try:
        import sqlite_vec
    except ImportError:
        return _fail("sqlite-vec is not installed. Run: pip install sqlite-vec",
                     unavailable=True)
    try:
        con = sqlite3.connect(":memory:")
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        version = con.execute("SELECT vec_version()").fetchone()[0]
        # Two vectors that point the same way must score higher than two that do not.
        near = con.execute(
            "SELECT vec_distance_cosine(?, ?)",
            (sqlite_vec.serialize_float32([1.0, 0.0, 0.0]),
             sqlite_vec.serialize_float32([0.9, 0.1, 0.0]))).fetchone()[0]
        far = con.execute(
            "SELECT vec_distance_cosine(?, ?)",
            (sqlite_vec.serialize_float32([1.0, 0.0, 0.0]),
             sqlite_vec.serialize_float32([0.0, 1.0, 0.0]))).fetchone()[0]
        con.close()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"sqlite-vec present but not usable: {type(exc).__name__}: {exc}")
    if not near < far:
        return _fail(f"cosine distances are not sane: near={near:.3f} far={far:.3f}")
    return _ok(f"sqlite-vec {version} loaded; cosine distance sane "
               f"(near {near:.3f} < far {far:.3f})", vec_version=version)


def probe_grobid(cfg: dict[str, Any]) -> ProbeResult:
    """Ping the GROBID server."""
    url = (cfg.get("url") or "http://localhost:8070").rstrip("/")
    try:
        import httpx
    except ImportError:
        return _fail("httpx is missing; run /lit-setup to repair the venv")
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(f"{url}/api/isalive")
    except Exception:  # noqa: BLE001
        return _fail(f"no GROBID server responding at {url}. Start one with: docker run "
                     "--rm -p 8070:8070 lfoppiano/grobid:0.8.0", unavailable=True)
    if resp.status_code != 200:
        return _fail(f"{url}/api/isalive returned HTTP {resp.status_code}")
    return _ok(f"GROBID alive at {url}", url=url)


PROBES: dict[str, Callable[[dict[str, Any]], ProbeResult]] = {
    "python_env": probe_python_env,
    "source": probe_source,
    "pdf_text": probe_pdf_text,
    "figures": probe_figures,
    "layout_text": probe_layout_text,
    "arxiv_source": probe_arxiv_source,
    "browser": probe_browser,
    "semantic_scholar": probe_semantic_scholar,
    "scholar_labs": probe_scholar_labs,
    "zotero_write": probe_zotero_write,
    "vector_index": probe_vector_index,
    "grobid": probe_grobid,
}


# ---------------------------------------------------------------- detection


def default_zotero_db() -> Path:
    return Path.home() / "Zotero" / "zotero.sqlite"


def detect_sources() -> list[dict[str, Any]]:
    """Auto-detect library sources in the priority order of spec section 5, phase B."""
    found: list[dict[str, Any]] = []

    # 1. Zotero local HTTP API
    api = _probe_zotero_api(None)
    found.append({
        "adapter": "zotero_api", "priority": 1,
        "available": api.ok, "detail": api.detail,
        "config": api.config if api.ok else {},
    })

    # 2. zotero.sqlite
    db = default_zotero_db()
    if db.is_file():
        found.append({
            "adapter": "zotero_sqlite", "priority": 2, "available": True,
            "detail": f"found zotero.sqlite ({db.stat().st_size:,} bytes) at {db}",
            "config": {"adapter": "zotero_sqlite", "db_path": str(db)},
        })
    else:
        found.append({"adapter": "zotero_sqlite", "priority": 2, "available": False,
                      "detail": f"no zotero.sqlite at {db}", "config": {}})

    # 3 & 4. Export directory / plain PDF folder, relative to the working directory.
    cwd = Path.cwd()
    pdfs = [p for p in cwd.rglob("*.pdf") if ".lit" not in p.parts][:200]
    exports = [p for p in cwd.glob("*") if p.suffix.lower() in (".bib", ".csv", ".json", ".rdf")]
    found.append({
        "adapter": "export_dir", "priority": 3, "available": bool(exports and pdfs),
        "detail": (f"{len(exports)} export file(s) and {len(pdfs)} PDF(s) under {cwd}"
                   if exports else f"no BibTeX/CSV/CSL-JSON/RDF export files under {cwd}"),
        "config": {"adapter": "export_dir", "path": str(cwd)} if exports and pdfs else {},
    })
    found.append({
        "adapter": "generic_pdf", "priority": 4, "available": bool(pdfs),
        "detail": f"{len(pdfs)} PDF(s) under {cwd}" if pdfs else f"no PDFs under {cwd}",
        "config": {"adapter": "generic_pdf", "path": str(cwd)} if pdfs else {},
    })
    return found


def _user_agent(cfg: dict[str, Any]) -> str:
    contact = cfg.get("contact_email") or os.environ.get("LIT_AGENT_CONTACT", "")
    suffix = f"; mailto:{contact}" if contact else ""
    return f"lit-agent/0.1 (+https://github.com/bilgehangul/lit-agent{suffix})"


# ---------------------------------------------------------------- driver


def run_probes(caps: Capabilities, only: list[str] | None = None,
               include_unconfigured: bool = True) -> dict[str, ProbeResult]:
    """Run probes and return results. Does not mutate ``caps``; see ``apply_results``."""
    results: dict[str, ProbeResult] = {}
    for spec in REGISTRY:
        if only and spec.id not in only:
            continue
        state = caps.get(spec.id)
        # Skip optional capabilities the user has never turned on, unless asked for them.
        if (not include_unconfigured and not spec.required
                and state.status not in ("enabled", "broken")):
            continue
        probe = PROBES.get(spec.id)
        if probe is None:
            results[spec.id] = _fail("no probe implemented")
            continue
        started = time.time()
        try:
            result = probe(dict(state.config))
        except Exception as exc:  # noqa: BLE001 - a probe must never crash the doctor
            result = _fail(f"probe raised {type(exc).__name__}: {exc}")
        result.elapsed = time.time() - started
        results[spec.id] = result
    return results


def apply_results(caps: Capabilities, results: dict[str, ProbeResult],
                  enable_on_pass: bool = True) -> None:
    """Fold probe results back into capability state.

    A pass promotes to ``enabled`` only when the capability was already on or is required --
    passing a probe is not consent to turn an optional feature on (**P3**). A failure
    demotes an ``enabled`` capability to ``broken``; a capability that was already off stays
    off with its reason recorded.
    """
    for cid, result in results.items():
        spec = BY_ID.get(cid)
        state = caps.get(cid)
        if result.ok:
            if enable_on_pass and (state.is_enabled or (spec and spec.required)):
                caps.enable(cid, {**state.config, **result.config})
            else:
                state.last_error = None
        else:
            if state.is_enabled:
                caps.mark_broken(cid, result.detail)
            else:
                caps.disable(cid, result.detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lit-doctor",
        description="Re-run every capability probe, report status, and offer repairs.")
    parser.add_argument("--only", nargs="+", metavar="CAP",
                        help="probe only these capability ids")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--apply", action="store_true",
                        help="write probe results back to capabilities.json")
    parser.add_argument("--detect", action="store_true",
                        help="only auto-detect library sources, then exit")
    args = parser.parse_args(argv)

    if args.detect:
        detected = detect_sources()
        print(json.dumps(detected, indent=2) if args.json else _format_detection(detected))
        return 0

    caps = Capabilities.load()
    if caps.load_error and not args.json:
        print(f"warning: capabilities file unreadable ({caps.load_error}); "
              "treating every capability as off.\n", file=sys.stderr)

    unknown = [c for c in (args.only or []) if c not in BY_ID]
    if unknown:
        parser.error(f"unknown capability id(s): {', '.join(unknown)}. "
                     f"Known: {', '.join(BY_ID)}")

    results = run_probes(caps, only=args.only)
    if args.apply:
        apply_results(caps, results)
        caps.save()

    if args.json:
        print(json.dumps({
            "capabilities_file": str(caps.path),
            "applied": args.apply,
            "results": {
                cid: {"ok": r.ok, "detail": r.detail, "unavailable": r.unavailable,
                      "config": r.config, "seconds": round(r.elapsed, 2),
                      "status": caps.get(cid).status}
                for cid, r in results.items()},
        }, indent=2))
    else:
        print(_format_report(caps, results))

    required_broken = [c.id for c in REGISTRY
                       if c.required and c.id in results and not results[c.id].ok]
    return 1 if required_broken else 0


def _format_detection(detected: list[dict[str, Any]]) -> str:
    lines = ["Library sources, in priority order:", ""]
    for d in sorted(detected, key=lambda x: x["priority"]):
        lines.append(f"  [{'x' if d['available'] else ' '}] {d['adapter']:14} {d['detail']}")
    return "\n".join(lines)


def _format_report(caps: Capabilities, results: dict[str, ProbeResult]) -> str:
    lines = ["", "lit-agent capability report", "=" * 60, "", format_table(caps), "", "Probes:"]
    for cid, r in results.items():
        spec = BY_ID.get(cid)
        title = spec.title if spec else cid
        mark = "PASS" if r.ok else ("n/a " if r.unavailable else "FAIL")
        lines.append(f"  [{mark}] {title} ({r.elapsed:.1f}s)")
        lines.append(f"         {r.detail}")
    failed_required = [c.id for c in REGISTRY
                       if c.required and c.id in results and not results[c.id].ok]
    lines.append("")
    if failed_required:
        lines.append("A required capability is not working. lit-agent cannot run until it is:")
        for cid in failed_required:
            lines.append(f"  /lit-setup --reconfigure {cid}")
    else:
        broken = [cid for cid, r in results.items()
                  if not r.ok and caps.get(cid).status == "broken"]
        if broken:
            lines.append("Broken capabilities (previously working):")
            for cid in broken:
                lines.append(f"  /lit-setup --reconfigure {cid}")
        else:
            lines.append("Required capabilities are healthy.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
