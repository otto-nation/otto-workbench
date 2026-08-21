"""Tests for review and target-state garbage collection."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_state
import review_gc
import run_lock
import workbench_paths
from trail import Trail


class _RecordingTrail:
    """Stands in for a Trail, recording what the prune would have written.

    Mirrors `Trail._make_event`'s context merge — a per-event `context`
    overrides the run's own rather than replacing it — so a test can tell an
    override from a run with no context to override in the first place.
    """

    def __init__(self, context=None):
        self._context = context or {}
        self.summaries = []

    def summary(self, action, detail, *, data=None, context=None):
        self.summaries.append({
            "action": action,
            "detail": detail,
            "data": data,
            "context": {**self._context, **context} if context else self._context,
        })


def _seed_target(base, name="widget-feat-a", **overrides):
    """Create a target dir with a state.json, defaulting to a single open PR."""
    target = base / name
    target.mkdir(parents=True)
    pr_state.save_state(target, pr_state.new_state(
        repo=overrides.pop("repo", "acme/widget"),
        branch=overrides.pop("branch", "feat/a"),
        pr_number=overrides.pop("pr_number", 1),
        head_sha=overrides.pop("head_sha", "sha"),
        worktree_root=overrides.pop("worktree_root", "/wt"),
    ))
    return target


def _closed(state: pr_state.PRCloseState, ended_at: str = "") -> pr_state.PRClosure:
    """A stand-in closure, for tests that patch out the gh call entirely."""
    return pr_state.PRClosure(state, ended_at)


def test_pr_closure_reports_merged_with_its_timestamp(monkeypatch):
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="", stdout=json.dumps({
            "state": "MERGED", "mergedAt": "2026-08-01T12:00:00Z", "closedAt": None,
        })))
    assert review_gc._pr_closure("acme/widget", 7) == pr_state.PRClosure(
        pr_state.PRCloseState.MERGED, "2026-08-01T12:00:00Z")


def test_pr_closure_reports_closed_with_its_timestamp(monkeypatch):
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="", stdout=json.dumps({
            "state": "CLOSED", "mergedAt": None, "closedAt": "2026-08-02T08:00:00Z",
        })))
    assert review_gc._pr_closure("acme/widget", 7) == pr_state.PRClosure(
        pr_state.PRCloseState.CLOSED, "2026-08-02T08:00:00Z")


def test_pr_closure_reads_the_timestamp_field_its_state_names(monkeypatch):
    """A merged PR is dated by `mergedAt` even when `closedAt` is also populated —
    GitHub sets both on a merge, and the enum is what picks between them."""
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="", stdout=json.dumps({
            "state": "MERGED",
            "mergedAt": "2026-08-01T12:00:00Z",
            "closedAt": "2026-08-01T12:00:01Z",
        })))
    assert review_gc._pr_closure("acme/widget", 7).ended_at == "2026-08-01T12:00:00Z"


def test_pr_closure_asks_gh_for_every_field_a_closure_needs(monkeypatch):
    """The --json list is derived from the enum, so a state added there is fetched
    without anyone remembering to widen the query."""
    seen = []

    def _record(cmd, **kw):
        seen.append(cmd)
        return MagicMock(returncode=0, stderr="", stdout=json.dumps({"state": "OPEN"}))

    monkeypatch.setattr("proc.subprocess.run", _record)

    review_gc._pr_closure("acme/widget", 7)

    fields = seen[0][seen[0].index("--json") + 1].split(",")
    assert set(fields) == {"state", "mergedAt", "closedAt"}


def test_pr_closure_treats_a_null_mergedat_as_no_timestamp(monkeypatch):
    """gh's `mergedAt` can still be null in the window right after a merge lands;
    a PR noticed as MERGED then must report an empty string, not the word "None"."""
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="",
        stdout=json.dumps({"state": "MERGED", "mergedAt": None, "closedAt": None})))
    assert review_gc._pr_closure("acme/widget", 7) == pr_state.PRClosure(
        pr_state.PRCloseState.MERGED, "")


def test_pr_closure_warns_when_gh_fails_rather_than_reading_as_open(monkeypatch, capsys):
    """A gh that cannot answer keeps the artifacts, like an open PR does — but the
    scheduled sweep is unattended, so the two must not look the same in the log."""
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=1, stdout="",
        stderr="gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN\nmore\n"))

    assert review_gc._pr_closure("acme/widget", 7) is None

    err = capsys.readouterr().err
    assert "acme/widget#7" in err
    assert "set the GH_TOKEN" in err
    # One line, however many gh wrote — the sweep's log is read as a list of
    # PRs it could not ask about, not as a transcript.
    assert err.strip().count("\n") == 0


def test_pr_closure_warns_with_the_exit_code_when_gh_says_nothing(monkeypatch, capsys):
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=4, stdout="", stderr=""))

    assert review_gc._pr_closure("acme/widget", 7) is None
    assert "exit 4" in capsys.readouterr().err


def test_pr_closure_warns_when_gh_cannot_be_run(monkeypatch, capsys):
    def _boom(*a, **kw):
        raise FileNotFoundError("gh")

    monkeypatch.setattr("proc.subprocess.run", _boom)

    assert review_gc._pr_closure("acme/widget", 7) is None
    assert "acme/widget#7" in capsys.readouterr().err


def test_pr_closure_says_nothing_about_an_open_pr(monkeypatch, capsys):
    """OPEN is a real answer, not a failure to ask — warning on it would put a
    line in the maintenance log for every PR still in flight, every cycle."""
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="",
        stdout=json.dumps({"state": "OPEN", "mergedAt": None, "closedAt": None})))

    assert review_gc._pr_closure("acme/widget", 7) is None
    assert capsys.readouterr().err == ""


def test_pr_closure_warns_when_gh_reports_a_state_it_does_not_know(monkeypatch, capsys):
    """A renamed or added gh state exits 0 and parses cleanly, so it would read as
    "still open" forever and quietly retire the prune."""
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="",
        stdout=json.dumps({"state": "LOCKED", "mergedAt": None, "closedAt": None})))

    assert review_gc._pr_closure("acme/widget", 7) is None
    err = capsys.readouterr().err
    assert "acme/widget#7" in err
    assert "LOCKED" in err


def test_pr_closure_warns_when_gh_omits_the_state_field(monkeypatch, capsys):
    monkeypatch.setattr("proc.subprocess.run", lambda *a, **kw: MagicMock(
        returncode=0, stderr="", stdout=json.dumps({"mergedAt": None})))

    assert review_gc._pr_closure("acme/widget", 7) is None
    assert "no state field" in capsys.readouterr().err


def test_prune_merged_targets_removes_a_merged_prs_dir(tmp_path, monkeypatch):
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 1
    assert not target.exists()


def test_prune_merged_targets_keeps_an_open_pr(tmp_path, monkeypatch):
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_closure", lambda repo, n: None)

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_leaves_a_live_targets_dir_alone(tmp_path, monkeypatch):
    """Removing the target would unlink the inode that run's flock lives on."""
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))

    with run_lock.acquire(target, command="pr review", started="t"):
        os.environ.pop(run_lock.LOCK_ENV, None)
        assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0

    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_skips_our_own_target(tmp_path, monkeypatch):
    """gc runs holding its own target's lock; LOCK_ENV would wave it through."""
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))

    assert review_gc.prune_merged_targets(tmp_path, skip=target, trail=_RecordingTrail()) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_removes_a_target_with_corrupt_state(tmp_path):
    """load_state folds an unparseable file into None; the recovery branch
    that follows must still reclaim the target rather than leaving it stuck."""
    target = tmp_path / "widget-feat-a"
    target.mkdir(parents=True)
    (target / pr_state.STATE_FILE).write_bytes(b"{ not json")

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 1
    assert not target.exists()


def test_prune_merged_targets_keeps_a_target_with_no_pr_number(tmp_path, monkeypatch):
    """A branch that never opened a PR has no liveness signal to ask GitHub
    about, so the guard must skip it before it ever costs budget or a `gh`
    call — not merely leave it unpruned after asking."""
    target = _seed_target(tmp_path, pr_number=None)

    def _fail(repo, n):
        raise AssertionError("must not ask GitHub about a target with no PR")

    monkeypatch.setattr(review_gc, "_pr_closure", _fail)

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_counts_a_partial_prune_failure_as_not_pruned(tmp_path, monkeypatch):
    """An entry that will not unlink leaves the directory there; the count and
    the target's continued existence must agree, or a later sweep whose
    state.json alone went would never revisit it."""
    target = _seed_target(tmp_path)
    (target / "leftover").mkdir()
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert target.exists()
    # The next sweep's glob only finds targets that still have a state.json,
    # so surviving the failure means surviving with that file.
    assert (target / pr_state.STATE_FILE).is_file()


def test_prune_merged_targets_yields_to_a_run_that_arrives_mid_removal(tmp_path, monkeypatch):
    """The window rmtree left open: a run that takes the target after we unlink
    run.lock holds a flock on a fresh inode while we still hold the old one.
    A non-recursive rmdir fails with ENOTEMPTY instead of deleting its state."""
    target = _seed_target(tmp_path)
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))

    real_rmdir = Path.rmdir

    def arriving_run(self):
        (self / run_lock.LOCK_FILE).write_text("{}")
        real_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", arriving_run)

    assert review_gc.prune_merged_targets(tmp_path, trail=_RecordingTrail()) == 0
    assert (target / run_lock.LOCK_FILE).is_file()


def test_prune_one_target_unlinks_the_lock_file_last(tmp_path):
    """Ordering is the whole fix: every other entry goes while the lock we hold
    still pins the inode a contender would have to agree with."""
    target = _seed_target(tmp_path)

    unlinked = []
    real_unlink = Path.unlink

    def recording_unlink(self, **kwargs):
        unlinked.append(self.name)
        real_unlink(self, **kwargs)

    with patch.object(Path, "unlink", recording_unlink):
        assert review_gc._prune_one_target(target) is True

    assert unlinked[-1] == run_lock.LOCK_FILE
    assert pr_state.STATE_FILE in unlinked[:-1]


def test_prune_merged_targets_respects_the_budget(tmp_path, monkeypatch):
    for i in range(5):
        _seed_target(tmp_path, name=f"widget-feat-{i}",
                     branch=f"feat/{i}", pr_number=i + 1)
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))

    assert review_gc.prune_merged_targets(tmp_path, max_files=2, trail=_RecordingTrail()) == 2


def test_merged_target_emits_one_terminal_summary(tmp_path, monkeypatch):
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(
        review_gc, "_pr_closure",
        lambda repo, n: _closed(pr_state.PRCloseState.MERGED, "2026-08-13T09:00:00Z"))
    trail = _RecordingTrail()

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 1

    assert len(trail.summaries) == 1
    event = trail.summaries[0]
    assert event["action"] == pr_state.TERMINAL_SUMMARY_ACTION
    assert event["data"]["outcome"] == "MERGED"
    assert event["data"]["ended_at"] == "2026-08-13T09:00:00Z"


def test_terminal_summary_names_the_pruned_pr_not_the_gc_run(tmp_path, monkeypatch):
    """`otto-log query --pr N` has to find the record that says how N ended,
    not the repo/pr/branch of the `pr gc` invocation that happened to prune it."""
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.CLOSED))
    trail = _RecordingTrail(context={"repo": "org/gc-runner", "pr": 999, "branch": "chore/gc"})

    review_gc.prune_merged_targets(tmp_path, trail=trail)

    ctx = trail.summaries[0]["context"]
    assert set(ctx) == {"repo", "pr", "branch"}
    assert ctx == {"repo": "acme/widget", "pr": 1, "branch": "feat/a"}


def test_open_target_emits_nothing(tmp_path, monkeypatch):
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(review_gc, "_pr_closure", lambda repo, n: None)
    trail = _RecordingTrail()

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 0
    assert trail.summaries == []


def test_a_target_that_fails_to_prune_emits_nothing(tmp_path, monkeypatch):
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(review_gc, "_pr_closure",
                        lambda repo, n: _closed(pr_state.PRCloseState.MERGED))
    monkeypatch.setattr(review_gc, "_prune_one_target", lambda target: False)
    trail = _RecordingTrail()

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 0
    assert trail.summaries == []


def test_terminal_summary_survives_a_real_trail(tmp_path, monkeypatch):
    """`_RecordingTrail` never serializes the payload it captures, so nothing
    else proves `terminal_summary()`'s dict survives `json.dumps` — a field
    that stopped being a plain str/int/dict (an Enum, a Path) would break
    `pr gc` in production against a suite that stayed green everywhere else."""
    _seed_target(tmp_path, "repo-feat-x")
    monkeypatch.setattr(
        review_gc, "_pr_closure",
        lambda repo, n: _closed(pr_state.PRCloseState.MERGED, "2026-08-13T09:00:00Z"))
    trail = Trail.start(script="pr", context={"repo": "acme/other", "pr": 99, "branch": "main"})

    assert review_gc.prune_merged_targets(tmp_path, trail=trail) == 1
    trail.finish()

    events = []
    for path in sorted(workbench_paths.trail_dir().glob("*.jsonl")):
        events += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    outcome = next(e for e in events if e["action"] == pr_state.TERMINAL_SUMMARY_ACTION)
    assert outcome["data"]["outcome"] == "MERGED"
    assert outcome["data"]["ended_at"] == "2026-08-13T09:00:00Z"
    assert outcome["context"] == {"repo": "acme/widget", "pr": 1, "branch": "feat/a"}
