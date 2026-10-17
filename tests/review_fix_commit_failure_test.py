"""A fix commit that fails pre-commit must not be reported as done.

On the 2026-10-16 batch run, a self-review `--fix` pass edited files, then
`git commit` died in the pre-commit chain:

- PR 4529 (`isaac/fix/queue_conflicts_partial_fetch`): `precommit:lint:py`
  Ruff I001 auto-fixed the file and did not re-stage it.
- PR 4116 (`isaac/ENG-4203/doc_external_tokens`): `precommit:lint:ts` failed
  because `doc-external/node_modules` was missing.

Both left staged, uncommitted edits. The review sidecar's `fix_commit_sha`
stayed empty. The review child still exited 0, so the batch marked 4529 `done`
(every box ticked, no open findings) and 4116 as an ordinary `step_review`
(open findings). Nothing reported the uncommitted work.

Expected: a formatter's auto-fix is re-staged and the commit retried; a
commit that still fails is a failed step, with the reason surfaced.
"""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import batch.model
import batch.outcomes
import git.land
import review.paths
from git.land import CommitStatus

from review_fix_pass_support import _PUSHED, _make_job, _run, git_wt

_COMMIT_FAILED_LINE = "✗ commit failed: Running pre-commit checks..."
_REVIEW = (
    "## Must fix\n"
    "- [ ] **[M1]** `helper.py:1` — Missing helper\n"
)

ITEM = batch.model.Item(
    key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
    worktree="/wt",
)


def _install_auto_fix_hook(tmp_path) -> None:
    """Rewrite staged files that still carry `UNSORTED`, and fail the commit.

    The rewrite is the worktree only — the index keeps the unsorted bytes —
    which is what Ruff I001 (and the other formatters that `--fix` in place)
    leaves behind when the hook does not `git add` afterwards.
    """
    hook = tmp_path / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "found=0\n"
        "for f in $(git diff --cached --name-only); do\n"
        "  if grep -q UNSORTED \"$f\" 2>/dev/null; then\n"
        "    printf 'sorted\\n' > \"$f\"\n"
        "    echo 'I001 [*] Import block is un-sorted or un-formatted' >&2\n"
        "    found=1\n"
        "  fi\n"
        "done\n"
        "if [ \"$found\" -eq 1 ]; then exit 1; fi\n"
        "exit 0\n"
    )
    hook.chmod(0o755)


def _install_refusing_hook(tmp_path, message: str = "gate refused") -> None:
    hook = tmp_path / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n")
    hook.chmod(0o755)


def _head(wt: Path) -> str:
    return git_out(wt, "rev-parse", "HEAD").strip()


def _commit_count(wt: Path) -> int:
    return int(git_out(wt, "rev-list", "--count", "HEAD").strip())


def _land(repo, **kwargs):
    kwargs.setdefault("message", "fix: work")
    kwargs.setdefault("gated", False)
    with patch("git.push.push", return_value=_PUSHED) as mock_push:
        landed = git.land.land(repo, **kwargs)
    return landed, mock_push


# ── formatter auto-fix: restage and retry ───────────────────────────────────


class TestAFormatterAutoFixIsRestaged:
    """Ruff I001 on PR 4529: the hook rewrote the file and the commit died."""

    def test_land_restages_the_rewrite_and_retries_the_commit(
        self, git_wt, tmp_path, live_git_hooks,
    ):
        _install_auto_fix_hook(tmp_path)
        (git_wt / "src.py").write_text("UNSORTED\n")
        before = _head(git_wt)

        landed, mock_push = _land(git_wt)

        assert landed.status is CommitStatus.PUSHED
        assert landed.sha
        assert landed.sha != before
        assert git_out(git_wt, "show", "HEAD:src.py") == "sorted\n"
        assert git_out(git_wt, "status", "--porcelain").strip() == ""
        mock_push.assert_called_once()

    def test_a_pathspec_commit_restages_only_the_scoped_rewrite(
        self, git_wt, tmp_path, live_git_hooks,
    ):
        """A formatter touching a file outside the pass's scope stays out."""
        _install_auto_fix_hook(tmp_path)
        (git_wt / "src.py").write_text("operator work in progress\n")
        (git_wt / "helper.py").write_text("UNSORTED\n")

        landed, _ = _land(git_wt, paths=["helper.py"])

        assert landed.status is CommitStatus.PUSHED
        assert git_out(git_wt, "show", "HEAD:helper.py") == "sorted\n"
        assert git_out(git_wt, "show", "HEAD:src.py") == "original\n"
        assert "src.py" in git_out(git_wt, "status", "--porcelain")

    def test_the_review_fix_pass_commits_the_rewritten_file(
        self, git_wt, tmp_path, live_git_hooks,
    ):
        _install_auto_fix_hook(tmp_path)
        job = _make_job(git_wt, tmp_path, _REVIEW)
        before = _head(git_wt)

        _run(job, {"M1": "fixed"},
             work=lambda: (git_wt / "helper.py").write_text("UNSORTED\n"))

        assert _head(git_wt) != before
        assert git_out(git_wt, "show", "HEAD:helper.py") == "sorted\n"
        assert "helper.py" not in git_out(git_wt, "status", "--porcelain")
        meta = review.paths.read_review_meta(Path(job.artifact_dir))
        assert meta.fix_commit_sha == _head(git_wt)


# ── a hook that will not auto-fix: the step fails ───────────────────────────


class TestARefusedCommitIsAFailedPass:
    """PR 4116: `precommit:lint:ts` failed; nothing to restage would have helped."""

    def test_the_work_stays_uncommitted_and_the_sidecar_stays_unstamped(
        self, git_wt, tmp_path, live_git_hooks,
    ):
        _install_refusing_hook(tmp_path)
        job = _make_job(git_wt, tmp_path, _REVIEW)
        before = _head(git_wt)

        with patch("git.push.push", return_value=_PUSHED) as mock_push:
            _run(job, {"M1": "fixed"},
                 work=lambda: (git_wt / "helper.py").write_text("def helper(): pass\n"))

        mock_push.assert_not_called()
        assert _head(git_wt) == before
        assert _commit_count(git_wt) == 1
        status = git_out(git_wt, "status", "--porcelain")
        assert "helper.py" in status
        meta = review.paths.read_review_meta(Path(job.artifact_dir))
        assert meta.fix_commit_sha == ""
        assert meta.fix_commit_status in ("", CommitStatus.COMMIT_FAILED)

    def test_land_names_the_refusal_and_does_not_push(
        self, git_wt, tmp_path, live_git_hooks,
    ):
        _install_refusing_hook(tmp_path, "precommit:lint:ts failed")
        (git_wt / "src.py").write_text("edited\n")

        landed, mock_push = _land(git_wt)

        assert landed.status is CommitStatus.COMMIT_FAILED
        assert landed.sha == ""
        assert "precommit:lint:ts failed" in landed.error
        mock_push.assert_not_called()


# ── batch mapping: exit 0 is not done ───────────────────────────────────────


def _classify_review(monkeypatch, *, findings, log_tail):
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: findings)
    monkeypatch.setattr(batch.outcomes, "fix_checks", lambda *a, **k: [])
    return batch.outcomes.classify(
        batch.model.Step.REVIEW, 0, "", item=ITEM, log_tail=log_tail,
    )


class TestBatchDoesNotMarkAFailedCommitDone:
    """The review child exited 0. classify read that, not the failed landing."""

    def test_ticked_findings_and_a_failed_commit_are_not_done(self, monkeypatch):
        """PR 4529: every box ticked, `fix_commit_sha` empty, item `done`."""
        r = _classify_review(monkeypatch, findings=[], log_tail=[_COMMIT_FAILED_LINE])

        assert r.status is batch.model.StepStatus.FAILED
        assert r.decisions
        assert r.decisions[0].kind is batch.model.DecisionKind.FAILED
        assert r.decisions[0].payload.get("reason") == "commit_failed"

    def test_open_findings_do_not_hide_a_failed_commit(self, monkeypatch):
        """PR 4116: classified as ordinary `step_review` over the leftover nits."""
        findings = [{"severity": "nit", "title": "x", "declined": True}]
        r = _classify_review(monkeypatch, findings=findings, log_tail=[_COMMIT_FAILED_LINE])

        assert r.status is batch.model.StepStatus.FAILED
        assert all(d.kind is not batch.model.DecisionKind.STEP_REVIEW for d in r.decisions)

    def test_a_clean_review_without_a_commit_failure_is_still_done(self, monkeypatch):
        """The new read must not turn every exit-0 review into a failure."""
        r = _classify_review(monkeypatch, findings=[], log_tail=["Review: /tmp/review.md"])

        assert r.status is batch.model.StepStatus.DONE
        assert r.decisions == []
