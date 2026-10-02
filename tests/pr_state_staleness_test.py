"""pr.state staleness: dating each domain's answer, and what readiness will not vouch for."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import pr.state
from pr.comments_fix import FixSummary
from pr.domains import (
    CIDomain,
    CommentsSummary,
    DescribeSummary,
    PushDomain,
    ReviewSummary,
    ReviewVerdict,
)
from pr.state import (
    PRState,
    new_state,
    age_suffix,
    apply,
    _domains,
    merge_readiness,
    render_dashboard,
)
from pr.ci_failures import RunState


# ── Dating the snapshot ─────────────────────────────────────────────────────
#
# Every line on the dashboard is as old as the last run of the subcommand that
# wrote it, and nothing on it used to say so — a week-old "CI: 65 failure(s)"
# read exactly like one taken a minute ago.


def _ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()


def _ci_line(updated_at: str) -> str:
    """The CI line as `pr status` prints it for a domain written at `updated_at`."""
    state = new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                      worktree_root="/wt")
    apply(state, CIDomain(conclusion="failure", failure_count=65,
                          updated_at=updated_at))
    lines = render_dashboard(state, PushDomain(), repo="acme/widget", branch="feat/x")
    return next(l for l in lines if l.startswith("**CI**"))


def test_a_fresh_domain_is_not_dated():
    """An answer taken minutes ago is the one case a reader may assume."""
    assert _ci_line(_ago(minutes=2)) == "**CI** (red): failure — 65 failure(s)"


def test_an_hour_old_domain_says_when_it_was_taken():
    assert _ci_line(_ago(hours=3)).endswith(" (as of 3 hours ago)")


def test_a_day_old_domain_is_marked_stale():
    """The trap this exists to close: a week-old red CI presented as current."""
    line = _ci_line(_ago(days=7))
    assert "65 failure(s)" in line
    assert line.endswith(" [STALE — 7 days ago]")


def test_an_unwritten_domain_is_not_dated():
    """Its own line already says "not checked yet"; an age would contradict it."""
    assert _ci_line("") == "**CI**: not checked yet"


def test_a_stamp_that_cannot_be_read_is_stale_rather_than_fresh():
    """Something wrote the domain, so the answer is old-of-unknown-age — not new."""
    assert _ci_line("garbage").endswith(" [STALE — age unknown]")


def test_only_the_first_line_of_a_domain_is_dated():
    """The age belongs to the domain, not to each count it breaks out."""
    state = new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                      worktree_root="/wt")
    apply(state, CIDomain(conclusion="failure", failure_count=3,
                          failure_kinds={"test": 3}, last_run_number=12,
                          updated_at=_ago(days=7)))
    lines = render_dashboard(state, PushDomain(), repo="acme/widget", branch="feat/x")
    dated = [l for l in lines if "STALE" in l]
    assert dated == ["**CI** (red): failure — 3 failure(s) [STALE — 7 days ago]"]
    assert "  test: 3" in lines
    assert "  run #12" in lines


def test_the_live_push_observation_is_never_dated():
    """`cmd_status` observes push now, so its stamp cannot be old."""
    state = new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                      worktree_root="/wt")
    push = PushDomain(ahead=2, updated_at=pr.state.now_iso())
    lines = render_dashboard(state, push, repo="acme/widget", branch="feat/x")
    assert "**Push**: 2 commit(s) not pushed" in lines


@pytest.mark.parametrize("name,cls", sorted(_domains().items()))
def test_every_domain_in_the_registry_is_dated(name, cls):
    """The marker is applied by the fold, so a domain added later gets it too.

    A domain that renders nothing has nothing to date and is exempt; one that
    speaks must say when it last spoke. Push is exempt for a second reason,
    pinned by the test above: the dashboard overwrites it with a live
    observation, so the stamp reaching the fold is always the one taken here.
    """
    if cls is PushDomain:
        pytest.skip("push is observed live, never read from the cache")
    domain = cls(updated_at=_ago(days=7))
    if not domain.render_status():
        pytest.skip(f"{name} renders no line to date")
    state = new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                      worktree_root="/wt")
    apply(state, domain)
    lines = render_dashboard(state, PushDomain(), repo="acme/widget", branch="feat/x")
    assert any("[STALE — 7 days ago]" in l for l in lines)


def test_dating_a_domain_does_not_write_back_into_what_it_returned():
    """Rendering twice must not stack two suffixes on one line.

    Every domain builds a fresh list today, so writing through `rendered[0]`
    happens to work; one returning a shared or cached list would grow a suffix
    per render, and the second `pr status` of a session would be the one that
    showed it.
    """
    stale = _ago(days=7)
    state = new_state("acme/widget", "feat/x", pr_number=7, head_sha="a",
                      worktree_root="/wt")
    shared = ["**CI** (red): failure — 65 failure(s)"]
    domain = CIDomain(conclusion="failure", failure_count=65, updated_at=stale)
    with patch.object(CIDomain, "render_status", return_value=shared):
        apply(state, domain)
        first = render_dashboard(state, PushDomain(), repo="acme/widget", branch="feat/x")
        second = render_dashboard(state, PushDomain(), repo="acme/widget", branch="feat/x")
    assert first == second
    assert shared == ["**CI** (red): failure — 65 failure(s)"]


def test_age_suffix_boundaries():
    """The two thresholds, taken from either side."""
    assert age_suffix(_ago(minutes=59)) == ""
    assert age_suffix(_ago(hours=1)) == " (as of 1 hour ago)"
    assert age_suffix(_ago(hours=23)) == " (as of 23 hours ago)"
    assert age_suffix(_ago(hours=24)) == " [STALE — 1 day ago]"


# ── A stale verdict is not a clean bill of health ───────────────────────────
#
# The dashboard marks a week-old line [STALE] and the readiness line two lines
# below used to declare the PR ready on the strength of it. "We looked a week
# ago and it was fine" is not "it is fine", and only one of them is what
# `ready` is read as.


def _clean_but_aged(**age) -> PRState:
    state = new_state("acme/w", "feat/x", pr_number=7, head_sha="abc",
                      worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", failure_count=0, updated_at=_ago(**age)))
    apply(state, ReviewSummary(verdict=ReviewVerdict.APPROVE.value,
                               finding_counts={}, updated_at=_ago(**age)))
    apply(state, CommentsSummary(total_threads=0, updated_at=_ago(**age)))
    return state


def test_a_stale_clean_domain_is_unchecked_not_ready():
    answer = merge_readiness(_clean_but_aged(days=9))
    assert answer.blockers == ()
    assert any(u.startswith("CI (") for u in answer.unchecked)
    assert "ready" not in answer.render().lower()


def test_the_readiness_line_dates_what_it_could_not_vouch_for():
    """The operator has to know how old, not merely that it was not checked."""
    line = merge_readiness(_clean_but_aged(days=9)).render()
    assert "CI (last checked 9 days ago)" in line


def test_a_fresh_clean_domain_is_still_ready():
    """The gate must not turn every PR into a permanent 'not checked'."""
    assert merge_readiness(_clean_but_aged(minutes=2)).render() == (
        "**Merge readiness**: ready"
    )


def test_a_stale_failure_stays_a_blocker():
    """An old failure is still a reason not to merge.

    Downgrading it to "unchecked" would make a stale failing domain quieter
    than a fresh one, which is the wrong direction for the same asymmetry the
    rest of this change follows.
    """
    state = new_state("acme/w", "feat/x", pr_number=7, head_sha="abc",
                      worktree_root="/wt")
    apply(state, CIDomain(conclusion="failure", failure_count=3,
                          updated_at=_ago(days=9)))
    answer = merge_readiness(state)
    assert "CI failing" in answer.blockers
    assert not any(u.startswith("CI (last checked") for u in answer.unchecked)


def test_an_unwritten_domain_is_unchecked_without_an_age():
    """It has no answer to be stale about; its own readiness already says so."""
    state = new_state("acme/w", "feat/x", pr_number=7, head_sha="abc",
                      worktree_root="/wt")
    answer = merge_readiness(state)
    assert "CI" in answer.unchecked
    assert not any("last checked" in u for u in answer.unchecked)


def test_a_domain_whose_stamp_cannot_be_read_is_not_vouched_for():
    """Unreadable is unknown, and unknown must not read as current."""
    state = new_state("acme/w", "feat/x", pr_number=7, head_sha="abc",
                      worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", failure_count=0,
                          updated_at="garbage"))
    assert any(u.startswith("CI (") for u in merge_readiness(state).unchecked)


def test_the_stale_threshold_is_the_one_the_dashboard_marks():
    """One threshold, so the [STALE] marker and the readiness line agree.

    A domain the dashboard marks stale must be one the fold declines to vouch
    for, and a domain it leaves unmarked must be one the fold accepts.
    """
    for age, marked in [(dict(hours=23), False), (dict(hours=24), True)]:
        state = _clean_but_aged(**age)
        vouched = merge_readiness(state).render() == "**Merge readiness**: ready"
        assert vouched is not marked
        assert bool(age_suffix(state.ci.updated_at).count("STALE")) is marked


# ── The clock and the commit are different questions ────────────────────────
#
# `pr ci`, then a commit, then a push: the CI line is minutes old and describes
# the commit before this one. Fresh by every reading of `updated_at`, and not
# an answer about the branch as it now stands. The age check cannot see it and
# the commit check cannot see a week-old verdict about an unchanged tree, so
# the dashboard and the readiness fold ask both.


def _ci_for(commit: str, **age) -> CIDomain:
    ci = CIDomain(conclusion="success", failure_count=0,
                  updated_at=_ago(**age), latest_run_id=1)
    ci.runs[1] = RunState(run_id=1, run_number=1, head_sha=commit,
                          status="completed", conclusion="success",
                          fetched_at="t", failures={})
    return ci


def _state_at(head: str, domain) -> PRState:
    state = new_state("acme/w", "feat/x", pr_number=7, head_sha=head,
                      worktree_root="/wt")
    apply(state, domain)
    return state


def test_a_recent_verdict_about_another_commit_is_marked_superseded():
    """The gap a wall-clock check cannot close."""
    state = _state_at("newsha", _ci_for("oldsha", minutes=2))
    lines = render_dashboard(state, PushDomain(), repo="acme/w", branch="feat/x")
    ci_line = next(l for l in lines if l.startswith("**CI**"))
    assert ci_line.endswith(" [STALE — checked another commit]")


def test_a_recent_verdict_about_this_commit_is_not_marked():
    state = _state_at("newsha", _ci_for("newsha", minutes=2))
    lines = render_dashboard(state, PushDomain(), repo="acme/w", branch="feat/x")
    assert not any("STALE" in l for l in lines)


def test_the_commit_marker_outranks_the_age_one():
    """Dating a superseded verdict would argue it is still current."""
    state = _state_at("newsha", _ci_for("oldsha", days=9))
    lines = render_dashboard(state, PushDomain(), repo="acme/w", branch="feat/x")
    ci_line = next(l for l in lines if l.startswith("**CI**"))
    assert ci_line.endswith(" [STALE — checked another commit]")
    assert "9 days ago" not in ci_line


def test_readiness_will_not_vouch_for_a_superseded_verdict():
    state = _state_at("newsha", _ci_for("oldsha", minutes=2))
    answer = merge_readiness(state)
    assert "CI (checked another commit)" in answer.unchecked


def test_a_superseded_failure_stays_a_blocker():
    """Same asymmetry as the age check: an unvouchable domain is not quieter."""
    ci = _ci_for("oldsha", minutes=2)
    ci.conclusion, ci.failure_count = "failure", 2
    answer = merge_readiness(_state_at("newsha", ci))
    assert "CI failing" in answer.blockers
    assert not any("checked another commit" in u for u in answer.unchecked)


def test_a_domain_that_records_no_commit_is_judged_by_the_clock_alone():
    """CommentsSummary cannot place its answer, so it keeps its old reading."""
    fresh = _state_at("newsha", CommentsSummary(total_threads=0,
                                                updated_at=_ago(minutes=2)))
    # The other domains are unwritten and report themselves unchecked; what
    # matters is that comments is not among them and is never called superseded.
    assert not any("comments" in u for u in merge_readiness(fresh).unchecked)
    stale = _state_at("newsha", CommentsSummary(total_threads=0,
                                                updated_at=_ago(days=9)))
    unchecked = merge_readiness(stale).unchecked
    assert any("comments (last checked" in u for u in unchecked)
    assert not any("comments (checked another commit)" in u for u in unchecked)


def test_an_unresolvable_head_does_not_supersede_everything():
    """With no HEAD to compare against, the commit check must stay silent.

    `describes` is false when either side is unknown, so a naive check would
    mark every domain superseded the moment HEAD could not be read.
    """
    state = _state_at("", _ci_for("oldsha", minutes=2))
    lines = render_dashboard(state, PushDomain(), repo="acme/w", branch="feat/x")
    assert not any("checked another commit" in l for l in lines)


def test_a_domain_that_does_not_gate_merging_never_blocks_on_its_age():
    """`pr describe` has no bearing on whether the PR may merge.

    The staleness fold runs over the whole registry, and the domains that
    return an empty `Readiness()` do so precisely because they have no say.
    Blocking on one leaves the line permanently red on any branch old enough
    to carry a stale describe snapshot, and a readiness line that always says
    blocked is one nobody reads.
    """
    state = _clean_but_aged(minutes=2)
    apply(state, DescribeSummary(head_sha="abc", updated_at=_ago(days=30)))
    assert merge_readiness(state).render() == "**Merge readiness**: ready"


def test_a_stale_merge_relevant_domain_still_blocks_alongside_an_inert_one():
    """Exempting the inert domains must not exempt the ones that do gate."""
    state = _clean_but_aged(days=9)
    apply(state, DescribeSummary(head_sha="abc", updated_at=_ago(days=30)))
    unchecked = merge_readiness(state).unchecked
    assert any(u.startswith("CI (last checked") for u in unchecked)
    assert not any("describe" in u for u in unchecked)


def test_a_delivered_closeout_does_not_go_stale():
    """`FixSummary` answers from bookkeeping, not from a measurement.

    Its readiness reads the record this very file holds, so it is as true a
    week later as when written, and re-running the pass could not refresh it.
    Ageing it blocks a PR whose closeout was delivered yesterday.
    """
    state = _clean_but_aged(minutes=2)
    apply(state, FixSummary(updated_at=_ago(hours=30)))
    assert merge_readiness(state).render() == "**Merge readiness**: ready"


def test_an_undelivered_closeout_blocks_however_old_it_is():
    """Exempting it from the clock must not exempt it from its own verdict."""
    state = _clean_but_aged(minutes=2)
    apply(state, FixSummary(summary_deferred=True, updated_at=_ago(hours=30)))
    assert "closeout not delivered" in merge_readiness(state).render()


@pytest.mark.parametrize("name,cls", sorted(_domains().items()))
def test_every_domain_declares_whether_its_answer_can_go_stale(name, cls):
    """`ages` is read off the class, so a new domain inherits the default.

    True is the safe default \u2014 a domain reporting a measurement it forgot to
    mark is aged, which is noisy rather than unsound. This pins that the flag
    is a real class attribute on every domain rather than a field that
    serialises, which would put it in the state file.
    """
    import dataclasses
    assert isinstance(cls.ages, bool)
    assert "ages" not in {f.name for f in dataclasses.fields(cls)}
