"""End-to-end coverage of both review entry paths, ahead of merging them.

`review_flow_characterisation_test.py` pins the *order* of steps within a
single frame, stubbing the frame below it every time. That leaves the
composition untested: `_run_review` stubs `_run_review_pr`, which stubs
`_run_review_body`, so no test walks the three PR frames together and nothing
fails if a step moves between them.

This module closes that hole from the other side. Each test drives a whole
entry path — `main(argv)` or the outermost flow function — stubbing only the
edges that leave the process (`gh`, `git`, the orchestrate subprocess) and
asserting what an operator would observe: which flow ran, what reached
review-orchestrate, and whether the domain was recorded.

The distinction matters for the merge. A characterisation test says "this
step happens here"; these say "this step happens at all, once, on this path".
A merge that relocates a step satisfies one and not the other.
"""

import json
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


def _written_review(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    f = directory / "review.md"
    f.write_text("## Must fix\n- **[M1]** boom\n")
    return f


# ── the real parser ──────────────────────────────────────────────────────────
#
# The two argparse tests these replace built a throwaway ArgumentParser and
# asserted that argparse works. Nothing pointed at claude-review's own parser,
# so every flag below could have been renamed or dropped with the suite green.


@pytest.mark.parametrize("argv,expect_self", [
    (["--self"], True),
    (["--self", "--fix"], True),
    (["42"], False),
])
def test_main_dispatches_on_the_self_flag(cr, reviews_dir, monkeypatch, argv, expect_self):
    """Which flow runs is decided by --self, through the real parser."""
    monkeypatch.setattr(sys, "argv", ["claude-review", *argv])
    self_flow, pr_flow = MagicMock(), MagicMock()
    monkeypatch.setattr(cr, "_run_self_review", self_flow)
    monkeypatch.setattr(cr, "_run_review", pr_flow)
    monkeypatch.setattr(cr.pr_context, "classify_target", lambda c: (c, None))
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: make_ctx())
    monkeypatch.setattr(cr.run_lock, "claim_for_process", lambda *a, **kw: None)

    cr.main()

    assert bool(self_flow.call_count) is expect_self
    assert bool(pr_flow.call_count) is (not expect_self)


def test_main_parses_json_summary_as_a_flag_not_a_target(cr, reviews_dir, monkeypatch):
    """`--json-summary 42` reviews PR 42; it does not review a PR named --json-summary."""
    monkeypatch.setattr(sys, "argv", ["claude-review", "--json-summary", "42"])
    seen = {}
    monkeypatch.setattr(cr, "_run_review", lambda args, ctx: seen.update(args=args))
    monkeypatch.setattr(cr.pr_context, "classify_target",
                        lambda c: seen.setdefault("target", c) and (c, None) or (c, None))
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: make_ctx())
    monkeypatch.setattr(cr.run_lock, "claim_for_process", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "json_summary", lambda *a, **kw: "{}")

    cr.main()

    assert seen["target"] == "42"
    assert seen["args"].json_summary is True


@pytest.mark.parametrize("argv,reason", [
    (["--fix"], "--fix requires --self"),
    (["--self", "--push"], "--push requires --fix"),
    (["--self", "--recover", "--force"], "--recover and --force are mutually exclusive"),
    (["--no-post", "--post", "42"], "--no-post and --post are mutually exclusive"),
])
def test_main_refuses_contradictory_flags(cr, reviews_dir, monkeypatch, argv, reason):
    """Each rejection is a real parse through the real parser, not a unit call.

    `--no-post --post` is the interesting row: its check sits after the --self
    dispatch returns, so it only ever fires on the PR path. The merge must not
    quietly move that validation somewhere it stops running.
    """
    monkeypatch.setattr(sys, "argv", ["claude-review", *argv])
    monkeypatch.setattr(cr, "_run_self_review", MagicMock())
    monkeypatch.setattr(cr, "_run_review", MagicMock())
    monkeypatch.setattr(cr.pr_context, "classify_target", lambda c: (c, None))
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: make_ctx())
    monkeypatch.setattr(cr.run_lock, "claim_for_process", lambda *a, **kw: None)

    with pytest.raises(SystemExit) as excinfo:
        cr.main()

    assert excinfo.value.code == 1, reason


# ── the PR path, composed ────────────────────────────────────────────────────


def _stub_pr_edges(cr, monkeypatch, tmp_path, tape, review_file, *, returncode=0):
    """Stub only what leaves the process, so all three PR frames really run.

    The orchestrate stub writes *review_file*, because that is what the real
    subprocess does and the flow checks for it afterwards. Leaving it out made
    the flow fail for the right reason at the wrong time: `_resolve_prior_review`
    archives the existing review before the run, so a stub that produces nothing
    reaches the "produced no review file" guard rather than the assertion.

    It also records only the orchestrate spawn. `subprocess` is a module, not a
    per-caller import, so replacing `run` on it intercepts every spawn in the
    process — the version lookup included. A tape that counted all of them would
    be asserting on an unrelated implementation detail.
    """

    def _orchestrate(argv, *a, **kw):
        if not str(argv[0]).endswith("/review-orchestrate"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        tape.append("orchestrate")
        if returncode == 0:
            review_file.parent.mkdir(parents=True, exist_ok=True)
            review_file.write_text("## Must fix\n- **[M1]** boom\n")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(cr.review_worktree, "find_repo_root", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(cr.gh_client, "pr_view",
                        lambda *a, **kw: {"headRefName": "feat/x", "body": ""})
    monkeypatch.setattr(cr.review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(cr.review_issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(cr.review_preflight, "check_stale_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_preflight, "check_pending_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(cr.review_worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(tmp_path), is_fallback=False))
    monkeypatch.setattr(cr.review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(cr.review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(cr.subprocess, "run", _orchestrate)
    monkeypatch.setattr(cr, "_display_review", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "_print_summary", lambda *a, **kw: tape.append("print_summary"))
    monkeypatch.setattr(cr, "_update_pr_state", lambda *a, **kw: tape.append("domain_write"))
    monkeypatch.setattr(cr.Trail, "start", lambda **kw: MagicMock())


def _pr_args(**overrides):
    base = dict(
        no_post=True, post=False, submit=False, issue="", max_parallel=1,
        force=False, disprove=None, max_cost=None, model=None, repo_dir="",
        effort=None, max_groups=None, generated=False, recover=False,
        debug=False, push=False, fix=False, no_holistic=False, no_scout=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_the_pr_path_runs_its_whole_spine_in_one_go(cr, tmp_path, monkeypatch):
    """`_run_review` through `_run_review_body` with nothing in between stubbed.

    The characterisation suite pins each PR frame with the next one replaced,
    so a step that migrated from the body up into `_run_review_pr` — or fell
    out entirely — would keep every one of those tests green. This is the test
    that does not.
    """
    tape = []
    review_file = _written_review(tmp_path / "reviews" / "widget-42")
    _stub_pr_edges(cr, monkeypatch, tmp_path, tape, review_file)
    monkeypatch.setattr(cr, "review_file_path", lambda *a, **kw: review_file)

    cr._run_review(_pr_args(), make_ctx(target_dir=tmp_path / "t"))

    assert tape == ["orchestrate", "print_summary", "domain_write"], (
        "the PR path orchestrates, reports, then records — composed, not per-frame"
    )


def test_a_failed_orchestration_is_not_recorded_on_the_pr_path(
    cr, tmp_path, monkeypatch,
):
    """The PR twin of the self-path failure test.

    The self flow writes the domain inside its body, below the return-code
    check. The PR flow writes it from the caller, *after* the body — so on the
    PR path the check and the write sit in different frames, and only a
    composed test can show that a failed run still records nothing. The merge
    picks one frame for this write; this test is what makes the choice safe.
    """
    tape = []
    review_file = _written_review(tmp_path / "reviews" / "widget-42")
    _stub_pr_edges(cr, monkeypatch, tmp_path, tape, review_file, returncode=1)
    monkeypatch.setattr(cr, "review_file_path", lambda *a, **kw: review_file)

    with pytest.raises(SystemExit):
        cr._run_review(_pr_args(), make_ctx(target_dir=tmp_path / "t"))

    assert "domain_write" not in tape, "a failed review is not a review to record"


def test_the_pr_path_records_the_domain_even_when_the_operator_declines_to_post(
    cr, tmp_path, monkeypatch,
):
    """Declining to post is not declining to record.

    The interactive tail returns rather than exiting precisely so the domain
    write below it still runs; otherwise `pr status` reports "not checked:
    review" for a review that did run. Driven through the composed flow so the
    return lands where the write can see it.
    """
    tape = []
    review_file = _written_review(tmp_path / "reviews" / "widget-42")
    _stub_pr_edges(cr, monkeypatch, tmp_path, tape, review_file)
    monkeypatch.setattr(cr, "review_file_path", lambda *a, **kw: review_file)
    monkeypatch.setattr(cr.prompt, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(cr, "_post_review",
                        lambda *a, **kw: tape.append("posted"))

    cr._run_review(_pr_args(no_post=False), make_ctx(target_dir=tmp_path / "t"))

    assert "posted" not in tape, "declining the prompt must not post"
    assert "domain_write" in tape, "but the review still happened and is recorded"


# ── what reaches review-orchestrate ──────────────────────────────────────────
#
# Seven existing tests each poke one flag. None asserts the argv as a whole, so
# a flag dropped from the middle of the list is invisible. These two goldens
# cover the set, and they are what the in-process conversion will later
# re-target at a call's kwargs.


def _orchestrate_argv(cr, tmp_path, **overrides):
    kwargs = dict(
        pr_number="42", repo="acme/widget", review_file=tmp_path / "review.md",
        wt_path="/wt", session_log="/log", prior_review_path="", issue_link="",
        issue_context="", max_parallel=1, max_cost=None, model=None,
        target_dir=tmp_path / "state",
    )
    kwargs.update(overrides)
    return cr._build_orchestrate_args(**kwargs)


def test_the_pr_review_argv_is_exactly_this(cr, tmp_path):
    """A golden for the PR shape: no --mode, no --fix, no --post."""
    argv = _orchestrate_argv(cr, tmp_path)

    assert argv[0].endswith("/review-orchestrate")
    assert argv[1:] == [
        "--repo", "acme/widget",
        "--review-file", str(tmp_path / "review.md"),
        "--repo-dir", "/wt",
        "--target-dir", str(tmp_path / "state"),
        "--session-log", "/log",
        "--pr", "42",
        "--max-parallel", "1",
        "--generator-version", cr._generator_version(),
    ]


def test_the_self_review_argv_carries_mode_fix_and_post(cr, tmp_path):
    """A golden for the self shape, including the flags only self review sets.

    `--post` is not "publish this review": it tells the orchestrate process
    that its fix pass may push, because the publishing gate is process-wide.
    That is the single most consequential flag in this list and the one an
    in-process call is most likely to get wrong.
    """
    argv = _orchestrate_argv(
        cr, tmp_path, mode="self", fix_pass=True, post=True, model="sonnet",
        max_cost=12.5, effort="high", max_groups=3, generated=True,
        recover_sha="abc1234",
    )

    assert argv[1:] == [
        "--repo", "acme/widget",
        "--review-file", str(tmp_path / "review.md"),
        "--repo-dir", "/wt",
        "--target-dir", str(tmp_path / "state"),
        "--session-log", "/log",
        "--pr", "42",
        "--mode", "self",
        "--max-parallel", "1",
        "--generator-version", cr._generator_version(),
        "--fix",
        "--post",
        "--max-cost", "12.5",
        "--model", "sonnet",
        "--effort", "high",
        "--max-groups", "3",
        "--generated",
        "--recover-sha", "abc1234",
    ]


def test_a_self_review_without_a_pr_omits_the_pr_flag(cr, tmp_path):
    """A branch with no PR still reviews; it just has no number to pass."""
    argv = _orchestrate_argv(cr, tmp_path, pr_number="", mode="self")

    assert "--pr" not in argv
    assert argv[argv.index("--mode") + 1] == "self"
