"""Which keys a config write accepts, and at which scope.

A write is judged against this checkout's key surface and the installed
workbench's, and a key that declares its scopes is refused anywhere else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_report
import config.workbench_config_write
from core.phases import Phase

from workbench_config_support import roots, _write


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
