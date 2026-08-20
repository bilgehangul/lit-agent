"""M1 — capability state machine and runtime gating.

The acceptance criterion for M1 is that a fresh user can run setup, decline every optional
capability, and land in a valid enabled state. ``test_zero_optional_path`` is that criterion
expressed as a test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.capabilities import (  # noqa: E402
    BROKEN,
    DISABLED,
    ENABLED,
    FAILURE_BUDGET,
    REGISTRY,
    REQUIRED_IDS,
    Capabilities,
    CapabilityError,
    format_table,
    gate,
)


@pytest.fixture
def caps(tmp_path: Path) -> Capabilities:
    return Capabilities(path=tmp_path / "capabilities.json")


# --- P1: no half-configured state -----------------------------------------


def test_unknown_capability_defaults_to_disabled(caps: Capabilities) -> None:
    assert caps.get("figures").status == DISABLED
    assert not caps.is_enabled("figures")


def test_every_registry_entry_has_a_state(caps: Capabilities) -> None:
    for spec in REGISTRY:
        assert caps.get(spec.id).status in (ENABLED, DISABLED, BROKEN)


def test_unrecognized_status_on_disk_reads_as_disabled(tmp_path: Path) -> None:
    """A garbage status must not become a third state the user sits in unknowingly."""
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps({
        "version": 1,
        "capabilities": {"figures": {"status": "probably-fine"}},
    }), encoding="utf-8")
    assert Capabilities.load(path).get("figures").status == DISABLED


def test_corrupt_file_is_reported_not_silently_empty(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = Capabilities.load(path)
    assert loaded.load_error is not None          # P4: the reason survives
    assert not loaded.is_enabled("pdf_text")      # and nothing is trusted


def test_save_is_atomic_and_roundtrips(caps: Capabilities) -> None:
    caps.enable("figures", {"images": 1})
    caps.save()
    reloaded = Capabilities.load(caps.path)
    assert reloaded.is_enabled("figures")
    assert reloaded.get("figures").config == {"images": 1}
    assert reloaded.get("figures").last_verified is not None
    # No stray temp files left behind.
    assert [p.name for p in caps.path.parent.iterdir()] == ["capabilities.json"]


def test_saved_file_only_contains_known_capabilities(caps: Capabilities) -> None:
    caps.states["made_up"] = caps.get("made_up")
    caps.save()
    written = json.loads(caps.path.read_text(encoding="utf-8"))
    assert "made_up" not in written["capabilities"]


# --- transitions -----------------------------------------------------------


def test_enable_clears_previous_error(caps: Capabilities) -> None:
    caps.mark_broken("figures", "exploded")
    caps.enable("figures")
    assert caps.get("figures").last_error is None
    assert caps.is_enabled("figures")


def test_disable_records_a_reason(caps: Capabilities) -> None:
    caps.disable("grobid", "user declined")
    st = caps.get("grobid")
    assert st.status == DISABLED and st.last_error == "user declined"


def test_three_strikes_demotes_and_never_spins(caps: Capabilities) -> None:
    """Spec section 5: three in-run failures demote to broken. Never retry forever."""
    caps.enable("figures")
    for attempt in range(1, FAILURE_BUDGET):
        assert caps.record_failure("figures", "boom") is False
        assert caps.is_enabled("figures"), f"demoted early on attempt {attempt}"
    assert caps.record_failure("figures", "boom") is True
    assert caps.get("figures").status == BROKEN
    assert caps.demotions == [("figures", "boom")]      # reported in the run summary (P4)


def test_failure_count_resets_when_reenabled(caps: Capabilities) -> None:
    caps.enable("figures")
    caps.record_failure("figures", "boom")
    caps.enable("figures")
    assert caps.failure_count("figures") == 0


# --- runtime gating --------------------------------------------------------


def test_gate_passes_when_enabled(caps: Capabilities) -> None:
    caps.enable("pdf_text")
    assert gate(["pdf_text"], caps) is caps


def test_gate_raises_naming_what_is_missing_and_the_fix(caps: Capabilities) -> None:
    with pytest.raises(CapabilityError) as excinfo:
        gate(["pdf_text", "figures"], caps)
    message = str(excinfo.value)
    assert excinfo.value.missing == ["pdf_text", "figures"]
    # The user gets the one command that fixes it, per capability.
    assert "/lit-setup --reconfigure pdf_text" in message
    assert "/lit-setup --reconfigure figures" in message


def test_gate_surfaces_the_last_error(caps: Capabilities) -> None:
    caps.enable("figures")
    caps.mark_broken("figures", "pymupdf blew up on page 3")
    with pytest.raises(CapabilityError) as excinfo:
        gate(["figures"], caps)
    assert "pymupdf blew up on page 3" in str(excinfo.value)


def test_broken_capability_does_not_satisfy_a_gate(caps: Capabilities) -> None:
    caps.mark_broken("pdf_text", "nope")
    with pytest.raises(CapabilityError):
        gate(["pdf_text"], caps)


# --- M1 acceptance ---------------------------------------------------------


def test_zero_optional_path(caps: Capabilities) -> None:
    """M1 acceptance: decline every optional capability, land in a valid enabled state.

    This is the path spec P3 calls the one that has to be bulletproof.
    """
    for cid in REQUIRED_IDS:
        caps.enable(cid)
    for spec in REGISTRY:
        if not spec.required:
            caps.disable(spec.id, "user declined at setup")

    caps.save()
    reloaded = Capabilities.load(caps.path)

    # Required on, everything else explicitly off -- no third state anywhere (P1).
    assert set(reloaded.enabled_ids()) == set(REQUIRED_IDS)
    for spec in REGISTRY:
        assert reloaded.get(spec.id).status in (ENABLED, DISABLED)

    # The core pipeline's gates all pass.
    gate(["pdf_text"], reloaded)
    gate(["source"], reloaded)
    gate(["python_env", "source", "pdf_text"], reloaded)

    # And an optional-capability command refuses cleanly rather than half-running.
    with pytest.raises(CapabilityError):
        gate(["figures"], reloaded)


def test_status_table_lists_every_capability(caps: Capabilities) -> None:
    caps.enable("pdf_text")
    caps.mark_broken("figures", "an error worth showing")
    table = format_table(caps)
    for spec in REGISTRY:
        assert spec.title in table
    assert "BROKEN" in table
    assert "an error worth showing" in table
