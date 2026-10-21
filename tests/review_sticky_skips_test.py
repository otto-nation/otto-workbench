"""A design call handed to a person stays with them until its file changes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from conftest import git_out  # noqa: E402
import review.fix  # noqa: E402
import review.outcome  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from review.convergence import anchor_key, apply_sticky_skips, blob_of, mark_skips  # noqa: E402
from review.paths import read_review_meta, write_review_meta  # noqa: E402
from review.record import Disposition, ReviewRecord  # noqa: E402
from review.store import read_review, write_review  # noqa: E402
from review.types import ReviewMeta, StickySkip  # noqa: E402
from review_convergence_support import committed_repo, finding, review_job  # noqa: E402
from review_fix_pass_support import _run, _tracking  # noqa: E402


def _repo(tmp_path):
    return committed_repo(tmp_path / "wt", {"a.py": "x = 1\n" * 5, "b.py": "y = 1\n"})


def test_a_live_mark_skips_the_finding_at_its_anchor(tmp_path):
    wt = _repo(tmp_path)
    marks = {anchor_key("a.py", 3): StickySkip(blob_of(wt, "a.py"), "S", "design call")}
    record = ReviewRecord(findings=(finding("S1", "x", path="a.py", line=3),))
    applied = apply_sticky_skips(record, marks, wt)
    (f,) = applied.record.findings
    assert (f.disposition, f.reason) == (Disposition.SKIPPED, "design call")
    assert applied.marks == marks


def test_a_changed_file_drops_the_mark(tmp_path):
    wt = _repo(tmp_path)
    marks = {anchor_key("a.py", 3): StickySkip(blob_of(wt, "a.py"), "S", "design call")}
    (wt / "a.py").write_text("x = 2\n")
    applied = apply_sticky_skips(
        ReviewRecord(findings=(finding("S1", "x", path="a.py", line=3),)), marks, wt)
    assert applied.record.findings[0].disposition is Disposition.OPEN
    assert applied.marks == {}


def test_another_severity_at_the_anchor_is_not_silenced(tmp_path):
    wt = _repo(tmp_path)
    marks = {anchor_key("a.py", 3): StickySkip(blob_of(wt, "a.py"), "N", "taste")}
    applied = apply_sticky_skips(
        ReviewRecord(findings=(finding("M1", "x", path="a.py", line=3),)), marks, wt)
    assert applied.record.findings[0].disposition is Disposition.OPEN


def test_a_publish_applies_a_live_mark(tmp_path):
    wt = _repo(tmp_path)
    job = review_job(wt, tmp_path / "rev", head_sha=git_out(wt, "rev-parse", "HEAD").strip())
    write_review_meta(Path(job.artifact_dir), ReviewMeta(sticky_skips={
        anchor_key("a.py", 3): StickySkip(blob_of(wt, "a.py"), "S", "design call")}))
    review.outcome._publish(job, ReviewRecord(findings=(finding("S1", "x", path="a.py", line=3),)))
    stored = read_review(Path(job.review_file))
    assert stored.record.findings[0].disposition is Disposition.SKIPPED
    assert anchor_key("a.py", 3) in stored.meta.sticky_skips


def test_a_publish_prunes_a_stale_mark(tmp_path):
    wt = _repo(tmp_path)
    job = review_job(wt, tmp_path / "rev", head_sha=git_out(wt, "rev-parse", "HEAD").strip())
    write_review_meta(Path(job.artifact_dir), ReviewMeta(sticky_skips={
        anchor_key("a.py", 3): StickySkip(blob_of(wt, "a.py"), "S", "design call")}))
    (wt / "a.py").write_text("x = 2\n")
    review.outcome._publish(job, ReviewRecord(findings=(finding("S1", "x", path="a.py", line=3),)))
    stored = read_review(Path(job.review_file))
    assert stored.meta.sticky_skips == {}
    assert stored.record.findings[0].disposition is Disposition.OPEN


def test_a_live_mark_does_not_relabel_a_settled_finding(tmp_path):
    wt = _repo(tmp_path)
    marks = {anchor_key("a.py", 3): StickySkip(blob_of(wt, "a.py"), "S", "design call")}
    declined = finding("S1", "x", path="a.py", line=3,
                       disposition=Disposition.DECLINED, reason="no")
    fixed = finding("S2", "y", path="a.py", line=3, disposition=Disposition.FIXED)
    applied = apply_sticky_skips(ReviewRecord(findings=(declined, fixed)), marks, wt)
    assert [f.disposition for f in applied.record.findings] == [
        Disposition.DECLINED, Disposition.FIXED]


def _fix_job(tmp_path, wt, *findings):
    job = review_job(wt, tmp_path / "reviews", head_sha="abc1234", files=["a.py", "b.py"])
    write_review(Path(job.review_file), ReviewRecord(findings=findings), ReviewMeta())
    return job


def test_a_skip_is_recorded_per_anchor(tmp_path):
    wt = _repo(tmp_path)
    job = _fix_job(tmp_path, wt, finding("S1", "x", path="a.py", line=3, checkbox=True))
    _run(job, {"S1": "needs a person — design call"})
    marks = read_review_meta(Path(job.artifact_dir)).sticky_skips
    assert marks == {"a.py:3": StickySkip(blob_of(wt, "a.py"), "S", "design call")}


def test_a_skipped_finding_is_not_handed_to_the_next_pass(tmp_path):
    wt = _repo(tmp_path)
    job = _fix_job(
        tmp_path, wt,
        finding("S1", "x", path="a.py", line=3, checkbox=True,
                disposition=Disposition.SKIPPED, reason="design call"),
        finding("N1", "y", path="b.py", line=1, checkbox=True),
    )
    seen = {}
    _run(job, {"N1": "fixed"}, work=lambda: seen.update(text=_tracking(job).read_text()))
    assert "fix:N1" in seen["text"] and "fix:S1" not in seen["text"]


NEEDS_A_PERSON = [ItemOutcome(id="S1", outcome=FixOutcome.NEEDS_HUMAN, reason="design call")]


def test_mark_skips_leaves_out_an_anchor_the_pass_committed(tmp_path):
    wt = _repo(tmp_path)
    found = {"S1": finding("S1", "x", path="a.py", line=3,
                           disposition=Disposition.SKIPPED).view()}
    assert mark_skips(found, NEEDS_A_PERSON, wt, committed={"a.py"}) == {}
    assert list(mark_skips(found, NEEDS_A_PERSON, wt)) == ["a.py:3"]


def test_mark_skips_leaves_out_a_needs_human_that_stayed_open(tmp_path):
    """An unverified tail from an earlier round: apply_outcomes left it OPEN."""
    wt = _repo(tmp_path)
    found = {"S1": finding(
        "S1", "x *(unverified — no runnable check)*", path="a.py", line=3,
    ).view()}
    assert found["S1"].skipped is False
    assert mark_skips(found, NEEDS_A_PERSON, wt) == {}


def test_no_mark_when_the_pass_committed_the_anchor_file(tmp_path):
    """The file changed in this very pass: what the mark would wait for already happened."""
    wt = _repo(tmp_path)
    job = _fix_job(tmp_path, wt, finding("S1", "x", path="a.py", line=3, checkbox=True))
    write_review_meta(Path(job.artifact_dir), ReviewMeta())
    adapter = review.fix.ReviewFixAdapter(job, [finding("S1", "x", path="a.py", line=3).view()])
    adapter.changed = {"a.py"}
    adapter._persist_sticky_skips(NEEDS_A_PERSON)
    assert read_review_meta(Path(job.artifact_dir)).sticky_skips == {}
