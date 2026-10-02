"""pr.comments: where a round's artifacts land, and delivering the PR body."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _fix_adapter, _no_published_summary  # noqa: E402
from conftest import make_ctx
import fix.comment_checklist
import fix.engine
from core.proc import CmdResult
import pr.comments


# ── CommitPushResult ────────────────────────────────────────────────────────


def _make_completed(returncode, stdout="", stderr=""):
    """Create a CompletedProcess with the given results.

    For the gh call sites, which run through `proc` rather than a stub of the
    client itself. Git goes through `_git_ran`.
    """
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class TestTheArtifactsAreOutsideTheWorktree:
    """Nothing this pass writes may be picked up by a `git add` in the target.

    The pass runs against arbitrary repos, and `ignore/` — where these used to
    go — is only gitignored by convention. A repo that tracks it had the
    tracking file, the session log and the PR description draft committed along
    with the fix, and then pushed.
    """

    def _adapter(self, tmp_path):
        worktree = tmp_path / "wt"
        worktree.mkdir()
        ctx = make_ctx(repo="owner/repo", pr_number=1,
                       worktree_root=worktree, target_dir=tmp_path / "state")
        return worktree, _fix_adapter(worktree, ctx=ctx)

    def test_the_tracking_file_is_not_in_the_worktree(self, tmp_path):
        worktree, adapter = self._adapter(tmp_path)
        assert worktree not in adapter.tracking_path.parents

    def test_the_session_log_is_not_in_the_worktree(self, tmp_path):
        worktree, adapter = self._adapter(tmp_path)
        assert worktree not in adapter.session_log.parents

    def test_the_pr_description_draft_is_not_in_the_worktree(self, tmp_path):
        worktree, adapter = self._adapter(tmp_path)
        draft = pr.comments.pr_body_draft(adapter.artifacts)
        assert worktree not in draft.parents

    def test_the_artifacts_are_keyed_off_the_run_s_target(self, tmp_path):
        """The same identity the state file is filed under, not a second one."""
        _, adapter = self._adapter(tmp_path)
        assert adapter.artifacts == pr.comments.artifacts_dir(tmp_path / "state")


class TestDeliverPrBody:
    """A comment answered by rewriting the PR description is gated like a reply.

    The fix agent may not run `gh` at all, so the rewrite arrives as a file in
    the pass's artifact directory and this is what sends it. Every test here
    asserts on what reached (or did not reach) the process boundary rather than
    on a flag the caller consulted first.

    The directory these pass is a `tmp_path`-rooted stand-in: which directory
    it is belongs to `TestTheArtifactsAreOutsideTheWorktree`, and what the
    delivery does with a draft in it is the same wherever it sits.
    """

    def _draft(self, wt_path, body="A rewritten description.\n"):
        path = pr.comments.pr_body_draft(wt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def test_a_draft_run_issues_no_gh_call(self, worktree):
        """The regression: --fix without --post must not edit the PR."""
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        draft = self._draft(worktree)
        with patch("core.proc.subprocess.run", boom):
            assert pr.comments.deliver_pr_body(worktree, "owner/repo", 42) is True
        assert draft.exists(), "the undelivered rewrite must survive for --finish"

    def test_the_gate_is_checked_at_the_write_not_by_the_caller(self, worktree):
        """No `publishing.enabled()` guard here — the client refuses on its own.

        `pc.deliver_pr_body` is called unconditionally by the fix pass. If the gate
        lived at the call site instead, this call would publish.
        """
        self._draft(worktree)
        with patch.object(pr.comments, "_gh_post", return_value=CmdResult(1)) as post:
            pr.comments.deliver_pr_body(worktree, "owner/repo", 42)
        post.assert_called_once()

    def test_post_sends_it_through_the_pulls_endpoint(self, worktree, publishing_on):
        calls = []
        self._draft(worktree)
        with patch(
            "core.proc.subprocess.run",
            lambda *a, **kw: calls.append(a[0]) or _make_completed(0),
        ):
            assert pr.comments.deliver_pr_body(worktree, "owner/repo", 42) is False
        assert calls == [[
            "gh", "api", "repos/owner/repo/pulls/42",
            "--method", "PATCH", "--input", "-",
        ]]

    def test_a_delivered_rewrite_is_not_sent_twice(self, worktree, publishing_on):
        draft = self._draft(worktree)
        with patch.object(pr.comments, "update_pr_body", return_value=True):
            pr.comments.deliver_pr_body(worktree, "owner/repo", 42)
        assert not draft.exists()

    def test_the_fix_prompt_names_the_file_the_delivery_reads(self, worktree):
        """One path, two ends: the agent writes where `pc.deliver_pr_body` looks."""
        adapter = _fix_adapter(worktree)
        adapter.tracking_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.tracking_path.write_text("")
        with patch.object(fix.comment_checklist, "find_and_update_main_worktree", return_value=None):
            prompt = fix.engine._prompt(adapter, 10)

        assert str(pr.comments.pr_body_draft(adapter.artifacts)) in prompt
        assert "${pr_body_file}" not in prompt

    def test_no_draft_owes_nothing(self, worktree):
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran with nothing to send: {a}")

        with patch("core.proc.subprocess.run", boom):
            assert pr.comments.deliver_pr_body(worktree, "owner/repo", 42) is False

    def test_an_empty_draft_is_discarded_rather_than_sent(self, worktree,
                                                          publishing_on):
        """Sending it would blank the description the reviewer is reading."""
        draft = self._draft(worktree, body="   \n")
        with patch.object(pr.comments, "update_pr_body") as update:
            assert pr.comments.deliver_pr_body(worktree, "owner/repo", 42) is False
        update.assert_not_called()
        assert not draft.exists()
