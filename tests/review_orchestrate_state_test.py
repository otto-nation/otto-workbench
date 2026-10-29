"""Pipeline state in `review.state`: the failure round trip, the failures body and its section."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from core.phases import Phase
from review.render import failures_body
from review.state import (
    PIPELINE_SCHEMA_VERSION, PipelineState, _validate_resume_state, failures_of,
)
from review.types import Group
from review_focus_support import user_area
from review_orchestrate_test import TestCleanupScope as _OrchestrateRun


def _failures_md(state):
    rows = failures_of(state)
    return failures_body(rows) if rows else ""


class TestPipelineStateFailureRoundTrip:
    """`groups_failed` survives state.json in both the old and new format."""

    def _job(self, ro, tmp_path):
        job = MagicMock()
        job.review_file = str(tmp_path / "review.md")
        return job

    def _state(self, ro, groups_failed):
        from review.state import PipelineState
        return PipelineState(
            head_sha="abc", group_names=["ui"], groups_failed=groups_failed,
        )

    def test_a_diagnosis_survives_write_then_read(self, ro, tmp_path):
        diagnosis = ro.Diagnosis(
            ro.DiagnosisKind.MAX_TURNS, num_turns=12, no_write_tool=True,
        )
        job = self._job(ro, tmp_path)
        ro._write_pipeline_state(job, self._state(ro, {1: diagnosis}))
        assert ro.read_pipeline_state(job).groups_failed == {1: diagnosis}

    def test_a_legacy_string_recovers_the_kind_it_renders_as(self, ro, tmp_path):
        """State written before diagnoses were typed holds a rendered reason.

        Where that reason is one a kind renders verbatim, the kind comes back —
        a recovered run gets the real retry policy rather than UNKNOWN's.
        """
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"],
            "groups_failed": {"1": "quota exhausted (429)"},
        }))
        state = ro.read_pipeline_state(self._job(ro, tmp_path))
        assert state.groups_failed == {1: ro.Diagnosis(ro.DiagnosisKind.QUOTA_EXHAUSTED)}

    def test_a_legacy_string_no_kind_renders_stays_unknown(self, ro, tmp_path):
        """An interpolated reason names no kind, so it is kept as written."""
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"],
            "groups_failed": {"1": "agent hit max turns (12)"},
        }))
        state = ro.read_pipeline_state(self._job(ro, tmp_path))
        assert state.groups_failed == {
            1: ro.Diagnosis(ro.DiagnosisKind.UNKNOWN, detail="agent hit max turns (12)"),
        }

    def _write_state(self, ro, tmp_path, **fields):
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"], **fields,
        }))
        return ro.read_pipeline_state(self._job(ro, tmp_path))

    @pytest.mark.parametrize("raw,kind", [
        ("all groups failed", "ALL_GROUPS_FAILED"),
        ("mechanical fallback", "MECHANICAL_FALLBACK"),
        ("budget exceeded", "BUDGET_EXCEEDED"),
    ])
    def test_a_legacy_synthesis_sentinel_becomes_its_kind(self, ro, tmp_path, raw, kind):
        """The three strings synthesis used to write are now diagnoses.

        A review directory written before the change is the common case for
        `--recover`, so each sentinel has to read back as the kind it names —
        otherwise the recovered run loses the outcome it recorded.
        """
        state = self._write_state(ro, tmp_path, failed={"synthesis": raw})
        assert state.failed == {
            Phase.SYNTHESIS: ro.Diagnosis(getattr(ro.DiagnosisKind, kind)),
        }

    def test_a_state_file_from_before_the_phase_keys_reads_as_nothing_done(
        self, ro, tmp_path,
    ):
        """The per-phase flags are gone, and `serde` ignores what it does not know.

        A review mid-flight across the upgrade therefore reads back with no
        phase recorded, re-runs its scan once and is correct from there — the
        whole migration, which is why there is no migration.
        """
        state = self._write_state(
            ro, tmp_path,
            holistic_done=True, synthesis_done=True,
            synthesis_failed="mechanical fallback",
        )

        assert state.done == set()
        assert state.failed == {}
        assert state.scanned is False
        assert _failures_md(state) == ""

    def test_a_legacy_reason_still_renders_verbatim(self, ro, tmp_path):
        state = self._state(ro, {
            1: ro.Diagnosis(ro.DiagnosisKind.UNKNOWN, detail="quota exhausted (429)"),
        })
        assert "quota exhausted (429)" in _failures_md(state)


class TestBuildFailuresBody:
    def test_no_failures_returns_empty(self):
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            done={Phase.SYNTHESIS},
        )
        assert _failures_md(state) == ""

    def test_a_pipe_in_a_reason_cannot_break_its_row(self):
        """A quoted stderr line is free text; one `|` shifted the Status cell."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            group_names=["a"],
            groups_failed={1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="error: a | b")},
        )
        row = _failures_md(state).splitlines()[2]
        assert "a \\| b" in row
        assert row.endswith("| failed |")

    def test_group_failures_produce_table(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["ui-components", "api-routes", "tests"],
            groups_done=[1], groups_failed={
                2: Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED),
                3: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5),
            },
            done={Phase.SYNTHESIS},
        )
        result = _failures_md(state)
        assert "## Agent Failures" not in result
        assert "group-2: api-routes" in result
        assert "quota exhausted (429)" in result
        assert "group-3: tests" in result
        assert "agent hit max turns" in result
        assert "failed" in result
        assert "pr review --recover" in result

    def test_synthesis_fallback_in_table(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[1], groups_failed={},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )
        result = _failures_md(state)
        assert "synthesis" in result
        assert "fallback" in result

    def test_no_recover_hint_for_permission_errors(self):
        """The reason a denial really produces, not the bare marker.

        The check this replaced compared the whole rendered reason against the
        marker tuple, so it never fired on `agent error: permission denied`.
        """
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[],
            groups_failed={
                1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied"),
            },
            done={Phase.SYNTHESIS},
        )
        result = _failures_md(state)
        assert "agent error: permission denied" in result
        assert "pr review --recover" not in result

    def test_recover_hint_survives_one_recoverable_failure(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[],
            groups_failed={
                1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied"),
                2: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5),
            },
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" in _failures_md(state)

    def test_no_recover_hint_when_every_group_is_unrecoverable(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        denial = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied")
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[], groups_failed={1: denial, 2: denial},
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" not in _failures_md(state)

    def test_recover_hint_offered_for_max_turns(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[], groups_failed={1: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5)},
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" in _failures_md(state)

    def test_synthesis_failure_alone_stays_recoverable(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[1], groups_failed={},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )
        assert "pr review --recover" in _failures_md(state)


class TestFailuresSectionInReview:
    """`apply_failures` + render — what the body says, and where it lands."""

    @staticmethod
    def _state():
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        return PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1], groups_failed={2: Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )

    @staticmethod
    def _clean_state():
        from review.state import PipelineState
        return PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            done={Phase.SYNTHESIS},
        )

    @staticmethod
    def _body(rec, state):
        from review.render import render_body
        from review.state import apply_failures
        return render_body(apply_failures(rec, state))

    def test_mechanical_fallback_includes_failures(self):
        """When synthesis falls back, the review includes ## Agent Failures."""
        from review_record_support import record
        result = self._body(record(summary="note"), self._state())
        assert "## Agent Failures" in result
        assert "group-2: api" in result
        assert "quota exhausted" in result
        assert "synthesis" in result
        assert "fallback" in result

    def test_the_section_sits_above_the_summary(self):
        from review_record_support import record
        result = self._body(record(summary="note", verdict="Approve"), self._state())
        assert result.index("## Agent Failures") < result.index("## Summary")

    def test_a_review_with_no_summary_still_gets_the_section(self):
        """A run that never reached synthesis has no Summary to sit above, and
        the failures are the only account of why."""
        from review_record_support import finding, record
        result = self._body(record(finding("M1", "bug")), self._state())
        assert "## Agent Failures" in result
        assert result.index("## Agent Failures") < result.index("## Must fix")

    def test_a_rerun_that_failed_nothing_drops_the_section(self):
        from review.record import AgentFailure
        from review_record_support import record
        result = self._body(
            record(
                summary="note",
                failures=(AgentFailure("old", "table", "failed"),),
            ),
            self._clean_state(),
        )
        assert "## Agent Failures" not in result
        assert "## Summary" in result


class TestInjectFailuresAndStatus:
    """Tests for _inject_failures_and_status — specifically the always-update status fix."""

    def _make_pipeline_json(self, tmp_path, failure_reason="", groups_failed=None):
        import json
        data = {
            "head_sha": "abc123",
            "group_names": ["ui", "api"],
            "groups_done": [1],
            "groups_failed": groups_failed or {},
            # Through the gate, so status turns on what failed rather than on a
            # run these tests never meant to leave unfinished.
            "done": ["synthesis", "disprove"],
            "failed": {"synthesis": failure_reason} if failure_reason else {},
            "review_type": "full",
            "prior_sha": "",
            "skipped_groups": [],
        }
        (tmp_path / "pipeline.json").write_text(json.dumps(data))

    def _seed(self, tmp_path, status=None):
        from pr.domains import ReviewStatus
        from review.store import write_review
        from review.types import ReviewMeta
        from review_record_support import record
        review_file = tmp_path / "review.md"
        write_review(
            review_file,
            record(summary="All good."),
            ReviewMeta(status=status if status is not None else ReviewStatus.COMPLETED),
        )
        return review_file

    def test_replaces_existing_status_line(self, tmp_path):
        """I1: status already present as 'completed' is updated to 'partial' on synthesis failure."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from pr.domains import ReviewStatus
        from review.state import PipelineState
        from review.state import _inject_failures_and_status

        review_file = self._seed(tmp_path, ReviewStatus.COMPLETED)
        self._make_pipeline_json(tmp_path, failure_reason="budget exceeded")
        state = PipelineState(
            head_sha="abc123", group_names=["ui", "api"],
            groups_done=[1], groups_failed={},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)},
        )
        _inject_failures_and_status(str(review_file), state)

        content = review_file.read_text()
        assert "<!-- status: partial -->" in content
        assert "<!-- status: completed -->" not in content

    def test_inserts_status_when_absent(self, tmp_path):
        """Status line is inserted when the stored sidecar carried none."""
        from review.state import PipelineState
        from review.state import _inject_failures_and_status
        from review.types import ReviewMeta
        from review.store import write_review
        from review_record_support import record

        review_file = tmp_path / "review.md"
        write_review(
            review_file,
            record(summary="All good."),
            ReviewMeta(),
        )
        self._make_pipeline_json(tmp_path, failure_reason="")
        state = PipelineState(
            head_sha="abc123", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            done={Phase.SYNTHESIS, Phase.DISPROVE},
        )
        _inject_failures_and_status(str(review_file), state)

        content = review_file.read_text()
        assert "<!-- status: completed -->" in content

    def test_replaces_status_not_duplicated(self, tmp_path):
        """Replacing an existing status line does not add a second status line."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from pr.domains import ReviewStatus
        from review.state import PipelineState
        from review.state import _inject_failures_and_status

        review_file = self._seed(tmp_path, ReviewStatus.COMPLETED)
        self._make_pipeline_json(tmp_path, failure_reason="mechanical fallback")
        state = PipelineState(
            head_sha="abc123", group_names=["ui", "api"],
            groups_done=[1], groups_failed={},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )
        _inject_failures_and_status(str(review_file), state)

        content = review_file.read_text()
        assert content.count("<!-- status:") == 1


def test_a_pipeline_state_from_before_json_artifacts_is_stale():
    """X8: a run interrupted before the upgrade is not resumed onto .md artifacts."""
    groups = [Group(name="g1", files=["a.py"], lines=1)]
    old = PipelineState(head_sha="abc", group_names=["g1"])
    new = PipelineState(head_sha="abc", group_names=["g1"], schema_version=PIPELINE_SCHEMA_VERSION)
    assert not _validate_resume_state(old, "abc", groups)
    assert _validate_resume_state(new, "abc", groups)


class TestFocusAcrossAResume:
    """A resumed run keeps the areas it was asked for, and a new ask starts fresh."""

    _GROUPS = [Group(name="g1", files=["a.py"], lines=1)]

    @staticmethod
    def _job(tmp_path, focus=()):
        from types import SimpleNamespace
        return SimpleNamespace(review_file=str(tmp_path / "review.md"), focus=focus,
                               pr=SimpleNamespace(head_sha="abc123"))

    def _write(self, ro, tmp_path, focus):
        state = PipelineState(head_sha="abc123", group_names=["g1"], focus=list(focus),
                              schema_version=PIPELINE_SCHEMA_VERSION)
        ro._write_pipeline_state(self._job(tmp_path), state)
        return Path(ro._pipeline_state_path(self._job(tmp_path)))

    def test_a_resume_with_a_different_focus_starts_fresh(self, ro, tmp_path):
        path = self._write(ro, tmp_path, [user_area("A")])
        plan = ro._resolve_recovery(self._job(tmp_path, (user_area("B"),)), self._GROUPS)
        assert plan == ro.RecoveryPlan()
        assert not path.exists()

    def test_a_resume_with_the_same_focus_is_kept(self, ro, tmp_path):
        self._write(ro, tmp_path, [user_area("A")])
        plan = ro._resolve_recovery(self._job(tmp_path, (user_area("A"),)), self._GROUPS)
        assert plan.state is not None
        assert plan.state.focus == [user_area("A")]

    def test_a_pre_focus_state_resumes_without_focus(self, ro, tmp_path):
        path = Path(ro._pipeline_state_path(self._job(tmp_path)))
        path.write_text(json.dumps({
            "head_sha": "abc123", "group_names": ["g1"],
            "schema_version": PIPELINE_SCHEMA_VERSION,
        }))
        state = ro.read_pipeline_state(self._job(tmp_path))
        assert state.focus == []
        assert _validate_resume_state(state, "abc123", self._GROUPS, user_focus=())

    def _orchestrated_focus(self, ro, monkeypatch, tmp_path, resumed):
        """The job's focus when a run at the job's head left `resumed` behind and the
        prior record requested F1 Retry."""
        review_dir = tmp_path / "review-dir"
        review_dir.mkdir()
        self._write(ro, review_dir, resumed)
        return _OrchestrateRun()._focus_after_prior(ro, monkeypatch, tmp_path)

    def test_a_resume_without_focus_keeps_the_run_s_own(self, ro, monkeypatch, tmp_path):
        focus = self._orchestrated_focus(ro, monkeypatch, tmp_path, [user_area("Own", "r")])
        assert focus == (user_area("Own", "r"),)

    def test_a_resume_that_recorded_no_focus_keeps_none(self, ro, monkeypatch, tmp_path):
        """D7: a resumed run that asked for nothing is not handed the prior's ask."""
        assert self._orchestrated_focus(ro, monkeypatch, tmp_path, []) == ()
