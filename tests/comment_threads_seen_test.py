"""`pr comments`: a top-level comment is recorded as seen only once triage has read it.

Driven through `run_threads` because the loss lived in the ordering there: the
seen record was saved before triage ran, so a triage that failed still marked
the review body read, and the next run skipped it for good.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402,F401
from conftest import make_ctx
from gh.pr_data import PRData
import agent.invoke
import cli.review_threads
import pr.comments
import pr.state
import pr.thread_context
import review.comment_threads

REVIEW_ID = 5506752759
REVIEW_BODY = {
    "id": REVIEW_ID, "user": "bob", "last_edited_at": "",
    "body": "should-fix: handle the error path\nnit: rename x\nnit: typo\nnit: spacing",
}
DECOMPOSED = json.dumps({"threads": [], "comment_items": [{
    "source_id": str(REVIEW_ID), "source_type": "review_body", "index": 0,
    "reviewer": "bob", "classification": "suggestion", "verification": "valid",
    "summary": "handle the error path", "body": "should-fix: handle the error path",
}]})


def _ctx(tmp_path):
    work = tmp_path / "wt"
    (work / ".git").mkdir(parents=True, exist_ok=True)
    return make_ctx(repo="owner/repo", pr_number=34, branch="isaac/deps/pi_1_1_0",
                    worktree_root=work, head_sha="abc1234",
                    target_dir=tmp_path / "target")


def _run(tmp_path, argv, answer):
    """One `pr comments` run whose triage prompt answers `answer`.

    Returns the exit code, the prompt mock and the persisted seen record.
    """
    ctx = _ctx(tmp_path)
    pr_data = PRData(viewer_login="isaacg", head_sha="abc1234",
                     head_ref="isaac/deps/pi_1_1_0", base_ref="main",
                     review_threads=[], threads_complete=True)
    prompt = MagicMock(return_value=answer)
    args = cli.review_threads.build_parser().parse_args(argv)
    with patch.object(review.comment_threads, "fetch_pr_data", return_value=pr_data), \
         patch.object(pr.comments, "fetch_review_body_comments",
                      side_effect=lambda *a, **k: [dict(REVIEW_BODY)]), \
         patch.object(pr.comments, "fetch_issue_comments", return_value=[]), \
         patch.object(pr.comments, "fetch_reviewer_verdicts", return_value=[]), \
         patch.object(pr.thread_context, "branch_commit_log", return_value=""), \
         patch.object(agent.invoke, "run_prompt", prompt):
        code = review.comment_threads.run_threads(MagicMock(), args, ctx)
    st = pr.state.load_state(ctx.target_dir)
    seen = st.comments.seen_review_body_comments if st else {}
    return code, prompt, seen


def _failed():
    return agent.invoke.PromptResult("", 0, False)


def _decomposed():
    return agent.invoke.PromptResult(DECOMPOSED, 0, True)


class TestSeenFollowsTriage:
    def test_a_triage_that_returned_nothing_leaves_the_comment_unseen(self, tmp_path):
        code, prompt, seen = _run(tmp_path, ["--triage"], _failed())
        assert code == 1
        assert prompt.called, "triage never ran, so this tested nothing"
        assert REVIEW_ID not in seen

    def test_a_triage_that_failed_outright_leaves_the_comment_unseen(self, tmp_path):
        code, _prompt, seen = _run(
            tmp_path, ["--triage"], agent.invoke.PromptResult("404 not found", 1, False))
        assert code == 1
        assert REVIEW_ID not in seen

    def test_the_next_run_triages_the_comment_again(self, tmp_path):
        _run(tmp_path, ["--triage"], _failed())
        code, prompt, seen = _run(tmp_path, ["--triage"], _decomposed())
        assert code == 0
        prompt_text = prompt.call_args.args[1]
        assert str(REVIEW_ID) in prompt_text
        assert "handle the error path" in prompt_text
        assert seen == {REVIEW_ID: ""}

    def test_a_comment_triage_read_is_not_sent_again(self, tmp_path):
        _run(tmp_path, ["--triage"], _decomposed())
        code, prompt, seen = _run(tmp_path, ["--triage"], _decomposed())
        assert code == 0
        assert not prompt.called
        assert seen == {REVIEW_ID: ""}

    def test_a_view_without_triage_marks_nothing_seen(self, tmp_path):
        code, prompt, seen = _run(tmp_path, [], _decomposed())
        assert code == 0
        assert not prompt.called
        assert REVIEW_ID not in seen
        # And the triage after it still gets the comment.
        _code, prompt, _seen = _run(tmp_path, ["--triage"], _decomposed())
        assert str(REVIEW_ID) in prompt.call_args.args[1]
