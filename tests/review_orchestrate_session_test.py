"""Session-log diagnosis in `agent.session`, reached through the `ro` re-exports.

Recovering output, diagnosing a missing one, model, quota and transient errors,
and the cost a session reports.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401


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

    def test_the_last_qualifying_denial_wins(self, ro, tmp_path):
        """An agent refused once writes again, and the document grows.

        Pinned because the rule changed with the Pi reader: recovery used to
        take the first qualifying denial, which on a run that was refused
        mid-draft recovers the draft and discards the finished review.
        """
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [
                {"tool_input": {"content": "## Must fix\n- draft\n"}},
                {"tool_input": {"content": "## Must fix\n- final\n"}},
            ],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is True
        assert "final" in output.read_text()
        assert "draft" not in output.read_text()

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

    def test_an_empty_path_is_a_missing_log_not_a_directory_read(self, ro):
        """`Path("")` is `Path(".")`, which exists — and is not a log.

        A caller that has no session log to name passes the empty string, and
        an existence check answers True for the working directory. Reading it
        raises rather than reporting the log as missing.
        """
        result = ro.diagnose_missing_output("")
        assert result.kind is ro.DiagnosisKind.NO_SESSION_LOG

    def test_a_directory_is_a_missing_log(self, ro, tmp_path):
        result = ro.diagnose_missing_output(str(tmp_path))
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
