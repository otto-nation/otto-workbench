"""Characterisation of the two review flows, now that they are two modules.

`review.run` holds a PR flow and a self flow. They are nearly identical and
deliberately not unified, because several of their differences are positional:
the same call, in both flows, made in a different order. A test asserting only
that a call happened cannot see those, so every ordering test here records into
one shared list and asserts the sequence.

These tests describe what the flows do, including where today's answer is
merely what the code grew into. They are a tripwire, not a specification: a
change to any of this should have to say so out loud by editing a test, rather
than being discovered later as a regression.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from conftest import make_ctx  # noqa: E402

from review import completion as review_completion  # noqa: E402
from review import invoke as review_invoke  # noqa: E402
from review import issue as review_issue  # noqa: E402
from review import preflight as review_preflight  # noqa: E402
from review import publish as review_publish  # noqa: E402
from review import recover as review_recover  # noqa: E402
from review import run as review_run  # noqa: E402
from review import worktree as review_worktree  # noqa: E402


def _flags(**overrides):
    base = dict(bin_dir=Path("/bin"), generator_version="test 1.0", no_post=True)
    base.update(overrides)
    return review_run.ReviewFlags(**base)


def _review_file(tmp_path, name="review"):
    """A review file on disk, which is what both flows check for after the run."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / "review.md"
    f.write_text("## Must fix\n- **[M1]** boom\n")
    return f


def _trace_common(monkeypatch, tape, *, returncode=0):
    """Stub the machinery both flows share, recording each step onto *tape*.

    Everything here is a step both flows must keep; the per-test assertions are
    about where the remaining, flow-specific steps land relative to these.
    """
    def _run(request):
        tape.append("orchestrate")
        if returncode != 0:
            raise SystemExit(1)
        return 0

    monkeypatch.setattr(review_invoke, "run", _run)
    monkeypatch.setattr(review_run.review_invoke, "run", _run)
    monkeypatch.setattr(review_completion, "cleanup_prior_review", lambda *a, **kw: None)
    monkeypatch.setattr(review_completion, "_display", lambda *a, **kw: None)
    monkeypatch.setattr(review_completion, "summarise",
                        lambda *a, **kw: tape.append("print_summary"))
    monkeypatch.setattr(review_completion, "record_domain",
                        lambda *a, **kw: tape.append("domain_write"))
    monkeypatch.setattr(review_run, "resolve_prior_review", lambda *a, **kw: "")
    monkeypatch.setattr(review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(review_issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))


# ── A. where the review-domain write happens ────────────────────────────────


def test_both_flows_write_the_domain_after_the_summary(tmp_path, monkeypatch):
    """One frame owns this call, and it is the shared tail.

    The two flows used to disagree — the PR path wrote from its caller after the
    body returned, the self path from inside its own body — which meant the
    guarantee "a review that ran is recorded" had two independent
    implementations. `finish_review` is now the only writer, so the ordering is
    asserted once and holds for both.
    """
    tape = []
    review_file = _review_file(tmp_path, "self")
    _trace_common(monkeypatch, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(review_worktree, "cleanup_worktree", lambda *a, **kw: None)

    review_run.run_self_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file.parent,
        str(tmp_path), recover_head_sha="", trail=MagicMock(),
    )

    assert tape.index("print_summary") < tape.index("domain_write"), (
        "the summary is printed before the domain is written"
    )


# ── D. when the supersession preflight runs ─────────────────────────────────


def test_the_self_flow_refuses_a_superseded_review_before_doing_any_work(
    tmp_path, monkeypatch,
):
    """Self path: the preflight is the first statement of the flow.

    It runs ahead of the issue fetch and the pin, which is the cheapest point
    at which a superseded run can still cost nothing. The PR path deliberately
    runs it later — after the worktree exists, so a refusal still cleans up
    what it read — and that asymmetry is the one a merge would flatten.
    """
    tape = []
    review_file = _review_file(tmp_path, "self")
    _trace_common(monkeypatch, tape)

    monkeypatch.setattr(review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: tape.append("preflight"))
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (tape.append("pin"), (str(tmp_path), None))[1])
    monkeypatch.setattr(review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(review_issue, "load_issue_provider",
                        lambda *a, **kw: (tape.append("issue_provider"),
                                          SimpleNamespace(name="", options={}))[1])

    review_run.run_self_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file.parent,
        str(tmp_path), recover_head_sha="", trail=MagicMock(),
    )

    assert tape[0] == "preflight", (
        "the self flow refuses a superseded review before anything costs money"
    )
    assert tape.index("preflight") < tape.index("pin")
    assert tape.index("preflight") < tape.index("issue_provider")


def test_the_pr_flow_refuses_inside_the_finally_that_owns_its_worktree(
    tmp_path, monkeypatch,
):
    """PR path: the refusal comes after the worktree, and still cleans it up.

    The converse of the test above, and the reason the two orderings cannot be
    one parameter: the self flow wants the refusal as early as possible, the PR
    flow wants it somewhere a `finally` will release what it read.
    """
    tape = []
    review_file = _review_file(tmp_path, "pr")
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)

    monkeypatch.setattr(review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: (tape.append("preflight"),
                                          (_ for _ in ()).throw(SystemExit(3)))[0])

    with pytest.raises(SystemExit):
        review_run.run_pr_review(
            make_ctx(target_dir=tmp_path / "t"), _flags(), review_file,
            trail=MagicMock(),
        )

    assert tape.index("setup_worktree") < tape.index("preflight"), (
        "the PR flow has a worktree before it decides whether to keep it"
    )
    assert "cleanup" in tape, "and releases it on the way out of the refusal"


# ── E. what the pin's finally covers ────────────────────────────────────────


def test_the_self_flow_releases_its_pin_before_rendering_the_review(
    tmp_path, monkeypatch,
):
    """Self path: the pin's `finally` wraps only the pipeline call.

    The pinned worktree is gone by the time the review is displayed and the
    summary printed. The PR path holds its pin across the whole body instead.
    Widening this `finally` to match would keep a throwaway checkout alive
    through the interactive tail of the run, and no call-count assertion
    anywhere would notice.
    """
    tape = []
    review_file = _review_file(tmp_path, "self")
    _trace_common(monkeypatch, tape)

    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), object()))
    monkeypatch.setattr(review_worktree, "cleanup_worktree",
                        lambda *a, **kw: tape.append("pin_cleanup"))

    review_run.run_self_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file.parent,
        str(tmp_path), recover_head_sha="", trail=MagicMock(),
    )

    assert tape.index("orchestrate") < tape.index("pin_cleanup"), (
        "the pin is released after the pipeline it exists for"
    )
    assert tape.index("pin_cleanup") < tape.index("print_summary"), (
        "and before the summary — the self pin does not span the whole flow"
    )


def _stub_pr_edges(monkeypatch, tmp_path, tape):
    """The PR flow's own edges: clone lookup, gh, worktree, freshness."""
    monkeypatch.setattr(review_worktree, "find_repo_root", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(review_run.gh_client, "pr_view",
                        lambda *a, **kw: {"headRefName": "f", "body": ""})
    monkeypatch.setattr(review_recover, "get_pr_head_sha", lambda *a, **kw: "sha")
    monkeypatch.setattr(review_preflight, "check_stale_review",
                        lambda *a, **kw: tape.append("check_stale"))
    monkeypatch.setattr(review_preflight, "check_pending_review",
                        lambda *a, **kw: tape.append("check_pending"))
    monkeypatch.setattr(review_recover, "should_auto_recover",
                        lambda *a, **kw: tape.append("should_auto_recover"))
    monkeypatch.setattr(review_worktree, "setup_pr_worktree",
                        lambda *a, **kw: (tape.append("setup_worktree"),
                                          SimpleNamespace(path=str(tmp_path),
                                                          is_fallback=False))[1])
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(review_worktree, "cleanup_worktree",
                        lambda *a, **kw: tape.append("cleanup"))
    monkeypatch.setattr(review_publish, "resolve",
                        lambda *a, **kw: review_publish.PostResult(False, False, ""))


def test_the_pr_flow_holds_its_pin_across_the_whole_body(tmp_path, monkeypatch):
    """PR path: both worktrees are released only after the review is done.

    The converse of the test above, and the reason the PR path's `finally`
    covers more: `pin_recover_worktree` can exit, so a fallback PR worktree
    would otherwise be left on disk.
    """
    tape = []
    review_file = _review_file(tmp_path, "pr")
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file, trail=MagicMock(),
    )

    assert tape.index("domain_write") < tape.index("cleanup"), (
        "cleanup happens only once the review is recorded"
    )
    assert tape.count("cleanup") == 2, "the pin and the PR worktree are both released"


def test_the_pr_flow_checks_freshness_before_it_pays_for_a_worktree(
    tmp_path, monkeypatch,
):
    """Building the worktree is the expensive step, and it comes last.

    `check_stale_review` and `check_pending_review` can both abort the run by
    prompting, and `should_auto_recover` can redirect it. All three are cheap
    and all three run first, so an aborted review costs a `gh` call rather
    than an unshallowed clone and a checkout.
    """
    tape = []
    review_file = _review_file(tmp_path, "pr")
    (review_file.parent / "pipeline.json").write_text("{}")
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file, trail=MagicMock(),
    )

    assert "setup_worktree" in tape, "the PR flow builds a worktree"
    assert tape.index("check_pending") < tape.index("setup_worktree"), (
        "freshness is decided before a worktree is paid for"
    )
    assert tape.index("should_auto_recover") < tape.index("setup_worktree")


def test_the_pr_flow_builds_exactly_one_worktree(tmp_path, monkeypatch):
    """One review, one checkout."""
    tape = []
    review_file = _review_file(tmp_path, "pr")
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file, trail=MagicMock(),
    )

    assert tape.count("setup_worktree") == 1, "built more than one worktree for one review"


def test_the_stale_check_and_the_auto_recover_check_are_exclusive(
    tmp_path, monkeypatch,
):
    """Pipeline state decides which of the two freshness questions is asked.

    A resumed run is not stale — it is unfinished — so asking both would prompt
    twice about the same review. The two live on opposite sides of one `if`,
    which nothing else states.
    """
    review_file = _review_file(tmp_path, "pr")
    resumed, fresh = [], []

    for tape, has_state in ((resumed, True), (fresh, False)):
        rf = _review_file(tmp_path, "pr")
        state = rf.parent / "pipeline.json"
        state.write_text("{}") if has_state else state.unlink(missing_ok=True)
        _trace_common(monkeypatch, tape)
        _stub_pr_edges(monkeypatch, tmp_path, tape)
        monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)

        review_run.run_pr_review(
            make_ctx(target_dir=tmp_path / "t"), _flags(), rf, trail=MagicMock(),
        )

    assert "should_auto_recover" in resumed and "check_stale" not in resumed
    assert "check_stale" in fresh and "should_auto_recover" not in fresh


# ── the shared spine ────────────────────────────────────────────────────────


def test_both_flows_run_the_same_ordered_spine(tmp_path, monkeypatch):
    """Whatever else differs, these steps happen, in this order, on both paths."""
    review_file = _review_file(tmp_path, "self")
    self_tape, pr_tape = [], []

    _trace_common(monkeypatch, self_tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: self_tape.append("preflight"))
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    review_run.run_self_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), review_file.parent,
        str(tmp_path), recover_head_sha="", trail=MagicMock(),
    )

    pr_file = _review_file(tmp_path, "pr")
    _trace_common(monkeypatch, pr_tape)
    _stub_pr_edges(monkeypatch, tmp_path, pr_tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded",
                        lambda *a, **kw: pr_tape.append("preflight"))
    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(), pr_file, trail=MagicMock(),
    )

    spine = ["preflight", "orchestrate", "print_summary", "domain_write"]
    assert [s for s in self_tape if s in spine] == spine
    assert [s for s in pr_tape if s in spine] == spine


@pytest.mark.parametrize("flow", ["self", "pr"])
def test_a_failed_orchestration_stops_before_the_domain_is_written(
    tmp_path, monkeypatch, flow,
):
    """A failed pipeline exits, and nothing downstream of it runs.

    The failure path matters as much as the success one: a flow that recorded
    the domain above the return-code check would report a review that never
    finished. Asserted on both flows because they reach the same tail by
    different routes.
    """
    tape = []
    review_file = _review_file(tmp_path, flow)
    _trace_common(monkeypatch, tape, returncode=1)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(review_worktree, "cleanup_worktree", lambda *a, **kw: None)

    with pytest.raises(SystemExit):
        if flow == "self":
            review_run.run_self_review(
                make_ctx(target_dir=tmp_path / "t"), _flags(), review_file.parent,
                str(tmp_path), recover_head_sha="", trail=MagicMock(),
            )
        else:
            _stub_pr_edges(monkeypatch, tmp_path, tape)
            monkeypatch.setattr(review_preflight, "refuse_if_superseded",
                                lambda *a, **kw: None)
            review_run.run_pr_review(
                make_ctx(target_dir=tmp_path / "t"), _flags(), review_file,
                trail=MagicMock(),
            )

    assert "domain_write" not in tape, "a failed review is not a review to record"


# ── which `force` each gate reads ───────────────────────────────────────────


@pytest.mark.parametrize("unattended", [{"no_post": True}, {"auto_post": True}])
def test_an_unattended_pr_review_is_still_refused_when_superseded(
    tmp_path, monkeypatch, unattended,
):
    """`--post` and `--no-post` skip the prompts; they do not skip the refusal.

    The PR flow keeps two different notions of "force". One absorbs the
    unattended flags, because nobody is present to answer a confirmation. The
    supersession refusal reads the raw `--force` instead, and the distinction
    matters most here: an unattended run is the one with no operator to notice
    that the findings describe deleted code before they are posted to the PR.

    Both are booleans named force, three lines apart, and passing the wrong one
    is invisible in review — so it is asserted rather than described.
    """
    tape = []
    review_file = _review_file(tmp_path, "pr")
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)

    seen = {}
    monkeypatch.setattr(
        review_preflight, "refuse_if_superseded",
        lambda *a, **kw: seen.update(override=kw["override"]),
    )

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(force=False, **unattended),
        review_file, trail=MagicMock(),
    )

    assert seen["override"] is False, (
        "an unattended run is not an overridden one — the refusal reads --force"
    )


def test_the_freshness_prompts_are_skipped_when_nobody_can_answer_them(
    tmp_path, monkeypatch,
):
    """The converse, so the test above cannot be satisfied by ignoring both flags.

    `check_pending_review` prompts, so an unattended run has to be told not to
    ask. This is the reader that *should* see the absorbed value.
    """
    tape = []
    review_file = _review_file(tmp_path, "pr")
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)

    seen = {}
    monkeypatch.setattr(
        review_preflight, "check_pending_review",
        lambda repo, pr, force: seen.update(force=force),
    )

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(force=False, no_post=True),
        review_file, trail=MagicMock(),
    )

    assert seen["force"] is True, "--no-post means nobody is here to confirm"


# ── what the operator is asked, and when ────────────────────────────────────


@pytest.mark.parametrize("gate,stub", [
    ("check_stale_review", lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0))),
    ("check_pending_review", lambda *a, **kw: (_ for _ in ()).throw(SystemExit(0))),
    ("refuse_if_superseded", lambda *a, **kw: (_ for _ in ()).throw(SystemExit(3))),
])
def test_the_issue_prompt_comes_after_every_gate_that_can_abort(
    tmp_path, monkeypatch, gate, stub,
):
    """Nothing is asked of the operator until the run is certain to happen.

    Three gates below the issue lookup can end the run: a stale review, a
    pending one, and a superseded branch. Two of them do it by prompting and
    exiting on the answer. Asking for an issue link above them means a user can
    type one, hit Enter, and be told the review is not going to run — and the
    link they typed is gone.

    Parametrised over all three because the prompt only has to be above one of
    them to waste the answer, and a fix aimed at the supersession case alone
    would leave the other two.
    """
    review_file = _review_file(tmp_path, "pr")
    tape = []
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)

    monkeypatch.setattr(review_preflight, gate, stub)
    monkeypatch.setattr(review_issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(
        review_run.prompt, "ask",
        MagicMock(side_effect=AssertionError(f"asked for an issue link above {gate}")),
    )

    with pytest.raises(SystemExit):
        review_run.run_pr_review(
            make_ctx(target_dir=tmp_path / "t"),
            _flags(no_post=False, auto_post=False), review_file, trail=MagicMock(),
        )


def test_an_attended_pr_review_still_asks_for_an_issue_link(tmp_path, monkeypatch):
    """The prompt moved; it did not go away.

    Without this, the test above is satisfied by never asking at all.
    """
    review_file = _review_file(tmp_path, "pr")
    tape = []
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review_issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))
    asked = MagicMock(return_value="ENG-1")
    monkeypatch.setattr(review_run.prompt, "ask", asked)

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"),
        _flags(no_post=False, auto_post=False), review_file, trail=MagicMock(),
    )

    assert asked.call_count == 1, "an attended review with no issue still asks"


def test_a_review_that_found_its_issue_context_does_not_ask_for_a_link(
    tmp_path, monkeypatch,
):
    """Detected context answers the question the prompt would ask.

    The tracker resolved the issue from the branch or the PR body, so the
    reviewer already has what the link would have supplied. Asking anyway is
    asking for something that is not needed and will not be used.
    """
    review_file = _review_file(tmp_path, "pr")
    tape = []
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(
        review_issue, "fetch_issue_context",
        lambda *a, **kw: SimpleNamespace(link="", context="ENG-1: do the thing"),
    )
    monkeypatch.setattr(
        review_run.prompt, "ask",
        MagicMock(side_effect=AssertionError("asked for a link it already had context for")),
    )

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"),
        _flags(no_post=False, auto_post=False), review_file, trail=MagicMock(),
    )


@pytest.mark.parametrize("unattended", [{"no_post": True}, {"auto_post": True}])
def test_an_unattended_pr_review_is_never_prompted(tmp_path, monkeypatch, unattended):
    """--post and --no-post mean nobody is at the keyboard to answer."""
    review_file = _review_file(tmp_path, "pr")
    tape = []
    _trace_common(monkeypatch, tape)
    _stub_pr_edges(monkeypatch, tmp_path, tape)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review_issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(
        review_run.prompt, "ask",
        MagicMock(side_effect=AssertionError("prompted an unattended run")),
    )

    review_run.run_pr_review(
        make_ctx(target_dir=tmp_path / "t"), _flags(**unattended),
        review_file, trail=MagicMock(),
    )
