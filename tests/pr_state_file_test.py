"""pr.state file I/O: where the state file lives, and how it is read, rebuilt and saved."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from pr.domains import CIDomain, TriageSummary
from pr.state import load_state, save_state, new_state, apply, load_or_init
from pr.ci_failures import RunState, FailureGroup, FailureItem, FailureKind


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
    import core.serde

    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc",
                      worktree_root=str(worktree))
    save_state(worktree, state)

    def _explode(obj, fp, **kwargs):
        fp.write('{"partial":')
        raise OSError("disk full")

    monkeypatch.setattr(core.serde.json, "dump", _explode)
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
    import core.serde

    state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc",
                      worktree_root=str(worktree))
    save_state(worktree, state)

    def _explode(obj, fp, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(core.serde.json, "dump", _explode)
    with pytest.raises(OSError):
        save_state(worktree, state)

    assert list(worktree.glob("*.tmp")) == []
