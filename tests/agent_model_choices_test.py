"""agent.model_choices — the picker document: floor flags, defaults, fail-open catalogue."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.model_choices
import agent.phases
import agent.pi_models
from agent.registry import PHASES
from core.phases import Phase, PhaseDomain, Tier

from workbench_config_support import roots, _write  # noqa: F401

LISTING = """\
provider              model                               context  max-out
anthropic             claude-sonnet-5-5                   200K     64K
anthropic             claude-haiku-4-5                    200K     64K
google-vertex         gemini-2.5-flash                    1.0M     65.5K
"""

_MODELS = (
    "models:\n"
    "  light: anthropic/claude-haiku-4-5\n"
    "  standard: anthropic/claude-sonnet-5-5\n"
    "  deep: anthropic/claude-opus-5\n"
)


def _doc(roots, monkeypatch, listing=LISTING):
    _write(roots[0] / "config.yml", _MODELS)
    if listing is not None:
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: listing)
    return agent.model_choices.model_choices(roots[1])


def _phase(doc, phase: Phase) -> dict:
    return doc.as_json()["phases"][phase.value]


def test_the_document_is_schema_1_with_every_registry_phase(roots, monkeypatch):
    payload = _doc(roots, monkeypatch).as_json()
    assert payload["schema"] == 1
    assert list(payload["phases"]) == [p.value for p in PHASES]
    assert list(payload["tiers"]) == [t.value for t in Tier]


def test_review_floor_follows_the_phase_domain(roots, monkeypatch):
    payload = _doc(roots, monkeypatch).as_json()
    for phase, spec in PHASES.items():
        assert payload["phases"][phase.value]["review_floor"] is (
            spec.domain is PhaseDomain.REVIEW)


def test_a_haiku_catalogue_row_is_not_review_ok(roots, monkeypatch):
    providers = _doc(roots, monkeypatch).as_json()["catalogue"]["providers"]
    by_id = {m["id"]: m for p in providers for m in p["models"]}
    assert by_id["claude-haiku-4-5"]["review_ok"] is False
    assert by_id["claude-haiku-4-5"]["ref"] == "anthropic/claude-haiku-4-5"
    assert by_id["claude-sonnet-5-5"]["review_ok"] is True
    assert by_id["gemini-2.5-flash"]["context"] == 1_000_000


def test_the_light_tier_is_not_review_ok(roots, monkeypatch):
    tiers = _doc(roots, monkeypatch).as_json()["tiers"]
    assert tiers["light"]["review_ok"] is False
    assert tiers["light"]["ref"] == "anthropic/claude-haiku-4-5"
    assert tiers["standard"]["review_ok"] is True
    assert tiers["deep"]["review_ok"] is True


def test_defaults_come_from_the_phase_report(roots, monkeypatch):
    scout = _phase(_doc(roots, monkeypatch), Phase.SCOUT)
    assert scout["domain"] == "review"
    assert scout["tier"] == "standard"
    assert scout["error"] is None
    assert scout["default"] == {
        "ref": "anthropic/claude-sonnet-5-5",
        "source_key": "models.standard",
    }
    describe = _phase(_doc(roots, monkeypatch), Phase.DESCRIBE)
    assert describe["review_floor"] is False
    assert describe["default"]["ref"] == "anthropic/claude-haiku-4-5"


def test_an_unreadable_catalogue_is_ok_false(roots):
    payload = agent.model_choices.model_choices(roots[1]).as_json()
    assert payload["catalogue"]["ok"] is False
    assert payload["catalogue"]["providers"] == []
    assert payload["catalogue"]["error"]
    assert payload["schema"] == 1


def test_enabled_lists_the_tier_refs(roots, monkeypatch):
    enabled = _doc(roots, monkeypatch).as_json()["enabled"]
    assert "anthropic/claude-sonnet-5-5" in enabled
    assert "anthropic/claude-haiku-4-5" in enabled
