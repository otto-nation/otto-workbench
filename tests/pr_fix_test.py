"""Tests for the fix vocabulary every domain shares — outcomes and the record.

Nothing writes a `FixRecord` yet, so what is pinned here is the contract the
shared fix pipeline will write against: the value strings a state file already
holds, the default an unset outcome reads as, and how two rounds fold together.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_comments_fix
import pr_domains
import pr_fix
import pr_state
import serde


# ── FixOutcome ────────────────────────────────────────────────────────────


def test_fix_outcome_keeps_the_strings_thread_action_persists():
    """Every ThreadAction reads back as the FixOutcome of the same name.

    The comment pass has been writing these strings into state files for months.
    Phase 4 swaps its vocabulary for this one, and that swap is only free while
    the values match — a rename here is a state migration there.
    """
    for action in pr_comments_fix.ThreadAction:
        assert pr_fix.FixOutcome(action.value).name == action.name


def test_fix_outcome_adds_only_what_no_thread_has():
    """The two extra members are the ones the other two passes need."""
    extra = {o.name for o in pr_fix.FixOutcome} - {
        a.name for a in pr_comments_fix.ThreadAction
    }
    assert extra == {"SKIPPED", "DECLINED"}


def test_fix_outcome_serializes_as_its_string():
    item = pr_fix.ItemOutcome(id="i1", outcome=pr_fix.FixOutcome.DECLINED)
    assert serde.to_dict(item)["outcome"] == "declined"
    assert serde.from_dict(pr_fix.ItemOutcome, {"outcome": "declined"}) == item.__class__(
        outcome=pr_fix.FixOutcome.DECLINED,
    )


# ── ItemOutcome ───────────────────────────────────────────────────────────


def test_an_outcome_nobody_set_is_still_owed():
    """A crashed pass leaves work deferred, never claimed as fixed."""
    assert pr_fix.ItemOutcome().outcome is pr_fix.FixOutcome.DEFERRED


def test_an_outcome_round_trips_through_serde():
    item = pr_fix.ItemOutcome(
        id="i1", outcome=pr_fix.FixOutcome.FIXED, summary="s",
        file="a.py", line=12, commit_sha="abc1234", read_sha="def5678",
    )
    assert serde.from_dict(pr_fix.ItemOutcome, serde.to_dict(item)) == item


# ── FixRecord.merge_into ──────────────────────────────────────────────────


def _record(*ids, **kwargs) -> pr_fix.FixRecord:
    return pr_fix.FixRecord(
        items=[pr_fix.ItemOutcome(id=i) for i in ids],
        updated_at="2026-07-14T00:00:00+00:00",
        **kwargs,
    )


def test_a_later_round_keeps_the_items_it_did_not_touch():
    merged = _record("b").merge_into(_record("a"))
    assert [item.id for item in merged.items] == ["a", "b"]


def test_a_later_round_supersedes_the_same_item():
    later = pr_fix.FixRecord(
        items=[pr_fix.ItemOutcome(id="a", outcome=pr_fix.FixOutcome.FIXED)],
        updated_at="t2",
    )
    prior = pr_fix.FixRecord(
        items=[pr_fix.ItemOutcome(id="a", outcome=pr_fix.FixOutcome.DEFERRED)],
    )
    merged = later.merge_into(prior)
    assert [(i.id, i.outcome) for i in merged.items] == [
        ("a", pr_fix.FixOutcome.FIXED),
    ]


def test_the_envelope_comes_from_the_later_round():
    merged = _record("b", commit_sha="new").merge_into(_record("a", commit_sha="old"))
    assert merged.commit_sha == "new"


def test_items_without_an_id_are_kept_side_by_side():
    """Two anonymous outcomes are two items, not one overwriting the other."""
    merged = _record("").merge_into(_record(""))
    assert len(merged.items) == 2


def test_an_empty_round_leaves_the_prior_record_alone():
    """A subcommand writing its own domain says nothing about the fix pass."""
    prior = _record("a", commit_sha="abc1234")
    assert pr_fix.FixRecord().merge_into(prior) == prior


# ── Domain carries one ────────────────────────────────────────────────────


def test_every_domain_starts_with_an_empty_record():
    assert pr_domains.Domain().fix == pr_fix.FixRecord()


def test_a_domain_that_never_fixes_anything_says_nothing():
    assert pr_domains.Domain(updated_at="t").render_status() == []
    assert pr_domains.Domain(updated_at="t").readiness() == pr_domains.Readiness()


def test_a_domain_write_folds_the_record_rather_than_replacing_it():
    """`apply` replaces a domain wholesale; the fix record is the exception."""
    prior = pr_domains.CIDomain(fix=_record("a"), updated_at="t1")
    written = pr_domains.CIDomain(conclusion="success", updated_at="t2")
    merged = written.merge_into(prior)
    assert merged.conclusion == "success"
    assert [item.id for item in merged.fix.items] == ["a"]


@pytest.mark.parametrize("name,cls", sorted(pr_state._domains().items()))
def test_no_domain_drops_the_record_on_the_way_through(name, cls):
    """An override that forgets to chain through `super()` loses the record.

    Swept over the registry rather than asserted on the one domain that
    overrides `merge_into` today, because the next override is written by
    copying that one.
    """
    merged = cls(updated_at="t2").merge_into(cls(fix=_record("a"), updated_at="t1"))
    assert [item.id for item in merged.fix.items] == ["a"]
