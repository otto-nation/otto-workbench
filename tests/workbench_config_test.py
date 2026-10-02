"""Tests for the typed, layered workbench configuration and the writes into it.

``workbench_config`` and ``workbench_config_write`` are tested together because
nearly every assertion about a write is "write it, then load it back" — the
scope a value lands in and the scope it is read from are the same question, and
splitting them would leave two files that only make sense read side by side.
The renderings in ``workbench_config_report`` read and nothing else, so they
stand alone in ``workbench_config_report_test.py``.

This suite holds loading, the scopes a value is read from, the modeline, and
the adopt and reuse paths. Three siblings take one question each:
``workbench_config_phases_test.py`` the per-phase precedence chain,
``workbench_config_keys_test.py`` which keys a write accepts and at which
scope, and ``workbench_config_types_test.py`` how a written value is typed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, add_worktree, seed_repo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_report
import config.workbench_config_write
from core.phases import Effort, Phase, Thinking

from workbench_config_support import needs_yaml, roots, _write, _row


# ── Loading and merging ─────────────────────────────────────────────────────


def test_missing_files_give_built_in_defaults(roots):
    _, project = roots
    cfg = config.workbench_config.load_config(project)
    assert cfg.reuse.default is config.workbench_config.ReuseLevel.FULL
    assert cfg.reuse.level is None
    assert cfg.agent.model is None
    assert cfg.agent.phases == {}
    assert cfg.issues.provider is None


def test_global_config_is_typed(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
reuse:
  level: ultra
review:
  effort: high
agent:
  thinking: medium
  phases:
    scout:
      model: haiku
""")
    cfg = config.workbench_config.load_config(project)
    assert cfg.reuse.level is config.workbench_config.ReuseLevel.ULTRA
    assert cfg.review.effort is Effort.HIGH
    assert cfg.review.self_effort is None
    assert cfg.agent.thinking is Thinking.MEDIUM
    assert cfg.agent.phases[Phase.SCOUT].model == "haiku"


def test_self_effort_is_on_the_key_surface():
    assert config.workbench_config.defines_key("review.self_effort")


def test_self_effort_loads_as_effort(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  self_effort: low\n")
    cfg = config.workbench_config.load_config(project)
    assert cfg.review.self_effort is Effort.LOW


def test_project_config_wins_over_global(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    _write(project / ".workbench.yml", "agent:\n  model: opus\n")
    assert config.workbench_config.load_config(project).agent.model == "opus"


def test_project_config_does_not_discard_global_siblings(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: sonnet
  thinking: medium
issues:
  provider: github
  team: ENG
""")
    _write(project / ".workbench.yml", "agent:\n  phases:\n    fix:\n      model: opus\n")
    cfg = config.workbench_config.load_config(project)
    assert cfg.agent.model == "sonnet"
    assert cfg.agent.thinking is Thinking.MEDIUM
    assert cfg.issues.provider is config.workbench_config.IssueProvider.GITHUB
    assert cfg.issues.team == "ENG"
    assert cfg.agent.phases[Phase.FIX].model == "opus"


def test_an_empty_file_is_not_an_error(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "")
    assert config.workbench_config.load_config(project) == config.workbench_config.WorkbenchConfig()


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_reads_the_same_config(roots, monkeypatch):
    """PyYAML is optional, so the yq path has to produce the same answer."""
    monkeypatch.setattr(config.workbench_config, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\nreview:\n  effort: high\n")
    cfg = config.workbench_config.load_config(project)
    assert cfg.agent.model == "sonnet"
    assert cfg.review.effort is Effort.HIGH


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_rejects_malformed_yaml(roots, monkeypatch):
    monkeypatch.setattr(config.workbench_config, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: [unclosed\n")
    with pytest.raises(config.workbench_config.ConfigError):
        config.workbench_config.load_config(project)


# ── Error handling ──────────────────────────────────────────────────────────


def test_unknown_enum_value_is_rejected_by_file_name(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  thinking: turbo\n")
    with pytest.raises(config.workbench_config.ConfigError) as excinfo:
        config.workbench_config.load_config(project)
    assert "config.yml" in str(excinfo.value)
    assert "turbo" in str(excinfo.value)


def test_unknown_phase_key_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  phases:\n    scoot:\n      model: haiku\n")
    with pytest.raises(config.workbench_config.ConfigError, match="scoot"):
        config.workbench_config.load_config(project)


def test_load_config_or_default_survives_a_bad_file(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  thinking: turbo\n")
    assert config.workbench_config.load_config_or_default(project) == config.workbench_config.WorkbenchConfig()


def test_malformed_yaml_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: [unclosed\n")
    with pytest.raises(config.workbench_config.ConfigError):
        config.workbench_config.load_config(project)


def test_a_non_mapping_file_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "- one\n- two\n")
    with pytest.raises(config.workbench_config.ConfigError, match="mapping"):
        config.workbench_config.load_config(project)


def test_batch_keys_default_and_merge(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yml").write_text("batch:\n  pool_max: 3\n")
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(cfg_dir))
    cfg = config.workbench_config.load_config(tmp_path)
    assert cfg.batch.pool_max == 3
    assert cfg.batch.pool_default == 1
    assert cfg.batch.mem_reserve == "2G"
    assert cfg.batch.cpu_pressure_max == 30.0
    assert cfg.batch.mem_pressure_max == 5.0


# ── The container scope ─────────────────────────────────────────────────────


def test_a_worktree_of_a_bare_repo_gains_a_container_scope(roots, container):
    config_root, _ = roots
    assert [s.path for s in config.workbench_config.config_scopes(container / "main")] == [
        config_root / config.workbench_config.CONFIG_NAME,
        container / config.workbench_config.PROJECT_CONFIG_NAME,
        container / "main" / config.workbench_config.PROJECT_CONFIG_NAME,
    ]


def test_the_container_sits_between_the_two_older_scopes(roots, container):
    """Merge order, so the report's precedence order is its reverse."""
    assert [s.name for s in config.workbench_config.config_scopes(container / "main")] == [
        config.workbench_config.GLOBAL_SCOPE, config.workbench_config.CONTAINER_SCOPE, config.workbench_config.PROJECT_SCOPE,
    ]
    assert [s.name for s in config.workbench_config_report.config_status(container / "main").scopes] == [
        config.workbench_config.PROJECT_SCOPE, config.workbench_config.CONTAINER_SCOPE, config.workbench_config.GLOBAL_SCOPE,
    ]


def test_a_plain_clone_keeps_the_two_scopes_it_always_had(roots, tmp_path):
    clone = seed_repo(tmp_path / "clone")
    assert config.workbench_config.container_config_path(clone) is None
    assert [s.name for s in config.workbench_config.config_scopes(clone)] == [config.workbench_config.GLOBAL_SCOPE, config.workbench_config.PROJECT_SCOPE]


def test_the_container_file_beats_the_global_one(roots, container):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    _write(container / config.workbench_config.PROJECT_CONFIG_NAME, "agent:\n  model: opus\n")
    assert config.workbench_config.load_config(container / "main").agent.model == "opus"


def test_the_worktree_file_beats_the_container_one(roots, container):
    _write(container / config.workbench_config.PROJECT_CONFIG_NAME, "agent:\n  model: opus\n")
    _write(container / "main" / config.workbench_config.PROJECT_CONFIG_NAME, "agent:\n  model: haiku\n")
    assert config.workbench_config.load_config(container / "main").agent.model == "haiku"


def test_the_container_does_not_discard_global_siblings(roots, container):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n  thinking: medium\n")
    _write(container / config.workbench_config.PROJECT_CONFIG_NAME, "agent:\n  model: opus\n")
    cfg = config.workbench_config.load_config(container / "main")
    assert cfg.agent.model == "opus"
    assert cfg.agent.thinking is Thinking.MEDIUM


def test_a_container_value_names_the_container_in_the_report(roots, container):
    _write(container / config.workbench_config.PROJECT_CONFIG_NAME, "issues:\n  provider: github\n")
    status = config.workbench_config_report.config_status(container / "main")
    assert _row(status, "issues.provider").scope.name == config.workbench_config.CONTAINER_SCOPE


def test_set_container_value_writes_above_the_worktrees(roots, container):
    config.workbench_config_write.set_container_value("issues.provider", "github", container / "main")
    assert not (container / "main" / config.workbench_config.PROJECT_CONFIG_NAME).exists()
    assert "github" in (container / config.workbench_config.PROJECT_CONFIG_NAME).read_text()


def test_a_sibling_worktree_reads_what_the_container_recorded(roots, container):
    """The reason the scope exists: `wt switch -c` cuts a checkout holding
    nothing, and a worktree file would have to be copied into it by hand."""
    config.workbench_config_write.set_container_value("issues.provider", "github", container / "main")
    feature = add_worktree(container, "feature")
    assert config.workbench_config.load_config(feature).issues.provider is config.workbench_config.IssueProvider.GITHUB


def test_set_container_value_refuses_a_plain_clone(roots, tmp_path):
    """Falling back to the worktree would answer the opposite of what was asked:
    that file is deleted by `wt remove` and unseen by every sibling checkout."""
    clone = seed_repo(tmp_path / "clone")
    with pytest.raises(config.workbench_config.ConfigError, match="container"):
        config.workbench_config_write.set_container_value("issues.provider", "github", clone)
    assert not (clone / config.workbench_config.PROJECT_CONFIG_NAME).exists()


def test_set_container_value_refuses_the_same_keys(roots, container):
    with pytest.raises(config.workbench_config.ConfigKeyError):
        config.workbench_config_write.set_container_value("issues.providr", "github", container / "main")


# ── The key surface ─────────────────────────────────────────────────────────


def test_schema_lists_every_phase_as_a_valid_key():
    """`surface_schema` is what a dotted key is judged against, here and installed.

    The renderings built on it are `workbench_config_report`'s, and are tested
    there; this is the surface itself, which the write guard reads directly.
    """
    phases = config.workbench_config.surface_schema()["properties"]["agent"]["properties"]["phases"]
    assert phases["propertyNames"]["enum"] == [p.value for p in Phase]


# ── Writing ─────────────────────────────────────────────────────────────────


def test_set_value_creates_and_updates_the_global_file(roots):
    config_root, _ = roots
    config.workbench_config_write.set_value("reuse.level", "ultra")
    assert config.workbench_config.load_config().reuse.level is config.workbench_config.ReuseLevel.ULTRA
    config.workbench_config_write.set_value("reuse.level", "lite")
    assert config.workbench_config.load_config().reuse.level is config.workbench_config.ReuseLevel.LITE
    assert (config_root / "config.yml").is_file()


def test_set_value_preserves_unrelated_keys(roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    config.workbench_config_write.set_value("reuse.level", "ultra")
    cfg = config.workbench_config.load_config()
    assert cfg.agent.model == "sonnet"
    assert cfg.reuse.level is config.workbench_config.ReuseLevel.ULTRA


# ── Schema modeline ─────────────────────────────────────────────────────────


def test_the_schema_url_points_at_a_path_the_repo_actually_has():
    """A moved or renamed schema has to move the URL with it.

    The modeline is the only consumer of the committed schema, and a URL
    pointing at a path the repo no longer has fails silently — in someone
    else's editor, months later. No network here, but neither a rename nor a
    move into a subdirectory gets past it.
    """
    assert (REPO_ROOT / config.workbench_config.SCHEMA_PATH).is_file()
    assert config.workbench_config.SCHEMA_URL == f"{config.workbench_config.REPO_RAW_URL}/{config.workbench_config.SCHEMA_PATH}"


def test_a_new_config_file_is_born_with_the_modeline(roots):
    config_root, _ = roots
    config.workbench_config_write.set_value("reuse.level", "ultra")
    assert (config_root / "config.yml").read_text().startswith(config.workbench_config.CONFIG_HEADER)


def test_the_modeline_survives_later_writes(roots):
    """yq is the writer precisely because it carries comments through."""
    config_root, _ = roots
    config.workbench_config_write.set_value("reuse.level", "ultra")
    config.workbench_config_write.set_value("agent.model", "sonnet")
    text = (config_root / "config.yml").read_text()
    assert text.startswith(config.workbench_config.CONFIG_HEADER)
    assert text.count(config.workbench_config.CONFIG_HEADER) == 1
    cfg = config.workbench_config.load_config()
    assert cfg.reuse.level is config.workbench_config.ReuseLevel.ULTRA
    assert cfg.agent.model == "sonnet"


def test_a_modeline_only_file_reads_as_an_empty_config(roots):
    """The seeded file is comments and nothing else until the first key lands."""
    config_root, project = roots
    _write(config_root / "config.yml", config.workbench_config.CONFIG_HEADER + "\n")
    cfg = config.workbench_config.load_config(project)
    assert cfg.reuse.level is None
    assert cfg.reuse.default is config.workbench_config.ReuseLevel.FULL


@needs_yaml
def test_the_pyyaml_fallback_puts_the_modeline_back(roots, monkeypatch):
    """Without yq the document is re-rendered, so the header is re-applied.

    Every comment the user wrote is still lost on this path — the modeline is
    the one this module owns and can restore.
    """
    config_root, _ = roots
    monkeypatch.setattr(config.workbench_config_write.shutil, "which", lambda _: None)
    config.workbench_config_write.set_value("reuse.level", "ultra")
    config.workbench_config_write.set_value("agent.model", "sonnet")
    text = (config_root / "config.yml").read_text()
    assert text.startswith(config.workbench_config.CONFIG_HEADER)
    assert text.count(config.workbench_config.CONFIG_HEADER) == 1
    assert config.workbench_config.load_config().agent.model == "sonnet"


@needs_yaml
def test_the_pyyaml_fallback_adds_no_modeline_to_a_file_without_one(
    roots, monkeypatch,
):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    monkeypatch.setattr(config.workbench_config_write.shutil, "which", lambda _: None)
    config.workbench_config_write.set_value("reuse.level", "ultra")
    assert config.workbench_config.CONFIG_HEADER not in (config_root / "config.yml").read_text()


# ── Per-repo adoption of .claude/review.yml ─────────────────────────────────


def test_adopt_converts_a_project_review_yml(roots):
    import review.issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(
        project / ".claude" / "review.yml",
        "issue_tracker:\n  provider: github\n  team: ENG\n",
    )

    assert review.issue.adopt_project_review_yml(str(project)) is True

    cfg = config.workbench_config.load_config(project)
    assert cfg.issues.provider is config.workbench_config.IssueProvider.GITHUB
    assert cfg.issues.team == "ENG"


def test_adopt_writes_the_top_level_key_not_the_legacy_nesting(roots):
    """The old file's key was review-namespaced; the config's is not."""
    import review.issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review.issue.adopt_project_review_yml(str(project))
    assert "review:" not in (project / ".workbench.yml").read_text()


def test_adopt_seeds_the_modeline_like_every_other_creator(roots):
    """docs/libraries.md promises every workbench-created file carries it."""
    import review.issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review.issue.adopt_project_review_yml(str(project))
    assert (project / ".workbench.yml").read_text().startswith(config.workbench_config.CONFIG_HEADER + "\n")


def test_adopt_leaves_the_old_file_in_place(roots):
    import review.issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review.issue.adopt_project_review_yml(str(project))
    assert (project / ".claude" / "review.yml").is_file()


def test_adopt_is_a_no_op_when_workbench_yml_exists(roots):
    import review.issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")
    _write(project / ".workbench.yml", "issues:\n  provider: jira\n")

    assert review.issue.adopt_project_review_yml(str(project)) is False
    assert config.workbench_config.load_config(project).issues.provider is config.workbench_config.IssueProvider.JIRA


def test_adopt_is_a_no_op_without_an_old_file(roots):
    import review.issue

    _, project = roots
    assert review.issue.adopt_project_review_yml(str(project)) is False
    assert not (project / ".workbench.yml").exists()


# ── Reuse level ─────────────────────────────────────────────────────────────


@pytest.fixture
def reuse_levels(roots):
    """_reuse_levels, importable only with ai/claude/bin on the path."""
    bin_dir = str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    import _reuse_levels

    return _reuse_levels


def test_reuse_level_defaults_to_full(reuse_levels):
    assert reuse_levels.read_level() == "full"
    assert reuse_levels.read_default() == "full"


def test_reuse_level_round_trips_through_the_config(reuse_levels):
    reuse_levels.write_level("ultra")
    assert reuse_levels.read_level() == "ultra"
    assert config.workbench_config.load_config().reuse.level is config.workbench_config.ReuseLevel.ULTRA


def test_reuse_default_round_trips_through_the_config(reuse_levels):
    reuse_levels.write_default("lite")
    assert reuse_levels.read_default() == "lite"
    assert config.workbench_config.load_config().reuse.default is config.workbench_config.ReuseLevel.LITE


def test_reuse_level_falls_back_to_the_configured_default(reuse_levels, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "reuse:\n  default: lite\n")
    assert reuse_levels.read_level() == "lite"


def test_reuse_default_env_var_still_wins(reuse_levels, roots, monkeypatch):
    config_root, _ = roots
    _write(config_root / "config.yml", "reuse:\n  default: lite\n")
    monkeypatch.setenv("REUSE_DEFAULT_MODE", "ultra")
    assert reuse_levels.read_default() == "ultra"


def test_reuse_reader_survives_a_bad_config(reuse_levels, roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "reuse:\n  level: turbo\n")
    assert reuse_levels.read_level() == "full"


def test_a_declared_issue_provider_is_still_read(roots):
    _, project = roots
    _write(project / config.workbench_config.PROJECT_CONFIG_NAME, """
issues:
  provider: github
""")
    assert config.workbench_config.load_config(project).issues.provider is config.workbench_config.IssueProvider.GITHUB
