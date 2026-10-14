"""Tests for `cli.needs` — what a `pr` subcommand asks of dispatch.

These ran through `pr_cli_test.py`'s script shim until the declarations moved
into `ai/lib/cli/`. They are here now because the module is importable, which
is the point of the move: a need is resolved from an argv and a table, with no
binary in the way.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402
import cli.review_modes  # noqa: E402
import core.run_lock  # noqa: E402
from cli.needs import (  # noqa: E402
    LOCAL, NO_LOCK, NONE, REMOTE, REVIEW_DEFAULT_NEED, REVIEW_MODE_NEED, REVIEW_SELF_NEED,
    TARGET_AND_CHECKOUT, TARGET_ONLY, Need, ReviewMode, has_flag, review_modes, review_need,
)


def _handled(*_a, **_kw):
    return 0


# A table shaped like `pr`'s, declared here so these tests describe the
# resolution rules rather than the set of modes `pr` happens to offer today.
# Handlers mirror `pr`'s: every mode but `--recover` is answered locally.
MODES = {
    "--post": ReviewMode(handler=_handled),
    "--repair": ReviewMode(handler=_handled),
    "--summary": ReviewMode(handler=_handled),
    "--recover": ReviewMode(),
    "--list": ReviewMode(handler=_handled, need=Need(NONE, update=False, locks=NO_LOCK),
                         schema_versions=(1,)),
}


# ── what a need records ──────────────────────────────────────────────────


def test_a_query_records_no_trail():
    """Resolving nothing and holding no lock names nothing to record."""
    assert Need(NONE, update=False, locks=NO_LOCK).records_a_trail is False


def test_holding_the_target_records_a_trail():
    assert Need(NONE, update=False, locks=TARGET_ONLY).records_a_trail is True


def test_a_global_lock_alone_records_no_dispatch_trail():
    """`pr batch run` names no repo, PR or branch, and keeps a trail of its own."""
    need = Need(NONE, update=False, locks={core.run_lock.Global("batch")})
    assert need.records_a_trail is False


def test_resolving_a_target_records_a_trail():
    assert Need(LOCAL, update=False, locks=NO_LOCK).records_a_trail is True
    assert Need(REMOTE, update=True, locks=TARGET_ONLY).records_a_trail is True


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
    assert review_need(["--list"], MODES) == Need(NONE, update=False, locks=NO_LOCK)


def test_the_first_mode_in_table_order_decides():
    assert review_need(["--list", "--post"], MODES) == REVIEW_MODE_NEED


def test_fix_with_post_needs_what_a_review_run_needs():
    assert review_need(["--fix", "--post"], MODES) == REVIEW_DEFAULT_NEED


# ── --self resolves without the network ──────────────────────────────


def test_self_resolves_locally():
    """A self-review's subject is the working tree, which git alone can name.

    The pre-PR gate ran at REMOTE and so failed whenever `gh` could not answer
    — an exhausted GraphQL budget, an expired token — for a reason that had
    nothing to do with the branch being reviewed.
    """
    assert review_need(["--self"], MODES) == REVIEW_SELF_NEED
    assert REVIEW_SELF_NEED.depth is LOCAL


def test_self_still_fetches_and_locks():
    """Only the depth drops: the subject is still the branch's current state,
    and a --fix pass still commits to the worktree."""
    assert REVIEW_SELF_NEED.update is True
    assert REVIEW_SELF_NEED.locks == TARGET_AND_CHECKOUT


def test_self_with_fix_resolves_locally():
    assert review_need(["--self", "--fix"], MODES) == REVIEW_SELF_NEED


def test_self_with_fix_and_post_resolves_locally():
    """`--post` beside `--fix` is a modifier, not a mode, so it does not
    escalate the depth."""
    assert review_need(["--self", "--fix", "--post"], MODES) == REVIEW_SELF_NEED


def test_an_abbreviated_fix_beside_self_and_post_still_locks_the_checkout():
    """argparse takes `--fi` for `--fix`, so `--self --fi --post` is still a
    self-review with a modifier, not the publish mode."""
    assert review_need(["--self", "--fi", "--post"], MODES) == REVIEW_SELF_NEED
    assert REVIEW_SELF_NEED.locks == TARGET_AND_CHECKOUT


def test_a_mode_flag_outranks_self():
    """`--summary --self` acts on a review already on disk, and that review
    names a PR — so the mode's need decides, not the target selector's."""
    assert review_need(["--summary", "--self"], MODES) == REVIEW_MODE_NEED


def test_a_bare_review_is_unaffected():
    """Reviewing a PR still needs `gh` to name it."""
    assert review_need([], MODES).depth is REMOTE


# ── the table `pr` actually declares ─────────────────────────────────────


def test_every_pr_review_mode_declares_a_need():
    """The real table, checked through the shared resolver.

    Reached by import rather than by executing the binary: the table moved to
    `cli.review_modes` in #909's T7 commit 3a, which is what let this file drop
    its last `load_script`.
    """
    assert cli.review_modes.MODES
    for flag in cli.review_modes.MODES:
        assert isinstance(review_need([flag], cli.review_modes.MODES), Need)


def test_a_bare_review_holds_only_its_target():
    """Its tree is the PR's own, set up later and locked once known."""
    assert REVIEW_DEFAULT_NEED.locks == TARGET_ONLY


def test_self_recover_also_locks_the_checkout():
    """`--recover` is the one mode `review` runs itself, and under `--self` it writes the tree."""
    need = review_need(["--self", "--recover"], MODES)
    assert (need.depth, need.update) == (REVIEW_MODE_NEED.depth, REVIEW_MODE_NEED.update)
    assert need.locks == TARGET_AND_CHECKOUT


def test_an_abbreviated_self_is_still_a_self_review():
    """argparse takes `--sel` for `--self`, so the declaration must too."""
    assert review_need(["--sel"], MODES) == REVIEW_SELF_NEED


@pytest.mark.parametrize("argv,expected", [
    (["--dry-run"], True),
    (["--dry"], True),
    (["--dry-run=1"], True),
    (["--title", "x"], False),
    (["--", "--dry-run"], False),
    (["-d"], False),
])
def test_a_flag_is_found_the_way_argparse_would_accept_it(argv, expected):
    assert has_flag(argv, "--dry-run") is expected
