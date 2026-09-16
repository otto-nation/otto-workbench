"""Archiving the prior review and the gates that exit are mutually exclusive.

`_resolve_prior_review` is destructive when it archives: `archive_review`
copies the review to `prior.md` and *moves* the review and its logs into
`archives/`. Two gates below that call exit rather than return — a `--fix`
pass refused because the recovered commit has drifted, and a pin that cannot
find its commit. A run that archived and then hit one of those would have
rotated a review it never replaced.

It cannot happen, and the reason is not local to any of those three places:
`_resolve_prior_review` archives only when `resume` is false, `--recover`
forces `resume` true before the call, and both gates are reachable only under
`--recover`. The safety is an interaction between a flag and a branch thirty
lines apart, stated nowhere.

That is the shape `…-00`'s fourth recurring finding describes: a decomposition
does not break invariants, it removes the adjacency that was silently
enforcing them. These tests pin the invariant now, so that a refactor which
separates the archive from the recover branch has to fail a test rather than
quietly start destroying recoverable reviews.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "ai" / "bin" / "claude-review"
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from conftest import load_script, make_ctx  # noqa: E402


@pytest.fixture(scope="session")
def cr():
    bin_dir = str(SCRIPT_PATH.parent)
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    return load_script("claude_review", SCRIPT_PATH)


def _seeded_review_dir(tmp_path, *, pipeline_state=True):
    """A review directory as a failed run leaves it.

    `pipeline.json` is present by default because a `--recover` run cannot get
    past `resolve_recover_sha` without it: that function exits 1 on a missing
    pipeline state (`review/recover.py:61`). A fixture omitting it would be
    describing a state no recover run reaches.

    That is also the third mechanism holding the invariant these tests pin, and
    the reason two of them cannot be made to fail by flipping one branch: the
    recover flag forces `resume`, *and* recover requires the state file whose
    presence would have set it anyway.
    """
    d = tmp_path / "widget-self-feat-x"
    d.mkdir(parents=True)
    (d / "review.md").write_text("## Must fix\n- **[M1]** the finding being recovered\n")
    (d / "session.jsonl").write_text('{"type":"result"}\n')
    if pipeline_state:
        (d / "pipeline.json").write_text('{"head_sha": "abc1234"}')
    return d


def _args(**overrides):
    base = dict(
        no_post=True, post=False, submit=False, issue="", max_parallel=None,
        force=False, disprove=None, max_cost=None, model="", repo_dir="",
        effort=None, max_groups=None, generated=False, recover=True,
        debug=False, push=False, fix=True, _json_stdout=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_resumed_run_reuses_the_prior_review_rather_than_rotating_it(cr, tmp_path):
    """The load-bearing half: `resume` is what makes the call non-destructive.

    Asserted directly on the helper, because this is the property every test
    below depends on and it is one boolean deep.
    """
    review_dir = _seeded_review_dir(tmp_path)
    (review_dir / "prior.md").write_text("## Must fix\n- **[M1]** from the failed run\n")

    resolved = cr._resolve_prior_review(
        review_dir / "review.md", str(review_dir / "session.jsonl"), True,
    )

    assert resolved == str(review_dir / "prior.md")
    assert (review_dir / "review.md").is_file(), "resuming must not rotate the review"
    assert not (review_dir / "archives").exists()


def test_a_refused_fix_on_a_drifted_recover_leaves_the_review_in_place(
    cr, tmp_path, monkeypatch,
):
    """`--self --fix --recover` against a moved HEAD refuses without rotating.

    The run is refused because a fix pass would edit a throwaway checkout. The
    review the operator is told to recover without `--fix` has to still be
    there when they do.
    """
    review_dir = _seeded_review_dir(tmp_path)
    original = (review_dir / "review.md").read_text()

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_recover, "resolve_recover_sha", lambda *a, **kw: "abc1234")
    # HEAD has moved on from the commit the failed review ran against.
    monkeypatch.setattr(cr.review_recover, "recover_drifted", lambda *a, **kw: True)
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(cr.git_client, "abbrev", lambda sha: sha[:7])

    with pytest.raises(SystemExit) as excinfo:
        cr._run_self_review_body(
            "acme/widget", "", str(tmp_path), "issue-1", None, None, "", True,
            _args(), review_dir, "feat/x",
            ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
        )

    assert excinfo.value.code == 1
    assert (review_dir / "review.md").read_text() == original, (
        "the refused run rotated the review it refused to act on"
    )
    assert not (review_dir / "archives").exists()


def test_a_pin_that_cannot_find_its_commit_leaves_the_review_in_place(
    cr, tmp_path, monkeypatch,
):
    """The same guarantee for the other gate below the archive.

    `pin_recover_worktree` exits when the recovered commit has gone — a
    force-push, a pruned branch. It lives in another module, so a reordering
    is likelier to leave it behind than the in-file check above.
    """
    review_dir = _seeded_review_dir(tmp_path)

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_recover, "resolve_recover_sha", lambda *a, **kw: "abc1234")
    monkeypatch.setattr(cr.review_recover, "recover_drifted", lambda *a, **kw: False)
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))

    def _pin_fails(*a, **kw):
        raise SystemExit(1)

    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree", _pin_fails)

    with pytest.raises(SystemExit):
        cr._run_self_review_body(
            "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
            _args(fix=False), review_dir, "feat/x",
            ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
        )

    assert (review_dir / "review.md").is_file()
    assert not (review_dir / "archives").exists()


def test_the_body_asks_whether_to_archive_rather_than_always_archiving(
    cr, tmp_path, monkeypatch,
):
    """The body routes through `_resolve_prior_review`, not `archive_review`.

    The tests above cannot see this. Under `--recover` the pipeline state file
    must exist, so `resume` is true by two independent routes and calling
    `archive_review` directly from the body behaves identically — a mutation
    that did exactly that passed all of them. What distinguishes the two is
    only visible on a resumed run that is *not* a recover: pipeline state
    present, `--recover` absent.

    Pinned as the call itself because that is the property worth keeping. The
    body's job is to ask whether this run replaces a review; deciding that it
    always does is the refactor this file exists to stop.
    """
    review_dir = _seeded_review_dir(tmp_path)
    (review_dir / "prior.md").write_text("## Must fix\n- **[M1]** from the failed run\n")

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_recover, "recover_drifted", lambda *a, **kw: False)
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_display_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_print_summary", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_update_pr_state", lambda *a, **kw: None)

    def _orchestrate(request):
        (review_dir / "review.md").write_text("## Must fix\n- **[M1]** a fresh finding\n")
        return 0

    monkeypatch.setattr(cr.review_invoke, "run", _orchestrate)

    cr._run_self_review_body(
        "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
        _args(recover=False, fix=False), review_dir, "feat/x",
        ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
    )

    assert not (review_dir / "archives").exists(), (
        "a resumed run reuses prior.md; it does not rotate the review it is finishing"
    )


def test_a_fresh_review_rotates_the_one_it_replaces(cr, tmp_path, monkeypatch):
    """The other half: with no pipeline state the archive is the whole point.

    A plain self review rotates the previous review into `archives/` so the
    directory holds one review. Pinned alongside the tests above so that
    "nothing was archived" cannot be satisfied by never archiving at all.
    """
    review_dir = _seeded_review_dir(tmp_path, pipeline_state=False)

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_recover, "recover_drifted", lambda *a, **kw: False)
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_display_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_print_summary", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_update_pr_state", lambda *a, **kw: None)

    def _orchestrate(request):
        (review_dir / "review.md").write_text("## Must fix\n- **[M1]** a fresh finding\n")
        return 0

    monkeypatch.setattr(cr.review_invoke, "run", _orchestrate)

    cr._run_self_review_body(
        "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
        _args(recover=False, fix=False), review_dir, "feat/x",
        ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
    )

    archived = sorted((review_dir / "archives").glob("*.md"))
    assert archived, "a fresh review archives the one it replaces"
    assert "the finding being recovered" in archived[0].read_text()
    assert "a fresh finding" in (review_dir / "review.md").read_text()
