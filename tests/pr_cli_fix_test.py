"""pr CLI: `pr fix` — which passes it runs, which cached verdicts it trusts, and describe."""

import sys
from pathlib import Path
from unittest.mock import patch

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_commands  # noqa: E402
import pr.domains  # noqa: E402
import pr.state  # noqa: E402

from pr_cli_support import _cmd_fix


# ── cmd_fix ─────────────────────────────────────────────────────────────────


@patch("pr.state.load_state")
def test_cmd_fix_no_state_returns_error(mock_load):
    mock_load.return_value = None
    ctx = make_ctx()
    rc = _cmd_fix([], ctx)
    assert rc == 1


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_dispatches_review_when_findings(mock_load, mock_call):
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={"M": 1}, verdict=pr.domains.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0
    ctx = make_ctx()
    rc = _cmd_fix([], ctx)
    assert rc == 0
    cmd = _first_call_containing(mock_call, "review")
    assert "--self" in cmd
    assert "--fix" in cmd


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_names_the_target_its_parent_locked(mock_load, mock_call):
    """The review pass is told which target to resolve, not left to guess.

    It ran with `--repo-dir` alone, so the child re-resolved from that
    checkout's current branch. When that is not the branch the parent locked —
    a PR run keys on the GitHub head ref while the worktree sits on something
    else — the two computed different lock keys and both ran against one
    checkout, which is the contention the run lock is supposed to prevent.
    """
    import pr.state
    state = pr.state.new_state(
        "repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt",
    )
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={"M": 1},
        verdict=pr.domains.ReviewVerdict.CHANGES_REQUESTED.value,
        updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0

    _cmd_fix([], make_ctx(pr_number=4242), original_pr=None,
                   original_branch=None)

    cmd = _first_call_containing(mock_call, "review")
    assert ["--pr", "4242"] == cmd[cmd.index("--pr"):cmd.index("--pr") + 2]
    # Exactly one target flag: pr.context.resolve() rejects both at once.
    assert "--branch" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_prefers_the_target_the_operator_named(mock_load, mock_call):
    """An explicit --pr outranks the resolved context, as every delegate does."""
    import pr.state
    state = pr.state.new_state(
        "repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt",
    )
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={"M": 1},
        verdict=pr.domains.ReviewVerdict.CHANGES_REQUESTED.value,
        updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0

    _cmd_fix([], make_ctx(pr_number=4242), original_pr="99",
                   original_branch=None)

    cmd = _first_call_containing(mock_call, "review")
    assert ["--pr", "99"] == cmd[cmd.index("--pr"):cmd.index("--pr") + 2]


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_skips_review_when_no_findings_on_this_commit(mock_load, mock_call):
    """A clean verdict suppresses the pass only when it was about this commit."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={}, verdict=pr.domains.ReviewVerdict.APPROVE.value,
        head_sha="abc123", updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0
    rc = _cmd_fix([], make_ctx(head_sha="abc123"))
    assert rc == 0
    assert not _calls_containing(mock_call, "review")


# ── A cached "nothing to do" is only about the commit it was measured on ─────
#
# `pr fix` decides whether to run its passes from the state file, and used to
# ask only "were there findings / failures?". A clean answer from a week ago
# suppressed the pass on a branch that had been pushed to since — silently, and
# with nothing downstream to re-check. Running when unsure costs a spawn that
# re-fetches and no-ops; skipping when unsure loses the work altogether.


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_reviews_again_when_the_clean_verdict_was_another_commit(
    mock_load, mock_call,
):
    """The false negative this gate exists to close."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={}, verdict=pr.domains.ReviewVerdict.APPROVE.value,
        head_sha="0ldc0mmit", updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha="abc123"))
    assert _calls_containing(mock_call, "review")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_reviews_again_when_the_verdict_names_no_commit(mock_load, mock_call):
    """A state file written before head_sha was recorded is unknown, not clean."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={}, verdict=pr.domains.ReviewVerdict.APPROVE.value,
        head_sha="", updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha="abc123"))
    assert _calls_containing(mock_call, "review")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_reviews_again_when_head_cannot_be_resolved(mock_load, mock_call):
    """Unknown on the caller's side is unknown too — it must not read as a match.

    Both sides empty is the case a bare equality check gets wrong: "" == ""
    would call a verdict that names no commit an answer about a HEAD nobody
    could resolve.
    """
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={}, verdict=pr.domains.ReviewVerdict.APPROVE.value,
        head_sha="", updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha=""))
    assert _calls_containing(mock_call, "review")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_checks_ci_again_when_the_green_run_was_another_commit(
    mock_load, mock_call,
):
    """The costly half: a stale-green CI cache used to skip the spawn entirely.

    The child re-fetches before it fixes anything, so spawning on a stale red
    is self-correcting. Skipping on a stale green is not — nothing downstream
    looks again, and `pr fix` reports success having checked nothing.
    """
    import pr.state
    from pr.ci_failures import RunState
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    ci = pr.domains.CIDomain(
        conclusion="success", failure_count=0, updated_at="t", latest_run_id=7,
    )
    ci.runs[7] = RunState(
        run_id=7, run_number=1, head_sha="0ldc0mmit", status="completed",
        conclusion="success", fetched_at="t", failures={},
    )
    pr.state.apply(state, ci)
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha="abc123"))
    assert _calls_containing(mock_call, "ci-check")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_trusts_a_green_ci_run_for_this_commit(mock_load, mock_call):
    """The gate must still save the spawn it is there to save."""
    import pr.state
    from pr.ci_failures import RunState
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    ci = pr.domains.CIDomain(
        conclusion="success", failure_count=0, updated_at="t", latest_run_id=7,
    )
    ci.runs[7] = RunState(
        run_id=7, run_number=1, head_sha="abc123", status="completed",
        conclusion="success", fetched_at="t", failures={},
    )
    pr.state.apply(state, ci)
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha="abc123"))
    assert not _calls_containing(mock_call, "ci-check")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_still_spawns_ci_on_a_stale_red_cache(mock_load, mock_call):
    """Cached work outranks the commit check: the child re-fetches either way."""
    import pr.state
    from pr.ci_failures import RunState
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    ci = pr.domains.CIDomain(
        conclusion="failure", failure_count=3, failure_kinds={"test": 3},
        updated_at="t", latest_run_id=7,
    )
    ci.runs[7] = RunState(
        run_id=7, run_number=1, head_sha="0ldc0mmit", status="completed",
        conclusion="failure", fetched_at="t", failures={},
    )
    pr.state.apply(state, ci)
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha="abc123"))
    assert _calls_containing(mock_call, "ci-check")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_does_not_check_ci_that_never_ran_against_a_matching_sha(
    mock_load, mock_call,
):
    """An unwritten domain is an absent verdict, so the pass runs."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], make_ctx(head_sha="abc123"))
    assert _calls_containing(mock_call, "ci-check")


_SCRIPT_HANDLERS = {
    "review": "cli.review_entry:main",
    "ci-check": "cli.ci_check:main",
    "pr-describe": "cli.pr_describe:main",
    "pr-rebase": "cli.pr_rebase:main",
    "review-threads": "cli.review_threads:main",
    "review-post": "cli.review_post:main",
    "review-rebuild": "cli.review_rebuild:main",
}


def _calls_containing(mock_call, script: str) -> list[list[str]]:
    """Argv lists for in-process calls of this former script.

    Matched on the handler string `publishing.call_entry_point` received, not a
    suffix scan across every argument: a --repo-dir whose path happened to end
    in a script name would otherwise pass for an invocation of that script.
    """
    handler = _SCRIPT_HANDLERS[script]
    return [
        call.args[1] for call in mock_call.call_args_list
        if call.args[0] == handler
    ]


def _first_call_containing(mock_call, script: str) -> list[str]:
    calls = _calls_containing(mock_call, script)
    assert calls, f"{script} was never invoked"
    return calls[0]


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_stops_when_the_review_refuses_the_branch(mock_load, mock_call):
    """A branch not worth reviewing is not worth running the CI fix pass on either."""
    import pr.supersession
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={"M": 1}, verdict=pr.domains.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    ))
    pr.state.apply(state, pr.domains.CIDomain(failure_count=3, updated_at="t"))
    mock_load.return_value = state
    mock_call.return_value = pr.supersession.EXIT_SUPERSEDED

    rc = _cmd_fix([], ctx=make_ctx())

    assert rc == pr.supersession.EXIT_SUPERSEDED
    # Not even pr-describe: nothing after the refusal gets to act on the branch.
    assert [call.args[0] for call in mock_call.call_args_list] == [
        "cli.review_entry:main",
    ]


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_describes_last(mock_load, mock_call):
    """The description must reflect the branch state after all fix passes complete."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={"M": 1}, verdict=pr.domains.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix([], ctx=make_ctx())
    scripts = [call.args[0] for call in mock_call.call_args_list]
    assert scripts[-1] == "cli.pr_describe:main"


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_does_not_forward_argv_to_describe(mock_load, mock_call):
    """--fix and friends mean nothing to pr-describe."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    mock_load.return_value = state
    mock_call.return_value = 0
    _cmd_fix(["--verbose"], ctx=make_ctx())
    cmd = _first_call_containing(mock_call, "pr-describe")
    assert "--verbose" not in cmd


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_reports_a_failing_describe(mock_load, mock_call):
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a", worktree_root="/wt")
    mock_load.return_value = state
    mock_call.return_value = 1
    assert _cmd_fix([], ctx=make_ctx()) == 1


# ── cmd_fix forwards the publishing gate to describe ────────────────────────


def _describe_cmd(argv, tmp_path):
    """Run cmd_fix over a state with nothing to fix, and return describe's argv.

    A real `worktree_root` on purpose: `cmd_fix` opens with
    `ctx.require_worktree()`, so a context without one exits before any
    delegate runs and every assertion below would hold vacuously.

    Empty state otherwise, because describe runs at the tail of every `pr fix`
    regardless — that isolates the forwarding from the fix passes.
    """
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="a",
                               worktree_root=str(tmp_path))
    with patch("pr.state.load_state", return_value=state), \
         patch("core.publishing.call_entry_point", return_value=0) as mock_call:
        _cmd_fix(argv, make_ctx(worktree_root=tmp_path))
        return _first_call_containing(mock_call, "pr-describe")


def test_cmd_fix_post_reaches_the_description(tmp_path):
    """Editing the body is gated, so a `pr fix --post` that dropped the flag
    would push its commits and post its replies, then draft the description
    alone — the one output of the run left unpublished, with nothing saying so.
    """
    assert "--post" in _describe_cmd(["--post"], tmp_path)


def test_cmd_fix_without_post_leaves_the_description_drafted(tmp_path):
    assert "--post" not in _describe_cmd([], tmp_path)


def test_cmd_fix_still_withholds_its_other_flags_from_describe(tmp_path):
    """`--post` is the exception to the no-argv rule, not the end of it."""
    cmd = _describe_cmd(["--post", "--fix", "--wait"], tmp_path)
    assert "--fix" not in cmd
    assert "--wait" not in cmd


# ── The review gate asks about the commit the review will read ──────────────
#
# `ctx.head_sha` is the PR's *remote* head under `--pr`, while `review
# --self` reads the worktree (`review.pipeline._with_local_diff`). Asking the
# review gate about the remote head skipped the review after a clean pass
# followed by unpushed commits — the local tree nobody had read was the one it
# declined to look at.


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_reviews_unpushed_local_commits(mock_load, mock_call):
    """A clean verdict for the remote head says nothing about local work."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="remote",
                               worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={}, verdict=pr.domains.ReviewVerdict.APPROVE.value,
        head_sha="remote", updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0

    # `--pr` resolves ctx.head_sha to the remote head; the checkout has moved on.
    _cmd_fix([], make_ctx(head_sha="remote"), worktree_head="local")

    assert _calls_containing(mock_call, "review")


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.state.load_state")
def test_cmd_fix_still_skips_when_the_checkout_is_on_the_reviewed_commit(
    mock_load, mock_call,
):
    """The saving survives: nothing to review when the worktree matches."""
    import pr.state
    state = pr.state.new_state("repo", "branch", pr_number=1, head_sha="remote",
                               worktree_root="/wt")
    pr.state.apply(state, pr.domains.ReviewSummary(
        finding_counts={}, verdict=pr.domains.ReviewVerdict.APPROVE.value,
        head_sha="local", updated_at="t",
    ))
    mock_load.return_value = state
    mock_call.return_value = 0

    _cmd_fix([], make_ctx(head_sha="remote"), worktree_head="local")

    assert not _calls_containing(mock_call, "review")


def test_the_review_subject_falls_back_when_the_checkout_is_gone():
    """A gate deciding what to run must not be what ends the run.

    `pr.context.head_sha` shells out with `cwd=` set, which raises rather than
    returning "" when the directory is not there.
    """
    from pathlib import Path as _Path
    assert cli.pr_commands._worktree_head(
        _Path("/definitely/not/here"), "ctxsha") == "ctxsha"
