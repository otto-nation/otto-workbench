"""The head SHA a finished review states is the harness', not the agent's.

The marker is the point the next re-review measures its delta from, and a wrong
one is invisible in the review carrying it: the run that suffers is the next
one, which either measures from another commit or gives up and reviews the
whole PR again. So what is pinned here is the value on disk after a run, on the
two paths where the document's header is the review agent's own — the
single-agent review and a synthesis that completed.

`set_head_sha` is unit-tested against handwritten headers in the document
suite. These are the end-to-end half: a scripted agent writes a review whose
marker is wrong, or absent, and the assertion is on the file the pipeline left
behind.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from conftest import synthetic_review

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from review import phases as review_phases
from review import pipeline as review_pipeline
from review import steps as review_steps
from review.document import ReviewHeader

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


def _stated_sha(job) -> str:
    return ReviewHeader.parse(Path(job.review_file).read_text()).head_sha


@pytest.fixture
def job(tmp_path):
    """A job over four files, which `group_files` splits into groups `a` and `b`."""
    for rel in _FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n" * 20)
    (tmp_path / "reviews").mkdir()

    files = [{"path": p, "additions": 20, "deletions": 0} for p in _FILES]
    return review_pipeline.ReviewJob(
        repo="org/repo", pr_number="1",
        pr=review_pipeline.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha=_HEAD_SHA,
            additions=80, deletions=0, changed_files=len(files), files=files,
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
