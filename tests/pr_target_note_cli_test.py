"""`pr target note`: validate, append one decision, change nothing else.

Revert: in _cmd_note, emit {} and return 0 without calling batch.target_session.note.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.schema  # noqa: E402
import core.run_events  # noqa: E402
import pr.state  # noqa: E402
from pr_target_support import make_target  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _note(t, *extra, pr_ref="o/r#7", finding="ci_failing:ci", verdict="deferred",
          reason="flaky runner"):
    return _main(["--repo-dir", str(t.repo), "target", "note", pr_ref, "--finding", finding,
                  "--verdict", verdict, "--reason", reason, *extra])


def test_note_appends_a_decision_prints_it_and_leaves_state_alone(tmp_path, capsys):
    t = make_target(tmp_path, jobs=["lint"])
    state = t.target_dir / pr.state.STATE_FILE
    before = (state.read_bytes(), state.stat().st_mtime_ns)
    assert _note(t, "--command", "pr ci --fix") == 0
    printed = json.loads(capsys.readouterr().out)
    assert core.run_events.read(t.target_dir).events == [printed]
    assert {k: printed[k] for k in ("kind", "item", "finding", "verdict", "reason", "commands")} == {
        "kind": "decision", "item": "o/r#7", "finding": "ci_failing:ci", "verdict": "deferred",
        "reason": "flaky runner", "commands": ["pr ci --fix"]}
    assert isinstance(printed["run"], str) and printed["run"]
    assert (state.read_bytes(), state.stat().st_mtime_ns) == before


def test_a_bare_number_names_the_targets_pr(tmp_path, capsys):
    t = make_target(tmp_path)
    assert _note(t, pr_ref="7") == 0
    assert json.loads(capsys.readouterr().out)["item"] == "o/r#7"


@pytest.mark.parametrize("override, named", [
    ({"pr_ref": "o/r#9"}, "o/r#9"),
    ({"finding": "review_findings:review"}, "review_findings:review"),
    ({"reason": "   "}, "reason"),
])
def test_a_bad_value_is_exit_2_naming_it_and_appends_nothing(tmp_path, capsys, override, named):
    t = make_target(tmp_path)
    assert _note(t, **override) == 2
    assert named in capsys.readouterr().err
    assert core.run_events.read(t.target_dir).events == []


@pytest.mark.parametrize("override", [{"verdict": "resolved"}, {"finding": "ci_failing"}])
def test_argparse_refuses_a_bad_verdict_or_finding_shape(tmp_path, override):
    t = make_target(tmp_path)
    assert _note(t, **override) == 2
    assert core.run_events.read(t.target_dir).events == []


def test_note_serves_no_schema_version():
    assert cli.schema.served_schema_versions("target", ["note"]) == ()
