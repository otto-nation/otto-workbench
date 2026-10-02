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
        assert ro._read_pipeline_state(job).groups_failed == {1: diagnosis}

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
        state = ro._read_pipeline_state(self._job(ro, tmp_path))
        assert state.groups_failed == {1: ro.Diagnosis(ro.DiagnosisKind.QUOTA_EXHAUSTED)}

    def test_a_legacy_string_no_kind_renders_stays_unknown(self, ro, tmp_path):
        """An interpolated reason names no kind, so it is kept as written."""
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"],
            "groups_failed": {"1": "agent hit max turns (12)"},
        }))
        state = ro._read_pipeline_state(self._job(ro, tmp_path))
        assert state.groups_failed == {
            1: ro.Diagnosis(ro.DiagnosisKind.UNKNOWN, detail="agent hit max turns (12)"),
        }

    def _write_state(self, ro, tmp_path, **fields):
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"], **fields,
        }))
        return ro._read_pipeline_state(self._job(ro, tmp_path))

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
        assert ro.build_failures_body(state) == ""

    def test_a_legacy_reason_still_renders_verbatim(self, ro, tmp_path):
        state = self._state(ro, {
            1: ro.Diagnosis(ro.DiagnosisKind.UNKNOWN, detail="quota exhausted (429)"),
        })
        assert "quota exhausted (429)" in ro.build_failures_body(state)


class TestBuildFailuresBody:
    def test_no_failures_returns_empty(self):
        from review.state import PipelineState
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            done={Phase.SYNTHESIS},
        )
        assert build_failures_body(state) == ""

    def test_group_failures_produce_table(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["ui-components", "api-routes", "tests"],
            groups_done=[1], groups_failed={
                2: Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED),
                3: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5),
            },
            done={Phase.SYNTHESIS},
        )
        result = build_failures_body(state)
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
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[1], groups_failed={},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )
        result = build_failures_body(state)
        assert "synthesis" in result
        assert "fallback" in result

    def test_no_recover_hint_for_permission_errors(self):
        """The reason a denial really produces, not the bare marker.

        The check this replaced compared the whole rendered reason against the
        marker tuple, so it never fired on `agent error: permission denied`.
        """
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[],
            groups_failed={
                1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied"),
            },
            done={Phase.SYNTHESIS},
        )
        result = build_failures_body(state)
        assert "agent error: permission denied" in result
        assert "pr review --recover" not in result

    def test_recover_hint_survives_one_recoverable_failure(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[],
            groups_failed={
                1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied"),
                2: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5),
            },
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" in build_failures_body(state)

    def test_no_recover_hint_when_every_group_is_unrecoverable(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import build_failures_body
        denial = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied")
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[], groups_failed={1: denial, 2: denial},
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" not in build_failures_body(state)

    def test_recover_hint_offered_for_max_turns(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[], groups_failed={1: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5)},
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" in build_failures_body(state)

    def test_synthesis_failure_alone_stays_recoverable(self):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[1], groups_failed={},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )
        assert "pr review --recover" in build_failures_body(state)


class TestFailuresSectionInReview:
    """`set_failures_section` — what the body says, and where it lands."""

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

    def test_mechanical_fallback_includes_failures(self):
        """When synthesis falls back, the review includes ## Agent Failures."""
        from review.state import set_failures_section
        result = set_failures_section("## Summary\n\nnote\n", self._state())
        assert "## Agent Failures" in result
        assert "group-2: api" in result
        assert "quota exhausted" in result
        assert "synthesis" in result
        assert "fallback" in result

    def test_the_section_sits_above_the_summary(self):
        from review.state import set_failures_section
        result = set_failures_section("## Summary\n\nnote\n\n## Verdict\n\nApprove\n", self._state())
        assert result.index("## Agent Failures") < result.index("## Summary")

    def test_a_review_with_no_summary_still_gets_the_section(self):
        """A run that never reached synthesis has no Summary to sit above, and
        the failures are the only account of why."""
        from review.state import set_failures_section
        result = set_failures_section("## Must fix\n\n- **[M1]** a.py:1 — bug\n", self._state())
        assert "## Agent Failures" in result
        assert result.index("## Must fix") < result.index("## Agent Failures")

    def test_a_rerun_that_failed_nothing_drops_the_section(self):
        from review.state import set_failures_section
        review = "## Agent Failures\n\nold table\n\n## Summary\n\nnote\n"
        assert set_failures_section(review, self._clean_state()).startswith("## Summary")


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

    def test_replaces_existing_status_line(self, tmp_path):
        """I1: status already present as 'completed' is updated to 'partial' on synthesis failure."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import _inject_failures_and_status

        # Write a review file that already has a 'completed' status line
        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- status: completed -->\n"
            "<!-- generator: review v1 -->\n"
            "\n"
            "## Summary\n\nAll good.\n"
        )

        # Pipeline state says synthesis failed — status should be 'partial'
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
        """Status line is inserted before the generator line when not already present."""
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.state import PipelineState
        from review.state import _inject_failures_and_status

        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- generator: review v1 -->\n"
            "\n"
            "## Summary\n\nAll good.\n"
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
        from review.state import PipelineState
        from review.state import _inject_failures_and_status

        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- status: completed -->\n"
            "<!-- generator: review v1 -->\n"
            "\n"
            "## Summary\n\nAll good.\n"
        )

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
