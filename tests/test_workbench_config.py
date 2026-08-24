"""Tests for workbench_config — the typed, layered workbench configuration."""

from __future__ import annotations

import json
import os
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
    assert cfg.issue_tracker.provider is None


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
  thinking: medium
issue_tracker:
  provider: github
  team: ENG
""")
    _write(project / ".workbench.yml", "review:\n  phases:\n    fix:\n      model: opus\n")
    cfg = wc.load_config(project)
    assert cfg.review.model == "sonnet"
    assert cfg.review.thinking is Thinking.MEDIUM
    assert cfg.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.issue_tracker.team == "ENG"
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


def test_composed_docs_carry_the_generated_reference():
    """The key table in the docs is generated from the same dataclass.

    `bin/local/validate-docs-composed` is what fails on a stale artifact; this
    fails on a directive that stopped asking for the block at all, which the
    freshness check cannot see — a doc with no directive is consistent with its
    source and simply has no key reference in it.
    """
    text = (_repo_root() / wc.DOCS_PATH).read_text()
    assert wc.docs_reference() in text


def test_the_module_header_asks_for_the_reference_block():
    """`lib/config.sh` is where the directive that pulls the block in lives.

    The block name is a string in two files — the directive and `BLOCKS` in the
    generator — so a rename that misses one leaves the composer failing on an
    unknown block. Naming it here means the pair is checked without composing.
    """
    header = (_repo_root() / "lib" / "config.sh").read_text()
    assert f"<!-- include: {wc.GENERATOR_PATH} --emit config-reference -->" in header


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
    assert wc.ISSUE_PROVIDER_KEY in keys
    assert wc.GITHUB_SSH_443_KEY in keys


def test_the_generator_banner_names_a_script_that_exists():
    """The schema tells the reader to run `GENERATOR_PATH`, and so does the doc.

    The generator itself compares the constant against its own location, so a
    move it did not follow fails there — but only for someone who runs it. This
    fails for everyone, which is what a banner pointing at nothing deserves.
    The docs half is the `--emit` directive, checked above.
    """
    generator = _repo_root() / wc.GENERATOR_PATH
    assert generator.is_file() and os.access(generator, os.X_OK)
    assert wc.GENERATOR_PATH in json.loads(wc.schema_json())["description"]


def test_the_docs_link_to_the_schema_resolves_from_the_docs_directory():
    """The block links to a repo-root file from a doc that is not at the root.

    The `../` depth is derived from `DOCS_PATH`, so moving the doc keeps the
    link pointing at the schema instead of quietly pointing above the repo.
    """
    docs_dir = (_repo_root() / wc.DOCS_PATH).parent
    link = f"({wc._DOCS_TO_ROOT}{wc.SCHEMA_PATH})"
    assert link in wc.docs_reference()
    assert (docs_dir / f"{wc._DOCS_TO_ROOT}{wc.SCHEMA_PATH}").resolve().is_file()


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
    assert cfg.issue_tracker.provider is wc.IssueProvider.GITHUB
    assert cfg.issue_tracker.team == "ENG"


def test_adopt_writes_the_top_level_key_not_the_legacy_nesting(roots):
    """The old file's key was review-namespaced; the config's is not."""
    import review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review_issue.adopt_project_review_yml(str(project))
    assert "review:" not in (project / ".workbench.yml").read_text()


def test_adopt_seeds_the_modeline_like_every_other_creator(roots):
    """docs/libraries.md promises every workbench-created file carries it."""
    import review_issue

    _, project = roots
    (project / ".claude").mkdir()
    _write(project / ".claude" / "review.yml", "issue_tracker:\n  provider: github\n")

    review_issue.adopt_project_review_yml(str(project))
    assert (project / ".workbench.yml").read_text().startswith(wc.CONFIG_HEADER + "\n")


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
    _write(project / ".workbench.yml", "issue_tracker:\n  provider: jira\n")

    assert review_issue.adopt_project_review_yml(str(project)) is False
    assert wc.load_config(project).issue_tracker.provider is wc.IssueProvider.JIRA


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


def test_a_declared_issue_provider_is_still_read(roots):
    _, project = roots
    _write(project / wc.PROJECT_CONFIG_NAME, """
issue_tracker:
  provider: github
""")
    assert wc.load_config(project).issue_tracker.provider is wc.IssueProvider.GITHUB


def test_set_project_value_writes_the_repo_config(roots):
    _, project = roots
    wc.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
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
    wc.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
    assert "# we file on GitHub, not Linear" in (project / wc.PROJECT_CONFIG_NAME).read_text()
    assert wc.load_config(project).issue_tracker.team == "ENG"


def test_set_project_value_seeds_the_schema_modeline(roots):
    """A file the workbench creates gets completion, same as the global one."""
    _, project = roots
    wc.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
    assert (project / wc.PROJECT_CONFIG_NAME).read_text().startswith(wc.CONFIG_HEADER)


def test_set_project_value_does_not_touch_the_global_config(roots):
    config_root, project = roots
    wc.set_project_value(wc.ISSUE_PROVIDER_KEY, "github", project)
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
    schema = json.loads(wc.schema_json())
    tracker = schema["properties"].pop("issue_tracker")
    schema["properties"]["review"]["properties"]["issue_tracker"] = tracker
    path = tmp_path / "installed" / wc.SCHEMA_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(schema))
    monkeypatch.setattr(wc, "installed_schema_path", lambda: path)
    return path


def test_set_value_refuses_a_key_the_config_does_not_define(roots):
    config_root, _ = roots
    with pytest.raises(wc.ConfigKeyError) as exc:
        wc.set_value("reuse.levl", "ultra")
    assert "reuse.levl" in str(exc.value)
    assert not (config_root / wc.CONFIG_NAME).exists()


def test_set_value_refuses_the_shape_the_key_moved_off(roots):
    """The literal key the incident wrote, judged by the surface it moved to."""
    with pytest.raises(wc.ConfigKeyError):
        wc.set_value("review.issue_tracker.provider", "github")


def test_a_refused_key_is_a_config_error_too(roots):
    """A caller that only handles the general failure still catches this one."""
    assert issubclass(wc.ConfigKeyError, wc.ConfigError)
    with pytest.raises(wc.ConfigError):
        wc.set_value("nonsense", "x")


def test_set_project_value_refuses_the_same_keys(roots):
    """A repo file is committed, so a dead key travels to everyone who clones."""
    _, project = roots
    with pytest.raises(wc.ConfigKeyError):
        wc.set_project_value("issue_tracker.provdier", "github", project)
    assert not (project / wc.PROJECT_CONFIG_NAME).exists()


def test_a_key_the_installed_workbench_does_not_read_is_refused(roots, stale_install):
    config_root, _ = roots
    with pytest.raises(wc.ConfigKeyError) as exc:
        wc.set_value(wc.ISSUE_PROVIDER_KEY, "github")
    assert str(stale_install) in str(exc.value)
    assert not (config_root / wc.CONFIG_NAME).exists()


def test_a_key_both_surfaces_read_is_written(roots, stale_install):
    """The installed surface refuses keys; it does not refuse writing."""
    wc.set_value("reuse.level", "ultra")
    assert wc.load_config().reuse.level is wc.ReuseLevel.ULTRA


def test_no_installed_workbench_leaves_the_local_surface(roots, monkeypatch):
    """CI and a fresh clone have no install, and still have to be able to write."""
    monkeypatch.setattr(wc, "installed_schema_path", lambda: None)
    wc.set_value(wc.ISSUE_PROVIDER_KEY, "github")
    assert wc.load_config().issue_tracker.provider is wc.IssueProvider.GITHUB


def test_an_unreadable_installed_schema_leaves_the_local_surface(roots, tmp_path, monkeypatch):
    """One broken file must not make the config unwritable machine-wide."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(wc, "installed_schema_path", lambda: broken)
    wc.set_value(wc.ISSUE_PROVIDER_KEY, "github")
    assert wc.load_config().issue_tracker.provider is wc.IssueProvider.GITHUB


def test_an_enum_keyed_section_is_writable_by_its_declared_keys(roots):
    """`review.phases.<phase>` is a dict, so the guard reads propertyNames."""
    wc.set_value(f"review.phases.{Phase.SCOUT}.model", "sonnet")
    assert wc.load_config().review.phases[Phase.SCOUT].model == "sonnet"
    with pytest.raises(wc.ConfigKeyError):
        wc.set_value("review.phases.nosuchphase.model", "sonnet")


def test_check_key_says_which_surface_refused(stale_install):
    assert wc.check_key("reuse.level").ok
    here = wc.check_key("reuse.levl")
    assert not here.ok
    assert here.verdict is wc.KeyVerdict.UNKNOWN_HERE
    assert "WorkbenchConfig defines" in here.reason
    installed = wc.check_key(wc.ISSUE_PROVIDER_KEY)
    assert not installed.ok
    assert installed.verdict is wc.KeyVerdict.UNKNOWN_INSTALLED
    assert "the two disagree about where the value lives" in installed.reason
    assert wc.check_key("reuse.level").reason == ""


def test_installed_schema_path_resolves_through_the_launcher(tmp_path, monkeypatch):
    """The PATH symlink is the whole mechanism — a worktree cannot fake it."""
    installed = tmp_path / "checkout"
    (installed / "bin").mkdir(parents=True)
    (installed / "bin" / wc.INSTALLED_LAUNCHER).write_text("#!/bin/sh\n")
    (installed / wc.SCHEMA_PATH).write_text("{}")
    monkeypatch.setattr(wc.shutil, "which",
                        lambda name: str(installed / "bin" / name))
    assert wc.installed_schema_path() == installed / wc.SCHEMA_PATH


def test_installed_schema_path_is_none_without_an_install(monkeypatch):
    monkeypatch.setattr(wc.shutil, "which", lambda name: None)
    assert wc.installed_schema_path() is None
