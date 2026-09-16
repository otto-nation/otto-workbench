"""Characterisation of the two review flows, ahead of merging them.

`claude-review` runs a PR review and a self review through two nearly
identical flows. They are about to become one, and the risk in that merge is
not the code that obviously differs — it is the code that looks the same and
is not. Several of the differences below are positional: the same call, in
both flows, made from a different frame or in a different order. A test
asserting only that a call happened cannot see those, so every ordering test
here records into one shared list and asserts the sequence.

These tests describe what the flows do today, including where today's answer
is merely what the code grew into. They are a tripwire, not a specification:
a merge that changes any of this should have to say so out loud by editing a
test, rather than discovering it later as a regression.
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


def _review_file(tmp_path, name="review"):
    """A review file on disk, which is what both flows check for after the run."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "review.md"
    f.write_text("## Must fix\n- **[M1]** boom\n")
    return f


def _args(**overrides):
    """Parsed-argv stand-in carrying only what the flows read off it."""
    base = dict(
        no_post=True, post=False, submit=False, issue="", max_parallel=None,
        force=False, disprove=None, max_cost=None, model="", repo_dir="",
        effort=None, max_groups=None, generated=False, recover=False,
        debug=False, push=False, fix=False, _json_stdout=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _trace_common(cr, monkeypatch, tape, review_file):
    """Stub the machinery both flows share, recording each step onto *tape*.

    Everything here is a step the merge must keep; the per-test assertions are
    about where the remaining, flow-specific steps land relative to these.
    """
    monkeypatch.setattr(cr, "_build_orchestrate_args",
                        lambda **kw: (tape.append("build_args"), ["true"])[1])
    monkeypatch.setattr(cr.subprocess, "run",
                        lambda *a, **kw: (tape.append("orchestrate"),
                                          SimpleNamespace(returncode=0))[1])
    monkeypatch.setattr(cr, "_cleanup_prior_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_display_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_print_summary",
                        lambda *a, **kw: tape.append("print_summary"))
    monkeypatch.setattr(cr, "_update_pr_state",
                        lambda *a, **kw: tape.append("domain_write"))
    monkeypatch.setattr(cr, "_resolve_prior_review", lambda *a, **kw: "")
    monkeypatch.setattr(cr, "json_summary", lambda *a, **kw: '{"x":1}')


# ── A. where the review-domain write happens ────────────────────────────────


def test_the_pr_flow_writes_the_domain_after_the_body_returns(
    cr, tmp_path, monkeypatch,
):
    """PR path: the write is the caller's, not the body's.

    `_run_review` calls it after `_run_review_pr` returns, inside the trail's
    scope so a failed write has somewhere to report. Pinned by frame, not by
    call count: moving it into the body keeps the call and breaks the order.
    """
    tape = []
    review_file = _review_file(tmp_path)
    _trace_common(cr, monkeypatch, tape, review_file)

    monkeypatch.setattr(cr, "review_file_path", lambda *a, **kw: review_file)
    monkeypatch.setattr(cr, "_run_review_pr",
                        lambda *a, **kw: tape.append("body_returned"))
    monkeypatch.setattr(cr.Trail, "start", lambda **kw: MagicMock())

    cr._run_review(_args(), make_ctx(target_dir=tmp_path / "t"))

    assert tape == ["body_returned", "domain_write"], (
        "the PR flow's domain write belongs to _run_review, after the body"
    )


def test_the_self_flow_writes_the_domain_inside_the_body(
    cr, tmp_path, monkeypatch,
):
    """Self path: the write is the body's own, before the JSON summary.

    The mirror of the test above. The two flows genuinely disagree about which
    frame owns this call, and the merge has to pick one deliberately.
    """
    tape = []
    review_dir = tmp_path / "self"
    review_file = _review_file(tmp_path, "self")
    _trace_common(cr, monkeypatch, tape, review_file)

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: tape.append("preflight"))
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree",
                        lambda *a, **kw: tape.append("pin_cleanup"))
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))

    cr._run_self_review_body(
        "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
        _args(), review_dir, "feat/x",
        ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
    )

    assert "domain_write" in tape, "the self body writes the domain itself"
    assert tape.index("print_summary") < tape.index("domain_write"), (
        "the self body prints its summary before writing the domain"
    )


# ── D. when the supersession preflight runs ─────────────────────────────────


def test_the_self_flow_refuses_a_superseded_review_before_doing_any_work(
    cr, tmp_path, monkeypatch,
):
    """Self path: the preflight is the first statement of the body.

    It runs ahead of the issue fetch and the pin, which is the cheapest point
    at which a superseded run can still cost nothing. The PR path deliberately
    runs it later — after the pin, so a refusal still cleans up the worktree it
    read — and that asymmetry is the one the merge is most likely to flatten.
    """
    tape = []
    review_dir = tmp_path / "self"
    review_file = _review_file(tmp_path, "self")
    _trace_common(cr, monkeypatch, tape, review_file)

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: tape.append("preflight"))
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (tape.append("pin"), (str(tmp_path), None))[1])
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: (tape.append("issue_provider"),
                                          SimpleNamespace(name="", options={}))[1])

    cr._run_self_review_body(
        "acme/widget", "", str(tmp_path), "", None, None, "", False,
        _args(), review_dir, "feat/x",
        ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
    )

    assert tape[0] == "preflight", (
        "the self body refuses a superseded review before anything costs money"
    )
    assert tape.index("preflight") < tape.index("pin")
    assert tape.index("preflight") < tape.index("issue_provider")


# ── E. what the pin's finally covers ────────────────────────────────────────


def test_the_self_flow_releases_its_pin_before_rendering_the_review(
    cr, tmp_path, monkeypatch,
):
    """Self path: the pin's `finally` wraps only the orchestrate subprocess.

    The pinned worktree is gone by the time the review is displayed and the
    summary printed. The PR path holds its pin across the whole body instead.
    Widening this `finally` to match would keep a throwaway checkout alive
    through the interactive tail of the run, and no call-count assertion
    anywhere would notice.
    """
    tape = []
    review_dir = tmp_path / "self"
    review_file = _review_file(tmp_path, "self")
    _trace_common(cr, monkeypatch, tape, review_file)

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), object()))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree",
                        lambda *a, **kw: tape.append("pin_cleanup"))
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))

    cr._run_self_review_body(
        "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
        _args(), review_dir, "feat/x",
        ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
    )

    assert tape.index("orchestrate") < tape.index("pin_cleanup"), (
        "the pin is released after the subprocess it exists for"
    )
    assert tape.index("pin_cleanup") < tape.index("print_summary"), (
        "and before the summary — the self pin does not span the whole body"
    )


def test_the_pr_flow_holds_its_pin_across_the_whole_body(
    cr, tmp_path, monkeypatch,
):
    """PR path: both worktrees are released only after the body returns.

    The converse of the test above, and the reason the PR path's `finally`
    covers more: `pin_recover_worktree` can exit, so a fallback PR worktree
    would otherwise be left on disk.
    """
    tape = []
    review_dir = tmp_path / "pr"
    review_file = _review_file(tmp_path, "pr")

    monkeypatch.setattr(cr.review_worktree, "find_repo_root", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(cr.gh_client, "pr_view", lambda *a, **kw: {"headRefName": "f", "body": ""})
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(cr.review_recover, "get_pr_head_sha", lambda *a, **kw: "sha")
    monkeypatch.setattr(cr.review_preflight, "check_stale_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_preflight, "check_pending_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(tmp_path), is_fallback=False))
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree",
                        lambda *a, **kw: tape.append("cleanup"))
    monkeypatch.setattr(cr, "_resolve_prior_review", lambda *a, **kw: "")
    monkeypatch.setattr(cr, "_run_review_body", lambda *a, **kw: tape.append("body"))

    cr._run_review_pr(
        MagicMock(), make_ctx(target_dir=tmp_path / "t"), "42", "acme/widget",
        review_dir, review_file, "issue-1", True, False, False,
        False, "", None, None, "",
    )

    assert tape[0] == "body", "cleanup happens only once the body is done"
    assert tape.count("cleanup") == 2, "the pin and the PR worktree are both released"


# ── the shared spine ────────────────────────────────────────────────────────


def test_both_flows_run_the_same_ordered_spine(cr, tmp_path, monkeypatch):
    """Whatever else differs, these steps happen, in this order, on both paths.

    Written as one test over the self body because that is the flow that holds
    the whole spine in a single frame; the PR half of each step is pinned by
    the frame-specific tests above. A merge that drops or reorders any of
    these is a merge that changed behaviour.
    """
    tape = []
    review_dir = tmp_path / "self"
    review_file = _review_file(tmp_path, "self")
    _trace_common(cr, monkeypatch, tape, review_file)

    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: tape.append("preflight"))
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))

    cr._run_self_review_body(
        "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
        _args(), review_dir, "feat/x",
        ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
    )

    assert tape == [
        "preflight", "build_args", "orchestrate", "print_summary", "domain_write",
    ]


def test_a_failed_orchestration_stops_before_the_domain_is_written(
    cr, tmp_path, monkeypatch,
):
    """A non-zero orchestrate exits, and nothing downstream of it runs.

    The failure path matters as much as the success one: a merge that moved
    the domain write above the return-code check would record a review that
    never finished.
    """
    tape = []
    review_dir = tmp_path / "self"
    review_dir.mkdir()
    _trace_common(cr, monkeypatch, tape, review_dir / "review.md")

    monkeypatch.setattr(cr.subprocess, "run",
                        lambda *a, **kw: (tape.append("orchestrate"),
                                          SimpleNamespace(returncode=1))[1])
    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))

    with pytest.raises(SystemExit):
        cr._run_self_review_body(
            "acme/widget", "", str(tmp_path), "issue-1", None, None, "", False,
            _args(), review_dir, "feat/x",
            ctx=make_ctx(target_dir=tmp_path / "t"), trail=MagicMock(),
        )

    assert "domain_write" not in tape, "a failed review is not a review to record"
