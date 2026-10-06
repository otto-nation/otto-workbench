"""Tests for the ci-check flags `pr batch` relies on, and its stdout fix tally."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.ci_check  # noqa: E402
import core.publishing  # noqa: E402
import core.run_lock  # noqa: E402
import fix.engine  # noqa: E402
import fix.verify  # noqa: E402
import gh.run_reads  # noqa: E402
import pr.ci_failures  # noqa: E402
import pr.ci_report  # noqa: E402
import pr.ci_check  # noqa: E402
import pr.ci_runs  # noqa: E402
import pr.context  # noqa: E402
import rebase.ci_fix  # noqa: E402
import rebase.commands  # noqa: E402
import rebase.target  # noqa: E402
import rebase.types  # noqa: E402
from fix.suite import SuiteResult, SuiteStatus  # noqa: E402
from git.land import CommitStatus, LandResult  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402


def _group(kind, item_id):
    return pr.ci_failures.FailureGroup(
        job=item_id, kind=kind,
        items=(pr.ci_failures.FailureItem(
            id=item_id, annotation="x", file="src/a.go", line=1, diagnosis=None,
            fix_sha=None, outcome=None, headline="x"),),
    )


def _report(failures):
    return CIReport(repo="owner/repo", branch="feat/test", pr_number=42, run_id=1,
                    run_ids=[1], run_number=1, head_sha="abc123", conclusion="failure",
                    behind_main=3, failures=failures, progression={},
                    resolved_since_prior=[])


def _args(**kw):
    args = MagicMock()
    args.run, args.head_sha, args.wait_timeout, args.wait_interval = None, "", 1, 0
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def _tally(capsys) -> dict:
    chunks = [c.strip() for c in capsys.readouterr().out.split("---") if c.strip()]
    docs = [json.loads(c) for c in chunks]
    return next(d for d in reversed(docs) if d.get("type") == pr.ci_report.FIX_TALLY_TYPE)


def test_head_sha_finds_the_runs_of_the_remote_commit():
    with patch("gh.run_reads.fetch_latest_runs",
               return_value=gh.run_reads.RunDiscovery()) as runs, \
         patch("gh.run_reads.fetch_commit_checks",
               return_value=gh.run_reads.CommitChecks()), \
         pytest.raises(pr.ci_runs.RunUnavailable):
        pr.ci_check.run_ci(MagicMock(), _args(head_sha="remote1"), make_ctx(head_sha="local1"))
    assert runs.call_args[0][2] == "remote1"


def test_wait_polls_the_remote_commit():
    seen = {}

    def poll(*a, **k):
        seen["head"] = k["head_sha"]
        raise pr.ci_runs.RunUnavailable("none")

    with patch("pr.ci_wait.poll_until_complete", side_effect=poll), \
         pytest.raises(pr.ci_runs.RunUnavailable):
        pr.ci_check.run_ci_wait(MagicMock(), _args(head_sha="remote1"), make_ctx(head_sha="local1"))
    assert seen["head"] == "remote1"


def test_no_rebase_reaches_the_fix_phase():
    seen = {}
    with patch.object(pr.context, "resolve", return_value=make_ctx()), \
         patch.object(core.run_lock, "claim_for_process"), \
         patch.object(cli.ci_check.Trail, "start", return_value=MagicMock()), \
         patch.object(pr.ci_check, "run_ci", return_value=_report({})), \
         patch.object(rebase.ci_fix, "run_fix",
                      side_effect=lambda t, r, c, **k: seen.update(k) or 0):
        assert cli.ci_check.main(["--fix", "--no-rebase"]) == 0
    assert seen["rebase_first"] is False


def test_the_fix_phase_is_gated_unless_no_verify_is_given():
    seen = []
    with patch.object(pr.context, "resolve", return_value=make_ctx()), \
         patch.object(core.run_lock, "claim_for_process"), \
         patch.object(cli.ci_check.Trail, "start", return_value=MagicMock()), \
         patch.object(pr.ci_check, "run_ci", return_value=_report({})), \
         patch.object(rebase.ci_fix, "run_fix",
                      side_effect=lambda t, r, c, **k: seen.append(k) or 0):
        assert cli.ci_check.main(["--fix"]) == 0
        assert cli.ci_check.main(["--fix", "--no-verify"]) == 0
    assert [k["verify"] for k in seen] == [True, False]


def _engine_verify(tmp_path, **kw):
    """The `verify=` `run_fix` hands the engine for a run with one fixable failure."""
    report = _report({"b": _group(pr.ci_failures.FailureKind.BUILD, "b-1")})
    with patch.object(rebase.ci_fix, "rebase_if_behind", return_value=False), \
         patch.object(fix.engine, "run", return_value=fix.engine.FixRun()) as run:
        rebase.ci_fix.run_fix(MagicMock(), report,
                              make_ctx(worktree_root=tmp_path, target_dir=tmp_path), **kw)
    return run.call_args.kwargs.get("verify")


def test_a_claimed_ci_fix_goes_to_the_verify_gate_by_default(tmp_path):
    """The review and comments passes gate their claims; CI's are claims too."""
    assert _engine_verify(tmp_path) is fix.verify.run


def test_no_verify_hands_the_engine_no_gate(tmp_path):
    assert _engine_verify(tmp_path, verify=False) is None


def test_a_fix_without_rebase_never_rebases(tmp_path):
    report = _report({"b": _group(pr.ci_failures.FailureKind.BUILD, "b-1")})
    with patch.object(rebase.ci_fix, "rebase_if_behind") as rebase_first, \
         patch.object(fix.engine, "run", return_value=fix.engine.FixRun()):
        rebase.ci_fix.run_fix(MagicMock(), report,
                              make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
                              rebase_first=False)
    rebase_first.assert_not_called()


def test_an_all_skipped_run_reports_its_skips_on_stdout(tmp_path, capsys):
    report = _report({"i": _group(pr.ci_failures.FailureKind.INFRA, "i-1")})
    assert rebase.ci_fix.run_fix(MagicMock(), report,
                                 make_ctx(worktree_root=tmp_path, target_dir=tmp_path)) == 0
    tally = _tally(capsys)
    assert tally["skipped"] == [{"id": "i-1", "kind": "infra"}]
    assert tally["unfixed"] == [] and tally["fixed"] == []


def test_a_fix_pass_reports_what_it_fixed_and_the_suite(tmp_path, capsys):
    report = _report({"b": _group(pr.ci_failures.FailureKind.BUILD, "b-1"),
                      "c": _group(pr.ci_failures.FailureKind.BUILD, "c-1")})
    ran = fix.engine.FixRun(
        outcomes=[ItemOutcome(id="b-1", outcome=FixOutcome.FIXED),
                  ItemOutcome(id="c-1", outcome=FixOutcome.DECLINED)],
        landed=LandResult(CommitStatus.PUSH_HELD, sha="c0ffee"),
        suite=SuiteResult(status=SuiteStatus.RED))
    with patch.object(rebase.ci_fix, "rebase_if_behind", return_value=False), \
         patch.object(fix.engine, "run", return_value=ran):
        rebase.ci_fix.run_fix(MagicMock(), report,
                              make_ctx(worktree_root=tmp_path, target_dir=tmp_path))
    tally = _tally(capsys)
    assert tally["fixed"] == ["b-1"] and tally["unfixed"] == ["c-1"]
    assert tally["suite_status"] == "red" and tally["commit"] == "c0ffee"


def test_a_rebase_whose_push_was_held_is_not_reported_as_pushed():
    """`pr rebase` holds its push on a one-sided resolution, even with --post."""
    state = MagicMock()
    state.rebase.force_pushed = False
    ctx = MagicMock()
    ctx.require_worktree.return_value = Path("/tmp/wt")
    with patch.object(rebase.target, "resolve_target_ref", return_value="origin/main"), \
         patch.object(rebase.commands, "cmd_start", return_value=0), \
         patch.object(core.publishing, "enabled", return_value=True), \
         patch.object(rebase.types, "load_or_init", return_value=state):
        assert rebase.ci_fix.rebase_if_behind(MagicMock(), _report({}), ctx) is False
