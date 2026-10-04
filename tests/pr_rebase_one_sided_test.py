"""Tests for one-sided resolutions: accumulated, persisted, reported, holding the push."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import core.publishing  # noqa: E402
import core.report  # noqa: E402
import git.client  # noqa: E402
import git.land  # noqa: E402
import pr.domains  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.land  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.pr_snapshot  # noqa: E402
import rebase.replay_audit  # noqa: E402
import rebase.stash  # noqa: E402
import rebase.survival  # noqa: E402
import rebase.target  # noqa: E402
import rebase.types  # noqa: E402
from git.land import CommitStatus  # noqa: E402

from pr_rebase_support import _LANDED_SHA, _LEASE, _RESUME, _TARGET, _pushed


def _advisory(path, commit):
    loss = rebase.survival.Loss(kind=rebase.survival.LossKind.REGION_ONE_SIDED,
                                side=rebase.survival.Side.TARGET, line=3, blocking=False)
    return rebase.replay_audit.CommitAudit(
        commit=commit, subject="subj", files=(rebase.survival.FileAudit(path, (loss,)),))


def _held():
    return git.land.LandResult(CommitStatus.PUSH_HELD, sha=_LANDED_SHA, resume=_RESUME)


def test_one_sided_regions_accumulate_across_two_stopped_commits():
    tally = rebase.types.ResolutionTally()
    # The first commit is audited twice, as a refusal or a resume re-audits it.
    audits = (_advisory("a.py", "aaa1111"), _advisory("a.py", "aaa1111"),
              _advisory("b.py", "bbb2222"))
    for audit in audits:
        with mock.patch.object(rebase.lifecycle.replay_audit, "audit_replay", return_value=audit):
            assert rebase.lifecycle._halt_if_discarding(
                "/fake", mock.MagicMock(), tally, target_ref=_TARGET, restore=False) is None
    assert tally.one_sided == ["a.py", "b.py"]
    assert len(tally.one_sided_regions) == 2
    assert "aaa1111" in tally.one_sided_regions[0] and "a.py" in tally.one_sided_regions[0]


def test_a_one_sided_resolution_holds_the_push_even_when_posting(capsys):
    ctx = mock.MagicMock()
    ctx.branch = "isaac/feat/x"
    tally = rebase.types.ResolutionTally(files=["a.py"], commits=1, one_sided=["a.py"],
                                         one_sided_regions=["aaa a.py"], pre_rebase_head="p0")
    saved = []
    land = mock.Mock(side_effect=lambda *a, **k: _pushed() if core.publishing.enabled() else _held())
    with core.publishing.run(post=True), \
         mock.patch.object(git.client, "commits_ahead", return_value=1), \
         mock.patch.object(rebase.land, "land_rebased", land), \
         mock.patch.object(rebase.types.RebaseOutcome, "save", lambda self, c: saved.append(self)), \
         mock.patch.object(core.report, "emit_json") as emit:
        rc = rebase.lifecycle.rebase_success("/fake", ctx, rebase.types.RunMode.FIX, tally,
                                             target_ref=_TARGET, lease=_LEASE)
    assert rc == 0
    report = emit.call_args[0][0]
    assert "force_pushed" not in report
    assert report["files_one_sided"] == ["a.py"] and report["pre_rebase_head"] == "p0"
    assert saved[0].files_one_sided == ["a.py"]
    err = capsys.readouterr().err
    assert "pr rebase --push-only" in err
    assert "without --no-push" not in err


def test_a_one_sided_rebase_in_push_mode_skips_the_push():
    args = SimpleNamespace(push_only=False, abort=False, onto=None, fix=False, push=True,
                           force=False, no_verify=False, fork_point=None)

    def start(*a, **k):
        core.publishing.hold("x")
        return 0

    with mock.patch.object(rebase.pr_snapshot, "fetch", return_value=None), \
         mock.patch.object(rebase.target, "resolve_target_ref", return_value=_TARGET), \
         mock.patch.object(cli.pr_rebase, "cmd_start", side_effect=start), \
         mock.patch.object(cli.pr_rebase, "cmd_push", return_value=1) as push:
        rc = cli.pr_rebase._run(args, mock.MagicMock(), "/fake", mock.MagicMock())
    assert rc == 0
    push.assert_not_called()


def test_a_one_sided_rebase_holds_the_gate_cmd_push_would_open():
    tally = rebase.types.ResolutionTally(one_sided=["a.py"])
    with core.publishing.run(post=True), \
         mock.patch.object(git.client, "commits_ahead", return_value=1), \
         mock.patch.object(rebase.types.RebaseOutcome, "save"), \
         mock.patch.object(core.report, "emit_json"):
        rc = rebase.lifecycle.rebase_success("/fake", mock.MagicMock(), rebase.types.RunMode.PUSH,
                                             tally, target_ref=_TARGET)
        assert rc == 0 and core.publishing.held()


def test_fresh_records_the_tip_it_started_from():
    ctx = mock.MagicMock()
    ctx.branch = "feat/my-branch"
    ctx.current_branch = "feat/my-branch"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run), \
         mock.patch.object(git.client, "head_sha", return_value="pre1"), \
         mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=False), \
         mock.patch.object(rebase.lifecycle, "rebase_success", return_value=0) as success:
        rebase.lifecycle.fresh("/fake", ctx, rebase.types.RunMode.FIX_ONLY, force=True,
                               target_ref=_TARGET)
    assert success.call_args[0][3].pre_rebase_head == "pre1"


def test_a_resumed_rebase_continues_from_the_recorded_regions():
    prior = SimpleNamespace(rebase=SimpleNamespace(
        status="conflicts", files_one_sided=["a.py"], one_sided_regions=["r1"]))
    with mock.patch.object(rebase.types, "load_or_init", return_value=prior), \
         mock.patch.object(rebase.inspect, "rebase_orig_head", return_value="orig1"):
        tally = rebase.lifecycle.resumed_tally("/fake", mock.MagicMock())
    assert (tally.one_sided, tally.one_sided_regions, tally.pre_rebase_head) == (
        ["a.py"], ["r1"], "orig1")


def test_cmd_start_resumes_with_the_recorded_tally():
    seeded = rebase.types.ResolutionTally(one_sided=["a.py"])
    with mock.patch.object(rebase.inspect, "rebase_in_progress", return_value=True), \
         mock.patch.object(rebase.stash, "restore"), \
         mock.patch.object(rebase.target, "resume_target_ref", return_value=_TARGET), \
         mock.patch.object(rebase.lifecycle, "resumed_tally", return_value=seeded), \
         mock.patch.object(rebase.lifecycle, "drive_to_completion", return_value=0) as drive:
        cli.pr_rebase.cmd_start("/fake", mock.MagicMock(), rebase.types.RunMode.FIX,
                                target_ref=_TARGET)
    assert drive.call_args.kwargs["tally"] is seeded


def test_an_old_state_file_loads_without_the_new_fields():
    summary = pr.domains.RebaseSummary()
    assert summary.files_one_sided == [] and summary.pre_rebase_head == ""


def test_the_status_line_names_one_sided_files():
    lines = pr.domains.RebaseSummary(updated_at="t", status="completed", commits_replayed=1,
                                     files_one_sided=["a.py"]).render_status()
    assert any("one side" in line and "a.py" in line for line in lines)
