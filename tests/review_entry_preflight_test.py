"""Tests for the review entry point's preflight — the run lock, the supersession
refusal, and base resolution on the --self and PR paths."""

import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import pr.context
import review.invoke
import review.issue
import review.preflight
import review.publish
import review.recover
import review.run
import review.worktree

from conftest import (
    commit_all,
    git_in,
    init_repo,
    make_ctx,
    supersession_evidence,
    supersession_verdict,
)

import core.prompt
import core.run_lock
import pr.supersession

# cr and reviews_dir are pytest fixtures: imported by name so tests here can request them.
from review_entry_support import cr, reviews_dir, _self_flags, _stub_pr_flow


# ── the run lock on the --self path ──────────────────────────────────────────


def _self_review_args(**overrides):
    base = dict(
        positional=[], issue=None, max_parallel=1, skip_user_verification=True,
        force=False, no_holistic=False, no_scout=False, disprove=None, max_cost=None,
        model=None, repo_dir="", fix=True, effort="medium", max_groups=None,
        generated=False, recover=False, debug=False, base="",
        post=False, push=False, no_post=False, submit=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _stub_self_review(cr, monkeypatch, target, reviews_dir):
    """Drive _run_self_review far enough to reach the lock, and no further.

    `worktree_root` is the tree a plain `--self` reviews, and the lock now reads
    it. A sibling of the target rather than the target itself: the two are
    different directories in a real run, and sharing one here would hide a
    confusion between them.
    """
    ctx = make_ctx(
        repo="acme/widget", pr_number=None, branch="feat/x", head_sha="abc1234",
        worktree_root=target.parent / "wt", target_dir=target,
    )
    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/wt")
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR())
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(review.run, "run_self_review", MagicMock())
    return ctx


def test_self_review_takes_the_run_lock(cr, tmp_path, reviews_dir, monkeypatch):
    """`review --self --fix` twice must not give two committers on a branch.

    The lock is what stops that, and this branch's docs claim it covers a direct
    `--self` invocation — so a second, unrelated run has to be refused.
    """
    import core.run_lock

    target = tmp_path / "pr" / "target"
    _stub_self_review(cr, monkeypatch, target, reviews_dir)

    cr._run_self_review(_self_review_args(), ["--self", "--fix"], "test 1.0")

    record = json.loads((target / core.run_lock.LOCK_FILE).read_text())
    assert record["command"].startswith("review")
    assert record["started"]
    # The argv the flow was handed, not `sys.argv`. Called in-process from
    # `pr fix`, `sys.argv` is the *parent's*, so a lock that read it would
    # tell whoever it turns away that `pr fix` holds the lock — naming a
    # command the operator can neither find nor wait on.
    assert record["command"] == "review --self --fix"
    assert "pytest" not in record["command"]

    # A fresh run, with none of our bookkeeping inherited, must be turned away.
    # Both the marker and the registry entry go: either alone would pass it
    # through as the holder rather than letting the flock decide. The popped
    # entry stays bound — it is the only reference to the open file, and a
    # collected handle would close the descriptor and free the lock.
    monkeypatch.delenv(core.run_lock.LOCK_ENV, raising=False)
    disowned = core.run_lock._HELD.pop(str(target / core.run_lock.LOCK_FILE), None)
    assert disowned is not None
    with pytest.raises(SystemExit) as exc:
        core.run_lock.claim_for_process(target, command="review --self", started="t")
    assert exc.value.code == 1


def test_self_review_passes_through_the_lock_pr_already_holds(
    cr, tmp_path, reviews_dir, monkeypatch,
):
    """Launched by `pr`, a --self run inherits WORKBENCH_RUN_LOCK, not a deadlock."""
    import core.run_lock

    target = tmp_path / "pr" / "target"
    _stub_self_review(cr, monkeypatch, target, reviews_dir)

    with core.run_lock.acquire(target, command="pr review --self --fix", started="t"):
        cr._run_self_review(_self_review_args(), [], "test 1.0")
        # Passed through: the parent's ownership record is untouched.
        record = json.loads((target / core.run_lock.LOCK_FILE).read_text())
        assert record["command"] == "pr review --self --fix"


def test_self_review_on_a_branch_locks_the_worktree_it_switches_to(
    cr, tmp_path, reviews_dir, monkeypatch,
):
    """`review --self <branch>` writes to a different tree than launched from.

    `_run_self_review` leaves the checkout unlocked at entry for this case —
    the switch below hasn't happened yet, so the launch tree isn't the one
    that ends up written to. Once `switch_to_branch` lands on the real tree,
    that has to be the one the checkout lock covers.
    """
    target = tmp_path / "pr" / "target"
    switched = tmp_path / "switched-wt"
    switched.mkdir()

    ctx = make_ctx(
        repo="acme/widget", pr_number=None, branch="feat/x", head_sha="abc1234",
        worktree_root=tmp_path / "launch-wt", target_dir=target,
    )
    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/orig/wt")
    monkeypatch.setattr(review.worktree, "resolve_branch_input", lambda pr_input, repo_dir: pr_input)
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR())
    monkeypatch.setattr(
        review.worktree, "switch_to_branch",
        lambda branch, wt: review.worktree.WorktreeResult(
            path=str(switched), cleanup_ref=branch, is_fallback=False),
    )
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(review.run, "run_self_review", MagicMock())

    claim = MagicMock()
    monkeypatch.setattr(core.run_lock, "claim_for_process", claim)

    cr._run_self_review(SimpleNamespace(
        positional=["feat/x"], issue=None, max_parallel=1, skip_user_verification=True,
        force=False, no_holistic=False, no_scout=False, disprove=None, max_cost=None,
        model=None, repo_dir="", fix=False, effort="medium", max_groups=None,
        generated=False, recover=False, debug=False, base="",
        post=False, push=False, no_post=False, submit=False,
    ), [], "test 1.0")

    # First claim is at entry with no checkout named (the launch tree is not
    # what gets written to). Second is after the switch, on the real one.
    assert claim.call_args_list[0].kwargs["worktree"] is None
    assert claim.call_args_list[1].kwargs["worktree"] == switched


def test_self_review_on_a_branch_already_checked_out_still_locks_it(
    cr, tmp_path, reviews_dir, monkeypatch,
):
    """`switch_to_branch` returns None when the checkout is already on the
    target branch — the re-review flow (`--self --fix --push` run from inside
    a branch that already has its own PR checked out). The checkout lock has
    to fire even though no actual switch happened, because `--fix` still
    commits into that tree.
    """
    target = tmp_path / "pr" / "target"
    already_checked_out = tmp_path / "already-checked-out"
    already_checked_out.mkdir()

    ctx = make_ctx(
        repo="acme/widget", pr_number=None, branch="feat/x", head_sha="abc1234",
        worktree_root=tmp_path / "launch-wt", target_dir=target,
    )
    monkeypatch.setattr(
        review.worktree, "resolve_wt_path",
        lambda repo_dir, pr_input: str(already_checked_out),
    )
    monkeypatch.setattr(review.worktree, "resolve_branch_input", lambda pr_input, repo_dir: pr_input)
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR())
    monkeypatch.setattr(review.worktree, "switch_to_branch", lambda branch, wt: None)
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(review.run, "run_self_review", MagicMock())

    claim = MagicMock()
    monkeypatch.setattr(core.run_lock, "claim_for_process", claim)

    cr._run_self_review(SimpleNamespace(
        positional=["feat/x"], issue=None, max_parallel=1, skip_user_verification=True,
        force=False, no_holistic=False, no_scout=False, disprove=None, max_cost=None,
        model=None, repo_dir="", fix=False, effort="medium", max_groups=None,
        generated=False, recover=False, debug=False, base="",
        post=False, push=False, no_post=False, submit=False,
    ), [], "test 1.0")

    # First claim is at entry with no checkout named. Second still fires after
    # the no-op switch, naming the tree that is already checked out.
    assert claim.call_args_list[0].kwargs["worktree"] is None
    assert claim.call_args_list[1].kwargs["worktree"] == Path(str(already_checked_out))


# ── the supersession refusal (ordering in the binary flow) ───────────────────


def test_self_review_refuses_before_it_fetches_anything(tmp_path, monkeypatch):
    """Ordering, not just presence: the check has to precede the issue lookup."""
    import pr.supersession

    monkeypatch.setattr(
        pr.supersession, "detect_cached",
        MagicMock(return_value=supersession_verdict(supersession_evidence())),
    )
    monkeypatch.setattr(
        review.issue, "load_issue_provider",
        MagicMock(side_effect=AssertionError("fetched issue context anyway")),
    )

    with pytest.raises(SystemExit) as exc:
        review.run.run_self_review(
            make_ctx(branch="feat/x", pr_number=None),
            _self_flags(), tmp_path, str(tmp_path),
            recover_head_sha="", trail=MagicMock(),
        )
    assert exc.value.code == pr.supersession.EXIT_SUPERSEDED


def test_self_review_recovery_is_not_refused(tmp_path, monkeypatch):
    """A recovery run must not be stranded by a signal that appeared after it started."""
    monkeypatch.setattr(
        pr.supersession, "detect_cached",
        MagicMock(side_effect=AssertionError("checked a recovery run")),
    )
    monkeypatch.setattr(review.recover, "resolve_recover_sha",
                        MagicMock(side_effect=SystemExit(99)))

    with pytest.raises(SystemExit) as exc:
        review.run.run_self_review(
            make_ctx(branch="feat/x", pr_number=None),
            _self_flags(recover=True), tmp_path, str(tmp_path),
            recover_head_sha="", trail=MagicMock(),
        )
    assert exc.value.code == 99


def test_self_review_resolves_locally(cr, tmp_path, reviews_dir, monkeypatch):
    """A self-review names its repo from git, not from a GraphQL call.

    `gh repo view` is GraphQL under the hood, so resolving at REMOTE made the
    pre-PR gate fail whenever that budget was exhausted — on a branch the
    remote has usually never seen.
    """
    seen = {}
    target = tmp_path / "pr" / "target"
    ctx = make_ctx(repo="acme/widget", pr_number=None, branch="feat/x",
                   head_sha="abc1234", worktree_root=tmp_path, target_dir=target)

    def record(depth, **kw):
        seen["depth"] = depth
        return ctx

    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/wt")
    monkeypatch.setattr(pr.context, "resolve_at", record)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR())
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_run_self_review_body", MagicMock())

    cr._run_self_review(_self_review_args(), [])

    assert seen["depth"] is pr.context.ContextDepth.LOCAL


def test_self_review_still_finds_an_open_pr(cr, tmp_path, reviews_dir, monkeypatch):
    """Resolving locally must not cost the reply-thread dedup.

    `--self --fix --push` is documented for a branch whose PR is already open.
    That run uses the PR number to fetch reply threads and skip findings
    already answered there, so the number is looked up separately rather than
    dropped with the REMOTE rung.
    """
    target = tmp_path / "pr" / "target"
    ctx = make_ctx(repo="acme/widget", pr_number=None, branch="feat/x",
                   head_sha="abc1234", worktree_root=tmp_path, target_dir=target)
    body = MagicMock()

    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/wt")
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR(number=2973))
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_run_self_review_body", body)

    cr._run_self_review(_self_review_args(), [])

    # pr_number is the second positional of _run_self_review_body.
    assert body.call_args.args[1] == "2973"


def test_self_review_carries_the_open_prs_base_into_the_run(
    cr, tmp_path, reviews_dir, monkeypatch,
):
    """The number and the base come off one call, and both have to survive the
    hop into the body: the context resolved at LOCAL carries neither, so a base
    dropped here leaves a stacked branch measured against the trunk."""
    target = tmp_path / "pr" / "target"
    ctx = make_ctx(repo="acme/widget", pr_number=None, branch="feat/child",
                   head_sha="abc1234", worktree_root=tmp_path, target_dir=target)
    body = MagicMock()

    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/wt")
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(
        pr.context, "pr_number_if_reachable",
        lambda repo, branch: pr.context.BranchPR(number=2973, base="feat/parent"),
    )
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_run_self_review_body", body)

    cr._run_self_review(_self_review_args(), [])

    # ctx is the first positional of _run_self_review_body.
    assert body.call_args.args[0].base == "feat/parent"
    assert body.call_args.args[0].pr_number == 2973


def test_self_review_proceeds_when_no_pr_can_be_named(cr, tmp_path, reviews_dir, monkeypatch):
    """The pre-PR case, and the unreachable-API case, are the same case here:
    no number, and the run goes ahead regardless."""
    target = tmp_path / "pr" / "target"
    ctx = make_ctx(repo="acme/widget", pr_number=None, branch="feat/x",
                   head_sha="abc1234", worktree_root=tmp_path, target_dir=target)
    body = MagicMock()

    monkeypatch.setattr(review.worktree, "resolve_wt_path", lambda repo_dir, pr_input: "/wt")
    monkeypatch.setattr(pr.context, "resolve_at", lambda depth, **kw: ctx)
    monkeypatch.setattr(pr.context, "pr_number_if_reachable",
                        lambda repo, branch: pr.context.BranchPR())
    monkeypatch.setattr(review.worktree, "cleanup_self_review_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_run_self_review_body", body)

    cr._run_self_review(_self_review_args(), [])

    assert body.call_args.args[1] == ""


def test_a_pr_review_locks_the_worktree_it_actually_writes_to(tmp_path, monkeypatch):
    """The tree reviewed is not the tree the run was launched in.

    `setup_pr_worktree` switches to the PR's own worktree and hard-resets it,
    so the checkout claimed at entry — where the operator stood — is not the
    one being written to. The lock has to be taken again once the real tree is
    known, or the reset happens in a tree no lock covers.
    """
    review_file = tmp_path / "review" / "review.md"
    review_file.parent.mkdir()
    review_file.write_text("## Must fix\n- **[M1]** boom\n")

    reviewed = tmp_path / "pr-worktree"
    reviewed.mkdir()
    _stub_pr_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(review.worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(reviewed),
                                                         is_fallback=False))
    monkeypatch.setattr(review.recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(reviewed), None))
    monkeypatch.setattr(core.prompt, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(review.publish, "post", MagicMock())
    monkeypatch.setattr(core.prompt, "ask", lambda *a, **kw: "")

    claim = MagicMock()
    monkeypatch.setattr(core.run_lock, "claim_for_process", claim)

    ctx = make_ctx(target_dir=tmp_path / "t")
    review.run.run_pr_review(
        ctx,
        review.run.ReviewFlags(bin_dir=Path("/bin"), generator_version="test 1.0"),
        review_file, trail=MagicMock(),
    )

    assert claim.call_args.kwargs["worktree"] == reviewed
    # Same target as the lock taken at entry, so the two are one claim and the
    # second passes through rather than contending with the first.
    assert claim.call_args[0][0] == ctx.target_dir


def test_a_pr_review_refuses_a_mistyped_base_too(tmp_path, monkeypatch):
    """The PR path takes the same override and anchors the same ranges, so it
    needs the same refusal — and it resolves against the PR's own checkout, not
    the tree the operator was standing in."""
    review_file = tmp_path / "review" / "review.md"
    review_file.parent.mkdir()
    review_file.write_text("## Must fix\n- **[M1]** boom\n")

    reviewed = init_repo(tmp_path / "pr-worktree")
    (reviewed / "a.txt").write_text("a\n")
    commit_all(reviewed, "init")

    _stub_pr_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(review.worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(reviewed),
                                                         is_fallback=False))
    monkeypatch.setattr(review.recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(reviewed), None))
    monkeypatch.setattr(core.prompt, "ask", lambda *a, **kw: "")
    monkeypatch.setattr(
        review.invoke, "run",
        MagicMock(side_effect=AssertionError("spent a review on an unresolvable base")),
    )

    with pytest.raises(SystemExit) as exc:
        review.run.run_pr_review(
            make_ctx(target_dir=tmp_path / "t"),
            review.run.ReviewFlags(bin_dir=Path("/bin"), generator_version="test 1.0",
                                   base="relase/1.2"),
            review_file, trail=MagicMock(),
        )

    assert exc.value.code == 1


def test_a_pr_review_checkout_lock_reuses_the_target_locks_command(tmp_path, monkeypatch):
    """The checkout lock and the target lock report the same holder.

    The target lock is claimed in `review`'s `main()` with the full
    invocation (`" ".join([SCRIPT] + sys.argv[1:])`). The checkout lock here
    used to hardcode `f"review {pr_number}"` instead, so a contender
    tripping the checkout lock saw a different command than one tripping the
    target lock for the same run. `flags.command` threads the same string
    through to both.
    """
    review_file = tmp_path / "review" / "review.md"
    review_file.parent.mkdir()
    review_file.write_text("## Must fix\n- **[M1]** boom\n")

    reviewed = tmp_path / "pr-worktree"
    reviewed.mkdir()
    _stub_pr_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(review.worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(reviewed),
                                                         is_fallback=False))
    monkeypatch.setattr(review.recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(reviewed), None))
    monkeypatch.setattr(core.prompt, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(review.publish, "post", MagicMock())
    monkeypatch.setattr(core.prompt, "ask", lambda *a, **kw: "")

    claim = MagicMock()
    monkeypatch.setattr(core.run_lock, "claim_for_process", claim)

    ctx = make_ctx(target_dir=tmp_path / "t")
    review.run.run_pr_review(
        ctx,
        review.run.ReviewFlags(bin_dir=Path("/bin"), generator_version="test 1.0",
                               command="review 42 --fix --push"),
        review_file, trail=MagicMock(),
    )

    assert claim.call_args.kwargs["command"] == "review 42 --fix --push"


def test_a_stacked_self_review_measures_against_its_parent(tmp_path, monkeypatch):
    """End to end through `run_self_review`: the base the ladder resolves is the
    one that reaches the pipeline, and the supersession gate that runs before
    it. Without this the two disagree — the branch is refused over commits its
    parent made, and if it survives, reviewed against the wrong base."""
    seen = {}
    monkeypatch.setattr(
        review.preflight, "refuse_if_superseded",
        lambda *a, **kw: seen.__setitem__("gate_base", kw.get("base")),
    )
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(review.issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(review.invoke, "run",
                        lambda request: seen.__setitem__("sent_base", request.base) or 0)
    monkeypatch.setattr(review.run, "finish_review", lambda *a, **kw: None)

    ctx = make_ctx(repo="owner/repo", pr_number=None, branch="feat/child",
                   head_sha="abc1234", worktree_root=tmp_path,
                   target_dir=tmp_path / "state", base="feat/parent")

    review.run.run_self_review(
        ctx, _self_flags(), tmp_path, str(tmp_path),
        recover_head_sha="", trail=MagicMock(),
    )

    assert seen["sent_base"] == "feat/parent"
    assert seen["gate_base"] == "feat/parent"


def test_an_explicit_base_overrides_the_one_github_reports(tmp_path, monkeypatch):
    """The escape hatch: derivation picks a stale branch parked between this one
    and its real parent, and `--base` is how an operator says otherwise."""
    seen = {}
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(review.issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(review.invoke, "run",
                        lambda request: seen.__setitem__("sent_base", request.base) or 0)
    monkeypatch.setattr(review.run, "finish_review", lambda *a, **kw: None)

    # A real repo holding the branch `--base` names: an override that resolves
    # to nothing is refused before it gets here, which is its own test.
    repo = init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n")
    commit_all(repo, "init")
    git_in(repo, "branch", "feat/right")
    git_in(repo, "checkout", "-b", "feat/child", "-q")
    (repo / "b.txt").write_text("b\n")
    commit_all(repo, "child")

    ctx = make_ctx(repo="owner/repo", pr_number=None, branch="feat/child",
                   head_sha="abc1234", worktree_root=repo,
                   target_dir=tmp_path / "state", base="feat/wrong")

    review.run.run_self_review(
        ctx, _self_flags(base="feat/right"), tmp_path, str(repo),
        recover_head_sha="", trail=MagicMock(),
    )

    assert seen["sent_base"] == "feat/right"


def test_a_mistyped_base_stops_the_run_before_it_spends_anything(tmp_path, monkeypatch):
    """End to end: a typo'd `--base` must not reach the pipeline. Every range
    would anchor to a ref that does not exist, the diff would come back empty,
    and the review would report no findings for a branch it never read."""
    monkeypatch.setattr(
        review.invoke, "run",
        MagicMock(side_effect=AssertionError("spent a review on an unresolvable base")),
    )
    repo = init_repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n")
    commit_all(repo, "init")

    ctx = make_ctx(repo="owner/repo", pr_number=None, branch="feat/child",
                   worktree_root=repo, target_dir=tmp_path / "state")

    with pytest.raises(SystemExit) as exc:
        review.run.run_self_review(
            ctx, _self_flags(base="feat/parnet"), tmp_path, str(repo),
            recover_head_sha="", trail=MagicMock(),
        )

    assert exc.value.code == 1


def test_a_derived_base_that_does_not_resolve_is_not_refused(tmp_path, monkeypatch):
    """The refusal is scoped to the operator's own `--base`. A derived base git
    cannot answer for is a gap in the tool, not a typo — refusing there would
    fail every worktree with no origin, which the range fallbacks handle."""
    seen = {}
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda wt: SimpleNamespace(name="none", options={}))
    monkeypatch.setattr(review.issue, "extract_issue_id", lambda *a: "")
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(review.invoke, "run",
                        lambda request: seen.__setitem__("ran", True) or 0)
    monkeypatch.setattr(review.run, "finish_review", lambda *a, **kw: None)

    ctx = make_ctx(repo="owner/repo", pr_number=None, branch="feat/child",
                   worktree_root=tmp_path, target_dir=tmp_path / "state",
                   base="a-branch-that-is-not-here")

    review.run.run_self_review(
        ctx, _self_flags(), tmp_path, str(tmp_path),
        recover_head_sha="", trail=MagicMock(),
    )

    assert seen["ran"] is True


def test_an_in_process_caller_can_keep_its_own_signal_handler(cr, monkeypatch):
    """The entry point that owns the process owns SIGINT.

    `signal.signal` overwrites without chaining and nothing restores it, so a
    `main` called in-process must be able to decline to install one rather than
    silently replacing its caller's for the rest of the run.
    """
    installed = []
    monkeypatch.setattr(signal, "signal", lambda *a: installed.append(a[0]))

    def signals_installed_by(flag):
        installed.clear()
        with pytest.raises(RuntimeError):
            cr.main([], install_signal_handler=flag)
        return list(installed)

    with patch.object(cr, "build_parser", side_effect=RuntimeError("stop")):
        assert signals_installed_by(False) == []
        assert signals_installed_by(True) == [signal.SIGINT]
