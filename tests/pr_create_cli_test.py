"""Tests for `cli.pr_create` — `pr create`'s parser, driven through `cli.pr.main`.

Driven with the argv a user types, with `pr.create.run_create` stubbed: what
is under test is that each flag reaches `CreateOptions` intact (D12), that a
flag value is never classified as a PR target, the `--body-file` contract
(D11), and that `pr create --help` / `--tool-schema` answer from create's own
parser. Ported from `tests/parse_pr_flags.bats`, since deleted with the bash
owner, and the create-forwarding tests that lived in `tests/pr_cli_test.py`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from conftest import git_in, init_repo, make_ctx, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import pr.context  # noqa: E402
import pr.create  # noqa: E402
from pr.create import CreateOptions  # noqa: E402

_TEST_PR = "3057"


def _main(*argv: str) -> int:
    """`cli.pr.main`, with SystemExit (argparse) folded into a code."""
    with patch("cli.pr.Trail.start", return_value=MagicMock()), \
         patch("git.topology.current_branch_quiet", return_value=None):
        try:
            return cli.pr.main(list(argv), bin_dir=BIN_DIR)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1


@pytest.fixture
def created(tmp_path):
    """Stub resolution and `run_create`; yields the recorded calls."""
    ctx = make_ctx(pr_number=None, branch="feat/x", worktree_root=tmp_path,
                   target_dir=tmp_path / "target")
    calls: list[CreateOptions] = []
    trails: list[object] = []

    def run_create(c, opts, *, trail=None):
        calls.append(opts)
        trails.append(trail)
        return 0

    with patch("pr.context.resolve", return_value=ctx) as resolve, \
         patch("pr.context.resolve_local", return_value=ctx), \
         patch.object(pr.create, "run_create", run_create):
        yield MagicMock(calls=calls, trails=trails, resolve=resolve)


@pytest.mark.parametrize("flag,field,value", [
    ("--title", "title", "fix(pr): trust gh's exit code"),
    ("--body", "body", "## What\n\nA body with a blank line."),
    ("--base", "base", "release/v2"),
    ("--issue", "issue", "ENG-42"),
])
def test_a_value_flag_reaches_create_options_intact(created, flag, field, value):
    assert _main("create", "--no-issue", "--draft", flag, value) == 0
    opts = created.calls[-1]
    assert getattr(opts, field) == value
    assert opts.draft is True


def test_the_dispatch_trail_reaches_run_create(created):
    trail = MagicMock(name="dispatch-trail")
    with patch("cli.pr.Trail.start", return_value=trail), \
         patch("git.topology.current_branch_quiet", return_value=None):
        assert cli.pr.main(["create"], bin_dir=BIN_DIR) == 0
    assert created.trails == [trail]


def test_closes_is_repeatable_and_keeps_order(created):
    assert _main("create", "--closes", "941", "--closes", "#942") == 0
    assert created.calls[-1].closes == ("941", "#942")


def test_the_valueless_flags_default_off_and_turn_on(created):
    assert _main("create") == 0
    assert created.calls[-1] == CreateOptions()
    assert _main("create", "--draft", "--no-verify", "--dry-run") == 0
    opts = created.calls[-1]
    assert (opts.draft, opts.no_verify, opts.dry_run) == (True, True, True)


def test_a_title_that_reads_like_a_pr_number_is_not_a_target(created):
    assert _main("create", "--title", _TEST_PR) == 0
    assert created.calls[-1].title == _TEST_PR
    assert created.resolve.call_args[1]["pr_ref"] is None
    assert created.resolve.call_args[1]["branch"] is None


def test_no_issue_is_accepted(created):
    assert _main("create", "--no-issue") == 0
    assert len(created.calls) == 1


def test_an_unknown_flag_is_a_usage_error(created):
    assert _main("create", "--bogus") == 2
    assert not created.calls


def test_a_value_flag_without_its_value_is_a_usage_error(created):
    assert _main("create", "--title") == 2
    assert not created.calls


def test_body_file_reads_the_body_from_the_file(created, tmp_path):
    body = tmp_path / "body.md"
    body.write_text("## What\n\nfrom a file\n")
    assert _main("create", "--title", "t", "--body-file", str(body)) == 0
    assert created.calls[-1].body == "## What\n\nfrom a file\n"
    assert created.calls[-1].title == "t"


def test_a_missing_body_file_exits_2_with_a_controlled_message(created, tmp_path, capsys):
    missing = tmp_path / "nope.md"
    assert _main("create", "--body-file", str(missing)) == 2
    err = capsys.readouterr().err
    assert f"✗ --body-file {missing}: No such file or directory" in err
    assert not created.calls


def test_body_and_body_file_together_is_a_usage_error(created, tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("x")
    assert _main("create", "--body", "b", "--body-file", str(body)) == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert not created.calls


@patch("pr.context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_help_prints_the_create_parsers_flags(_resolve, capsys):
    assert _main("create", "--help") == 0
    out = capsys.readouterr().out
    assert "usage: pr create" in out
    for flag in ("--dry-run", "--draft", "--body-file", "--closes", "--base"):
        assert flag in out
    # Accepted but undocumented.
    assert "--no-issue" not in out


@patch("cli.pr._reference_parser")
@patch("pr.context.resolve", side_effect=AssertionError("resolve must not be called"))
def test_a_command_with_no_parser_factory_keeps_its_own_help(_resolve, mock_help, capsys):
    """`status` has no factory: argparse's own subparser help answers, as before."""
    assert _main("status", "--help") == 0
    mock_help.assert_not_called()
    assert "usage: pr status" in capsys.readouterr().out


def test_tool_schema_for_create_names_its_flags(capsys):
    assert _main("--tool-schema", "create") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["name"] == "pr create"
    assert "dry_run" in doc["input_schema"]["properties"]
    assert "output_schema" not in doc


def _repo_ahead_of_origin(tmp_path: Path) -> Path:
    """A checkout on feat/x, one commit ahead of a bare origin's main.

    The origin is spelled as a github.com URL so the repo resolves from git
    alone, and rewritten with ``insteadOf`` to the local bare repo so the
    fetch reaches something real.
    """
    remote = tmp_path / "remote.git"
    run_checked(["git", "init", "-q", "--bare", "-b", "main", str(remote)])
    wt = init_repo(tmp_path / "wt")
    url = "https://github.com/otto-nation/scrub-fixture.git"
    git_in(wt, "config", f"url.{remote}.insteadOf", url)
    git_in(wt, "remote", "add", "origin", url)
    git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
    git_in(wt, "push", "-q", "origin", "main")
    git_in(wt, "remote", "set-head", "origin", "main")
    git_in(wt, "checkout", "-q", "-b", "feat/x")
    git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", "feat: work")
    return wt


def test_create_answers_for_repo_dir_not_an_inherited_git_dir(tmp_path, monkeypatch, capsys):
    wt = _repo_ahead_of_origin(tmp_path)
    other = init_repo(tmp_path / "other")
    git_in(other, "commit", "-q", "--allow-empty", "--no-verify", "-m", "unrelated")
    # monkeypatch, so the variable is restored whatever the scrub did to it.
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    with patch("cli.pr.Trail.start", return_value=MagicMock()), \
         patch("pr.push_intent.reconcile"), \
         patch("config.workbench_projects.register"), \
         patch("pr.context._pr_from_current", return_value=pr.context.BranchPR()):
        code = cli.pr.main(
            ["--repo-dir", str(wt), "create", "--dry-run", "--title", "t", "--body", "b"],
            bin_dir=BIN_DIR,
        )

    out, err = capsys.readouterr()
    assert code == 0, err
    assert "→ PR Title:" in out
    assert "   t" in out.splitlines()
    assert "feat/x" in err
    assert "GIT_DIR" not in os.environ
