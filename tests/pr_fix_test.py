"""Tests for the fix vocabulary every domain shares — outcomes and the record.

What is pinned here is the contract every pass writes against: the value strings
a state file already holds, the default an unset outcome reads as, and how two
rounds fold together.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.land
from conftest import readiness_state
import pr.domains
import pr.fix
import pr.state
import core.serde


# ── FixOutcome ────────────────────────────────────────────────────────────


def test_fix_outcome_keeps_the_strings_the_comment_pass_persisted():
    """The six verdicts the comment pass wrote before the fold still read back.

    Those strings are in state files on disk, under a key the migration renames
    but does not translate. A rename of any member here is a silent data loss
    there — the outcome comes back as something else, or the load raises and
    `serde.load_file` discards the whole file.
    """
    persisted = {
        "fixed": "FIXED",
        "deferred": "DEFERRED",
        "needs_human": "NEEDS_HUMAN",
        "dismissed": "DISMISSED",
        "already_addressed": "ALREADY_ADDRESSED",
        "declined": "DECLINED",
    }
    for value, name in persisted.items():
        assert pr.fix.FixOutcome(value).name == name


def test_fix_outcome_adds_only_what_no_comment_thread_had():
    """The members no comment thread ever wrote — the rest are that pass's own."""
    persisted = {
        "FIXED", "DEFERRED", "NEEDS_HUMAN", "DISMISSED", "ALREADY_ADDRESSED", "DECLINED",
    }
    assert {o.name for o in pr.fix.FixOutcome} - persisted == {
        "SKIPPED", "SETTLED_ELSEWHERE",
    }


def test_fix_outcome_serializes_as_its_string():
    item = pr.fix.ItemOutcome(id="i1", outcome=pr.fix.FixOutcome.DECLINED)
    assert core.serde.to_dict(item)["outcome"] == "declined"
    assert core.serde.from_dict(pr.fix.ItemOutcome, {"outcome": "declined"}) == pr.fix.ItemOutcome(
        outcome=pr.fix.FixOutcome.DECLINED,
    )


# ── The two shared predicates ─────────────────────────────────────────────


def test_only_a_fix_counts_as_one():
    """Every tally of fixed work reads this, so the answer lives in one place."""
    counted = {o for o in pr.fix.FixOutcome if o.counts_as_fixed}
    assert counted == {pr.fix.FixOutcome.FIXED}


def test_only_a_fix_may_name_a_commit():
    """Stamping a SHA on anything else credits a commit with work it lacks."""
    citing = {o for o in pr.fix.FixOutcome if o.may_cite_a_commit}
    assert citing == {pr.fix.FixOutcome.FIXED}


def test_a_thread_settled_on_the_forge_is_neither_fixed_nor_attributable():
    """The point of the member: resolution is not evidence that code changed."""
    settled = pr.fix.FixOutcome.SETTLED_ELSEWHERE
    assert not settled.counts_as_fixed
    assert not settled.may_cite_a_commit


# ── SettledBy ─────────────────────────────────────────────────────────────


def test_an_outcome_written_before_provenance_existed_reads_as_the_pass():
    """A state file with no `settled_by` recorded the pass's own work."""
    restored = core.serde.from_dict(pr.fix.ItemOutcome, {"id": "i1", "outcome": "fixed"})
    assert restored.settled_by is pr.fix.SettledBy.PASS


def test_provenance_survives_the_round_trip():
    item = pr.fix.ItemOutcome(
        id="i1",
        outcome=pr.fix.FixOutcome.SETTLED_ELSEWHERE,
        settled_by=pr.fix.SettledBy.RECONCILIATION,
    )
    assert core.serde.to_dict(item)["settled_by"] == "reconciliation"
    assert core.serde.from_dict(pr.fix.ItemOutcome, core.serde.to_dict(item)) == item


# ── ItemOutcome ───────────────────────────────────────────────────────────


def test_an_outcome_nobody_set_is_still_owed():
    """A crashed pass leaves work deferred, never claimed as fixed."""
    assert pr.fix.ItemOutcome().outcome is pr.fix.FixOutcome.DEFERRED


def test_an_outcome_round_trips_through_serde():
    item = pr.fix.ItemOutcome(
        id="i1", outcome=pr.fix.FixOutcome.FIXED, summary="s",
        file="a.py", line=12, commit_sha="abc1234", read_sha="def5678",
    )
    assert core.serde.from_dict(pr.fix.ItemOutcome, core.serde.to_dict(item)) == item


# ── FixRecord.merge_into ──────────────────────────────────────────────────


def _record(*ids, **kwargs) -> pr.fix.FixRecord:
    return pr.fix.FixRecord(
        items=[pr.fix.ItemOutcome(id=i) for i in ids],
        updated_at="2026-07-14T00:00:00+00:00",
        **kwargs,
    )


def test_a_later_round_keeps_the_items_it_did_not_touch():
    merged = _record("b").merge_into(_record("a"))
    assert [item.id for item in merged.items] == ["a", "b"]


def test_a_later_round_supersedes_the_same_item():
    later = pr.fix.FixRecord(
        items=[pr.fix.ItemOutcome(id="a", outcome=pr.fix.FixOutcome.FIXED)],
        updated_at="t2",
    )
    merged = later.merge_into(_record("a"))
    assert [(i.id, i.outcome) for i in merged.items] == [
        ("a", pr.fix.FixOutcome.FIXED),
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
    assert pr.fix.FixRecord().merge_into(prior) == prior


# ── Domain carries one ────────────────────────────────────────────────────


def test_every_domain_starts_with_an_empty_record():
    assert pr.domains.Domain().fix == pr.fix.FixRecord()


def test_a_domain_that_never_fixes_anything_says_nothing():
    assert pr.domains.Domain(updated_at="t").render_status() == []
    assert pr.domains.Domain(updated_at="t").readiness(readiness_state()) == pr.domains.Readiness()


def test_a_domain_write_folds_the_record_rather_than_replacing_it():
    """`apply` replaces a domain wholesale; the fix record is the exception."""
    prior = pr.domains.CIDomain(fix=_record("a"), updated_at="t1")
    written = pr.domains.CIDomain(conclusion="success", updated_at="t2")
    merged = written.merge_into(prior)
    assert merged.conclusion == "success"
    assert [item.id for item in merged.fix.items] == ["a"]


def test_a_record_survives_the_state_file():
    """`commit_status` is the field a round trip is most likely to lose.

    It is the one `Optional[Enum]` on the record, and None is also what an
    unwritten record holds — so a serde gap here would read back as "no pass has
    run" rather than as an error, on every domain at once.
    """
    state = pr.state.new_state(
        repo="o/r", pr_number=1, branch="b", head_sha="def5678", worktree_root="/tmp/wt",
    )
    state.ci.fix = pr.fix.FixRecord(
        items=[pr.fix.ItemOutcome(id="i1", outcome=pr.fix.FixOutcome.SKIPPED)],
        commit_sha="abc1234",
        commit_status=git.land.CommitStatus.PUSH_HELD,
        head_sha="def5678",
        updated_at="2026-07-14T00:00:00+00:00",
    )
    restored = pr.state.state_from_dict(pr.state.state_to_dict(state))
    assert restored.ci.fix == state.ci.fix
    assert restored.review.fix == pr.fix.FixRecord()


@pytest.mark.parametrize("name,cls", sorted(pr.state._domains().items()))
def test_no_domain_drops_the_record_on_the_way_through(name, cls):
    """An override that forgets to chain through `super()` loses the record.

    Swept over the registry rather than asserted on the one domain that
    overrides `merge_into` today, because the next override is written by
    copying that one.
    """
    merged = cls(updated_at="t2").merge_into(cls(fix=_record("a"), updated_at="t1"))
    assert [item.id for item in merged.fix.items] == ["a"]
