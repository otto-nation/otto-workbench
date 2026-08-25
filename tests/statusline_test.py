"""Tests for the workbench status line's PR segment."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from conftest import load_script, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

statusline = load_script("workbench_statusline", BIN_DIR / "workbench-statusline")

import pr_domains  # noqa: E402
import pr_state  # noqa: E402
import pr_target  # noqa: E402


def _at(root: Path):
    """Make _pr_piece resolve its target dir to `root`, without touching git."""
    return patch.object(
        statusline.pr_target, "target_dir_for_checkout", return_value=root,
    )


def _save(root: Path, *domains, pr_number: int | None = 42):
    state = pr_state.new_state("owner/repo", "feat", pr_number=pr_number,
                               head_sha="abc", worktree_root=str(root))
    for domain in domains:
        pr_state.apply(state, domain)
    pr_state.save_state(root, state)


def test_statusline_reads_the_target_dir_for_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    wt = tmp_path / "wt"
    wt.mkdir()
    run_checked(["git", "init", "-q", "-b", "feat/a", str(wt)])
    run_checked(["git", "-C", str(wt), "remote", "add", "origin",
                 "git@github.com:acme/widget.git"])
    monkeypatch.chdir(wt)

    # Derived, not a literal: the repo key is <readable>-<digest> and a
    # hardcoded one here would pin this test to a key shape it does not own.
    target = pr_target.target_dir(pr_target.repo_key_from_origin(str(wt)), "feat/a")
    pr_state.save_state(target, pr_state.new_state(
        repo="acme/widget", branch="feat/a", pr_number=7,
        head_sha="sha", worktree_root=str(wt)))

    assert "PR#7" in statusline._pr_piece()


def test_statusline_is_silent_without_an_origin(tmp_path, monkeypatch):
    """No origin means no derivable target. Seed a state file exactly where
    the pre-target-dir implementation used to look — the checkout root
    itself — so a regression that reverts to guessing from cwd would find
    it and light up the segment; the correct implementation must still stay
    silent because it never derives a target without an origin remote."""
    wt = tmp_path / "wt"
    wt.mkdir()
    run_checked(["git", "init", "-q", str(wt)])
    monkeypatch.chdir(wt)
    _save(wt)

    assert statusline._pr_piece() == ""
    assert pr_target.target_dir_for_checkout(wt) is None


def test_pr_piece_renders_ci_failures(tmp_path):
    _save(tmp_path, pr_domains.CIDomain(conclusion="failure", failure_count=3))

    with _at(tmp_path):
        assert statusline._pr_piece() == "PR#42 CI:3F"


def test_pr_piece_is_blank_without_a_state_file(tmp_path):
    with _at(tmp_path):
        assert statusline._pr_piece() == ""


def test_pr_piece_is_blank_for_a_corrupt_state_file(tmp_path, capsys):
    """The status line renders or it does not. It never tracebacks, and it
    never leaks load_state's warning into the terminal."""
    path = tmp_path / pr_state.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")

    with _at(tmp_path):
        assert statusline._pr_piece() == ""
    assert capsys.readouterr().err == ""


def test_pr_piece_survives_null_behind_a_scalar_field(tmp_path):
    """Regression: a syntactically valid state.json with an explicit `null`
    behind an int or dict field used to load successfully and then crash in
    `_pr_details` — `failure_count > 0` on a `None`, `by_state.get()` on a
    `None`. serde now degrades a `null` there to the field's default."""
    path = tmp_path / pr_state.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "identity": {
            "repo": "owner/repo",
            "branch": "feat",
            "pr_number": 42,
            "head_sha": "abc",
            "worktree_root": str(tmp_path),
        },
        "ci": {"conclusion": "failure", "failure_count": None},
        "comments": {"by_state": None},
    }))

    with _at(tmp_path):
        # failure_count degrades to 0, so the CI:<n>F branch does not fire —
        # the point of this test is that it renders at all, not which branch.
        assert statusline._pr_piece() == "PR#42 CI:failure"


def test_pr_piece_survives_a_wrong_typed_scalar_field(tmp_path):
    """Regression: `"failure_count": "many"` parsed cleanly and then raised
    TypeError on `failure_count > 0`, which killed the whole line rather than
    the segment. serde now degrades an unrecoverable value to the default."""
    path = tmp_path / pr_state.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "identity": {
            "repo": "owner/repo",
            "branch": "feat",
            "pr_number": 42,
            "head_sha": "abc",
            "worktree_root": str(tmp_path),
        },
        "ci": {"conclusion": "failure", "failure_count": "many"},
    }))

    with _at(tmp_path):
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
    pr_state.apply(state, pr_domains.ReviewSummary(verdict="approve"))
    pr_state.apply(state, pr_domains.CommentsSummary(by_state={"open": 2}))

    assert statusline._pr_details(state) == "review:approve 2open"
