"""The header a finished review states is the harness', not the agent's.

Every key in it records something about the *run* — which commit was read,
what it is a delta against, which build produced it — and the agent can see
none of that from inside its own prompt. What it wrote instead was its
template's placeholder. So what is pinned here is the block on disk after a
run, on the two paths where the document is the review agent's own: the
single-agent review and a synthesis that completed.

Two of those keys have teeth rather than being cosmetic. `head_sha` is the
point the next re-review measures its delta from, and a wrong one is invisible
in the review carrying it — the run that suffers is the next one, which either
measures from another commit or reviews the whole PR again. `review_type` is
worse, because its failure mode was *absence*: no template ever asked for it,
and `ReviewHeader.parse` reads an absent one as `full`, so every agent-written
incremental review on record claimed to be a full one.

`set_meta` and `stamp_header` are unit-tested against handwritten headers in
the document suite. These are the end-to-end half: a scripted agent writes a
review whose header is wrong, or absent, and the assertion is on the file the
pipeline left behind.
"""

import contextlib
import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from conftest import synthetic_review

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from core.phases import Mode
from review import phases as review_phases
from review import pipeline as review_pipeline
from review import steps as review_steps
from review.document import ReviewHeader
from review.types import PreflightData, ReviewType

_HEAD_SHA = "abc1234def5678"
_AGENTS_SHA = "deadbeefdeadbeef"
_OK_RECORD = json.dumps({"type": "result", "subtype": "success", "num_turns": 3})
_GROUP_FINDING = "## Nit\n- **[N1]** `a/one.py:1` — naming\n"
_FILES = ("a/one.py", "a/two.py", "b/three.py", "b/four.py")
_MUST_FIX = "## Must fix\n- **[M1]** `a/one.py:1` — bug\n"

# What the agent types into the header, against a harness holding `_HEAD_SHA`.
_WRONG = f"generator: test -->\n<!-- head_sha: {_AGENTS_SHA}"
_ABSENT = "generator: test"
_UNFILLED = "generator: test -->\n<!-- head_sha: FULL_SHA"


def _review_body(meta: str) -> str:
    return synthetic_review(meta=meta)


def _stated(job) -> ReviewHeader:
    return ReviewHeader.parse(Path(job.review_file).read_text())


def _stated_sha(job) -> str:
    return _stated(job).head_sha


@pytest.fixture
def job(tmp_path):
    """A job over four files, which `group_files` splits into groups `a` and `b`."""
    for rel in _FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n" * 20)
    (tmp_path / "reviews").mkdir()

    # Two files per top-level dir; 150 lines each keeps a/ and b/ above
    # MIN_GROUP_LINES so they remain separate groups.
    files = [{"path": p, "additions": 150, "deletions": 0} for p in _FILES]
    return review_pipeline.ReviewJob(
        repo="org/repo", pr_number="1",
        pr=review_pipeline.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha=_HEAD_SHA,
            additions=600, deletions=0, changed_files=len(files), files=files,
        ),
        ctx=review_pipeline.PRContext(),
        wt_path=str(tmp_path),
        review_file=str(tmp_path / "reviews" / "review.md"),
        session_log=str(tmp_path / "reviews" / "session.jsonl"),
    )


@pytest.fixture
def run_single(monkeypatch):
    """Run the single-agent pipeline with an agent writing `meta` as its header."""
    def _run(job, meta: str):
        def _agent(invocation, throttle=None) -> int:
            Path(invocation.session_log).write_text(_OK_RECORD + "\n")
            Path(job.review_file).write_text(_review_body(meta))
            return 0

        monkeypatch.setattr(review_pipeline, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_phases, "run_agent", _agent)
        with contextlib.redirect_stdout(io.StringIO()):
            review_pipeline.run_single_agent(job, disprove=False)
        return job

    return _run


@pytest.fixture
def run_multi(monkeypatch):
    """Run the multi-phase pipeline; its synthesis agent writes `meta`."""
    def _run(job, meta: str):
        def _agent(invocation, throttle=None) -> int:
            log_path = Path(invocation.session_log)
            with log_path.open("a") as fh:
                fh.write(_OK_RECORD + "\n")
            if log_path.stem == "synthesis":
                Path(job.review_file).write_text(_review_body(meta))
            else:
                log_path.with_suffix(".md").write_text(_GROUP_FINDING)
            return 0

        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_steps, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_phases, "run_agent", _agent)
        with contextlib.redirect_stdout(io.StringIO()):
            review_pipeline.run_multi_phase(job)
        return job

    return _run


class TestSingleAgentReviewCarriesTheHarnessSha:
    def test_a_sha_the_agent_invented_is_replaced(self, job, run_single):
        run_single(job, _WRONG)

        assert _stated_sha(job) == _HEAD_SHA
        assert _AGENTS_SHA not in Path(job.review_file).read_text()

    def test_a_review_with_no_marker_gains_one(self, job, run_single):
        run_single(job, _ABSENT)

        assert _stated_sha(job) == _HEAD_SHA

    def test_an_unfilled_template_placeholder_is_replaced(self, job, run_single):
        run_single(job, _UNFILLED)

        assert _stated_sha(job) == _HEAD_SHA


class TestSynthesisReviewCarriesTheHarnessSha:
    def test_a_sha_the_synthesis_agent_invented_is_replaced(self, job, run_multi):
        run_multi(job, _WRONG)

        assert _stated_sha(job) == _HEAD_SHA
        assert _AGENTS_SHA not in Path(job.review_file).read_text()

    def test_a_synthesised_review_with_no_marker_gains_one(self, job, run_multi):
        run_multi(job, _ABSENT)

        assert _stated_sha(job) == _HEAD_SHA


class TestTheStampSurvivesWhatIsWrittenAfterIt:
    """The disprove gate rewrites the review file after the stamp lands.

    It is the one write that happens after post-processing, and it works on the
    text rather than on a parsed document — so whether the header survives it is
    a property of `drop_findings`, not something the stamp can enforce. Pinned
    here because a gate that dropped the marker would take the next re-review
    with it, silently.
    """

    def test_a_falsified_finding_does_not_take_the_marker_with_it(
        self, job, monkeypatch, tmp_path,
    ):
        body = synthetic_review(meta=_WRONG, findings=_MUST_FIX, verdict="Request changes")

        def _agent(invocation, throttle=None) -> int:
            log_path = Path(invocation.session_log)
            log_path.write_text(_OK_RECORD + "\n")
            if log_path.stem == "disprove":
                log_path.with_suffix(".md").write_text("- [M1] FALSIFIED — not a bug\n")
            else:
                Path(job.review_file).write_text(body)
            return 0

        monkeypatch.setattr(review_pipeline, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_phases, "run_agent", _agent)
        with contextlib.redirect_stdout(io.StringIO()):
            review_pipeline.run_single_agent(job, disprove=True)

        assert "[M1]" not in Path(job.review_file).read_text()
        assert _stated_sha(job) == _HEAD_SHA


class TestTheRestOfTheHeaderIsTheHarnessToo:
    """Every key, not just the SHA the stamp started life pinning.

    The agent's template asked it for a date and a generator and nothing else,
    so the keys below were either absent from an agent-written review or were
    whatever prose the template happened to carry.
    """

    def test_the_review_states_what_it_was_reviewing(self, job, run_single):
        run_single(job, _ABSENT)

        header = _stated(job)
        assert header.mode is Mode.PR
        assert header.pr_number == 1
        assert header.head_ref == "feat"
        assert header.base_ref == "main"

    def test_a_generator_the_agent_invented_is_replaced(self, job, run_single):
        """The run's own build, over whatever the template interpolated.

        `generator: test` is what the fixture's agent writes — which is also
        what a template with an unresolved version would write, and for months
        was literally `claude-review unknown` in production.
        """
        job.generator_version = "review 9.9.9"

        run_single(job, _ABSENT)

        assert _stated(job).generator_version == "review 9.9.9"

    def test_a_date_the_agent_typed_is_replaced_with_the_run_s(self, job, run_single):
        """The single-agent template's placeholder was a literal `YYYY-MM-DD`.

        Nothing interpolated it, so the date on the page was whatever the
        agent believed the date to be.
        """
        run_single(job, "generator: test -->\n<!-- date: YYYY-MM-DD")

        assert _stated(job).date == date.today().isoformat()


class TestAnIncrementalReviewSaysSo:
    """The defect the stamp was widened for.

    No template ever asked the agent for `review_type`, and an absent one
    parses as `full`. `review.collect` asks the prior review's header what the
    last run covered, so an incremental review recorded as full is a
    re-review that starts over.
    """

    @pytest.fixture
    def incremental(self, job):
        job.preflight = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            claude_md="", architecture_md="",
            prior_head_sha="0ldc0de", delta_files=["a/one.py"],
        )
        job.prior_review = (
            "# Review: org/repo#1 — t\n<!-- date: 2026-01-01 -->\n\n## Summary\nPrior.\n"
        )
        return job

    def test_the_single_agent_document_states_its_review_type(
        self, incremental, run_single,
    ):
        run_single(incremental, _ABSENT)

        assert _stated(incremental).review_type is ReviewType.INCREMENTAL

    def test_it_states_what_it_is_a_delta_against(self, incremental, run_single):
        run_single(incremental, _ABSENT)

        header = _stated(incremental)
        assert header.prior_sha == "0ldc0de"
        assert header.prior_date == "2026-01-01"
        assert header.delta_files == 1

    def test_a_full_review_states_full_rather_than_staying_silent(
        self, job, run_single,
    ):
        """`full` is a claim about the review, not a gap in the record.

        Without it a reader cannot tell a full review from one written by a
        version that did not record the key.
        """
        run_single(job, _ABSENT)

        assert "<!-- review_type: full -->" in Path(job.review_file).read_text()


class TestTheStatedShaAgreesWithTheSidecar:
    """The two records of what a run reviewed cannot disagree.

    `meta.json` has always carried the harness' SHA; the document's header is
    what the agent typed. A reader that trusts one over the other was choosing
    between them, which is the state this stamp exists to make impossible.
    """

    def test_the_single_agent_document_and_its_sidecar_state_one_sha(
        self, job, run_single, tmp_path,
    ):
        run_single(job, _WRONG)

        sidecar = json.loads((tmp_path / "reviews" / "meta.json").read_text())
        assert sidecar["head_sha"] == _stated_sha(job) == _HEAD_SHA

    def test_the_synthesised_document_and_its_sidecar_state_one_sha(
        self, job, run_multi, tmp_path,
    ):
        run_multi(job, _WRONG)

        sidecar = json.loads((tmp_path / "reviews" / "meta.json").read_text())
        assert sidecar["head_sha"] == _stated_sha(job) == _HEAD_SHA
