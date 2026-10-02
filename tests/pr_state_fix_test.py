"""pr.state and the comment fix domain: FixSummary, accumulating outcomes, and state files
written before the fold."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import readiness_state
from git.land import CommitStatus
from pr.comments_fix import CLOSEOUT_COMMAND, FixSummary
from pr.fix import FixOutcome, FixRecord, ItemOutcome
from pr.state import (
    PRIdentity,
    PRState,
    load_state,
    save_state,
    new_state,
    apply,
    state_to_dict,
    state_from_dict,
    apply_state_update,
    STATE_VERSION,
)


# ── FixSummary ─────────────────────────────────────────────────────────────


def test_fix_summary_defaults():
    f = FixSummary()
    assert f.fix == FixRecord()
    assert f.reviewers == {}
    assert f.replies_posted == 0
    assert f.summary_url == ""
    assert f.summary_deferred is False
    assert f.deferred_issue_id == ""
    assert f.deferred_issue_url == ""
    assert f.updated_at == ""


def test_fix_summary_round_trips_head_sha(worktree):
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(fix=FixRecord(head_sha="abc1234")),
    )
    save_state(worktree, state)
    assert load_state(worktree).fix.fix.head_sha == "abc1234"


def test_fix_summary_head_sha_defaults_empty_on_legacy_state(worktree):
    """State written before this field must still load."""
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(fix=FixRecord(head_sha="abc1234")),
    )
    save_state(worktree, state)
    path = worktree / "state.json"
    raw = json.loads(path.read_text())
    del raw["fix"]["fix"]["head_sha"]
    path.write_text(json.dumps(raw))
    assert load_state(worktree).fix.fix.head_sha == ""


def test_an_outcome_round_trips_commit_sha(worktree):
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(fix=FixRecord(items=[ItemOutcome(id="t1", commit_sha="deadbee")])),
    )
    save_state(worktree, state)
    assert load_state(worktree).fix.fix.items[0].commit_sha == "deadbee"


def test_an_outcome_commit_sha_defaults_empty_on_legacy_state(worktree):
    """State written before this field must still load."""
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(fix=FixRecord(items=[ItemOutcome(id="t1", commit_sha="deadbee")])),
    )
    save_state(worktree, state)
    path = worktree / "state.json"
    raw = json.loads(path.read_text())
    del raw["fix"]["fix"]["items"][0]["commit_sha"]
    path.write_text(json.dumps(raw))
    assert load_state(worktree).fix.fix.items[0].commit_sha == ""


def _fix(
    *items: ItemOutcome, commit_sha="", commit_status=None, head_sha="", **kwargs,
) -> FixSummary:
    """A comment fix pass carrying one record, spelled as the domain now holds it.

    Same shape as `test_review_threads._fix`: the record's envelope fields stay
    keywords rather than a nested `FixRecord` literal, so a caller that only
    cares about the outcomes names none of them and one that names a commit is
    making a point about the commit, not about which object holds it.
    """
    return FixSummary(
        fix=FixRecord(
            items=list(items), commit_sha=commit_sha,
            commit_status=commit_status, head_sha=head_sha,
        ),
        **kwargs,
    )


def test_legacy_thread_id_key_loads_as_id():
    """Outcomes written before the field was renamed carry `thread_id`."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    d = state_to_dict(state)
    d["fix"] = {"threads": [{"thread_id": "t1", "file": "f.go", "action": "fixed"}]}
    restored = state_from_dict(d)
    assert restored.fix.fix.items[0].id == "t1"
    assert restored.fix.fix.items[0].file == "f.go"


def test_the_thread_id_rename_does_not_mutate_the_caller():
    """The old reader popped `thread_id` out of the dict it was handed."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    d = state_to_dict(state)
    incoming = {"threads": [{"thread_id": "t1"}]}
    d["fix"] = incoming
    state_from_dict(d)
    assert incoming["threads"][0] == {"thread_id": "t1"}


def test_accumulated_outcomes_keep_their_own_shas():
    """The whole point: round two must not relabel round one's commit."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", commit_sha="1111111", outcome=FixOutcome.FIXED),
        commit_sha="1111111",
    ))
    apply(state, _fix(
        ItemOutcome(id="t2", commit_sha="2222222", outcome=FixOutcome.FIXED),
        commit_sha="2222222",
    ))
    by_id = {o.id: o.commit_sha for o in state.fix.fix.items}
    assert by_id == {"t1": "1111111", "t2": "2222222"}


def test_pr_state_has_fix_field():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.fix.fix == FixRecord()


def test_apply_fix_replaces_scalar_fields():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.FIXED),
        commit_sha="abc", commit_status=CommitStatus.PUSHED,
        updated_at="t1",
    ))
    assert state.fix.fix.commit_sha == "abc"
    assert len(state.fix.fix.items) == 1
    assert state.fix.fix.items[0].outcome == FixOutcome.FIXED
    apply(state, _fix(
        commit_sha="", commit_status=CommitStatus.NO_CHANGES,
        updated_at="t2",
    ))
    assert state.fix.fix.commit_sha == ""
    assert state.fix.fix.commit_status == CommitStatus.NO_CHANGES
    assert state.fix.updated_at == "t2"


def test_apply_fix_accumulates_outcomes_across_rounds():
    """A later pass must not drop the items an earlier one recorded."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.FIXED),
        ItemOutcome(id="t2", outcome=FixOutcome.DISMISSED),
        commit_sha="abc", commit_status=CommitStatus.PUSHED,
        updated_at="t1",
    ))
    apply(state, _fix(
        ItemOutcome(id="t3", outcome=FixOutcome.ALREADY_ADDRESSED),
        commit_status=CommitStatus.NO_CHANGES,
        updated_at="t2",
    ))
    assert [o.id for o in state.fix.fix.items] == ["t1", "t2", "t3"]
    assert state.fix.fix.items[2].outcome == FixOutcome.ALREADY_ADDRESSED


def test_apply_fix_supersedes_the_same_item():
    """Re-processing an item replaces its earlier outcome rather than duplicating it."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED, reason="too complex"),
        updated_at="t1",
    ))
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.FIXED),
        commit_sha="abc", commit_status=CommitStatus.PUSHED,
        updated_at="t2",
    ))
    assert len(state.fix.fix.items) == 1
    assert state.fix.fix.items[0].outcome == FixOutcome.FIXED
    assert state.fix.fix.items[0].reason == ""


def test_apply_fix_does_not_mutate_caller_summary():
    """The merged list is a new object — the caller's FixSummary stays untouched."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(ItemOutcome(id="t1", outcome=FixOutcome.FIXED)))
    incoming = _fix(ItemOutcome(id="t2", outcome=FixOutcome.DISMISSED))
    apply(state, incoming)
    assert [o.id for o in incoming.fix.items] == ["t2"]
    assert [o.id for o in state.fix.fix.items] == ["t1", "t2"]


def test_apply_fix_accumulates_the_reviewers_beside_the_outcomes():
    """The login a reply is addressed to outlives the round that recorded it.

    It rides beside the record rather than on the outcome, so nothing in
    `FixRecord.merge_into` carries it across — this is the domain's own fold.
    """
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED),
        reviewers={"t1": "alice"}, updated_at="t1",
    ))
    apply(state, _fix(
        ItemOutcome(id="t2", outcome=FixOutcome.FIXED),
        reviewers={"t2": "bob"}, updated_at="t2",
    ))
    assert state.fix.reviewers == {"t1": "alice", "t2": "bob"}


def test_state_roundtrip_with_fix_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(
            id="t1", file="src/foo.go", line=10,
            summary="fix the thing", outcome=FixOutcome.FIXED,
        ),
        ItemOutcome(
            id="t2", file="src/bar.go", line=20,
            summary="add validation",
            outcome=FixOutcome.DEFERRED, reason="agent could not auto-fix",
        ),
        ItemOutcome(
            id="t3", file="src/baz.go", line=30,
            summary="needs design",
            outcome=FixOutcome.NEEDS_HUMAN, reason="contested",
        ),
        commit_sha="abc1234", commit_status=CommitStatus.PUSHED,
        reviewers={"t1": "alice", "t2": "bob", "t3": "charlie"},
        replies_posted=2, summary_url="https://github.com/r/p/issues/1#comment",
        deferred_issue_id="ENG-456",
        deferred_issue_url="https://linear.app/team/issue/ENG-456/slug",
        updated_at="2026-07-14T00:00:00+00:00",
    ))

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert len(restored.fix.fix.items) == 3
    assert restored.fix.fix.items[0].id == "t1"
    assert restored.fix.fix.items[0].outcome == FixOutcome.FIXED
    assert restored.fix.fix.items[0].file == "src/foo.go"
    assert restored.fix.fix.items[1].outcome == FixOutcome.DEFERRED
    assert restored.fix.fix.items[1].reason == "agent could not auto-fix"
    assert restored.fix.fix.items[2].outcome == FixOutcome.NEEDS_HUMAN
    assert restored.fix.fix.commit_sha == "abc1234"
    assert restored.fix.fix.commit_status == CommitStatus.PUSHED
    assert restored.fix.reviewers == {"t1": "alice", "t2": "bob", "t3": "charlie"}
    assert restored.fix.replies_posted == 2
    assert restored.fix.deferred_issue_id == "ENG-456"
    assert restored.fix.summary_url == "https://github.com/r/p/issues/1#comment"
    assert restored.fix.deferred_issue_url == "https://linear.app/team/issue/ENG-456/slug"


def test_save_preserves_fix_data(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, _fix(
        ItemOutcome(id="t1", file="a.go", outcome=FixOutcome.FIXED),
        ItemOutcome(id="t2", file="b.go", outcome=FixOutcome.DISMISSED, reason="invalid"),
        commit_sha="def456", commit_status=CommitStatus.PUSHED,
        replies_posted=1,
        updated_at="2026-07-14T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert len(loaded.fix.fix.items) == 2
    assert loaded.fix.fix.items[0].outcome == FixOutcome.FIXED
    assert loaded.fix.fix.items[1].reason == "invalid"
    assert loaded.fix.fix.commit_sha == "def456"


def test_already_addressed_outcome_roundtrips(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, _fix(
        ItemOutcome(
            id="t1", file="a.go", outcome=FixOutcome.ALREADY_ADDRESSED,
            reason="the constructor already injects the logger",
        ),
        commit_status=CommitStatus.NO_CHANGES,
        updated_at="2026-07-14T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.fix.fix.items[0].outcome == FixOutcome.ALREADY_ADDRESSED


def test_load_state_without_fix_defaults_empty(worktree):
    """Old state files without fix key should deserialize with empty FixSummary."""
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    save_state(worktree, state)
    path = worktree / "state.json"
    data = json.loads(path.read_text())
    del data["fix"]
    path.write_text(json.dumps(data))
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.fix.fix == FixRecord()


def test_apply_state_update_fix(worktree):
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="fix",
        data={
            "fix": {
                "items": [{"id": "t1", "file": "f.go", "outcome": "fixed"}],
                "commit_sha": "xyz", "commit_status": "pushed",
            },
            "updated_at": "t",
        },
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.fix.fix.commit_sha == "xyz"
    assert len(loaded.fix.fix.items) == 1
    assert loaded.fix.fix.items[0].outcome == FixOutcome.FIXED


def test_apply_state_update_migrates_a_pre_fold_payload(worktree):
    """The dict route reaches `_from_raw` too, or a legacy round applies as empty.

    `serde` honours the hook on nested fields only, so this domain gets it from
    `apply_state_update` itself. Without that, `threads` is an unrecognised key
    that `from_dict` drops in silence — the update lands, reports nothing wrong,
    and carries none of the outcomes it was handed.
    """
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="fix",
        data={
            "threads": [
                {"thread_id": "t1", "file": "f.go", "reviewer": "alice",
                 "action": "fixed"},
            ],
            "commit_sha": "xyz", "commit_status": "pushed",
            "updated_at": "t",
        },
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.fix.fix.commit_sha == "xyz"
    assert loaded.fix.fix.commit_status == CommitStatus.PUSHED
    assert [o.id for o in loaded.fix.fix.items] == ["t1"]
    assert loaded.fix.fix.items[0].outcome == FixOutcome.FIXED
    assert loaded.fix.reviewers == {"t1": "alice"}


def test_apply_state_update_leaves_a_hookless_domain_alone(worktree):
    """Only a domain owning its reconstruction is built through the hook."""
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="review",
        data={"verdict": "approve", "updated_at": "t"},
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.review.verdict == "approve"


def test_apply_fix_preserves_deferred_issue_across_rounds():
    """A later round must not clear the tracking issue, or it opens a duplicate."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED),
        deferred_issue_id="ENG-456",
        deferred_issue_url="https://linear.app/team/issue/ENG-456",
        updated_at="t1",
    ))
    apply(state, _fix(
        ItemOutcome(id="t2", outcome=FixOutcome.FIXED),
        commit_sha="abc", commit_status=CommitStatus.PUSHED,
        updated_at="t2",
    ))
    assert state.fix.deferred_issue_id == "ENG-456"
    assert state.fix.deferred_issue_url == "https://linear.app/team/issue/ENG-456"


def test_apply_fix_preserves_an_unfiled_deferred_issue_across_rounds():
    """Only the phase that files the issue settles the debt, not the next pass."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED),
        deferred_issue_pending=True, updated_at="t1",
    ))
    apply(state, _fix(
        ItemOutcome(id="t2", outcome=FixOutcome.FIXED),
        commit_sha="abc", commit_status=CommitStatus.PUSHED,
        updated_at="t2",
    ))
    assert state.fix.deferred_issue_pending is True


def test_apply_fix_replaces_deferred_issue_when_supplied():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(deferred_issue_id="ENG-1", deferred_issue_url="u1"))
    apply(state, FixSummary(deferred_issue_id="ENG-2", deferred_issue_url="u2"))
    assert state.fix.deferred_issue_id == "ENG-2"
    assert state.fix.deferred_issue_url == "u2"


def test_apply_fix_preserves_summary_url_across_quiet_rounds():
    """A round that posts nothing must not erase the summary already on the PR."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, _fix(
        ItemOutcome(id="t1", outcome=FixOutcome.FIXED),
        commit_sha="abc", commit_status=CommitStatus.PUSHED,
        summary_url="https://github.com/r/p/pull/1#issuecomment-1",
        updated_at="t1",
    ))
    apply(state, _fix(
        commit_status=CommitStatus.NO_CHANGES, updated_at="t2",
    ))
    assert state.fix.summary_url == "https://github.com/r/p/pull/1#issuecomment-1"
    assert state.fix.summary_deferred is False
    assert [o.id for o in state.fix.fix.items] == ["t1"]


def test_apply_fix_replaces_summary_url_when_supplied():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(summary_url="u1"))
    apply(state, FixSummary(summary_url="u2"))
    assert state.fix.summary_url == "u2"


# ── The pre-fold comment domain ─────────────────────────────────────────────
#
# `FixSummary` used to hold `threads: list[ThreadOutcome]` beside its own
# `commit_sha`/`commit_status`/`head_sha`, where it now carries a `FixRecord`
# like every other domain. State files on disk still hold the old shape, and
# what they report after loading has to be what they reported before — so these
# read one through the three surfaces the fold has to be right at: `pr status`
# (`render_status`), `pr comments --finish` (`readiness`), and `closeout_debt`.

_PRE_FOLD_STATE = {
    "version": STATE_VERSION,
    "identity": {
        "repo": "owner/repo", "branch": "feat", "pr_number": 42,
        "head_sha": "abc1234", "worktree_root": "/wt",
    },
    "fix": {
        "threads": [
            {"thread_id": "t1", "file": "a.go", "line": 10, "reviewer": "alice",
             "summary": "rename it", "action": "fixed", "commit_sha": "abc1234",
             "read_sha": "abc1234"},
            {"thread_id": "t2", "file": "b.go", "line": 20, "reviewer": "bob",
             "summary": "add validation", "action": "deferred",
             "reason": "agent could not auto-fix"},
            {"thread_id": "t3", "file": "c.go", "line": 30, "reviewer": "carol",
             "summary": "wrong premise", "action": "dismissed", "reason": "invalid"},
            {"thread_id": "t4", "file": "d.go", "line": 40, "reviewer": "dave",
             "summary": "needs design", "action": "needs_human", "reason": "contested"},
            {"thread_id": "t5", "file": "e.go", "line": 50, "reviewer": "erin",
             "summary": "guard the nil", "action": "already_addressed",
             "reason": "the constructor already does"},
        ],
        "commit_sha": "abc1234",
        "commit_status": "pushed",
        "head_sha": "abc1234",
        "replies_pending": True,
        "summary_deferred": True,
        "updated_at": "2026-07-14T00:00:00+00:00",
    },
}


def _loaded_pre_fold(**fix_overrides) -> FixSummary:
    raw = json.loads(json.dumps(_PRE_FOLD_STATE))
    raw["fix"].update(fix_overrides)
    return state_from_dict(raw).fix


def test_a_pre_fold_state_file_lands_its_threads_on_the_record():
    fix = _loaded_pre_fold()
    assert [(o.id, o.outcome) for o in fix.fix.items] == [
        ("t1", FixOutcome.FIXED),
        ("t2", FixOutcome.DEFERRED),
        ("t3", FixOutcome.DISMISSED),
        ("t4", FixOutcome.NEEDS_HUMAN),
        ("t5", FixOutcome.ALREADY_ADDRESSED),
    ]
    assert fix.fix.items[1].reason == "agent could not auto-fix"
    assert fix.fix.items[0].commit_sha == "abc1234"
    assert fix.fix.items[0].read_sha == "abc1234"


def test_a_pre_fold_state_file_lands_its_envelope_on_the_record():
    fix = _loaded_pre_fold()
    assert fix.fix.commit_sha == "abc1234"
    assert fix.fix.commit_status == CommitStatus.PUSHED
    assert fix.fix.head_sha == "abc1234"
    assert fix.fix.updated_at == "2026-07-14T00:00:00+00:00"


def test_a_pre_fold_state_file_keeps_its_reviewers():
    """The one field with no counterpart on the record, so it moves rather than folds."""
    assert _loaded_pre_fold().reviewers == {
        "t1": "alice", "t2": "bob", "t3": "carol", "t4": "dave", "t5": "erin",
    }


def test_a_pre_fold_state_file_reports_the_same_status_line():
    """`pr status`, which reads the counts off whatever holds the outcomes."""
    line = _loaded_pre_fold().render_status()[0]
    assert "**1 fixed**" in line
    assert "1 deferred" in line
    assert "1 dismissed" in line
    assert "1 need discussion" in line
    assert "1 already addressed" in line
    assert "abc1234" in line
    assert "pushed" in line


def test_a_pre_fold_state_file_still_owes_the_closeout_it_owed():
    """`closeout_debt` — the three reply-owing outcomes, not the two that owe none."""
    debt = _loaded_pre_fold().closeout_debt()
    assert debt.owed is True
    assert debt.summary is True
    assert debt.replies is True
    assert debt.reply_count == 3


def test_a_pre_fold_state_file_still_blocks_the_merge_it_blocked():
    """`pr comments --finish` — readiness reads the same undelivered closeout."""
    answer = _loaded_pre_fold().readiness(readiness_state())
    assert answer.blockers == (
        f"closeout not delivered (run: {CLOSEOUT_COMMAND})",
    )


def test_a_pre_fold_state_file_with_an_unrun_pass_still_loads():
    """The empty status is the load hazard.

    `CommitStatus("")` raises, and `serde.load_file` answers a raising load by
    discarding the whole file — so a domain that never ran would take every
    other domain's state down with it.
    """
    fix = _loaded_pre_fold(threads=[], commit_sha="", commit_status="", head_sha="")
    assert fix.fix.commit_status is None
    assert fix.fix.items == []
    assert fix.render_status()[0].startswith("**Fix**:")


def test_a_post_fold_state_file_is_left_alone():
    """The migration reads the old keys; a file without them is not rewritten."""
    raw = json.loads(json.dumps(_PRE_FOLD_STATE))
    raw["fix"] = {
        "fix": {
            "items": [{"id": "t1", "outcome": "fixed"}],
            "commit_sha": "def5678", "commit_status": "push_held",
        },
        "reviewers": {"t1": "alice"},
        "updated_at": "2026-07-14T00:00:00+00:00",
    }
    fix = state_from_dict(raw).fix
    assert fix.fix.commit_sha == "def5678"
    assert fix.fix.commit_status == CommitStatus.PUSH_HELD
    assert fix.reviewers == {"t1": "alice"}


def test_a_pre_fold_state_file_survives_a_round_trip(worktree):
    """Loaded once and saved back, it is the folded shape and reports the same."""
    path = worktree / "state.json"
    path.write_text(json.dumps(_PRE_FOLD_STATE))
    save_state(worktree, load_state(worktree))

    raw = json.loads(path.read_text())
    assert "threads" not in raw["fix"]
    assert [o["id"] for o in raw["fix"]["fix"]["items"]] == ["t1", "t2", "t3", "t4", "t5"]

    reloaded = load_state(worktree).fix
    assert reloaded.render_status() == _loaded_pre_fold().render_status()
    assert reloaded.closeout_debt() == _loaded_pre_fold().closeout_debt()
