"""Tests for the typed, layered workbench configuration and the writes into it.

``workbench_config`` and ``workbench_config_write`` are tested together because
nearly every assertion about a write is "write it, then load it back" — the
scope a value lands in and the scope it is read from are the same question, and
splitting them would leave two files that only make sense read side by side.
The renderings in ``workbench_config_report`` read and nothing else, so they
stand alone in ``workbench_config_report_test.py``.
"""

from __future__ import annotations

import json
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

# The PyYAML write path only exists for a machine without yq, so the tests for
# it only run where PyYAML is installed — the same shape review_grouping_test
# uses for the reader.
needs_yaml = pytest.mark.skipif(config.workbench_config.yaml is None, reason="PyYAML not installed")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A sandboxed config root plus an empty project directory."""
    config_root = tmp_path / "config"
    config_root.mkdir()
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_root))
    project = tmp_path / "project"
    project.mkdir()
    return config_root, project


def _write(path: Path, text: str) -> None:
    path.write_text(text.lstrip("\n"))


def _row(status: config.workbench_config_report.ConfigStatus, key: str) -> config.workbench_config_report.ResolvedKey:
    return next(row for row in status.keys if row.key == key)


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


# ── Precedence across all five layers ───────────────────────────────────────


@pytest.fixture
def phase_cfg(roots):
    """A config that sets a phase model at both scopes, for layering tests."""
    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: global-section
  phases:
    scout:
      model: global-phase
""")
    _write(project / ".workbench.yml", """
agent:
  phases:
    scout:
      model: project-phase
""")
    return project


def test_layer_5_global_config_beats_the_built_in(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: from-global\n")
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "from-global"


def test_layer_4_project_config_beats_the_global(phase_cfg):
    import agent.phases

    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "project-phase"


def test_a_phase_entry_beats_the_section_within_one_file(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: section
  phases:
    scout:
      model: phase
""")
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "phase"
    assert agent.phases.phase_model(Phase.FIX, None, cfg) == "section"


def test_layer_3_global_env_beats_the_config(phase_cfg, monkeypatch):
    import agent.phases

    monkeypatch.setenv("WORKBENCH_AI_MODEL", "from-env")
    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "from-env"


def test_layer_2_phase_env_beats_the_global_env(phase_cfg, monkeypatch):
    import agent.phases

    monkeypatch.setenv("WORKBENCH_AI_MODEL", "from-env")
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "from-phase-env")
    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, None, cfg) == "from-phase-env"


def test_layer_1_explicit_beats_every_env_and_file(phase_cfg, monkeypatch):
    import agent.phases

    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "from-phase-env")
    cfg = config.workbench_config.load_config(phase_cfg)
    assert agent.phases.phase_model(Phase.SCOUT, "explicit", cfg) == "explicit"


def test_phase_model_loads_the_config_itself_when_not_given_one(roots):
    """The default argument is what a single-value caller relies on."""
    import agent.phases

    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: from-disk\n")
    assert agent.phases.phase_model(Phase.SCOUT, None) == "from-disk"


def test_thinking_layers_the_same_way(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  thinking: low
  phases:
    scout:
      thinking: high
""")
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_thinking_default(Phase.SCOUT, Effort.MEDIUM, cfg) is Thinking.HIGH
    assert agent.phases.phase_thinking_default(Phase.FIX, Effort.MEDIUM, cfg) is Thinking.LOW


def test_thinking_falls_back_to_the_effort_preset(roots):
    import agent.phases
    from agent.types import EFFORT_PRESETS

    _, project = roots
    cfg = config.workbench_config.load_config(project)
    assert agent.phases.phase_thinking_default(
        Phase.SCOUT, Effort.HIGH, cfg,
    ) == EFFORT_PRESETS[Effort.HIGH].thinking


def test_effort_falls_back_from_config_to_the_built_in(roots):
    import agent.phases

    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  effort: high\n")
    assert agent.phases.resolve_effort(None, config.workbench_config.load_config(project)) is Effort.HIGH
    assert agent.phases.resolve_effort(Effort.LOW, config.workbench_config.load_config(project)) is Effort.LOW
    assert agent.phases.resolve_effort(None, config.workbench_config.WorkbenchConfig()) is Effort.MEDIUM


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


def test_set_project_value_writes_the_repo_config(roots):
    _, project = roots
    config.workbench_config_write.set_project_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github", project)
    cfg = config.workbench_config.load_config(project)
    assert cfg.issues.provider is config.workbench_config.IssueProvider.GITHUB


def test_set_project_value_preserves_hand_written_comments(roots):
    """yq goes first precisely so a hand-authored file keeps its comments."""
    _, project = roots
    _write(project / config.workbench_config.PROJECT_CONFIG_NAME, """
# we file on GitHub, not Linear
issues:
  team: ENG
""")
    config.workbench_config_write.set_project_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github", project)
    assert "# we file on GitHub, not Linear" in (project / config.workbench_config.PROJECT_CONFIG_NAME).read_text()
    assert config.workbench_config.load_config(project).issues.team == "ENG"


def test_set_project_value_seeds_the_schema_modeline(roots):
    """A file the workbench creates gets completion, same as the global one."""
    _, project = roots
    config.workbench_config_write.set_project_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github", project)
    assert (project / config.workbench_config.PROJECT_CONFIG_NAME).read_text().startswith(config.workbench_config.CONFIG_HEADER)


def test_set_project_value_does_not_touch_the_global_config(roots):
    config_root, project = roots
    config.workbench_config_write.set_project_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github", project)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


# ── The key guard ───────────────────────────────────────────────────────────
#
# Both config files are shared — the global one by every repo on the machine,
# the project one by everyone who clones — and `serde` drops a key it does not
# know, so a write under the wrong name is lost with no error at either end.
# `check_key` is what turns that into a refusal at write time.


@pytest.fixture
def stale_install(tmp_path, monkeypatch):
    """Point ``check_key`` at an installed schema that lacks ``issues``.

    The incident with the two checkouts swapped: there the writing checkout was
    the stale one, and here it is this checkout that knows the key the install
    does not. The mechanism under test is the same either way — a write is
    judged by the surface the machine reads, not by the one in front of it —
    and it is the only direction a test can build, since the local surface is
    whatever this checkout ships.
    """
    schema = json.loads(config.workbench_config_report.schema_json())
    tracker = schema["properties"].pop("issues")
    schema["properties"]["review"]["properties"]["issues"] = tracker
    path = tmp_path / "installed" / config.workbench_config.SCHEMA_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(schema))
    monkeypatch.setattr(config.workbench_config_write, "installed_schema_path", lambda: path)
    return path


def test_set_value_refuses_a_key_the_config_does_not_define(roots):
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigKeyError) as exc:
        config.workbench_config_write.set_value("reuse.levl", "ultra")
    assert "reuse.levl" in str(exc.value)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_set_value_refuses_the_shape_the_key_moved_off(roots):
    """The literal key the incident wrote, judged by the surface it moved to."""
    with pytest.raises(config.workbench_config.ConfigKeyError):
        config.workbench_config_write.set_value("review.issue_tracker.provider", "github")


def test_set_value_refuses_the_section_the_key_left(roots):
    """`issue_tracker` is the name the section had before it became `issues`.

    The migration moves what is on disk, but an agent working from a rule it
    read last week writes the old name by hand. `serde` drops it silently, so
    the refusal at write time is the only place it is still visible.
    """
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigKeyError):
        config.workbench_config_write.set_value("issue_tracker.provider", "github")
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_a_refused_key_is_a_config_error_too(roots):
    """A caller that only handles the general failure still catches this one."""
    assert issubclass(config.workbench_config.ConfigKeyError, config.workbench_config.ConfigError)
    with pytest.raises(config.workbench_config.ConfigError):
        config.workbench_config_write.set_value("nonsense", "x")


def test_set_project_value_refuses_the_same_keys(roots):
    """A repo file is committed, so a dead key travels to everyone who clones."""
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigKeyError):
        config.workbench_config_write.set_project_value("issues.provdier", "github", project)
    assert not (project / config.workbench_config.PROJECT_CONFIG_NAME).exists()


def test_a_key_the_installed_workbench_does_not_read_is_refused(roots, stale_install):
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigKeyError) as exc:
        config.workbench_config_write.set_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github")
    assert str(stale_install) in str(exc.value)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_a_key_both_surfaces_read_is_written(roots, stale_install):
    """The installed surface refuses keys; it does not refuse writing."""
    config.workbench_config_write.set_value("reuse.level", "ultra")
    assert config.workbench_config.load_config().reuse.level is config.workbench_config.ReuseLevel.ULTRA


def test_no_installed_workbench_leaves_the_local_surface(roots, monkeypatch):
    """CI and a fresh clone have no install, and still have to be able to write."""
    monkeypatch.setattr(config.workbench_config_write, "installed_schema_path", lambda: None)
    config.workbench_config_write.set_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github")
    assert config.workbench_config.load_config().issues.provider is config.workbench_config.IssueProvider.GITHUB


def test_an_unreadable_installed_schema_leaves_the_local_surface(roots, tmp_path, monkeypatch):
    """One broken file must not make the config unwritable machine-wide."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(config.workbench_config_write, "installed_schema_path", lambda: broken)
    config.workbench_config_write.set_value(config.workbench_config.ISSUE_PROVIDER_KEY, "github")
    assert config.workbench_config.load_config().issues.provider is config.workbench_config.IssueProvider.GITHUB


def test_an_enum_keyed_section_is_writable_by_its_declared_keys(roots):
    """`agent.phases.<phase>` is a dict, so the guard reads propertyNames."""
    config.workbench_config_write.set_value(f"agent.phases.{Phase.SCOUT}.model", "sonnet")
    assert config.workbench_config.load_config().agent.phases[Phase.SCOUT].model == "sonnet"
    with pytest.raises(config.workbench_config.ConfigKeyError):
        config.workbench_config_write.set_value("agent.phases.nosuchphase.model", "sonnet")


# ── Scope restrictions ─────────────────────────────────────────────────
#
# A key may declare which files are allowed to hold it. Declaring nothing means
# any of them, which is what every key did before the rules existed.


def test_a_global_only_key_is_refused_at_project_scope(roots):
    """An absolute machine path in a committed file is meaningless elsewhere."""
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_project_value(config.workbench_config.WIKI_ROOT_KEY, "/home/someone/vault", project)
    assert not (project / config.workbench_config.PROJECT_CONFIG_NAME).exists()


def test_a_global_only_key_is_refused_at_container_scope(roots, container):
    """The container file is not committed, but it is still not this machine."""
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_container_value(config.workbench_config.WIKI_ROOT_KEY, "/home/someone/vault", container / "main")
    assert not (container / config.workbench_config.PROJECT_CONFIG_NAME).exists()


def test_a_global_only_key_is_written_at_global_scope(roots):
    config.workbench_config_write.set_value(config.workbench_config.WIKI_ROOT_KEY, "/home/someone/vault")
    assert config.workbench_config.load_config().wiki.root == "/home/someone/vault"


def test_a_scope_refusal_names_the_scope_that_would_work(roots):
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigScopeError) as exc:
        config.workbench_config_write.set_project_value(config.workbench_config.WIKI_ROOT_KEY, "/home/someone/vault", project)
    message = str(exc.value)
    assert "global scope" in message
    assert f"otto-workbench config set {config.workbench_config.WIKI_ROOT_KEY}" in message


def test_a_scope_refusal_is_not_a_key_error(roots):
    """What keeps the "here are the keys we accept" hint off a real key.

    `config_cli` catches `ConfigKeyError` first and appends the schema URL,
    which sends someone hunting a spelling mistake they did not make.
    """
    _, project = roots
    assert issubclass(config.workbench_config.ConfigScopeError, config.workbench_config.ConfigError)
    assert not issubclass(config.workbench_config.ConfigScopeError, config.workbench_config.ConfigKeyError)
    with pytest.raises(config.workbench_config.ConfigScopeError) as exc:
        config.workbench_config_write.set_project_value(config.workbench_config.WIKI_ROOT_KEY, "/x", project)
    assert not isinstance(exc.value, config.workbench_config.ConfigKeyError)


# passes-at-base: every scope already accepted an undeclared key, and this pins that opt-in did not become opt-out
def test_a_key_that_declares_nothing_is_writable_at_every_scope(roots, container):
    _, project = roots
    config.workbench_config_write.set_value("reuse.level", "ultra")
    config.workbench_config_write.set_project_value("reuse.level", "ultra", project)
    config.workbench_config_write.set_container_value("reuse.level", "ultra", container / "main")
    assert (project / config.workbench_config.PROJECT_CONFIG_NAME).exists()
    assert (container / config.workbench_config.PROJECT_CONFIG_NAME).exists()
    assert config.workbench_config.load_config().reuse.level == config.workbench_config.ReuseLevel.ULTRA
    assert config.workbench_config.load_config(project).reuse.level == config.workbench_config.ReuseLevel.ULTRA
    assert config.workbench_config.load_config(container / "main").reuse.level == config.workbench_config.ReuseLevel.ULTRA


def test_a_non_global_path_without_a_matching_scope_is_refused(roots):
    """A mismatched pair would check a project write against the global rules.

    That is the guard silently not applying rather than failing, which is the
    failure mode scope enforcement exists to close.
    """
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigError) as exc:
        config.workbench_config_write.set_value("reuse.level", "ultra", config.workbench_config.project_config_path(project))
    assert "set_project_value" in str(exc.value)
    assert not (project / config.workbench_config.PROJECT_CONFIG_NAME).exists()


# passes-at-base: a misspelled key was already refused, and this pins that the scope check did not get in front of that
def test_the_key_check_still_runs_before_the_scope_check(roots):
    """A misspelling is a misspelling, wherever it was going to be written."""
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigKeyError):
        config.workbench_config_write.set_project_value("wiki.rooot", "/x", project)


def test_scope_rules_finds_a_nested_key_and_skips_an_unrestricted_one(roots):
    rules = config.workbench_config.scope_rules()
    assert config.workbench_config.WIKI_ROOT_KEY in rules
    assert rules[config.workbench_config.WIKI_ROOT_KEY].allowed == frozenset({config.workbench_config.GLOBAL_SCOPE})
    assert config.workbench_config.WIKI_DIR_KEY not in rules
    assert "reuse.level" not in rules


def test_the_network_key_is_refused_at_project_scope(roots):
    """A second declaring key: the mechanism is not built for one caller."""
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_project_value(config.workbench_config.GITHUB_SSH_443_KEY, "true", project)


def test_check_key_says_which_surface_refused(stale_install):
    assert config.workbench_config_write.check_key("reuse.level").ok
    here = config.workbench_config_write.check_key("reuse.levl")
    assert not here.ok
    assert here.verdict is config.workbench_config_write.KeyVerdict.UNKNOWN_HERE
    assert "WorkbenchConfig defines" in here.reason
    installed = config.workbench_config_write.check_key(config.workbench_config.ISSUE_PROVIDER_KEY)
    assert not installed.ok
    assert installed.verdict is config.workbench_config_write.KeyVerdict.UNKNOWN_INSTALLED
    assert "the two disagree about where the value lives" in installed.reason
    assert config.workbench_config_write.check_key("reuse.level").reason == ""


def test_installed_schema_path_resolves_through_the_launcher(tmp_path, monkeypatch):
    """The PATH symlink is the whole mechanism — a worktree cannot fake it."""
    installed = tmp_path / "checkout"
    (installed / "bin").mkdir(parents=True)
    (installed / "bin" / config.workbench_config_write.INSTALLED_LAUNCHER).write_text("#!/bin/sh\n")
    (installed / config.workbench_config.SCHEMA_PATH).write_text("{}")
    monkeypatch.setattr(config.workbench_config_write.shutil, "which",
                        lambda name: str(installed / "bin" / name))
    assert config.workbench_config_write.installed_schema_path() == installed / config.workbench_config.SCHEMA_PATH


def test_installed_schema_path_is_none_without_an_install(monkeypatch):
    monkeypatch.setattr(config.workbench_config_write.shutil, "which", lambda name: None)
    assert config.workbench_config_write.installed_schema_path() is None


# ── The value guard ─────────────────────────────────────────────────────────
#
# The same loss as the key guard catches, one level down. Every value arrives
# as a string off a command line, and `serde` replaces a scalar it cannot
# convert with the field's default — so a boolean written as `"true"` is a
# write that reports success and leaves the setting off.


def test_a_boolean_key_is_written_as_a_boolean(roots):
    config_root, _ = roots
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "true")
    assert "ssh_over_443: true" in (config_root / config.workbench_config.CONFIG_NAME).read_text()
    assert config.workbench_config.load_config().github.ssh_over_443 is True


def test_a_boolean_key_round_trips_back_to_false(roots):
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "true")
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "false")
    assert config.workbench_config.load_config().github.ssh_over_443 is False


def test_a_boolean_key_refuses_a_value_that_is_neither(roots):
    """`bool("yes")` is True and so is `bool("no")`, which is why nothing guesses."""
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "yes")
    assert config.workbench_config.GITHUB_SSH_443_KEY in str(exc.value)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_a_refused_value_is_a_config_error_too(roots):
    """A caller that only handles the general failure still catches this one."""
    assert issubclass(config.workbench_config.ConfigValueError, config.workbench_config.ConfigError)
    with pytest.raises(config.workbench_config.ConfigError):
        config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "sideways")


def test_a_refused_value_is_not_a_refused_key(roots):
    """The two failures owe the caller different advice, so they are different types."""
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "sideways")
    assert not issubclass(config.workbench_config.ConfigValueError, config.workbench_config.ConfigKeyError)


def test_a_string_key_is_written_as_the_string_it_was_given(roots):
    """A value that looks like a bool under a string field stays a string."""
    _, project = roots
    config.workbench_config_write.set_project_value("issues.team", "true", project)
    assert config.workbench_config.load_config(project).issues.team == "true"


@needs_yaml
def test_the_pyyaml_fallback_writes_the_same_types(roots, monkeypatch):
    """Two writers, one coercion — the fallback cannot disagree about the type."""
    monkeypatch.setattr(config.workbench_config_write.shutil, "which", lambda _: None)
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "true")
    config.workbench_config_write.set_value("agent.model", "sonnet")
    cfg = config.workbench_config.load_config()
    assert cfg.github.ssh_over_443 is True
    assert cfg.agent.model == "sonnet"


def test_a_list_key_refuses_the_scalar_this_writer_can_offer(roots):
    """`serde` reads a string where a list belongs as `[]`, so a write would vanish."""
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.set_value(config.workbench_config.ISSUE_LABELS_KEY, "follow-up")
    assert config.workbench_config.ISSUE_LABELS_KEY in str(exc.value)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_a_hand_written_list_still_loads(roots):
    """The refusal is the writer's alone — the file itself holds a list fine."""
    _, project = roots
    _write(
        project / config.workbench_config.PROJECT_CONFIG_NAME,
        "issues:\n  labels:\n    - follow-up\n    - needs-triage\n",
    )
    assert config.workbench_config.load_config(project).issues.labels == ["follow-up", "needs-triage"]


def test_labels_default_to_the_follow_up_label(roots):
    """A repo that says nothing still labels what its automation files."""
    _, project = roots
    assert config.workbench_config.load_config(project).issues.labels == [config.workbench_config.FOLLOW_UP_LABEL]


def test_an_empty_label_list_is_kept_as_the_opt_out_it_is(roots):
    """`labels: []` has to outrank the default, or opting out is impossible."""
    _, project = roots
    _write(project / config.workbench_config.PROJECT_CONFIG_NAME, "issues:\n  labels: []\n")
    assert config.workbench_config.load_config(project).issues.labels == []


def test_a_numeric_field_is_parsed_into_the_number_it_names():
    """Against a real integer key, so the writer is read through the surface.

    This used to monkeypatch `schema_type` because nothing on the surface was
    numeric. `fix.verify_timeout` is, so the coercion is now reached the way a
    caller reaches it — a patched type would keep passing if the key stopped
    being an integer.
    """
    assert config.workbench_config_write.coerce_value(config.workbench_config.FIX_VERIFY_TIMEOUT_KEY, "30") == 30


# The float half of the numeric test this change split in two. The integer half
# now runs against a real key; nothing on the surface is a float, so this one
# keeps the patched type it always had.
# passes-at-base: it is the pre-existing patched-type case, carried over intact
def test_a_float_field_is_parsed_through_a_patched_type(monkeypatch):
    """No float on the surface yet; the branch is reached by its type."""
    monkeypatch.setattr(config.workbench_config_write, "schema_type", lambda _: "number")
    assert config.workbench_config_write.coerce_value("some.ratio", "1.5") == 1.5


def test_the_fix_verification_keys_are_on_the_surface():
    """`fix.engine` reads these off a loaded config; an absent key reads as unset."""
    assert config.workbench_config.defines_key(config.workbench_config.FIX_VERIFY_COMMAND_KEY)
    assert config.workbench_config.defines_key(config.workbench_config.FIX_VERIFY_TIMEOUT_KEY)


def test_a_repo_that_declares_no_verify_command_gets_the_empty_default():
    """Empty is "this repo has not said", which `fix.suite` reports as such.

    A default command here would point every repo at a script only one of them
    has, which is the reason the key exists instead of a hardcoded runner.
    """
    assert config.workbench_config.WorkbenchConfig().fix.verify_command == ""


def test_the_declared_command_round_trips_from_the_project_scope(roots):
    _, project = roots
    _write(project / config.workbench_config.PROJECT_CONFIG_NAME,
           "fix:\n  verify_command: bin/local/run-tests --changed\n"
           "  verify_timeout: 120\n")

    loaded = config.workbench_config.load_config(project)

    assert loaded.fix.verify_command == "bin/local/run-tests --changed"
    assert loaded.fix.verify_timeout == 120


def test_the_verify_timeout_refuses_a_value_that_is_not_a_number():
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.workbench_config_write.coerce_value(config.workbench_config.FIX_VERIFY_TIMEOUT_KEY, "ten minutes")


def test_the_verification_keys_are_refused_at_the_machine_scope():
    """They name a path inside one checkout and bound that repo's own checks.

    A machine-wide command points every other repo at a script it does not
    have, and the pass would report ERROR on repos that never opted in.
    """
    for key in (config.workbench_config.FIX_VERIFY_COMMAND_KEY, config.workbench_config.FIX_VERIFY_TIMEOUT_KEY):
        assert config.workbench_config_write.check_scope(key, config.workbench_config.GLOBAL_SCOPE).ok is False
        assert config.workbench_config_write.check_scope(key, config.workbench_config.PROJECT_SCOPE).ok is True
        assert config.workbench_config_write.check_scope(key, config.workbench_config.CONTAINER_SCOPE).ok is True


def test_a_numeric_field_refuses_a_value_that_is_not_a_number(monkeypatch):
    monkeypatch.setattr(config.workbench_config_write, "schema_type", lambda _: "integer")
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.coerce_value("some.count", "a few")
    assert "some.count" in str(exc.value)


def test_a_numeric_field_refuses_the_floats_yaml_cannot_spell(monkeypatch):
    """`float("nan")` parses, and `.key = nan` is a bare word yq reads as nothing."""
    monkeypatch.setattr(config.workbench_config_write, "schema_type", lambda _: "number")
    for spelled in ("nan", "inf", "-inf"):
        with pytest.raises(config.workbench_config.ConfigValueError):
            config.workbench_config_write.coerce_value("some.ratio", spelled)


def test_an_optional_field_is_typed_through_its_null_half():
    """`str | None` is a union in the schema and a string to a writer."""
    schema = config.workbench_config.surface_schema()
    assert config.workbench_config.schema_type(config.workbench_config.schema_at(schema, "reuse.level")) == "string"
    assert config.workbench_config.schema_type(config.workbench_config.schema_at(schema, config.workbench_config.GITHUB_SSH_443_KEY)) == "boolean"


def test_a_fragment_that_names_no_type_is_left_permissive():
    """`schema_gen` emits an open fragment for a hint it cannot describe.

    A check that cannot see the type must not be the thing that refuses a
    write, which is the same direction the key walk is permissive in.
    """
    assert config.workbench_config.schema_type({}) is None
