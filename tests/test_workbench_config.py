"""Tests for workbench_config — the typed, layered workbench configuration."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import workbench_config as wc
from review_common import Effort, Phase, Thinking

# The PyYAML write path only exists for a machine without yq, so the tests for
# it only run where PyYAML is installed — the same shape test_review_profiles
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ── Loading and merging ─────────────────────────────────────────────────────


def test_missing_files_give_built_in_defaults(roots):
    _, project = roots
    cfg = wc.load_config(project)
    assert cfg.reuse.default is wc.ReuseLevel.FULL
    assert cfg.reuse.level is None
    assert cfg.review.model is None
    assert cfg.review.phases == {}
    assert cfg.review.issue_tracker.provider is wc.IssueProvider.LINEAR


def test_global_config_is_typed(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
reuse:
  level: ultra
review:
  effort: high
  thinking: medium
  phases:
    scout:
      model: haiku
""")
    cfg = wc.load_config(project)
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA
    assert cfg.review.effort is Effort.HIGH
    assert cfg.review.thinking is Thinking.MEDIUM
    assert cfg.review.phases[Phase.SCOUT].model == "haiku"


def test_project_config_wins_over_global(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n")
    _write(project / ".workbench.yml", "review:\n  model: opus\n")
    assert wc.load_config(project).review.model == "opus"


def test_project_config_does_not_discard_global_siblings(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
review:
  model: sonnet
  issue_tracker:
    provider: github
    team: ENG
""")
    _write(project / ".workbench.yml", "review:\n  phases:\n    fix:\n      model: opus\n")
    cfg = wc.load_config(project)
    assert cfg.review.model == "sonnet"
    assert cfg.review.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.review.issue_tracker.team == "ENG"
    assert cfg.review.phases[Phase.FIX].model == "opus"


def test_an_empty_file_is_not_an_error(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "")
    assert wc.load_config(project) == wc.WorkbenchConfig()


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_reads_the_same_config(roots, monkeypatch):
    """PyYAML is optional, so the yq path has to produce the same answer."""
    monkeypatch.setattr(wc, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n  effort: high\n")
    cfg = wc.load_config(project)
    assert cfg.review.model == "sonnet"
    assert cfg.review.effort is Effort.HIGH


@pytest.mark.skipif(not shutil.which("yq"), reason="yq is the fallback under test")
def test_the_yq_fallback_rejects_malformed_yaml(roots, monkeypatch):
    monkeypatch.setattr(wc, "yaml", None)
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: [unclosed\n")
    with pytest.raises(wc.ConfigError):
        wc.load_config(project)


# ── Error handling ──────────────────────────────────────────────────────────


def test_unknown_enum_value_is_rejected_by_file_name(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  thinking: turbo\n")
    with pytest.raises(wc.ConfigError) as excinfo:
        wc.load_config(project)
    assert "config.yml" in str(excinfo.value)
    assert "turbo" in str(excinfo.value)


def test_unknown_phase_key_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  phases:\n    scoot:\n      model: haiku\n")
    with pytest.raises(wc.ConfigError, match="scoot"):
        wc.load_config(project)


def test_load_config_or_default_survives_a_bad_file(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  thinking: turbo\n")
    assert wc.load_config_or_default(project) == wc.WorkbenchConfig()


def test_malformed_yaml_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: [unclosed\n")
    with pytest.raises(wc.ConfigError):
        wc.load_config(project)


def test_a_non_mapping_file_is_rejected(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "- one\n- two\n")
    with pytest.raises(wc.ConfigError, match="mapping"):
        wc.load_config(project)


# ── Schema ──────────────────────────────────────────────────────────────────


def test_committed_schema_matches_the_generator():
    """The committed schema is generated, so drift is a test failure.

    CLAUDE.md requires a cross-validation test wherever the same defaults
    appear in two formats. This is that test: renaming a field or adding a
    Phase member fails here until `bin/local/generate-config-schema` is re-run.
    """
    committed = json.loads((_repo_root() / wc.SCHEMA_PATH).read_text())
    assert committed == json.loads(wc.schema_json())


def test_committed_docs_reference_matches_the_generator():
    """The key table in the docs is generated from the same dataclass.

    The prose around it is hand-written; this covers the spliced block, which
    is where a new key or a changed default would otherwise go unmentioned.
    """
    text = (_repo_root() / wc.DOCS_PATH).read_text()
    start = f"<!-- {wc.DOCS_MARKER}-START -->\n"
    end = f"\n<!-- {wc.DOCS_MARKER}-END -->"
    assert start in text and end in text, f"{wc.DOCS_PATH} lost its splice markers"
    block = text.split(start, 1)[1].split(end, 1)[0]
    assert block == wc.docs_reference()


def test_every_written_key_resolves_to_a_field():
    """A dotted key nothing answers to writes a field `serde` then drops.

    `set_value` does not check its argument, so the constants naming the keys
    other modules write are checked here instead — against the same walk the
    docs table is built from, so a renamed field fails rather than silently
    stranding the value it used to hold.
    """
    keys = {key for key, _, _ in wc._reference_rows(wc.WorkbenchConfig)}
    assert wc.REUSE_LEVEL_KEY in keys
    assert wc.REUSE_DEFAULT_KEY in keys


def test_schema_lists_every_phase_as_a_valid_key():
    import schema_gen

    schema = schema_gen.dataclass_to_schema(wc.WorkbenchConfig)
    phases = schema["properties"]["review"]["properties"]["phases"]
    assert phases["propertyNames"]["enum"] == [p.value for p in Phase]


# ── Writing ─────────────────────────────────────────────────────────────────


def test_set_value_creates_and_updates_the_global_file(roots):
    config_root, _ = roots
    wc.set_value("reuse.level", "ultra")
    assert wc.load_config().reuse.level is wc.ReuseLevel.ULTRA
    wc.set_value("reuse.level", "lite")
    assert wc.load_config().reuse.level is wc.ReuseLevel.LITE
    assert (config_root / "config.yml").is_file()


def test_set_value_preserves_unrelated_keys(roots):
    config_root, _ = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n")
    wc.set_value("reuse.level", "ultra")
    cfg = wc.load_config()
    assert cfg.review.model == "sonnet"
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA


# ── Schema modeline ─────────────────────────────────────────────────────────


def test_the_schema_url_points_at_a_path_the_repo_actually_has():
    """A moved or renamed schema has to move the URL with it.

    The modeline is the only consumer of the committed schema, and a URL
    pointing at a path the repo no longer has fails silently — in someone
    else's editor, months later. No network here, but neither a rename nor a
    move into a subdirectory gets past it.
    """
    assert (_repo_root() / wc.SCHEMA_PATH).is_file()
    assert wc.SCHEMA_URL == f"{wc.REPO_RAW_URL}/{wc.SCHEMA_PATH}"


def test_a_new_config_file_is_born_with_the_modeline(roots):
    config_root, _ = roots
    wc.set_value("reuse.level", "ultra")
    assert (config_root / "config.yml").read_text().startswith(wc.CONFIG_HEADER)


def test_the_modeline_survives_later_writes(roots):
    """yq is the writer precisely because it carries comments through."""
    config_root, _ = roots
    wc.set_value("reuse.level", "ultra")
    wc.set_value("review.model", "sonnet")
    text = (config_root / "config.yml").read_text()
    assert text.startswith(wc.CONFIG_HEADER)
    assert text.count(wc.CONFIG_HEADER) == 1
    cfg = wc.load_config()
    assert cfg.reuse.level is wc.ReuseLevel.ULTRA
    assert cfg.review.model == "sonnet"


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
    monkeypatch.setattr(wc.shutil, "which", lambda _: None)
    wc.set_value("reuse.level", "ultra")
    wc.set_value("review.model", "sonnet")
    text = (config_root / "config.yml").read_text()
    assert text.startswith(wc.CONFIG_HEADER)
    assert text.count(wc.CONFIG_HEADER) == 1
    assert wc.load_config().review.model == "sonnet"


@needs_yaml
def test_the_pyyaml_fallback_adds_no_modeline_to_a_file_without_one(
    roots, monkeypatch,
):
    config_root, _ = roots
    _write(config_root / "config.yml", "review:\n  model: sonnet\n")
    monkeypatch.setattr(wc.shutil, "which", lambda _: None)
    wc.set_value("reuse.level", "ultra")
    assert wc.CONFIG_HEADER not in (config_root / "config.yml").read_text()


# ── Precedence across all five layers ───────────────────────────────────────


@pytest.fixture
def phase_cfg(roots):
    """A config that sets a phase model at both scopes, for layering tests."""
    config_root, project = roots
    _write(config_root / "config.yml", """
review:
  model: global-section
  phases:
    scout:
      model: global-phase
""")
    _write(project / ".workbench.yml", """
review:
  phases:
    scout:
      model: project-phase
""")
    return project


def test_layer_5_global_config_beats_the_built_in(roots):
    import review_phases

    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  model: from-global\n")
    cfg = wc.load_config(project)
    assert review_phases.phase_model(Phase.SCOUT, None, cfg) == "from-global"


def test_layer_4_project_config_beats_the_global(phase_cfg):
    import review_phases

    cfg = wc.load_config(phase_cfg)
    assert review_phases.phase_model(Phase.SCOUT, None, cfg) == "project-phase"


def test_a_phase_entry_beats_the_section_within_one_file(roots):
    import review_phases

    config_root, project = roots
    _write(config_root / "config.yml", """
review:
  model: section
  phases:
    scout:
      model: phase
""")
    cfg = wc.load_config(project)
    assert review_phases.phase_model(Phase.SCOUT, None, cfg) == "phase"
    assert review_phases.phase_model(Phase.FIX, None, cfg) == "section"


def test_layer_3_global_env_beats_the_config(phase_cfg, monkeypatch):
    import review_phases

    monkeypatch.setenv("CLAUDE_REVIEW_MODEL", "from-env")
    cfg = wc.load_config(phase_cfg)
    assert review_phases.phase_model(Phase.SCOUT, None, cfg) == "from-env"


def test_layer_2_phase_env_beats_the_global_env(phase_cfg, monkeypatch):
    import review_phases

    monkeypatch.setenv("CLAUDE_REVIEW_MODEL", "from-env")
    monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "from-phase-env")
    cfg = wc.load_config(phase_cfg)
    assert review_phases.phase_model(Phase.SCOUT, None, cfg) == "from-phase-env"


def test_layer_1_explicit_beats_every_env_and_file(phase_cfg, monkeypatch):
    import review_phases

    monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "from-phase-env")
    cfg = wc.load_config(phase_cfg)
    assert review_phases.phase_model(Phase.SCOUT, "explicit", cfg) == "explicit"


def test_phase_model_loads_the_config_itself_when_not_given_one(roots):
    """The default argument is what a single-value caller relies on."""
    import review_phases

    config_root, _ = roots
    _write(config_root / "config.yml", "review:\n  model: from-disk\n")
    assert review_phases.phase_model(Phase.SCOUT, None) == "from-disk"


def test_thinking_layers_the_same_way(roots):
    import review_phases

    config_root, project = roots
    _write(config_root / "config.yml", """
review:
  thinking: low
  phases:
    scout:
      thinking: high
""")
    cfg = wc.load_config(project)
    assert review_phases.phase_thinking_default(Phase.SCOUT, Effort.MEDIUM, cfg) is Thinking.HIGH
    assert review_phases.phase_thinking_default(Phase.FIX, Effort.MEDIUM, cfg) is Thinking.LOW


def test_thinking_falls_back_to_the_effort_preset(roots):
    import review_phases
    from review_common import EFFORT_PRESETS

    _, project = roots
    cfg = wc.load_config(project)
    assert review_phases.phase_thinking_default(
        Phase.SCOUT, Effort.HIGH, cfg,
    ) == EFFORT_PRESETS[Effort.HIGH].thinking


def test_effort_falls_back_from_config_to_the_built_in(roots):
    import review_phases

    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  effort: high\n")
    assert review_phases.resolve_effort(None, wc.load_config(project)) is Effort.HIGH
    assert review_phases.resolve_effort(Effort.LOW, wc.load_config(project)) is Effort.LOW
    assert review_phases.resolve_effort(None, wc.WorkbenchConfig()) is Effort.MEDIUM


# ── Per-repo adoption of .claude/review.yml ─────────────────────────────────


def test_adopt_converts_a_project_review_yml(roots):
    import review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(
        project / ".claude" / "review.yml",
        "issue_tracker:\n  provider: github\n  team: ENG\n",
    )

    assert review_issue.adopt_project_review_yml(str(project)) is True

    cfg = wc.load_config(project)
    assert cfg.review.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.review.issue_tracker.team == "ENG"


def test_adopt_leaves_the_old_file_in_place(roots):
    import review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review_issue.adopt_project_review_yml(str(project))
    assert (project / ".claude" / "review.yml").is_file()


def test_adopt_is_a_no_op_when_workbench_yml_exists(roots):
    import review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")
    _write(project / ".workbench.yml", "review:\n  issue_tracker:\n    provider: jira\n")

    assert review_issue.adopt_project_review_yml(str(project)) is False
    assert wc.load_config(project).review.issue_tracker.provider is wc.IssueProvider.JIRA


def test_adopt_is_a_no_op_without_an_old_file(roots):
    import review_issue

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
