"""Tests for review_agent failure diagnosis."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_agent
import review_pipeline


# Arbitrary — the diagnosis echoes whatever num_turns the result record carries,
# so the value only has to be distinguishable from the pipeline's turn defaults.
_TURNS = 16
_MAX_TURNS_REASON = f"agent hit max turns ({_TURNS})"


def _tool_use(name: str, **inp) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
    })


def _text(text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    })


def _result(subtype: str = "error_max_turns", num_turns: int = _TURNS) -> str:
    return json.dumps({
        "type": "result", "subtype": subtype, "num_turns": num_turns,
    })


def _write_log(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


class TestDiagnoseMissingOutput:
    def test_max_turns_without_write_tool_names_the_thrash(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/wt/a.py"),
            _tool_use("Bash", command="ls"),
            _result(),
        )
        reason = review_agent.diagnose_missing_output(log_path)
        assert _MAX_TURNS_REASON in reason
        assert review_agent.DIAG_NO_WRITE_TOOL_CALL in reason

    def test_max_turns_with_edit_call_stays_plain(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/out.md"),
            _tool_use("Edit", file_path="/tmp/out.md", old_string=""),
            _result(),
        )
        reason = review_agent.diagnose_missing_output(log_path)
        assert reason == _MAX_TURNS_REASON

    def test_no_assistant_records_stays_plain(self, tmp_path):
        """Non-Claude backends log no tool_use — absence is not evidence."""
        log_path = _write_log(tmp_path, _result())
        reason = review_agent.diagnose_missing_output(log_path)
        assert reason == _MAX_TURNS_REASON

    def test_crash_is_not_labelled_a_no_write_failure(self, tmp_path):
        """The error explains the missing output; a retry would reproduce it."""
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/a"),
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "spawn ENOENT",
            }),
        )
        reason = review_agent.diagnose_missing_output(log_path)
        assert review_agent.DIAG_NO_WRITE_TOOL_CALL not in reason
        assert not review_pipeline._is_retryable(reason)

    def test_clean_completion_without_a_write_is_labelled(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/a"),
            json.dumps({"type": "result", "subtype": "success"}),
        )
        reason = review_agent.diagnose_missing_output(log_path)
        assert review_agent.DIAG_NO_WRITE_TOOL_CALL in reason
        assert review_pipeline._is_retryable(reason)

    def test_refusal_without_any_tool_call_is_labelled(self, tmp_path):
        """A one-turn refusal calls no tool at all — the clearest no-write case.

        Regression: this used to fall through unlabelled, because an empty tool
        set was read as "cannot tell" rather than "called nothing", leaving the
        fix pass unable to retry an agent that simply declined the task.
        """
        log_path = _write_log(
            tmp_path,
            _text("I'm configured as a review-only assistant; I won't apply fixes."),
            json.dumps({"type": "result", "subtype": "success"}),
        )
        reason = review_agent.diagnose_missing_output(log_path)
        assert review_agent.DIAG_NO_WRITE_TOOL_CALL in reason
        assert review_pipeline._is_retryable(reason)

    def test_missing_log_unchanged(self, tmp_path):
        reason = review_agent.diagnose_missing_output(str(tmp_path / "nope.jsonl"))
        assert reason == review_agent.DIAG_NO_SESSION_LOG

    def test_no_result_record_unchanged(self, tmp_path):
        log_path = _write_log(tmp_path, _tool_use("Read", file_path="/tmp/a"))
        reason = review_agent.diagnose_missing_output(log_path)
        assert reason == review_agent.DIAG_NO_RESULT_RECORD


class TestSinglePassRead:
    def test_diagnosis_reads_the_log_once(self, tmp_path, monkeypatch):
        log_path = _write_log(
            tmp_path, _tool_use("Read", file_path="/tmp/a"), _result(),
        )
        reads = []
        real = review_agent._read_jsonl
        monkeypatch.setattr(
            review_agent, "_read_jsonl",
            lambda p: (reads.append(p), real(p))[1],
        )
        review_agent.diagnose_missing_output(log_path)
        assert reads == [log_path]


class TestMaxTurnsReasonMatching:
    """The reason string carries a turn count and may carry a suffix."""

    def _reason(self, tmp_path, *extra_lines):
        log_path = _write_log(tmp_path, *extra_lines, _result())
        return review_agent.diagnose_missing_output(log_path)

    def test_plain_max_turns_is_retryable(self, tmp_path):
        reason = self._reason(tmp_path)
        assert review_pipeline._MAX_TURNS_REASON in reason
        assert review_pipeline._is_retryable(reason)

    def test_suffixed_max_turns_is_retryable(self, tmp_path):
        reason = self._reason(tmp_path, _tool_use("Read", file_path="/tmp/a"))
        assert review_agent.DIAG_NO_WRITE_TOOL_CALL in reason
        assert review_pipeline._is_retryable(reason)
        assert review_pipeline._MAX_TURNS_REASON in reason


class TestWritableDirs:
    """The agent may write to its own artifact dir and the worktree — nothing else.

    Granting the shared reviews root is what let scratch files land beside other
    reviews instead of inside the run that made them.
    """

    def _add_dirs(self, monkeypatch, artifact_dir: str) -> list[str]:
        captured = {}
        monkeypatch.setattr(
            review_agent.ai_backend, "invoke_agent",
            lambda *a, **kw: captured.update(kw) or 0,
        )
        review_agent.invoke_agent(
            "prompt", "/tmp/session.jsonl", "/tmp/wt", artifact_dir,
        )
        return captured["add_dirs"]

    def test_grants_the_artifact_dir_and_the_worktree(self, monkeypatch):
        assert self._add_dirs(monkeypatch, "/tmp/reviews/repo-1") == [
            "/tmp/reviews/repo-1", "/tmp/wt",
        ]

    def test_does_not_grant_the_reviews_root(self, monkeypatch):
        add_dirs = self._add_dirs(monkeypatch, "/tmp/reviews/repo-1")
        assert "/tmp/reviews" not in add_dirs
