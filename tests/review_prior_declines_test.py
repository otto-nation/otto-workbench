"""A prior decline whose file did not change is settled without a ledger line."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.retry  # noqa: E402
import review.outcome  # noqa: E402
import review.phases  # noqa: E402
import review.steps  # noqa: E402
from core.phases import Mode  # noqa: E402
from review.convergence import change_set  # noqa: E402
from review.reconcile import DispositionSource, passed_over, reconcile  # noqa: E402
from review.record import Disposition, ReviewRecord  # noqa: E402
from review.store import StoredReview  # noqa: E402
from review.types import PreflightData, PriorDisposition, ReviewMeta  # noqa: E402
from review.verify import PostProcessed  # noqa: E402
from review_convergence_support import committed_repo, finding, head, merged, review_job  # noqa: E402

PRIOR_HEAD = "p" * 40
DECLINED = finding("S1", "uses a module global", path="a.py", line=3,
                   disposition=Disposition.DECLINED, reason="by design")


def _prior(*findings):
    return StoredReview(ReviewRecord(findings=tuple(findings)), ReviewMeta(head_sha=PRIOR_HEAD))


def _preflight(delta):
    return PreflightData(diff="", commit_log="", file_contents={}, file_permissions={},
                         instructions_md="", architecture_md="",
                         delta_files=list(delta), prior_head_sha=PRIOR_HEAD)


def test_a_decline_on_an_unchanged_file_settles_without_the_ledger():
    (rec,) = reconcile(_prior(DECLINED), ReviewRecord(), changed=frozenset({"b.py"})).records
    assert (rec.disposition, rec.source) == (PriorDisposition.DECLINED, DispositionSource.PRIOR)
    assert rec.source.stated


def test_a_decline_whose_file_changed_is_asked_again():
    (rec,) = reconcile(_prior(DECLINED), ReviewRecord(), changed=frozenset({"a.py"})).records
    assert rec.source is not DispositionSource.PRIOR and not rec.decided


def test_an_unknown_change_set_settles_nothing_from_the_prior():
    (rec,) = reconcile(_prior(DECLINED), ReviewRecord()).records
    assert not rec.decided


def test_an_open_prior_finding_is_not_settled_by_its_own_record():
    (rec,) = reconcile(_prior(finding("S1", "x", path="a.py", line=3)), ReviewRecord(),
                       changed=frozenset()).records
    assert not rec.decided


def test_a_settled_decline_is_not_passed_over():
    assert passed_over(_prior(DECLINED), ReviewRecord(), changed=frozenset()) == []


def test_a_ledger_fixed_outranks_an_untouched_decline():
    review = ReviewRecord(ledger="- **[S1]** `a.py` — Fixed")
    (rec,) = reconcile(_prior(DECLINED), review, changed=frozenset()).records
    assert (rec.disposition, rec.source) == (PriorDisposition.FIXED, DispositionSource.LEDGER)


def test_a_restated_finding_outranks_an_untouched_decline():
    restated = finding("S1", "uses a module global", path="a.py", line=3)
    (rec,) = reconcile(_prior(DECLINED), ReviewRecord(findings=(restated,)),
                       changed=frozenset()).records
    assert (rec.disposition, rec.source) == (
        PriorDisposition.STILL_OPEN, DispositionSource.CARRIED,
    )


def test_change_set_is_the_delta_on_an_incremental_review(tmp_path):
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40,
                     prior=_prior(), preflight=_preflight(["b.py"]))
    assert change_set(job) == frozenset({"b.py"})


def test_change_set_is_empty_when_the_prior_covers_head(tmp_path):
    job = review_job(tmp_path, tmp_path / "r", mode=Mode.PR, head_sha=PRIOR_HEAD, prior=_prior())
    assert change_set(job) == frozenset()


def test_a_self_review_covering_head_includes_the_working_tree(tmp_path):
    repo = committed_repo(tmp_path, {"a.py": "one\n", "b.py": "two\n"})
    sha = head(repo)
    (repo / "a.py").write_text("edited\n")
    prior = StoredReview(ReviewRecord(), ReviewMeta(head_sha=sha, dirty_files=("b.py",)))
    job = review_job(repo, tmp_path / "r", head_sha=sha, prior=prior)
    job.full_review = True
    changed = change_set(job)
    assert "a.py" in changed
    assert "b.py" in changed


def test_change_set_is_unknown_on_a_full_review_with_a_moved_head(tmp_path):
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40, prior=_prior())
    assert change_set(job) is None


def test_the_published_reconciliation_uses_the_change_set(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(review.outcome, "record_prior_findings", lambda *a, **k: seen.update(k))
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40,
                     prior=_prior(), preflight=_preflight(["b.py"]))
    review.outcome._reconcile_and_verify(job, ReviewRecord())
    assert seen["changed"] == frozenset({"b.py"})


def test_unaccounted_leaves_out_a_settled_decline(tmp_path):
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40,
                     prior=_prior(DECLINED), preflight=_preflight(["b.py"]))
    assert review.steps._unaccounted(job, ReviewRecord()) == []


def test_synthesis_is_handed_what_unaccounted_returns(tmp_path, monkeypatch):
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40)
    seen = {}
    monkeypatch.setattr(review.steps, "_unaccounted", lambda j, record: ["SENTINEL"])
    monkeypatch.setattr(review.steps, "build_prompt",
                        lambda phase, j, **extra: seen.update(extra) or "prompt")

    def failing_agent(inv, **kwargs):
        Path(inv.session_log).write_text("")
        return 1
    monkeypatch.setattr(review.phases, "run_agent", failing_agent)
    monkeypatch.setattr(agent.retry, "try_recover_output", lambda *a, **k: False)
    monkeypatch.setattr(review.outcome, "post_process_record",
                        lambda record, *a, **k: PostProcessed(record, None))
    review.steps._phase_synthesis(job, "", 3, merged())
    assert seen["unaccounted_prior"] == ["SENTINEL"]
