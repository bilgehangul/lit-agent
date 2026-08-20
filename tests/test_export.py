"""The literature-review folder export."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from export import UNFILED_DIR, export, link, safe_name  # noqa: E402
from lib.note import SECTIONS, Note  # noqa: E402
from lib.paths import Corpus  # noqa: E402

pytest.importorskip("yaml")


def add_paper(corpus: Corpus, citekey: str, collections: list[str],
              analyzed: bool = True, pdf: bool = True, tmp_path: Path | None = None) -> None:
    pdf_path = None
    if pdf and tmp_path is not None:
        pdf_path = tmp_path / "library" / f"{citekey}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes for " + citekey.encode())

    (corpus.raw / f"{citekey}.json").write_text(json.dumps({
        "source_id": citekey.upper()[:8],
        "citekey": citekey,
        "metadata": {"title": f"Title of {citekey}", "author": [{"family": "Doe",
                                                                "given": "Jane"}],
                     "issued": {"date-parts": [[2024]]}},
        "attachments": [str(pdf_path)] if pdf_path else [],
        "notes": [], "annotations": [], "tags": [],
        "collections": collections, "warnings": [],
    }), encoding="utf-8")

    (corpus.text / f"{citekey}.md").write_text(
        f"<!-- page 1 -->\nText of {citekey}.\n", encoding="utf-8")

    if analyzed:
        frontmatter = {"citekey": citekey, "title": f"Title of {citekey}", "year": 2024,
                       "relevance": "high", "paper_type": "empirical",
                       "scope_version": "v1", "analyzed": "2026-08-20"}
        sections = {name: "Not determinable from the text" for name in SECTIONS}
        (corpus.papers / f"{citekey}.md").write_text(
            Note(citekey=citekey, frontmatter=frontmatter, sections=sections).to_markdown(),
            encoding="utf-8")


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / ".lit")
    c.ensure()
    add_paper(c, "doe2024alpha", ["ToSDR LLM"], tmp_path=tmp_path)
    add_paper(c, "doe2024beta", ["Social media apps", "Literature"], tmp_path=tmp_path)
    add_paper(c, "doe2024gamma", [], analyzed=False, tmp_path=tmp_path)
    add_paper(c, "doe2024delta", ["ToSDR LLM"], pdf=False, tmp_path=tmp_path)
    c.synthesis.mkdir(parents=True, exist_ok=True)
    (c.synthesis / "themes.md").write_text("# Themes\n", encoding="utf-8")
    c.refs_bib.write_text("@misc{doe2024alpha}\n", encoding="utf-8")
    return c


# --- path safety -----------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("ToSDR LLM", "ToSDR LLM"),
    ("AI/ML papers", "AI-ML papers"),
    ('Bad:name?"', "Bad-name"),
    ("CON", "CON-"),
    ("trailing.", "trailing"),
])
def test_safe_name(raw: str, expected: str) -> None:
    assert safe_name(raw) == expected


def test_links_are_percent_encoded() -> None:
    """An unescaped space breaks the index in most markdown viewers."""
    assert link("ToSDR LLM/doe2024alpha/") == "ToSDR%20LLM/doe2024alpha/"
    assert link("plain/path.md") == "plain/path.md"


# --- structure -------------------------------------------------------------


def test_mirrors_collections(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = export(corpus, dest)
    assert (dest / "ToSDR LLM" / "doe2024alpha" / "SUMMARY.md").is_file()
    assert (dest / "ToSDR LLM" / "doe2024alpha" / "doe2024alpha.pdf").is_file()
    assert (dest / "ToSDR LLM" / "doe2024alpha" / "fulltext.md").is_file()
    assert (dest / "ToSDR LLM" / "doe2024alpha" / "metadata.json").is_file()
    assert report.papers == 4


def test_paper_with_no_collection_goes_to_unfiled(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    export(corpus, dest)
    assert (dest / UNFILED_DIR / "doe2024gamma" / "SUMMARY.md").is_file()


def test_cross_filed_paper_is_stored_once_with_a_pointer(corpus: Corpus,
                                                         tmp_path: Path) -> None:
    """Two collections, one PDF. The second gets a pointer, not a 1 MB duplicate."""
    dest = tmp_path / "out"
    report = export(corpus, dest)
    primary = dest / "Literature" / "doe2024beta"
    pointer = dest / "Social media apps" / "doe2024beta.md"
    assert primary.is_dir() and (primary / "doe2024beta.pdf").is_file()
    assert pointer.is_file()
    assert not (dest / "Social media apps" / "doe2024beta").exists()
    assert "Literature" in pointer.read_text(encoding="utf-8")
    assert report.cross_filed == [("doe2024beta", "Social media apps")]


def test_synthesis_is_copied(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    export(corpus, dest)
    assert (dest / "_synthesis" / "themes.md").is_file()
    assert (dest / "_synthesis" / "refs.bib").is_file()


# --- gaps are visible ------------------------------------------------------


def test_unanalyzed_paper_gets_a_placeholder_explaining_why(corpus: Corpus,
                                                            tmp_path: Path) -> None:
    """P4: an empty folder looks finished. A placeholder says it is not."""
    dest = tmp_path / "out"
    report = export(corpus, dest)
    summary = (dest / UNFILED_DIR / "doe2024gamma" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "No summary yet" in summary
    assert "lit-analyze" in summary
    assert "doe2024gamma" in report.unanalyzed


def test_paper_without_a_pdf_is_reported(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = export(corpus, dest)
    assert report.pdfs_missing == ["doe2024delta"]
    assert (dest / "ToSDR LLM" / "doe2024delta" / "SUMMARY.md").is_file()
    assert "no PDF" in (dest / "README.md").read_text(encoding="utf-8")


# --- the index -------------------------------------------------------------


def test_readme_lists_every_collection_and_paper(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    export(corpus, dest)
    readme = (dest / "README.md").read_text(encoding="utf-8")
    for collection in ("ToSDR LLM", "Social media apps", "Literature"):
        assert collection in readme
    for citekey in ("doe2024alpha", "doe2024beta", "doe2024gamma", "doe2024delta"):
        assert citekey in readme
    assert "ToSDR%20LLM/doe2024alpha/SUMMARY.md" in readme


def test_readme_says_the_folder_is_a_projection(corpus: Corpus, tmp_path: Path) -> None:
    """P5: nothing is ever read back from here, and the reader should know it."""
    dest = tmp_path / "out"
    export(corpus, dest)
    assert "projection, not a" in (dest / "README.md").read_text(encoding="utf-8")


# --- rebuilding ------------------------------------------------------------


def test_rerun_reuses_pdfs_rather_than_recopying(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    first = export(corpus, dest)
    second = export(corpus, dest)
    assert first.pdfs_copied == 3
    assert second.pdfs_copied == 0
    assert second.pdfs_reused == 3


def test_rerun_picks_up_a_new_summary(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    export(corpus, dest)
    add_paper(corpus, "doe2024gamma", [], analyzed=True, pdf=False)
    export(corpus, dest)
    summary = (dest / UNFILED_DIR / "doe2024gamma" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "No summary yet" not in summary


def test_prune_removes_a_paper_dropped_from_the_corpus(corpus: Corpus,
                                                       tmp_path: Path) -> None:
    dest = tmp_path / "out"
    export(corpus, dest)
    (corpus.raw / "doe2024alpha.json").unlink()
    report = export(corpus, dest)
    assert not (dest / "ToSDR LLM" / "doe2024alpha").exists()
    assert report.removed == 1


def test_prune_leaves_unrelated_files_alone(corpus: Corpus, tmp_path: Path) -> None:
    """Only folders that look like exported papers are ever removed."""
    dest = tmp_path / "out"
    export(corpus, dest)
    stray = dest / "my own notes.md"
    stray.write_text("mine", encoding="utf-8")
    export(corpus, dest)
    assert stray.is_file()


def test_filters_narrow_the_export(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = export(corpus, dest, filters=["relevance=high"])
    # doe2024gamma has no note, so no frontmatter to match: it is excluded.
    assert report.papers == 3
    assert not (dest / UNFILED_DIR / "doe2024gamma").exists()


def test_no_pdfs_mode_writes_summaries_only(corpus: Corpus, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = export(corpus, dest, include_pdfs=False)
    assert report.pdfs_copied == 0
    assert (dest / "ToSDR LLM" / "doe2024alpha" / "SUMMARY.md").is_file()
    assert not (dest / "ToSDR LLM" / "doe2024alpha" / "doe2024alpha.pdf").exists()
