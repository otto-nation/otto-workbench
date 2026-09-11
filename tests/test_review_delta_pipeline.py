"""How much work a re-review does, decided from its delta rather than its PR.

Two decisions, both made in `_run_phases` before any agent is invoked and both
previously made from the whole PR's GitHub stats:

`_review_scale` sizes the run. A large PR took the multi-phase pipeline on every
re-review, however small the change since the last one — a group plan and a
fan-out to review one line.

`_is_no_op_rereview` decides whether to run at all. Merging the base into a
branch moves HEAD without adding to it, and the only guard against that was an
interactive prompt keyed on a byte-identical SHA, which a merge always defeats.

The gate is the dangerous half. Firing it wrongly means a real change is never
reviewed, by this run or any later one — the marker advances past it — so the
tests that matter most here are the ones asserting it stays shut.
"""

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import synthetic_review

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from cli import review_orchestrate as ro
from core.phases import Effort, Mode
from gh.types import PRContext, PRMetadata
from review.document import ReviewHeader
from review.types import DeltaAttribution, Pipeline, PreflightData, ReviewJob

# MEDIUM's thresholds are 500 lines / 10 files.
_BIG_PR = {"additions": 4000, "deletions": 2000, "changed_files": 120}
_PRIOR = synthetic_review(
    meta="head_sha: 0ldc0de",
    findings="## Must fix\n- **[M1]** `a.py:1` — a bug nobody has fixed\n",
    verdict="Request changes",
)


class _Trail:
    """Records the decisions a run reports, which is where the basis shows up."""

    def __init__(self):
        self.decisions: list[tuple[str, str, dict]] = []

    def decision(self, name, detail, reason="", data=None):
        self.decisions.append((name, reason, data or {}))

    def info(self, *_args, **_kwargs):
        pass

    def span(self, *_args, **_kwargs):
        from contextlib import nullcontext
        return nullcontext()

    def named(self, name: str) -> dict:
        return next(d for n, _, d in self.decisions if n == name)

    def has(self, name: str) -> bool:
        return any(n == name for n, _, _ in self.decisions)


def _job(tmp_path, *, prior_review="", **pr_overrides) -> ReviewJob:
    pr_fields = {
        "title": "t", "body": "", "head": "feat", "base": "main",
        "head_sha": "newc0de", "additions": 10, "deletions": 0,
        "changed_files": 1, "files": [{"path": "a.py", "additions": 10, "deletions": 0}],
    }
    pr_fields.update(pr_overrides)
    return ReviewJob(
        repo="org/repo", pr_number="42", pr=PRMetadata(**pr_fields),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        generator_version="1.0.0", effort=Effort.MEDIUM,
        prior_review=prior_review,
    )


def _preflight(**overrides) -> PreflightData:
    fields = {
        "diff": "", "commit_log": "", "file_contents": {}, "file_permissions": {},
        "claude_md": "", "architecture_md": "",
    }
    fields.update(overrides)
    return PreflightData(**fields)


def _args(**overrides) -> SimpleNamespace:
    defaults = {
        "max_parallel": 2, "max_cost": 20.0, "max_groups": None,
        "disprove": None, "fix": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def run_phases(monkeypatch):
    """Drive `_run_phases`, recording which pipeline it invoked.

    Both pipelines are replaced by recorders that write a review the way a real
    one does, so a run reaching either is visible and a run reaching neither
    leaves the artifact the fast path wrote.
    """
    called: list[str] = []

    def _multi(job, **_kwargs):
        called.append("multi")
        Path(job.review_file).write_text(synthetic_review())

    def _single(job, **_kwargs):
        called.append("single")
        Path(job.review_file).write_text(synthetic_review())

    monkeypatch.setattr(ro, "run_multi_phase", _multi)
    monkeypatch.setattr(ro, "run_single_agent", _single)
    monkeypatch.setattr(ro, "run_static_analysis", lambda *a, **k: [])

    def _run(job) -> tuple[_Trail, list[str], Pipeline]:
        trail = _Trail()
        with redirect_stdout(io.StringIO()):
            pipeline = ro._run_phases(trail, _args(), job)
        return trail, called, pipeline

    return _run


class TestPipelineSizingUsesTheDelta:
    def test_a_large_pr_with_a_small_delta_takes_the_single_agent_path(
        self, tmp_path, run_phases,
    ):
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=["a.py"], delta_lines=2, prior_head_sha="0ldc0de",
            delta_attribution=DeltaAttribution.ATTRIBUTED,
        )

        _, called, pipeline = run_phases(job)

        assert called == ["single"]
        assert pipeline == Pipeline.SINGLE

    def test_a_large_pr_with_a_large_delta_still_fans_out(self, tmp_path, run_phases):
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=[f"f{i}.py" for i in range(40)], delta_lines=3000,
            prior_head_sha="0ldc0de", delta_attribution=DeltaAttribution.ATTRIBUTED,
        )

        _, called, pipeline = run_phases(job)

        assert called == ["multi"]
        assert pipeline == Pipeline.MULTI

    def test_a_first_pass_review_is_still_sized_by_the_whole_pr(
        self, tmp_path, run_phases,
    ):
        """No prior review means no delta, and the PR is the only measure."""
        job = _job(tmp_path, **_BIG_PR)
        job.preflight = _preflight()

        trail, called, _ = run_phases(job)

        assert called == ["multi"]
        assert trail.named("select_pipeline")["basis"] == "pr"

    def test_the_run_reports_which_count_it_sized_itself_by(
        self, tmp_path, run_phases,
    ):
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=["a.py"], delta_lines=2, prior_head_sha="0ldc0de",
            delta_attribution=DeltaAttribution.ATTRIBUTED,
        )

        trail, _, _ = run_phases(job)

        recorded = trail.named("select_pipeline")
        assert recorded["basis"] == "delta"
        assert recorded["changed_files"] == 1
        assert recorded["total_lines"] == 2


class TestTheEmptyDeltaFastPath:
    @staticmethod
    def _merge_only(tmp_path) -> ReviewJob:
        """A re-review whose HEAD moved by a merge the author did not write."""
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=[], delta_lines=0, delta_attribution=DeltaAttribution.ATTRIBUTED,
            prior_head_sha="0ldc0de",
        )
        return job

    def test_no_agent_runs(self, tmp_path, run_phases):
        _, called, _ = run_phases(self._merge_only(tmp_path))

        assert called == []

    def test_the_marker_advances_to_the_new_head(self, tmp_path, run_phases):
        job = self._merge_only(tmp_path)

        run_phases(job)

        written = Path(job.review_file).read_text()
        assert ReviewHeader.parse(written).head_sha == job.pr.head_sha

    def test_the_review_states_what_it_is_a_delta_against(self, tmp_path, run_phases):
        job = self._merge_only(tmp_path)

        run_phases(job)

        header = ReviewHeader.parse(Path(job.review_file).read_text())
        assert header.prior_sha == "0ldc0de"

    def test_prior_findings_are_carried_forward(self, tmp_path, run_phases):
        job = self._merge_only(tmp_path)

        run_phases(job)

        assert "a bug nobody has fixed" in Path(job.review_file).read_text()

    def test_an_unaddressed_must_fix_still_requests_changes(
        self, tmp_path, run_phases,
    ):
        """Nothing was addressed, so the prior review's call stands.

        Approving here would clear a blocking finding on the strength of the
        author having merged the base branch.
        """
        job = self._merge_only(tmp_path)

        run_phases(job)

        assert "Request changes" in Path(job.review_file).read_text()

    def test_the_run_records_why_it_did_nothing(self, tmp_path, run_phases):
        trail, _, _ = run_phases(self._merge_only(tmp_path))

        assert trail.has("empty_delta")
        assert not trail.has("select_pipeline")

    def test_a_sidecar_is_written_beside_the_review(self, tmp_path, run_phases):
        run_phases(self._merge_only(tmp_path))

        assert (tmp_path / "meta.json").exists()


class TestTheFastPathStaysShut:
    """Every way an empty delta can arise without the author having idled.

    A wrong skip here is unrecoverable: the change goes unreviewed and the
    marker moves past it, so no later re-review looks at it either.
    """

    def test_an_empty_delta_that_was_not_proven_still_runs_the_pipeline(
        self, tmp_path, run_phases,
    ):
        """The case a `not delta_files` test would have got wrong.

        An unresolvable base ref and a failed git walk both leave the file list
        empty without establishing that nothing changed.
        """
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=[], delta_lines=0, delta_attribution=DeltaAttribution.UNATTRIBUTED,
            prior_head_sha="0ldc0de",
        )

        _, called, _ = run_phases(job)

        assert called == ["multi"]

    def test_a_first_pass_review_is_never_the_fast_path(self, tmp_path, run_phases):
        """`proven_empty` cannot be set without a prior SHA, but not by luck."""
        job = _job(tmp_path, **_BIG_PR)
        job.preflight = _preflight(delta_attribution=DeltaAttribution.ATTRIBUTED)

        _, called, _ = run_phases(job)

        assert called == ["multi"]

    def test_an_attributed_empty_delta_with_no_prior_review_text_still_runs(
        self, tmp_path, run_phases,
    ):
        """The fast path has nothing to carry forward, so an agent writes the review.

        A prior SHA is parsed out of the prior review, so in a real run this
        pair cannot come apart. The guard is here because the fast path's whole
        output is the prior review's findings: reaching it without them would
        ship an empty document and advance the marker past the range.
        """
        job = _job(tmp_path, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=[], delta_attribution=DeltaAttribution.ATTRIBUTED,
            prior_head_sha="0ldc0de",
        )

        _, called, _ = run_phases(job)

        assert called, "an agent has to write the review the fast path did not"

    def test_author_work_beside_a_merge_is_reviewed(self, tmp_path, run_phases):
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.preflight = _preflight(
            delta_files=["a.py"], delta_lines=1, prior_head_sha="0ldc0de",
            delta_attribution=DeltaAttribution.ATTRIBUTED,
        )

        _, called, _ = run_phases(job)

        assert called == ["single"]


class TestSelfReviewIsUnaffected:
    def test_a_self_review_is_never_short_circuited(self, tmp_path, run_phases):
        """Self-review cannot prove an empty delta, so the flag is never set.

        Its surface is the working tree, which has no commits to attribute.
        Asserted here rather than left to `review.collect` because this is the
        caller that would act on it.
        """
        job = _job(tmp_path, prior_review=_PRIOR, **_BIG_PR)
        job.mode = Mode.SELF
        job.preflight = _preflight(
            delta_files=[], delta_lines=0, prior_head_sha="0ldc0de",
        )

        _, called, _ = run_phases(job)

        assert called == ["multi"]
