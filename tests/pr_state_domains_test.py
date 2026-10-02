"""pr.domains value types held in state: SupersessionDomain and ReviewVerdict."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from pr.domains import ReviewVerdict, SupersessionDomain, SupersessionKind, SupersessionSignal
from pr.state import PRIdentity, PRState, load_state, save_state


# ── SupersessionDomain ──────────────────────────────────────────────────────


def _superseded_state(worktree, **overrides):
    fields = dict(
        head_sha="a" * 40, base_sha="b" * 40,
        signals=[SupersessionSignal(
            SupersessionKind.READDS_REMOVED_SYMBOL, "`foo` is gone",
        )],
    )
    fields.update(overrides)
    return PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="a" * 40, worktree_root=str(worktree)),
        supersession=SupersessionDomain(**fields),
    )


def test_supersession_round_trips_its_signals(worktree):
    """The verdict is only worth caching if it survives the write."""
    save_state(worktree, _superseded_state(worktree))
    stored = load_state(worktree).supersession
    assert stored.signals == [SupersessionSignal(
        SupersessionKind.READDS_REMOVED_SYMBOL, "`foo` is gone",
    )]
    assert stored.matches("a" * 40, "b" * 40)


def test_supersession_defaults_empty_on_legacy_state(worktree):
    """State written before this domain must still load."""
    save_state(worktree, _superseded_state(worktree))
    path = worktree / "state.json"
    raw = json.loads(path.read_text())
    del raw["supersession"]
    path.write_text(json.dumps(raw))
    assert load_state(worktree).supersession == SupersessionDomain()


def test_a_verdict_matches_only_the_commits_it_was_computed_from():
    domain = SupersessionDomain(head_sha="a" * 40, base_sha="b" * 40)
    assert domain.matches("a" * 40, "b" * 40)
    assert not domain.matches("c" * 40, "b" * 40)
    assert not domain.matches("a" * 40, "c" * 40)


def test_a_verdict_keyed_on_an_empty_sha_matches_nothing():
    """An unresolvable ref records nothing that can later be keyed on."""
    assert not SupersessionDomain(head_sha="", base_sha="").matches("", "")
    assert not SupersessionDomain(head_sha="a" * 40).matches("a" * 40, "")


# ── ReviewVerdict ───────────────────────────────────────────────────────────


def test_review_verdict_value_is_the_persisted_spelling():
    """Serialized state keeps the snake-case values it always held."""
    assert ReviewVerdict.APPROVE.value == "approve"
    assert ReviewVerdict.CHANGES_REQUESTED.value == "changes_requested"
    assert ReviewVerdict.DISAPPROVE.value == "disapprove"


def test_review_verdict_prose_is_the_spelling_reviews_are_written_in():
    assert ReviewVerdict.CHANGES_REQUESTED.prose == "Request changes"
    assert ReviewVerdict.NEEDS_DISCUSSION.prose == "Needs discussion"


@pytest.mark.parametrize("must,should,expected", [
    (2, 3, ReviewVerdict.CHANGES_REQUESTED),
    (1, 0, ReviewVerdict.CHANGES_REQUESTED),
    (0, 1, ReviewVerdict.NEEDS_DISCUSSION),
    (0, 0, ReviewVerdict.APPROVE),
])
def test_review_verdict_from_counts(must, should, expected):
    assert ReviewVerdict.from_counts(must, should) is expected


@pytest.mark.parametrize("text,expected", [
    ("Approve — looks good.", ReviewVerdict.APPROVE),
    ("**Needs discussion** — two open questions.", ReviewVerdict.NEEDS_DISCUSSION),
    ("request changes — a bug.", ReviewVerdict.CHANGES_REQUESTED),
    ("  Disapprove — wrong approach.", ReviewVerdict.DISAPPROVE),
    ("Looks fine to me.", None),
    ("", None),
])
def test_review_verdict_stated_in(text, expected):
    assert ReviewVerdict.stated_in(text) is expected


def test_review_verdict_outranks_orders_the_derivable_calls():
    assert ReviewVerdict.CHANGES_REQUESTED.outranks(ReviewVerdict.NEEDS_DISCUSSION)
    assert ReviewVerdict.NEEDS_DISCUSSION.outranks(ReviewVerdict.APPROVE)
    assert not ReviewVerdict.APPROVE.outranks(ReviewVerdict.CHANGES_REQUESTED)
    assert not ReviewVerdict.APPROVE.outranks(ReviewVerdict.APPROVE)


def test_review_verdict_disapprove_is_outside_the_ranking():
    """No finding count implies Disapprove, and none refutes it."""
    assert ReviewVerdict.DISAPPROVE.rank is None
    assert not ReviewVerdict.DISAPPROVE.outranks(ReviewVerdict.APPROVE)
    assert not ReviewVerdict.CHANGES_REQUESTED.outranks(ReviewVerdict.DISAPPROVE)
    assert not ReviewVerdict.APPROVE.outranks(None)
