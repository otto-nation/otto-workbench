"""What a run pins as its head, what it re-reads, and what it does when the tree moves under it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review.collect  # noqa: E402
import review.invoke  # noqa: E402
import review.outcome  # noqa: E402
from conftest import commit_all, git_out, init_repo  # noqa: E402
from core.phases import Mode  # noqa: E402
from gh.types import PRContext, PRMetadata  # noqa: E402
from review.paths import read_review_meta  # noqa: E402
from review.record import ReviewRecord  # noqa: E402
from review.store import StoredReview  # noqa: E402
from review.types import ReviewJob, ReviewMeta  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    wt = init_repo(tmp_path / "wt")
    (wt / "a.py").write_text("x = 1\n")
    (wt / "b.py").write_text("y = 1\n")
    commit_all(wt, "base")
    return wt


def _head(wt: Path) -> str:
    return git_out(wt, "rev-parse", "HEAD").strip()


def _job(wt: Path, tmp_path: Path, *, mode: Mode, head: str, prior_head: str = "",
         prior_dirty=(), files=("a.py", "b.py")) -> ReviewJob:
    job = ReviewJob(
        repo="o/r", pr_number="", ctx=PRContext(), mode=mode,
        pr=PRMetadata("t", "", "feat", "main", head, 1, 0, len(files),
                      [{"path": p} for p in files]),
        wt_path=str(wt), review_file=str(tmp_path / "rev" / "review.md"),
        session_log=str(tmp_path / "rev" / "session.jsonl"),
    )
    if prior_head:
        # `dirty_files` only when a case sets it, so the cases that pass at
        # base construct a `ReviewMeta` base can build.
        recorded = {"dirty_files": tuple(prior_dirty)} if prior_dirty else {}
        job.prior = StoredReview(ReviewRecord(), ReviewMeta(head_sha=prior_head, **recorded))
    return job


def test_a_dirty_tree_on_the_reviewed_head_is_reviewed_incrementally(tmp_path):
    wt = _repo(tmp_path)
    head = _head(wt)
    (wt / "a.py").write_text("x = 2\n")
    job = _job(wt, tmp_path, mode=Mode.SELF, head=head, prior_head=head)
    assert review.collect._prior_sha_for_delta(job) == head
    assert review.collect._collect_delta(job).files == ["a.py"]


# passes-at-base: asserts the clean same-HEAD case keeps the full review it always got
def test_a_clean_tree_on_the_reviewed_head_still_runs_full(tmp_path):
    wt = _repo(tmp_path)
    head = _head(wt)
    job = _job(wt, tmp_path, mode=Mode.SELF, head=head, prior_head=head)
    assert review.collect._prior_sha_for_delta(job) == ""


# passes-at-base: asserts PR mode keeps ignoring the working tree, as it always did
def test_pr_mode_ignores_the_working_tree(tmp_path):
    wt = _repo(tmp_path)
    head = _head(wt)
    (wt / "a.py").write_text("x = 2\n")
    job = _job(wt, tmp_path, mode=Mode.PR, head=head, prior_head=head)
    assert review.collect._prior_sha_for_delta(job) == ""


# passes-at-base: asserts --force keeps the full same-HEAD review X1 would otherwise replace
def test_force_keeps_a_same_head_review_full(tmp_path):
    wt = _repo(tmp_path)
    head = _head(wt)
    (wt / "a.py").write_text("x = 2\n")
    job = _job(wt, tmp_path, mode=Mode.SELF, head=head, prior_head=head)
    job.full_review = True
    assert review.collect._prior_sha_for_delta(job) == ""


def test_the_argv_carries_full_review(tmp_path):
    common = dict(repo="o/r", pr_number="", review_file=tmp_path / "review.md", wt_path="/wt",
                  target_dir=tmp_path, session_log="/log", bin_dir=Path("/bin"),
                  generator_version="v", mode="self")
    forced = review.invoke.OrchestrateRequest(**common, full_review=True)
    assert "--full-review" in review.invoke.build_argv(forced)
    assert "--full-review" not in review.invoke.build_argv(review.invoke.OrchestrateRequest(**common))


def test_a_reverted_edit_is_reviewed_again(tmp_path):
    """The last round read a.py dirty; it matches HEAD now, so only the record says it moved."""
    wt = _repo(tmp_path)
    head = _head(wt)
    job = _job(wt, tmp_path, mode=Mode.SELF, head=head, prior_head=head, prior_dirty=["a.py"])
    assert review.collect._prior_sha_for_delta(job) == head
    assert review.collect._collect_delta(job).files == ["a.py"]


def test_the_self_delta_lists_files_past_the_diff_budget(tmp_path, monkeypatch):
    """The patch is clipped to the prompt budget; the file list must not be."""
    wt = _repo(tmp_path)
    prior = _head(wt)
    (wt / "a.py").write_text("x = 2\n")
    commit_all(wt, "small")
    (wt / "b.py").write_text("y = 2\n" * 2000)
    (wt / "c.py").write_text("outside the surface\n")
    monkeypatch.setattr(review.collect, "MAX_DELTA_DIFF_BYTES", 2_000)
    job = _job(wt, tmp_path, mode=Mode.SELF, head=_head(wt), prior_head=prior)
    assert review.collect._collect_delta(job).files == ["a.py", "b.py"]


def test_a_rename_lists_both_paths(tmp_path):
    """Rename detection would fold the pair into one `a.py => d.py` entry naming neither file."""
    wt = _repo(tmp_path)
    git_out(wt, "config", "diff.renames", "true")
    head = _head(wt)
    git_out(wt, "mv", "a.py", "d.py")
    job = _job(wt, tmp_path, mode=Mode.SELF, head=head, prior_head=head,
               files=("a.py", "b.py", "d.py"))
    assert review.collect.uncommitted_files(str(wt), head) == ["a.py", "d.py"]
    assert review.collect._collect_delta(job).files == ["a.py", "d.py"]


def test_reconciliation_is_told_the_pinned_head(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(review.outcome, "record_prior_findings",
                        lambda *a, **k: seen.update(k))
    job = _job(tmp_path, tmp_path, mode=Mode.SELF, head="f" * 40)
    review.outcome._reconcile_and_verify(job, ReviewRecord())
    assert seen["head_sha"] == "f" * 40


def test_a_commit_raced_into_the_run_is_recorded(tmp_path, capsys):
    wt = _repo(tmp_path)
    pinned = _head(wt)
    (wt / "c.py").write_text("z = 1\n")
    commit_all(wt, "raced")
    live = _head(wt)
    job = _job(wt, tmp_path, mode=Mode.SELF, head=pinned)
    review.outcome._publish(job, ReviewRecord(summary="s"))
    meta = read_review_meta(Path(job.artifact_dir))
    assert (meta.head_sha, meta.head_moved_to) == (pinned, live)
    assert f"<!-- head_moved_to: {live} -->" in Path(job.review_file).read_text()
    assert "HEAD moved" in capsys.readouterr().err


def test_a_publish_records_what_was_read_uncommitted(tmp_path):
    wt = _repo(tmp_path)
    (wt / "a.py").write_text("x = 2\n")
    job = _job(wt, tmp_path, mode=Mode.SELF, head=_head(wt))
    review.outcome._publish(job, ReviewRecord(summary="s"))
    assert read_review_meta(Path(job.artifact_dir)).dirty_files == ("a.py",)


def test_pr_mode_records_no_race_and_no_dirty_files(tmp_path):
    wt = _repo(tmp_path)
    pinned = _head(wt)
    (wt / "c.py").write_text("z = 1\n")
    commit_all(wt, "local")
    (wt / "a.py").write_text("x = 2\n")
    job = _job(wt, tmp_path, mode=Mode.PR, head=pinned)
    review.outcome._publish(job, ReviewRecord(summary="s"))
    meta = read_review_meta(Path(job.artifact_dir))
    assert (meta.head_moved_to, meta.dirty_files) == ("", ())
