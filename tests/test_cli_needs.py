"""Tests for `cli.needs` — what a `pr` subcommand asks of dispatch.

These ran through `pr_cli_test.py`'s script shim until the declarations moved
into `ai/lib/cli/`. They are here now because the module is importable, which
is the point of the move: a need is resolved from an argv and a table, with no
binary in the way.
"""

import sys
from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from cli.needs import (  # noqa: E402
    LOCAL, NONE, REMOTE, REVIEW_DEFAULT_NEED, REVIEW_MODE_NEED,
    Need, ReviewMode, review_modes, review_need,
)

# A table shaped like `pr`'s, declared here so these tests describe the
# resolution rules rather than the set of modes `pr` happens to offer today.
MODES = {
    "--post": ReviewMode(),
    "--repair": ReviewMode(),
    "--summary": ReviewMode(),
    "--recover": ReviewMode(),
    "--list": ReviewMode(need=Need(NONE, update=False, lock=False),
                         schema_versions=(1,)),
}


# ── what a need records ──────────────────────────────────────────────────


def test_a_query_records_no_trail():
    """Resolving nothing and holding no lock names nothing to record."""
    assert Need(NONE, update=False, lock=False).records_a_trail is False


def test_holding_the_lock_records_a_trail():
    assert Need(NONE, update=False, lock=True).records_a_trail is True


def test_resolving_a_target_records_a_trail():
    assert Need(LOCAL, update=False, lock=False).records_a_trail is True
    assert Need(REMOTE, update=True, lock=True).records_a_trail is True


# ── reading a mode off an argv ───────────────────────────────────────────


def test_no_mode_flag_finds_nothing():
    assert review_modes([], MODES) == []
    assert review_modes(["--json-summary"], MODES) == []


def test_a_mode_flag_is_found():
    assert review_modes(["--summary"], MODES) == ["--summary"]


def test_several_mode_flags_come_back_in_table_order():
    """The exclusivity error quotes them back, so the order has to be the
    table's rather than the argv's."""
    assert review_modes(["--list", "--post"], MODES) == ["--post", "--list"]


def test_fix_with_post_is_not_the_post_mode():
    """`--fix --post` means publish what this run produces, which is the fix
    pass with a modifier — not the mode that publishes a review already on
    disk."""
    assert review_modes(["--fix", "--post"], MODES) == []


def test_fix_leaves_the_other_modes_alone():
    assert review_modes(["--fix", "--summary"], MODES) == ["--summary"]


# ── the need a mode declares ─────────────────────────────────────────────


def test_a_bare_review_needs_the_default():
    assert review_need([], MODES) == REVIEW_DEFAULT_NEED
    assert REVIEW_DEFAULT_NEED.update is True


def test_a_mode_flag_does_not_fast_forward_the_worktree():
    """Every mode acts on a review already on disk, at the commit that review
    describes."""
    assert review_need(["--summary"], MODES) == REVIEW_MODE_NEED
    assert REVIEW_MODE_NEED.update is False


def test_a_mode_that_declares_less_gets_less():
    assert review_need(["--list"], MODES) == Need(NONE, update=False, lock=False)


def test_the_first_mode_in_table_order_decides():
    assert review_need(["--list", "--post"], MODES) == REVIEW_MODE_NEED


def test_fix_with_post_needs_what_a_review_run_needs():
    assert review_need(["--fix", "--post"], MODES) == REVIEW_DEFAULT_NEED


# ── the table `pr` actually declares ─────────────────────────────────────


def test_every_pr_review_mode_declares_a_need():
    """The binary's own table, checked through the shared resolver."""
    pr_cli = load_script("pr_cli", REPO_ROOT / "ai" / "bin" / "pr")
    assert pr_cli._REVIEW_MODES
    for flag in pr_cli._REVIEW_MODES:
        assert isinstance(review_need([flag], pr_cli._REVIEW_MODES), Need)
