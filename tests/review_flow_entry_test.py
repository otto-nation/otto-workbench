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


from cli import claude_review  # noqa: E402


@pytest.fixture
def cr():
    """The entry point under test.

    An import, not a `SourceFileLoader` shim: the binary is a shim over this
    module now, and importing it gives every caller the one module object the
    interpreter already holds.
    """
    return claude_review


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
    self_flow, pr_flow = MagicMock(), MagicMock()
    monkeypatch.setattr(cr, "_run_self_review", self_flow)
    monkeypatch.setattr(cr, "_run_review", pr_flow)
    monkeypatch.setattr(cr.pr_context, "classify_target", lambda c: (c, None))
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: make_ctx())
    monkeypatch.setattr(cr.run_lock, "claim_for_process", lambda *a, **kw: None)

    cr.main(argv)

    assert bool(self_flow.call_count) is expect_self
    assert bool(pr_flow.call_count) is (not expect_self)


def test_main_parses_json_summary_as_a_flag_not_a_target(cr, reviews_dir, monkeypatch):
    """`--json-summary 42` reviews PR 42; it does not review a PR named --json-summary."""
    seen = {}

    def _capture(args, ctx, generator_version):
        seen.update(args=args)
        return review_run.ReviewOutcome("owner/repo", "42", Path("/dev/null"))

    def _classify(c):
        seen.setdefault("target", c)
        return (c, None)

    monkeypatch.setattr(cr, "_run_review", _capture)
    monkeypatch.setattr(cr.pr_context, "classify_target", _classify)
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: make_ctx())
    monkeypatch.setattr(cr.run_lock, "claim_for_process", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "json_summary", lambda *a, **kw: "{}")

    cr.main(["--json-summary", "42"])

    assert seen["target"] == "42"
    assert seen["args"].json_summary is True


@pytest.mark.parametrize("argv,reason", [
    (["--fix"], "--fix requires --self"),
    (["--self", "--push"], "--push requires --fix"),
    (["--self", "--recover", "--force"], "--recover and --force are mutually exclusive"),
    (["--no-post", "--post", "42"], "--no-post and --post are mutually exclusive"),
    (["--self", "--no-post", "--post"], "the mutex holds on the self path too"),
])
def test_main_refuses_contradictory_flags(cr, reviews_dir, monkeypatch, argv, reason):
    """Each rejection is a real parse through the real parser, not a unit call.

    The two --no-post --post rows are the ones worth reading. The check used to
    sit below the --self dispatch, so it fired on the PR path and never on the
    self one: `--self --no-post --post` ran a review that ignored --no-post and
    still told the orchestrate process it might publish.
    """
    monkeypatch.setattr(cr, "_run_self_review", MagicMock())
    monkeypatch.setattr(cr, "_run_review", MagicMock())
    monkeypatch.setattr(cr.pr_context, "classify_target", lambda c: (c, None))
    monkeypatch.setattr(cr.pr_context, "resolve", lambda **kw: make_ctx())
    monkeypatch.setattr(cr.run_lock, "claim_for_process", lambda *a, **kw: None)

    assert cr.main(argv) == 1, reason


# ── the PR path, composed ────────────────────────────────────────────────────


def _stub_pr_edges(cr, monkeypatch, tmp_path, tape, review_file, *, returncode=0):
    """Stub only what leaves the process, so all three PR frames really run.

    The orchestrate stub writes *review_file*, because that is what the real
    subprocess does and the flow checks for it afterwards. Leaving it out made
    the flow fail for the right reason at the wrong time: `resolve_prior_review`
    archives the existing review before the run, so a stub that produces nothing
    reaches the "produced no review file" guard rather than the assertion.

    It also records only the orchestrate spawn. `subprocess` is a module, not a
    per-caller import, so replacing `run` on it intercepts every spawn in the
    process — the version lookup included. A tape that counted all of them would
    be asserting on an unrelated implementation detail.
    """

    def _orchestrate(request):
        tape.append("orchestrate")
        if returncode != 0:
            raise SystemExit(1)
        review_file.parent.mkdir(parents=True, exist_ok=True)
        review_file.write_text("## Must fix\n- **[M1]** boom\n")
        return 0

    monkeypatch.setattr(review_worktree, "find_repo_root", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(review_run.gh_client, "pr_view",
                        lambda *a, **kw: {"headRefName": "feat/x", "body": ""})
    monkeypatch.setattr(review_issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(review_issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(review_preflight, "check_stale_review", lambda *a, **kw: None)
    monkeypatch.setattr(review_preflight, "check_pending_review", lambda *a, **kw: None)
    monkeypatch.setattr(review_preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review_worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(tmp_path), is_fallback=False))
    monkeypatch.setattr(review_recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(review_worktree, "cleanup_worktree", lambda *a, **kw: None)
    monkeypatch.setattr(review_invoke, "run", _orchestrate)
    monkeypatch.setattr(review_run.review_invoke, "run", _orchestrate)
    monkeypatch.setattr(review_completion, "_display", lambda *a, **kw: None)
    monkeypatch.setattr(review_completion, "summarise",
                        lambda *a, **kw: tape.append("print_summary"))
    monkeypatch.setattr(review_completion, "record_domain",
                        lambda *a, **kw: tape.append("domain_write"))
    monkeypatch.setattr(cr.Trail, "start", lambda **kw: MagicMock())


def _pr_args(**overrides):
    base = dict(
        no_post=True, post=False, submit=False, issue="", max_parallel=1,
        force=False, disprove=None, max_cost=None, model=None, repo_dir="",
        effort=None, max_groups=None, generated=False, recover=False,
        debug=False, push=False, fix=False, no_holistic=False, no_scout=False,
        skip_user_verification=False,
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

    cr._run_review(_pr_args(), make_ctx(target_dir=tmp_path / "t"), "test 1.0")

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
        cr._run_review(_pr_args(), make_ctx(target_dir=tmp_path / "t"), "test 1.0")

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
    monkeypatch.setattr(review_publish.prompt, "confirm", lambda *a, **kw: False)
    monkeypatch.setattr(review_publish, "post",
                        lambda *a, **kw: tape.append("posted"))
    monkeypatch.setattr(review_run.prompt, "ask", lambda *a, **kw: "")

    cr._run_review(_pr_args(no_post=False), make_ctx(target_dir=tmp_path / "t"), "test 1.0")

    assert "posted" not in tape, "declining the prompt must not post"
    assert "domain_write" in tape, "but the review still happened and is recorded"
