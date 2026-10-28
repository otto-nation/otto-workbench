"""A fix commit that qualifies counts as reviewed: no re-run is owed for it."""

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import batch.plan  # noqa: E402
import fix.engine  # noqa: E402
import fix.suite  # noqa: E402
import git.land  # noqa: E402
import review.fix  # noqa: E402
import review.outcome  # noqa: E402
from conftest import commit_all, git_out  # noqa: E402
from fix.gate import Verdict  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from review.convergence import fix_coverage  # noqa: E402
from review.paths import read_review_meta  # noqa: E402
from review.record import ReviewRecord  # noqa: E402
from review.store import write_review  # noqa: E402
from review.types import ReviewMeta  # noqa: E402
from review_convergence_support import committed_repo, finding, head, review_job  # noqa: E402
from review_fix_pass_support import _run  # noqa: E402

FIXED_N1 = ItemOutcome(id="N1", outcome=FixOutcome.FIXED, verified=True, file="a.py")


def _history(tmp_path):
    """A repo holding the reviewed commit and one fix commit on top of it."""
    repo = committed_repo(tmp_path / "repo", {"a.py": "x = 1\n", "tests/a_test.py": "t = 1\n"})
    reviewed = head(repo)
    (repo / "a.py").write_text("y = 1\n")
    commit_all(repo, "fix")
    return repo, reviewed, head(repo)


def _run_of(sha, reviewed, **changes):
    run = fix.engine.FixRun(
        outcomes=[FIXED_N1],
        landed=git.land.LandResult(git.land.CommitStatus.PUSH_HELD, sha),
        head_before=reviewed,
        suite=fix.suite.SuiteResult(status=fix.suite.SuiteStatus.GREEN),
    )
    return replace(run, **changes) if changes else run


def test_a_qualifying_commit_is_covered(tmp_path):
    repo, reviewed, fixed = _history(tmp_path)
    coverage = fix_coverage(_run_of(fixed, reviewed), {"a.py", "tests/a_test.py"},
                            {"N1": "a.py"}, reviewed, repo)
    assert coverage.sha == fixed


def test_each_failed_condition_says_why(tmp_path):
    repo, reviewed, fixed = _history(tmp_path)
    unverified = [ItemOutcome(id="N1", outcome=FixOutcome.FIXED, verified=None, file="a.py")]
    cases = {
        "no commit": (_run_of("", reviewed, landed=git.land.LandResult(
            git.land.CommitStatus.NO_CHANGES)), reviewed),
        "parent": (_run_of(fixed, reviewed), "b" * 40),
        "checks": (_run_of(fixed, reviewed, suite=fix.suite.SuiteResult(
            status=fix.suite.SuiteStatus.PARTIAL)), reviewed),
        "verified": (_run_of(fixed, reviewed, outcomes=unverified), reviewed),
    }
    for word, (run, reviewed_through) in cases.items():
        coverage = fix_coverage(run, {"a.py"}, {"N1": "a.py"}, reviewed_through, repo)
        assert not coverage.covered and word in coverage.why, (word, coverage.why)


def test_a_merge_commit_is_not_covered(tmp_path):
    """Its first parent is covered; a second parent is code no review read."""
    repo = committed_repo(tmp_path / "repo", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    # `merge --no-verify` skips `pre-merge-commit` and `commit-msg` but not
    # `prepare-commit-msg`, which the machine's global `core.hooksPath` installs;
    # an empty hooks dir keeps the merge commit this test's own.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(repo, "config", "core.hooksPath", str(hooks))
    git_out(repo, "checkout", "-q", "-b", "side")
    (repo / "b.py").write_text("y = 2\n")
    commit_all(repo, "side")
    git_out(repo, "checkout", "-q", "main")
    reviewed = head(repo)
    git_out(repo, "merge", "-q", "--no-ff", "--no-verify", "-m", "merge", "side")
    coverage = fix_coverage(_run_of(head(repo), reviewed), {"a.py"}, {"N1": "a.py"},
                            reviewed, repo)
    assert not coverage.covered and "single-parent" in coverage.why


def test_a_committed_path_no_fix_names_is_not_covered(tmp_path):
    repo, reviewed, fixed = _history(tmp_path)
    coverage = fix_coverage(_run_of(fixed, reviewed), {"a.py", "b.py"}, {"N1": "a.py"},
                            reviewed, repo)
    assert not coverage.covered and "b.py" in coverage.why


def test_a_committed_anchor_of_an_unfixed_item_is_not_covered(tmp_path):
    """Attribution is by path: an edit in a skipped item's file has no verified fix behind it."""
    repo, reviewed, fixed = _history(tmp_path)
    run = _run_of(fixed, reviewed, outcomes=[
        FIXED_N1, ItemOutcome(id="S1", outcome=FixOutcome.NEEDS_HUMAN, file="a.py")])
    coverage = fix_coverage(run, {"a.py"}, {"N1": "a.py", "S1": "a.py"}, reviewed, repo)
    assert not coverage.covered and "did not fix" in coverage.why


def _pass(tmp_path, verdict, *, hand_commit=False):
    wt = committed_repo(tmp_path / "worktree", {"a.py": "x = 1\n"})
    # Empty hooks dir: the machine's global `core.hooksPath` would otherwise run
    # on the fix pass's own commit, as `review_fix_pass_support.git_wt` notes.
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(wt, "config", "core.hooksPath", str(hooks))
    script = tmp_path / "checks"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    (wt / ".workbench.yml").write_text(f"fix:\n  verify_command: {script}\n  verify_timeout: 60\n")
    commit_all(wt, "config")
    reviewed = head(wt)
    job = review_job(wt, tmp_path / "reviews", head_sha=reviewed, files=["a.py"])
    write_review(Path(job.review_file),
                 ReviewRecord(findings=(finding("N1", "rename", path="a.py", line=1,
                                                checkbox=True),)),
                 ReviewMeta(head_sha=reviewed))

    def work():
        if hand_commit:
            (wt / "b.py").write_text("by hand\n")
            commit_all(wt, "hand commit while the agent worked")
        (wt / "a.py").write_text("y = 1\n")
    _run(job, {"N1": "fixed — renamed, covered by tests/a_test.py"},
         work=work, verdicts={"N1": verdict})
    return wt, job


def test_a_qualifying_pass_records_its_commit_as_covered(tmp_path):
    wt, job = _pass(tmp_path, Verdict(ok=True, detail="ran tests/a_test.py"))
    fixed = head(wt)
    meta = read_review_meta(Path(job.artifact_dir))
    assert meta.covered_sha == fixed and meta.reviewed_through == fixed
    assert f"<!-- covered_sha: {fixed} -->" in Path(job.review_file).read_text()


def test_a_hand_commit_made_while_the_agent_ran_is_not_covered(tmp_path):
    """`head_before` is read before the agent runs; only the commit's own parent cannot be raced."""
    wt, job = _pass(tmp_path, Verdict(ok=True, detail="ran tests/a_test.py"), hand_commit=True)
    meta = read_review_meta(Path(job.artifact_dir))
    assert meta.fix_commit_sha == head(wt)
    assert meta.covered_sha == "" and meta.reviewed_through == meta.head_sha


def test_an_unverified_pass_is_not_covered(tmp_path):
    _wt, job = _pass(tmp_path, Verdict(ok=None, detail="nothing covers it"))
    meta = read_review_meta(Path(job.artifact_dir))
    assert meta.covered_sha == "" and meta.reviewed_through == meta.head_sha


def test_a_recorder_handed_no_adapter_never_covers(tmp_path):
    """Without the adapter there is no commit scope to judge, so a qualifying run is not covered."""
    repo, reviewed, fixed = _history(tmp_path)
    job = review_job(repo, tmp_path / "reviews", head_sha=reviewed, files=["a.py"])
    write_review(Path(job.review_file), ReviewRecord(), ReviewMeta(head_sha=reviewed))
    review.fix._record_commit(job, _run_of(fixed, reviewed))
    meta = read_review_meta(Path(job.artifact_dir))
    assert meta.fix_commit_sha == fixed
    assert meta.covered_sha == "" and meta.reviewed_through == reviewed


def test_review_need_counts_a_covered_fix_commit(tmp_path):
    f = tmp_path / "review.md"
    write_review(f, ReviewRecord(), ReviewMeta(head_sha="1111111", covered_sha="2222222"))
    assert batch.plan.review_need(f, "2222222aaaa").needed is False
    assert batch.plan.review_need(f, "3333333aaaa").needed is True


def test_the_header_shows_covered_sha_only_when_it_differs(tmp_path):
    f = tmp_path / "review.md"
    write_review(f, ReviewRecord(), ReviewMeta(head_sha="1" * 40, covered_sha="1" * 40))
    assert "covered_sha" not in f.read_text()
    write_review(f, ReviewRecord(), ReviewMeta(head_sha="1" * 40, covered_sha="2" * 40))
    assert f"<!-- covered_sha: {'2' * 40} -->" in f.read_text()


def test_publish_resets_covered_sha(tmp_path):
    """A new review does not inherit a prior pass's coverage."""
    repo = committed_repo(tmp_path / "repo", {"a.py": "x = 1\n"})
    reviewed = head(repo)
    covered = "c" * 40
    job = review_job(repo, tmp_path / "reviews", head_sha=reviewed, files=["a.py"])
    write_review(Path(job.review_file), ReviewRecord(summary="s"),
                 ReviewMeta(head_sha=reviewed, covered_sha=covered))
    assert read_review_meta(Path(job.artifact_dir)).covered_sha == covered
    review.outcome._publish(job, ReviewRecord(summary="s"))
    assert read_review_meta(Path(job.artifact_dir)).covered_sha == ""


def test_an_unfixed_item_blocks_coverage_from_both_its_paths(tmp_path):
    """Unfixed S1 tracking c.py / handed d.py: committing c.py is not coverage."""
    repo, reviewed, fixed = _history(tmp_path)
    run = _run_of(fixed, reviewed, outcomes=[
        ItemOutcome(id="N1", outcome=FixOutcome.FIXED, verified=True, file="c.py"),
        ItemOutcome(id="S1", outcome=FixOutcome.NEEDS_HUMAN, file="c.py"),
    ])
    coverage = fix_coverage(run, {"c.py"}, {"N1": "c.py", "S1": "d.py"}, reviewed, repo)
    assert not coverage.covered and "c.py" in coverage.why


def test_an_unfixed_item_with_an_empty_outcome_file_uses_item_files(tmp_path):
    """The handed item's path is the anchor when the outcome names none."""
    repo, reviewed, fixed = _history(tmp_path)
    run = _run_of(fixed, reviewed, outcomes=[
        FIXED_N1, ItemOutcome(id="S1", outcome=FixOutcome.NEEDS_HUMAN, file="")])
    coverage = fix_coverage(run, {"a.py"}, {"N1": "a.py", "S1": "a.py"}, reviewed, repo)
    assert not coverage.covered and "did not fix" in coverage.why


def test_a_rewritten_tracking_heading_does_not_cover_the_item_file(tmp_path):
    """FIXED N1 headed as b.py while the handed item is a.py: committing b.py is not coverage."""
    repo, reviewed, fixed = _history(tmp_path)
    run = _run_of(fixed, reviewed, outcomes=[
        ItemOutcome(id="N1", outcome=FixOutcome.FIXED, verified=True, file="b.py")])
    coverage = fix_coverage(run, {"b.py"}, {"N1": "a.py"}, reviewed, repo)
    assert not coverage.covered and "b.py" in coverage.why


def test_a_deleted_unfixed_section_still_blocks_coverage(tmp_path):
    """M2 in a.py vanished from outcomes; N1 fixed in a.py does not cover the commit."""
    repo, reviewed, fixed = _history(tmp_path)
    coverage = fix_coverage(_run_of(fixed, reviewed), {"a.py"},
                            {"N1": "a.py", "M2": "a.py"}, reviewed, repo)
    assert not coverage.covered and "did not fix" in coverage.why


def test_zero_fixes_says_no_fix_to_vouch_for(tmp_path):
    repo, reviewed, fixed = _history(tmp_path)
    run = _run_of(fixed, reviewed, outcomes=[
        ItemOutcome(id="N1", outcome=FixOutcome.NEEDS_HUMAN, file="a.py")])
    coverage = fix_coverage(run, {"a.py"}, {"N1": "a.py"}, reviewed, repo)
    assert coverage.why == "no fix to vouch for"


def test_unknown_commit_scope_says_so(tmp_path):
    repo, reviewed, fixed = _history(tmp_path)
    coverage = fix_coverage(_run_of(fixed, reviewed), None, {"N1": "a.py"}, reviewed, repo)
    assert coverage.why == "commit scope unknown"


def _coverage_adapter():
    return SimpleNamespace(
        changed={"a.py", "tests/a_test.py"},
        items=lambda: [SimpleNamespace(id="N1", file="a.py")],
    )


def test_a_second_pass_chains_through_reviewed_through(tmp_path):
    """Pass 2 on F1 is covered because the recorder reads reviewed_through, not head_sha."""
    repo, reviewed, f1 = _history(tmp_path)
    job = review_job(repo, tmp_path / "reviews", head_sha=reviewed, files=["a.py"])
    write_review(Path(job.review_file), ReviewRecord(), ReviewMeta(head_sha=reviewed))
    adapter = _coverage_adapter()
    review.fix._record_commit(job, _run_of(f1, reviewed), adapter)
    meta = read_review_meta(Path(job.artifact_dir))
    assert meta.covered_sha == f1 and meta.reviewed_through == f1
    assert meta.head_sha == reviewed

    (repo / "a.py").write_text("z = 1\n")
    commit_all(repo, "fix2")
    f2 = head(repo)
    review.fix._record_commit(job, _run_of(f2, f1), adapter)
    meta = read_review_meta(Path(job.artifact_dir))
    assert meta.covered_sha == f2 and meta.reviewed_through == f2


def test_an_empty_commit_says_nothing_committed(tmp_path):
    repo, reviewed, fixed = _history(tmp_path)
    coverage = fix_coverage(_run_of(fixed, reviewed), set(), {"N1": "a.py"}, reviewed, repo)
    assert coverage.why == "nothing committed"
