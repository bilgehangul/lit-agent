"""The research scope block (spec section 7).

The interview itself is conversational and lives in ``skills/lit-scope``. This module owns
the durable half: validating what the interview produced, versioning it, persisting it to
``.lit/config.yaml``, rendering the hand-editable ``.lit/scope.md``, and producing the
prompt block that every downstream template injects.

**Why the version matters.** Notes written under one scope are not comparable with notes
written under another. Every per-paper note is stamped with ``scope_version``; when the
scope changes, the version changes, and ``/lit-analyze`` can tell which notes are stale
instead of silently mixing outputs generated under different questions (spec section 7).
"""

from __future__ import annotations

import hashlib
import json
# `field` is a Scope attribute name (the research field), so the dataclasses helper
# is imported under an alias to avoid shadowing it inside the class body.
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Ranked answers to "what matters in a paper" (spec section 7, question 5).
WHAT_MATTERS = (
    "methods", "empirical_results", "datasets", "theory",
    "limitations", "reproducibility", "deployment",
)

#: Desired artifacts (question 6). Offered only when capabilities support them.
ARTIFACTS = (
    "per_paper_notes", "methods_matrix", "thematic_synthesis", "gap_analysis",
    "review_draft", "figures", "bibtex_subset",
)

PURPOSES = (
    "related_work", "survey_paper", "thesis_chapter", "methods_scouting",
    "grant_background", "staying_current",
)

STAGES = ("starting_cold", "refining_draft", "filling_gaps")


@dataclass
class Scope:
    """A research scope. Every field maps to a question in spec section 7."""

    field: str = ""                       # 1. field and subfield
    subfield: str = ""
    venue_vocabulary: str = ""
    research_question: str = ""           # 2. the actual question
    purpose: str = ""                     # 3.
    stage: str = ""                       # 4.
    what_matters: list[str] = dc_field(default_factory=list)      # 5. ranked
    artifacts: list[str] = dc_field(default_factory=list)         # 6. multi-select
    exclusions: dict[str, Any] = dc_field(default_factory=dict)   # 7. years, venues, types
    vocabulary: dict[str, Any] = dc_field(default_factory=dict)   # 8. terms, synonyms, groups
    voice: dict[str, Any] = dc_field(default_factory=dict)        # 9. prose style
    created: str = ""
    #: Set to True for development fixtures so generated notes are never mistaken for
    #: output produced from a real interview.
    is_fixture: bool = False

    # --- versioning --------------------------------------------------------

    def fingerprint(self) -> str:
        """Stable hash of the semantic content. Changes only when the answers change."""
        payload = {k: v for k, v in asdict(self).items() if k not in ("created", "version")}
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    @property
    def version(self) -> str:
        return self.fingerprint()

    # --- validation --------------------------------------------------------

    def problems(self) -> list[str]:
        """Answers that would degrade every downstream output if left as they are."""
        issues: list[str] = []
        if not self.research_question.strip():
            issues.append("research_question is empty; every downstream output depends on it")
        elif len(self.research_question.split()) < 6:
            issues.append("research_question is very short. Spec section 7 asks for "
                          "specificity here because a vague question degrades every "
                          "downstream output.")
        if not self.field.strip():
            issues.append("field is empty")
        if self.purpose and self.purpose not in PURPOSES:
            issues.append(f"purpose {self.purpose!r} is not one of {', '.join(PURPOSES)}")
        if self.stage and self.stage not in STAGES:
            issues.append(f"stage {self.stage!r} is not one of {', '.join(STAGES)}")
        for item in self.what_matters:
            if item not in WHAT_MATTERS:
                issues.append(f"what_matters entry {item!r} is not recognized")
        for item in self.artifacts:
            if item not in ARTIFACTS:
                issues.append(f"artifact {item!r} is not recognized")
        return issues

    def is_usable(self) -> bool:
        return not any(p.startswith(("research_question is empty", "field is empty"))
                       for p in self.problems())

    # --- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["version"] = self.version
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scope":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    # --- the block every prompt injects ------------------------------------

    def prompt_block(self) -> str:
        """Rendered scope, injected verbatim into every analysis and synthesis prompt.

        Kept compact: it is prepended to hundreds of calls, so wasted tokens here are
        multiplied across the whole corpus.
        """
        lines = [
            "RESEARCH SCOPE",
            f"Field: {self.field}" + (f" / {self.subfield}" if self.subfield else ""),
            f"Research question: {self.research_question}",
        ]
        if self.purpose:
            lines.append(f"Purpose of this pass: {self.purpose.replace('_', ' ')}")
        if self.stage:
            lines.append(f"Stage: {self.stage.replace('_', ' ')}")
        if self.what_matters:
            lines.append("What matters most, in order: "
                         + ", ".join(w.replace("_", " ") for w in self.what_matters))
        if terms := self.vocabulary.get("key_terms"):
            lines.append("Key terms: " + ", ".join(terms))
        if syn := self.vocabulary.get("synonyms"):
            pairs = syn.items() if isinstance(syn, dict) else []
            if pairs:
                lines.append("Competing terminology: "
                             + "; ".join(f"{k} = {', '.join(v)}" for k, v in pairs))
        if groups := self.vocabulary.get("author_groups"):
            lines.append("Known author groups: " + ", ".join(groups))
        if self.exclusions:
            parts = [f"{k}: {v}" for k, v in self.exclusions.items() if v]
            if parts:
                lines.append("Deprioritize -- " + "; ".join(parts))
        if tone := self.voice.get("tone"):
            lines.append(f"Prose voice: {tone}")
        if self.is_fixture:
            lines.append("(NOTE: development fixture scope, not a real interview.)")
        return "\n".join(lines)

    # --- the hand-editable rendering ---------------------------------------

    def to_markdown(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {i.replace('_', ' ')}" for i in items) or "- (none given)"

        excl = "\n".join(f"- **{k}:** {v}" for k, v in self.exclusions.items() if v) \
            or "- (none given)"
        vocab_lines = []
        if terms := self.vocabulary.get("key_terms"):
            vocab_lines.append("**Key terms:** " + ", ".join(terms))
        if syn := self.vocabulary.get("synonyms"):
            if isinstance(syn, dict):
                for k, v in syn.items():
                    vocab_lines.append(f"**{k}** is also called: " + ", ".join(v))
        if groups := self.vocabulary.get("author_groups"):
            vocab_lines.append("**Author groups:** " + ", ".join(groups))

        fixture_note = ("\n> **This is a development fixture, not a real interview.** "
                        "Run `/lit-scope` to replace it.\n" if self.is_fixture else "")

        return f"""# Research scope

`scope_version: {self.version}` · created {self.created or "unknown"}
{fixture_note}
Edit this file by hand if you like, then run `/lit-scope --import` to fold your edits back
into `config.yaml`. Changing anything here changes the scope version, which marks every
note generated under the old scope as stale rather than silently mixing them.

## Field

{self.field}{" / " + self.subfield if self.subfield else ""}

{self.venue_vocabulary or ""}

## Research question

{self.research_question or "_(not set)_"}

## Purpose and stage

- **Purpose of this pass:** {self.purpose.replace('_', ' ') or "_(not set)_"}
- **Stage:** {self.stage.replace('_', ' ') or "_(not set)_"}

## What matters in a paper, ranked

{bullets(self.what_matters)}

## Desired artifacts

{bullets(self.artifacts)}

## Exclusion criteria

{excl}

## Vocabulary

{chr(10).join(vocab_lines) or "- (none given)"}

## Voice

{self.voice.get("tone") or "_(not set)_"}
{("Match style against: " + self.voice["match_manuscript"]) if self.voice.get("match_manuscript") else ""}
"""


# --- persistence -----------------------------------------------------------


def load(config_path: Path) -> Scope | None:
    """Read the scope out of ``.lit/config.yaml``. Returns None when absent."""
    if not config_path.is_file():
        return None
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw = data.get("scope")
    return Scope.from_dict(raw) if raw else None


def save(scope: Scope, config_path: Path, extra: dict[str, Any] | None = None) -> str:
    """Merge the scope into ``.lit/config.yaml`` and return the scope version.

    Other sections of config.yaml (source config, output prefs) are preserved.
    """
    import yaml
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if config_path.is_file():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not scope.created:
        scope.created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["scope"] = scope.to_dict()
    if extra:
        data.update(extra)
    config_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8")
    (config_path.parent / "scope.md").write_text(scope.to_markdown(), encoding="utf-8")
    return scope.version


def available_artifacts(enabled_capabilities: set[str]) -> list[str]:
    """Only offer artifacts the current capabilities can actually produce (spec section 7).

    Nothing is offered that would produce an empty file -- that is the silent gap P4 forbids.
    """
    offered = ["per_paper_notes", "methods_matrix", "thematic_synthesis",
               "gap_analysis", "review_draft", "bibtex_subset"]
    if "figures" in enabled_capabilities:
        offered.append("figures")
    return offered
