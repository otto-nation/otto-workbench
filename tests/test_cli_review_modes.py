"""Tests for `cli.review_modes` — `pr review`'s mode flags and their handlers.

These ran through `pr_cli_test.py`'s script shim until the table and its four
handlers moved out of `ai/bin/pr` in #909's T7 commit 3a. They are here now
because the module is importable: a mode handler can be called directly, with
no binary in the way.
"""

import sys
from pathlib import Path
from unittest import mock

from conftest import make_ctx, seed_review

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from cli import review_modes  # noqa: E402
from cli.needs import NONE, REVIEW_MODE_NEED, Need, ReviewMode  # noqa: E402


# ── the table has one owner ──────────────────────────────────────────────


def test_the_binary_routes_through_this_table():
    """`cmd_review` dispatches to whatever *this* table says, not to a copy.

    Asserted by substitution rather than by identity: a binary that copies the
    table keeps `is` true for the module attribute while routing through its
    own dict, so only replacing an entry and watching the call land proves the
    routing reads this one. A second table in the binary would be the fourth
    of the "declarations that must agree" the registry commit collapses, and
    it would agree right up until someone added a mode.
    """
    from cli import pr as pr_cli
    sentinel = mock.Mock(return_value=0)
    patched = dict(review_modes.MODES)
    patched["--summary"] = ReviewMode(sentinel)
    with mock.patch.dict(review_modes.MODES, patched, clear=True):
        rc = pr_cli.cmd_review(["--summary"], make_ctx(), bin_dir=BIN_DIR)
    assert rc == 0
    sentinel.assert_called_once()


def test_the_exclusivity_check_reads_this_table_too():
    """A flag is a mode because this table says so, wherever it is checked."""
    from cli import pr as pr_cli
    with mock.patch.dict(review_modes.MODES,
                         {"--summary": ReviewMode(), "--list": ReviewMode()},
                         clear=True):
        assert pr_cli.cmd_review(["--summary", "--list"], make_ctx(), bin_dir=BIN_DIR) == 1


def test_every_mode_declares_the_need_it_was_given():
    """Each mode's need, pinned as a literal rather than read off the member.

    A loop asserting `isinstance(mode.need, Need)` passes for every mode after
    a mode silently falls back to the table default, which is the one change
    that matters here: `--list` resolving REMOTE would take a lock and record a
    trail for an invocation that touches no repo.
    """
    assert set(review_modes.MODES) == {
        "--post", "--repair", "--summary", "--recover", "--list",
    }
    for flag in ("--post", "--repair", "--summary", "--recover"):
        assert review_modes.MODES[flag].need == REVIEW_MODE_NEED, flag
    listing_need = review_modes.MODES["--list"].need
    assert listing_need == Need(NONE, update=False, lock=False)
    assert not listing_need.records_a_trail


def test_the_exclusivity_prose_names_every_mode():
    """The error quotes the flags back, so it must list all of them."""
    prose = review_modes.flags_prose()
    for flag in review_modes.MODES:
        assert flag in prose


def test_the_exclusivity_prose_drops_the_leading_comma_for_a_single_flag():
    """A one-entry table has nothing to join, so the prose is just the flag —
    not the dangling `, and --flag` a naive `flags[:-1]` join produces when
    there is no first element to join it to."""
    with mock.patch.dict(review_modes.MODES, clear=True,
                          values={"--only": review_modes.MODES["--post"]}):
        assert review_modes.flags_prose() == "--only"


# ── a handler runs without executing the binary ──────────────────────────


def test_summary_runs_without_executing_the_binary(reviews_dir, capsys):
    """`--summary` is reachable by import, which is what the move bought."""
    review = seed_review(reviews_dir, name="owner-repo-42") / "review.md"
    with mock.patch("cli.review_modes.find_review_file", return_value=review), \
         mock.patch("cli.review_modes.json_summary", return_value='{"ok":true}'):
        rc = review_modes.summary([], make_ctx())
    assert rc == 0
    assert '{"ok":true}' in capsys.readouterr().out


def test_summary_reports_a_missing_review_rather_than_raising():
    """No review on disk is a diagnosis, not a traceback."""
    with mock.patch("cli.review_modes.find_review_file", return_value=None):
        assert review_modes.summary([], make_ctx()) == 1


def test_a_mode_without_a_pr_number_fails_cleanly():
    """Every mode acts on a review found by PR number."""
    ctx = make_ctx(pr_number=None)
    with mock.patch("cli.review_modes.find_review_file") as finder:
        assert review_modes.summary([], ctx) == 1
    finder.assert_not_called()


# ── the handler calls the delegate in-process ───────────────────────────


def test_post_calls_review_post_in_process(tmp_path):
    """`--post` runs review-post in this process, not as a child of bin_dir."""
    review = tmp_path / "review.md"
    with mock.patch("cli.review_modes.find_review_file", return_value=review), \
         mock.patch("core.publishing.call_entry_point", return_value=0) as call:
        rc = review_modes.post([], make_ctx(), bin_dir=tmp_path)
    assert rc == 0
    assert call.call_args[0][0] == "cli.review_post:main"
    argv = call.call_args[0][1]
    assert "--pr" in argv
    assert argv[argv.index("--pr") + 1] == "42"
    assert "--review-file" in argv
    assert argv[argv.index("--review-file") + 1] == str(review)


def test_repair_calls_rebuild_in_process(tmp_path):
    """The same contract on the other former-spawn mode."""
    review_dir = tmp_path / "reviews" / "owner-repo-42"
    review_dir.mkdir(parents=True)
    with mock.patch("cli.review_modes.find_review_file", return_value=None), \
         mock.patch("cli.review_modes.review_file_path",
                    return_value=review_dir / "review.md"), \
         mock.patch("core.publishing.call_entry_point", return_value=0) as call:
        rc = review_modes.repair([], make_ctx(), bin_dir=tmp_path)
    assert rc == 0
    assert call.call_args[0][0] == "cli.review_rebuild:main"
    argv = call.call_args[0][1]
    assert "--review-dir" in argv
    assert argv[argv.index("--review-dir") + 1] == str(review_dir)
    assert "--pr" in argv
    assert argv[argv.index("--pr") + 1] == "42"


def test_post_names_the_branch_it_is_publishing_for(tmp_path):
    """--expect-ref is how review-post tells this run's review from another's."""
    with mock.patch("cli.review_modes.find_review_file",
                    return_value=tmp_path / "review.md"), \
         mock.patch("core.publishing.call_entry_point", return_value=0) as call:
        review_modes.post([], make_ctx(branch="feat/x"), bin_dir=tmp_path)
    cmd = call.call_args[0][1]
    assert "--expect-ref" in cmd
    assert cmd[cmd.index("--expect-ref") + 1] == "feat/x"
