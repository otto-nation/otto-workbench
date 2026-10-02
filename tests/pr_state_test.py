"""Tests for pr.state library: construction, serialization, load_or_init,
apply_state_update and the close state."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import pr.state
from git.land import CommitStatus
from pr.comments_fix import FixSummary
from config.workbench_config import IssueProvider
from pr.follow_ups import FollowUp, FollowUpDomain, FollowUpSource, IssueRef
from pr.domains import CIDomain, CommentsSummary, TriageSummary, ReviewSummary, ReviewVerdict
from pr.fix import FixRecord
from pr.state import (
    PRIdentity,
    PRCloseState,
    PRClosure,
    PRState,
    load_state,
    save_state,
    new_state,
    apply,
    state_to_dict,
    state_from_dict,
    load_or_init,
    apply_state_update,
    STATE_VERSION,
)
from pr.ci_failures import RunState, FailureGroup, FailureItem, FailureKind, Outcome


# ── Dataclass construction ──────────────────────────────────────────────────


def test_pr_identity_fields():
    ident = PRIdentity(
        repo="owner/repo", branch="isaac/feat/foo",
        pr_number=42, head_sha="abc123", worktree_root="/tmp/wt",
    )
    assert ident.repo == "owner/repo"
    assert ident.pr_number == 42


def test_ci_domain_defaults():
    ci = CIDomain()
    assert ci.conclusion == ""
    assert ci.failure_count == 0
    assert ci.failure_kinds == {}
    assert ci.last_run_id is None
    assert ci.last_run_number is None
    assert ci.updated_at == ""
    assert ci.runs == {}
    assert ci.latest_run_id is None


def test_review_summary_defaults():
    rev = ReviewSummary()
    assert rev.review_file == ""
    assert rev.finding_counts == {}
    assert rev.cost_usd == 0.0


def test_comments_summary_defaults():
    c = CommentsSummary()
    assert c.total_threads == 0
    assert c.by_state == {}
    assert c.blocking_reviewers == []
    assert c.has_approvals is False
    assert c.seen_issue_comments == {}
    assert c.seen_review_body_comments == {}


def test_comments_summary_with_seen_ids():
    c = CommentsSummary(seen_issue_comments={111: "", 222: "2026-01-01T00:00:00Z"})
    assert c.seen_issue_comments == {111: "", 222: "2026-01-01T00:00:00Z"}


def test_triage_summary_defaults():
    t = TriageSummary()
    assert t.total == 0
    assert t.actionable == 0
    assert t.valid == 0
    assert t.questions == 0
    assert t.updated_at == ""


def test_pr_state_defaults():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.ci.failure_count == 0
    assert state.review.verdict == ""
    assert state.comments.total_threads == 0
    assert state.triage.total == 0


# ── new_state ───────────────────────────────────────────────────────────────


def test_new_state_sets_identity():
    state = new_state("owner/repo", "main", pr_number=7, head_sha="aaa", worktree_root="/wt")
    assert state.identity.repo == "owner/repo"
    assert state.identity.pr_number == 7
    assert state.created_at != ""


def test_new_state_no_pr():
    state = new_state("owner/repo", "main", pr_number=None, head_sha="bbb", worktree_root="/wt")
    assert state.identity.pr_number is None


# ── Serialization roundtrip ─────────────────────────────────────────────────


def test_state_to_dict_has_version():
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root="/wt")
    d = state_to_dict(state)
    assert d["_version"] == STATE_VERSION


def test_state_to_dict_and_back_empty():
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root="/wt")
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.identity.repo == "owner/repo"
    assert restored.identity.pr_number == 1
    assert restored.ci.failure_count == 0
    assert restored.review.verdict == ""
    assert restored.comments.total_threads == 0
    assert restored.triage.total == 0


def test_state_from_dict_requires_identity():
    """identity has no default: a payload without it is not a PRState."""
    with pytest.raises(TypeError, match="identity"):
        state_from_dict({"created_at": "t"})


def test_state_from_dict_null_ci_defaults_empty():
    """A domain reset to `null` reconstructs its default, not `None`.

    CIDomain has no null state of its own — every field defaults — so a `null`
    written for it means "value omitted", same as the key being absent.
    """
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root="/wt")
    d = state_to_dict(state)
    d["ci"] = None
    restored = state_from_dict(d)
    assert restored.ci == CIDomain()


def test_state_roundtrip_with_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, CIDomain(
        last_run_id=999, last_run_number=7,
        conclusion="failure", failure_count=3,
        failure_kinds={"lint": 2, "test": 1},
        updated_at="2026-06-20T00:00:00+00:00",
    ))
    apply(state, ReviewSummary(
        review_file="/tmp/review.md", review_type="self",
        head_sha="def", finding_counts={"M": 1, "S": 2},
        verdict=ReviewVerdict.CHANGES_REQUESTED.value, cost_usd=1.50, total_tokens=54321,
        updated_at="2026-06-20T00:00:00+00:00",
    ))
    apply(state, CommentsSummary(
        total_threads=5, by_state={"new": 2, "addressed": 3},
        blocking_reviewers=["alice"], has_approvals=True,
        updated_at="2026-06-20T00:00:00+00:00",
    ))

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert restored.ci.last_run_id == 999
    assert restored.ci.failure_count == 3
    assert restored.ci.failure_kinds == {"lint": 2, "test": 1}

    assert restored.review.review_file == "/tmp/review.md"
    assert restored.review.finding_counts == {"M": 1, "S": 2}
    assert restored.review.verdict == ReviewVerdict.CHANGES_REQUESTED.value
    assert restored.review.cost_usd == 1.50
    assert restored.review.total_tokens == 54321

    assert restored.comments.total_threads == 5
    assert restored.comments.by_state == {"new": 2, "addressed": 3}
    assert restored.comments.blocking_reviewers == ["alice"]
    assert restored.comments.has_approvals is True
    assert restored.comments.seen_issue_comments == {}


def test_commit_status_wire_values_are_the_strings_state_files_hold():
    """The enum is for the code. Changing a value breaks every saved state file."""
    assert [s.value for s in CommitStatus] == [
        "pushed", "no_changes", "commit_failed", "push_failed", "push_held",
        "push_lost", "push_unverified", "reconciled",
    ]


def test_a_commit_status_survives_a_state_roundtrip_as_a_plain_string():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, FixSummary(fix=FixRecord(commit_status=CommitStatus.PUSH_HELD)))

    restored = state_from_dict(json.loads(json.dumps(state_to_dict(state))))

    assert restored.fix.fix.commit_status == CommitStatus.PUSH_HELD
    assert restored.fix.fix.commit_status == "push_held"


def test_a_status_read_from_an_older_state_file_still_compares():
    """Loaded values are plain strings; the code compares them against members.

    The status is also where the pre-fold comment domain kept it — at the top
    level of the domain rather than on the record — so this is the migration's
    path too.
    """
    state = state_from_dict({
        "version": STATE_VERSION,
        "identity": {
            "repo": "owner/repo", "branch": "feat", "pr_number": 42,
            "head_sha": "def", "worktree_root": "/wt",
        },
        "fix": {"commit_status": "push_held"},
    })
    assert state.fix.fix.commit_status == CommitStatus.PUSH_HELD


def test_state_roundtrip_with_seen_issue_comments():
    """The stamps survive JSON, which turns every mapping key into a string."""
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, CommentsSummary(
        total_threads=3, by_state={"new": 1, "addressed": 2},
        seen_issue_comments={111: "", 222: "2026-07-02T00:00:00Z"},
        updated_at="2026-07-02T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(json.loads(json.dumps(d)))
    assert restored.comments.seen_issue_comments == {
        111: "", 222: "2026-07-02T00:00:00Z",
    }


def test_state_roundtrip_with_seen_review_body_comments():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, CommentsSummary(
        total_threads=3, by_state={"new": 1, "addressed": 2},
        seen_review_body_comments={444: "2026-07-13T00:00:00Z", 555: ""},
        updated_at="2026-07-13T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(json.loads(json.dumps(d)))
    assert restored.comments.seen_review_body_comments == {
        444: "2026-07-13T00:00:00Z", 555: "",
    }


def test_a_state_file_from_before_the_stamps_reads_as_nothing_seen():
    """The migration, such as it is: one round re-reports, nothing is dropped.

    The old field was a list of bare ids. serde drops what no field claims, so
    the mapping comes back empty and every comment reads unseen — noisy once,
    and the only direction that cannot silently swallow a reviewer's words.
    """
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, CommentsSummary(total_threads=1, updated_at="t"))
    d = state_to_dict(state)
    d["comments"]["seen_issue_comment_ids"] = [111, 222]
    d["comments"].pop("seen_issue_comments", None)

    restored = state_from_dict(json.loads(json.dumps(d)))

    assert restored.comments.seen_issue_comments == {}


def test_state_roundtrip_with_triage_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, TriageSummary(
        total=10, actionable=4, valid=3, questions=2,
        updated_at="2026-06-20T00:00:00+00:00",
    ))

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert restored.triage.total == 10
    assert restored.triage.actionable == 4
    assert restored.triage.valid == 3
    assert restored.triage.questions == 2
    assert restored.triage.updated_at == "2026-06-20T00:00:00+00:00"


def test_state_roundtrip_with_ci_runs():
    """CIDomain with nested RunState objects survives round-trip."""
    item = FailureItem(
        id="sc2086-bin-foo-42", annotation="SC2086: Double quote",
        file="bin/foo.sh", line=42, diagnosis="Unquoted var",
        fix_sha="abc123", outcome=Outcome.FIXED,
        headline="SC2086: Double quote to prevent globbing",
    )
    group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
    run = RunState(
        run_id=999, run_number=7, head_sha="def456",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00",
        failures={"shellcheck": group},
    )
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="def456", worktree_root="/wt")
    state.ci.runs[999] = run
    state.ci.latest_run_id = 999
    state.ci.conclusion = "failure"
    state.ci.failure_count = 1

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert restored.ci.latest_run_id == 999
    assert 999 in restored.ci.runs
    restored_run = restored.ci.runs[999]
    assert restored_run.head_sha == "def456"
    assert "shellcheck" in restored_run.failures
    restored_group = restored_run.failures["shellcheck"]
    assert restored_group.kind == FailureKind.LINT
    assert len(restored_group.items) == 1
    assert restored_group.items[0].outcome == Outcome.FIXED
    assert restored_group.items[0].fix_sha == "abc123"
    assert restored_group.items[0].headline == "SC2086: Double quote to prevent globbing"
    assert restored_group.items[0].context is None


def test_state_roundtrip_ci_with_context():
    """FailureItem context field survives round-trip."""
    item = FailureItem(
        id="drift-gen-verify", annotation="Process completed with exit code 1",
        file=None, line=None, diagnosis=None, fix_sha=None, outcome=None,
        context="Run 'mise run generate' locally and commit\n7 lines to delete",
    )
    group = FailureGroup(job="Generate & verify", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=200, run_number=3, head_sha="bbb",
        status="completed", conclusion="failure",
        fetched_at="2026-07-16T00:00:00+00:00",
        failures={"generate-verify": group},
    )
    state = new_state("owner/repo", "feat", pr_number=2, head_sha="bbb", worktree_root="/wt")
    state.ci.runs[200] = run
    state.ci.latest_run_id = 200

    d = state_to_dict(state)
    restored = state_from_dict(d)
    restored_item = restored.ci.runs[200].failures["generate-verify"].items[0]
    assert restored_item.context == "Run 'mise run generate' locally and commit\n7 lines to delete"
    assert restored_item.annotation == "Process completed with exit code 1"


def test_state_roundtrip_ci_runs_without_headline():
    """Old state files without headline field should deserialize with None."""
    item = FailureItem(
        id="x", annotation="err", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None,
    )
    group = FailureGroup(job="build", kind=FailureKind.BUILD, items=(item,))
    run = RunState(
        run_id=100, run_number=1, head_sha="aaa",
        status="completed", conclusion="failure",
        fetched_at="2026-06-26T00:00:00+00:00",
        failures={"build": group},
    )
    state = new_state("owner/repo", "feat", pr_number=1, head_sha="aaa", worktree_root="/wt")
    state.ci.runs[100] = run
    state.ci.latest_run_id = 100

    d = state_to_dict(state)
    del d["ci"]["runs"][100]["failures"]["build"]["items"][0]["headline"]

    restored = state_from_dict(d)
    restored_item = restored.ci.runs[100].failures["build"].items[0]
    assert restored_item.headline is None


# ── load_or_init ───────────────────────────────────────────────────────────


def test_load_or_init_creates_new_state(worktree):
    state = load_or_init(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=42, head_sha="abc123",
    )
    assert state.identity.repo == "owner/repo"
    assert state.identity.pr_number == 42
    assert state.identity.head_sha == "abc123"


def test_load_or_init_loads_existing_and_updates_identity(worktree):
    state = new_state("owner/repo", "feat", pr_number=1, head_sha="old", worktree_root=str(worktree))
    apply(state, CIDomain(conclusion="failure", failure_count=3, updated_at="t"))
    save_state(worktree, state)

    loaded = load_or_init(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=2, head_sha="new",
    )
    assert loaded.identity.head_sha == "new"
    assert loaded.identity.pr_number == 2
    assert loaded.ci.failure_count == 3


# ── apply_state_update ─────────────────────────────────────────────────────


def test_apply_state_update_ci(worktree):
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="ci",
        data={"conclusion": "failure", "failure_count": 2, "failure_kinds": {"lint": 2}, "updated_at": "t"},
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.ci.conclusion == "failure"
    assert loaded.ci.failure_count == 2


def test_apply_state_update_review(worktree):
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="review",
        data={"verdict": ReviewVerdict.APPROVE.value, "finding_counts": {"S": 1}, "cost_usd": 0.5, "updated_at": "t"},
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.review.verdict == ReviewVerdict.APPROVE.value
    assert loaded.review.cost_usd == 0.5


def test_apply_state_update_triage(worktree):
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="triage",
        data={"total": 5, "actionable": 2, "valid": 1, "questions": 1, "updated_at": "t"},
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.triage.total == 5
    assert loaded.triage.actionable == 2


def test_apply_state_update_unknown_domain(worktree):
    with pytest.raises(ValueError, match="Unknown state domain"):
        apply_state_update(
            target_dir=worktree, repo="r", branch="b",
            head_sha="a", domain="bogus", data={},
        )


class TestPRCloseState:
    def test_only_a_terminal_state_dates_itself(self):
        assert PRCloseState.MERGED.is_terminal
        assert PRCloseState.CLOSED.is_terminal
        assert not PRCloseState.OPEN.is_terminal
        assert PRCloseState.OPEN.ended_at_field is None

    def test_each_terminal_state_names_the_field_that_dates_it(self):
        assert PRCloseState.MERGED.ended_at_field == "mergedAt"
        assert PRCloseState.CLOSED.ended_at_field == "closedAt"

    def test_parse_reads_what_gh_says(self):
        assert PRCloseState.parse("MERGED") is PRCloseState.MERGED
        assert PRCloseState.parse("OPEN") is PRCloseState.OPEN

    def test_parse_returns_none_for_a_state_it_does_not_know(self):
        """A renamed or added gh state must be distinguishable from OPEN, which
        is what keeps it from reading as "still open" forever."""
        assert PRCloseState.parse("LOCKED") is None
        assert PRCloseState.parse(None) is None
        assert PRCloseState.parse("") is None

    def test_the_gh_query_asks_for_every_field_a_state_needs(self):
        """Derived from the enum, so a state added there is fetched for free."""
        fields = pr.state.GH_STATE_JSON_FIELDS.split(",")
        assert fields[0] == "state"
        assert set(fields[1:]) == {
            s.ended_at_field for s in PRCloseState if s.is_terminal
        }


class TestTerminalSummary:
    def _state(self) -> pr.state.PRState:
        state = pr.state.PRState(identity=pr.state.PRIdentity(
            repo="org/repo", branch="feat/x", pr_number=7,
            head_sha="abc1234", worktree_root="/tmp/wt",
        ))
        state.review.cost_usd = 4.25
        state.review.total_tokens = 91_000
        state.review.verdict = ReviewVerdict.CHANGES_REQUESTED.prose
        state.review.finding_counts = {"must-fix": 2, "nit": 5}
        state.rebase.conflicts_resolved = 3
        return state

    def test_carries_every_field_the_prune_is_about_to_delete(self):
        payload = pr.state.terminal_summary(self._state(), PRClosure(
            PRCloseState.MERGED, "2026-08-13T09:00:00Z"))
        assert payload == {
            "outcome": "MERGED",
            "ended_at": "2026-08-13T09:00:00Z",
            "cost_usd": 4.25,
            "total_tokens": 91_000,
            "verdict": "Request changes",
            "finding_counts": {"must-fix": 2, "nit": 5},
            "rebase_conflicts": 3,
            "follow_ups": [],
        }

    def test_the_follow_ups_are_carried_whole_rather_than_counted(self):
        """After the prune this event is the only record of what was deferred.

        "What follow-ups came out of that PR?" is a question about merged work,
        and a count answers that something was deferred without saying what.
        """
        state = self._state()
        state.follow_ups = FollowUpDomain(entries=[FollowUp(
            ref=IssueRef(provider=IssueProvider.GITHUB, id="1455", url="https://x"),
            title="a deferred thing",
            source=FollowUpSource.SELF_REVIEW,
        )], updated_at="t")
        payload = pr.state.terminal_summary(state, PRClosure(PRCloseState.MERGED))
        assert payload["follow_ups"] == [{
            "ref": {"provider": "github", "id": "1455", "url": "https://x"},
            "title": "a deferred thing",
            "source": "self_review",
            "filed_at": "", "head_sha": "", "invocation": "", "trail_root": "",
            "reason": "", "in_pr_body": False,
        }]
        # The emit path catches a TypeError rather than raising it, so an
        # unserialisable payload would go out as a warning nobody reads.
        assert json.dumps(payload)

    def test_finding_counts_are_copied_not_aliased(self):
        state = self._state()
        payload = pr.state.terminal_summary(state, PRClosure(PRCloseState.CLOSED))
        state.review.finding_counts["must-fix"] = 99
        assert payload["finding_counts"]["must-fix"] == 2

    def test_the_outcome_is_recorded_as_a_word_not_an_enum(self):
        """The payload is written to the trail as JSON; an Enum would not
        serialize, and `pr gc` reports the failure rather than raising it — so a
        regression here would go out as a warning nobody reads."""
        payload = pr.state.terminal_summary(
            self._state(), PRClosure(PRCloseState.MERGED))
        assert payload["outcome"] == "MERGED"
        assert json.loads(json.dumps(payload)) == payload

    def test_the_action_name_is_published(self):
        assert pr.state.TERMINAL_SUMMARY_ACTION == "pr_outcome"
