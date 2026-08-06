"""Tests for pr-describe."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless pr-describe script via importlib
_path = str(BIN_DIR / "pr-describe")
_loader = importlib.machinery.SourceFileLoader("pr_describe_cli", _path)
_spec = importlib.util.spec_from_loader("pr_describe_cli", _loader, origin=_path)
pr_describe_cli = importlib.util.module_from_spec(_spec)
pr_describe_cli.__file__ = _path
_spec.loader.exec_module(pr_describe_cli)

import pr_context  # noqa: E402
import pr_state  # noqa: E402


def _ctx(tmp_path, head_sha="aaaa111", pr_number=7):
    return pr_context.ResolvedContext(
        repo="owner/repo",
        branch="isaac/feat/x",
        pr_number=pr_number,
        worktree_root=tmp_path,
        head_sha=head_sha,
    )


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


def test_checked_in_template_wins_over_the_fallback(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "pull_request_template.md").write_text("## Why\n")
    template, rel = pr_describe_cli._load_template(tmp_path)
    assert template == "## Why\n"
    assert rel == ".github/pull_request_template.md"


def test_first_recognised_path_wins(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "pull_request_template.md").write_text("first")
    (tmp_path / "PULL_REQUEST_TEMPLATE.md").write_text("last")
    template, rel = pr_describe_cli._load_template(tmp_path)
    assert template == "first"
    assert rel == ".github/pull_request_template.md"


def test_repo_without_a_template_falls_back(tmp_path):
    template, rel = pr_describe_cli._load_template(tmp_path)
    assert "## Summary" in template
    assert rel == ""


# ── commit awareness ────────────────────────────────────────────────────────


def test_unchanged_head_skips_the_ai_call(tmp_path):
    state = pr_state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(tmp_path))
    pr_state.update_describe(state, pr_state.DescribeSummary(head_sha="aaaa111"))
    pr_state.save_state(tmp_path, state)

    rc, edits, prompt = _run(_ctx(tmp_path))
    assert rc == 0
    assert not prompt.called
    assert edits == []


def test_moved_head_earns_a_fresh_pass(tmp_path):
    state = pr_state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(tmp_path))
    pr_state.update_describe(state, pr_state.DescribeSummary(head_sha="old0000"))
    pr_state.save_state(tmp_path, state)

    rc, edits, prompt = _run(_ctx(tmp_path))
    assert rc == 0
    assert prompt.called
    assert edits == ["NEW BODY"]


def test_force_overrides_the_head_check(tmp_path):
    state = pr_state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(tmp_path))
    pr_state.update_describe(state, pr_state.DescribeSummary(head_sha="aaaa111"))
    pr_state.save_state(tmp_path, state)

    rc, edits, prompt = _run(_ctx(tmp_path), force=True)
    assert rc == 0
    assert prompt.called


def test_a_branch_never_described_runs(tmp_path):
    rc, edits, prompt = _run(_ctx(tmp_path))
    assert rc == 0
    assert prompt.called


def test_no_pr_is_a_no_op(tmp_path):
    rc, edits, prompt = _run(_ctx(tmp_path, pr_number=None))
    assert rc == 0
    assert not prompt.called


# ── revision outcomes ───────────────────────────────────────────────────────


def test_conforming_body_is_left_alone_but_still_recorded(tmp_path):
    rc, edits, _ = _run(_ctx(tmp_path), ai=(pr_describe_cli._NO_CHANGE, 0))
    assert rc == 0
    assert edits == []
    state = pr_state.load_state(tmp_path)
    # The SHA is recorded either way — a body confirmed current at this HEAD
    # does not need confirming twice.
    assert state.describe.head_sha == "aaaa111"
    assert state.describe.changed is False


def test_revision_is_applied_and_recorded(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "pull_request_template.md").write_text("## Why\n")
    rc, edits, _ = _run(_ctx(tmp_path), ai=(_wrapped("## Why\n\nBecause."), 0))
    assert rc == 0
    assert edits == ["## Why\n\nBecause."]
    state = pr_state.load_state(tmp_path)
    assert state.describe.changed is True
    assert state.describe.template_path == ".github/pull_request_template.md"


def test_dry_run_prints_without_applying_or_recording(tmp_path, capsys):
    rc, edits, _ = _run(_ctx(tmp_path), dry_run=True)
    assert rc == 0
    assert edits == []
    assert "NEW BODY" in capsys.readouterr().out
    assert pr_state.load_state(tmp_path) is None


def test_blank_ai_answer_would_wipe_the_body_so_it_fails(tmp_path):
    rc, edits, _ = _run(_ctx(tmp_path), ai=("   ", 0))
    assert rc == 1
    assert edits == []
    assert pr_state.load_state(tmp_path) is None


def test_an_unmarked_answer_is_never_posted(tmp_path):
    """A preamble-and-fences reply must not reach `gh pr edit` verbatim.

    The prompt asks for markers, but nothing stops a model from replying
    conversationally; without extraction that text became the PR body.
    """
    chatty = "Sure, here's the revised description:\n```markdown\n## Summary\n```"
    rc, edits, prompt = _run(_ctx(tmp_path), ai=(chatty, 0))
    assert rc == 1
    assert edits == []
    # Unusable, so it burns the one retry the thrash guard allows.
    assert prompt.call_count == 2
    assert pr_state.load_state(tmp_path) is None


def test_only_the_marked_span_is_posted(tmp_path):
    """Text outside the markers is commentary, not description."""
    answer = (
        "Here you go!\n"
        f"{pr_describe_cli._DESCRIBE_BEGIN}\n## Why\n\nBecause.\n"
        f"{pr_describe_cli._DESCRIBE_END}\nHope that helps."
    )
    rc, edits, _ = _run(_ctx(tmp_path), ai=(answer, 0))
    assert rc == 0
    assert edits == ["## Why\n\nBecause."]


def test_failed_ai_call_is_not_recorded(tmp_path):
    rc, edits, _ = _run(_ctx(tmp_path), ai=("", 1))
    assert rc == 1
    assert edits == []
    assert pr_state.load_state(tmp_path) is None


def test_unreachable_pr_stops_before_the_ai_call(tmp_path):
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=None), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt") as prompt:
        rc = pr_describe_cli.run_describe(_ctx(tmp_path))
    assert rc == 1
    assert not prompt.called


def test_a_rejected_edit_is_not_recorded(tmp_path):
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=(_wrapped("B"), 0)), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=False):
        rc = pr_describe_cli.run_describe(_ctx(tmp_path))
    assert rc == 1
    assert pr_state.load_state(tmp_path) is None


# ── thrash guard ────────────────────────────────────────────────────────────


def test_a_blank_first_answer_earns_one_retry(tmp_path):
    answers = [("", 0), (_wrapped("SECOND"), 0)]
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=True), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           side_effect=answers) as prompt:
        rc = pr_describe_cli.run_describe(_ctx(tmp_path))
    assert rc == 0
    assert prompt.call_count == 2


# ── prompt content ──────────────────────────────────────────────────────────


def test_prompt_carries_the_template_and_the_branch_contents(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "pull_request_template.md").write_text("## Why\n")
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body",
                           return_value=("feat: x", "old body")), \
         mock.patch.object(pr_describe_cli, "_git", return_value="deadbee fix: y"), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=True), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=(_wrapped("B"), 0)) as prompt:
        pr_describe_cli.run_describe(_ctx(tmp_path))
    text = prompt.call_args[0][0]
    assert "## Why" in text
    assert "feat: x" in text
    assert "old body" in text
    assert "deadbee fix: y" in text
    assert "checked-in PR template" in text


def test_prompt_says_so_when_the_repo_ships_no_template(tmp_path):
    with mock.patch.object(pr_describe_cli, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr_describe_cli, "_git", return_value=""), \
         mock.patch.object(pr_describe_cli, "_apply_body", return_value=True), \
         mock.patch.object(pr_describe_cli.ai_backend, "prompt",
                           return_value=(_wrapped("B"), 0)) as prompt:
        pr_describe_cli.run_describe(_ctx(tmp_path))
    assert "this repo ships none" in prompt.call_args[0][0]
