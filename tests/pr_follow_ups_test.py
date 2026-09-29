"""Tests for the follow-up ledger: what it accumulates, and what it projects.

The two halves are tested apart. Accumulation is what stops an earlier round's
filing being dropped — and unlike every other domain, a dropped entry cannot be
recovered by re-running the pass, because the issue is already filed. Projection
is what a reviewer actually sees, and it has to survive a body that already
holds a copy of the block.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import readiness_state

from config.workbench_config import IssueProvider
from pr import follow_ups as pr_follow_ups
from pr import state as pr_state
from pr.follow_ups import (
    FollowUp,
    FollowUpDomain,
    FollowUpSource,
    IssueRef,
)


def _entry(issue_id, title="a thing", *, projected=False, provider=IssueProvider.GITHUB):
    return FollowUp(
        ref=IssueRef(provider=provider, id=issue_id, url=f"https://x/{issue_id}"),
        title=title,
        source=FollowUpSource.SELF_REVIEW,
        in_pr_body=projected,
    )



# ── accumulation ────────────────────────────────────────────────────────────


def test_a_later_filing_keeps_the_entries_an_earlier_round_recorded():
    """The base replaces wholesale; these cannot be re-measured."""
    prior = FollowUpDomain(entries=[_entry("1"), _entry("2")], updated_at="t1")
    later = FollowUpDomain(entries=[_entry("3")], updated_at="t2")
    merged = later.merge_into(prior)
    assert [e.ref.id for e in merged.entries] == ["1", "2", "3"]


def test_refiling_one_issue_supersedes_its_entry_rather_than_doubling_it():
    prior = FollowUpDomain(entries=[_entry("1", "old title")], updated_at="t1")
    later = FollowUpDomain(entries=[_entry("1", "new title")], updated_at="t2")
    merged = later.merge_into(prior)
    assert [(e.ref.id, e.title) for e in merged.entries] == [("1", "new title")]


def test_a_filing_pass_does_not_unproject_an_entry_the_body_already_holds():
    """`in_pr_body` is OR-ed: the filing writer knows nothing about the body.

    Without this the blocker reappears for an entry a reviewer can already see,
    and nothing the operator does clears it.
    """
    prior = FollowUpDomain(entries=[_entry("1", projected=True)], updated_at="t1")
    later = FollowUpDomain(entries=[_entry("1")], updated_at="t2")
    assert later.merge_into(prior).entries[0].in_pr_body is True


def test_a_write_of_this_domain_keeps_the_fix_record_beside_it():
    """`super().merge_into` folds the record every domain carries."""
    from pr.fix import FixOutcome, FixRecord, ItemOutcome

    record = FixRecord(items=[ItemOutcome(id="x", outcome=FixOutcome.FIXED)])
    prior = FollowUpDomain(entries=[_entry("1")], fix=record, updated_at="t1")
    merged = FollowUpDomain(entries=[_entry("2")], updated_at="t2").merge_into(prior)
    assert [i.id for i in merged.fix.items] == ["x"]


# ── reading a ledger back ───────────────────────────────────────────────────


def test_one_unreadable_entry_does_not_discard_the_rest_of_the_ledger():
    """serde drops a whole list on one bad element; these are not rebuildable."""
    raw = {
        "updated_at": "t",
        "entries": [
            {"ref": {"provider": "github", "id": "1", "url": ""},
             "title": "good", "source": "ci"},
            {"ref": {"provider": "not-a-tracker", "id": "2", "url": ""},
             "title": "bad", "source": "ci"},
            {"ref": {"provider": "linear", "id": "ENG-9", "url": ""},
             "title": "also good", "source": "manual"},
        ],
    }
    domain = FollowUpDomain._from_raw(raw)
    assert [e.ref.id for e in domain.entries] == ["1", "ENG-9"]


def test_the_ledger_round_trips_through_a_state_file():
    state = readiness_state()
    state.follow_ups = FollowUpDomain(entries=[_entry("1")], updated_at="t")
    back = pr_state.state_from_dict(pr_state.state_to_dict(state))
    assert back.follow_ups.entries[0].ref.provider is IssueProvider.GITHUB
    assert back.follow_ups.entries[0].source is FollowUpSource.SELF_REVIEW


def test_a_state_file_written_before_the_ledger_existed_still_reads():
    d = pr_state.state_to_dict(readiness_state())
    del d["follow_ups"]
    assert pr_state.state_from_dict(d).follow_ups.entries == []


# ── what it says ────────────────────────────────────────────────────────────


def test_an_unprojected_entry_is_reported_once_a_pr_exists():
    state = readiness_state(pr_number=7)
    state.follow_ups = FollowUpDomain(entries=[_entry("1")], updated_at="t")
    answer = state.follow_ups.readiness(state)
    assert answer.blockers == ()
    assert answer.unchecked == ("follow-ups not in the PR body (#1)",)


def test_nothing_is_reported_before_a_pr_exists():
    """Entries accrue from the first filing; there is no body to reach yet."""
    state = readiness_state(pr_number=None)
    state.follow_ups = FollowUpDomain(entries=[_entry("1")], updated_at="t")
    assert state.follow_ups.readiness(state) == pr_follow_ups.Readiness()


def test_a_projected_entry_reports_nothing():
    state = readiness_state(pr_number=7)
    state.follow_ups = FollowUpDomain(
        entries=[_entry("1", projected=True)], updated_at="t")
    assert state.follow_ups.readiness(state) == pr_follow_ups.Readiness()


def test_the_ledger_does_not_age():
    """Bookkeeping the file owns, not a measurement that goes out of date."""
    assert FollowUpDomain.ages is False


# ── projection ──────────────────────────────────────────────────────────────


def test_a_body_gains_one_marked_region():
    out = pr_follow_ups.project("## What\n\nthe change", [_entry("1", "fix a thing")])
    assert out.count(pr_follow_ups.FOLLOW_UPS_OPEN) == 1
    assert out.count(pr_follow_ups.FOLLOW_UPS_CLOSE) == 1
    assert "- #1 — fix a thing" in out
    assert out.startswith("## What")


def test_projecting_twice_leaves_one_region():
    entries = [_entry("1")]
    once = pr_follow_ups.project("## What\n\nbody", entries)
    assert pr_follow_ups.project(once, entries) == once


def test_a_body_that_came_back_holding_two_copies_collapses_to_one():
    """The describe prompt shows the model the block, so it may reproduce it."""
    entries = [_entry("1")]
    once = pr_follow_ups.project("## What\n\nbody", entries)
    doubled = once + "\n\n" + pr_follow_ups.render_block(entries)
    assert pr_follow_ups.project(doubled, entries) == once


def test_prose_outside_the_region_survives():
    entries = [_entry("1")]
    body = pr_follow_ups.project("## What\n\nkeep me\n\n## Why\n\nand me", entries)
    assert "keep me" in body and "and me" in body


def test_an_emptied_ledger_removes_the_region():
    body = pr_follow_ups.project("## What\n\nbody", [_entry("1")])
    assert pr_follow_ups.FOLLOW_UPS_OPEN not in pr_follow_ups.project(body, [])


def test_a_linear_key_is_rendered_without_a_hash():
    """`#ENG-9` is not a reference to anything."""
    entry = _entry("ENG-9", provider=IssueProvider.LINEAR)
    assert entry.ref.render() == "ENG-9"
    assert "- ENG-9 — a thing" in pr_follow_ups.render_block([entry])


@pytest.mark.parametrize("given", ["1455", "#1455"])
def test_a_github_number_is_rendered_with_exactly_one_hash(given):
    assert _entry(given).ref.render() == "#1455"


def test_an_unclosed_marker_does_not_swallow_a_later_block():
    """A half-written region must not pair with the next block's closer."""
    entries = [_entry("1")]
    body = pr_follow_ups.FOLLOW_UPS_OPEN + "\ntruncated"
    assert pr_follow_ups.project(body, entries).count(
        pr_follow_ups.FOLLOW_UPS_OPEN) == 1


# ── survival past the merge ─────────────────────────────────────────────────


def test_the_terminal_summary_carries_the_entries_not_a_count():
    """`pr gc` deletes the target; this event is the only record left."""
    state = readiness_state()
    state.follow_ups = FollowUpDomain(entries=[_entry("1", "a deferred thing")],
                                      updated_at="t")
    closure = pr_state.PRClosure(pr_state.PRCloseState.MERGED, "2026-01-01")
    carried = pr_state.terminal_summary(state, closure)["follow_ups"]
    assert carried == [{
        "ref": {"provider": "github", "id": "1", "url": "https://x/1"},
        "title": "a deferred thing",
        "source": "self_review",
        "filed_at": "", "head_sha": "", "invocation": "", "trail_root": "",
        "reason": "", "in_pr_body": False,
    }]


def test_the_terminal_summary_stays_json_serialisable():
    """The emit path catches a TypeError here; better to fail in a test."""
    import json

    state = readiness_state()
    state.follow_ups = FollowUpDomain(
        entries=[_entry("ENG-9", provider=IssueProvider.LINEAR)], updated_at="t")
    closure = pr_state.PRClosure(pr_state.PRCloseState.MERGED, "2026-01-01")
    assert json.dumps(pr_state.terminal_summary(state, closure))
