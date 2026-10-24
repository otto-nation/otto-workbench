"""Model resolution in `agent.phases`: per-phase models and the env behind them."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401


# ── 19. the env model layers ──────────────────────────────────────────────────────


@pytest.fixture
def no_model_env(monkeypatch):
    """A clean slate — the developer's own shell usually has these set."""
    for key in ("WORKBENCH_AI_MODEL", "UNUSED_KEY", "MY_MODEL_KEY",
                "AI_SONNET_MODEL", "AI_OPUS_MODEL", "AI_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        monkeypatch.delenv(key, raising=False)


TIER_ENV = (
    "AI_SONNET_MODEL", "AI_OPUS_MODEL", "AI_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)


class TestResolveModel:
    """The env layers above the config, through the one public resolver.

    Their order against each other and against the config is pinned layer by
    layer in workbench_config_phases_test; these are the two properties that
    are not.
    """

    def test_tier_env_vars_are_not_read(self, ro, no_model_env, monkeypatch):
        """The Claude tier variables feed the Claude Code harness, not dispatch.

        A Claude tier word anywhere in the chain is an ordinary model id, so a
        machine still exporting AI_SONNET_MODEL does not see it rewritten.
        """
        for key in TIER_ENV:
            monkeypatch.setenv(key, "claude-sonnet-5-5")
        assert ro.phase_model("scout", "") is None
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "sonnet")
        assert ro.phase_model("scout", "") == "sonnet"

    def test_the_alias_layer_is_gone(self, ro):
        assert not hasattr(ro, "ModelAlias")
        assert not hasattr(ro, "resolve_alias")


# ── 19b. phase_model / collect_phase_models ─────────────────────────────────


class TestPhaseModel:
    def _clean_env(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        for phase in ro.Phase:
            monkeypatch.delenv(phase.model_env_key, raising=False)
        for key in TIER_ENV:
            monkeypatch.delenv(key, raising=False)

    def test_phase_env_keys_follow_convention(self, ro):
        assert ro.Phase.SCOUT.model_env_key == "WORKBENCH_AI_SCOUT_MODEL"
        assert ro.Phase.SCOUT.thinking_env_key == "WORKBENCH_AI_SCOUT_THINKING"

    def test_no_model_when_nothing_is_configured(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        assert ro.phase_model("scout", "") is None

    def test_tier_env_does_not_supply_a_default(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("AI_SONNET_MODEL", "claude-sonnet-5-5")
        assert ro.phase_model("scout", "") is None

    def test_env_key_derived_from_phase_name(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "") == "claude-haiku-4-5"
        assert ro.phase_model("group", "") is None

    def test_explicit_overrides_env(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "claude-opus-5") == "claude-opus-5"

    def test_collect_reads_the_worktree_config_when_given_one(
        self, ro, monkeypatch, tmp_path,
    ):
        """Preflight must resolve the models the review will actually run.

        `ReviewJob.config` is project- and container-scoped, so a repo setting
        a tier in its own `.workbench.yml` runs a model the global scope never
        names. Checking without the worktree cleared a model the review
        never uses and missed the one it does — the unknown-window failure then
        landed inside `build_prompt`, after metadata and collection were paid
        for, which is the cost the preflight check exists to avoid.
        """
        self._clean_env(ro, monkeypatch)
        (tmp_path / ".workbench.yml").write_text(
            "models:\n  deep: claude-haiku-4-5\n  standard: claude-haiku-4-5\n"
            "  light: claude-haiku-4-5\n",
        )
        models = ro.collect_phase_models("", tmp_path)
        assert set(models) == {"claude-haiku-4-5"}
        assert "sonnet" not in models

    def test_collect_groups_phases_by_model(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        models = ro.collect_phase_models("")
        assert models["claude-haiku-4-5"] == ["scout"]
        assert set(models["claude-sonnet-5"]) == set(ro.REVIEW_PHASES) - {"scout"}

    def test_collect_covers_every_review_phase_with_a_model(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-sonnet-5")
        models = ro.collect_phase_models("")
        phases = [p for group in models.values() for p in group]
        assert sorted(phases) == sorted(ro.REVIEW_PHASES)

    def test_collect_skips_phases_with_no_model(self, ro, monkeypatch):
        """An unset model is not a model to preflight; the budget floor covers it."""
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.collect_phase_models("") == {"claude-haiku-4-5": [ro.Phase.SCOUT]}

    def test_unset_review_phases_names_the_rest(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        unset = ro.unset_review_phases("")
        assert ro.Phase.SCOUT not in unset
        assert sorted(unset) == sorted(set(ro.REVIEW_PHASES) - {ro.Phase.SCOUT})
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "claude-sonnet-5")
        assert ro.unset_review_phases("") == []

    def test_a_phase_from_another_entry_point_is_not_preflighted(self, ro, monkeypatch):
        """Preflight resolves the models a *review* is about to run.

        A fix pass belonging to `pr comments` or `pr ci` never runs here, so a
        bad model pinned on it must not fail a review before it starts.
        """
        self._clean_env(ro, monkeypatch)
        models = ro.collect_phase_models("")
        named = {p for group in models.values() for p in group}
        assert not named & {ro.Phase.COMMENTS_FIX, ro.Phase.CI_FIX}
