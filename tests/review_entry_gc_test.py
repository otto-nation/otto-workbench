"""Tests for review.gc — garbage collection, pruning merged reviews, stale
intermediates and cleaned_on_success."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import review.gc

# cr and reviews_dir are pytest fixtures: imported by name so tests here can request them.
from review_entry_support import cr, reviews_dir


# ── gc_reviews ─────────────────────────────────────────────────────────────────


def test_gc_removes_orphaned_stale_dirs(cr, reviews_dir):
    orphan = reviews_dir / "test-repo-100"
    orphan.mkdir()
    (orphan / "pipeline.json").write_text("{}")
    (orphan / "group-1.jsonl").write_text("{}")
    for f in orphan.iterdir():
        os.utime(str(f), (1622505600, 1622505600))

    has_review = reviews_dir / "test-repo-200"
    has_review.mkdir()
    (has_review / "review.md").write_text("## Review")
    (has_review / "pipeline.json").write_text("{}")

    review.gc.gc_reviews(reviews_dir)

    assert not orphan.exists()
    assert has_review.exists()


def test_gc_removes_stale_intermediates(cr, reviews_dir):
    d = reviews_dir / "test-repo-300"
    d.mkdir()
    (d / "review.md").write_text("## Summary")
    for f_name in ("group-1.md", "group-1.jsonl", "holistic.md", "holistic.jsonl"):
        p = d / f_name
        p.write_text("{}")
        os.utime(str(p), (1622505600, 1622505600))

    review.gc.gc_reviews(reviews_dir)

    assert (d / "review.md").exists()
    assert not (d / "group-1.md").exists()
    assert not (d / "group-1.jsonl").exists()
    assert not (d / "holistic.md").exists()
    assert not (d / "holistic.jsonl").exists()


def test_gc_removes_stale_logs_for_every_phase(cr, reviews_dir):
    """The log half of the glob is derived from Phase — every phase that
    writes a session log of its own must be collected, not just the ones a
    hand-written list happened to name."""
    d = reviews_dir / "test-repo-320"
    d.mkdir()
    (d / "review.md").write_text("## Summary")
    log_names = ("holistic.jsonl", "scout.jsonl", "group-1.jsonl", "synthesis.jsonl", "disprove.jsonl", "fix.jsonl")
    for f_name in log_names:
        p = d / f_name
        p.write_text("{}")
        os.utime(str(p), (1622505600, 1622505600))

    review.gc.gc_reviews(reviews_dir)

    assert (d / "review.md").exists()
    for f_name in log_names:
        assert not (d / f_name).exists(), f"{f_name} should have been collected"


def test_gc_preserves_recent_intermediates(cr, reviews_dir):
    d = reviews_dir / "test-repo-350"
    d.mkdir()
    (d / "review.md").write_text("## Summary")
    for f_name in ("group-1.md", "group-1.jsonl", "holistic.md", "holistic.jsonl", "synthesis.jsonl"):
        (d / f_name).write_text("{}")

    review.gc.gc_reviews(reviews_dir)

    assert (d / "review.md").exists()
    for f_name in ("group-1.md", "group-1.jsonl", "holistic.md", "holistic.jsonl", "synthesis.jsonl"):
        assert (d / f_name).exists()


def test_gc_preserves_active_pipeline(cr, reviews_dir):
    d = reviews_dir / "test-repo-400"
    d.mkdir()
    (d / "pipeline.json").write_text("{}")
    (d / "group-1.jsonl").write_text("{}")

    review.gc.gc_reviews(reviews_dir)

    assert d.exists()
    assert (d / "group-1.jsonl").exists()


# ── stray files at the reviews root ──────────────────────────────────────────

STALE_MTIME = (1622505600, 1622505600)


def test_gc_removes_stale_stray_files(cr, reviews_dir):
    strays = ("check_hunks.py", "backfill_pr842.sql", "earning_pr829.go")
    for name in strays:
        p = reviews_dir / name
        p.write_text("scratch")
        os.utime(str(p), STALE_MTIME)

    cleaned = review.gc.gc_reviews(reviews_dir)

    assert cleaned == len(strays)
    for name in strays:
        assert not (reviews_dir / name).exists()


def test_gc_removes_stranded_flat_artifacts(cr, reviews_dir):
    """Suffixed leftovers from the flat layout whose `.md` is gone are unclaimable."""
    stranded = reviews_dir / "maximum-1403.holistic.jsonl"
    stranded.write_text("{}")
    os.utime(str(stranded), STALE_MTIME)

    review.gc.gc_reviews(reviews_dir)

    assert not stranded.exists()


def test_gc_keeps_flat_artifacts_the_migration_still_claims(cr, reviews_dir):
    """A flat `.md` at the root means the startup migration owns its siblings."""
    for name in ("maximum-1403.md", "maximum-1403.holistic.jsonl"):
        p = reviews_dir / name
        p.write_text("{}")
        os.utime(str(p), STALE_MTIME)

    review.gc.gc_reviews(reviews_dir)

    assert (reviews_dir / "maximum-1403.md").exists()
    assert (reviews_dir / "maximum-1403.holistic.jsonl").exists()


def test_gc_preserves_recent_stray_files(cr, reviews_dir):
    """A stray from a run still in flight is not garbage yet."""
    stray = reviews_dir / "check_hunks.py"
    stray.write_text("scratch")

    review.gc.gc_reviews(reviews_dir)

    assert stray.exists()


# ── prune_merged_reviews ─────────────────────────────────────────────────────


@patch("core.proc.subprocess.run")
def test_prune_removes_merged_pr(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-42"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "session.jsonl").write_text("session data")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "42", "head_sha": "abc",
    }))

    old_time = time.time() - 8 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if "gh" in cmd[0] and "pr" in cmd:
            m.returncode = 0
            m.stdout = json.dumps({
                "state": "MERGED", "mergedAt": "2026-08-01T00:00:00Z", "closedAt": None,
            })
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    review.gc.prune_merged_reviews(reviews_dir)

    assert not d.exists()


@patch("core.proc.subprocess.run")
def test_prune_keeps_open_pr(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-99"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "99", "head_sha": "def",
    }))

    old_time = time.time() - 8 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    def side_effect(cmd, **kwargs):
        m = MagicMock()
        if "gh" in cmd[0] and "pr" in cmd:
            m.returncode = 0
            m.stdout = json.dumps({"state": "OPEN", "mergedAt": None, "closedAt": None})
        else:
            m.returncode = 0
            m.stdout = ""
        return m

    mock_run.side_effect = side_effect
    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists()
    assert (d / "review.md").exists()


@patch("core.proc.subprocess.run")
def test_prune_keeps_recent_merged_pr(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-50"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "50", "head_sha": "abc",
    }))

    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "recently-modified merged review should be retained"
    mock_run.assert_not_called()


@patch("core.proc.subprocess.run")
def test_prune_keeps_recent_failed_review(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-51"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "51", "head_sha": "abc",
    }))
    (d / "pipeline.json").write_text(json.dumps({
        "failed": {"synthesis": "all groups failed"},
    }))

    old_time = time.time() - 15 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    mock_run.side_effect = lambda cmd, **kw: MagicMock(
        returncode=0, stdout=json.dumps({
            "state": "MERGED", "mergedAt": "2026-08-01T00:00:00Z", "closedAt": None,
        }))
    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "failed review within 30-day window should be retained"


@patch("core.proc.subprocess.run")
def test_prune_removes_old_failed_review(mock_run, cr, reviews_dir):
    d = reviews_dir / "my-repo-52"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "pr_number": "52", "head_sha": "abc",
    }))
    (d / "pipeline.json").write_text(json.dumps({
        "failed": {"synthesis": "all groups failed"},
    }))

    old_time = time.time() - 35 * 86400
    for f in d.iterdir():
        os.utime(f, (old_time, old_time))

    mock_run.side_effect = lambda cmd, **kw: MagicMock(
        returncode=0, stdout=json.dumps({
            "state": "MERGED", "mergedAt": "2026-08-01T00:00:00Z", "closedAt": None,
        }))
    review.gc.prune_merged_reviews(reviews_dir)

    assert not d.exists(), "failed review older than 30 days should be pruned"


# ── a self-review, which carries a head ref and no PR number ────────────────


def _seed_self_review(reviews_dir, name, *, head_ref, age_days, repo="org/my-repo"):
    """A self-review directory: a head ref, no PR number, aged past a gate."""
    d = reviews_dir / name
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": repo, "head_ref": head_ref, "head_sha": "abc", "mode": "self",
    }))
    old = time.time() - age_days * 86400
    for f in d.iterdir():
        os.utime(f, (old, old))
    return d


def _pr_list_returning(*states):
    """Stub `gh pr list --head` with one row per state in *states*."""
    rows = json.dumps([{"state": s} for s in states])
    return lambda cmd, **kw: MagicMock(returncode=0, stdout=rows)


@patch("core.proc.subprocess.run")
def test_prune_removes_a_self_review_whose_branch_has_only_merged_prs(
    mock_run, cr, reviews_dir,
):
    d = _seed_self_review(reviews_dir, "my-repo-self-done", head_ref="x/done", age_days=40)
    mock_run.side_effect = _pr_list_returning("MERGED")

    review.gc.prune_merged_reviews(reviews_dir)

    assert not d.exists(), "a branch whose every PR has ended leaves nothing to keep"


@patch("core.proc.subprocess.run")
def test_prune_keeps_a_self_review_whose_branch_never_opened_a_pr(
    mock_run, cr, reviews_dir,
):
    """The safety case: no PR history reads the same as not yet pushed.

    A self-review runs before the PR exists, so "this branch has no PR" is the
    ordinary state of live work rather than evidence the work is over.
    """
    d = _seed_self_review(reviews_dir, "my-repo-self-wip", head_ref="x/wip", age_days=400)
    mock_run.side_effect = _pr_list_returning()

    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "a branch with no PR history is indistinguishable from unpushed work"


@patch("core.proc.subprocess.run")
def test_prune_keeps_a_self_review_whose_branch_still_has_an_open_pr(
    mock_run, cr, reviews_dir,
):
    d = _seed_self_review(reviews_dir, "my-repo-self-open", head_ref="x/open", age_days=40)
    mock_run.side_effect = _pr_list_returning("MERGED", "OPEN")

    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "a reopened branch is live however many earlier PRs ended"


@patch("core.proc.subprocess.run")
def test_prune_keeps_a_self_review_inside_the_unlinked_window(mock_run, cr, reviews_dir):
    """A PR-less review gets 30 days, not the 7 a PR-attributed one gets."""
    d = _seed_self_review(reviews_dir, "my-repo-self-recent", head_ref="x/recent", age_days=10)

    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "10 days is inside the unlinked window"
    # The age gate answers before anything is asked.
    mock_run.assert_not_called()


@patch("core.proc.subprocess.run")
def test_prune_keeps_a_self_review_when_gh_cannot_be_asked(mock_run, cr, reviews_dir):
    d = _seed_self_review(reviews_dir, "my-repo-self-offline", head_ref="x/offline", age_days=40)
    mock_run.side_effect = lambda cmd, **kw: MagicMock(returncode=1, stdout="", stderr="boom")

    review.gc.prune_merged_reviews(reviews_dir)

    assert d.exists(), "a question we could not ask keeps the artifacts"


@patch("core.proc.subprocess.run")
def test_prune_asks_about_the_head_ref_including_closed_prs(mock_run, cr, reviews_dir):
    """The query must name the ref and span every state.

    `--state all` is load-bearing: the default lists open PRs only, so a merged
    branch would come back with no rows and read as "never opened a PR" — the
    one answer that is never collected.
    """
    _seed_self_review(reviews_dir, "my-repo-self-q", head_ref="x/asked", age_days=40)
    mock_run.side_effect = _pr_list_returning("MERGED")

    review.gc.prune_merged_reviews(reviews_dir)

    cmd = mock_run.call_args[0][0]
    assert "--head" in cmd and "x/asked" in cmd
    assert cmd[cmd.index("--state") + 1] == "all"


@patch("core.proc.subprocess.run")
def test_prune_asks_an_enterprise_host_by_name(mock_run, cr, reviews_dir):
    """A bare OWNER/REPO resolves against gh's default host, not the review's.

    On a machine logged into an enterprise instance and github.com, that reads
    the wrong instance — and a same-named public repo answering instead is the
    dangerous case here, because this sweep deletes on what it is told.
    """
    d = reviews_dir / "ent-self"
    d.mkdir()
    (d / "review.md").write_text("review content")
    (d / "meta.json").write_text(json.dumps({
        "repo": "org/my-repo", "host": "ghe.example.com",
        "head_ref": "x/ent", "mode": "self",
    }))
    old = time.time() - 40 * 86400
    for f in d.iterdir():
        os.utime(f, (old, old))
    mock_run.side_effect = _pr_list_returning("MERGED")

    review.gc.prune_merged_reviews(reviews_dir)

    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("--repo") + 1] == "ghe.example.com/org/my-repo"


@patch("core.proc.subprocess.run")
def test_prune_leaves_a_public_repo_unqualified(mock_run, cr, reviews_dir):
    """An empty host is public github.com, where the bare form is correct."""
    _seed_self_review(reviews_dir, "pub-self", head_ref="x/pub", age_days=40)
    mock_run.side_effect = _pr_list_returning("MERGED")

    review.gc.prune_merged_reviews(reviews_dir)

    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("--repo") + 1] == "org/my-repo"


# ── _dir_is_all_stale ────────────────────────────────────────────────────────


def test_gc_dir_all_stale(cr, tmp_path):
    d = tmp_path / "stale-dir"
    d.mkdir()
    f = d / "old.jsonl"
    f.write_text("{}")
    os.utime(str(f), (1622505600, 1622505600))
    assert review.gc._dir_is_all_stale(d) is True


def test_gc_dir_has_recent_files(cr, tmp_path):
    d = tmp_path / "mixed-dir"
    d.mkdir()
    old = d / "old.jsonl"
    old.write_text("{}")
    os.utime(str(old), (1622505600, 1622505600))
    (d / "new.jsonl").write_text("{}")
    assert review.gc._dir_is_all_stale(d) is False


def test_gc_dir_empty_and_just_created(cr, tmp_path):
    """The race that killed a review mid-run.

    A run creates its directory and only then writes into it, so a sweep can
    arrive while it holds no files. This used to read as "every file here is
    stale" vacuously and return True, and the caller deleted the directory the
    review was about to write review.md into — the run then died on the missing
    path. `pr gc` runs from scheduled maintenance, so it lands in that window on
    its own schedule rather than only under a concurrent operator.
    """
    d = tmp_path / "empty-dir"
    d.mkdir()
    assert review.gc._dir_is_all_stale(d) is False


def test_gc_dir_empty_and_long_abandoned(cr, tmp_path):
    """The narrowing above does not exempt an empty directory for good.

    One left behind by a run that died before writing anything is real garbage
    once it is old enough, and is what this branch collected before it started
    collecting live ones too.
    """
    d = tmp_path / "abandoned-dir"
    d.mkdir()
    os.utime(str(d), (1622505600, 1622505600))
    assert review.gc._dir_is_all_stale(d) is True


# ── _clean_stale_intermediates ───────────────────────────────────────────────


def test_gc_clean_stale_intermediates_removes_stale(cr, tmp_path):
    d = tmp_path / "review-dir"
    d.mkdir()
    for name in ("group-1.md", "group-1.jsonl", "synthesis.jsonl"):
        f = d / name
        f.write_text("{}")
        os.utime(str(f), (1622505600, 1622505600))
    (d / "meta.json").write_text("{}")

    count = review.gc._clean_stale_intermediates(d)
    assert count == 3
    assert not (d / "group-1.md").exists()
    assert (d / "meta.json").exists()


def test_gc_clean_stale_intermediates_collects_every_phase_artifact(cr, tmp_path):
    # The pattern list used to be hand-copied and never grew scout.md or
    # disprove.md. Derived from Phase, a new phase's artifact is
    # collected without editing review.gc.
    d = tmp_path / "review-dir"
    d.mkdir()
    stale = ("holistic.md", "scout.md", "group-1.md", "disprove.md")
    for name in stale:
        f = d / name
        f.write_text("findings")
        os.utime(str(f), (1622505600, 1622505600))
    keep = d / "review.md"
    keep.write_text("review")
    os.utime(str(keep), (1622505600, 1622505600))

    count = review.gc._clean_stale_intermediates(d)

    assert count == len(stale)
    for name in stale:
        assert not (d / name).exists(), f"{name} was not collected"
    assert keep.exists(), "review.md is the deliverable, not an intermediate"


def test_gc_clean_stale_intermediates_preserves_recent(cr, tmp_path):
    d = tmp_path / "review-dir"
    d.mkdir()
    for name in ("group-1.md", "holistic.jsonl"):
        (d / name).write_text("{}")

    count = review.gc._clean_stale_intermediates(d)
    assert count == 0
    assert (d / "group-1.md").exists()


# ── cleaned_on_success ───────────────────────────────────────────────────────


def _finished_review(tmp_path):
    """A review directory as a run that just finished leaves it."""
    d = tmp_path / "review-dir"
    d.mkdir()
    (d / "review.md").write_text("review")
    (d / "disprove.md").write_text("findings")
    (d / "fix.jsonl").write_text("{}")
    (d / "prompt-single.md").write_text("PROMPT")
    return d


def test_cleaned_on_success_sweeps_after_the_last_phase(cr, tmp_path):
    d = _finished_review(tmp_path)

    with review.gc.cleaned_on_success(d):
        pass

    assert sorted(p.name for p in d.iterdir()) == ["review.md"]


def test_cleaned_on_success_keeps_everything_after_an_exception(cr, tmp_path):
    d = _finished_review(tmp_path)

    with pytest.raises(RuntimeError):
        with review.gc.cleaned_on_success(d):
            raise RuntimeError("phase blew up")

    assert (d / "disprove.md").exists()
    assert (d / "fix.jsonl").exists()


def test_cleaned_on_success_propagates_a_non_zero_exit(cr, tmp_path):
    """`sys.exit(1)` is how a phase reports it produced no review."""
    d = _finished_review(tmp_path)

    with pytest.raises(SystemExit) as exc:
        with review.gc.cleaned_on_success(d):
            sys.exit(1)

    assert exc.value.code == 1
    assert (d / "disprove.md").exists()


def test_cleaned_on_success_keeps_a_partial_run_for_recover(cr, tmp_path):
    d = _finished_review(tmp_path)
    (d / "pipeline.json").write_text(json.dumps({
        "head_sha": "abc123", "group_names": ["a", "b"],
        "done": ["synthesis"],
        "groups_done": [1], "groups_failed": {"2": "agent hit max turns (20)"},
    }))

    with review.gc.cleaned_on_success(d):
        pass

    assert (d / "pipeline.json").exists()
    assert (d / "disprove.md").exists()


def test_cleaned_on_success_survives_a_sweep_that_cannot_delete(cr, tmp_path, capsys, monkeypatch):
    """A tidy-up failure must not take the run's outcome down with it.

    The orchestrator prints the JSON its caller parses after this scope closes,
    so an OSError escaping here would discard a review that succeeded.
    """
    d = _finished_review(tmp_path)

    def _explode(review_dir):
        raise OSError(30, "Read-only file system", str(review_dir / "disprove.md"))

    monkeypatch.setattr(review.gc, "cleanup_intermediates", _explode)

    with review.gc.cleaned_on_success(d):
        pass

    err = capsys.readouterr().err
    assert "could not sweep" in err
    assert "disprove.md" in err
