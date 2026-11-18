"""Resume rebuilds generated files deferred while a hand-written conflict was open."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from conftest import commit_all, git_in, git_out, init_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.publishing  # noqa: E402
import core.report  # noqa: E402
import git.regenerate  # noqa: E402
import rebase.conflicts  # noqa: E402
import rebase.inspect  # noqa: E402
import rebase.land  # noqa: E402
import rebase.lifecycle  # noqa: E402
import rebase.regen_dispatch  # noqa: E402
import rebase.resolve_ai  # noqa: E402
import rebase.types  # noqa: E402
import agent.backend  # noqa: E402

from pr_rebase_support import _LEASE, _TARGET, _pushed


def test_resumed_tally_restores_files_stale_from_the_stop():
    """The stop already writes files_stale; resume must not drop the queue."""
    prior = SimpleNamespace(rebase=SimpleNamespace(
        status="conflicts", files_one_sided=[], one_sided_regions=[],
        fork_point="", squashed_commits=0, files_stale=["go.sum"],
    ))
    with mock.patch.object(rebase.types, "load_or_init", return_value=prior), \
         mock.patch.object(rebase.inspect, "rebase_orig_head", return_value="orig1"):
        tally = rebase.lifecycle.resumed_tally("/fake", mock.MagicMock())
    assert tally.stale == ["go.sum"]


def test_resume_rebuilds_stale_generated_after_last_conflict_resolves():
    """A later --fix rebuilds queued lockfiles once the last file resolves."""
    order = []
    tally = rebase.types.ResolutionTally(stale=["go.sum"])

    def fake_resolve(*_args, **_kwargs):
        order.append("resolve")
        return rebase.types.Resolution(files=["bad.go"])

    def fake_regen(job, **_kwargs):
        order.append("regen")
        assert job.files == ["go.sum"]
        return True

    with mock.patch.object(rebase.inspect, "rebase_head_info",
                           return_value=("abc123", "feat: deps")), \
         mock.patch.object(rebase.inspect, "remaining_rebase_commits",
                           return_value=1), \
         mock.patch.object(agent, "backend") as mock_ai, \
         mock.patch.object(
             rebase.resolve_ai, "resolve_file_conflicts", side_effect=fake_resolve,
         ), \
         mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(git.regenerate, "run_regeneration", side_effect=fake_regen), \
         mock.patch("subprocess.run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="", stderr="")):
        mock_ai.is_available.return_value = True
        rc = rebase.lifecycle.step_conflicts(
            "/fake", mock.MagicMock(), rebase.types.RunMode.FIX, ["bad.go"],
            tally, target_ref=_TARGET,
        )

    assert rc is None
    assert order == ["resolve", "regen"]
    assert "go.sum" not in tally.stale


def test_failed_rebuild_on_resume_stays_stale_with_error():
    """A regenerator that fails keeps the path stale and logs the real error."""
    tally = rebase.types.ResolutionTally(stale=["go.sum"])
    trail = mock.MagicMock()
    with mock.patch.object(rebase.inspect, "detect_conflicts", return_value=[]), \
         mock.patch.object(git.regenerate, "run_regeneration", return_value=False):
        rebase.regen_dispatch.rebuild_stale("/fake", tally, trail=trail)

    assert tally.stale == ["go.sum"]
    trail.error.assert_called_once()
    assert trail.error.call_args[0][0] == "regenerate"
    assert "regeneration failed" in trail.error.call_args[0][1]


def test_rebuild_does_not_run_while_a_hand_written_file_is_unmerged():
    """Same skip as the first pass: generators import unresolved sources."""
    tally = rebase.types.ResolutionTally(stale=["go.sum"])
    plan = rebase.types.ConflictPlan(rebase.types.ConflictStrategy.AI_MERGE)
    with mock.patch.object(
             rebase.inspect, "detect_conflicts", return_value=["bad.go"],
         ), \
         mock.patch.object(rebase.conflicts, "classify_conflict", return_value=plan), \
         mock.patch.object(git.regenerate, "run_regeneration") as mock_regen:
        rebase.regen_dispatch.rebuild_stale("/fake", tally, trail=None)

    mock_regen.assert_not_called()
    assert tally.stale == ["go.sum"]


def test_step_advance_retries_deferred_stale_once_not_every_step():
    tally = rebase.types.ResolutionTally(stale=["go.sum"], deferred=["go.sum"])
    calls = []
    with mock.patch.object(rebase.regen_dispatch, "rebuild_stale",
                           side_effect=lambda *a, **k: calls.append(k["paths"])), \
         mock.patch.object(rebase.regen_dispatch, "_hand_written_unmerged",
                           return_value=False), \
         mock.patch.object(rebase.lifecycle, "_halt_if_discarding", return_value=7):
        for _ in range(2):
            rebase.lifecycle.step_advance(
                "/fake", mock.MagicMock(), tally, target_ref=_TARGET,
            )
    assert calls == [["go.sum"]]


def _tip_regen_repo(tmp_path, *, hash_on_feat: str) -> Path:
    repo = init_repo(tmp_path / "repo")
    (repo / ".gitattributes").write_text(
        "schema.hash linguist-generated=true regenerate=./regen.sh\n",
    )
    script = repo / "regen.sh"
    script.write_text("#!/bin/sh\nprintf 'fresh-hash\n' > schema.hash\n")
    script.chmod(0o755)
    (repo / "schema.hash").write_text("fresh-hash\n")
    commit_all(repo, "init generated")
    git_in(repo, "switch", "-c", "feat")
    if hash_on_feat != "fresh-hash\n":
        (repo / "schema.hash").write_text(hash_on_feat)
        commit_all(repo, "stale hash")
    return repo


def _tip_regen_success(repo, tally):
    ctx = mock.MagicMock()
    with core.publishing.run(post=True), \
         mock.patch.object(rebase.land, "land_rebased", return_value=_pushed()), \
         mock.patch.object(rebase.types.RebaseOutcome, "save"), \
         mock.patch.object(core.report, "emit_json") as emit:
        rc = rebase.lifecycle.rebase_success(
            str(repo), ctx, rebase.types.RunMode.FIX, tally,
            target_ref="main", lease=_LEASE,
        )
        held = core.publishing.held()
    return rc, emit, held


def test_tip_regeneration_commits_rebuilt_stale_file_and_clears_tally(tmp_path):
    repo = _tip_regen_repo(tmp_path, hash_on_feat="stale-hash\n")
    tally = rebase.types.ResolutionTally(
        files=["schema.hash"], stale=["schema.hash"], commits=1,
    )
    rc, emit, held = _tip_regen_success(repo, tally)
    assert rc == 0
    assert tally.stale == []
    assert git_out(repo, "log", "-1", "--format=%s").strip() == "chore: regenerate after rebase"
    assert (repo / "schema.hash").read_text() == "fresh-hash\n"
    assert emit.call_args[0][0]["commits_replayed"] == 1
    assert not held


def test_tip_regeneration_with_zero_diff_clears_stale_without_extra_commit(tmp_path):
    repo = _tip_regen_repo(tmp_path, hash_on_feat="fresh-hash\n")
    head_before = git_out(repo, "rev-parse", "HEAD").strip()
    tally = rebase.types.ResolutionTally(
        files=["schema.hash"], stale=["schema.hash"], commits=1,
    )
    rc, _, held = _tip_regen_success(repo, tally)
    assert rc == 0
    assert git_out(repo, "rev-parse", "HEAD").strip() == head_before
    assert tally.stale == []
    assert not held
