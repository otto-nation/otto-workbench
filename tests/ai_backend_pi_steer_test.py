"""Tests for agent.backend_pi run limits: _check_limits, the stall watch and the steers."""

import io
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi

from ai_backend_pi_support import _event


# ai_backend_pi_rpc_test.py subclasses this (and TestConsumeStreamTracksWrites
# below) under __test__ = False to reach MockStdin/MockProc without
# recollecting these cases there. A test method added directly to either class
# runs here but not through that subclass, so it stays silent about the split.
class TestCheckLimits:
    class MockStdin:
        def __init__(self):
            self.commands = []
        def write(self, data):
            self.commands.append(json.loads(data.strip()))
        def flush(self):
            pass

    class MockProc:
        def __init__(self, stdin_cls):
            self.stdin = stdin_cls()

    def _make_proc(self):
        """Create a mock process with stdin that records writes."""
        return self.MockProc(self.MockStdin)

    def test_no_action_within_limits(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 5, 2.0, 10, 5.0)
        assert stop is None
        assert len(proc.stdin.commands) == 0

    def test_abort_at_max_turns(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 10, 2.0, 10, 5.0)
        assert stop == "max_turns"
        assert any(c["type"] == "abort" for c in proc.stdin.commands)

    def test_abort_over_budget(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 5, 5.1, 10, 5.0)
        assert stop == "max_budget"
        assert any(c["type"] == "abort" for c in proc.stdin.commands)

    def test_steer_at_80_pct_budget(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 5, 4.1, 10, 5.0)
        assert stop is None
        assert any(c["type"] == "steer" for c in proc.stdin.commands)

    def test_steer_at_exact_80_pct_budget_boundary(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 5, 4.0, 10, 5.0)
        assert stop is None
        assert steered is True
        assert any(c["type"] == "steer" for c in proc.stdin.commands)

    def test_steer_at_80_pct_turns(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 8, 2.0, 10, 5.0)
        assert stop is None
        assert any(c["type"] == "steer" for c in proc.stdin.commands)

    def test_no_steer_when_no_limits(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 100, 100.0, None, None)
        assert stop is None
        assert len(proc.stdin.commands) == 0

    def test_follow_up_on_abort(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 10, 2.0, 10, 5.0)
        assert stop == "max_turns"
        assert any(c["type"] == "follow_up" for c in proc.stdin.commands)

    def test_no_duplicate_steer_when_steered_true(self):
        proc = self._make_proc()
        # First call triggers steer
        stop, steered = agent.backend_pi._check_limits(proc, 8, 2.0, 10, 5.0, steered=False)
        assert stop is None
        assert steered is True
        first_count = len(proc.stdin.commands)
        # Second call with steered=True should not send another steer
        stop, steered = agent.backend_pi._check_limits(proc, 9, 2.0, 10, 5.0, steered=True)
        assert stop is None
        assert len(proc.stdin.commands) == first_count

    def test_steered_flag_returned_true_after_steer(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 8, 2.0, 10, 5.0, steered=False)
        assert steered is True

    def test_steered_flag_unchanged_when_within_limits(self):
        proc = self._make_proc()
        stop, steered = agent.backend_pi._check_limits(proc, 5, 2.0, 10, 5.0, steered=False)
        assert steered is False


class TestStallWatchWiring:
    # Uses TestConsumeStreamTracksWrites.MockProc, defined further down this
    # file; Python resolves the attribute at call time, so the forward
    # reference works, it just reads out of order top to bottom.
    """The stream loop arms the stall watch, and a stall ends the run.

    The watch itself is covered by `agent_stall_test.py`; what is checked here
    is the wiring, which is where it can be switched off without anything
    looking broken.
    """

    class _Watch:
        """Records what the stream loop asked of the watch."""

        def __init__(self, abort_after=None):
            self.events = []
            self.aborted_reason = ""
            self._abort_after = abort_after

        def stamp(self):
            self.events.append("stamp")
            if self._abort_after is not None and \
                    self.events.count("stamp") > self._abort_after:
                self.aborted_reason = "stalled: nothing was happening"

        def arm(self, tool):
            self.events.append(f"arm:{tool}")

        def disarm(self):
            self.events.append("disarm")

    def _run(self, lines, watch):
        proc = TestConsumeStreamTracksWrites.MockProc(
            [json.dumps(line) + "\n" for line in lines],
        )
        return proc, agent.backend_pi._consume_events(proc, io.StringIO(), "", watch)

    def test_a_tool_call_arms_the_watch_and_its_turn_disarms_it(self):
        watch = self._Watch()
        self._run([
            {"type": "tool_execution_start",
             "toolName": "bash", "args": {"command": "sleep 1"}},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ], watch)
        armed = [e for e in watch.events if e.startswith("arm:")]
        assert len(armed) == 1
        # Ordering, not mere presence: a disarm that ran before the arm would
        # leave the watch live across the model round trip, which is the
        # false-positive this gating exists to prevent.
        assert watch.events.index(armed[0]) < watch.events.index("disarm")

    def test_the_watch_is_not_armed_by_an_ordinary_event(self):
        watch = self._Watch()
        self._run([{"type": "turn_end"}, {"type": "agent_end"}], watch)
        assert not [e for e in watch.events if e.startswith("arm:")]

    def test_every_line_counts_as_liveness(self):
        watch = self._Watch()
        self._run([
            {"type": "message_update"},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ], watch)
        assert watch.events.count("stamp") == 3

    def test_a_stalled_run_reports_itself_as_stalled(self):
        watch = self._Watch(abort_after=1)
        _, stream = self._run([
            {"type": "tool_execution_start",
             "toolName": "bash", "args": {"command": "sleep 1"}},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ], watch)
        assert stream.stop_reason == "stalled"

    def test_the_follow_up_turn_after_a_stall_is_not_counted_as_work(self):
        # The watch sends abort and follow_up itself, so the turn_end that
        # follows is the summary being written, not another turn of work.
        watch = self._Watch(abort_after=1)
        _, stream = self._run([
            {"type": "tool_execution_start",
             "toolName": "bash", "args": {"command": "sleep 1"}},
            {"type": "turn_end"},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ], watch)
        assert stream.turn_count == 0

    def test_a_healthy_run_is_not_reported_as_stalled(self):
        watch = self._Watch()
        _, stream = self._run([
            {"type": "tool_execution_start",
             "toolName": "bash", "args": {"command": "echo hi"}},
            {"type": "turn_end"},
            {"type": "agent_end"},
        ], watch)
        assert stream.stop_reason == "completed"

    def test_a_process_with_no_pid_leaves_the_watch_inert(self):
        # Every mock proc in this suite lacks a pid, so an inert watch is what
        # keeps them running. That makes this the load-bearing case: if the
        # inert path were reached in production the detector would be off
        # everywhere and nothing else here would notice.
        watch = agent.backend_pi.StallWatch(root_pid=0, send=lambda _c: True)
        watch.arm("bash")
        watch.start()
        try:
            time.sleep(0.05)
        finally:
            watch.stop()
        assert watch.aborted_reason == ""

    def test_a_real_process_gets_a_watch_bound_to_it(self, monkeypatch):
        # The counterpart to the case above, and the more important half: the
        # inert path must be reachable only by a proc with no pid. Hard-wiring
        # the root to 0 would switch the detector off everywhere while every
        # other test in this file — all of which use a pidless mock — kept
        # passing, so this is what stands between that and a silent no-op.
        #
        # Built through `_consume_stream` rather than by hand for the same
        # reason: the construction is the subject, so a test that constructs
        # its own watch is testing the class, not the wiring.
        seen = {}
        real = agent.backend_pi.StallWatch

        def capture(**kwargs):
            seen.update(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(agent.backend_pi, "StallWatch", capture)
        proc = subprocess.Popen(["sleep", "5"])
        proc.stdout = iter([json.dumps({"type": "agent_end"}) + "\n"])
        try:
            agent.backend_pi._consume_stream(proc, io.StringIO(), "")
        finally:
            proc.kill()
            proc.wait()
        assert seen["root_pid"] == proc.pid


class TestLimitStopTurnCount:
    """The follow_up after abort is a summary, not another counted turn."""

    def test_a_limit_stopped_agent_records_the_cap_not_the_follow_up(self):
        proc = TestConsumeStreamTracksWrites.MockProc([
            _event("turn_end"),
            _event("turn_end"),
            _event("turn_end"),
            _event("agent_end"),
        ])
        stream = agent.backend_pi._consume_stream(
            proc, io.StringIO(), "", max_turns=2,
        )
        assert stream.stop_reason == "max_turns"
        assert stream.turn_count == 2
        assert any(c["type"] == "follow_up" for c in proc.stdin.commands)


class TestWriteAwareSteer:
    """The 80% steer names the write mechanism when nothing has been written."""

    def _steer_text(self, *args):
        """The message of the single steer command sent by _check_limits."""
        proc = TestCheckLimits.MockProc(TestCheckLimits.MockStdin)
        agent.backend_pi._check_limits(proc, *args)
        steers = [c for c in proc.stdin.commands if c["type"] == "steer"]
        assert len(steers) == 1
        return steers[0]["message"]

    def test_unwritten_agent_is_told_how_to_write(self):
        text = self._steer_text(8, 2.0, 10, 5.0, False, False)
        assert agent.backend_pi._WRITE_FIRST in text
        assert agent.backend_pi._WRAP_UP not in text

    def test_written_agent_is_told_to_wrap_up(self):
        text = self._steer_text(8, 2.0, 10, 5.0, False, True)
        assert agent.backend_pi._WRAP_UP in text
        assert agent.backend_pi._WRITE_FIRST not in text

    def test_warning_context_is_kept_in_both_messages(self):
        assert "8/10 turns" in self._steer_text(8, 2.0, 10, 5.0, False, False)
        assert "8/10 turns" in self._steer_text(8, 2.0, 10, 5.0, False, True)

    def test_budget_steer_is_also_write_aware(self):
        text = self._steer_text(5, 4.1, 10, 5.0, False, False)
        assert agent.backend_pi._WRITE_FIRST in text
        assert "4.10/5.00 USD" in text

    def test_default_assumes_nothing_was_written(self):
        """Callers that cannot observe tool calls get the safe message."""
        assert agent.backend_pi._WRITE_FIRST in self._steer_text(8, 2.0, 10, 5.0)

    def test_the_steer_names_a_tool_pi_actually_has(self):
        """Regression: this steer used to prescribe Claude's Edit recipe.

        `old_string` is not a parameter Pi's edit tool accepts, and an empty
        `oldText` is rejected outright — so the steer spent an agent's last
        turns on a call that could not succeed. It must name `write`, which is
        in the tool list this module passes.
        """
        assert "old_string" not in agent.backend_pi._WRITE_FIRST
        assert "`write`" in agent.backend_pi._WRITE_FIRST
        assert "write" in agent.backend_pi.PI_TOOLS.split(",")


# See the pointer comment above TestCheckLimits: ai_backend_pi_rpc_test.py also
# subclasses this one (under __test__ = False) to reach MockProc.
class TestConsumeStreamTracksWrites:
    """_consume_stream is what tells _check_limits whether a write happened."""

    class MockProc:
        def __init__(self, lines):
            self.stdout = iter(lines)
            self.stdin = TestCheckLimits.MockStdin()

    def _steer_message(self, tool_name):
        """The 80% turn warning's message for a run whose only tool was `tool_name`.

        Selected by its warning text rather than by being the only steer: a
        read-only run also earns the 25% write-first steer, which carries
        `_WRITE_FIRST` without the turn count and would otherwise let this
        pass while the warning itself said "wrap up".
        """
        lines = [json.dumps({
            "type": "message_update",
            "content": [{"type": "toolCall", "name": tool_name, "arguments": {}}],
        })]
        lines += [json.dumps({"type": "turn_end"})] * 8
        lines.append(json.dumps({"type": "agent_end"}))
        proc = self.MockProc([l + "\n" for l in lines])
        agent.backend_pi._consume_stream(proc, io.StringIO(), "", max_turns=10)
        warnings = [
            c["message"] for c in proc.stdin.commands
            if c["type"] == "steer" and "Turn warning: 8/10" in c["message"]
        ]
        assert len(warnings) == 1
        return warnings[0]

    def test_edit_call_earns_the_wrap_up_message(self):
        assert agent.backend_pi._WRAP_UP in self._steer_message("edit")

    def test_read_only_run_earns_the_write_first_message(self):
        assert agent.backend_pi._WRITE_FIRST in self._steer_message("read")


class TestNoProgressSteer:
    """The read-only loop the turn and budget caps do not catch in time.

    Both limits are satisfied by an agent re-reading the same region until it
    runs out, which is how a run reached its cap having written nothing.
    """

    class MockProc:
        def __init__(self, lines):
            self.stdout = iter(lines)
            self.stdin = TestCheckLimits.MockStdin()

    def _read(self, path):
        return json.dumps({
            "type": "tool_execution_start",
            "toolName": "read",
            "args": {"path": path},
        }) + "\n"

    def _write(self, path):
        return json.dumps({
            "type": "tool_execution_start",
            "toolName": "write",
            "args": {"path": path},
        }) + "\n"

    def _run(self, lines, output_path=""):
        proc = self.MockProc([*lines, json.dumps({"type": "agent_end"}) + "\n"])
        agent.backend_pi._consume_stream(
            proc, io.StringIO(), "", output_path=output_path,
        )
        return [c for c in proc.stdin.commands if c["type"] == "steer"]

    def test_repeating_one_read_earns_a_steer(self):
        steers = self._run([self._read("/a.py")] * agent.backend_pi.REPEAT_TOOL_LIMIT)
        assert len(steers) == 1
        assert "write" in steers[0]["message"]

    def test_below_the_limit_is_left_alone(self):
        steers = self._run([self._read("/a.py")] * (agent.backend_pi.REPEAT_TOOL_LIMIT - 1))
        assert steers == []

    def test_reading_different_files_is_progress(self):
        steers = self._run([self._read(f"/f{i}.py") for i in range(6)])
        assert steers == []

    def test_a_write_clears_the_count(self):
        # An agent that wrote is working, so what it repeated before does not
        # count against it.
        lines = [self._read("/a.py")] * (agent.backend_pi.REPEAT_TOOL_LIMIT - 1)
        lines += [self._write("/out.md")]
        lines += [self._read("/a.py")] * (agent.backend_pi.REPEAT_TOOL_LIMIT - 1)
        assert self._run(lines) == []

    def test_the_steer_fires_once(self):
        steers = self._run([self._read("/a.py")] * (agent.backend_pi.REPEAT_TOOL_LIMIT * 3))
        assert len(steers) == 1

    def test_a_scratch_write_does_not_pass_for_the_output(self):
        """A probe under /tmp is not the deliverable.

        An agent that writes a scratch script to check something has produced
        nothing the run was asked for. Counting it as output switched off the
        repeat steer for the rest of the run, which is how three reviews in
        one sweep spent their whole budget probing and wrote no findings.
        """
        lines = [self._write("/tmp/probe.py")]
        lines += [self._read("/a.py")] * agent.backend_pi.REPEAT_TOOL_LIMIT
        steers = self._run(lines, output_path="/out/review.md")
        assert len(steers) == 1
        assert "write" in steers[0]["message"]

    def test_writing_the_output_file_still_clears_the_count(self):
        lines = [self._write("/out/review.md")]
        lines += [self._read("/a.py")] * agent.backend_pi.REPEAT_TOOL_LIMIT
        assert self._run(lines, output_path="/out/review.md") == []

    def test_the_turn_warning_still_asks_for_the_output_after_a_scratch_write(
        self,
    ):
        """The 80% warning must not downgrade to "wrap up" on a probe."""
        lines = [self._write("/tmp/probe.py")]
        lines += [json.dumps({"type": "turn_end"}) + "\n"] * 8
        proc = self.MockProc([*lines, json.dumps({"type": "agent_end"}) + "\n"])
        agent.backend_pi._consume_stream(
            proc, io.StringIO(), "", max_turns=10,
            output_path="/out/review.md",
        )
        steers = [c for c in proc.stdin.commands if c["type"] == "steer"]
        assert any(agent.backend_pi._WRITE_FIRST in s["message"] for s in steers)

    def test_the_no_progress_steer_is_independent_of_the_turn_warning(self):
        # Different conditions, so a run that loops early and then nears its
        # turn cap earns both. Suppressing one behind the other would hide
        # whichever fired second. The unwritten-by-25% steer is a third such
        # condition and fires here too.
        lines = [self._read("/a.py")] * agent.backend_pi.REPEAT_TOOL_LIMIT
        lines += [json.dumps({"type": "turn_end"}) + "\n"] * 8
        proc = self.MockProc([*lines, json.dumps({"type": "agent_end"}) + "\n"])
        agent.backend_pi._consume_stream(proc, io.StringIO(), "", max_turns=10)
        steers = [c["message"] for c in proc.stdin.commands if c["type"] == "steer"]
        assert len(steers) == 3
        assert sum("same tool call" in s for s in steers) == 1
        assert sum("Turn warning: 8/10" in s for s in steers) == 1
        assert sum(s == agent.backend_pi._WRITE_FIRST for s in steers) == 1

    def test_streaming_updates_do_not_count_as_repeats(self):
        # message_update repeats the same call many times over; counting those
        # would read a single read as a loop.
        line = json.dumps({
            "type": "message_update",
            "content": [{"type": "toolCall", "name": "read", "arguments": {}}],
        }) + "\n"
        assert self._run([line] * 10) == []


class TestWriteFirstSteer:
    """A one-shot _WRITE_FIRST when nothing has been written by 25% of max_turns.

    The 80% steer arrives when the budget is nearly spent, which is too late
    to be a course correction: an agent that has written nothing by then has
    already spent the turns it needed to investigate. This one fires early
    enough that the file exists before a run can die with it empty.
    """

    def _steers(self, lines, *, max_turns=15):
        proc = TestConsumeStreamTracksWrites.MockProc(
            [line + "\n" for line in lines]
        )
        agent.backend_pi._consume_stream(
            proc, io.StringIO(), "",
            max_turns=max_turns, output_path="/out/review.md",
        )
        return [c for c in proc.stdin.commands if c["type"] == "steer"]

    def test_threshold_at_group_medium_is_turn_four(self):
        assert agent.backend_pi._write_first_turn(15) == 4

    def test_threshold_floors_at_three(self):
        assert agent.backend_pi._write_first_turn(4) == 3
        assert agent.backend_pi._write_first_turn(8) == 3

    def test_an_unwritten_run_is_steered_at_25_percent(self):
        lines = [json.dumps({"type": "turn_end"})] * 4
        lines.append(json.dumps({"type": "agent_end"}))
        steers = self._steers(lines)
        assert len(steers) == 1
        assert agent.backend_pi._WRITE_FIRST in steers[0]["message"]

    def test_a_written_run_is_not_steered_at_25_percent(self):
        lines = [
            json.dumps({
                "type": "tool_execution_start",
                "toolName": "write",
                "args": {"path": "/out/review.md"},
            }),
        ]
        lines += [json.dumps({"type": "turn_end"})] * 4
        lines.append(json.dumps({"type": "agent_end"}))
        assert self._steers(lines) == []

    def test_a_scratch_write_does_not_suppress_the_steer(self):
        """The deliverable, not any write — the same split session.py makes."""
        lines = [
            json.dumps({
                "type": "tool_execution_start",
                "toolName": "write",
                "args": {"path": "/tmp/probe.py"},
            }),
        ]
        lines += [json.dumps({"type": "turn_end"})] * 4
        lines.append(json.dumps({"type": "agent_end"}))
        steers = self._steers(lines)
        assert len(steers) == 1
        assert agent.backend_pi._WRITE_FIRST in steers[0]["message"]

    def test_the_25_percent_steer_is_one_shot(self):
        lines = [json.dumps({"type": "turn_end"})] * 6
        lines.append(json.dumps({"type": "agent_end"}))
        # turn 4 fires once; turns 5-6 do not; turn 12 is not reached
        assert len(self._steers(lines)) == 1

    def test_80_percent_steer_still_fires_after_the_25_percent_steer(self):
        lines = [json.dumps({"type": "turn_end"})] * 12
        lines.append(json.dumps({"type": "agent_end"}))
        steers = self._steers(lines)
        assert len(steers) == 2
        assert all(agent.backend_pi._WRITE_FIRST in s["message"] for s in steers)
        assert "Turn warning: 12/15" in steers[1]["message"]

    def test_the_two_steers_never_share_a_turn(self):
        """At max_turns=4 both thresholds are turn 3; the 80% message wins.

        The early turn is max(3, ceil(max_turns * 0.25)) and the warning is
        int(max_turns * 0.8), so the floor of 3 collides with the 80%
        threshold for any max_turns of 4 or fewer and for no larger value.
        Without the upper bound the 25% steer would fire on the same turn as
        the turn warning, sending the same _WRITE_FIRST text twice.
        """
        lines = [json.dumps({"type": "turn_end"})] * 3
        lines.append(json.dumps({"type": "agent_end"}))
        steers = self._steers(lines, max_turns=4)
        assert len(steers) == 1
        assert "Turn warning: 3/4" in steers[0]["message"]


class TestAskForDeliverableOnAgentEnd:
    """A run that ends having written nothing is asked once more for the file.

    The last point at which findings the agent holds can still be saved. The
    ask is a `prompt` after `agent_settled`, not a `steer` on `agent_end`: Pi
    drains the steer and follow-up queues before emitting `agent_end`, so a
    steer sent afterwards is accepted, queued, and never run — and a reader
    waiting behind it for another `agent_end` waits forever.
    """

    OUT = "/out/review.md"

    def _run(self, lines, *, output_path=OUT, max_turns=None):
        proc = TestConsumeStreamTracksWrites.MockProc([l + "\n" for l in lines])
        result = agent.backend_pi._consume_stream(
            proc, io.StringIO(), "", max_turns=max_turns, output_path=output_path,
        )
        return proc, result

    @staticmethod
    def _prompts(proc):
        return [c for c in proc.stdin.commands if c["type"] == "prompt"]

    def test_an_unwritten_run_is_asked_once_for_its_deliverable(self):
        proc, result = self._run([
            json.dumps({"type": "turn_end"}),
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
            json.dumps({"type": "agent_end"}),
        ])
        prompts = self._prompts(proc)
        assert len(prompts) == 1
        assert prompts[0]["message"] == agent.backend_pi._WRITE_FIRST
        assert result.stop_reason == "completed"

    def test_the_ask_is_a_prompt_not_a_steer(self):
        """A steer after agent_end is queued and never run — that is the hang."""
        proc, _ = self._run([
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
            json.dumps({"type": "agent_end"}),
        ])
        after_end = [
            c for c in proc.stdin.commands
            if c["type"] in ("steer", "follow_up")
            and c.get("message") == agent.backend_pi._WRITE_FIRST
        ]
        assert after_end == []

    def test_the_second_agent_end_ends_the_run(self):
        proc, result = self._run([
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
            json.dumps({"type": "agent_end"}),
        ])
        assert len(self._prompts(proc)) == 1
        assert result.stop_reason == "completed"

    def test_the_loop_cannot_spin_on_a_stream_that_never_stops(self):
        """The termination proof. A finite list ends even with no break at all.

        This feeds an endless alternation of the two events the ask reacts to,
        so a loop that re-armed would never return. The generator fails the
        test rather than hanging it.
        """
        def endless():
            for n in range(12):
                yield json.dumps({"type": "agent_end"}) + "\n"
                yield json.dumps({"type": "agent_settled"}) + "\n"
            raise AssertionError("the stream loop did not terminate")

        proc = TestConsumeStreamTracksWrites.MockProc(endless())
        result = agent.backend_pi._consume_stream(
            proc, io.StringIO(), "", output_path=self.OUT,
        )
        assert len(self._prompts(proc)) == 1
        assert result.stop_reason == "completed"

    def test_a_run_that_wrote_its_deliverable_is_not_asked(self):
        proc, _ = self._run([
            json.dumps({
                "type": "tool_execution_start",
                "toolName": "write", "args": {"path": self.OUT},
            }),
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
        ])
        assert self._prompts(proc) == []

    def test_a_scratch_write_does_not_count_as_the_deliverable(self):
        proc, _ = self._run([
            json.dumps({
                "type": "tool_execution_start",
                "toolName": "write", "args": {"path": "/tmp/probe.py"},
            }),
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
            json.dumps({"type": "agent_end"}),
        ])
        assert len(self._prompts(proc)) == 1

    def test_a_caller_with_no_declared_deliverable_is_not_asked(self):
        proc, _ = self._run([
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
        ], output_path="")
        assert self._prompts(proc) == []

    def test_an_aborted_run_is_not_asked(self):
        """The cap path already spent its one round trip on the summary."""
        lines = [json.dumps({"type": "turn_end"})] * 4
        lines += [
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
        ]
        proc, _ = self._run(lines, max_turns=4)
        assert self._prompts(proc) == []
        assert [c["type"] for c in proc.stdin.commands if c["type"] == "abort"]

    def test_no_agent_settled_means_no_ask_and_no_hang(self):
        """Degrades to the old behaviour on a Pi that does not emit it."""
        proc, result = self._run([
            json.dumps({"type": "agent_end"}),
        ])
        assert self._prompts(proc) == []
        assert result.stop_reason == "completed"

    def test_a_dead_child_ends_the_run_without_an_error(self):
        class DeadStdin:
            commands: list = []
            def write(self, data):
                raise BrokenPipeError("gone")
            def flush(self):
                pass

        lines = [
            json.dumps({"type": "agent_end"}) + "\n",
            json.dumps({"type": "agent_settled"}) + "\n",
        ]
        proc = TestConsumeStreamTracksWrites.MockProc(lines)
        proc.stdin = DeadStdin()
        result = agent.backend_pi._consume_stream(
            proc, io.StringIO(), "", output_path=self.OUT,
        )
        assert result.stop_reason == "completed"
        assert result.error is None

    def test_a_refused_ask_does_not_turn_a_finished_run_into_an_error(self):
        """The first prompt staying fatal is the contrast — see TestFatalRpcResponse."""
        proc, result = self._run([
            json.dumps({"type": "agent_end"}),
            json.dumps({"type": "agent_settled"}),
            json.dumps({
                "type": "response", "command": "prompt",
                "success": False, "error": "nope",
            }),
        ])
        assert len(self._prompts(proc)) == 1
        assert result.stop_reason == "completed"
        assert result.error is None
