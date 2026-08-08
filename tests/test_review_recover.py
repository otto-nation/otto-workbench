"""End-to-end coverage of `run_multi_phase`'s recover paths.

The unit tests pin each piece of the recover machinery separately — state
round-trips in `TestPipelineStateFailureRoundTrip`, the hint in
`TestBuildFailuresSection`, the classifier in the diagnostics suite. None of
them run the pipeline twice against one review directory, which is the only
thing that exercises persistence, `_resolve_recovery` and the rendered Agent
Failures section together.

The agent is faked at `invoke_agent`, the single seam every phase reaches
through `PhaseRunner.invoke`. A test scripts a run by naming the phases that
produce no output, then runs the pipeline again over the same directory the
way `pr review --recover` does.
"""

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_pipeline
import review_state

_MAX_TURNS_RECORD = json.dumps({
    "type": "result", "subtype": "error_max_turns", "is_error": True,
    "num_turns": 20,
})
_DENIED_RECORD = json.dumps({
    "type": "result", "subtype": "error", "is_error": True,
    "errors": ["permission denied"],
})
_OK_RECORD = json.dumps({"type": "result", "subtype": "success", "num_turns": 3})

_GROUP_FINDING = "## Nit\n- **[N1]** `a/one.py:1` — naming\n"
_REVIEW_BODY = (
    "# Review: org/repo#1 — t\n"
    "<!-- generator: test -->\n"
    "## Summary\nSynthesized.\n\n"
    "## Verdict\nApprove\n"
)

_FILES = ("a/one.py", "a/two.py", "b/three.py", "b/four.py")
_RECOVER_HINT = "Run `pr review --recover`"


class _Agent:
    """A scripted stand-in for `invoke_agent`.

    `fails` and `denied` name the phases that produce no output, keyed on the
    session log's stem (`group-2`, `holistic`, `synthesis`) — the only handle
    an `AgentInvocation` carries back to the phase that built it.
    """

    def __init__(
        self, review_file: str,
        fails: "set[str] | None" = None, denied: "set[str] | None" = None,
    ):
        self.review_file = review_file
        self.fails = fails or set()
        self.denied = denied or set()
        self.phases: list[str] = []

    def __call__(self, invocation, throttle=None) -> int:
        log_path = Path(invocation.session_log)
        phase = log_path.stem
        self.phases.append(phase)

        if phase in self.denied:
            self._append(log_path, _DENIED_RECORD)
            return 1
        if phase in self.fails:
            self._append(log_path, _MAX_TURNS_RECORD)
            return 1

        self._append(log_path, _OK_RECORD)
        if phase == "synthesis":
            Path(self.review_file).write_text(_REVIEW_BODY)
        else:
            log_path.with_suffix(".md").write_text(_GROUP_FINDING)
        return 0

    @staticmethod
    def _append(log_path: Path, record: str):
        with log_path.open("a") as fh:
            fh.write(record + "\n")


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
            title="t", body="", head="feat", base="main", head_sha="abc123",
            additions=80, deletions=0, changed_files=len(files), files=files,
        ),
        ctx=review_pipeline.PRContext(),
        wt_path=str(tmp_path),
        review_file=str(tmp_path / "reviews" / "review.md"),
        session_log=str(tmp_path / "reviews" / "session.jsonl"),
    )


@pytest.fixture
def run(monkeypatch):
    """Run the pipeline with a scripted agent, returning that agent."""
    monkeypatch.setattr(review_pipeline, "build_prompt", lambda *a, **k: "PROMPT")

    def _run(job, fails=None, denied=None) -> _Agent:
        agent = _Agent(job.review_file, fails=fails, denied=denied)
        monkeypatch.setattr(review_pipeline, "invoke_agent", agent)
        with contextlib.redirect_stdout(io.StringIO()):
            review_pipeline.run_multi_phase(job)
        return agent

    return _run


def _state(job) -> dict:
    return json.loads(Path(review_state._pipeline_state_path(job)).read_text())


def _review(job) -> str:
    return Path(job.review_file).read_text()


class TestRecoverRerunsOnlyWhatFailed:
    def test_second_run_retries_the_failed_group_and_skips_the_done_one(self, job, run):
        run(job, fails={"group-2"})
        assert _state(job)["groups_done"] == [1]

        second = run(job)

        assert "group-1" not in second.phases
        assert "group-2" in second.phases

    def test_a_recovered_group_leaves_no_state_behind(self, job, run):
        run(job, fails={"group-2"})
        assert list(_state(job)["groups_failed"]) == ["2"]

        run(job)

        assert not Path(review_state._pipeline_state_path(job)).exists()

    def test_a_recovered_group_drops_the_failures_section(self, job, run):
        run(job, fails={"group-2"})
        assert "## Agent Failures" in _review(job)

        run(job)

        assert "## Agent Failures" not in _review(job)
        assert "<!-- status: completed -->" in _review(job)

    def test_a_still_failing_group_keeps_reporting_itself(self, job, run):
        run(job, fails={"group-2"})
        run(job, fails={"group-2"})

        assert "| group-2: b | agent hit max turns (20) | failed |" in _review(job)
        assert list(_state(job)["groups_failed"]) == ["2"]


class TestRecoverRendersTheRightHint:
    def test_a_retryable_failure_offers_recover(self, job, run):
        run(job, fails={"group-2"})
        assert _RECOVER_HINT in _review(job)

    def test_a_permission_denial_does_not_offer_recover(self, job, run):
        run(job, denied={"group-2"})

        review = _review(job)
        assert "| group-2: b | agent error: permission denied | failed |" in review
        assert _RECOVER_HINT not in review

    def test_one_retryable_failure_restores_the_hint(self, job, run):
        run(job, fails={"group-1"}, denied={"group-2"})

        review = _review(job)
        assert "agent error: permission denied" in review
        assert "agent hit max turns (20)" in review
        assert _RECOVER_HINT in review


class TestRecoverAcrossASchemaChange:
    """A `pipeline.json` written before diagnoses were typed still recovers.

    Reviews live in `~/.config/workbench/reviews/` and outlive the code that
    wrote them, so the first `--recover` after this change reads a state file
    holding rendered strings where it now expects records.
    """

    @staticmethod
    def _downgrade(job, reason: str):
        path = Path(review_state._pipeline_state_path(job))
        state = json.loads(path.read_text())
        state["groups_failed"] = {"2": reason}
        path.write_text(json.dumps(state))

    def test_a_legacy_string_failure_still_recovers(self, job, run):
        run(job, fails={"group-2"})
        self._downgrade(job, "agent error: model not available")

        second = run(job)

        assert "group-1" not in second.phases
        assert "group-2" in second.phases
        assert not Path(review_state._pipeline_state_path(job)).exists()

    def test_a_legacy_string_failure_renders_verbatim(self, job, run):
        run(job, fails={"group-2"})
        self._downgrade(job, "agent error: model not available")
        # Synthesis rewrites the review file each run, so the section injected
        # from the prior run is not what is under test here.
        Path(job.review_file).write_text(_REVIEW_BODY)

        review_state._inject_failures_and_status(
            job.review_file, review_state._read_pipeline_state(job),
            review_pipeline.group_files(job.pr),
        )

        assert "| group-2: b | agent error: model not available | failed |" in _review(job)


class TestRecoverDeclinesTheWorkItShould:
    def test_a_surviving_complete_state_blocks_a_re_run(self, job, run):
        """The `--force` guard.

        A clean run deletes its own state file, so this is reachable only when
        the run recorded a failure the state never saw — a group marked skipped
        whose output had vanished.
        """
        run(job)
        Path(review_state._pipeline_state_path(job)).write_text(json.dumps({
            "head_sha": "abc123", "group_names": ["a", "b"],
            "groups_done": [1, 2], "synthesis_done": True,
        }))

        assert run(job).phases == []

    def test_a_new_head_sha_starts_over(self, job, run):
        run(job, fails={"group-2"})
        job.pr.head_sha = "def456"

        second = run(job)

        assert "group-1" in second.phases
        assert "group-2" in second.phases
