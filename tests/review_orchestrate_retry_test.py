"""Group failure handling: `review.retry` classification and the retry pass in `review.phases`."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401



# ── 18. _check_serial_abort ─────────────────────────────────────────────────


class TestCheckSerialAbort:
    def _diagnosis(self, ro, detail: str):
        return ro.Diagnosis(ro.DiagnosisKind.AGENT_ERROR, detail=detail)

    def test_model_error(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "api_error_status": 404,
        }) + "\n")
        msg, consec, last = ro._check_serial_abort(
            1, 5, self._diagnosis(ro, "error"), str(log), 0, None,
        )
        assert "Model not available" in msg
        assert "4" in msg  # 5 - 1 = 4 remaining

    def test_consecutive_threshold(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        same = self._diagnosis(ro, "same_reason")
        msg, consec, last = ro._check_serial_abort(
            3, 10, same, str(log), ro.CONSECUTIVE_FAIL_THRESHOLD - 1, same,
        )
        assert "consecutive failures" in msg
        assert same.message in msg

    def test_equal_diagnoses_count_together_regardless_of_identity(self, ro, tmp_path):
        """Two runs failing the same way are separate objects with equal value."""
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            2, 10, self._diagnosis(ro, "boom"), str(log), 1,
            self._diagnosis(ro, "boom"),
        )
        assert msg == ""
        assert consec == 2

    def test_a_differing_turn_count_resets_the_streak(self, ro, tmp_path):
        """The turn count is part of the diagnosis, so it parts the streak.

        Unchanged from the string comparison this replaced, where the count was
        embedded in the rendered reason. It holds together in practice only
        because every group runs on the same turn budget and so exhausts at the
        same count.
        """
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            3, 10, ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=30), str(log),
            ro.CONSECUTIVE_FAIL_THRESHOLD - 1,
            ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=15),
        )
        assert msg == ""
        assert consec == 1

    def test_oversized_prompts_count_together_despite_differing_sizes(self, ro, tmp_path):
        """The detail measures the failure here rather than naming it.

        Every group renders a different number of kilobytes, so comparing whole
        diagnoses read as a new reason each time and the streak never built —
        leaving a run that cannot prompt any group grinding through all of them.
        """
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            3, 10,
            ro.Diagnosis(ro.DiagnosisKind.PROMPT_TOO_LARGE, detail="group prompt is 512KB"),
            str(log), ro.CONSECUTIVE_FAIL_THRESHOLD - 1,
            ro.Diagnosis(ro.DiagnosisKind.PROMPT_TOO_LARGE, detail="group prompt is 604KB"),
        )
        assert "consecutive failures" in msg

    def test_different_reason_resets(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        new = self._diagnosis(ro, "new_reason")
        msg, consec, last = ro._check_serial_abort(
            2, 10, new, str(log), 2, self._diagnosis(ro, "old_reason"),
        )
        assert msg == ""
        assert consec == 1
        assert last == new

    def test_below_threshold(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            1, 10, self._diagnosis(ro, "some_reason"), str(log), 0, None,
        )
        assert msg == ""
        assert consec == 1


# ── 30. _validate_group_output ──────────────────────────────────────────────


class TestValidateGroupOutput:
    def test_valid_sections(self, ro, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("## Must fix\n- **[M1]** finding\n## Nit\n- **[N1]** nit\n")
        assert ro._validate_group_output(str(f), "test") is True

    def test_no_recognized_sections(self, ro, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("## Random Header\nsome content\n")
        assert ro._validate_group_output(str(f), "test") is False

    def test_empty_file(self, ro, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("")
        assert ro._validate_group_output(str(f), "test") is True


class TestIsRetryable:
    def test_max_turns_is_retryable(self, ro):
        assert ro._is_retryable(
            ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=16)) is True

    def test_no_session_log_is_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(ro.DiagnosisKind.NO_SESSION_LOG)) is True

    def test_no_result_record_is_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(ro.DiagnosisKind.NO_RESULT_RECORD)) is True

    def test_model_error_not_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="model not available")) is False

    def test_agent_error_not_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="something broke")) is False

    def test_skipped_not_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.SKIPPED,
            detail="3 consecutive failures (agent hit max turns) — aborting")) is False

    def test_transient_is_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.TRANSIENT, detail="ETIMEDOUT")) is True


class TestRetryTurns:
    def _job_no_omitted(self, ro, tmp_path):
        return ro.ReviewJob(
            repo="org/repo", pr_number="1",
            pr=ro.PRMetadata(title="t", body="", head="f", base="main",
                             head_sha="abc", additions=1, deletions=0,
                             changed_files=1, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path), review_file=str(tmp_path / "r.md"),
            session_log=str(tmp_path / "s.jsonl"),
            mode=ro.Mode.PR,
        )

    def test_max_turns_gets_doubled(self, ro, tmp_path):
        job = self._job_no_omitted(ro, tmp_path)
        diagnosis = ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=16)
        assert ro._retry_turns(diagnosis, job) == ro.RETRY_MAX_TURNS_GROUP

    def test_other_reason_gets_default(self, ro, tmp_path):
        job = self._job_no_omitted(ro, tmp_path)
        diagnosis = ro.Diagnosis(ro.DiagnosisKind.NO_SESSION_LOG)
        assert ro._retry_turns(diagnosis, job) == ro.PHASES[ro.Phase.GROUP].max_turns


def _max_turns_16(ro):
    """Turn exhaustion, the retryable failure these tests drive retries with."""
    return ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=16)


class TestRetryFailedGroups:
    def _make_job(self, ro, tmp_path):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=5, files=[
                                 {"path": "a.go", "additions": 10, "deletions": 5, "status": "modified"},
                                 {"path": "b.go", "additions": 20, "deletions": 10, "status": "modified"},
                             ]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )

    def test_retries_max_turns_failure(self, ro, tmp_path, monkeypatch):
        import review.phases

        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        calls = []
        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(inv.max_turns)
            output_path = str(tmp_path / "group-1.md")
            Path(output_path).write_text("## Must fix\n- **[M1]** **`a.go:1`** — issue\n")
            Path(inv.session_log).write_text("")
            return 0

        monkeypatch.setattr(review.phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(review.phases, "_validate_group_output", lambda *a: None)

        failed = [ro.GroupFailure("grp-a", _max_turns_16(ro))]
        result = ro._retry_failed_groups(failed, groups, job, 1, "", None)
        assert result == []
        assert calls[-1] == ro.RETRY_MAX_TURNS_GROUP

    def test_skips_model_errors(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        failure = ro.GroupFailure("grp-a", ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="model not available"))
        result = ro._retry_failed_groups([failure], groups, job, 1, "", None)
        assert result == [failure]

    def test_every_group_out_of_turns_is_still_retried(
        self, ro, tmp_path, monkeypatch,
    ):
        """The breaker is for a broken machine, not for a large diff.

        Turn exhaustion is the one shared reason a retry answers: it comes back
        at the escalated ceiling rather than the budget that just ran out. A
        big branch makes every group run out at once, which tripped the breaker
        on `len(reasons) == 1` and skipped the retry that would have worked —
        observed as three groups at `max_turns(15)`, no retry, and a review
        shipped with every group unread.
        """
        from pathlib import Path

        import review.phases

        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name=f"grp-{c}", files=[f"{c}.go"], lines=100)
            for c in "abc"
        ]
        retried = []

        def mock_invoke(inv, **kwargs):
            retried.append(inv.label)
            # Each group writes its own artifact, as the real phase does: one
            # shared path would leave groups 2 and 3 with no output and the
            # assertion would read a harness bug as a retry that failed.
            for n in range(1, 4):
                Path(str(tmp_path / f"group-{n}.md")).write_text(
                    "## Must fix\n- **[M1]** **`a.go:1`** — issue\n",
                )
            Path(inv.session_log).write_text("")
            return 0

        monkeypatch.setattr(review.phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **kw: "p")
        monkeypatch.setattr(review.phases, "_validate_group_output", lambda *a: None)

        failed = [
            ro.GroupFailure(g.name, _max_turns_16(ro)) for g in groups
        ]
        result = ro._retry_failed_groups(failed, groups, job, 3, "", None)

        assert len(retried) == 3, "the breaker skipped a retry that would work"
        assert result == []

    # passes-at-base: the breaker's real case, which this change preserves
    def test_every_group_failing_the_same_systemic_way_still_trips_it(
        self, ro, tmp_path,
    ):
        """A missing model is not fixed by asking again with more turns."""
        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name=f"grp-{c}", files=[f"{c}.go"], lines=100)
            for c in "abc"
        ]
        failed = [
            ro.GroupFailure(g.name, ro.Diagnosis(
                ro.DiagnosisKind.TRANSIENT, detail="ECONNREFUSED"))
            for g in groups
        ]
        result = ro._retry_failed_groups(failed, groups, job, 3, "", None)
        assert result == failed

    def test_non_retryable_preserved(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        failed = [ro.GroupFailure("grp-a", ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="something broke"))]
        result = ro._retry_failed_groups(failed, groups, job, 1, "", None)
        assert result == failed

    def test_skipped_groups_run_after_retries_succeed(self, ro, tmp_path, monkeypatch):
        import review.phases

        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name="grp-a", files=["a.go"], lines=100),
            ro.Group(name="grp-b", files=["b.go"], lines=100),
        ]

        calls = []
        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            name = inv.label
            calls.append(name)
            if name == "grp-a":
                Path(job.artifact_dir, "group-1.md").write_text("## Must fix\n- **[M1]** **`a.go:1`** — issue\n")
            elif name == "grp-b":
                Path(job.artifact_dir, "group-2.md").write_text("## Must fix\n- **[M1]** **`b.go:1`** — issue\n")
            Path(inv.session_log).write_text("")
            return 0

        monkeypatch.setattr(review.phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(review.phases, "_validate_group_output", lambda *a: None)

        failed = [
            ro.GroupFailure("grp-a", _max_turns_16(ro)),
            ro.GroupFailure("grp-b", ro.Diagnosis(
                ro.DiagnosisKind.SKIPPED,
                detail="3 consecutive failures (agent hit max turns) — aborting remaining 1 groups")),
        ]
        result = ro._retry_failed_groups(failed, groups, job, 2, "", None)
        assert result == []
        assert "grp-a" in calls
        assert "grp-b" in calls

    def test_skipped_groups_kept_when_retries_fail(self, ro, tmp_path, monkeypatch):
        import review.phases

        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name="grp-a", files=["a.go"], lines=100),
            ro.Group(name="grp-b", files=["b.go"], lines=100),
        ]

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(inv.session_log).write_text("")
            return 1

        monkeypatch.setattr(review.phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(
            review.phases, "diagnose_missing_output",
            lambda *a, **kw: ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=30),
        )

        failed = [
            ro.GroupFailure("grp-a", _max_turns_16(ro)),
            ro.GroupFailure("grp-b", ro.Diagnosis(
                ro.DiagnosisKind.SKIPPED,
                detail="3 consecutive failures (agent hit max turns) — aborting")),
        ]
        result = ro._retry_failed_groups(failed, groups, job, 2, "", None)
        # grp-a retry failed, so grp-b stays as skipped
        assert len(result) == 2
        result_names = [f.group for f in result]
        assert "grp-a" in result_names
        assert "grp-b" in result_names
