"""Tests for pr-describe."""

import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import assert_no_worktree_exit, make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.describe  # noqa: E402
from config.workbench_config import IssueProvider  # noqa: E402
import pr.domains  # noqa: E402
import pr.state  # noqa: E402
from pr.follow_ups import FollowUp, FollowUpDomain, FollowUpSource, IssueRef  # noqa: E402
import agent.invoke
import gh.client
import agent.backend
import cli.pr_describe
import pr.context


@pytest.fixture(autouse=True)
def _fetched(monkeypatch):
    """Every run's `git fetch origin <base>` succeeds; the worktree has no origin."""
    monkeypatch.setattr(pr.describe, "_fetch_refusal", lambda wt, base: "")


def _ctx(worktree, head_sha="aaaa111", pr_number=7):
    """A context rooted in *worktree*, which these tests read and write for real."""
    return make_ctx(branch="isaac/feat/x", pr_number=pr_number,
                    worktree_root=worktree, head_sha=head_sha,
                    target_dir=worktree / "target")


def _wrapped(body: str) -> str:
    """A model answer in the form run_describe accepts — markers around the body."""
    return f"{pr.describe._DESCRIBE_BEGIN}\n{body}\n{pr.describe._DESCRIBE_END}"


def _run(ctx, *, body="", ai=(_wrapped("NEW BODY"), 0), **kw):
    """Run run_describe with git, gh, and the AI backend stubbed out."""
    edits = []
    with mock.patch.object(pr.describe, "_fetch_pr_body",
                           return_value=("title", body)), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=ai) as prompt, \
         mock.patch.object(pr.describe, "_apply_body",
                           side_effect=lambda r, n, b: edits.append(b) or True):
        rc = pr.describe.run_describe(ctx, pr.describe.DescribeOptions(**kw))
    return rc, edits, prompt


# ── cli imports ─────────────────────────────────────────────────────────────


def test_cli_pr_describe_imports_core_log_directly():
    """`main()` calls `core.log.info(...)`, so this module must import it itself.

    It otherwise only resolves because `core.publishing`, `core.run_lock`, and
    `pr.context` each transitively import `core.log`, which populates `log` as
    an attribute of the `core` package as a side effect. That's borrowed, not
    declared — if any of those three stopped, `core.log` would raise
    `AttributeError` here. Checked via the AST rather than at runtime because
    by the time this test file runs, something else in the suite has already
    imported `core.log` and the package-level attribute is set regardless of
    whether this module declares its own import.
    """
    import ast

    tree = ast.parse(Path(cli.pr_describe.__file__).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "core.log" in imported


# ── template discovery ──────────────────────────────────────────────────────


# The prompt's own two cases — a checked-in template and none — are covered
# under "prompt content" below, which predates this module having one owner.
# What is left to pin here is the path that reaches state, on a location the
# lists this replaced did not look in.


def test_the_resolved_path_is_recorded_in_state(worktree):
    """A `docs/` template, which neither of the lists this replaced looked in."""
    (worktree / "docs").mkdir()
    (worktree / "docs" / "pull_request_template.md").write_text("## Why\n")
    ctx = _ctx(worktree)
    _run(ctx)
    state = pr.state.load_state(ctx.target_dir)
    assert state.describe.template_path == "docs/pull_request_template.md"


# ── talking to gh ───────────────────────────────────────────────────────────


def test_fetching_the_body_reads_the_fields_it_asked_for():
    answer = mock.MagicMock(ok=True, stdout='{"title": "t", "body": "b"}')
    with mock.patch.object(gh.client, "run",
                           return_value=answer) as run:
        assert pr.describe._fetch_pr_body("owner/repo", 7) == ("t", "b")
    assert run.call_args[0][:3] == ("pr", "view", "7")


def test_fetching_the_body_gives_up_when_gh_cannot_answer():
    answer = mock.MagicMock(ok=False, detail="no such pull request")
    with mock.patch.object(gh.client, "run", return_value=answer):
        assert pr.describe._fetch_pr_body("owner/repo", 7) is None


def test_applying_the_body_sends_it_on_stdin(publishing_on):
    """`--body-file -` reads the body from stdin, so gh must be given one."""
    with mock.patch.object(gh.client, "run",
                           return_value=mock.MagicMock(ok=True)) as run:
        assert pr.describe._apply_body("owner/repo", 7, "NEW BODY") is True
    assert run.call_args.kwargs["input_text"] == "NEW BODY"
    assert run.call_args[0][-2:] == ("--body-file", "-")


# ── commit awareness ────────────────────────────────────────────────────────


def test_unchanged_head_skips_the_ai_call(worktree):
    state = pr.state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr.state.apply(state, pr.domains.DescribeSummary(head_sha="aaaa111", published=True))
    pr.state.save_state(worktree / "target", state)

    rc, edits, prompt = _run(_ctx(worktree))
    assert rc == 0
    assert not prompt.called
    assert edits == []


def test_moved_head_earns_a_fresh_pass(worktree):
    state = pr.state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr.state.apply(state, pr.domains.DescribeSummary(head_sha="old0000"))
    pr.state.save_state(worktree / "target", state)

    rc, edits, prompt = _run(_ctx(worktree))
    assert rc == 0
    assert prompt.called
    assert edits == ["NEW BODY"]


def test_force_overrides_the_head_check(worktree):
    state = pr.state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr.state.apply(state, pr.domains.DescribeSummary(head_sha="aaaa111"))
    pr.state.save_state(worktree / "target", state)

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
    rc, edits, _ = _run(_ctx(worktree), ai=(pr.describe._NO_CHANGE, 0))
    assert rc == 0
    assert edits == []
    state = pr.state.load_state(worktree / "target")
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
    state = pr.state.load_state(worktree / "target")
    assert state.describe.changed is True
    assert state.describe.template_path == ".github/pull_request_template.md"


def test_title_only_override_against_published_body_is_not_a_body_change(worktree):
    """A title override at a published HEAD must not mark the body as changed.

    `_write` and `run_describe` each have their own `revised`/`body` comparison
    in source; this pins `changed` to the one `_write` actually acted on, so a
    future edit that lets the two expressions diverge is caught here.
    """
    state = pr.state.new_state("owner/repo", "b", pr_number=7, head_sha="aaaa111",
                               worktree_root=str(worktree))
    pr.state.apply(state, pr.domains.DescribeSummary(head_sha="aaaa111", published=True))
    pr.state.save_state(worktree / "target", state)

    rc, edits, prompt = _run(_ctx(worktree), body="same body", title="New Title")
    assert rc == 0
    assert not prompt.called
    assert edits == []
    state = pr.state.load_state(worktree / "target")
    assert state.describe.changed is False


def test_dry_run_prints_without_applying_or_recording(worktree, capsys):
    rc, edits, _ = _run(_ctx(worktree), dry_run=True)
    assert rc == 0
    assert edits == []
    assert "NEW BODY" in capsys.readouterr().out
    assert pr.state.load_state(worktree / "target") is None


def test_blank_ai_answer_would_wipe_the_body_so_it_fails(worktree):
    rc, edits, _ = _run(_ctx(worktree), ai=("   ", 0))
    assert rc == 1
    assert edits == []
    assert pr.state.load_state(worktree / "target") is None


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
    assert pr.state.load_state(worktree / "target") is None


def test_only_the_marked_span_is_posted(worktree):
    """Text outside the markers is commentary, not description."""
    answer = (
        "Here you go!\n"
        f"{pr.describe._DESCRIBE_BEGIN}\n## Why\n\nBecause.\n"
        f"{pr.describe._DESCRIBE_END}\nHope that helps."
    )
    rc, edits, _ = _run(_ctx(worktree), ai=(answer, 0))
    assert rc == 0
    assert edits == ["## Why\n\nBecause."]


def test_failed_ai_call_is_not_recorded(worktree):
    rc, edits, _ = _run(_ctx(worktree), ai=("", 1))
    assert rc == 1
    assert edits == []
    assert pr.state.load_state(worktree / "target") is None


def test_unreachable_pr_stops_before_the_ai_call(worktree):
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=None), \
         mock.patch.object(agent.backend, "prompt") as prompt:
        rc = pr.describe.run_describe(_ctx(worktree))
    assert rc == 1
    assert not prompt.called


def test_a_rejected_edit_is_not_recorded(worktree, publishing_on):
    """A real `gh pr edit` failure, distinct from a draft: the gate is open."""
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=(_wrapped("B"), 0)), \
         mock.patch.object(pr.describe, "_apply_body", return_value=False):
        rc = pr.describe.run_describe(_ctx(worktree))
    assert rc == 1
    assert pr.state.load_state(worktree / "target") is None


# ── thrash guard ────────────────────────────────────────────────────────────


def test_a_blank_first_answer_earns_one_retry(worktree):
    answers = [("", 0), (_wrapped("SECOND"), 0)]
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(pr.describe, "_apply_body", return_value=True), \
         mock.patch.object(agent.backend, "prompt",
                           side_effect=answers) as prompt:
        rc = pr.describe.run_describe(_ctx(worktree))
    assert rc == 0
    assert prompt.call_count == 2


# ── prompt content ──────────────────────────────────────────────────────────


def test_prompt_carries_the_template_and_the_branch_contents(worktree):
    (worktree / ".github").mkdir()
    (worktree / ".github" / "pull_request_template.md").write_text("## Why\n")
    with mock.patch.object(pr.describe, "_fetch_pr_body",
                           return_value=("feat: x", "old body")), \
         mock.patch.object(pr.describe, "_git", return_value="deadbee fix: y"), \
         mock.patch.object(pr.describe, "_apply_body", return_value=True), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=(_wrapped("B"), 0)) as prompt:
        pr.describe.run_describe(_ctx(worktree))
    text = prompt.call_args[0][0]
    assert "## Why" in text
    assert "feat: x" in text
    assert "old body" in text
    assert "deadbee fix: y" in text
    assert "checked-in PR template" in text


def test_prompt_says_so_when_the_repo_ships_no_template(worktree):
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(pr.describe, "_apply_body", return_value=True), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=(_wrapped("B"), 0)) as prompt:
        pr.describe.run_describe(_ctx(worktree))
    assert "this repo ships none" in prompt.call_args[0][0]


# ── follow-up projection ────────────────────────────────────────────────────


def test_an_id_less_entry_is_not_marked_projected(worktree):
    """`render_block` skips an id-less entry, so marking it would lie to readiness.

    Without the `e.ref.id` filter in `_mark_projected`, this entry would be
    flagged `in_pr_body=True` even though `project_follow_ups` never wrote it
    into the rendered block a reviewer sees.
    """
    ctx = _ctx(worktree)
    state = pr.state.new_state(ctx.repo, ctx.branch, pr_number=ctx.pr_number,
                               head_sha=ctx.head_sha, worktree_root=str(worktree))
    anonymous = FollowUp(
        ref=IssueRef(provider=IssueProvider.GITHUB, id="", url=""),
        title="an id-less follow-up", source=FollowUpSource.SELF_REVIEW,
    )
    pr.state.apply(state, FollowUpDomain(entries=[anonymous], updated_at="t"))

    with mock.patch.object(pr.describe, "_fetch_pr_body",
                           return_value=("t", "body")), \
         mock.patch.object(pr.describe, "_apply_body", return_value=True):
        projection = pr.describe.project_follow_ups(ctx, state)

    assert projection.moved is False
    assert state.follow_ups.entries[0].in_pr_body is False


# ── worktree_root guards ──────────────────────────────────────────────────


def test_run_describe_without_a_worktree_exits_with_guidance(capsys):
    ctx = make_ctx(branch="isaac/feat/x", pr_number=7,
                   worktree_root=None, head_sha="aaaa111")
    assert_no_worktree_exit(capsys, "isaac/feat/x",
                            pr.describe.run_describe, ctx)


def test_no_pr_reports_before_demanding_a_worktree(capsys):
    """The trail directory degrades, so the no-PR path is not blocked by it."""
    ctx = make_ctx(branch="isaac/feat/x", pr_number=None,
                   worktree_root=None, head_sha="aaaa111")
    assert pr.describe.run_describe(ctx) == 0
    assert "nothing to describe" in capsys.readouterr().err


# ── the publishing gate ─────────────────────────────────────────────────────


def test_a_draft_run_does_not_edit_the_pr(capsys):
    """The AI writes the body; publishing it is something a caller opts into.

    Every other GitHub write in the `pr` CLI is gated, and this was the one
    AI-authored write that reached a PR with no flag behind it.
    """
    with mock.patch.object(gh.client, "run") as run:
        assert pr.describe._apply_body("owner/repo", 7, "NEW BODY") is False
    run.assert_not_called()
    assert "DRAFT" in capsys.readouterr().err


def test_a_draft_run_shows_the_body_it_would_have_posted(capsys):
    """Drafting is only useful if the operator can read what was withheld."""
    with mock.patch.object(gh.client, "run"):
        pr.describe._apply_body("owner/repo", 7, "NEW BODY")
    assert "NEW BODY" in capsys.readouterr().err


def test_post_lets_the_edit_through(publishing_on):
    """Pairs with the draft case: proves the gate is not refusing everything."""
    with mock.patch.object(gh.client, "run",
                           return_value=mock.MagicMock(ok=True)) as run:
        assert pr.describe._apply_body("owner/repo", 7, "NEW BODY") is True
    assert run.call_args.kwargs["input_text"] == "NEW BODY"


def test_run_describe_with_the_gate_closed_is_not_a_failure(worktree, capsys):
    """A drafted edit is the default outcome, not an error.

    Exercises the real `_apply_body` — unlike every other case in this file,
    which stubs it to always return True — so a regression that treats a
    draft's False the same as a genuine `gh pr edit` failure fails here.
    """
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=("t", "")), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=(_wrapped("NEW BODY"), 0)), \
         mock.patch.object(gh.client, "run") as run:
        rc = pr.describe.run_describe(_ctx(worktree))
    assert rc == 0
    run.assert_not_called()
    assert "DRAFT" in capsys.readouterr().err
    state = pr.state.load_state(worktree / "target")
    assert state.describe.head_sha == "aaaa111"
    assert state.describe.changed is True


def _state_with_a_pending_follow_up(ctx, worktree):
    """A saved state whose one follow-up has not reached the PR body yet."""
    state = pr.state.new_state(ctx.repo, ctx.branch, pr_number=ctx.pr_number,
                               head_sha="old0000", worktree_root=str(worktree))
    entry = FollowUp(
        ref=IssueRef(provider=IssueProvider.GITHUB, id="941",
                     url="https://github.com/owner/repo/issues/941"),
        title="a pending follow-up", source=FollowUpSource.SELF_REVIEW,
    )
    pr.state.apply(state, FollowUpDomain(entries=[entry], updated_at="t"))
    pr.state.save_state(ctx.target_dir, state)


def test_a_draft_with_no_change_drafts_the_projection_once(worktree, capsys):
    """No change from the AI is no change, even with follow-ups still unprojected.

    In a draft the projection pass drafts the projected body and leaves the PR
    holding the unprojected one. The no-change answer is measured against what
    the PR would hold after projection, so it is not drafted a second time and
    not recorded as a revision.
    """
    ctx = _ctx(worktree)
    _state_with_a_pending_follow_up(ctx, worktree)
    with mock.patch.object(pr.describe, "_fetch_pr_body", return_value=("t", "body")), \
         mock.patch.object(pr.describe, "_git", return_value=""), \
         mock.patch.object(agent.backend, "prompt",
                           return_value=(pr.describe._NO_CHANGE, 0)), \
         mock.patch.object(gh.client, "run") as run:
        rc = pr.describe.run_describe(ctx)
    assert rc == 0
    run.assert_not_called()
    assert capsys.readouterr().err.count("DRAFT") == 1
    state = pr.state.load_state(ctx.target_dir)
    assert state.describe.head_sha == "aaaa111"
    assert state.describe.changed is False


def test_a_dry_run_with_no_change_records_nothing(worktree):
    """`--dry-run` writes no state, on the already-matches path as on any other."""
    rc, edits, _ = _run(_ctx(worktree), ai=(pr.describe._NO_CHANGE, 0), dry_run=True)
    assert rc == 0
    assert edits == []
    assert pr.state.load_state(worktree / "target") is None
