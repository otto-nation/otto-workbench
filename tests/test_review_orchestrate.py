import contextlib
import dataclasses
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from conftest import add_self_origin, commit_all, git_out, init_repo, synthetic_review

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent_types import Phase
from review_document import BUDGET_SUMMARY, FALLBACK_SUMMARY, SKIPPED_SUMMARY




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


# ── 19. resolve_model ──────────────────────────────────────────────────────


@pytest.fixture
def no_model_env(monkeypatch):
    """A clean slate — the developer's own shell usually has these set."""
    for key in ("WORKBENCH_AI_MODEL", "UNUSED_KEY", "MY_MODEL_KEY",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        monkeypatch.delenv(key, raising=False)


class TestResolveModel:
    def _clear_alias_envs(self, ro, monkeypatch):
        for alias in ro.ModelAlias:
            monkeypatch.delenv(alias.env_key, raising=False)

    def test_alias_env_keys_follow_convention(self, ro):
        assert ro.ModelAlias.SONNET.env_key == "ANTHROPIC_DEFAULT_SONNET_MODEL"
        assert ro.ModelAlias.OPUS.env_key == "ANTHROPIC_DEFAULT_OPUS_MODEL"
        assert ro.ModelAlias.HAIKU.env_key == "ANTHROPIC_DEFAULT_HAIKU_MODEL"

    def test_parse_rejects_concrete_model_id(self, ro):
        assert ro.ModelAlias.parse("claude-sonnet-5") is None
        assert ro.ModelAlias.parse("sonnet") is ro.ModelAlias.SONNET

    def test_explicit(self, ro, no_model_env):
        assert ro.resolve_model("opus", "SOME_KEY", "sonnet") == "opus"

    def test_env_key(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("MY_MODEL_KEY", "haiku")

        assert ro.resolve_model("", "MY_MODEL_KEY", "sonnet") == "haiku"

    def test_global_env(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "opus")
        self._clear_alias_envs(ro, monkeypatch)
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "opus"

    def test_global_env_alias_resolved(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MODEL", "opus")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6")
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "claude-opus-4-6"

    def test_default_fallback(self, ro, no_model_env):
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "sonnet"

    def test_alias_resolved_via_env(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        assert ro.resolve_model("", "UNUSED_KEY", "sonnet") == "claude-sonnet-5"

    def test_explicit_alias_resolved(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6")
        assert ro.resolve_model("opus", "SOME_KEY", "sonnet") == "claude-opus-4-6"

    def test_env_key_alias_resolved(self, ro, monkeypatch):
        monkeypatch.setenv("MY_MODEL_KEY", "haiku")
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5@20251001")
        assert ro.resolve_model("", "MY_MODEL_KEY", "sonnet") == "claude-haiku-4-5@20251001"

    def test_empty_alias_env_falls_back_to_alias(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
        assert ro.resolve_model("sonnet", "SOME_KEY", "opus") == "sonnet"

    def test_full_model_id_not_resolved(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        assert ro.resolve_model("claude-sonnet-5", "SOME_KEY", "sonnet") == "claude-sonnet-5"


# ── 19b. phase_model / collect_phase_models ─────────────────────────────────


class TestPhaseModel:
    def _clean_env(self, ro, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        for phase in ro.Phase:
            monkeypatch.delenv(phase.model_env_key, raising=False)
        for alias in ro.ModelAlias:
            monkeypatch.delenv(alias.env_key, raising=False)

    def test_phase_env_keys_follow_convention(self, ro):
        assert ro.Phase.SCOUT.model_env_key == "WORKBENCH_AI_SCOUT_MODEL"
        assert ro.Phase.SCOUT.thinking_env_key == "WORKBENCH_AI_SCOUT_THINKING"

    def test_default(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        assert ro.phase_model("scout", "") == "sonnet"

    def test_env_key_derived_from_phase_name(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "") == "claude-haiku-4-5"
        assert ro.phase_model("group", "") == "sonnet"

    def test_explicit_overrides_env(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "claude-opus-5") == "claude-opus-5"

    def test_collect_groups_phases_by_model(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        models = ro.collect_phase_models("")
        assert models["claude-haiku-4-5"] == ["scout"]
        assert set(models["claude-sonnet-5"]) == set(ro.REVIEW_PHASES) - {"scout"}

    def test_collect_covers_every_review_phase(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        models = ro.collect_phase_models("")
        phases = [p for group in models.values() for p in group]
        assert sorted(phases) == sorted(ro.REVIEW_PHASES)

    def test_a_phase_from_another_entry_point_is_not_preflighted(self, ro, monkeypatch):
        """Preflight resolves the models a *review* is about to run.

        A fix pass belonging to `pr comments` or `pr ci` never runs here, so a
        bad model pinned on it must not fail a review before it starts.
        """
        self._clean_env(ro, monkeypatch)
        models = ro.collect_phase_models("")
        named = {p for group in models.values() for p in group}
        assert not named & {ro.Phase.COMMENTS_FIX, ro.Phase.CI_FIX}


# ── 19c. enum_arg ───────────────────────────────────────────────────────────


class TestEnumArg:
    def test_converts_to_enum_member(self, ro):
        assert ro.enum_arg(ro.Mode)("self") is ro.Mode.SELF

    def test_error_lists_valid_choices(self, ro):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError) as exc:
            ro.enum_arg(ro.Effort)("bogus")
        assert str(exc.value) == "invalid choice: 'bogus' (choose from 'low', 'medium', 'high')"


# ── 20. _extract_heredoc ────────────────────────────────────────────────────


class TestExtractHeredoc:
    def test_with_eof(self, ro):
        cmd = "cat << EOF\nhello world\nline two\nEOF"
        assert ro._extract_heredoc(cmd) == "hello world\nline two"

    def test_with_review_eof(self, ro):
        cmd = "cat << REVIEW_EOF\ncontent here\nREVIEW_EOF"
        assert ro._extract_heredoc(cmd) == "content here"

    def test_no_heredoc(self, ro):
        assert ro._extract_heredoc("echo hello") == ""

    def test_multiline_content(self, ro):
        cmd = "cat << EOF\nline 1\nline 2\nline 3\nEOF"
        result = ro._extract_heredoc(cmd)
        assert "line 1" in result
        assert "line 2" in result
        assert "line 3" in result


# ── 21. _extract_denied_content ─────────────────────────────────────────────


class TestExtractDeniedContent:
    def test_content_in_tool_input(self, ro):
        denial = {"tool_input": {"content": "## Must fix\nfinding"}}
        assert ro._extract_denied_content(denial) == "## Must fix\nfinding"

    def test_bash_command_with_heredoc(self, ro):
        denial = {"tool_input": {"command": "cat << EOF\n## Must fix\nfinding\nEOF"}}
        result = ro._extract_denied_content(denial)
        assert "## Must fix" in result

    def test_bash_command_without_heredoc(self, ro):
        denial = {"tool_input": {"command": "echo hello"}}
        assert ro._extract_denied_content(denial) == ""


# ── 22. try_recover_output ─────────────────────────────────────────────────


class TestTryRecoverOutput:
    def test_recover_from_denied_write(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [{
                "tool_input": {"content": "## Must fix\n- **[M1]** finding\n"}
            }],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is True
        assert output.exists()
        assert "## Must fix" in output.read_text()

    def test_recover_from_bash_heredoc(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [{
                "tool_input": {"command": "cat << EOF\n## Should fix\ncontent\nEOF"}
            }],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is True
        assert "## Should fix" in output.read_text()

    def test_no_denials(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({"type": "result", "permission_denials": []}) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is False

    def test_denial_no_section_headers(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [{
                "tool_input": {"content": "just text no sections"}
            }],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is False

    def test_missing_log_file(self, ro, tmp_path):
        assert ro.try_recover_output(
            str(tmp_path / "missing.jsonl"),
            str(tmp_path / "output.md"),
        ) is False


# ── 23. _diagnose_result_type ───────────────────────────────────────────────


class TestDiagnoseResultType:
    def test_max_turns(self, ro):
        result = {"subtype": "max_turns", "num_turns": 15}
        diag = ro._diagnose_result_type(result)
        assert diag == ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=15)

    def test_error(self, ro):
        result = {"is_error": True, "errors": ["timeout"]}
        diag = ro._diagnose_result_type(result)
        assert diag == ro.Diagnosis(ro.DiagnosisKind.AGENT_ERROR, detail="timeout")

    def test_completed_no_output(self, ro):
        result = {"subtype": "completed"}
        diag = ro._diagnose_result_type(result)
        assert diag == ro.Diagnosis(ro.DiagnosisKind.COMPLETED, detail="completed")


# ── 24. diagnose_missing_output ────────────────────────────────────────────


class TestDiagnoseMissingOutput:
    def test_max_turns_result(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "max_turns", "num_turns": 10,
        }) + "\n")
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.MAX_TURNS

    def test_no_log_file(self, ro, tmp_path):
        result = ro.diagnose_missing_output(str(tmp_path / "missing.jsonl"))
        assert result.kind is ro.DiagnosisKind.NO_SESSION_LOG

    def test_empty_log(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text("")
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.NO_RESULT_RECORD

    def test_no_result_records(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "assistant", "message": "hi"}) + "\n")
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.NO_RESULT_RECORD

    def test_quota_exhausted_no_result(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "system", "subtype": "api_retry", "error_status": 429}) + "\n"
        )
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.QUOTA_EXHAUSTED


# ── 25. _is_model_error ─────────────────────────────────────────────────────


class TestIsModelError:
    def test_404_error(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "api_error_status": 404,
        }) + "\n")
        assert ro._is_model_error(str(log)) is True

    def test_not_available(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "result": "The model is Not Available right now",
        }) + "\n")
        assert ro._is_model_error(str(log)) is True

    def test_normal_completion(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "completed", "result": "done",
        }) + "\n")
        assert ro._is_model_error(str(log)) is False

    def test_missing_log(self, ro, tmp_path):
        assert ro._is_model_error(str(tmp_path / "missing.jsonl")) is False


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


# ── 31. _document ───────────────────────────────────────────────────────────


class TestDocument:
    def _job(self, ro, tmp_path, **kwargs):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test", body="", head="feat", base="main",
                             head_sha="abc123", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            generator_version="1.0.0",
            **kwargs,
        )

    def test_a_full_review_is_framed_by_what_the_sidecar_records(self, ro, tmp_path):
        from datetime import date
        rendered = ro._document(self._job(ro, tmp_path), "## Summary\nbody\n").render()
        assert rendered == (
            "# Review: org/repo#42 — test\n"
            f"<!-- date: {date.today().isoformat()} -->\n"
            "<!-- head_sha: abc123 -->\n"
            "<!-- review_type: full -->\n"
            "<!-- generator: 1.0.0 -->\n"
            "\n"
            "## Summary\nbody\n"
        )

    def test_a_full_review_reports_no_group_ratio(self, ro, tmp_path):
        """Skipped groups are a claim about an incremental run: the pipeline
        passes the count on every path, and a full review states none."""
        document = ro._document(
            self._job(ro, tmp_path), "## Summary\n",
            skipped_groups=1, total_groups=4,
        )
        assert "skipped_groups" not in document.render()

    def _incremental(self, ro, tmp_path, prior_review=""):
        job = self._job(ro, tmp_path, prior_review=prior_review)
        job.preflight = ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            claude_md="", architecture_md="",
            delta_files=["a.py"], prior_head_sha="def456",
        )
        return job

    def test_an_incremental_review_dates_itself_against_the_prior_one(self, ro, tmp_path):
        prior = (
            "# Review: org/repo#42\n"
            "<!-- date: 2026-08-20 -->\n"
            "<!-- head_sha: old -->\n"
        )
        document = ro._document(
            self._incremental(ro, tmp_path, prior), "## Summary\n",
            skipped_groups=1, total_groups=4,
        )
        assert document.header.prior_date == "2026-08-20"
        assert document.header.prior_sha == "def456"
        assert "<!-- skipped_groups: 1/4 -->" in document.render()

    def test_an_incremental_review_with_no_prior_document_says_so(self, ro, tmp_path):
        job = self._incremental(ro, tmp_path)
        assert ro._document(job, "## Summary\n").header.prior_date == "unknown"


# ── 32. _build_mechanical_fallback ──────────────────────────────────────────


class TestBuildMechanicalFallback:
    def test_pr_mode(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._build_mechanical_fallback(job, 3, merged).render()
        assert result.startswith("# Review: org/repo#42 — test PR\n")
        assert "Verdict" in result
        assert "1 finding" in result

    def test_self_review_mode(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="",
            pr=ro.PRMetadata(title="test", body="", head="my-branch", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.SELF,
        )
        merged = "## Nit\n- **[N1]** **`file.go:1`** — style\n"
        result = ro._build_mechanical_fallback(job, 2, merged).render()
        assert result.startswith("# Self-Review: org/repo — my-branch\n")
        assert "Verdict" not in result

    def test_counts_correct(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        merged = (
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
            "## Nit\n- **[N1]** **`b.go:2`** — style\n- **[N2]** **`c.go:3`** — naming\n"
        )
        result = ro._build_mechanical_fallback(job, 3, merged)
        assert "3 findings" in result.body


# ── 33. _write_clean_review ─────────────────────────────────────────────────


class TestWriteCleanReview:
    def test_pr_mode(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="clean PR", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=3, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(review_file),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        ro._write_clean_review(job, 2)
        content = review_file.read_text()
        assert "# Review:" in content
        assert "No issues found" in content
        assert "Approve" in content

    def test_self_review_mode(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        job = ro.ReviewJob(
            repo="org/repo", pr_number="",
            pr=ro.PRMetadata(title="self", body="", head="my-branch", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=3, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(review_file),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.SELF,
        )
        ro._write_clean_review(job, 2)
        content = review_file.read_text()
        assert "# Self-Review:" in content
        assert "No issues found" in content
        assert "Verdict" not in content


# ── 34. _parse_session_cost ─────────────────────────────────────────────────


class TestParseSessionCost:
    def test_valid_log(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "result", "total_cost_usd": 1.5}) + "\n"
            + json.dumps({"type": "result", "total_cost_usd": 0.5}) + "\n"
        )
        assert ro._parse_session_cost(str(log)) == 2.0

    def test_missing_file(self, ro, tmp_path):
        assert ro._parse_session_cost(str(tmp_path / "missing.jsonl")) == 0.0

    def test_no_result_records(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "assistant", "message": "hi"}) + "\n")
        assert ro._parse_session_cost(str(log)) == 0.0


# ── 35. _is_complete_review ─────────────────────────────────────────────────


class TestIsCompleteReview:
    def test_has_summary(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Summary\nLooks good.\n")
        assert ro._is_complete_review(str(f)) is True

    def test_has_verdict(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Verdict\nApprove.\n")
        assert ro._is_complete_review(str(f)) is True

    def test_missing_headers(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\nSome content without required headers.\n")
        assert ro._is_complete_review(str(f)) is False

    def test_file_not_exists(self, ro, tmp_path):
        assert ro._is_complete_review(str(tmp_path / "nonexistent.md")) is False

    def test_empty_file(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("")
        assert ro._is_complete_review(str(f)) is False


class TestPhaseSynthesis:
    def _make_job(self, ro, tmp_path, mode="pr"):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode),
        )

    @staticmethod
    def _patch_pipeline(monkeypatch, ro, **overrides):
        """Patch the module-level imports `_phase_synthesis` reaches through.

        The synthesis phase spans two modules: it builds its own prompt and
        post-processes its own findings, but invokes the agent through
        `PhaseRunner`, whose bindings live in review_phases. Each name is
        patched on whichever module binds it.
        """
        import review_phases
        import review_pipeline
        defaults = {
            "build_prompt": lambda *a, **kw: "mock prompt",
            "post_process_findings": lambda *a, **kw: None,
        }
        defaults.update(overrides)
        for name, func in defaults.items():
            owner = review_pipeline if hasattr(review_pipeline, name) else review_phases
            monkeypatch.setattr(owner, name, func)

    def test_successful_synthesis(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review: org/repo#42 — test PR\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, ro, run_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, "merged content")

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert FALLBACK_SUMMARY not in result

    def test_cost_comes_from_the_session_log(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"

        def mock_invoke(inv, **kwargs):
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 0

        self._patch_pipeline(monkeypatch, ro, run_agent=mock_invoke)

        assert ro._phase_synthesis(job, "", 3, "merged content").cost == 2.5

    def test_a_fallen_back_synthesis_is_still_charged(self, ro, tmp_path, monkeypatch):
        """The agent ran and spent; the review file just came from the merge."""
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._phase_synthesis(job, "", 3, merged)

        assert FALLBACK_SUMMARY in Path(job.review_file).read_text()
        assert result.cost == 2.5

    def test_an_unpromptable_synthesis_falls_back_to_the_merge(
        self, ro, tmp_path, monkeypatch,
    ):
        """The findings are already on disk; only the write-up would not fit.

        Merging them mechanically keeps every finding and loses the prose,
        which is the same trade a synthesis agent that failed already makes —
        and strictly better than discarding a phase's worth of group reviews
        because their cover letter is over the budget.
        """
        from review_prompt import PromptTooLarge

        job = self._make_job(ro, tmp_path)
        invoked = []

        def boom(*_a, **_kw):
            raise PromptTooLarge("synthesis.md", 600_000)

        self._patch_pipeline(
            monkeypatch, ro,
            build_prompt=boom,
            run_agent=lambda inv, **kw: invoked.append(inv),
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        assert invoked == []
        written = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in written
        assert "file.go:1" in written

    def test_agent_fails_no_output(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(inv.session_log).write_text("")
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result

    def test_incomplete_output_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            # Write incomplete review (no Summary or Verdict headers)
            Path(job.review_file).write_text("# Review\nSome partial content\n")
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, ro, run_agent=mock_invoke)

        merged = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result

    def test_transient_error_retries_then_succeeds(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review: org/repo#42 — test PR\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            if len(calls) == 1:
                Path(inv.session_log).write_text(
                    '{"type":"result","subtype":"success","is_error":true,'
                    '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
                )
                return 1
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, ro, run_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, "merged content")

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert FALLBACK_SUMMARY not in result
        assert len(calls) == 2

    def test_transient_error_retries_then_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result
        assert len(calls) == 2

    def test_non_transient_error_does_not_retry(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"agent error: something broke"}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result
        assert len(calls) == 1


class TestSynthesisIsAskedAboutWhatTheGroupsPassedOver:
    """A prior finding the groups never mentioned reaches the last agent that can act.

    Before this, reconciliation ran only after synthesis had written the
    document, so a finding whose subject the tree still held left the run as a
    warning and left the review saying nothing about it — which the next round
    reads as the finding no longer existing.
    """

    BEFORE = "func handle() {\n    rows, _ := db.Query(sql)\n}\n"
    PRIOR_LINE = (
        "- **[M1]** **`handler.go:2`** — `rows, _ := db.Query(sql)` drops the error"
    )

    def _job(self, ro, tmp_path, prior_review):
        wt = tmp_path / "wt"
        wt.mkdir()
        git_out(wt, "init", "-q", "-b", "main")
        git_out(wt, "config", "user.email", "test@example.com")
        git_out(wt, "config", "user.name", "Test")
        git_out(wt, "config", "commit.gpgsign", "false")
        git_out(wt, "config", "core.hooksPath", str(tmp_path / "hooks"))
        (wt / "handler.go").write_text(self.BEFORE)
        git_out(wt, "add", "-A")
        git_out(wt, "commit", "-qm", "before")
        prior_sha = git_out(wt, "rev-parse", "HEAD").strip()

        job = TestPhaseSynthesis()._make_job(ro, tmp_path)
        job.wt_path = str(wt)
        job.prior_review = (
            f"# Review: org/repo#42 — t\n<!-- head_sha: {prior_sha} -->\n"
            f"## Must fix\n{prior_review}\n"
        )
        return job

    def _extras(self, ro, tmp_path, monkeypatch, merged, prior_review=PRIOR_LINE):
        job = self._job(ro, tmp_path, prior_review)
        seen = {}

        def capture(phase, j, **extra):
            seen.update(extra)
            return "mock prompt"

        def mock_invoke(inv, **kwargs):
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nok.\n\n## Verdict\nApprove.\n")
            Path(inv.session_log).write_text("")
            return 0

        TestPhaseSynthesis._patch_pipeline(
            monkeypatch, ro, build_prompt=capture, run_agent=mock_invoke,
        )
        ro._phase_synthesis(job, "", 3, merged)
        return seen

    def test_a_finding_no_group_mentioned_is_handed_to_synthesis(
        self, ro, tmp_path, monkeypatch,
    ):
        extras = self._extras(
            ro, tmp_path, monkeypatch, "## Must fix\n- **[M9]** **`other.go:1`** — x\n",
        )
        assert [f.ref.finding_id for f in extras["unaccounted_prior"]] == ["M1"]

    def test_a_finding_the_groups_settled_is_not_asked_about_again(
        self, ro, tmp_path, monkeypatch,
    ):
        merged = "## Prior findings\n- **[M1]** `handler.go` — Fixed\n"
        assert self._extras(ro, tmp_path, monkeypatch, merged)["unaccounted_prior"] == []

    def test_a_prior_review_that_reported_nothing_hands_over_nothing(
        self, ro, tmp_path, monkeypatch,
    ):
        assert self._extras(
            ro, tmp_path, monkeypatch, "## Must fix\n", prior_review="",
        )["unaccounted_prior"] == []


class TestRunSynthesisOrFallback:
    """What the synthesis step records in state, and what it reports spending."""

    # A merge with one finding in it, so the step has something to carry: the
    # clean-review shortcut returns before synthesis and would answer for every
    # test below whatever the branch under test does.
    MERGED = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"

    def _make_state(self, ro):
        return ro.PipelineState(
            head_sha="abc123",
            group_names=["grp-1"],
            done={Phase.HOLISTIC},
            groups_done=[1],
        )

    def _make_job(self, ro, tmp_path, mode="pr", skip_phases=frozenset()):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode), skip_phases=skip_phases,
        )

    def test_self_review_fallback_detected(self, ro, tmp_path, monkeypatch):
        """Self-reviews without verdict must still detect mechanical fallback."""
        import review_pipeline

        job = self._make_job(ro, tmp_path, mode="self")
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            ro._build_mechanical_fallback(
                job, count, merged, skipped_groups=skipped_groups,
            ).write(job.review_file)
            return review_pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"))

        monkeypatch.setattr(review_pipeline, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert state.failed == {Phase.SYNTHESIS: ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)}

    def test_synthesis_reports_what_its_log_records(self, ro, tmp_path, monkeypatch):
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            from pathlib import Path
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nok\n\n## Verdict\nApprove\n")
            return review_pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"), 1.25)

        monkeypatch.setattr(review_pipeline, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result.cost == 1.25

    def test_a_clean_review_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, "No findings.\n", [], 0, 0.0, 20.0,
        )
        assert result == review_pipeline.PhaseResult()

    def test_a_mention_of_a_finding_id_is_not_a_finding(self, ro, tmp_path, monkeypatch):
        """The merge declares nothing, so the review is clean — a triage note
        naming a prior ID used to send the run to synthesis with no findings."""
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(
            review_pipeline, "_phase_synthesis",
            lambda *a, **kw: pytest.fail("synthesis ran for a review with no findings"))

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1,
            "## File Triage\n- `api.go` — reviewed, [M1] was fixed here\n", [],
            0, 0.0, 20.0,
        )
        assert result == review_pipeline.PhaseResult()

    def test_a_synthesis_skipped_on_budget_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 25.0, 20.0,
        )
        assert result == review_pipeline.PhaseResult()

    def test_no_group_reports_partial_not_a_failed_run(self, ro, tmp_path, monkeypatch):
        """A group phase the operator switched off is not the pipeline failing.

        Every group is unreviewed either way, so the honest verdict is partial
        with `skipped` as the reason — `all groups failed` would blame the
        agents for a review nobody asked to run.
        """
        import review_pipeline

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.GROUP}))
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        skipped = ro.Diagnosis(ro.DiagnosisKind.SKIPPED, detail="--no-group")
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [ro.GroupFailure("grp-1", skipped)],
            0, 0.0, 20.0,
        )
        assert state.failed == {Phase.SYNTHESIS: skipped}
        assert state.status is ro.ReviewStatus.PARTIAL

    def test_no_synthesis_writes_the_mechanical_merge(self, ro, tmp_path, monkeypatch):
        """`--no-synthesis` reaches the review file without an agent."""
        import review_pipeline

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        # The merge is post-processed like any other review, and that drops a
        # finding whose file it cannot find.
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(
            review_pipeline, "_phase_synthesis",
            lambda *a, **kw: pytest.fail("synthesis ran despite --no-synthesis"))

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result == review_pipeline.PhaseResult()
        assert Phase.SYNTHESIS in state.done
        assert state.failed == {}
        assert "api.go:10" in Path(job.review_file).read_text()

    def test_no_synthesis_writes_a_summary_the_gate_can_resume_from(
        self, ro, tmp_path, monkeypatch,
    ):
        """`--no-synthesis` leaves a review `_is_complete_review` accepts.

        Without the section a run resumed at the disprove gate reads its own
        review as unfinished and re-enters synthesis to rewrite it.
        """
        import review_pipeline

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 0.0, 20.0,
        )

        written = Path(job.review_file).read_text()
        assert "## Summary" in written
        assert SKIPPED_SUMMARY in written
        # The operator stopped synthesis; no agent failed.
        assert FALLBACK_SUMMARY not in written
        assert ro._is_complete_review(job.review_file)

    def test_a_budget_cut_off_writes_a_summary_naming_the_budget(
        self, ro, tmp_path, monkeypatch,
    ):
        """The budget path says why synthesis did not run, not that it failed."""
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 25.0, 20.0,
        )

        written = Path(job.review_file).read_text()
        assert "## Summary" in written
        assert BUDGET_SUMMARY in written
        assert FALLBACK_SUMMARY not in written
        assert ro._is_complete_review(job.review_file)

    def test_a_budget_cut_off_still_checks_its_findings_against_the_tree(
        self, ro, tmp_path, monkeypatch,
    ):
        """The run least able to afford an unchecked claim still checks them.

        Evidence verification and prior-finding reconciliation read the work
        tree and spend none of the budget that ran out, so the path that ships
        group output on a cut-off post-processes it like every other one.
        """
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 25.0, 20.0,
        )

        assert job.verification is not None

    @pytest.mark.parametrize(
        ("skipped", "cost_so_far"), [(True, 0.0), (False, 25.0)],
        ids=["no-synthesis", "budget"],
    )
    def test_neither_no_synthesis_path_states_a_verdict(
        self, ro, tmp_path, monkeypatch, skipped, cost_so_far,
    ):
        """Neither path weighed the review, so neither approves or blocks it."""
        import review_pipeline

        job = self._make_job(
            ro, tmp_path,
            skip_phases=frozenset({ro.Phase.SYNTHESIS}) if skipped else frozenset(),
        )
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, cost_so_far, 20.0,
        )

        assert "## Verdict" not in Path(job.review_file).read_text()


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


class TestTransientClassification:
    """Which backend errors the classifier files as TRANSIENT rather than fatal.

    Driven through `diagnose_missing_output` because the classifier is only
    reached once a crash is established — a marker in the output of a run that
    ended on its own terms is not an error report.
    """

    def _kind(self, ro, tmp_path, detail: str):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "error", "is_error": True,
            "result": detail,
        }) + "\n")
        return ro.diagnose_missing_output(str(log)).kind

    def test_socket_error(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path,
            "API Error: Connection to the API was lost (FailedToOpenSocket).",
        ) is ro.DiagnosisKind.TRANSIENT

    def test_connection_refused(self, ro, tmp_path):
        assert self._kind(ro, tmp_path, "ConnectionRefused") is ro.DiagnosisKind.TRANSIENT

    def test_connection_reset(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path, "Connection to the API was lost (ConnectionReset)",
        ) is ro.DiagnosisKind.TRANSIENT

    def test_etimedout(self, ro, tmp_path):
        assert self._kind(ro, tmp_path, "ETIMEDOUT") is ro.DiagnosisKind.TRANSIENT

    def test_model_error_not_transient(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path, "model not available") is ro.DiagnosisKind.AGENT_ERROR

    def test_generic_agent_error_not_transient(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path, "something broke") is ro.DiagnosisKind.AGENT_ERROR

    def test_a_marker_in_a_clean_run_is_not_an_error_report(self, ro, tmp_path):
        """A successful run whose output happens to mention a socket fault."""
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "success", "result": "ECONNRESET",
        }) + "\n")
        assert ro.diagnose_missing_output(str(log)).kind is ro.DiagnosisKind.COMPLETED


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
        import review_phases

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

        monkeypatch.setattr(review_phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(review_phases, "_validate_group_output", lambda *a: None)

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

    def test_non_retryable_preserved(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        failed = [ro.GroupFailure("grp-a", ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="something broke"))]
        result = ro._retry_failed_groups(failed, groups, job, 1, "", None)
        assert result == failed

    def test_skipped_groups_run_after_retries_succeed(self, ro, tmp_path, monkeypatch):
        import review_phases

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

        monkeypatch.setattr(review_phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(review_phases, "_validate_group_output", lambda *a: None)

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
        import review_phases

        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name="grp-a", files=["a.go"], lines=100),
            ro.Group(name="grp-b", files=["b.go"], lines=100),
        ]

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(inv.session_log).write_text("")
            return 1

        monkeypatch.setattr(review_phases, "run_agent", mock_invoke)
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(
            review_phases, "diagnose_missing_output",
            lambda *a: ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=30),
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


# ── Prompt stats persistence ────────────────────────────────────────────────


class TestPromptStats:
    def test_prompt_stats_written(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        ro._log_prompt_size("test", "hello world", {"sec": "data"}, job)

        stats_file = tmp_path / ro.FILENAME_PROMPT_STATS
        assert stats_file.exists()
        stats = json.loads(stats_file.read_text())
        assert isinstance(stats, list)
        assert stats[0]["template"] == "test"
        assert stats[0]["prompt_bytes"] == len(b"hello world")
        assert "utilization_pct" in stats[0]
        assert stats[0]["sections"]["sec"] == len(b"data")

    def test_prompt_stats_appends(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        ro._log_prompt_size("first", "aaa", {}, job)
        ro._log_prompt_size("second", "bbb", {}, job)

        stats = json.loads((tmp_path / ro.FILENAME_PROMPT_STATS).read_text())
        assert len(stats) == 2
        assert stats[0]["template"] == "first"
        assert stats[1]["template"] == "second"

    def test_prompt_stats_survives_corrupt_file(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        stats_file = tmp_path / ro.FILENAME_PROMPT_STATS
        stats_file.write_text("")

        ro._log_prompt_size("test", "hello", {}, job)

        stats = json.loads(stats_file.read_text())
        assert isinstance(stats, list)
        assert len(stats) == 1
        assert stats[0]["template"] == "test"


# ── _write_review_sidecar enriched meta.json ────────────────────────────────


class TestWriteReviewSidecar:
    @staticmethod
    def _make_job(ro, tmp_path):
        pr = ro.PRMetadata(
            title="Test PR", body="", head="feat/test", base="main",
            head_sha="abc123", additions=10, deletions=5,
            changed_files=3, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        return ro.ReviewJob(
            repo="org/repo", pr_number="42", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "session.jsonl"),
            generator_version="test-v1",
        )

    def test_meta_includes_title_and_changed_files(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["title"] == "Test PR"
        assert meta["changed_files"] == 3

    def test_meta_includes_mode(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["mode"] == "pr"

    def test_meta_includes_generator_version(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["generator_version"] == "test-v1"

    def test_meta_states_no_generator_when_the_run_had_none(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.generator_version = ""
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert not meta["generator_version"]

    def test_meta_records_the_runs_start(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.started_at = "2026-08-18T13:47:03+00:00"
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["started_at"] == "2026-08-18T13:47:03+00:00"

    def test_meta_claims_nothing_about_being_reviewed(self, ro, tmp_path):
        """The sidecar is written from every branch that reaches a review file,
        so it cannot be the thing that says a review was produced."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert not meta["reviewed_at"]

    def test_a_second_write_keeps_the_same_start(self, ro, tmp_path):
        """One run, one start — the sidecar carries the job's stamp, not the
        clock at each of the branches that write it."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        first = json.loads((tmp_path / "meta.json").read_text())["started_at"]
        ro._write_review_sidecar(job)
        assert json.loads((tmp_path / "meta.json").read_text())["started_at"] == first

    def test_every_key_on_disk_is_a_field_of_the_type(self, ro, tmp_path):
        """What the sidecar having an owner buys: a writer cannot record a key
        no reader can name, and cannot drop one a reader looks for."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert set(meta) == {f.name for f in dataclasses.fields(ro.ReviewMeta)}

    def test_the_sidecar_reads_back_as_what_was_written(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        assert ro.read_review_meta(tmp_path) == ro._job_meta(job)

    def test_an_incremental_run_records_what_it_is_a_delta_against(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.preflight = ro.PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            claude_md="", architecture_md="",
            delta_files=["a.py", "b.py"], prior_head_sha="dead00",
        )
        ro._write_review_sidecar(job)
        meta = ro.read_review_meta(tmp_path)
        assert meta.review_type == ro.ReviewType.INCREMENTAL
        assert meta.prior_sha == "dead00"
        assert meta.delta_files == ("a.py", "b.py")

    def test_a_full_run_records_no_delta(self, ro, tmp_path):
        """A full review is a delta against nothing, which is not the same
        claim as a delta against a prior review that moved no files."""
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = ro.read_review_meta(tmp_path)
        assert meta.review_type == ro.ReviewType.FULL
        assert (meta.prior_sha, meta.delta_files) == ("", ())


# ── is_quota_error ───────────────────────────────────────────────────


class TestIsQuotaError:
    def test_detects_429_retry(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "system", "subtype": "api_retry", "error_status": 429}) + "\n"
        )
        assert ro.is_quota_error(str(log)) is True

    def test_ignores_non_429(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "api_retry", "error_status": 500}) + "\n"
        )
        assert ro.is_quota_error(str(log)) is False

    def test_false_for_normal_session(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "result", "subtype": "completed"}) + "\n"
        )
        assert ro.is_quota_error(str(log)) is False

    def test_false_for_missing_log(self, ro, tmp_path):
        assert ro.is_quota_error(str(tmp_path / "missing.jsonl")) is False

    def test_false_for_empty_log(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text("")
        assert ro.is_quota_error(str(log)) is False


# ── build_prompt (preflight) ──────────────────────────────────────────


class TestBuildPromptPreflight:
    def test_includes_preflight_data_when_set(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="desc", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix it", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            claude_md="# Project",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result
        assert "--- a/a.go" in result

    def test_no_preflight_data_when_not_set(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="desc", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix it", reviews="[]",
            review_comments="[]", comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "Pre-collected data" not in result

    def test_synthesis_includes_reviews_section(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix",
            reviews='[{"user":"bob","state":"APPROVED"}]',
            review_comments="[]",
            comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(
            ro.Phase.SYNTHESIS, job, max_turns=15,
            holistic_content="assessment",
            group_count=1,
            merged_content="## Must fix\n- [M1] bug",
        )
        assert "bob" in result
        assert "APPROVED" in result

    def test_group_template_gets_scoped_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=20, deletions=10, changed_files=2,
            files=[
                {"path": "a.go", "additions": 10, "deletions": 5},
                {"path": "b.go", "additions": 10, "deletions": 5},
            ],
        )
        ctx = ro.PRContext()
        preflight = ro.PreflightData(
            diff="full diff here",
            commit_log="commits",
            file_contents={"a.go": "package a", "b.go": "package b"},
            file_permissions={"a.go": "0o644", "b.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(
            ro.Phase.GROUP, job, max_turns=15,
            group_idx=1, group_count=2, group_name="pkg",
            group_files_formatted="  - a.go (+10 -5)",
            holistic_content="",
            group_file_paths=["a.go"],
        )
        assert "package a" in result
        assert "package b" not in result

    def test_holistic_template_includes_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            claude_md="# Proj",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.HOLISTIC, job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result

    def test_self_review_template_includes_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext()
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight, mode=ro.Mode.SELF,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result

    def test_env_section_all_files_pre_collected(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="diff", commit_log="log",
            file_contents={"a.go": "pkg"},
            file_permissions={"a.go": "0o644"},
            claude_md="", architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "NOT in the PR" in result
        assert "Files not pre-collected" not in result
        assert "Read source files directly" not in result

    def test_env_section_partial_preflight_with_omitted_files(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=2,
            files=[
                {"path": "a.go", "additions": 5, "deletions": 2},
                {"path": "b.go", "additions": 5, "deletions": 3},
            ],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="diff", commit_log="log",
            file_contents={"a.go": "pkg"},
            file_permissions={"a.go": "0o644"},
            claude_md="", architecture_md="",
            omitted_files=["b.go"],
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "NOT in the PR" not in result
        assert "must be read directly" in result
        assert "Read source files directly" not in result

    def test_env_section_no_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "NOT in the PR" not in result
        assert "must be read directly" not in result
        assert "Read source files directly" in result


# ── self-review metadata ──────────────────────────────────────────────


def _pr_metadata(ro, **overrides):
    defaults = dict(
        title="feat: thing", body="why", head="feat", base="main",
        head_sha="a" * 40, additions=1, deletions=0, changed_files=1,
        files=[{"path": "pushed.go", "additions": 1, "deletions": 0}],
    )
    return ro.PRMetadata(**{**defaults, **overrides})


class TestWithLocalDiff:
    def test_diff_surface_comes_from_the_worktree(self, ro):
        pr = _pr_metadata(ro)
        local = _pr_metadata(
            ro, head_sha="b" * 40, additions=9, deletions=2, changed_files=2,
            files=[
                {"path": "pushed.go", "additions": 1, "deletions": 0},
                {"path": "unpushed.go", "additions": 8, "deletions": 2},
            ],
        )

        merged = ro._with_local_diff(pr, local)

        assert merged.head_sha == "b" * 40
        assert [f["path"] for f in merged.files] == ["pushed.go", "unpushed.go"]
        assert merged.additions == 9
        assert merged.deletions == 2
        assert merged.changed_files == 2

    def test_branch_name_comes_from_the_worktree(self, ro):
        pr = _pr_metadata(ro, head="feat")
        local = _pr_metadata(ro, head="renamed-locally")

        assert ro._with_local_diff(pr, local).head == "renamed-locally"

    def test_pr_narrative_is_preserved(self, ro):
        pr = _pr_metadata(ro, labels=["review"], author="isaac", is_draft=True)
        local = _pr_metadata(ro, title="add unpushed", body="", head_sha="b" * 40)

        merged = ro._with_local_diff(pr, local)

        assert merged.title == "feat: thing"
        assert merged.body == "why"
        assert merged.labels == ["review"]
        assert merged.author == "isaac"
        assert merged.is_draft is True

    def test_drift_is_reported(self, ro, capsys):
        pr = _pr_metadata(ro)
        local = _pr_metadata(ro, head_sha="b" * 40)

        ro._with_local_diff(pr, local)

        err = capsys.readouterr().err
        assert "b" * 7 in err
        assert "a" * 7 in err

    def test_matching_heads_are_silent(self, ro, capsys):
        pr = _pr_metadata(ro)

        ro._with_local_diff(pr, _pr_metadata(ro))

        assert capsys.readouterr().err == ""


class TestFetchMetadataSelfMode:
    def test_self_review_of_a_pr_sees_unpushed_commits(self, ro, tmp_path, monkeypatch):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "unpushed.go").write_text("package main\nfunc unpushed() {}\n")
        commit_all(repo, "add unpushed")

        monkeypatch.setattr(
            ro._rpl, "fetch_pr_metadata",
            lambda repo_name, pr_number: _pr_metadata(ro),
        )

        pr, ctx, pr_data = ro._fetch_metadata("o/r", "1", ro.Mode.SELF, str(repo))

        assert [f["path"] for f in pr.files] == ["unpushed.go"]
        assert pr.head_sha != "a" * 40
        assert pr.title == "feat: thing"
        assert pr_data is None


class TestStaticAnalysisIntegration:
    def test_static_analysis_injected_into_review(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")

        deep_script = tmp_path / "deep.sh"
        deep_script.write_text(
            "#!/bin/bash\n"
            "func() {\n"
            "  if true; then\n"
            "    for x in a; do\n"
            "      while true; do\n"
            "        echo deep\n"
            "      done\n"
            "    done\n"
            "  fi\n"
            "}\n"
        )

        changed_files = [{"path": "deep.sh", "additions": 10, "deletions": 0}]
        ro._inject_static_analysis_section(str(review_file), changed_files, str(tmp_path))

        result = review_file.read_text()
        assert "## Static Analysis" in result
        assert "Nesting depth" in result
        assert result.index("## Static Analysis") < result.index("## Verdict")

    def test_static_analysis_skipped_when_no_applicable_files(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        original = "## Summary\nLooks good.\n\n## Verdict\nApprove"
        review_file.write_text(original)

        changed_files = [{"path": "README.md", "additions": 5, "deletions": 0}]
        ro._inject_static_analysis_section(str(review_file), changed_files, str(tmp_path))

        assert review_file.read_text() == original

    def test_static_analysis_clean_files(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")

        clean_script = tmp_path / "clean.sh"
        clean_script.write_text("#!/bin/bash\necho hello\n")

        changed_files = [{"path": "clean.sh", "additions": 2, "deletions": 0}]
        ro._inject_static_analysis_section(str(review_file), changed_files, str(tmp_path))

        result = review_file.read_text()
        assert "## Static Analysis" in result
        assert "All checks passed" in result


class TestPipelineStateFailureRoundTrip:
    """`groups_failed` survives state.json in both the old and new format."""

    def _job(self, ro, tmp_path):
        job = MagicMock()
        job.review_file = str(tmp_path / "review.md")
        return job

    def _state(self, ro, groups_failed):
        from review_state import PipelineState
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
        from review_state import PipelineState
        from review_state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            done={Phase.SYNTHESIS},
        )
        assert build_failures_body(state) == ""

    def test_group_failures_produce_table(self):
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
        denial = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied")
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[], groups_failed={1: denial, 2: denial},
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" not in build_failures_body(state)

    def test_recover_hint_offered_for_max_turns(self):
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[], groups_failed={1: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5)},
            done={Phase.SYNTHESIS},
        )
        assert "pr review --recover" in build_failures_body(state)

    def test_synthesis_failure_alone_stays_recoverable(self):
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import build_failures_body
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        return PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1], groups_failed={2: Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)},
            done={Phase.SYNTHESIS},
            failed={Phase.SYNTHESIS: Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)},
        )

    @staticmethod
    def _clean_state():
        from review_state import PipelineState
        return PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            done={Phase.SYNTHESIS},
        )

    def test_mechanical_fallback_includes_failures(self):
        """When synthesis falls back, the review includes ## Agent Failures."""
        from review_state import set_failures_section
        result = set_failures_section("## Summary\n\nnote\n", self._state())
        assert "## Agent Failures" in result
        assert "group-2: api" in result
        assert "quota exhausted" in result
        assert "synthesis" in result
        assert "fallback" in result

    def test_the_section_sits_above_the_summary(self):
        from review_state import set_failures_section
        result = set_failures_section("## Summary\n\nnote\n\n## Verdict\n\nApprove\n", self._state())
        assert result.index("## Agent Failures") < result.index("## Summary")

    def test_a_review_with_no_summary_still_gets_the_section(self):
        """A run that never reached synthesis has no Summary to sit above, and
        the failures are the only account of why."""
        from review_state import set_failures_section
        result = set_failures_section("## Must fix\n\n- **[M1]** a.py:1 — bug\n", self._state())
        assert "## Agent Failures" in result
        assert result.index("## Must fix") < result.index("## Agent Failures")

    def test_a_rerun_that_failed_nothing_drops_the_section(self):
        from review_state import set_failures_section
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import _inject_failures_and_status

        # Write a review file that already has a 'completed' status line
        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- status: completed -->\n"
            "<!-- generator: claude-review v1 -->\n"
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import _inject_failures_and_status

        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- generator: claude-review v1 -->\n"
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
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_state import PipelineState
        from review_state import _inject_failures_and_status

        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- status: completed -->\n"
            "<!-- generator: claude-review v1 -->\n"
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


class TestCleanupScope:
    """What `_run_orchestrate` leaves in the review directory when it returns.

    Driven through the whole run rather than through the sweep, because the
    leak this covers was in the order the phases run and not in any one of
    them: the pipeline swept as it returned, and the fix pass wrote its log
    afterwards. Each phase is replaced by something that writes the files a
    real one leaves behind, which is all the sweep can see.
    """

    _REVIEW = synthetic_review(meta="status: completed", summary="All good.")

    @staticmethod
    def _args(ro, review_file, repo_dir, **overrides):
        from types import SimpleNamespace

        defaults = {
            "pr": "1", "review_file": str(review_file), "repo_dir": str(repo_dir),
            "mode": ro.Mode.PR, "effort": ro.Effort.MEDIUM,
            "prior_review": "", "issue": "", "issue_context": "",
            "generator_version": "", "model": "", "recover_sha": "",
            "target_dir": str(repo_dir), "max_parallel": 2,
            "max_cost": 20.0, "max_groups": None,
            "no_holistic": False, "no_scout": False, "no_group": False,
            "no_synthesis": False, "no_disprove": True, "disprove": None,
            "generated": False, "fix": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _run(self, ro, monkeypatch, tmp_path, pipeline=None, out=None, **arg_overrides):
        """Drive `_run_orchestrate` over *tmp_path* with every phase faked.

        Pass *out* to keep the result JSON the run prints; the default throws
        it away, since what most of these tests read is the directory.
        """
        import fix_engine  # the `ro` fixture has already put `ai/lib` on the path

        review_dir = tmp_path / "review-dir"
        review_dir.mkdir()
        review_file = review_dir / "review.md"

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc123",
            additions=10, deletions=0, changed_files=1,
            files=[{"path": "a.py", "additions": 10, "deletions": 0}],
        )

        def _pipeline(job, **_kwargs):
            Path(job.review_file).write_text(self._REVIEW)
            Path(job.session_log).write_text("{}\n")
            (review_dir / "meta.json").write_text("{}")
            (review_dir / "disprove.jsonl").write_text("{}\n")
            (review_dir / "prompt-single.md").write_text("PROMPT")

        def _fix(job, _trail=None, **_kwargs):
            artifacts = Path(job.artifact_dir)
            (artifacts / "fix.jsonl").write_text("{}\n")
            (artifacts / fix_engine.TRACKING_FILENAME).write_text("## <!-- fix:M1 -->\n")

        monkeypatch.setattr(ro.ai_backend, "preflight", lambda *a, **k: True)
        monkeypatch.setattr(ro.pr_state, "load_state", lambda *a, **k: None)
        monkeypatch.setattr(
            ro, "_fetch_metadata", lambda *a, **k: (pr, ro.PRContext(), None),
        )
        monkeypatch.setattr(ro, "collect_preflight_data", lambda job: MagicMock())
        monkeypatch.setattr(ro, "run_single_agent", pipeline or _pipeline)
        monkeypatch.setattr(ro, "run_static_analysis", lambda *a, **k: [])
        monkeypatch.setattr(ro, "run_fix_pass", _fix)

        args = self._args(ro, review_file, tmp_path, **arg_overrides)
        with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
            ro._run_orchestrate(MagicMock(), args, "org/repo", str(review_dir / "session.jsonl"))
        return review_dir

    def test_the_fix_passes_leavings_do_not_outlive_the_run(
        self, ro, monkeypatch, tmp_path,
    ):
        """The sweep runs after the fix pass, never before it.

        Its log and the checklist its agent answered on are both diagnostic,
        and the exact listing below is what says neither survived.
        """
        review_dir = self._run(ro, monkeypatch, tmp_path, fix=True)

        assert not (review_dir / "fix.jsonl").exists()
        assert sorted(p.name for p in review_dir.iterdir()) == [
            "meta.json", "review.md", "session.jsonl",
        ]

    def test_a_run_without_the_fix_pass_is_swept_too(self, ro, monkeypatch, tmp_path):
        review_dir = self._run(ro, monkeypatch, tmp_path)

        assert not (review_dir / "disprove.jsonl").exists()
        assert not (review_dir / "prompt-single.md").exists()
        assert (review_dir / "review.md").exists()

    def test_a_failed_run_keeps_its_artifacts(self, ro, monkeypatch, tmp_path):
        """A pipeline that exits non-zero leaves everything for diagnosis."""
        def _fails(job, **_kwargs):
            (Path(job.artifact_dir) / "disprove.jsonl").write_text("{}\n")
            (Path(job.artifact_dir) / "prompt-single.md").write_text("PROMPT")
            raise SystemExit(1)

        with pytest.raises(SystemExit) as exc:
            self._run(ro, monkeypatch, tmp_path, pipeline=_fails, fix=True)

        assert exc.value.code == 1
        review_dir = tmp_path / "review-dir"
        assert (review_dir / "disprove.jsonl").exists()
        assert (review_dir / "prompt-single.md").exists()

    def test_a_partial_run_keeps_its_artifacts(self, ro, monkeypatch, tmp_path):
        """Failed groups are not an exception, but they still block the sweep.

        `pr review --recover` resumes from pipeline.json and the outputs of the
        groups that did succeed, so a sweep here would strand the recovery.
        """
        import serde
        from agent_diagnosis import Diagnosis, DiagnosisKind
        from review_paths import FILENAME_PIPELINE_STATE
        from review_state import PipelineState

        def _partial(job, **_kwargs):
            review_dir = Path(job.artifact_dir)
            Path(job.review_file).write_text(self._REVIEW)
            (review_dir / "group-1.md").write_text("finding")
            state = PipelineState(
                head_sha="abc123", group_names=["a", "b"], groups_done=[1],
                groups_failed={2: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)},
                done={Phase.SYNTHESIS},
            )
            (review_dir / FILENAME_PIPELINE_STATE).write_text(
                json.dumps(serde.to_dict(state)),
            )

        review_dir = self._run(ro, monkeypatch, tmp_path, pipeline=_partial, fix=True)

        assert (review_dir / "group-1.md").exists()
        assert (review_dir / "pipeline.json").exists()
        assert (review_dir / "fix.jsonl").exists()

    def test_a_failed_sweep_still_reports_the_review(self, ro, monkeypatch, tmp_path):
        """The result JSON is printed after the sweep, so the sweep must not eat it.

        `unlink(missing_ok=True)` only suppresses FileNotFoundError; a
        read-only filesystem raises. claude-review reads the review it just
        paid for out of this JSON, so failing to delete a log cannot be what
        loses it.
        """
        import review_gc

        def _explode(review_dir):
            raise OSError(30, "Read-only file system", str(review_dir / "disprove.jsonl"))

        monkeypatch.setattr(review_gc, "cleanup_intermediates", _explode)

        out = io.StringIO()
        review_dir = self._run(ro, monkeypatch, tmp_path, out=out)

        assert json.loads(out.getvalue()) == {
            "review_file": str(review_dir / "review.md"),
            "session_log": str(review_dir / "session.jsonl"),
            "mode": ro.Pipeline.SINGLE,
        }
        assert (review_dir / "disprove.jsonl").exists()


class TestThePublishingGate:
    """`--post` is forwarded here because the gate is per-process.

    `claude-review` decides whether a run may publish, but the fix pass runs in
    this subprocess — spawned before that decision would otherwise be made — so
    a gate opened in the parent would never reach the push the pass makes.
    """

    def _gate_at_first_work(self, ro, monkeypatch, tmp_path, argv):
        """Whether the run could publish by the time it started doing anything."""
        import publishing

        seen = {}

        def stop(*args, **kwargs):
            seen["enabled"] = publishing.enabled()
            raise SystemExit(0)

        monkeypatch.setattr(ro, "detect_repo", stop)
        monkeypatch.setattr(sys, "argv", [
            "review-orchestrate", "--mode", "self",
            "--review-file", str(tmp_path / "review.md"),
            "--repo-dir", str(tmp_path), *argv,
        ])
        with pytest.raises(SystemExit):
            ro.main()
        return seen["enabled"]

    def test_a_fix_run_without_post_cannot_publish(self, ro, monkeypatch, tmp_path):
        assert self._gate_at_first_work(ro, monkeypatch, tmp_path, ["--fix"]) is False

    def test_post_opens_the_gate_before_anything_runs(self, ro, monkeypatch, tmp_path):
        assert self._gate_at_first_work(
            ro, monkeypatch, tmp_path, ["--fix", "--post"],
        ) is True
