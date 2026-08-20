"""M2 — models, citekeys, dedupe, checkpoints, and text extraction."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from adapters.base import dedupe, dedupe_key, merge_items, normalize_title  # noqa: E402
from extract import pdf_text  # noqa: E402
from lib.citekey import CitekeyAllocator, base_citekey, title_part  # noqa: E402
from lib.models import (  # noqa: E402
    Annotation,
    LibraryItem,
    Note,
    clean_title,
    normalize_arxiv,
    normalize_doi,
    parse_zotero_date,
)
from lib.sample_pdf import write_sample_pdf  # noqa: E402
from lib.state import DONE, State, file_hash  # noqa: E402


def item(source_id="X", title="A Study of Things", family="Doe", year=2024,
         doi="", arxiv="", pdfs=()) -> LibraryItem:
    metadata = {"title": title, "author": [{"family": family, "given": "Jane"}]}
    if year:
        metadata["issued"] = {"date-parts": [[year]]}
    if doi:
        metadata["DOI"] = doi
    if arxiv:
        metadata["arxiv_id"] = arxiv
    return LibraryItem(source_id=source_id, metadata=metadata,
                       attachments=[Path(p) for p in pdfs])


# --- identifier normalization ---------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("https://doi.org/10.1145/3637528.3671460", "10.1145/3637528.3671460"),
    ("doi:10.1145/ABC", "10.1145/abc"),
    ("http://dx.doi.org/10.1/X", "10.1/x"),
    ("10.1145/3637528.3671460", "10.1145/3637528.3671460"),
    ("", ""),
])
def test_normalize_doi(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("arXiv:2501.13958v2", "2501.13958"),
    ("https://arxiv.org/abs/2409.00077", "2409.00077"),
    ("https://arxiv.org/pdf/2409.00077v3", "2409.00077"),
    ("10.48550/arXiv.2501.13958", "2501.13958"),
    ("2501.13958", "2501.13958"),
    ("not an id", ""),
])
def test_normalize_arxiv(raw: str, expected: str) -> None:
    assert normalize_arxiv(raw) == expected


def test_clean_title_strips_web_import_junk() -> None:
    """Real titles in the dev library carry a leading "(PDF) " (M0/S2-d)."""
    assert clean_title("(PDF) Research Trends in Digital Forensics") == \
        "Research Trends in Digital Forensics"
    assert clean_title("A Normal Title") == "A Normal Title"


@pytest.mark.parametrize("raw,year,original", [
    ("2024-08-25 2024-08-25", 2024, "2024-08-25"),
    ("2011-12-05 December 5, 2011", 2011, "December 5, 2011"),
    ("2012-08-00 08/2012", 2012, "08/2012"),
    ("2025-04-00 2025-04", 2025, "2025-04"),
    ("", None, ""),
])
def test_parse_zotero_date(raw, year, original) -> None:
    """Zotero packs two values into one column; naive parsing poisons citekeys (M0/S2-c)."""
    assert parse_zotero_date(raw) == (year, original)


# --- citekeys --------------------------------------------------------------


def test_base_citekey_shape() -> None:
    assert base_citekey("Doe", 2024, "Privacy Policies at Scale") == "doe2024privacy"


def test_citekey_folds_accents_and_strips_punctuation() -> None:
    assert base_citekey("Güner-Smith", 2025, "Étude des Systèmes") == "gunersmith2025etude"


def test_citekey_handles_missing_pieces() -> None:
    assert base_citekey("", None, "") == "anonnodateuntitled"


def test_title_part_skips_stopwords_and_short_words() -> None:
    assert title_part("On the Use of A Very Large Model") == "very"


def test_citekey_collisions_get_stable_suffixes() -> None:
    alloc = CitekeyAllocator()
    a = alloc.allocate(item("A", "Privacy Policies", "Doe", 2024))
    b = alloc.allocate(item("B", "Privacy Policies Again", "Doe", 2024))
    c = alloc.allocate(item("C", "Privacy Preserving", "Doe", 2024))
    assert (a, b, c) == ("doe2024privacy", "doe2024privacya", "doe2024privacyb")


def test_existing_citekeys_are_never_reassigned() -> None:
    """Citekeys end up in the user's manuscript; they must not drift between runs."""
    alloc = CitekeyAllocator({"A": "doe2024original"})
    # Metadata has since been corrected, but the key must survive.
    assert alloc.allocate(item("A", "A Corrected Title", "Roe", 2025)) == "doe2024original"


def test_better_bibtex_key_is_preferred_when_offered() -> None:
    alloc = CitekeyAllocator()
    assert alloc.allocate(item("A"), preferred="doeEtAl2024") == "doeEtAl2024"


# --- dedupe ----------------------------------------------------------------


def test_dedupe_key_prefers_doi_then_arxiv_then_title() -> None:
    assert dedupe_key(item(doi="10.1/x", arxiv="2501.13958"))[0] == "doi"
    assert dedupe_key(item(arxiv="2501.13958"))[0] == "arxiv"
    assert dedupe_key(item())[0] == "title+year"
    assert dedupe_key(LibraryItem(source_id="Z"))[0] == "source_id"


def test_dedupe_merges_on_doi() -> None:
    report = dedupe([item("A", doi="10.1/x"), item("B", title="Different", doi="10.1/x")])
    assert len(report.kept) == 1
    assert report.merged[0][2] == "doi"


def test_dedupe_merges_on_normalized_title_and_year() -> None:
    a = item("A", title="Polisis: Automated Analysis of Privacy Policies")
    b = item("B", title="POLISIS - automated analysis of privacy policies!")
    report = dedupe([a, b])
    assert len(report.kept) == 1
    assert report.merged[0][2] == "title+year"


def test_dedupe_keeps_distinct_papers_apart() -> None:
    assert len(dedupe([item("A", doi="10.1/x"), item("B", doi="10.1/y")]).kept) == 2


def test_dedupe_prefers_the_copy_that_has_a_pdf() -> None:
    without = item("A", doi="10.1/x")
    with_pdf = item("B", doi="10.1/x", pdfs=["paper.pdf"])
    report = dedupe([without, with_pdf])
    assert len(report.kept) == 1
    assert report.kept[0].has_pdf, "merging must never lose the attachment"


def test_dedupe_reports_why_items_merged() -> None:
    report = dedupe([item("A", doi="10.1/x"), item("B", doi="10.1/x")])
    assert "merged 1 duplicate(s)" in report.summary()
    assert "by doi" in report.summary()


def test_merge_unions_rather_than_overwrites() -> None:
    a = item("A", doi="10.1/x")
    a.tags, a.notes = ["tag-a"], [Note(html="<p>one</p>", text="one")]
    b = item("B", doi="10.1/x")
    b.tags, b.notes = ["tag-b"], [Note(html="<p>two</p>", text="two")]
    b.annotations = [Annotation(type="highlight", text="hi")]
    b.metadata["abstract"] = "recovered from the other copy"
    merged = merge_items(a, b)
    assert merged.tags == ["tag-a", "tag-b"]
    assert {n.text for n in merged.notes} == {"one", "two"}
    assert len(merged.annotations) == 1
    assert merged.metadata["abstract"] == "recovered from the other copy"


def test_merge_does_not_duplicate_identical_notes() -> None:
    a, b = item("A", doi="10.1/x"), item("B", doi="10.1/x")
    a.notes = [Note(html="<p>same</p>", text="same")]
    b.notes = [Note(html="<p>same</p>", text="same")]
    assert len(merge_items(a, b).notes) == 1


def test_normalize_title_is_aggressive_enough_to_match_real_variants() -> None:
    assert normalize_title("Polisis: Automated Analysis!") == normalize_title(
        "POLISIS -- automated  analysis")


# --- notes -----------------------------------------------------------------


def test_note_html_to_text_keeps_structure_readable() -> None:
    text = Note.html_to_text("<p>First para</p><ul><li>one</li><li>two</li></ul>")
    assert "First para" in text and "- one" in text and "- two" in text


def test_note_html_to_text_decodes_entities() -> None:
    assert Note.html_to_text("<p>a &amp; b &lt;c&gt;</p>") == "a & b <c>"


def test_note_preserves_original_html_verbatim() -> None:
    """Spec 6.9: the user's own words are never rewritten."""
    html = '<p style="color:red">Their <b>exact</b> words</p>'
    note = Note(html=html, text=Note.html_to_text(html))
    assert note.html == html


# --- state / checkpoints ---------------------------------------------------


def test_state_roundtrips(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")
    state.mark_done("doe2024x", "text", chars=100)
    state.save()
    assert State.load(tmp_path / "state.json").is_done("doe2024x", "text")


def test_is_done_is_false_when_the_source_pdf_changed(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")
    state.mark_done("doe2024x", "text", hash="sha256:aaa")
    assert state.is_done("doe2024x", "text", "sha256:aaa")
    assert not state.is_done("doe2024x", "text", "sha256:bbb")


def test_a_skip_must_carry_a_reason(tmp_path: Path) -> None:
    """P4: a silently skipped item is indistinguishable from a processed one."""
    state = State(tmp_path / "state.json")
    with pytest.raises(ValueError):
        state.mark(("doe2024x"), "text", "skipped")
    state.mark_skipped("doe2024x", "text", reason="no PDF")
    assert state.skips()[0][2] == "no PDF"


def test_errors_are_collected_not_raised(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")
    state.mark_error("a", "text", "boom")
    state.mark_done("b", "text")
    assert state.errors() == [("a", "text", "boom")]
    assert state.citekeys_with("text", DONE) == ["b"]


def test_counts_treat_untouched_items_as_pending(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")
    state.mark_done("a", "ingest")
    state.mark_done("b", "ingest")
    state.mark_skipped("b", "text", reason="no PDF")
    counts = state.counts("text")
    assert counts["skipped"] == 1 and counts["pending"] == 1


def test_scope_change_is_detected(tmp_path: Path) -> None:
    state = State(tmp_path / "state.json")
    assert state.set_scope_version("v1") is False      # first time is not a change
    assert state.set_scope_version("v1") is False
    assert state.set_scope_version("v2") is True       # this makes existing notes stale


def test_corrupt_state_is_preserved_not_silently_dropped(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    state = State.load(path)
    assert state.load_error is not None
    assert any(p.name.startswith("state.corrupt-") for p in tmp_path.iterdir())


def test_file_hash_changes_with_content(tmp_path: Path) -> None:
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert file_hash(a) != file_hash(b)
    assert file_hash(a).startswith("sha256:")


# --- text extraction -------------------------------------------------------


def test_extraction_emits_page_markers_that_parse_back(tmp_path: Path) -> None:
    """P7 depends on this round trip: a locator must resolve to real page text."""
    pytest.importorskip("pymupdf")
    pdf = write_sample_pdf(tmp_path / "s.pdf")
    result = pdf_text.extract(pdf, citekey="sample2026test")
    assert result.ok and result.pages == 1
    pages = pdf_text.split_pages(result.markdown)
    assert set(pages) == {1}
    assert "known-good sample" in pages[1].lower()


def test_extraction_header_records_provenance(tmp_path: Path) -> None:
    pytest.importorskip("pymupdf")
    result = pdf_text.extract(write_sample_pdf(tmp_path / "s.pdf"), citekey="k")
    assert "citekey=k" in result.markdown
    assert "extractor=pymupdf" in result.markdown


def test_page_marker_regex_matches_what_we_write() -> None:
    text = pdf_text.PAGE_MARKER.format(n=7)
    assert pdf_text.PAGE_MARKER_RE.match(text)
    assert pdf_text.split_pages(f"{text}\nbody text\n") == {7: "body text"}


def test_split_pages_handles_many_pages() -> None:
    md = "".join(f"<!-- page {n} -->\ntext for {n}\n" for n in range(1, 6))
    pages = pdf_text.split_pages(md)
    assert len(pages) == 5 and pages[3] == "text for 3"


def test_missing_pdf_is_an_error_not_a_crash(tmp_path: Path) -> None:
    pytest.importorskip("pymupdf")
    result = pdf_text.extract(tmp_path / "nope.pdf", citekey="x")
    assert not result.ok and result.error


def test_scanned_pdf_is_detected(tmp_path: Path) -> None:
    """A near-empty text layer means scanned images, not an empty paper (P4)."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    assert pdf_text.extract(path, citekey="x").scanned


def test_heading_noise_is_rejected() -> None:
    """A bogus heading is worse than a missing one - it produces a fake section locator."""
    for noise in ("arXiv:2409.00077v2  [cs.CL]  6 Sep 2024",
                  "Open access to the Proceedings of the",
                  "https://doi.org/10.1/x",
                  "Figure 3"):
        assert pdf_text.HEADING_NOISE.search(noise), noise
    for heading in ("Introduction", "Related Work", "Evaluation"):
        assert not pdf_text.HEADING_NOISE.search(heading), heading


def test_split_sections_indexes_numbered_headings() -> None:
    md = "## 4.2 Evaluation Setup\nsome text\n## 5 Results\nmore text\n"
    sections = pdf_text.split_sections(md)
    assert sections["4.2"] == "some text"
    assert sections["5"] == "more text"


# --- LibraryItem accessors -------------------------------------------------


def test_library_item_accessors() -> None:
    it = item(title="A Study", family="Doe", year=2024, doi="https://doi.org/10.1/X")
    assert it.year == 2024
    assert it.first_author_family == "Doe"
    assert it.authors == ["Jane Doe"]
    assert it.doi == "10.1/x"
    assert not it.has_pdf


def test_library_item_json_roundtrip(tmp_path: Path) -> None:
    original = item("A", doi="10.1/x", pdfs=["a.pdf"])
    original.notes = [Note(html="<p>hi</p>", text="hi")]
    original.annotations = [Annotation(type="highlight", text="quoted", page_label="3")]
    path = tmp_path / "a.json"
    original.write_json(path)
    import json
    restored = LibraryItem.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert restored.doi == original.doi
    assert restored.notes[0].text == "hi"
    assert restored.annotations[0].page_label == "3"
    assert restored.attachments == [Path("a.pdf")]
