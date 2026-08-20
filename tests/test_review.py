"""M5 — synthesis artifacts and the rules they must obey."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ask import Corpus_Index  # noqa: E402
from lib.note import SECTIONS, Note  # noqa: E402
from lib.paths import Corpus  # noqa: E402
from review import (  # noqa: E402
    build_bibtex,
    build_brief,
    build_index,
    build_methods_matrix,
    check_contradictions,
    check_draft,
    check_gaps,
)

pytest.importorskip("yaml")


def make_corpus(tmp_path: Path) -> Corpus:
    corpus = Corpus(tmp_path / ".lit")
    corpus.ensure()

    # Two papers with extracted text, so locators can actually be resolved.
    (corpus.text / "doe2024privacy.md").write_text(
        "<!-- page 1 -->\nOur tool achieves 97 percent precision on manual validation of "
        "detected contradictions, with fourteen false positives identified during that "
        "validation.\n"
        "<!-- page 2 -->\nThe method uses supervised classification of policy segments.\n",
        encoding="utf-8")
    (corpus.text / "roe2019contradiction.md").write_text(
        "<!-- page 1 -->\nRe-running the tool, manual validation confirmed only 24 of the "
        "80 detected contradictions as true positives, making 56 false positives.\n"
        "<!-- page 2 -->\nSimplification discards the conditions attached to sharing.\n",
        encoding="utf-8")

    for citekey, fm_extra, sections in (
        ("doe2024privacy",
         {"title": "Privacy Analysis", "year": 2024, "item_type": "conferencePaper",
          "venue": "USENIX Security", "paper_type": "systems", "relevance": "high",
          "methods": ["supervised classification"], "datasets": ["OPP-115"],
          "metrics": ["F1"], "authors": ["Jane Doe"], "doi": "10.1/x"},
         {"One-line summary": "A supervised classifier for policy segments.",
          "Evaluation": "- Reports 97% precision [p. 1].",
          "Connections": "- **contradicts** [[roe2019contradiction]] on precision [p. 1].",
          "Open questions": "- Does this hold outside the benchmark corpus at all?"}),
        ("roe2019contradiction",
         {"title": "Contradiction Detection", "year": 2019, "item_type": "journalArticle",
          "venue": "Computing", "paper_type": "empirical", "relevance": "medium",
          "methods": ["manual re-evaluation"], "datasets": ["OPP-115"],
          "metrics": ["precision"], "authors": ["Ann Roe"], "confidence": "medium"},
         {"One-line summary": "Re-evaluates a contradiction detector.",
          "Evaluation": "- Only 24 of 80 detections were true positives [p. 1]."}),
    ):
        frontmatter = {"citekey": citekey, "scope_version": "v1", "analyzed": "2026-08-20",
                       "confidence": "high", "scope_tags": ["policy"], **fm_extra}
        body = {name: "Not determinable from the text" for name in SECTIONS}
        body.update(sections)
        (corpus.papers / f"{citekey}.md").write_text(
            Note(citekey=citekey, frontmatter=frontmatter, sections=body).to_markdown(),
            encoding="utf-8")
    return corpus


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    return make_corpus(tmp_path)


@pytest.fixture
def index(corpus: Corpus) -> Corpus_Index:
    return Corpus_Index.load(corpus)


# --- mechanical artifacts --------------------------------------------------


def test_index_lists_every_paper(corpus: Corpus, index: Corpus_Index) -> None:
    text = build_index(index, sorted(index.notes))
    assert "doe2024privacy" in text and "roe2019contradiction" in text
    assert "Privacy Analysis" in text
    assert "**Years:** 2019–2024" in text


def test_index_sorts_high_relevance_first(index: Corpus_Index) -> None:
    text = build_index(index, sorted(index.notes))
    assert text.index("doe2024privacy") < text.index("roe2019contradiction")


def test_index_reports_papers_with_no_year(tmp_path: Path) -> None:
    """P4: a missing year silently skews any temporal claim, so it is stated."""
    corpus = make_corpus(tmp_path)
    note_path = corpus.papers / "roe2019contradiction.md"
    note_path.write_text(note_path.read_text(encoding="utf-8").replace("year: 2019", "year: null"),
                         encoding="utf-8")
    text = build_index(Corpus_Index.load(corpus), ["doe2024privacy", "roe2019contradiction"])
    assert "No year recorded" in text and "roe2019contradiction" in text


def test_methods_matrix_groups_datasets(index: Corpus_Index) -> None:
    text = build_methods_matrix(index, sorted(index.notes))
    assert "OPP-115" in text
    assert "`doe2024privacy`" in text and "`roe2019contradiction`" in text


def test_methods_matrix_says_a_blank_is_not_an_absence(index: Corpus_Index) -> None:
    assert "not** that the paper lacks it" in build_methods_matrix(index, sorted(index.notes))


def test_bibtex_uses_the_right_entry_type_and_venue_field(index: Corpus_Index) -> None:
    text = build_bibtex(index, sorted(index.notes))
    assert "@inproceedings{doe2024privacy," in text
    assert "booktitle = {USENIX Security}" in text
    assert "@article{roe2019contradiction," in text
    assert "journal = {Computing}" in text


def test_bibtex_citekeys_match_the_notes(index: Corpus_Index) -> None:
    text = build_bibtex(index, sorted(index.notes))
    for citekey in index.notes:
        assert f"{{{citekey}," in text


# --- the brief -------------------------------------------------------------


def test_brief_collects_contradiction_candidates(index: Corpus_Index) -> None:
    brief = build_brief(index, sorted(index.notes), "SCOPE")
    candidates = brief["contradiction_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["from"] == "doe2024privacy"
    assert candidates[0]["targets"] == ["roe2019contradiction"]


def test_brief_flags_low_confidence_notes(index: Corpus_Index) -> None:
    brief = build_brief(index, sorted(index.notes), "SCOPE")
    assert brief["low_confidence"] == ["roe2019contradiction"]


def test_brief_carries_open_questions(index: Corpus_Index) -> None:
    brief = build_brief(index, sorted(index.notes), "SCOPE")
    assert any("outside the benchmark" in q["text"] for q in brief["open_questions"])


# --- enforcement: contradictions -------------------------------------------


def write_contradictions(corpus: Corpus, body: str) -> None:
    corpus.synthesis.mkdir(parents=True, exist_ok=True)
    (corpus.synthesis / "contradictions.md").write_text(body, encoding="utf-8")


def test_valid_contradiction_passes(corpus: Corpus, index: Corpus_Index) -> None:
    write_contradictions(corpus, """# Contradictions

## Precision disagreement

**Side A.** The tool achieves 97 percent precision on manual validation of detected
contradictions, with fourteen false positives [@doe2024privacy, p. 1].

**Side B.** Manual validation confirmed only 24 of 80 detected contradictions as true
positives, making 56 false positives [@roe2019contradiction, p. 1].
""")
    result = check_contradictions(corpus, index)
    assert result.ok, result.problems
    assert result.checked == 1


def test_contradiction_with_one_locator_is_rejected(corpus: Corpus, index: Corpus_Index) -> None:
    write_contradictions(corpus, """# Contradictions

## Half an argument

Side A reports 97% precision [@doe2024privacy, p. 1]. Side B disagrees.
""")
    result = check_contradictions(corpus, index)
    assert not result.ok
    assert "needs two verified locators" in result.problems[0]


def test_contradiction_citing_one_paper_twice_is_rejected(corpus: Corpus,
                                                          index: Corpus_Index) -> None:
    """Both locators must name distinct papers - a paper cannot contradict itself here."""
    write_contradictions(corpus, """# Contradictions

## Same source twice

Claim one [@doe2024privacy, p. 1]. Claim two [@doe2024privacy, p. 2].
""")
    result = check_contradictions(corpus, index)
    assert not result.ok
    assert "distinct paper" in result.problems[0]


def test_contradiction_with_a_fabricated_page_is_rejected(corpus: Corpus,
                                                          index: Corpus_Index) -> None:
    """The load-bearing test: an invented locator must never ship."""
    write_contradictions(corpus, """# Contradictions

## Invented

Side A [@doe2024privacy, p. 99]. Side B [@roe2019contradiction, p. 1].
""")
    result = check_contradictions(corpus, index)
    assert not result.ok
    assert any("does not exist" in p and "fabricated" in p for p in result.problems)


def test_contradiction_citing_an_absent_paper_is_rejected(corpus: Corpus,
                                                          index: Corpus_Index) -> None:
    write_contradictions(corpus, """# Contradictions

## Outside the corpus

Side A [@doe2024privacy, p. 1]. Side B [@ghost2020missing, p. 1].
""")
    result = check_contradictions(corpus, index)
    assert not result.ok
    assert any("not in the corpus" in p for p in result.problems)


def test_not_an_entry_sections_are_skipped(corpus: Corpus, index: Corpus_Index) -> None:
    write_contradictions(corpus, """# Contradictions

## Precision disagreement

**Side A.** The tool achieves 97 percent precision on manual validation of detected
contradictions [@doe2024privacy, p. 1].

**Side B.** Manual validation confirmed only 24 of 80 detected contradictions as true
positives [@roe2019contradiction, p. 1].

## Rejected candidates

<!-- not-an-entry -->

Things we considered and dropped, with no locators at all.
""")
    result = check_contradictions(corpus, index)
    assert result.ok, result.problems
    assert result.checked == 1


def test_missing_contradictions_file_is_not_a_failure(corpus: Corpus,
                                                      index: Corpus_Index) -> None:
    assert check_contradictions(corpus, index).ok


# --- enforcement: gaps -----------------------------------------------------


def test_gap_without_a_kind_is_rejected(corpus: Corpus) -> None:
    corpus.synthesis.mkdir(parents=True, exist_ok=True)
    (corpus.synthesis / "gaps.md").write_text(
        "# Gaps\n\n## Nobody has studied X\n\nThis is a gap.\n", encoding="utf-8")
    result = check_gaps(corpus)
    assert not result.ok
    assert "which kind of gap" in result.problems[0]


def test_tagged_gap_passes(corpus: Corpus) -> None:
    corpus.synthesis.mkdir(parents=True, exist_ok=True)
    (corpus.synthesis / "gaps.md").write_text(
        "# Gaps\n\n## X is unstudied here\n\nKind: `not-in-this-library`. Explanation.\n",
        encoding="utf-8")
    assert check_gaps(corpus).ok


# --- enforcement: draft ----------------------------------------------------


def write_draft(corpus: Corpus, body: str) -> None:
    corpus.synthesis.mkdir(parents=True, exist_ok=True)
    (corpus.synthesis / "review-draft.md").write_text(body, encoding="utf-8")


def test_draft_paragraph_without_a_locator_is_rejected(corpus: Corpus,
                                                       index: Corpus_Index) -> None:
    write_draft(corpus, "# Draft\n\n## Body\n\n" + "Automated policy analysis has a long "
                "and varied history across several distinct research communities.\n")
    result = check_draft(corpus, index)
    assert not result.ok
    assert "no locator" in result.problems[0]


def test_draft_paragraph_with_unverified_passes(corpus: Corpus,
                                                index: Corpus_Index) -> None:
    """[UNVERIFIED] is the honest answer and must be accepted, not penalised."""
    write_draft(corpus, "# Draft\n\n## Body\n\n" + "Automated policy analysis has a long "
                "and varied history across several research communities [UNVERIFIED].\n")
    assert check_draft(corpus, index).ok


def test_draft_citing_an_unknown_citekey_is_rejected(corpus: Corpus,
                                                     index: Corpus_Index) -> None:
    write_draft(corpus, "# Draft\n\n## Body\n\n" + "A supervised classifier reached high "
                "precision on the benchmark corpus [@ghost2020missing, p. 3].\n")
    result = check_draft(corpus, index)
    assert not result.ok
    assert any("not in the corpus" in p for p in result.problems)


def test_draft_not_an_entry_section_is_exempt(corpus: Corpus, index: Corpus_Index) -> None:
    write_draft(corpus, "# Draft\n\n<!-- not-an-entry -->\n\n"
                "This preamble explains how to read the draft and carries no claims at "
                "all about any paper in the corpus whatsoever.\n\n"
                "## Body\n\nA classifier reached 97% precision [@doe2024privacy, p. 1].\n")
    assert check_draft(corpus, index).ok
