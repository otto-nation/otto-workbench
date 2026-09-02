"""Tests for the three renderings of the workbench config surface.

``workbench_config_report`` is the one module of the three that only reads: the
JSON Schema an editor validates against, the key table the docs print, and the
resolved status ``otto-workbench config status`` reports. Nothing here writes a
file or decides whether a key may be written, so unlike the config itself —
where nearly every assertion is "write it, then load it back" and the writes are
tested alongside it — these stand on their own.

What they are guarding is that all three renderings walk ``WorkbenchConfig``
rather than listing its keys a second time. A second listing is not wrong on the
day it is written; it is wrong on the day a field is renamed, in whichever of
the three nobody remembered.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import workbench_config as wc
import workbench_config_report as wcr


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


# ── Status ──────────────────────────────────────────────────────────────────


def test_scopes_are_reported_highest_precedence_first(roots):
    config_root, project = roots
    status = wcr.config_status(project)
    assert [s.name for s in status.scopes] == [wc.PROJECT_SCOPE, wc.GLOBAL_SCOPE]
    assert [s.path for s in status.scopes] == [
        project / wc.PROJECT_CONFIG_NAME, config_root / wc.CONFIG_NAME,
    ]


def test_the_merge_and_the_report_read_the_same_files(roots):
    """`config_scopes` is the one owner, so neither can gain a file alone."""
    _, project = roots
    reported = [s.path for s in wcr.config_status(project).scopes]
    assert sorted(reported) == sorted(s.path for s in wc.config_scopes(project))


def test_a_scope_with_no_file_is_reported_as_absent(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  level: ultra\n")
    status = wcr.config_status(project)
    by_name = {s.name: s for s in status.scopes}
    assert by_name[wc.GLOBAL_SCOPE].exists
    assert not by_name[wc.PROJECT_SCOPE].exists


def test_outside_a_repo_there_is_only_the_global_scope(roots):
    status = wcr.config_status()
    assert [s.name for s in status.scopes] == [wc.GLOBAL_SCOPE]


def test_a_value_names_the_file_that_supplied_it(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  level: ultra\n")
    _write(project / ".workbench.yml", "issue_tracker:\n  provider: github\n")
    status = wcr.config_status(project)
    assert _row(status, "reuse.level").scope.name == wc.GLOBAL_SCOPE
    assert _row(status, "issue_tracker.provider").scope.name == wc.PROJECT_SCOPE


def test_an_overridden_value_names_the_file_that_won(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  level: ultra\n")
    _write(project / ".workbench.yml", "reuse:\n  level: lite\n")
    row = _row(wcr.config_status(project), "reuse.level")
    assert row.value == "lite"
    assert row.scope.name == wc.PROJECT_SCOPE


def test_a_key_no_file_sets_is_reported_as_a_default(roots):
    _, project = roots
    row = _row(wcr.config_status(project), "reuse.default")
    assert row.value == "full"
    assert row.is_default


def test_a_phase_override_is_reported_under_its_own_key(roots):
    config_root, project = roots
    _write(config_root / "config.yml", """
agent:
  phases:
    scout:
      model: haiku
""")
    row = _row(wcr.config_status(project), "agent.phases.scout.model")
    assert row.value == "haiku"
    assert row.scope.name == wc.GLOBAL_SCOPE


def test_a_phase_nobody_overrode_is_not_reported(roots):
    """Every phase would bury the ones a file actually names."""
    _, project = roots
    keys = [row.key for row in wcr.config_status(project).keys]
    assert not [key for key in keys if key.startswith("agent.phases.")]


def test_the_reported_keys_are_the_documented_keys(roots):
    """One walk over `WorkbenchConfig`, not a second listing of its keys.

    The docs table and the report both derive from the dataclass, so a renamed
    field moves in both at once. The placeholder rows are dropped because the
    report expands those over the entries a file actually holds.
    """
    _, project = roots
    documented = [key for key, _, _ in wcr._reference_rows(wc.WorkbenchConfig)
                  if "<" not in key]
    assert [row.key for row in wcr.config_status(project).keys] == documented


def test_a_key_the_surface_does_not_have_is_reported_as_a_stray(roots):
    """The incident this command exists for: the right value, the wrong key."""
    config_root, project = roots
    _write(config_root / "config.yml", "review:\n  issue_tracker:\n    provider: github\n")
    status = wcr.config_status(project)
    assert [(s.key, s.scope.name) for s in status.strays] == [
        ("review.issue_tracker.provider", wc.GLOBAL_SCOPE),
    ]
    assert _row(status, "issue_tracker.provider").is_default


def test_a_stray_key_does_not_make_the_report_a_failure(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  levl: ultra\n")
    assert wcr.config_status(project).ok


def test_an_unreadable_scope_is_a_problem_and_the_rest_still_reports(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  level: ultra\n")
    _write(project / ".workbench.yml", "reuse: [unclosed\n")
    status = wcr.config_status(project)
    assert not status.ok
    assert str(project / ".workbench.yml") in status.problems[0]
    assert _row(status, "reuse.level").value == "ultra"


def test_a_rejected_value_names_the_one_file_holding_it(roots):
    """The merged failure names every file that exists; this names the culprit."""
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  level: ultra\n")
    _write(project / ".workbench.yml", "reuse:\n  level: sideways\n")
    status = wcr.config_status(project)
    assert not status.ok
    assert status.problems == [
        f"{project / '.workbench.yml'}: 'sideways' is not a valid ReuseLevel",
    ]
    assert status.keys == []


def test_a_rejected_value_still_reports_the_scopes_and_the_strays(roots):
    config_root, project = roots
    _write(config_root / "config.yml", "reuse:\n  levl: ultra\n  level: sideways\n")
    status = wcr.config_status(project)
    assert [s.name for s in status.scopes] == [wc.PROJECT_SCOPE, wc.GLOBAL_SCOPE]
    assert [s.key for s in status.strays] == ["reuse.levl"]


def test_render_value_writes_what_a_config_file_would_hold():
    assert wcr.render_value(True) == "true"
    assert wcr.render_value(wc.ReuseLevel.FULL) == "full"
    assert wcr.render_value(None) == "—"
    assert wcr.render_value("") == "—"


# ── Schema ──────────────────────────────────────────────────────────────────


def test_committed_schema_matches_the_generator():
    """The committed schema is generated, so drift is a test failure.

    CLAUDE.md requires a cross-validation test wherever the same defaults
    appear in two formats. This is that test: renaming a field or adding a
    Phase member fails here until `bin/local/generate-config-schema` is re-run.
    """
    committed = json.loads((REPO_ROOT / wc.SCHEMA_PATH).read_text())
    assert committed == json.loads(wcr.schema_json())


def test_composed_docs_carry_the_generated_reference():
    """The key table in the docs is generated from the same dataclass.

    `bin/local/validate-docs-composed` is what fails on a stale artifact; this
    fails on a directive that stopped asking for the block at all, which the
    freshness check cannot see — a doc with no directive is consistent with its
    source and simply has no key reference in it.
    """
    text = (REPO_ROOT / wcr.DOCS_PATH).read_text()
    assert wcr.docs_reference() in text


def test_the_module_header_asks_for_the_reference_block():
    """`lib/config.sh` is where the directive that pulls the block in lives.

    The block name is a string in two files — the directive and `BLOCKS` in the
    generator — so a rename that misses one leaves the composer failing on an
    unknown block. Naming it here means the pair is checked without composing.
    """
    header = (REPO_ROOT / "lib" / "config.sh").read_text()
    assert f"<!-- include: {wcr.GENERATOR_PATH} --emit config-reference -->" in header


def test_every_written_key_resolves_to_a_field():
    """A dotted key nothing answers to writes a field `serde` then drops.

    `set_value` does not check its argument, so the constants naming the keys
    other modules write are checked here instead — against the same walk the
    docs table is built from, so a renamed field fails rather than silently
    stranding the value it used to hold.
    """
    keys = {key for key, _, _ in wcr._reference_rows(wc.WorkbenchConfig)}
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
    generator = REPO_ROOT / wcr.GENERATOR_PATH
    assert generator.is_file() and os.access(generator, os.X_OK)
    assert wcr.GENERATOR_PATH in json.loads(wcr.schema_json())["description"]


def test_the_docs_link_to_the_schema_resolves_from_the_docs_directory():
    """The block links to a repo-root file from a doc that is not at the root.

    The `../` depth is derived from `DOCS_PATH`, so moving the doc keeps the
    link pointing at the schema instead of quietly pointing above the repo.
    """
    docs_dir = (REPO_ROOT / wcr.DOCS_PATH).parent
    link = f"({wcr._DOCS_TO_ROOT}{wc.SCHEMA_PATH})"
    assert link in wcr.docs_reference()
    assert (docs_dir / f"{wcr._DOCS_TO_ROOT}{wc.SCHEMA_PATH}").resolve().is_file()
