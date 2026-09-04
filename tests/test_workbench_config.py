"""Tests for the typed, layered workbench configuration and the writes into it.

``workbench_config`` and ``workbench_config_write`` are tested together because
nearly every assertion about a write is "write it, then load it back" — the
scope a value lands in and the scope it is read from are the same question, and
splitting them would leave two files that only make sense read side by side.
The renderings in ``workbench_config_report`` read and nothing else, so they
stand alone in ``test_workbench_config_report.py``.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, add_worktree, seed_repo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from config import workbench_config as wc
from config import workbench_config_report as wcr
from config import workbench_config_write as wcw
from core.phases import Effort, Phase, Thinking

# The PyYAML write path only exists for a machine without yq, so the tests for
# it only run where PyYAML is installed — the same shape test_review_grouping
# uses for the reader.
needs_yaml = pytest.mark.skipif(wc.yaml is None, reason="PyYAML not installed")


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


def _row(status: wcr.ConfigStatus, key: str) -> wcr.ResolvedKey:
    return next(row for row in status.keys if row.key == key)


# ── Loading and merging ─────────────────────────────────────────────────────


def test_missing_files_give_built_in_defaults(roots):
    _, project = roots
    cfg = wc.load_config(project)
    assert cfg.reuse.default is wc.ReuseLevel.FULL
    assert cfg.reuse.level is None
    assert cfg.agent.model is None
    assert cfg.agent.phases == {}
    assert cfg.issue_tracker.provider is None


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
    cfg = wc.load_config(project)
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA
    assert cfg.review.effort is Effort.HIGH
    assert cfg.agent.thinking is Thinking.MEDIUM
    assert cfg.agent.phases[Phase.SCOUT].model == "haiku"


def test_project_config_wins_over_global(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    _write(project / ".workbench.yml", "agent:\n  model: opus\n")
    assert wc.load_config(project).agent.model == "opus"


def test_project_config_does_not_discard_global_siblings(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: sonnet
  thinking: medium
issue_tracker:
  provider: github
  team: ENG
""")
    _write(project / ".workbench.yml", "agent:\n  phases:\n    fix:\n      model: opus\n")
    cfg = wc.load_config(project)
    assert cfg.agent.model == "sonnet"
    assert cfg.agent.thinking is Thinking.MEDIUM
    assert cfg.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.issue_tracker.team == "ENG"
    assert cfg.agent.phases[Phase.FIX].model == "opus"


def test_an_empty_file_is_not_an_error(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "")
    assert wc.load_config(project) == wc.WorkbenchConfig()


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_reads_the_same_config(roots, monkeypatch):
    """PyYAML is optional, so the yq path has to produce the same answer."""
    monkeypatch.setattr(wc, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\nreview:\n  effort: high\n")
    cfg = wc.load_config(project)
    assert cfg.agent.model == "sonnet"
    assert cfg.review.effort is Effort.HIGH


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_rejects_malformed_yaml(roots, monkeypatch):
    monkeypatch.setattr(wc, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: [unclosed\n")
    with pytest.raises(wc.ConfigError):
        wc.load_config(project)


# ── Error handling ──────────────────────────────────────────────────────────


def test_unknown_enum_value_is_rejected_by_file_name(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  thinking: turbo\n")
    with pytest.raises(wc.ConfigError) as excinfo:
        wc.load_config(project)
    assert "config.yml" in str(excinfo.value)
    assert "turbo" in str(excinfo.value)


def test_unknown_phase_key_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  phases:\n    scoot:\n      model: haiku\n")
    with pytest.raises(wc.ConfigError, match="scoot"):
        wc.load_config(project)


def test_load_config_or_default_survives_a_bad_file(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  thinking: turbo\n")
    assert wc.load_config_or_default(project) == wc.WorkbenchConfig()


def test_malformed_yaml_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: [unclosed\n")
    with pytest.raises(wc.ConfigError):
        wc.load_config(project)


def test_a_non_mapping_file_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "- one\n- two\n")
    with pytest.raises(wc.ConfigError, match="mapping"):
        wc.load_config(project)


# ── The container scope ─────────────────────────────────────────────────────


def test_a_worktree_of_a_bare_repo_gains_a_container_scope(roots, container):
    config_root, _ = roots
    assert [s.path for s in wc.config_scopes(container / "main")] == [
        config_root / wc.CONFIG_NAME,
        container / wc.PROJECT_CONFIG_NAME,
        container / "main" / wc.PROJECT_CONFIG_NAME,
    ]


def test_the_container_sits_between_the_two_older_scopes(roots, container):
    """Merge order, so the report's precedence order is its reverse."""
    assert [s.name for s in wc.config_scopes(container / "main")] == [
        wc.GLOBAL_SCOPE, wc.CONTAINER_SCOPE, wc.PROJECT_SCOPE,
    ]
    assert [s.name for s in wcr.config_status(container / "main").scopes] == [
        wc.PROJECT_SCOPE, wc.CONTAINER_SCOPE, wc.GLOBAL_SCOPE,
    ]


def test_a_plain_clone_keeps_the_two_scopes_it_always_had(roots, tmp_path):
    clone = seed_repo(tmp_path / "clone")
    assert wc.container_config_path(clone) is None
    assert [s.name for s in wc.config_scopes(clone)] == [wc.GLOBAL_SCOPE, wc.PROJECT_SCOPE]


def test_the_container_file_beats_the_global_one(roots, container):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    _write(container / wc.PROJECT_CONFIG_NAME, "agent:\n  model: opus\n")
    assert wc.load_config(container / "main").agent.model == "opus"


def test_the_worktree_file_beats_the_container_one(roots, container):
    _write(container / wc.PROJECT_CONFIG_NAME, "agent:\n  model: opus\n")
    _write(container / "main" / wc.PROJECT_CONFIG_NAME, "agent:\n  model: haiku\n")
    assert wc.load_config(container / "main").agent.model == "haiku"


def test_the_container_does_not_discard_global_siblings(roots, container):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n  thinking: medium\n")
    _write(container / wc.PROJECT_CONFIG_NAME, "agent:\n  model: opus\n")
    cfg = wc.load_config(container / "main")
    assert cfg.agent.model == "opus"
    assert cfg.agent.thinking is Thinking.MEDIUM


def test_a_container_value_names_the_container_in_the_report(roots, container):
    _write(container / wc.PROJECT_CONFIG_NAME, "issue_tracker:\n  provider: github\n")
    status = wcr.config_status(container / "main")
    assert _row(status, "issue_tracker.provider").scope.name == wc.CONTAINER_SCOPE


def test_set_container_value_writes_above_the_worktrees(roots, container):
    wcw.set_container_value("issue_tracker.provider", "github", container / "main")
    assert not (container / "main" / wc.PROJECT_CONFIG_NAME).exists()
    assert "github" in (container / wc.PROJECT_CONFIG_NAME).read_text()


def test_a_sibling_worktree_reads_what_the_container_recorded(roots, container):
    """The reason the scope exists: `wt switch -c` cuts a checkout holding
    nothing, and a worktree file would have to be copied into it by hand."""
    wcw.set_container_value("issue_tracker.provider", "github", container / "main")
    feature = add_worktree(container, "feature")
    assert wc.load_config(feature).issue_tracker.provider is wc.IssueProvider.GITHUB


def test_set_container_value_refuses_a_plain_clone(roots, tmp_path):
    """Falling back to the worktree would answer the opposite of what was asked:
    that file is deleted by `wt remove` and unseen by every sibling checkout."""
    clone = seed_repo(tmp_path / "clone")
    with pytest.raises(wc.ConfigError, match="container"):
        wcw.set_container_value("issue_tracker.provider", "github", clone)
    assert not (clone / wc.PROJECT_CONFIG_NAME).exists()


def test_set_container_value_refuses_the_same_keys(roots, container):
    with pytest.raises(wc.ConfigKeyError):
        wcw.set_container_value("issue_tracker.providr", "github", container / "main")


# ── The key surface ─────────────────────────────────────────────────────────


def test_schema_lists_every_phase_as_a_valid_key():
    """`surface_schema` is what a dotted key is judged against, here and installed.

    The renderings built on it are `workbench_config_report`'s, and are tested
    there; this is the surface itself, which the write guard reads directly.
    """
    phases = wc.surface_schema()["properties"]["agent"]["properties"]["phases"]
    assert phases["propertyNames"]["enum"] == [p.value for p in Phase]


# ── Writing ─────────────────────────────────────────────────────────────────


def test_set_value_creates_and_updates_the_global_file(roots):
    config_root, _ = roots
    wcw.set_value("reuse.level", "ultra")
    assert wc.load_config().reuse.level is wc.ReuseLevel.ULTRA
    wcw.set_value("reuse.level", "lite")
    assert wc.load_config().reuse.level is wc.ReuseLevel.LITE
    assert (config_root / "config.yml").is_file()


def test_set_value_preserves_unrelated_keys(roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    wcw.set_value("reuse.level", "ultra")
    cfg = wc.load_config()
    assert cfg.agent.model == "sonnet"
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA


# ── Schema modeline ─────────────────────────────────────────────────────────


def test_the_schema_url_points_at_a_path_the_repo_actually_has():
    """A moved or renamed schema has to move the URL with it.

    The modeline is the only consumer of the committed schema, and a URL
    pointing at a path the repo no longer has fails silently — in someone
    else's editor, months later. No network here, but neither a rename nor a
    move into a subdirectory gets past it.
    """
    assert (REPO_ROOT / wc.SCHEMA_PATH).is_file()
    assert wc.SCHEMA_URL == f"{wc.REPO_RAW_URL}/{wc.SCHEMA_PATH}"


def test_a_new_config_file_is_born_with_the_modeline(roots):
    config_root, _ = roots
    wcw.set_value("reuse.level", "ultra")
    assert (config_root / "config.yml").read_text().startswith(wc.CONFIG_HEADER)


def test_the_modeline_survives_later_writes(roots):
    """yq is the writer precisely because it carries comments through."""
    config_root, _ = roots
    wcw.set_value("reuse.level", "ultra")
    wcw.set_value("agent.model", "sonnet")
    text = (config_root / "config.yml").read_text()
    assert text.startswith(wc.CONFIG_HEADER)
    assert text.count(wc.CONFIG_HEADER) == 1
    cfg = wc.load_config()
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA
    assert cfg.agent.model == "sonnet"


def test_a_modeline_only_file_reads_as_an_empty_config(roots):
    """The seeded file is comments and nothing else until the first key lands."""
    config_root, project = roots
    _write(config_root / "config.yml", wc.CONFIG_HEADER + "\n")
    cfg = wc.load_config(project)
    assert cfg.reuse.level is None
    assert cfg.reuse.default is wc.ReuseLevel.FULL


@needs_yaml
def test_the_pyyaml_fallback_puts_the_modeline_back(roots, monkeypatch):
    """Without yq the document is re-rendered, so the header is re-applied.

    Every comment the user wrote is still lost on this path — the modeline is
    the one this module owns and can restore.
    """
    config_root, _ = roots
    monkeypatch.setattr(wcw.shutil, "which", lambda _: None)
    wcw.set_value("reuse.level", "ultra")
    wcw.set_value("agent.model", "sonnet")
    text = (config_root / "config.yml").read_text()
    assert text.startswith(wc.CONFIG_HEADER)
    assert text.count(wc.CONFIG_HEADER) == 1
    assert wc.load_config().agent.model == "sonnet"


@needs_yaml
def test_the_pyyaml_fallback_adds_no_modeline_to_a_file_without_one(
    roots, monkeypatch,
):
    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: sonnet\n")
    monkeypatch.setattr(wcw.shutil, "which", lambda _: None)
    wcw.set_value("reuse.level", "ultra")
    assert wc.CONFIG_HEADER not in (config_root / "config.yml").read_text()


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
    from agent import phases as agent_phases

    config_root, project = roots
    _write(config_root / "config.yml", "agent:\n  model: from-global\n")
    cfg = wc.load_config(project)
    assert agent_phases.phase_model(Phase.SCOUT, None, cfg) == "from-global"


def test_layer_4_project_config_beats_the_global(phase_cfg):
    from agent import phases as agent_phases

    cfg = wc.load_config(phase_cfg)
    assert agent_phases.phase_model(Phase.SCOUT, None, cfg) == "project-phase"


def test_a_phase_entry_beats_the_section_within_one_file(roots):
    from agent import phases as agent_phases

    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  model: section
  phases:
    scout:
      model: phase
""")
    cfg = wc.load_config(project)
    assert agent_phases.phase_model(Phase.SCOUT, None, cfg) == "phase"
    assert agent_phases.phase_model(Phase.FIX, None, cfg) == "section"


def test_layer_3_global_env_beats_the_config(phase_cfg, monkeypatch):
    from agent import phases as agent_phases

    monkeypatch.setenv("WORKBENCH_AI_MODEL", "from-env")
    cfg = wc.load_config(phase_cfg)
    assert agent_phases.phase_model(Phase.SCOUT, None, cfg) == "from-env"


def test_layer_2_phase_env_beats_the_global_env(phase_cfg, monkeypatch):
    from agent import phases as agent_phases

    monkeypatch.setenv("WORKBENCH_AI_MODEL", "from-env")
    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "from-phase-env")
    cfg = wc.load_config(phase_cfg)
    assert agent_phases.phase_model(Phase.SCOUT, None, cfg) == "from-phase-env"


def test_layer_1_explicit_beats_every_env_and_file(phase_cfg, monkeypatch):
    from agent import phases as agent_phases

    monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "from-phase-env")
    cfg = wc.load_config(phase_cfg)
    assert agent_phases.phase_model(Phase.SCOUT, "explicit", cfg) == "explicit"


def test_phase_model_loads_the_config_itself_when_not_given_one(roots):
    """The default argument is what a single-value caller relies on."""
    from agent import phases as agent_phases

    config_root, _ = roots
    _write(config_root / "config.yml", "agent:\n  model: from-disk\n")
    assert agent_phases.phase_model(Phase.SCOUT, None) == "from-disk"


def test_thinking_layers_the_same_way(roots):
    from agent import phases as agent_phases

    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  thinking: low
  phases:
    scout:
      thinking: high
""")
    cfg = wc.load_config(project)
    assert agent_phases.phase_thinking_default(Phase.SCOUT, Effort.MEDIUM, cfg) is Thinking.HIGH
    assert agent_phases.phase_thinking_default(Phase.FIX, Effort.MEDIUM, cfg) is Thinking.LOW


def test_thinking_falls_back_to_the_effort_preset(roots):
    from agent import phases as agent_phases
    from agent.types import EFFORT_PRESETS

    _, project = roots
    cfg = wc.load_config(project)
    assert agent_phases.phase_thinking_default(
        Phase.SCOUT, Effort.HIGH, cfg,
    ) == EFFORT_PRESETS[Effort.HIGH].thinking


def test_effort_falls_back_from_config_to_the_built_in(roots):
    from agent import phases as agent_phases

    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  effort: high\n")
    assert agent_phases.resolve_effort(None, wc.load_config(project)) is Effort.HIGH
    assert agent_phases.resolve_effort(Effort.LOW, wc.load_config(project)) is Effort.LOW
    assert agent_phases.resolve_effort(None, wc.WorkbenchConfig()) is Effort.MEDIUM


# ── Per-repo adoption of .claude/review.yml ─────────────────────────────────


def test_adopt_converts_a_project_review_yml(roots):
    from review import issue as review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(
        project / ".claude" / "review.yml",
        "issue_tracker:\n  provider: github\n  team: ENG\n",
    )

    assert review_issue.adopt_project_review_yml(str(project)) is True

    cfg = wc.load_config(project)
    assert cfg.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.issue_tracker.team == "ENG"


def test_adopt_writes_the_top_level_key_not_the_legacy_nesting(roots):
    """The old file's key was review-namespaced; the config's is not."""
    from review import issue as review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review_issue.adopt_project_review_yml(str(project))
    assert "review:" not in (project / ".workbench.yml").read_text()


def test_adopt_seeds_the_modeline_like_every_other_creator(roots):
    """docs/libraries.md promises every workbench-created file carries it."""
    from review import issue as review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review_issue.adopt_project_review_yml(str(project))
    assert (project / ".workbench.yml").read_text().startswith(wc.CONFIG_HEADER + "\n")


def test_adopt_leaves_the_old_file_in_place(roots):
    from review import issue as review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review_issue.adopt_project_review_yml(str(project))
    assert (project / ".claude" / "review.yml").is_file()


def test_adopt_is_a_no_op_when_workbench_yml_exists(roots):
    from review import issue as review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")
    _write(project / ".workbench.yml", "issue_tracker:\n  provider: jira\n")

    assert review_issue.adopt_project_review_yml(str(project)) is False
    assert wc.load_config(project).issue_tracker.provider is wc.IssueProvider.JIRA


def test_adopt_is_a_no_op_without_an_old_file(roots):
    from review import issue as review_issue

    _, project = roots
    assert review_issue.adopt_project_review_yml(str(project)) is False
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
    assert wc.load_config().reuse.level is wc.ReuseLevel.ULTRA


def test_reuse_default_round_trips_through_the_config(reuse_levels):
    reuse_levels.write_default("lite")
    assert reuse_levels.read_default() == "lite"
    assert wc.load_config().reuse.default is wc.ReuseLevel.LITE


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
    _write(project / wc.PROJECT_CONFIG_NAME, """
issue_tracker:
  provider: github
""")
    assert wc.load_config(project).issue_tracker.provider is wc.IssueProvider.GITHUB


def test_set_project_value_writes_the_repo_config(roots):
    _, project = roots
    wcw.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
    cfg = wc.load_config(project)
    assert cfg.issue_tracker.provider is wc.IssueProvider.GITHUB


def test_set_project_value_preserves_hand_written_comments(roots):
    """yq goes first precisely so a hand-authored file keeps its comments."""
    _, project = roots
    _write(project / wc.PROJECT_CONFIG_NAME, """
# we file on GitHub, not Linear
issue_tracker:
  team: ENG
""")
    wcw.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
    assert "# we file on GitHub, not Linear" in (project / wc.PROJECT_CONFIG_NAME).read_text()
    assert wc.load_config(project).issue_tracker.team == "ENG"


def test_set_project_value_seeds_the_schema_modeline(roots):
    """A file the workbench creates gets completion, same as the global one."""
    _, project = roots
    wcw.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
    assert (project / wc.PROJECT_CONFIG_NAME).read_text().startswith(wc.CONFIG_HEADER)


def test_set_project_value_does_not_touch_the_global_config(roots):
    config_root, project = roots
    wcw.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
    assert not (config_root / wc.CONFIG_NAME).exists()


# ── The key guard ───────────────────────────────────────────────────────────
#
# Both config files are shared — the global one by every repo on the machine,
# the project one by everyone who clones — and `serde` drops a key it does not
# know, so a write under the wrong name is lost with no error at either end.
# `check_key` is what turns that into a refusal at write time.


@pytest.fixture
def stale_install(tmp_path, monkeypatch):
    """Point ``check_key`` at an installed schema that lacks ``issue_tracker``.

    The incident with the two checkouts swapped: there the writing checkout was
    the stale one, and here it is this checkout that knows the key the install
    does not. The mechanism under test is the same either way — a write is
    judged by the surface the machine reads, not by the one in front of it —
    and it is the only direction a test can build, since the local surface is
    whatever this checkout ships.
    """
    schema = json.loads(wcr.schema_json())
    tracker = schema["properties"].pop("issue_tracker")
    schema["properties"]["review"]["properties"]["issue_tracker"] = tracker
    path = tmp_path / "installed" / wc.SCHEMA_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(schema))
    monkeypatch.setattr(wcw, "installed_schema_path", lambda: path)
    return path


def test_set_value_refuses_a_key_the_config_does_not_define(roots):
    config_root, _ = roots
    with pytest.raises(wc.ConfigKeyError) as exc:
        wcw.set_value("reuse.levl", "ultra")
    assert "reuse.levl" in str(exc.value)
    assert not (config_root / wc.CONFIG_NAME).exists()


def test_set_value_refuses_the_shape_the_key_moved_off(roots):
    """The literal key the incident wrote, judged by the surface it moved to."""
    with pytest.raises(wc.ConfigKeyError):
        wcw.set_value("review.issue_tracker.provider", "github")


def test_a_refused_key_is_a_config_error_too(roots):
    """A caller that only handles the general failure still catches this one."""
    assert issubclass(wc.ConfigKeyError, wc.ConfigError)
    with pytest.raises(wc.ConfigError):
        wcw.set_value("nonsense", "x")


def test_set_project_value_refuses_the_same_keys(roots):
    """A repo file is committed, so a dead key travels to everyone who clones."""
    _, project = roots
    with pytest.raises(wc.ConfigKeyError):
        wcw.set_project_value("issue_tracker.provdier", "github", project)
    assert not (project / wc.PROJECT_CONFIG_NAME).exists()


def test_a_key_the_installed_workbench_does_not_read_is_refused(roots, stale_install):
    config_root, _ = roots
    with pytest.raises(wc.ConfigKeyError) as exc:
        wcw.set_value(wc.ISSUE_PROVIDER_KEY, "github")
    assert str(stale_install) in str(exc.value)
    assert not (config_root / wc.CONFIG_NAME).exists()


def test_a_key_both_surfaces_read_is_written(roots, stale_install):
    """The installed surface refuses keys; it does not refuse writing."""
    wcw.set_value("reuse.level", "ultra")
    assert wc.load_config().reuse.level is wc.ReuseLevel.ULTRA


def test_no_installed_workbench_leaves_the_local_surface(roots, monkeypatch):
    """CI and a fresh clone have no install, and still have to be able to write."""
    monkeypatch.setattr(wcw, "installed_schema_path", lambda: None)
    wcw.set_value(wc.ISSUE_PROVIDER_KEY, "github")
    assert wc.load_config().issue_tracker.provider is wc.IssueProvider.GITHUB


def test_an_unreadable_installed_schema_leaves_the_local_surface(roots, tmp_path, monkeypatch):
    """One broken file must not make the config unwritable machine-wide."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(wcw, "installed_schema_path", lambda: broken)
    wcw.set_value(wc.ISSUE_PROVIDER_KEY, "github")
    assert wc.load_config().issue_tracker.provider is wc.IssueProvider.GITHUB


def test_an_enum_keyed_section_is_writable_by_its_declared_keys(roots):
    """`agent.phases.<phase>` is a dict, so the guard reads propertyNames."""
    wcw.set_value(f"agent.phases.{Phase.SCOUT}.model", "sonnet")
    assert wc.load_config().agent.phases[Phase.SCOUT].model == "sonnet"
    with pytest.raises(wc.ConfigKeyError):
        wcw.set_value("agent.phases.nosuchphase.model", "sonnet")


def test_check_key_says_which_surface_refused(stale_install):
    assert wcw.check_key("reuse.level").ok
    here = wcw.check_key("reuse.levl")
    assert not here.ok
    assert here.verdict is wcw.KeyVerdict.UNKNOWN_HERE
    assert "WorkbenchConfig defines" in here.reason
    installed = wcw.check_key(wc.ISSUE_PROVIDER_KEY)
    assert not installed.ok
    assert installed.verdict is wcw.KeyVerdict.UNKNOWN_INSTALLED
    assert "the two disagree about where the value lives" in installed.reason
    assert wcw.check_key("reuse.level").reason == ""


def test_installed_schema_path_resolves_through_the_launcher(tmp_path, monkeypatch):
    """The PATH symlink is the whole mechanism — a worktree cannot fake it."""
    installed = tmp_path / "checkout"
    (installed / "bin").mkdir(parents=True)
    (installed / "bin" / wcw.INSTALLED_LAUNCHER).write_text("#!/bin/sh\n")
    (installed / wc.SCHEMA_PATH).write_text("{}")
    monkeypatch.setattr(wcw.shutil, "which",
                        lambda name: str(installed / "bin" / name))
    assert wcw.installed_schema_path() == installed / wc.SCHEMA_PATH


def test_installed_schema_path_is_none_without_an_install(monkeypatch):
    monkeypatch.setattr(wcw.shutil, "which", lambda name: None)
    assert wcw.installed_schema_path() is None


# ── The value guard ─────────────────────────────────────────────────────────
#
# The same loss as the key guard catches, one level down. Every value arrives
# as a string off a command line, and `serde` replaces a scalar it cannot
# convert with the field's default — so a boolean written as `"true"` is a
# write that reports success and leaves the setting off.


def test_a_boolean_key_is_written_as_a_boolean(roots):
    config_root, _ = roots
    wcw.set_value(wc.GITHUB_SSH_443_KEY, "true")
    assert "ssh_over_443: true" in (config_root / wc.CONFIG_NAME).read_text()
    assert wc.load_config().github.ssh_over_443 is True


def test_a_boolean_key_round_trips_back_to_false(roots):
    wcw.set_value(wc.GITHUB_SSH_443_KEY, "true")
    wcw.set_value(wc.GITHUB_SSH_443_KEY, "false")
    assert wc.load_config().github.ssh_over_443 is False


def test_a_boolean_key_refuses_a_value_that_is_neither(roots):
    """`bool("yes")` is True and so is `bool("no")`, which is why nothing guesses."""
    config_root, _ = roots
    with pytest.raises(wc.ConfigValueError) as exc:
        wcw.set_value(wc.GITHUB_SSH_443_KEY, "yes")
    assert wc.GITHUB_SSH_443_KEY in str(exc.value)
    assert not (config_root / wc.CONFIG_NAME).exists()


def test_a_refused_value_is_a_config_error_too(roots):
    """A caller that only handles the general failure still catches this one."""
    assert issubclass(wc.ConfigValueError, wc.ConfigError)
    with pytest.raises(wc.ConfigError):
        wcw.set_value(wc.GITHUB_SSH_443_KEY, "sideways")


def test_a_refused_value_is_not_a_refused_key(roots):
    """The two failures owe the caller different advice, so they are different types."""
    with pytest.raises(wc.ConfigValueError):
        wcw.set_value(wc.GITHUB_SSH_443_KEY, "sideways")
    assert not issubclass(wc.ConfigValueError, wc.ConfigKeyError)


def test_a_string_key_is_written_as_the_string_it_was_given(roots):
    """A value that looks like a bool under a string field stays a string."""
    _, project = roots
    wcw.set_project_value("issue_tracker.team", "true", project)
    assert wc.load_config(project).issue_tracker.team == "true"


@needs_yaml
def test_the_pyyaml_fallback_writes_the_same_types(roots, monkeypatch):
    """Two writers, one coercion — the fallback cannot disagree about the type."""
    monkeypatch.setattr(wcw.shutil, "which", lambda _: None)
    wcw.set_value(wc.GITHUB_SSH_443_KEY, "true")
    wcw.set_value("agent.model", "sonnet")
    cfg = wc.load_config()
    assert cfg.github.ssh_over_443 is True
    assert cfg.agent.model == "sonnet"


def test_a_numeric_field_is_parsed_into_the_number_it_names(monkeypatch):
    """No key on the surface is numeric yet, so the branch is reached by its type."""
    monkeypatch.setattr(wcw, "schema_type", lambda _: "integer")
    assert wcw.coerce_value("some.count", "3") == 3
    monkeypatch.setattr(wcw, "schema_type", lambda _: "number")
    assert wcw.coerce_value("some.ratio", "1.5") == 1.5


def test_a_numeric_field_refuses_a_value_that_is_not_a_number(monkeypatch):
    monkeypatch.setattr(wcw, "schema_type", lambda _: "integer")
    with pytest.raises(wc.ConfigValueError) as exc:
        wcw.coerce_value("some.count", "a few")
    assert "some.count" in str(exc.value)


def test_a_numeric_field_refuses_the_floats_yaml_cannot_spell(monkeypatch):
    """`float("nan")` parses, and `.key = nan` is a bare word yq reads as nothing."""
    monkeypatch.setattr(wcw, "schema_type", lambda _: "number")
    for spelled in ("nan", "inf", "-inf"):
        with pytest.raises(wc.ConfigValueError):
            wcw.coerce_value("some.ratio", spelled)


def test_an_optional_field_is_typed_through_its_null_half():
    """`str | None` is a union in the schema and a string to a writer."""
    schema = wc.surface_schema()
    assert wc.schema_type(wc.schema_at(schema, "reuse.level")) == "string"
    assert wc.schema_type(wc.schema_at(schema, wc.GITHUB_SSH_443_KEY)) == "boolean"


def test_a_fragment_that_names_no_type_is_left_permissive():
    """`schema_gen` emits an open fragment for a hint it cannot describe.

    A check that cannot see the type must not be the thing that refuses a
    write, which is the same direction the key walk is permissive in.
    """
    assert wc.schema_type({}) is None
