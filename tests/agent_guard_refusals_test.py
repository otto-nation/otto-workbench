"""Guard refusals: kept in the session log on any exit, and named in the gate's verdict.

`ai/pi/extensions-cli/review-guard.ts` writes one JSON line per tool call on
stderr, with `blocked` set when it refused the call. Those lines used to reach
the session log only on a non-zero exit, so a gate that exited cleanly after
every `pytest` it tried was refused reported "pytest unavailable" and left
nothing behind to say why. The global guards (`test-pipe-guard`,
`exit-status-guard`) write nothing to stderr at all; their refusals are read
from the `tool_execution_end` event pi writes for every blocked call.
"""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_events  # noqa: E402
import agent.invoke  # noqa: E402
import fix.verify  # noqa: E402
from core.phases import Phase  # noqa: E402
from fix.gate import Verdict  # noqa: E402
from fix.types import FixItem  # noqa: E402

REFUSED = json.dumps({"tool": "bash", "summary": "pytest", "blocked": "unscoped `pytest`"})
ALLOWED = json.dumps({"tool": "read", "summary": "a.py"})
PIPE_REFUSAL = "Piping `pytest` into a filter reports the filter's exit status, not the suite's"


def _proc(returncode: int, stderr: str):
    return SimpleNamespace(returncode=returncode, stderr=io.StringIO(stderr))


def _tool_end(text: str, *, duration: int | None = None, is_error: bool = True) -> str:
    """A pi `tool_execution_end` line as the RPC stream writes it into the session log.

    No `durationMs` is the shape of a call pi refused before running it —
    `prepareToolCall` returns those as an immediate result, and only an
    executed call is timed.
    """
    event = {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "bash",
             "result": {"content": [{"type": "text", "text": text}], "details": {}},
             "isError": is_error}
    if duration is not None:
        event["durationMs"] = duration
    return json.dumps(event)


def test_a_clean_exit_keeps_only_the_refusals(tmp_path):
    log = tmp_path / "s.jsonl"
    log.write_text("")
    agent.backend_events._log_stderr(_proc(0, f"{ALLOWED}\n{REFUSED}\n"), str(log))
    text = log.read_text()
    assert agent.backend_events.GUARD_REFUSALS_HEADER in text
    assert REFUSED in text and ALLOWED not in text


def test_a_failed_exit_still_keeps_all_of_stderr(tmp_path):
    log = tmp_path / "s.jsonl"
    log.write_text("")
    agent.backend_events._log_stderr(_proc(2, f"{ALLOWED}\nboom\n"), str(log))
    assert "--- stderr (exit 2) ---" in log.read_text() and "boom" in log.read_text()


def test_a_clean_exit_with_no_refusals_writes_nothing(tmp_path):
    log = tmp_path / "s.jsonl"
    log.write_text("")
    agent.backend_events._log_stderr(_proc(0, f"{ALLOWED}\n"), str(log))
    assert log.read_text() == ""


def test_guard_refusals_reads_the_reasons_back(tmp_path):
    log = tmp_path / "s.jsonl"
    log.write_text(f'{{"type":"result"}}\n{agent.backend_events.GUARD_REFUSALS_HEADER}\n{REFUSED}\n')
    assert agent.backend_events.guard_refusals(log) == ("unscoped `pytest`",)


def test_a_refusal_with_no_stderr_is_read_from_the_tool_event(tmp_path):
    """A global guard's block: no stderr, only pi's own event. Executed errors are not refusals."""
    log = tmp_path / "s.jsonl"
    log.write_text("\n".join([
        _tool_end(PIPE_REFUSAL),
        _tool_end("exit status 1", duration=40),
        _tool_end("Operation aborted"),
        _tool_end("Tool nope not found"),
        _tool_end("fine", is_error=False),
    ]) + "\n")
    assert agent.backend_events.guard_refusals(log) == (PIPE_REFUSAL,)


def test_a_review_guard_refusal_is_counted_once(tmp_path):
    """review-guard's block shows up as both an event and a stderr line; the events win."""
    log = tmp_path / "s.jsonl"
    log.write_text(f"{_tool_end('read a.py', duration=3, is_error=False)}\n"
                   f"{_tool_end('unscoped `pytest`')}\n"
                   f"{agent.backend_events.GUARD_REFUSALS_HEADER}\n{REFUSED}\n")
    assert agent.backend_events.guard_refusals(log) == ("unscoped `pytest`",)


def test_a_validation_error_is_not_a_refusal(tmp_path):
    """Arguments failing schema validation come back untimed too, under pi-ai's fixed prefix."""
    log = tmp_path / "s.jsonl"
    log.write_text("\n".join([
        _tool_end("ran", duration=12, is_error=False),
        _tool_end('Validation failed for tool "bash":\n  - command: must be string\n\n'
                  'Received arguments:\n{}'),
        _tool_end(PIPE_REFUSAL),
    ]) + "\n")
    assert agent.backend_events.guard_refusals(log) == (PIPE_REFUSAL,)


def test_a_call_cut_off_by_the_token_limit_is_not_a_refusal(tmp_path):
    """pi fails every call in a truncated response, untimed, with its own fixed text."""
    log = tmp_path / "s.jsonl"
    log.write_text("\n".join([
        _tool_end("ran", duration=12, is_error=False),
        _tool_end('Tool call "bash" was not executed: the response hit the output token '
                  'limit, so its arguments may be truncated. Re-issue the tool call with '
                  'complete arguments.'),
    ]) + "\n")
    assert agent.backend_events.guard_refusals(log) == ()


def test_a_log_with_no_timed_call_falls_back_to_stderr(tmp_path):
    """A pi that times no call cannot tell a refusal from a failed command by `durationMs`.

    Without one timed `tool_execution_end` in the log, the missing key proves
    nothing, so only review-guard's own stderr lines are read: the failing
    command below is not counted.
    """
    log = tmp_path / "s.jsonl"
    log.write_text(f"{_tool_end('exit status 1')}\n{_tool_end(PIPE_REFUSAL)}\n"
                   f"{agent.backend_events.GUARD_REFUSALS_HEADER}\n{REFUSED}\n")
    assert agent.backend_events.guard_refusals(log) == ("unscoped `pytest`",)
    log.write_text(f"{_tool_end('exit status 1')}\n")
    assert agent.backend_events.guard_refusals(log) == ()


def test_a_long_reason_is_shortened_in_the_verdict():
    out = fix.verify._with_refusals({"N1": Verdict(ok=None, detail="")}, ("word " * 100,))
    assert out["N1"].detail.endswith("[...])")
    assert len(out["N1"].detail) < fix.verify.REFUSAL_SHOWN + 80


def test_unverified_verdicts_carry_the_refusal_and_verified_ones_do_not():
    verdicts = {"N1": Verdict(ok=None, detail="pytest unavailable"), "N2": Verdict(ok=True, detail="ran")}
    out = fix.verify._with_refusals(verdicts, ("unscoped `pytest`", "unscoped `pytest`"))
    assert out["N1"].detail == (
        "pytest unavailable; the gate's tool calls were refused 2× "
        "(first: unscoped `pytest`)")
    assert out["N2"] == verdicts["N2"]


class _Adapter:
    def __init__(self, wt_path):
        self.workdir = Path(wt_path)
        self.artifacts = self.workdir / "artifacts"
        self.artifacts.mkdir()
        self.branch, self.repo, self.pr = "b", "o/r", ""
        self.config, self.effort, self.model = None, None, ""

    def add_dirs(self):
        return [self.workdir]

    def verify_tracking_path(self, chunk):
        return self.artifacts / f"verify-tracking-{chunk}.md"

    def verify_session_log(self, chunk):
        return self.artifacts / f"verify-session-{chunk}.jsonl"


def test_an_unverified_verdict_names_the_refusals(tmp_path):
    """End to end through the gate runner: the chunk's own log is what is read."""
    adapter = _Adapter(tmp_path)

    def run_fix(_phase, _prompt, **kwargs):
        tracking = adapter.verify_tracking_path(1)
        tracking.write_text(tracking.read_text().replace(
            "- [ ] not verified — <why>", "- [x] not verified — pytest unavailable"))
        Path(kwargs["session_log"]).write_text(
            f"{agent.backend_events.GUARD_REFUSALS_HEADER}\n{REFUSED}\n")
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        verdicts = fix.verify.run(Phase.FIX_VERIFY, "", adapter=adapter,
                                  items=[FixItem(id="N1", file="a.py", line=1, label="x")])
    assert "refused 1× (first: unscoped `pytest`)" in verdicts["N1"].detail
