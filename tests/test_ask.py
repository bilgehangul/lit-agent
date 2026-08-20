"""M4 — retrieval over the corpus."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ask import (  # noqa: E402
    Corpus_Index,
    apply_filters,
    parse_filter,
    query_terms,
    score_note,
    search,
)
from lib.note import Note  # noqa: E402
from lib.paths import Corpus  # noqa: E402

pytest.importorskip("yaml")


def write_note(corpus: Corpus, citekey: str, **overrides) -> None:
    frontmatter = {
        "citekey": citekey, "title": overrides.pop("title", "A Paper About Things"),
        "year": overrides.pop("year", 2024),
        "relevance": overrides.pop("relevance", "high"),
        "paper_type": overrides.pop("paper_type", "empirical"),
        "scope_tags": overrides.pop("scope_tags", []),
        "methods": overrides.pop("methods", []),
        "scope_version": "v1", "analyzed": "2026-08-20",
    }
    frontmatter.update(overrides.pop("frontmatter", {}))
    sections = {name: "Not determinable from the text"
                for name in __import__("lib.note", fromlist=["SECTIONS"]).SECTIONS}
    sections.update(overrides.pop("sections", {}))
    note = Note(citekey=citekey, frontmatter=frontmatter, sections=sections)
    corpus.papers.mkdir(parents=True, exist_ok=True)
    (corpus.papers / f"{citekey}.md").write_text(note.to_markdown(), encoding="utf-8")


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    c = Corpus(tmp_path / ".lit")
    c.ensure()
    write_note(c, "doe2024privacy", title="Privacy Policy Analysis with Language Models",
               year=2024, relevance="high", paper_type="empirical",
               scope_tags=["prompt-engineering"],
               sections={"One-line summary": "Evaluates prompted models on policy annotation.",
                         "Key findings": "- Prompting underperforms supervised baselines [p. 4]."})
    write_note(c, "roe2019contradiction", title="Detecting Contradictions in Policies",
               year=2019, relevance="medium", paper_type="systems",
               scope_tags=["policy-contradiction"],
               sections={"One-line summary": "A rule-based contradiction detector.",
                         "Key findings": "- Reports 97% precision on manual validation [p. 9]."})
    write_note(c, "poe2016corpus", title="A Corpus of Annotated Policies",
               year=2016, relevance="high", paper_type="dataset",
               sections={"One-line summary": "Builds an annotated privacy policy corpus."})
    return c


# --- filters ---------------------------------------------------------------


@pytest.mark.parametrize("expr,expected", [
    ("relevance=high", ("relevance", "=", "high")),
    ("year>=2020", ("year", ">=", "2020")),
    ("paper_type!=survey", ("paper_type", "!=", "survey")),
])
def test_parse_filter(expr, expected) -> None:
    assert parse_filter(expr) == expected


def test_bad_filter_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="cannot parse filter"):
        parse_filter("relevance high")


def test_filter_on_equality(corpus: Corpus) -> None:
    index = Corpus_Index.load(corpus)
    kept, _ = apply_filters(index, ["relevance=high"])
    assert kept == ["doe2024privacy", "poe2016corpus"]


def test_filter_on_numeric_comparison(corpus: Corpus) -> None:
    index = Corpus_Index.load(corpus)
    kept, _ = apply_filters(index, ["year>=2020"])
    assert kept == ["doe2024privacy"]


def test_filters_combine_as_and(corpus: Corpus) -> None:
    index = Corpus_Index.load(corpus)
    kept, _ = apply_filters(index, ["relevance=high", "year<2020"])
    assert kept == ["poe2016corpus"]


def test_list_valued_field_matches_any_element(corpus: Corpus) -> None:
    index = Corpus_Index.load(corpus)
    kept, _ = apply_filters(index, ["scope_tags=policy-contradiction"])
    assert kept == ["roe2019contradiction"]


def test_unknown_field_is_reported_not_silent(corpus: Corpus) -> None:
    """P4: a typo in a filter must not look like an empty corpus."""
    index = Corpus_Index.load(corpus)
    kept, messages = apply_filters(index, ["relevence=high"])
    assert kept == []
    assert any("relevence" in m for m in messages)


# --- scoring ---------------------------------------------------------------


def test_query_terms_drop_stopwords_and_keep_phrases() -> None:
    terms = query_terms('what does the "chain of thought" prompting do')
    assert "chain of thought" in terms
    assert "prompting" in terms
    assert "does" not in terms and "the" not in terms


def test_title_matches_outweigh_body_matches(corpus: Corpus) -> None:
    """Same note, two terms: one only in the title, one only in the body."""
    index = Corpus_Index.load(corpus)
    note, body = index.notes["doe2024privacy"], index.bodies["doe2024privacy"]
    in_title, _, _ = score_note(note, body, ["language"])      # title only
    in_body, _, _ = score_note(note, body, ["supervised"])     # key findings only
    assert in_title > in_body


def test_stemming_matches_word_forms() -> None:
    """A researcher types "policies"; the note says "policy". They must still match."""
    from ask import stem
    assert stem("policies") == stem("policy") or stem("policies") in "policy"
    index_terms = query_terms("contradictions in policies")
    assert any(_matches_form(t, "contradiction") for t in index_terms)


def _matches_form(term: str, target: str) -> bool:
    from ask import _count
    return _count(target, term) > 0


def test_matching_more_distinct_terms_scores_higher(corpus: Corpus) -> None:
    index = Corpus_Index.load(corpus)
    note, body = index.notes["doe2024privacy"], index.bodies["doe2024privacy"]
    broad, broad_terms, _ = score_note(note, body, ["privacy", "policy", "language", "models"])
    narrow, narrow_terms, _ = score_note(note, body, ["privacy"])
    assert len(broad_terms) > len(narrow_terms)
    assert broad > narrow


def test_snippets_explain_why_a_note_matched(corpus: Corpus) -> None:
    index = Corpus_Index.load(corpus)
    _, _, snippets = score_note(index.notes["roe2019contradiction"],
                                index.bodies["roe2019contradiction"], ["precision"])
    assert snippets
    assert any("97%" in text for _, text in snippets)


# --- search ----------------------------------------------------------------


def test_search_ranks_the_right_paper_first(corpus: Corpus) -> None:
    hits, _, total = search(corpus, "contradiction detection precision")
    assert total == 3
    assert hits[0].citekey == "roe2019contradiction"


def test_search_excludes_non_matching_notes(corpus: Corpus) -> None:
    hits, _, _ = search(corpus, "contradiction")
    assert "poe2016corpus" not in [h.citekey for h in hits]


def test_search_combines_filter_and_query(corpus: Corpus) -> None:
    hits, _, _ = search(corpus, "policies", filters=["year>=2020"])
    assert [h.citekey for h in hits] == ["doe2024privacy"]


def test_no_match_says_the_corpus_may_not_cover_it(corpus: Corpus) -> None:
    """The distinction between 'not covered' and 'filtered out' drives the answer."""
    hits, messages, _ = search(corpus, "quantum cryptography lattice")
    assert hits == []
    assert any("may simply not cover" in m for m in messages)


def test_empty_query_returns_the_filtered_corpus(corpus: Corpus) -> None:
    hits, _, _ = search(corpus, "", filters=["relevance=high"], top=100)
    assert sorted(h.citekey for h in hits) == ["doe2024privacy", "poe2016corpus"]


def test_hits_carry_frontmatter_for_the_answer(corpus: Corpus) -> None:
    hits, _, _ = search(corpus, "contradiction")
    hit = hits[0]
    assert hit.year == 2019
    assert hit.paper_type == "systems"
    assert hit.relevance == "medium"
    assert hit.path.endswith("roe2019contradiction.md")


def test_empty_corpus_is_not_an_error(tmp_path: Path) -> None:
    empty = Corpus(tmp_path / ".lit")
    empty.ensure()
    hits, _, total = search(empty, "anything")
    assert hits == [] and total == 0
