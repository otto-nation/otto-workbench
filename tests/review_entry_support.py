"""Fixtures and stubs shared by the review_entry_* suites."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import core.workbench_paths
import review.completion
import review.invoke
import review.issue
import review.preflight
import review.recover
import review.run
import review.worktree

import cli.review_entry  # noqa: E402
import gh.client
import core.run_lock


@pytest.fixture
def cr():
    """The entry point under test.

    An import, not a `SourceFileLoader` shim: the binary is a shim over this
    module now, and importing it gives every caller the one module object the
    interpreter already holds.
    """
    return cli.review_entry


@pytest.fixture
def reviews_dir(tmp_path, monkeypatch):
    """A throwaway reviews root, pointed at through the state root.

    One environment variable rather than one setattr per module that reads the
    root: every consumer resolves it per call, so there is nothing to patch.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    d = core.workbench_paths.reviews_dir()
    d.mkdir(parents=True)
    return d


def _self_flags(**overrides):
    base = dict(bin_dir=Path("/bin"), generator_version="test 1.0")
    base.update(overrides)
    return review.run.ReviewFlags(**base)


def _stub_pr_flow(monkeypatch, tmp_path):
    """The PR flow's edges, so a test can reach the posting decision."""
    monkeypatch.setattr(review.worktree, "find_repo_root", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(gh.client, "pr_view",
                        lambda *a, **kw: {"headRefName": "feat/x", "body": ""})
    monkeypatch.setattr(review.issue, "load_issue_provider",
                        lambda *a, **kw: SimpleNamespace(name="", options={}))
    monkeypatch.setattr(review.issue, "fetch_issue_context",
                        lambda *a, **kw: SimpleNamespace(link="", context=""))
    monkeypatch.setattr(review.preflight, "check_stale_review", lambda *a, **kw: None)
    monkeypatch.setattr(review.preflight, "check_pending_review", lambda *a, **kw: None)
    monkeypatch.setattr(review.preflight, "refuse_if_superseded", lambda *a, **kw: None)
    monkeypatch.setattr(review.worktree, "setup_pr_worktree",
                        lambda *a, **kw: SimpleNamespace(path=str(tmp_path), is_fallback=False))
    monkeypatch.setattr(review.recover, "pin_recover_worktree",
                        lambda *a, **kw: (str(tmp_path), None))
    monkeypatch.setattr(review.worktree, "cleanup_worktree", lambda *a, **kw: None)
    # The real one would flock a tmp_path that is not a git repo; what it is
    # handed is asserted directly in the test below.
    monkeypatch.setattr(core.run_lock, "claim_for_process",
                        lambda *a, **kw: None)
    monkeypatch.setattr(review.invoke, "run", lambda request: 0)
    monkeypatch.setattr(review.completion, "_display", lambda *a, **kw: None)
    monkeypatch.setattr(review.completion, "summarise", lambda *a, **kw: None)
    monkeypatch.setattr(review.completion, "record_domain", lambda *a, **kw: None)
    monkeypatch.setattr(review.run, "resolve_prior_review", lambda *a, **kw: "")
