"""Tests for pr-describe."""

import sys
from pathlib import Path
from unittest import mock

from conftest import assert_no_worktree_exit, load_script, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

pr_describe_cli = load_script("pr_describe_cli", BIN_DIR / "pr-describe")

import pr_domains  # noqa: E402
import pr_state  # noqa: E402


def _ctx(worktree, head_sha="aaaa111", pr_number=7):
    """A context rooted in *worktree*, which these tests read and write for real."""
    return make_ctx(branch="isaac/feat/x", pr_number=pr_number,
                    worktree_root=worktree, head_sha=head_sha,
                    target_dir=worktree / "target")


def _wrapped(body: str) -> str:
    """A model answer in the form run_describe accepts — markers around the body."""
    return f"{pr_describe_cli._DESCRIBE_BEGIN}\n{body}\n{pr_describe_cli._DESCRIBE_END}"


def _run(ctx, *, body="", ai=(_wrapped("NEW BODY"), 0), **kw):
    """Run run_describe with git, gh, and the AI backend stubbed out."""
    edits = []
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body",
                           return_value=("title", body)), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=ai) as prompt, \
         mock.patch.object(pr_describe_cli, "_apply_body",
                           side_effect=lambda r, n, b: edits.append(b) or True):
        rc = pr_describe_cli.run_describe(ctx, **kw)
    return rc, edits, prompt


# ── template discovery ──────────────────────────────────────────────────────


def test_checked_in_template_wins_over_the_fallback(worktree):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text("## Why\n")
    template, rel = pr_describe_cli._load_template(worktree)
    assert template == "## Why\n"
    assert rel == ".github/pull_request_template.md"


def test_first_recognised_path_wins(worktree):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text("first")
    (worktree / "PULL_REQUEST_TEMPLATE.md").write_text("last")
    template, rel = pr_describe_cli._load_template(worktree)
    assert template == "first"
    assert rel == ".github/pull_request_template.md"


def test_repo_without_a_template_falls_back(worktree):
    template, rel = pr_describe_cli._load_template(worktree)
    assert "## Summary" in template
    assert rel == ""


# ── talking to gh ───────────────────────────────────────────────────────────


def test_fetching_the_body_reads_the_fields_it_asked_for():
    answer = mock.MagicMock(ok=True, stdout='{"title": "t", "body": "b"}')
    with mock.patch.object(pr_describe_cli.gh_client, "run",
                           return_value=answer) as run:
        assert pr_describe_cli._fetch_pr_body("owner/repo", 7) == ("t", "b")
    assert run.call_args[0][:3] == ("pr", "view", "7")


def test_fetching_the_body_gives_up_when_gh_cannot_answer():
    answer = mock.MagicMock(ok=False, detail="no such pull request")
    with mock.patch.object(pr_describe_cli.gh_client, "run", return_value=answer):
        assert pr_describe_cli._fetch_pr_body("owner/repo", 7) is None


def test_applying_the_body_sends_it_on_stdin():
    """`--body-file -` reads the body from stdin, so gh must be given one."""
    with mock.patch.object(pr_describe_cli.gh_client, "run",
                           return_value=mock.MagicMock(ok=True)) as run:
        assert pr_describe_cli._apply_body("owner/repo", 7, "NEW BODY") is True
    assert run.call_args.kwargs["input_text"] == "NEW BODY"
    assert run.call_args[0][-2:] == ("--body-file", "-")


# ── commit awareness ────────────────────────────────────────────────────────


def test_unchanged_head_skips_the_ai_call(worktree):
    state = pr_state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr_state.apply(state, pr_domains.DescribeSummary(head_sha="aaaa111"))
    pr_state.save_state(worktree / "target", state)

    rc, edits, prompt = _run(_ctx(worktree))
    assert rc == 0
    assert not prompt.called
    assert edits == []


def test_moved_head_earns_a_fresh_pass(worktree):
    state = pr_state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr_state.apply(state, pr_domains.DescribeSummary(head_sha="old0000"))
    pr_state.save_state(worktree / "target", state)

    rc, edits, prompt = _run(_ctx(worktree))
    assert rc == 0
    assert prompt.called
    assert edits == ["NEW BODY"]


def test_force_overrides_the_head_check(worktree):
    state = pr_state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr_state.apply(state, pr_domains.DescribeSummary(head_sha="aaaa111"))
    pr_state.save_state(worktree / "target", state)

    rc, edits, prompt = _run(_ctx(worktree), force=True)
    assert rc == 0
    assert prompt.called


def test_a_branch_never_described_runs(worktree):
    rc, edits, prompt = _run(_ctx(worktree))
    assert rc == 0
    assert prompt.called


def test_no_pr_is_a_no_op(worktree):
    rc, edits, prompt = _run(_ctx(worktree, pr_number=None))
    assert rc == 0
    assert not prompt.called


# ── revision outcomes ───────────────────────────────────────────────────────


def test_conforming_body_is_left_alone_but_still_recorded(worktree):
    rc, edits, _ = _run(_ctx(worktree), ai=(pr_describe_cli._NO_CHANGE, 0))
    assert rc == 0
    assert edits == []
    state = pr_state.load_state(worktree / "target")
    # The SHA is recorded either way — a body confirmed current at this HEAD
    # does not need confirming twice.
    assert state.describe.head_sha == "aaaa111"
    assert state.describe.changed is False


def test_revision_is_applied_and_recorded(worktree):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text("## Why\n")
    rc, edits, _ = _run(_ctx(worktree), ai=(_wrapped("## Why\n\nBecause."), 0))
    assert rc == 0
    assert edits == ["## Why\n\nBecause."]
    state = pr_state.load_state(worktree / "target")
    assert state.describe.changed is True
    assert state.describe.template_path == ".github/pull_request_template.md"


def test_dry_run_prints_without_applying_or_recording(worktree, capsys):
    rc, edits, _ = _run(_ctx(worktree), dry_run=True)
    assert rc == 0
    assert edits == []
    assert "NEW BODY" in capsys.readouterr().out
    assert pr_state.load_state(worktree / "target") is None


def test_blank_ai_answer_would_wipe_the_body_so_it_fails(worktree):
    rc, edits, _ = _run(_ctx(worktree), ai=("   ", 0))
    assert rc == 1
    assert edits == []
    assert pr_state.load_state(worktree / "target") is None


def test_an_unmarked_answer_is_never_posted(worktree):
    """A preamble-and-fences reply must not reach `gh pr edit` verbatim.

    The prompt asks for markers, but nothing stops a model from replying
    conversationally; without extraction that text became the PR body.
    """
    chatty = "Sure, here's the revised description:\n```markdown\n## Summary\n```"
    rc, edits, prompt = _run(_ctx(worktree), ai=(chatty, 0))
    assert rc == 1
    assert edits == []
    # Unusable, so it burns the one retry the thrash guard allows.
    assert prompt.call_count == 2
    assert pr_state.load_state(worktree / "target") is None


def test_only_the_marked_span_is_posted(worktree):
    """Text outside the markers is commentary, not description."""
    answer = (
        "Here you go!\n"
        f"{pr_describe_cli._DESCRIBE_BEGIN}\n## Why\n\nBecause.\n"
        f"{pr_describe_cli._DESCRIBE_END}\nHope that helps."
    )
    rc, edits, _ = _run(_ctx(worktree), ai=(answer, 0))
    assert rc == 0
    assert edits == ["## Why\n\nBecause."]


def test_failed_ai_call_is_not_recorded(worktree):
    rc, edits, _ = _run(_ctx(worktree), ai=("", 1))
    assert rc == 1
    assert edits == []
    assert pr_state.load_state(worktree / "target") is None


def test_unreachable_pr_stops_before_the_ai_call(worktree):
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=None), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt") as prompt:
        rc = pr_describe_cli.run_describe(_ctx(worktree))
    assert rc == 1
    assert not prompt.called


def test_a_rejected_edit_is_not_recorded(worktree):
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=(_wrapped("B"), 0)), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=False):
        rc = pr_describe_cli.run_describe(_ctx(worktree))
    assert rc == 1
    assert pr_state.load_state(worktree / "target") is None


# ── thrash guard ────────────────────────────────────────────────────────────


def test_a_blank_first_answer_earns_one_retry(worktree):
    answers = [("", 0), (_wrapped("SECOND"), 0)]
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=True), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           side_effect=answers) as prompt:
        rc = pr_describe_cli.run_describe(_ctx(worktree))
    assert rc == 0
    assert prompt.call_count == 2


# ── prompt content ──────────────────────────────────────────────────────────


def test_prompt_carries_the_template_and_the_branch_contents(worktree):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text("## Why\n")
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body",
                           return_value=("feat: x", "old body")), \
         mock.patch.object(pr_describe_cli, "_git", return_value="deadbee fix: y"), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=True), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=(_wrapped("B"), 0)) as prompt:
        pr_describe_cli.run_describe(_ctx(worktree))
    text = prompt.call_args[0][0]
    assert "## Why" in text
    assert "feat: x" in text
    assert "old body" in text
    assert "deadbee fix: y" in text
    assert "checked-in PR template" in text


def test_prompt_says_so_when_the_repo_ships_no_template(worktree):
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=True), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=(_wrapped("B"), 0)) as prompt:
        pr_describe_cli.run_describe(_ctx(worktree))
    assert "this repo ships none" in prompt.call_args[0][0]


# ── worktree_root guards ──────────────────────────────────────────────────


def test_run_describe_without_a_worktree_exits_with_guidance(capsys):
    ctx = make_ctx(branch="isaac/feat/x", pr_number=7,
                   worktree_root=None, head_sha="aaaa111")
    assert_no_worktree_exit(capsys, "isaac/feat/x",
                            pr_describe_cli.run_describe, ctx)


def test_no_pr_reports_before_demanding_a_worktree(capsys):
    """The trail directory degrades, so the no-PR path is not blocked by it."""
    ctx = make_ctx(branch="isaac/feat/x", pr_number=None,
                   worktree_root=None, head_sha="aaaa111")
    assert pr_describe_cli.run_describe(ctx) == 0
    assert "nothing to describe" in capsys.readouterr().err
