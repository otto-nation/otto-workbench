"""pr CLI dispatch: worktree guards, the run lock, the dispatch axes, push reconciliation
and `pr status`."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from conftest import assert_no_worktree_exit, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402

import cli.pr_commands  # noqa: E402
import cli.registry  # noqa: E402
import core.proc  # noqa: E402
import core.run_lock  # noqa: E402
import pr.domains  # noqa: E402
import pr.state  # noqa: E402
import review.gc  # noqa: E402

from pr_cli_support import _cmd_fix, _TEST_PR, _run_main, _lock_file, _dispatch_stage


# ── worktree_root guards ───────────────────────────────────────────────────


def test_cmd_status_without_a_worktree_exits_with_guidance(capsys):
    assert_no_worktree_exit(capsys, "feat/test", cli.pr.cmd_status,
                            [], make_ctx(worktree_root=None))


def test_status_header_names_the_repo_from_the_context_without_state(worktree, capsys):
    """The user-visible consequence of resolving status locally: with no
    state.json to read the identity from, the header shows the origin-derived
    label the local rung returns, where it used to show `gh repo view`'s name."""
    ctx = make_ctx(repo="acme/widget", branch="feat/x", worktree_root=worktree,
                   target_dir=worktree / "target")
    with patch("pr.state.load_state", return_value=None):
        assert cli.pr.cmd_status([], ctx) == 0
    assert "## PR Status — acme/widget (no PR) (feat/x)" in capsys.readouterr().err


def _status_state(target_dir):
    """A state with two domains written and the rest untouched."""
    state = pr.state.new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                               worktree_root="/wt")
    pr.state.apply(state, pr.domains.CIDomain(conclusion="success", updated_at="t"))
    pr.state.apply(state, pr.domains.CommentsSummary(total_threads=2, updated_at="t"))
    pr.state.save_state(target_dir, state)
    return state


def test_status_prints_only_the_domains_that_have_something_to_say(worktree, capsys):
    """A silent domain takes up no room — no heading, and no blank line either.

    The dashboard is a fold over the registry rather than a hand-kept list of
    renderers, so the way a domain stays off it is by rendering nothing.
    """
    target = worktree / "target"
    _status_state(target)
    ctx = make_ctx(repo="acme/widget", branch="feat/x", worktree_root=worktree,
                   target_dir=target)

    with patch("git.client.run", return_value=core.proc.CmdResult(0, "0\n")):
        assert cli.pr.cmd_status([], ctx) == 0

    err = capsys.readouterr().err
    assert "**CI**" in err
    assert "**Comments**" in err
    # DescribeSummary and SupersessionDomain render nothing at all.
    assert "**Describe**" not in err
    assert "\n\n\n" not in err


def test_status_refreshes_push_without_writing_it_to_state(worktree, capsys):
    """Push is a local git question, so the dashboard asks it now.

    Reading state must not date it: the refreshed answer is reported and
    discarded, leaving the file as whatever last wrote it left behind.
    """
    target = worktree / "target"
    _status_state(target)
    ctx = make_ctx(repo="acme/widget", branch="feat/x", worktree_root=worktree,
                   target_dir=target)

    with patch("git.client.run", return_value=core.proc.CmdResult(0, "2\n")):
        assert cli.pr.cmd_status([], ctx) == 0

    captured = capsys.readouterr()
    assert "**Push**: 2 commit(s) not pushed" in captured.err
    assert "2 unpushed commit(s)" in captured.err
    assert pr.state.load_state(target).push == pr.domains.PushDomain()


def test_cmd_fix_without_a_worktree_exits_with_guidance(capsys):
    assert_no_worktree_exit(capsys, "feat/test", _cmd_fix,
                            [], make_ctx(worktree_root=None))


# ── run lock wiring ─────────────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_locks_the_target_for_a_mutating_command(
        mock_resolve, mock_call, worktree):
    """worktree_root and target_dir are different directories here on purpose:
    a lock keyed on worktree_root (the old bug) would land in the worktree, not
    in the target."""
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    mock_call.return_value = 0
    seen = {}
    def _capture(*a, **k):
        seen["env"] = os.environ.get(core.run_lock.LOCK_ENV)
        return 0
    mock_call.side_effect = _capture
    _run_main("--repo-dir", str(worktree), "comments")
    assert _lock_file(target).is_file()
    assert not _lock_file(worktree).exists()
    # The delegate has to inherit the marker, or it would deadlock on us.
    assert seen["env"] == str(target)


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_main_locks_a_bare_repo_run(mock_resolve, mock_call, tmp_path):
    """Regression: a bare repo (no worktree_root) used to skip the lock
    entirely via the old `if ctx.worktree_root:` guard. target_dir is never
    None, so a bare-repo run now takes a real lock like any other."""
    target = tmp_path / "target"
    mock_resolve.return_value = make_ctx(worktree_root=None, target_dir=target)
    mock_call.return_value = 0
    _run_main("--repo-dir", "/nonexistent", "comments")
    assert _lock_file(target).is_file()


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve_local")
def test_main_does_not_lock_for_status(mock_resolve, mock_call, worktree):
    """status is read-only, so it must never block on a run in flight."""
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    mock_call.return_value = 0
    _run_main("--repo-dir", str(worktree), "status")
    assert not _lock_file(target).exists()


@patch("review.gc.prune_merged_targets", return_value=review.gc.PruneOutcome())
@patch("review.gc.prune_merged_reviews", return_value=review.gc.PruneOutcome())
@patch("review.gc.gc_reviews", return_value=0)
@patch("pr.context.resolve")
def test_main_locks_for_gc(mock_resolve, _gc, _prune, _prune_targets, worktree):
    """gc deletes the state directory, so it is not safe to run unlocked.

    Unlike its neighbours, nothing here mocks `pr_cli.subprocess.run`, so the
    real `git rev-parse` answers for the worktree and the state dir resolves
    on its own.
    """
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    _run_main("--repo-dir", str(worktree), "gc")
    assert _lock_file(target).is_file()
    assert not _lock_file(worktree).exists()


@patch("review.gc.prune_merged_targets", return_value=review.gc.PruneOutcome())
@patch("review.gc.prune_merged_reviews", return_value=review.gc.PruneOutcome())
@patch("review.gc.gc_reviews", return_value=0)
@patch("pr.context.resolve")
def test_gc_skips_own_target_when_pruning(
        mock_resolve, _gc, _prune, mock_prune_targets, worktree):
    """cmd_gc must pass its own target as `skip` — gc holds that lock, so a
    prune that tried it would either deadlock or delete live state."""
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    _run_main("--repo-dir", str(worktree), "gc")
    assert mock_prune_targets.call_args.kwargs["skip"] == target


def test_the_maintenance_script_branches_on_the_exit_code_pr_actually_uses():
    """`pr` and the maintenance script spell EX_TEMPFAIL in different languages.

    The script cannot import the constant, so the number is written twice: once
    as `EXIT_BUDGET_EXHAUSTED` here and once as a bash comparison there. Nothing
    else ties them together, and drift is silent — the script would log a spent
    budget as a failure again, which is the bug this exit code exists to fix.
    Cheaper to bind them with an assertion than to leave the duplication unheld.
    """
    script = (REPO_ROOT / "maintenance" / "bin" / "otto-workbench-maintenance").read_text()
    assert f"-eq {cli.pr.EXIT_BUDGET_EXHAUSTED} ]]" in script


@patch("review.gc.prune_merged_targets",
       return_value=review.gc.PruneOutcome(0, cut_short=True))
@patch("review.gc.prune_merged_reviews", return_value=review.gc.PruneOutcome())
@patch("review.gc.gc_reviews", return_value=0)
@patch("pr.context.resolve")
def test_gc_cut_short_by_the_budget_is_not_reported_as_nothing_to_clean(
        mock_resolve, _gc, _prune, _prune_targets, worktree, capsys):
    """The headline symptom: a sweep that could not ask reported success.

    "nothing to clean" is a claim about the artifacts; the truth was that the
    PRs behind them were never asked about. Asserting the exit code alongside
    the wording on purpose — a test matching only stderr passes for a command
    that printed the right line and then exited 0, which is the bug.
    """
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)

    code = _run_main("--repo-dir", str(worktree), "gc")

    err = capsys.readouterr().err
    assert code == cli.pr.EXIT_BUDGET_EXHAUSTED
    assert "nothing to clean" not in err
    assert "never asked about" in err


@patch("review.gc.prune_merged_targets",
       return_value=review.gc.PruneOutcome(0, cut_short=True))
@patch("review.gc.prune_merged_reviews", return_value=review.gc.PruneOutcome())
@patch("review.gc.gc_reviews", return_value=0)
@patch("pr.context.resolve")
def test_gc_cut_short_records_a_trail_event(
        mock_resolve, _gc, _prune, _prune_targets, worktree):
    """The sweep is unattended, so the console line has no reader.

    Without a trail record there is nothing to measure the spender-side fix
    against later — an hour of refused sweeps would leave no trace at all.
    """
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    mock_trail = MagicMock()

    with patch("sys.argv", ["pr", "--repo-dir", str(worktree), "gc"]), \
         patch("cli.pr.Trail.start", return_value=mock_trail):
        try:
            cli.pr.main(bin_dir=BIN_DIR)
        except SystemExit:
            pass

    assert mock_trail.summary.call_args.args[0] == "gc_cut_short"


@patch("pr.context.resolve")
def test_main_reports_contention_and_exits_1(mock_resolve, worktree, capsys):
    target = worktree / "target"
    mock_resolve.return_value = make_ctx(worktree_root=worktree, target_dir=target)
    busy = core.run_lock.LockBusy(
        {"pid": 15461, "command": "pr review --self --fix", "started": "t"}, target)

    with patch("core.run_lock.acquire", side_effect=busy):
        code = _run_main("--repo-dir", str(worktree), "comments")
    assert code == 1
    err = capsys.readouterr().err
    assert "pr review --self --fix" in err
    assert "15461" in err


# ── dispatch axes ───────────────────────────────────────────────────────────
#
# One table per axis, because the axes are independent and a command routinely
# wants one without the others. Each is the behaviour that shipped before the
# needs were declared — spelled out rather than read off _COMMANDS, since a
# table derived from the declaration it checks would pass whatever it said.

# Which commands resolve with git alone, invoked bare. Only status: the rest
# need `gh` to name the PR.
#
# `review` is False here because these tables are keyed on the command alone,
# and a bare `pr review` does resolve remotely. `pr review --self` resolves
# locally — that is an argv-level decision, covered by
# test_review_declares_a_need_per_invocation above.
_RESOLVES_LOCALLY = {
    "create": False, "status": True, "ci": False, "review": False,
    "comments": False, "fix": False, "rebase": False, "describe": False,
    "batch": False, "gc": False,
}

# Which commands fetch and fast-forward the worktree first. The old
# _NO_UPDATE_COMMANDS, inverted: rebase does its own fetch, and the other three
# touch no remote state.
_FETCHES = {
    "create": False, "status": False, "ci": True, "review": True,
    "comments": True, "fix": True, "rebase": False, "describe": True,
    "batch": False, "gc": False,
}

# Which commands hold the run lock. The old _NO_LOCK_COMMANDS, inverted: gc is
# in here because deleting the state directory is the opposite of read-only.
_LOCKS = {
    "create": True, "status": False, "ci": True, "review": True,
    "comments": True, "fix": True, "rebase": True, "describe": True,
    "batch": False, "gc": True,
}


def test_axis_tables_cover_every_command():
    """A new command has to answer all three axes, here as well as in the
    registry — otherwise it ships untested on the axis nobody thought about."""
    for table in (_RESOLVES_LOCALLY, _FETCHES, _LOCKS):
        assert set(table) == set(cli.registry.COMMANDS)


@pytest.mark.parametrize("command", sorted(c for c in _RESOLVES_LOCALLY if c != "batch"))
def test_command_resolves_at_its_declared_depth(command, tmp_path):
    stage = _dispatch_stage(command, ctx=make_ctx(target_dir=tmp_path / "target"))
    local = _RESOLVES_LOCALLY[command]
    assert stage.local.called is local
    assert stage.remote.called is not local


def test_batch_resolves_nothing_at_all(tmp_path):
    """`pr batch` answers from the batch state root, so it consults neither git nor gh."""
    stage = _dispatch_stage("batch", ctx=make_ctx(target_dir=tmp_path / "target"))
    assert not stage.local.called
    assert not stage.remote.called


@pytest.mark.parametrize("command", sorted(_FETCHES))
def test_command_fetches_only_if_it_always_did(command, tmp_path):
    stage = _dispatch_stage(command, ctx=make_ctx(target_dir=tmp_path / "target"))
    assert stage.update.called is _FETCHES[command]


@pytest.mark.parametrize("command", sorted(_LOCKS))
def test_command_locks_only_if_it_always_did(command, tmp_path):
    target = tmp_path / "target"
    _dispatch_stage(command, ctx=make_ctx(target_dir=target))
    assert _lock_file(target).is_file() is _LOCKS[command]


def test_an_explicit_pr_escalates_the_depth_and_nothing_else(tmp_path):
    """A PR number names a branch only `gh` can report, and the branch is half
    the run's target key — resolving status locally anyway would read the
    directory of whatever branch happened to be checked out. The other two axes
    are independent, so escalating must not drag them along."""
    target = tmp_path / "target"
    stage = _dispatch_stage("--pr", _TEST_PR, "status", ctx=make_ctx(target_dir=target))
    assert stage.remote.called
    assert not stage.local.called
    assert not stage.update.called
    assert not _lock_file(target).exists()


# ── push reconciliation ─────────────────────────────────────────────────────


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_every_command_reconciles_recorded_pushes_first(mock_resolve, mock_call):
    """The single entry point for the record the global pre-push hook leaves.

    Before the context is resolved, so a report about a push made hours ago in
    another repository is not withheld by a command that goes on to fail for
    reasons of its own.
    """
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    with patch("pr.push_intent.reconcile") as reconcile:
        reconcile.side_effect = lambda: mock_resolve.assert_not_called()
        _run_main("rebase")
    reconcile.assert_called_once_with()
    mock_resolve.assert_called_once()


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_a_reconciliation_that_raises_leaves_the_command_running(
        mock_resolve, mock_call, capsys):
    """Every subcommand passes through reconciliation on its way to work that
    has nothing to do with pushing, so a bug in it is a warning, not an outage —
    and it is a warning rather than silence so the bug is still findable."""
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    with patch("pr.push_intent.reconcile", side_effect=RuntimeError("the record broke")):
        _run_main("rebase")
    mock_resolve.assert_called_once()
    assert "the record broke" in capsys.readouterr().err


def test_reconciliation_is_hooked_in_exactly_one_place():
    """One owner, so a new subcommand inherits it rather than declaring it.

    Counted off the parse tree rather than the source text: a docstring or a
    comment naming the call — this file's own prose about it, one day — would
    read as a second owner and fail a test about the code.
    """
    tree = ast.parse(Path(cli.pr.__file__).read_text())
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "reconcile"
             and ast.unparse(node.func.value) == "pr.push_intent"]
    assert len(calls) == 1


# ── `pr status` compares against the checkout, not the last write ───────────
#
# `identity.head_sha` is from whenever state was last written. A commit made
# since leaves it naming the very SHA the domains were measured at, so the
# supersession check would be asking a stale value about itself and a verdict
# about the previous commit would render as current.


@patch("pr.state.load_state")
@patch("pr.domains.PushDomain.observed")
def test_cmd_status_marks_a_verdict_left_behind_by_a_local_commit(
    observed, mock_load, capsys,
):
    import pr.state
    from pr.ci_failures import RunState
    state = pr.state.new_state("acme/w", "feat/x", pr_number=1, head_sha="A",
                               worktree_root="/wt")
    ci = pr.domains.CIDomain(conclusion="success", failure_count=0,
                             updated_at=pr.state.now_iso(), latest_run_id=1)
    ci.runs[1] = RunState(run_id=1, run_number=1, head_sha="A",
                          status="completed", conclusion="success",
                          fetched_at="t", failures={})
    pr.state.apply(state, ci)
    mock_load.return_value = state
    observed.return_value = pr.domains.PushDomain(ahead=1,
                                                  updated_at=pr.state.now_iso())

    # The checkout has moved on since `pr ci` wrote the state.
    with patch("cli.pr_commands._worktree_head", return_value="B"):
        cli.pr.cmd_status([], make_ctx(worktree_root=Path("/wt")))

    err = capsys.readouterr().err
    assert "[STALE — checked another commit]" in err


@patch("pr.state.load_state")
@patch("pr.domains.PushDomain.observed")
def test_cmd_status_leaves_a_current_verdict_unmarked(observed, mock_load, capsys):
    """The checkout is on the commit the run was about, so nothing is stale."""
    import pr.state
    from pr.ci_failures import RunState
    state = pr.state.new_state("acme/w", "feat/x", pr_number=1, head_sha="A",
                               worktree_root="/wt")
    ci = pr.domains.CIDomain(conclusion="success", failure_count=0,
                             updated_at=pr.state.now_iso(), latest_run_id=1)
    ci.runs[1] = RunState(run_id=1, run_number=1, head_sha="A",
                          status="completed", conclusion="success",
                          fetched_at="t", failures={})
    pr.state.apply(state, ci)
    mock_load.return_value = state
    observed.return_value = pr.domains.PushDomain(ahead=0,
                                                  updated_at=pr.state.now_iso())

    with patch("cli.pr_commands._worktree_head", return_value="A"):
        cli.pr.cmd_status([], make_ctx(worktree_root=Path("/wt")))

    assert "STALE" not in capsys.readouterr().err


@patch("pr.state.load_state")
@patch("pr.domains.PushDomain.observed")
def test_cmd_status_dumps_the_head_it_compared_against(observed, mock_load, capsys):
    """The JSON must agree with the dashboard rendered from the same object."""
    import pr.state
    state = pr.state.new_state("acme/w", "feat/x", pr_number=1, head_sha="A",
                               worktree_root="/wt")
    pr.state.apply(state, pr.domains.CIDomain(conclusion="success",
                                              updated_at=pr.state.now_iso()))
    mock_load.return_value = state
    observed.return_value = pr.domains.PushDomain(ahead=0,
                                                  updated_at=pr.state.now_iso())

    with patch("cli.pr_commands._worktree_head", return_value="B"):
        cli.pr.cmd_status([], make_ctx(worktree_root=Path("/wt")))

    assert json.loads(capsys.readouterr().out)["identity"]["head_sha"] == "B"


@pytest.mark.parametrize("boom", [
    OSError("no such directory"),
    subprocess.TimeoutExpired(cmd="git rev-parse HEAD", timeout=10.0),
])
def test_the_worktree_head_read_never_ends_the_command(boom):
    """Both ways the shell-out can fail, since they are not one exception.

    `pr.context.head_sha` runs git with a cwd and a timeout. A removed
    worktree or a missing git binary raises OSError; a hung rev-parse raises
    TimeoutExpired, which is a SubprocessError and NOT an OSError. Catching
    only the first would let a hung git take down a read-only `pr status`.
    """
    from pathlib import Path as _Path
    with patch("pr.context.head_sha", side_effect=boom):
        assert cli.pr_commands._worktree_head(_Path("/wt"), "ctxsha") == "ctxsha"
