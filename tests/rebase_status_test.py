"""Tests for rebase_status rendering."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_state
import rebase_status


def test_render_status_not_run():
    r = pr_state.RebaseSummary()
    assert rebase_status.render_status(r) == ["**Rebase**: not run yet"]


def test_render_status_completed_with_conflicts():
    r = pr_state.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = rebase_status.render_status(r)
    assert len(result) >= 1
    assert "2 file(s)" in result[0]
    assert "3 commit(s)" in result[0]
    assert "force-pushed" in result[0]


def test_render_status_clean():
    r = pr_state.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=5,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = rebase_status.render_status(r)
    assert len(result) >= 1
    assert "clean" in result[0].lower()


def test_render_status_not_pushed():
    r = pr_state.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["a.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00Z",
    )
    result = rebase_status.render_status(r)
    assert "force-pushed" not in result[0]


def test_render_status_conflicts():
    r = pr_state.RebaseSummary(
        status="conflicts", updated_at="2026-06-20T00:00:00Z",
    )
    result = rebase_status.render_status(r)
    assert len(result) == 1
    assert "conflicts" in result[0].lower()
    assert "pr rebase --fix" in result[0]


def test_render_status_stale_files():
    r = pr_state.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["pnpm-lock.yaml"],
        files_stale=["pnpm-lock.yaml"],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    result = rebase_status.render_status(r)
    assert len(result) == 2
    assert "regeneration failed" in result[1]
    assert "pnpm-lock.yaml" in result[1]


def test_render_status_no_stale_line_when_clean():
    r = pr_state.RebaseSummary(
        status="completed", target_base="origin/main", commits_replayed=1,
        conflicts_resolved=0, files_resolved=[],
        force_pushed=True, updated_at="2026-06-20T00:00:00Z",
    )
    assert len(rebase_status.render_status(r)) == 1


def test_render_status_aborted():
    r = pr_state.RebaseSummary(
        status="aborted", updated_at="2026-06-20T00:00:00Z",
    )
    result = rebase_status.render_status(r)
    assert result == ["**Rebase**: aborted"]
