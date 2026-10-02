"""Model resolution in `agent.phases`: aliases, per-phase models, and the env behind them."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401


# ── 19. resolve_model ──────────────────────────────────────────────────────


@pytest.fixture
def no_model_env(monkeypatch):
    """A clean slate — the developer's own shell usually has these set."""
    for key in ("WORKBENCH_AI_MODEL", "UNUSED_KEY", "MY_MODEL_KEY",
                "AI_SONNET_MODEL", "AI_OPUS_MODEL", "AI_HAIKU_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        monkeypatch.delenv(key, raising=False)


class TestResolveModel:
    def _clear_alias_envs(self, ro, monkeypatch):
        for alias in ro.ModelAlias:
            monkeypatch.delenv(alias.env_key, raising=False)
            monkeypatch.delenv(alias.legacy_env_key, raising=False)

    def test_alias_env_keys_follow_convention(self, ro):
        assert ro.ModelAlias.SONNET.env_key == "AI_SONNET_MODEL"
        assert ro.ModelAlias.OPUS.env_key == "AI_OPUS_MODEL"
        assert ro.ModelAlias.HAIKU.env_key == "AI_HAIKU_MODEL"

    def test_legacy_alias_env_keys_are_the_pre_rename_names(self, ro):
        assert ro.ModelAlias.SONNET.legacy_env_key == "ANTHROPIC_DEFAULT_SONNET_MODEL"
        assert ro.ModelAlias.OPUS.legacy_env_key == "ANTHROPIC_DEFAULT_OPUS_MODEL"
        assert ro.ModelAlias.HAIKU.legacy_env_key == "ANTHROPIC_DEFAULT_HAIKU_MODEL"

    def test_alias_env_keys_match_the_registry(self, ro):
        """ai/models.env.yml is the SSOT for these names, and this pins them to it.

        The bug this guards was exactly a drift between the two: the 20260908
        migration renamed the registry's `var` fields and `resolve_alias` kept
        reading the old spelling, so every tier alias silently stopped
        resolving and phases dispatched the bare word `sonnet` as a model id.
        `var` is what ~/.env.local sets; `target` is what the Claude Code
        settings mirror publishes, which is the fallback.
        """
        from config.workbench_config import read_yaml

        registry = Path(__file__).resolve().parent.parent / "ai" / "models.env.yml"
        entries = read_yaml(registry)["env"]
        by_var = {e["var"]: e for e in entries if e.get("role") == "model-tier"}

        assert len(by_var) == len(list(ro.ModelAlias)), (
            "every model-tier registry entry needs a ModelAlias member and vice versa"
        )
        for alias in ro.ModelAlias:
            assert alias.env_key in by_var, f"{alias.env_key} is not a registry var"
            assert by_var[alias.env_key]["target"] == alias.legacy_env_key

    def test_current_name_resolves_the_alias(self, ro, monkeypatch):
        """The rename's own name is the one a migrated ~/.env.local sets."""
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("AI_SONNET_MODEL", "claude-sonnet-5")
        assert ro.resolve_alias("sonnet") == "claude-sonnet-5"

    def test_legacy_name_still_resolves_when_it_is_the_only_one(self, ro, monkeypatch):
        """An un-migrated machine, or one inheriting Claude Code's mirror."""
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-5")
        assert ro.resolve_alias("sonnet") == "claude-sonnet-4-5"

    def test_current_name_wins_over_the_legacy_one(self, ro, monkeypatch):
        """Both set is the normal post-migration state, and they can disagree.

        The mirror publishes the old name into Claude Code's settings, so a
        session started from there carries a value that ~/.env.local may have
        since moved on from. Asserting the new name wins is what makes this
        fail against a base that reads only the old one — a test that set just
        one variable would pass either way.
        """
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("AI_SONNET_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-5")
        assert ro.resolve_alias("sonnet") == "claude-sonnet-5"

    def test_unset_everywhere_leaves_the_alias_bare(self, ro, monkeypatch):
        self._clear_alias_envs(ro, monkeypatch)
        assert ro.resolve_alias("sonnet") == "sonnet"

    def test_parse_rejects_concrete_model_id(self, ro):
        assert ro.ModelAlias.parse("claude-sonnet-5") is None
        assert ro.ModelAlias.parse("sonnet") is ro.ModelAlias.SONNET

    def test_explicit(self, ro, no_model_env):
        assert ro.resolve_model("opus", "SOME_KEY", "sonnet") == "opus"

    def test_env_key(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("MY_MODEL_KEY", "haiku")

        assert ro.resolve_model("", "MY_MODEL_KEY", "sonnet") == "haiku"

    def test_global_env(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "opus")
        self._clear_alias_envs(ro, monkeypatch)
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "opus"

    def test_global_env_alias_resolved(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "opus")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6")
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "claude-opus-4-6"

    def test_default_fallback(self, ro, no_model_env):
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "sonnet"

    def test_alias_resolved_via_env(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "claude-sonnet-5"

    def test_explicit_alias_resolved(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6")
        assert ro.resolve_model("opus", "SOME_KEY", "sonnet") == "claude-opus-4-6"

    def test_env_key_alias_resolved(self, ro, monkeypatch):
        monkeypatch.setenv("MY_MODEL_KEY", "haiku")
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5@20251001")
        assert ro.resolve_model("", "MY_MODEL_KEY", "sonnet") == "claude-haiku-4-5@20251001"

    def test_empty_alias_env_falls_back_to_alias(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
        assert ro.resolve_model("sonnet", "SOME_KEY", "opus") == "sonnet"

    def test_full_model_id_not_resolved(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        assert ro.resolve_model("claude-sonnet-5", "SOME_KEY", "sonnet") == "claude-sonnet-5"


# ── 19b. phase_model / collect_phase_models ─────────────────────────────────


class TestPhaseModel:
    def _clean_env(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        for phase in ro.Phase:
            monkeypatch.delenv(phase.model_env_key, raising=False)
        for alias in ro.ModelAlias:
            monkeypatch.delenv(alias.env_key, raising=False)

    def test_phase_env_keys_follow_convention(self, ro):
        assert ro.Phase.SCOUT.model_env_key == "WORKBENCH_AI_SCOUT_MODEL"
        assert ro.Phase.SCOUT.thinking_env_key == "WORKBENCH_AI_SCOUT_THINKING"

    def test_default(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        assert ro.phase_model("scout", "") == "sonnet"

    def test_env_key_derived_from_phase_name(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "") == "claude-haiku-4-5"
        assert ro.phase_model("group", "") == "sonnet"

    def test_explicit_overrides_env(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "claude-opus-5") == "claude-opus-5"

    def test_collect_reads_the_worktree_config_when_given_one(
        self, ro, monkeypatch, tmp_path,
    ):
        """Preflight must resolve the models the review will actually run.

        `ReviewJob.config` is project- and container-scoped, so a repo setting
        `agent.model` in its own `.workbench.yml` runs a model the global scope
        never names. Checking without the worktree cleared a model the review
        never uses and missed the one it does — the unknown-window failure then
        landed inside `build_prompt`, after metadata and collection were paid
        for, which is the cost the preflight check exists to avoid.
        """
        self._clean_env(ro, monkeypatch)
        (tmp_path / ".workbench.yml").write_text(
            "agent:\n  model: claude-haiku-4-5\n",
        )
        models = ro.collect_phase_models("", tmp_path)
        assert set(models) == {"claude-haiku-4-5"}
        assert "sonnet" not in models

    def test_collect_groups_phases_by_model(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        models = ro.collect_phase_models("")
        assert models["claude-haiku-4-5"] == ["scout"]
        assert set(models["claude-sonnet-5"]) == set(ro.REVIEW_PHASES) - {"scout"}

    def test_collect_covers_every_review_phase(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        models = ro.collect_phase_models("")
        phases = [p for group in models.values() for p in group]
        assert sorted(phases) == sorted(ro.REVIEW_PHASES)

    def test_a_phase_from_another_entry_point_is_not_preflighted(self, ro, monkeypatch):
        """Preflight resolves the models a *review* is about to run.

        A fix pass belonging to `pr comments` or `pr ci` never runs here, so a
        bad model pinned on it must not fail a review before it starts.
        """
        self._clean_env(ro, monkeypatch)
        models = ro.collect_phase_models("")
        named = {p for group in models.values() for p in group}
        assert not named & {ro.Phase.COMMENTS_FIX, ro.Phase.CI_FIX}
