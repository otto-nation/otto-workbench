"""Tests for pr_state library."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import pr_state
from pr_state import (
    PRIdentity, CIDomain, PRCloseState, PRClosure,
    ReviewSummary, ReviewVerdict, ReviewStatus,
    CommentsSummary, TriageSummary, RebaseSummary,
    ThreadAction, ThreadOutcome, FixSummary, CommitStatus,
    PendingComment, PRState, load_state, save_state, new_state, update_identity,
    apply, _domains,
    state_to_dict, state_from_dict,
    load_or_init, apply_state_update,
    STATE_VERSION,
)
from ci_failures import RunState, FailureGroup, FailureItem, FailureKind, Outcome


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
    assert c.seen_issue_comment_ids == []
    assert c.seen_review_body_comment_ids == []


def test_comments_summary_with_seen_ids():
    c = CommentsSummary(seen_issue_comment_ids=[111, 222, 333])
    assert c.seen_issue_comment_ids == [111, 222, 333]


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
    assert restored.comments.seen_issue_comment_ids == []


def test_commit_status_wire_values_are_the_strings_state_files_hold():
    """The enum is for the code. Changing a value breaks every saved state file."""
    assert [s.value for s in CommitStatus] == [
        "pushed", "no_changes", "commit_failed", "push_failed", "push_held",
        "commit_held", "reconciled",
    ]


def test_a_commit_status_survives_a_state_roundtrip_as_a_plain_string():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, FixSummary(commit_status=CommitStatus.COMMIT_HELD))

    restored = state_from_dict(json.loads(json.dumps(state_to_dict(state))))

    assert restored.fix.commit_status == CommitStatus.COMMIT_HELD
    assert restored.fix.commit_status == "commit_held"


def test_a_status_read_from_an_older_state_file_still_compares():
    """Loaded values are plain strings; the code compares them against members."""
    state = state_from_dict({
        "version": STATE_VERSION,
        "identity": {
            "repo": "owner/repo", "branch": "feat", "pr_number": 42,
            "head_sha": "def", "worktree_root": "/wt",
        },
        "fix": {"commit_status": "push_held"},
    })
    assert state.fix.commit_status == CommitStatus.PUSH_HELD


def test_state_roundtrip_with_seen_issue_comment_ids():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, CommentsSummary(
        total_threads=3, by_state={"new": 1, "addressed": 2},
        seen_issue_comment_ids=[111, 222, 333],
        updated_at="2026-07-02T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.comments.seen_issue_comment_ids == [111, 222, 333]


def test_state_roundtrip_with_seen_review_body_comment_ids():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, CommentsSummary(
        total_threads=3, by_state={"new": 1, "addressed": 2},
        seen_review_body_comment_ids=[444, 555, 666],
        updated_at="2026-07-13T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.comments.seen_review_body_comment_ids == [444, 555, 666]


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


# ── File I/O ────────────────────────────────────────────────────────────────


def test_load_state_missing_file():
    result = load_state(Path("/nonexistent/worktree"))
    assert result is None


def test_state_file_sits_directly_in_the_target_dir(tmp_path):
    """No .workbench/ nesting — the target dir is already ours alone."""
    state = new_state(repo="acme/widget", branch="feat/a", pr_number=1,
                      head_sha="sha", worktree_root="/wt")
    save_state(tmp_path, state)
    assert (tmp_path / "state.json").is_file()


def test_save_state_does_not_touch_gitignore(tmp_path):
    """State left the repo, so it has no business editing the repo's files."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules\n")
    state = new_state(repo="acme/widget", branch="feat/a", pr_number=1,
                      head_sha="sha", worktree_root=str(tmp_path))
    save_state(tmp_path, state)
    assert gitignore.read_text() == "node_modules\n"


def test_load_or_init_records_the_worktree_it_ran_from(tmp_path):
    """Identity keeps naming the checkout — ui-code reads that field."""
    state = load_or_init(target_dir=tmp_path, repo="acme/widget", branch="feat/a",
                         pr_number=1, head_sha="sha", worktree_root="/checkouts/feat-a")
    assert state.identity.worktree_root == "/checkouts/feat-a"


def test_load_or_init_repoints_the_worktree_a_later_run_ran_from(tmp_path):
    """The file outlives the checkout that wrote it, so the field has to move.

    A bare-repo run stores "", and review-threads reads exactly this field to
    decide whether a fix commit was pushed. Left stale it reports "Push still
    pending" forever; left pointing at worktree A it inspects the wrong tree.
    """
    save_state(tmp_path, load_or_init(
        target_dir=tmp_path, repo="acme/widget", branch="feat/a",
        pr_number=1, head_sha="sha", worktree_root=""))

    state = load_or_init(target_dir=tmp_path, repo="acme/widget", branch="feat/a",
                         pr_number=1, head_sha="sha2", worktree_root="/checkouts/feat-a")
    assert state.identity.worktree_root == "/checkouts/feat-a"


def test_load_or_init_keeps_a_known_worktree_when_a_bare_run_has_none(tmp_path):
    """A run with nothing to say must not erase what an earlier run knew."""
    save_state(tmp_path, load_or_init(
        target_dir=tmp_path, repo="acme/widget", branch="feat/a",
        pr_number=1, head_sha="sha", worktree_root="/checkouts/feat-a"))

    state = load_or_init(target_dir=tmp_path, repo="acme/widget", branch="feat/a",
                         pr_number=1, head_sha="sha2", worktree_root="")
    assert state.identity.worktree_root == "/checkouts/feat-a"


def test_load_or_init_keeps_a_known_head_sha_when_a_bare_run_has_none(tmp_path):
    """A bare-repo resolve() yields head_sha "", and that must not overwrite.

    review-threads posts `fix.commit_sha or state.identity.head_sha` in the fix
    summary body, so a blanked identity puts an empty SHA in a public comment.
    """
    save_state(tmp_path, load_or_init(
        target_dir=tmp_path, repo="acme/widget", branch="feat/a",
        pr_number=1, head_sha="abc1234", worktree_root="/checkouts/feat-a"))

    state = load_or_init(target_dir=tmp_path, repo="acme/widget", branch="feat/a",
                         pr_number=1, head_sha="", worktree_root="")
    assert state.identity.head_sha == "abc1234"


def _write_raw_state(root: Path, payload) -> Path:
    """Write a state file's bytes directly, bypassing save_state."""
    path = root / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return path


def _state_with_one_run(root: Path) -> dict:
    """A saved state carrying a full CI run, read back as raw JSON."""
    state = new_state("owner/repo", "feat", pr_number=1, head_sha="abc",
                      worktree_root=str(root))
    state.ci.runs[1] = RunState(
        run_id=1, run_number=1, head_sha="abc", status="completed",
        conclusion="failure", fetched_at="2026-08-12T00:00:00+00:00",
        failures={"build": FailureGroup(
            job="build", kind=FailureKind.BUILD,
            items=(FailureItem(
                id="x", annotation="err", file=None, line=None,
                diagnosis=None, fix_sha=None, outcome=None,
            ),),
        )},
    )
    save_state(root, state)
    return json.loads((root / "state.json").read_text())


def test_load_state_returns_none_for_truncated_json(worktree, capsys):
    _write_raw_state(worktree, '{"identity": {"repo": "owner/repo"')

    assert load_state(worktree) is None
    assert "unreadable" in capsys.readouterr().err


def test_load_state_returns_none_without_identity(worktree):
    """identity has no dataclass default, so serde raises TypeError."""
    _write_raw_state(worktree, {"created_at": "2026-08-12T00:00:00+00:00"})

    assert load_state(worktree) is None


def test_load_state_returns_none_for_an_unknown_failure_kind(worktree):
    d = _state_with_one_run(worktree)
    d["ci"]["runs"]["1"]["failures"]["build"]["kind"] = "not-a-kind"
    _write_raw_state(worktree, d)

    assert load_state(worktree) is None


def test_load_state_returns_none_for_a_non_numeric_run_key(worktree):
    """runs is dict[int, RunState]; serde restores the int keys, so a key that
    is not a number is a corrupt file rather than a coercible one."""
    d = _state_with_one_run(worktree)
    d["ci"]["runs"] = {"not-a-run-id": d["ci"]["runs"]["1"]}
    _write_raw_state(worktree, d)

    assert load_state(worktree) is None


def test_a_corrupt_file_is_rebuilt_by_the_next_write(worktree):
    """The recovery a user never has to know about: any writing command loads
    or inits, then saves over the bad file."""
    _write_raw_state(worktree, "{ this is not json")

    state = load_or_init(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=7, head_sha="abc1234",
    )
    save_state(worktree, state)

    reloaded = load_state(worktree)
    assert reloaded is not None
    assert reloaded.identity.pr_number == 7


def test_save_and_load_roundtrip(worktree):
    state = new_state("owner/repo", "main", pr_number=1, head_sha="abc", worktree_root=str(worktree))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.identity.repo == "owner/repo"
    assert loaded.identity.pr_number == 1
    assert loaded.updated_at != ""


def test_save_creates_the_state_directory(worktree):
    """A fresh target dir holds no state.json until the first write."""
    assert not (worktree / "state.json").exists()
    state = new_state("repo", "branch", pr_number=None, head_sha="",
                      worktree_root=str(worktree))
    save_state(worktree, state)
    assert load_state(worktree) is not None


def test_save_preserves_ci_data(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, CIDomain(
        last_run_id=100, conclusion="failure", failure_count=2,
        failure_kinds={"lint": 2}, updated_at="2026-06-20T00:00:00+00:00",
    ))
    save_state(worktree, state)

    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.ci.last_run_id == 100
    assert loaded.ci.failure_count == 2
    assert loaded.ci.failure_kinds == {"lint": 2}


def test_save_preserves_ci_runs(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    run = RunState(
        run_id=200, run_number=3, head_sha="ghi",
        status="completed", conclusion="failure",
        fetched_at="2026-06-18T14:30:00+00:00", failures={},
    )
    state.ci.runs[200] = run
    state.ci.latest_run_id = 200
    save_state(worktree, state)

    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.ci.latest_run_id == 200
    assert 200 in loaded.ci.runs
    assert loaded.ci.runs[200].run_number == 3


def test_run_keys_load_back_as_ints(worktree):
    """JSON has no int keys. serde restores them, so a lookup by
    `latest_run_id` — which is an int — finds its run without a conversion."""
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc",
                      worktree_root=str(worktree))
    state.ci.runs[999] = RunState(
        run_id=999, run_number=1, head_sha="abc", status="completed",
        conclusion="failure", fetched_at="2026-08-12T00:00:00+00:00", failures={},
    )
    state.ci.latest_run_id = 999
    save_state(worktree, state)

    raw = json.loads((worktree / "state.json").read_text())
    assert list(raw["ci"]["runs"]) == ["999"]

    loaded = load_state(worktree)
    assert loaded.ci.runs[loaded.ci.latest_run_id].run_number == 1


def test_save_preserves_triage_data(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, TriageSummary(
        total=8, actionable=3, valid=2, questions=1,
        updated_at="2026-06-20T00:00:00+00:00",
    ))
    save_state(worktree, state)

    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.triage.total == 8
    assert loaded.triage.actionable == 3
    assert loaded.triage.valid == 2
    assert loaded.triage.questions == 1


def test_save_never_exposes_a_truncated_file(worktree, monkeypatch):
    """Regression: save_state used to truncate the target in place, so a
    concurrent reader could load a zero-byte file and die on JSONDecodeError.
    A failed write must leave the previous state readable. The temp file the
    guarantee rests on is serde's now, so that is where the failure is
    injected — this asserts save_state still routes through it."""
    import serde

    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc",
                      worktree_root=str(worktree))
    save_state(worktree, state)

    def _explode(obj, fp, **kwargs):
        fp.write('{"partial":')
        raise OSError("disk full")

    monkeypatch.setattr(serde.json, "dump", _explode)
    with pytest.raises(OSError):
        save_state(worktree, state)

    reloaded = load_state(worktree)
    assert reloaded is not None
    assert reloaded.identity.repo == "owner/repo"


def test_save_leaves_no_temp_files_behind(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc",
                      worktree_root=str(worktree))
    save_state(worktree, state)
    save_state(worktree, state)
    leftovers = list(worktree.glob("*.tmp"))
    assert leftovers == []


def test_save_discards_the_temp_file_when_the_write_fails(worktree, monkeypatch):
    import serde

    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc",
                      worktree_root=str(worktree))
    save_state(worktree, state)

    def _explode(obj, fp, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(serde.json, "dump", _explode)
    with pytest.raises(OSError):
        save_state(worktree, state)

    assert list(worktree.glob("*.tmp")) == []


# ── Updaters ────────────────────────────────────────────────────────────────


def test_update_identity_refreshes_sha():
    state = new_state("repo", "branch", pr_number=None, head_sha="old", worktree_root="/wt")
    update_identity(state, head_sha="new", pr_number=42)
    assert state.identity.head_sha == "new"
    assert state.identity.pr_number == 42


def test_update_identity_preserves_pr_when_none():
    state = new_state("repo", "branch", pr_number=7, head_sha="old", worktree_root="/wt")
    update_identity(state, head_sha="new")
    assert state.identity.pr_number == 7


@pytest.mark.parametrize("name,cls", sorted(_domains().items()))
def test_apply_routes_every_domain_to_its_own_field(name, cls):
    """A domain update reaches the PRState field annotated with its type, and no other.

    Parametrized off the derived registry, so a new domain is covered here the
    day its field lands on PRState. Routing by type is the whole job of the
    registry, and misrouting is silent — the write succeeds, just into the
    wrong field.
    """
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")

    apply(state, cls(updated_at="marker"))

    assert getattr(state, name).updated_at == "marker"
    assert [n for n in _domains() if getattr(state, n).updated_at] == [name]


def test_apply_rejects_a_type_no_field_holds():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    with pytest.raises(ValueError, match="not a PRState domain"):
        apply(state, PendingComment())


def test_apply_replaces_ci_domain():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CIDomain(conclusion="success", updated_at="t1"))
    assert state.ci.conclusion == "success"
    apply(state, CIDomain(conclusion="failure", failure_count=1, updated_at="t2"))
    assert state.ci.conclusion == "failure"
    assert state.ci.failure_count == 1


def test_apply_replaces_review():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, ReviewSummary(verdict=ReviewVerdict.APPROVE.value, updated_at="t1"))
    assert state.review.verdict == ReviewVerdict.APPROVE.value


def test_review_summary_status_default():
    rev = ReviewSummary()
    assert rev.status == ""


def test_review_summary_status_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    apply(state, ReviewSummary(
        verdict=ReviewVerdict.APPROVE.value, status=ReviewStatus.ERROR.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.status == ReviewStatus.ERROR.value


def test_review_summary_status_completed_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    apply(state, ReviewSummary(
        verdict=ReviewVerdict.APPROVE.value, status=ReviewStatus.COMPLETED.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.status == ReviewStatus.COMPLETED.value


def test_review_summary_verdict_disapprove_roundtrip():
    state = new_state("repo", "branch", pr_number=1, head_sha="abc", worktree_root="/wt")
    apply(state, ReviewSummary(
        verdict=ReviewVerdict.DISAPPROVE.value, updated_at="t1",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.review.verdict == ReviewVerdict.DISAPPROVE.value


def test_apply_replaces_comments():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CommentsSummary(total_threads=3, updated_at="t1"))
    assert state.comments.total_threads == 3


def test_apply_comments_with_seen_ids():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, CommentsSummary(
        total_threads=2, seen_issue_comment_ids=[100, 200], updated_at="t1",
    ))
    assert state.comments.seen_issue_comment_ids == [100, 200]
    apply(state, CommentsSummary(
        total_threads=3, seen_issue_comment_ids=[100, 200, 300], updated_at="t2",
    ))
    assert state.comments.seen_issue_comment_ids == [100, 200, 300]


def test_apply_replaces_triage():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, TriageSummary(total=5, actionable=2, updated_at="t1"))
    assert state.triage.total == 5
    apply(state, TriageSummary(total=10, actionable=4, valid=3, updated_at="t2"))
    assert state.triage.total == 10
    assert state.triage.actionable == 4
    assert state.triage.valid == 3


def test_rebase_summary_defaults():
    rb = RebaseSummary()
    assert rb.target_base == ""
    assert rb.commits_replayed == 0
    assert rb.conflicts_resolved == 0
    assert rb.files_resolved == []
    assert rb.files_stale == []
    assert rb.force_pushed is False
    assert rb.updated_at == ""


def test_pr_state_has_rebase_field():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.rebase.target_base == ""
    assert state.rebase.force_pushed is False


def test_apply_replaces_rebase():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="t1",
    ))
    assert state.rebase.target_base == "origin/main"
    assert state.rebase.commits_replayed == 3
    assert state.rebase.conflicts_resolved == 2
    assert state.rebase.files_resolved == ["a.py", "b.py"]
    assert state.rebase.force_pushed is True


def test_state_roundtrip_with_rebase_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=5,
        conflicts_resolved=2, files_resolved=["x.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.rebase.target_base == "origin/main"
    assert restored.rebase.commits_replayed == 5
    assert restored.rebase.conflicts_resolved == 2
    assert restored.rebase.files_resolved == ["x.py"]
    assert restored.rebase.force_pushed is True


def test_state_roundtrip_with_stale_files():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=1,
        conflicts_resolved=1, files_resolved=["pnpm-lock.yaml"],
        files_stale=["pnpm-lock.yaml"],
        force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
    ))
    restored = state_from_dict(state_to_dict(state))
    assert restored.rebase.files_stale == ["pnpm-lock.yaml"]


def test_state_from_dict_without_files_stale():
    """State files written before files_stale existed still load."""
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=1,
        conflicts_resolved=1, files_resolved=["x.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    del d["rebase"]["files_stale"]
    assert state_from_dict(d).rebase.files_stale == []


def test_save_preserves_rebase_data(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=1, files_resolved=["f.py"],
        force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.rebase.target_base == "origin/main"
    assert loaded.rebase.commits_replayed == 3
    assert loaded.rebase.files_resolved == ["f.py"]


def test_save_preserves_seen_issue_comment_ids(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, CommentsSummary(
        total_threads=2, seen_issue_comment_ids=[111, 222],
        updated_at="2026-07-02T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.comments.seen_issue_comment_ids == [111, 222]


def test_load_state_without_seen_ids_defaults_empty(worktree):
    """Old state files without seen_issue_comment_ids should deserialize with []."""
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    save_state(worktree, state)
    path = worktree / "state.json"
    data = json.loads(path.read_text())
    del data["comments"]["seen_issue_comment_ids"]
    path.write_text(json.dumps(data))
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.comments.seen_issue_comment_ids == []


def test_load_state_without_seen_review_body_comment_ids_defaults_empty(worktree):
    """Old state files without seen_review_body_comment_ids should deserialize with []."""
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    save_state(worktree, state)
    path = worktree / "state.json"
    data = json.loads(path.read_text())
    del data["comments"]["seen_review_body_comment_ids"]
    path.write_text(json.dumps(data))
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.comments.seen_review_body_comment_ids == []


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


# ── FixSummary ─────────────────────────────────────────────────────────────


def test_fix_thread_outcome_defaults():
    t = ThreadOutcome()
    assert t.id == ""
    assert t.file == ""
    assert t.line == 0
    assert t.action == ThreadAction.FIXED
    assert t.reason == ""


def test_fix_thread_outcome_from_entry():
    entry = {
        "id": "t1", "file": "src/foo.go", "line": 42,
        "reviewer": "alice", "summary": "fix it", "reasoning": "not applicable",
    }
    t = ThreadOutcome.from_entry(entry, ThreadAction.DISMISSED, reason_key="reasoning")
    assert t.id == "t1"
    assert t.file == "src/foo.go"
    assert t.line == 42
    assert t.action == ThreadAction.DISMISSED
    assert t.reason == "not applicable"


def test_fix_thread_outcome_from_entry_defaults():
    t = ThreadOutcome.from_entry({}, ThreadAction.FIXED)
    assert t.id == ""
    assert t.file == ""
    assert t.action == ThreadAction.FIXED
    assert t.reason == ""


def test_fix_summary_defaults():
    f = FixSummary()
    assert f.threads == []
    assert f.commit_sha == ""
    assert f.commit_status == ""
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
        fix=FixSummary(head_sha="abc1234"),
    )
    save_state(worktree, state)
    assert load_state(worktree).fix.head_sha == "abc1234"


def test_fix_summary_head_sha_defaults_empty_on_legacy_state(worktree):
    """State written before this field must still load."""
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(head_sha="abc1234"),
    )
    save_state(worktree, state)
    path = worktree / "state.json"
    raw = json.loads(path.read_text())
    del raw["fix"]["head_sha"]
    path.write_text(json.dumps(raw))
    assert load_state(worktree).fix.head_sha == ""


def test_thread_outcome_round_trips_commit_sha(worktree):
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(threads=[ThreadOutcome(id="t1", commit_sha="deadbee")]),
    )
    save_state(worktree, state)
    assert load_state(worktree).fix.threads[0].commit_sha == "deadbee"


def test_thread_outcome_commit_sha_defaults_empty_on_legacy_state(worktree):
    """State written before this field must still load."""
    state = PRState(
        identity=PRIdentity(repo="o/r", branch="b", pr_number=1,
                            head_sha="abc1234", worktree_root=str(worktree)),
        fix=FixSummary(threads=[ThreadOutcome(id="t1", commit_sha="deadbee")]),
    )
    save_state(worktree, state)
    path = worktree / "state.json"
    raw = json.loads(path.read_text())
    del raw["fix"]["threads"][0]["commit_sha"]
    path.write_text(json.dumps(raw))
    assert load_state(worktree).fix.threads[0].commit_sha == ""


def test_legacy_thread_id_key_loads_as_id():
    """Outcomes written before the field was renamed carry `thread_id`."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    d = state_to_dict(state)
    d["fix"] = {"threads": [{"thread_id": "t1", "file": "f.go", "action": "fixed"}]}
    restored = state_from_dict(d)
    assert restored.fix.threads[0].id == "t1"
    assert restored.fix.threads[0].file == "f.go"


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
    apply(state, FixSummary(
        commit_sha="1111111",
        threads=[ThreadOutcome(id="t1", commit_sha="1111111",
                               action=ThreadAction.FIXED)],
    ))
    apply(state, FixSummary(
        commit_sha="2222222",
        threads=[ThreadOutcome(id="t2", commit_sha="2222222",
                               action=ThreadAction.FIXED)],
    ))
    by_id = {t.id: t.commit_sha for t in state.fix.threads}
    assert by_id == {"t1": "1111111", "t2": "2222222"}


def test_thread_outcome_from_entry_carries_commit_sha():
    """Both branches of from_entry — attribute objects and raw dicts."""
    entry = SimpleNamespace(
        id="t1", file="a.go", line=7, reviewer="kgn", summary="rename it",
        reason="", commit_sha="deadbee",
    )
    assert ThreadOutcome.from_entry(
        entry, ThreadAction.FIXED).commit_sha == "deadbee"
    assert ThreadOutcome.from_entry(
        {"id": "t1", "commit_sha": "deadbee"}, ThreadAction.FIXED,
    ).commit_sha == "deadbee"


def test_pr_state_has_fix_field():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.fix.threads == []
    assert state.fix.commit_sha == ""


def test_apply_fix_replaces_scalar_fields():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(
        threads=[ThreadOutcome(id="t1", action=ThreadAction.FIXED)],
        commit_sha="abc", commit_status="pushed",
        updated_at="t1",
    ))
    assert state.fix.commit_sha == "abc"
    assert len(state.fix.threads) == 1
    assert state.fix.threads[0].action == ThreadAction.FIXED
    apply(state, FixSummary(
        threads=[], commit_sha="", commit_status="no_changes",
        updated_at="t2",
    ))
    assert state.fix.commit_sha == ""
    assert state.fix.commit_status == "no_changes"
    assert state.fix.updated_at == "t2"


def test_apply_fix_accumulates_threads_across_rounds():
    """A later pass must not drop threads processed in an earlier one."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(
        threads=[
            ThreadOutcome(id="t1", action=ThreadAction.FIXED),
            ThreadOutcome(id="t2", action=ThreadAction.DISMISSED),
        ],
        commit_sha="abc", commit_status="pushed", updated_at="t1",
    ))
    apply(state, FixSummary(
        threads=[ThreadOutcome(id="t3", action=ThreadAction.ALREADY_ADDRESSED)],
        commit_status="no_changes", updated_at="t2",
    ))
    assert [t.id for t in state.fix.threads] == ["t1", "t2", "t3"]
    assert state.fix.threads[2].action == ThreadAction.ALREADY_ADDRESSED


def test_apply_fix_supersedes_same_thread():
    """Re-processing a thread replaces its earlier outcome rather than duplicating it."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(
        threads=[ThreadOutcome(id="t1", action=ThreadAction.DEFERRED, reason="too complex")],
        updated_at="t1",
    ))
    apply(state, FixSummary(
        threads=[ThreadOutcome(id="t1", action=ThreadAction.FIXED)],
        commit_sha="abc", commit_status="pushed", updated_at="t2",
    ))
    assert len(state.fix.threads) == 1
    assert state.fix.threads[0].action == ThreadAction.FIXED
    assert state.fix.threads[0].reason == ""


def test_apply_fix_does_not_mutate_caller_summary():
    """The merged list is a new object — the caller's FixSummary stays untouched."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(threads=[ThreadOutcome(id="t1", action=ThreadAction.FIXED)]))
    incoming = FixSummary(threads=[ThreadOutcome(id="t2", action=ThreadAction.DISMISSED)])
    apply(state, incoming)
    assert [t.id for t in incoming.threads] == ["t2"]
    assert [t.id for t in state.fix.threads] == ["t1", "t2"]


def test_state_roundtrip_with_fix_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    apply(state, FixSummary(
        threads=[
            ThreadOutcome(
                id="t1", file="src/foo.go", line=10,
                reviewer="alice", summary="fix the thing",
                action=ThreadAction.FIXED,
            ),
            ThreadOutcome(
                id="t2", file="src/bar.go", line=20,
                reviewer="bob", summary="add validation",
                action=ThreadAction.DEFERRED, reason="agent could not auto-fix",
            ),
            ThreadOutcome(
                id="t3", file="src/baz.go", line=30,
                reviewer="charlie", summary="needs design",
                action=ThreadAction.NEEDS_HUMAN, reason="contested",
            ),
        ],
        commit_sha="abc1234", commit_status="pushed",
        replies_posted=2, summary_url="https://github.com/r/p/issues/1#comment",
        deferred_issue_id="ENG-456",
        deferred_issue_url="https://linear.app/team/issue/ENG-456/slug",
        updated_at="2026-07-14T00:00:00+00:00",
    ))

    d = state_to_dict(state)
    restored = state_from_dict(d)

    assert len(restored.fix.threads) == 3
    assert restored.fix.threads[0].id == "t1"
    assert restored.fix.threads[0].action == ThreadAction.FIXED
    assert restored.fix.threads[0].file == "src/foo.go"
    assert restored.fix.threads[1].action == ThreadAction.DEFERRED
    assert restored.fix.threads[1].reason == "agent could not auto-fix"
    assert restored.fix.threads[2].action == ThreadAction.NEEDS_HUMAN
    assert restored.fix.commit_sha == "abc1234"
    assert restored.fix.commit_status == "pushed"
    assert restored.fix.replies_posted == 2
    assert restored.fix.deferred_issue_id == "ENG-456"
    assert restored.fix.summary_url == "https://github.com/r/p/issues/1#comment"
    assert restored.fix.deferred_issue_url == "https://linear.app/team/issue/ENG-456/slug"


def test_save_preserves_fix_data(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, FixSummary(
        threads=[
            ThreadOutcome(id="t1", file="a.go", action=ThreadAction.FIXED),
            ThreadOutcome(id="t2", file="b.go", action=ThreadAction.DISMISSED, reason="invalid"),
        ],
        commit_sha="def456", commit_status="pushed",
        replies_posted=1,
        updated_at="2026-07-14T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert len(loaded.fix.threads) == 2
    assert loaded.fix.threads[0].action == ThreadAction.FIXED
    assert loaded.fix.threads[1].reason == "invalid"
    assert loaded.fix.commit_sha == "def456"


def test_already_addressed_action_roundtrips(worktree):
    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=str(worktree))
    apply(state, FixSummary(
        threads=[ThreadOutcome(
            id="t1", file="a.go", action=ThreadAction.ALREADY_ADDRESSED,
            reason="the constructor already injects the logger",
        )],
        commit_status="no_changes",
        updated_at="2026-07-14T00:00:00+00:00",
    ))
    save_state(worktree, state)
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.fix.threads[0].action == ThreadAction.ALREADY_ADDRESSED


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
    assert loaded.fix.threads == []
    assert loaded.fix.commit_sha == ""


def test_apply_state_update_fix(worktree):
    apply_state_update(
        target_dir=worktree, repo="owner/repo", branch="feat",
        pr_number=1, head_sha="abc", domain="fix",
        data={
            "threads": [
                {"thread_id": "t1", "file": "f.go", "action": "fixed"},
            ],
            "commit_sha": "xyz", "commit_status": "pushed",
            "updated_at": "t",
        },
    )
    loaded = load_state(worktree)
    assert loaded is not None
    assert loaded.fix.commit_sha == "xyz"
    assert len(loaded.fix.threads) == 1
    assert loaded.fix.threads[0].action == ThreadAction.FIXED


def test_apply_fix_preserves_deferred_issue_across_rounds():
    """A later round must not clear the tracking issue, or it opens a duplicate."""
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(
        threads=[ThreadOutcome(id="t1", action=ThreadAction.DEFERRED)],
        deferred_issue_id="ENG-456",
        deferred_issue_url="https://linear.app/team/issue/ENG-456",
        updated_at="t1",
    ))
    apply(state, FixSummary(
        threads=[ThreadOutcome(id="t2", action=ThreadAction.FIXED)],
        commit_sha="abc", commit_status="pushed", updated_at="t2",
    ))
    assert state.fix.deferred_issue_id == "ENG-456"
    assert state.fix.deferred_issue_url == "https://linear.app/team/issue/ENG-456"


def test_apply_fix_replaces_deferred_issue_when_supplied():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    apply(state, FixSummary(deferred_issue_id="ENG-1", deferred_issue_url="u1"))
    apply(state, FixSummary(deferred_issue_id="ENG-2", deferred_issue_url="u2"))
    assert state.fix.deferred_issue_id == "ENG-2"
    assert state.fix.deferred_issue_url == "u2"


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
        fields = pr_state.GH_STATE_JSON_FIELDS.split(",")
        assert fields[0] == "state"
        assert set(fields[1:]) == {
            s.ended_at_field for s in PRCloseState if s.is_terminal
        }


class TestTerminalSummary:
    def _state(self) -> pr_state.PRState:
        state = pr_state.PRState(identity=pr_state.PRIdentity(
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
        payload = pr_state.terminal_summary(self._state(), PRClosure(
            PRCloseState.MERGED, "2026-08-13T09:00:00Z"))
        assert payload == {
            "outcome": "MERGED",
            "ended_at": "2026-08-13T09:00:00Z",
            "cost_usd": 4.25,
            "total_tokens": 91_000,
            "verdict": "Request changes",
            "finding_counts": {"must-fix": 2, "nit": 5},
            "rebase_conflicts": 3,
        }

    def test_finding_counts_are_copied_not_aliased(self):
        state = self._state()
        payload = pr_state.terminal_summary(state, PRClosure(PRCloseState.CLOSED))
        state.review.finding_counts["must-fix"] = 99
        assert payload["finding_counts"]["must-fix"] == 2

    def test_the_outcome_is_recorded_as_a_word_not_an_enum(self):
        """The payload is written to the trail as JSON; an Enum would not
        serialize, and `pr gc` reports the failure rather than raising it — so a
        regression here would go out as a warning nobody reads."""
        payload = pr_state.terminal_summary(
            self._state(), PRClosure(PRCloseState.MERGED))
        assert payload["outcome"] == "MERGED"
        assert json.loads(json.dumps(payload)) == payload

    def test_the_action_name_is_published(self):
        assert pr_state.TERMINAL_SUMMARY_ACTION == "pr_outcome"
