"""Tests for the workbench status line's PR segment."""

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless script via importlib. argv is pinned because the
# module exits at import time when it sees --help.
_path = str(BIN_DIR / "workbench-statusline")
_loader = importlib.machinery.SourceFileLoader("workbench_statusline", _path)
_spec = importlib.util.spec_from_loader("workbench_statusline", _loader, origin=_path)
statusline = importlib.util.module_from_spec(_spec)
statusline.__file__ = _path
with patch.object(sys, "argv", ["workbench-statusline"]):
    _spec.loader.exec_module(statusline)
sys.modules.setdefault("workbench_statusline", statusline)

import pr_state  # noqa: E402
import workbench_paths  # noqa: E402

from conftest import state_path  # noqa: E402


def _at(root: Path):
    """Make _pr_piece resolve its repo root to `root`.

    The patch lands on the subprocess module itself, so it also answers the
    `git rev-parse` behind the state's own path. Both questions are stubbed
    from the real worktree, or the segment would look for state somewhere the
    test never wrote it. A third git call routed through here needs its own
    branch in `_run`, or it silently gets answered with the repo root.
    """
    git_dir = workbench_paths.worktree_state_dir(root).parent

    def _run(cmd, *_args, **_kwargs):
        out = git_dir if "--absolute-git-dir" in cmd else root
        return SimpleNamespace(returncode=0, stdout=str(out))

    return patch("workbench_statusline.subprocess.run", side_effect=_run)


def _save(root: Path, *domains, pr_number: int | None = 42):
    state = pr_state.new_state("owner/repo", "feat", pr_number=pr_number,
                               head_sha="abc", worktree_root=str(root))
    for domain in domains:
        pr_state.apply(state, domain)
    pr_state.save_state(root, state)


def test_pr_piece_renders_ci_failures(worktree):
    _save(worktree, pr_state.CIDomain(conclusion="failure", failure_count=3))

    with _at(worktree):
        assert statusline._pr_piece() == "PR#42 CI:3F"


def test_pr_piece_is_blank_without_a_state_file(worktree):
    with _at(worktree):
        assert statusline._pr_piece() == ""


def test_pr_piece_is_blank_for_a_corrupt_state_file(worktree, capsys):
    """The status line renders or it does not. It never tracebacks, and it
    never leaks load_state's warning into the terminal."""
    path = state_path(worktree)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")

    with _at(worktree):
        assert statusline._pr_piece() == ""
    assert capsys.readouterr().err == ""


def test_pr_piece_survives_null_behind_a_scalar_field(worktree):
    """Regression: a syntactically valid state.json with an explicit `null`
    behind an int or dict field used to load successfully and then crash in
    `_pr_details` — `failure_count > 0` on a `None`, `by_state.get()` on a
    `None`. serde now degrades a `null` there to the field's default."""
    path = state_path(worktree)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "identity": {
            "repo": "owner/repo",
            "branch": "feat",
            "pr_number": 42,
            "head_sha": "abc",
            "worktree_root": str(worktree),
        },
        "ci": {"conclusion": "failure", "failure_count": None},
        "comments": {"by_state": None},
    }))

    with _at(worktree):
        # failure_count degrades to 0, so the CI:<n>F branch does not fire —
        # the point of this test is that it renders at all, not which branch.
        assert statusline._pr_piece() == "PR#42 CI:failure"


def test_pr_piece_survives_a_wrong_typed_scalar_field(worktree):
    """Regression: `"failure_count": "many"` parsed cleanly and then raised
    TypeError on `failure_count > 0`, which killed the whole line rather than
    the segment. serde now degrades an unrecoverable value to the default."""
    path = state_path(worktree)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "identity": {
            "repo": "owner/repo",
            "branch": "feat",
            "pr_number": 42,
            "head_sha": "abc",
            "worktree_root": str(worktree),
        },
        "ci": {"conclusion": "failure", "failure_count": "many"},
    }))

    with _at(worktree):
        assert statusline._pr_piece() == "PR#42 CI:failure"


def test_main_keeps_the_reuse_segment_when_the_pr_segment_raises(capsys):
    """The PR segment is guarded as a whole, not just its import: a raise
    inside it must not take down a status line that has something to say."""
    with patch.object(statusline, "_reuse_piece", return_value="reuse:ultra"), \
            patch.object(statusline, "_pr_piece", side_effect=RuntimeError("boom")):
        statusline.main()

    assert capsys.readouterr().out == "reuse:ultra"


def test_pr_details_reads_typed_fields(tmp_path):
    """Regression: the status line hand-parsed the JSON, so a rename on
    PRState blanked the segment with nothing to catch it."""
    state = pr_state.new_state("owner/repo", "feat", pr_number=42,
                               head_sha="abc", worktree_root=str(tmp_path))
    pr_state.apply(state, pr_state.ReviewSummary(verdict="approve"))
    pr_state.apply(state, pr_state.CommentsSummary(by_state={"open": 2}))

    assert statusline._pr_details(state) == "review:approve 2open"
