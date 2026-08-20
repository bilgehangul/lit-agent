"""M1 — the probes themselves, and the scope model.

These tests exercise the probes that can run hermetically (no network, no user library).
Network-dependent probes are covered by ``/lit-doctor`` in the real environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import doctor  # noqa: E402
from lib.capabilities import BROKEN, DISABLED, ENABLED, Capabilities  # noqa: E402
from lib.sample_pdf import ensure_sample_pdf, write_sample_pdf  # noqa: E402
from lib.scope import Scope, save  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")


# --- the sample PDF the probes rely on ------------------------------------


def test_sample_pdf_has_text_an_image_and_a_caption(tmp_path: Path) -> None:
    pdf = write_sample_pdf(tmp_path / "sample.pdf")
    doc = pymupdf.open(pdf)
    text = "".join(page.get_text() for page in doc)
    images = [img for page in doc for img in page.get_images(full=True)]
    doc.close()
    assert len(text) > 200, "pdf_text probe asserts >200 chars"
    assert images, "figures probe needs a real embedded raster image"
    assert "Figure 1:" in text, "caption pairing needs something to pair"


def test_sample_pdf_is_cached_not_regenerated(tmp_path: Path) -> None:
    first = ensure_sample_pdf(tmp_path)
    stamp = first.stat().st_mtime_ns
    assert ensure_sample_pdf(tmp_path).stat().st_mtime_ns == stamp


# --- hermetic probes -------------------------------------------------------


def test_probe_pdf_text_passes_and_reports_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(doctor.paths, "CACHE_DIR", tmp_path)
    result = doctor.probe_pdf_text({})
    assert result.ok
    assert result.config["chars"] > 200
    # P2: the detail is evidence, not a checkmark.
    assert "characters" in result.detail


def test_probe_figures_extracts_a_real_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(doctor.paths, "CACHE_DIR", tmp_path)
    result = doctor.probe_figures({})
    assert result.ok and result.config["images"] >= 1


def test_probe_source_without_config_reports_what_it_detected(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "detect_sources", lambda: [
        {"adapter": "zotero_sqlite", "priority": 2, "available": True,
         "detail": "found it", "config": {}}])
    result = doctor.probe_source({})
    assert not result.ok
    assert "zotero_sqlite" in result.detail


def test_probe_source_with_no_library_anywhere(monkeypatch) -> None:
    monkeypatch.setattr(doctor, "detect_sources", lambda: [
        {"adapter": "zotero_api", "priority": 1, "available": False,
         "detail": "not running", "config": {}}])
    result = doctor.probe_source({})
    assert not result.ok
    assert "no library source found" in result.detail


def test_probe_generic_pdf_folder(tmp_path: Path) -> None:
    write_sample_pdf(tmp_path / "paper.pdf")
    result = doctor.probe_source({"adapter": "generic_pdf", "path": str(tmp_path)})
    assert result.ok and "paper.pdf" in result.detail


def test_probe_zotero_sqlite_missing_db_is_unavailable_not_an_error(tmp_path: Path) -> None:
    result = doctor._probe_zotero_sqlite(str(tmp_path / "nope.sqlite"))
    assert not result.ok and result.unavailable


def test_browser_probe_never_self_certifies() -> None:
    """P2: doctor.py cannot drive a browser, so it must not claim the capability works."""
    assert not doctor.probe_browser({}).ok
    assert doctor.probe_browser({"verified_by_skill": True, "evidence": "read title"}).ok


def test_zotero_write_probe_refuses_without_a_scratch_item() -> None:
    """A write probe must never touch a real library by default."""
    result = doctor.probe_zotero_write({"user_id": "1", "api_key": "k"})
    assert not result.ok and "scratch item" in result.detail


def test_a_crashing_probe_does_not_crash_the_doctor(monkeypatch, tmp_path) -> None:
    def boom(cfg):
        raise RuntimeError("probe exploded")
    monkeypatch.setitem(doctor.PROBES, "figures", boom)
    caps = Capabilities(path=tmp_path / "c.json")
    results = doctor.run_probes(caps, only=["figures"])
    assert not results["figures"].ok
    assert "probe exploded" in results["figures"].detail


# --- applying results ------------------------------------------------------


def test_passing_probe_does_not_enable_an_optional_capability(tmp_path: Path) -> None:
    """P3: passing a probe is not consent to turn an optional feature on."""
    caps = Capabilities(path=tmp_path / "c.json")
    doctor.apply_results(caps, {"figures": doctor._ok("worked")})
    assert caps.get("figures").status == DISABLED


def test_passing_probe_enables_a_required_capability(tmp_path: Path) -> None:
    caps = Capabilities(path=tmp_path / "c.json")
    doctor.apply_results(caps, {"pdf_text": doctor._ok("worked")})
    assert caps.get("pdf_text").status == ENABLED


def test_failing_probe_demotes_an_enabled_capability(tmp_path: Path) -> None:
    caps = Capabilities(path=tmp_path / "c.json")
    caps.enable("figures")
    doctor.apply_results(caps, {"figures": doctor._fail("stopped working")})
    assert caps.get("figures").status == BROKEN
    assert "stopped working" in caps.get("figures").last_error


def test_failing_probe_on_a_disabled_capability_keeps_it_disabled(tmp_path: Path) -> None:
    caps = Capabilities(path=tmp_path / "c.json")
    doctor.apply_results(caps, {"grobid": doctor._fail("no server")})
    st = caps.get("grobid")
    assert st.status == DISABLED and "no server" in st.last_error


# --- scope -----------------------------------------------------------------


def test_scope_version_changes_only_when_answers_change() -> None:
    a = Scope(field="CS", research_question="How do LLMs analyze privacy policies at scale?")
    b = Scope(field="CS", research_question="How do LLMs analyze privacy policies at scale?")
    assert a.version == b.version
    b.research_question = "Something else entirely, asked at similar length."
    assert a.version != b.version


def test_scope_version_ignores_the_created_timestamp() -> None:
    a = Scope(field="CS", research_question="A sufficiently specific question about X.")
    b = Scope(field="CS", research_question="A sufficiently specific question about X.",
              created="2026-01-01T00:00:00Z")
    assert a.version == b.version


def test_scope_flags_a_vague_research_question() -> None:
    problems = Scope(field="CS", research_question="LLMs and privacy").problems()
    assert any("research_question is very short" in p for p in problems)


def test_scope_flags_missing_essentials() -> None:
    scope = Scope()
    assert not scope.is_usable()
    assert any("research_question is empty" in p for p in scope.problems())


def test_scope_roundtrips_through_config(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    from lib import scope as scope_mod
    original = Scope(field="CS", subfield="privacy",
                     research_question="How well do LLMs detect contradictions in policies?",
                     purpose="related_work", what_matters=["methods"],
                     vocabulary={"key_terms": ["contextual integrity"]})
    config = tmp_path / "config.yaml"
    version = save(original, config)
    loaded = scope_mod.load(config)
    assert loaded is not None
    assert loaded.version == version == original.version
    assert loaded.research_question == original.research_question
    assert (tmp_path / "scope.md").is_file()


def test_saving_scope_preserves_other_config_sections(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"source": {"adapter": "zotero_sqlite"}}), encoding="utf-8")
    save(Scope(field="CS", research_question="A specific enough question about things."), config)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["source"] == {"adapter": "zotero_sqlite"}
    assert data["scope"]["field"] == "CS"


def test_prompt_block_carries_the_question_and_vocabulary() -> None:
    scope = Scope(field="CS", subfield="privacy",
                  research_question="How do LLMs handle policy contradictions?",
                  what_matters=["methods", "empirical_results"],
                  vocabulary={"key_terms": ["contextual integrity", "GDPR"]})
    block = scope.prompt_block()
    assert "How do LLMs handle policy contradictions?" in block
    assert "contextual integrity" in block
    assert "methods, empirical results" in block


def test_fixture_scope_is_marked_as_such() -> None:
    scope = Scope(field="CS", research_question="A development fixture question about X.",
                  is_fixture=True)
    assert "development fixture" in scope.prompt_block()
    assert "development fixture" in scope.to_markdown()


def test_artifacts_offered_respect_capabilities() -> None:
    from lib.scope import available_artifacts
    assert "figures" not in available_artifacts(set())
    assert "figures" in available_artifacts({"figures"})
