"""A run that has stopped is ended; a run that is merely quiet is not.

The false-positive cases matter more than the true positive here. A detector
that ends a wedged run is worth having; one that also ends a slow build is
worse than nothing, because the run it kills was going to succeed.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent import stall
from agent.stall import Sample, StallWatch


def _sample(pids, cpu):
    """A sample over *pids* whose CPU is all attributed to the last of them.

    Where the total sits does not matter to any assertion here; what matters
    is that it is attributable per pid, so the baseline exclusion can subtract
    it. Spreading it evenly would hide an exclusion that silently kept a share.
    """
    ordered = sorted(pids)
    return Sample(cpu_by_pid={p: (cpu if p == ordered[-1] else 0.0) for p in ordered})


class TestParseTime:
    """`ps` TIME is MM:SS.ss, or HH:MM:SS.ss once a process passes an hour."""

    def test_minutes_and_seconds(self):
        assert stall._parse_time("2:30.50") == pytest.approx(150.5)

    def test_hours_are_read_when_present(self):
        assert stall._parse_time("1:00:00.00") == pytest.approx(3600.0)

    def test_an_unreadable_field_is_zero_rather_than_an_exception(self):
        # The watchdog thread declining to act is the right failure; taking
        # down the run it is watching over a format surprise is not.
        assert stall._parse_time("garbage") == 0.0
        assert stall._parse_time("") == 0.0


class TestShowsWork:
    """Either signal alone is enough, and neither alone covers every workload."""

    def test_cpu_burned_is_work(self):
        before = _sample({1, 2}, 10.0)
        after = _sample({1, 2}, 10.0 + stall.CPU_FLOOR)
        assert stall.shows_work(before, after, frozenset({1})) is True

    def test_cpu_burned_by_the_agent_itself_is_work(self):
        # The root is in the baseline by construction, so a tool doing its
        # work in process rather than by spawning would be read as a stall
        # while running unless the root is exempted from the exclusion.
        before = Sample(cpu_by_pid={1: 10.0})
        after = Sample(cpu_by_pid={1: 10.0 + stall.CPU_FLOOR})
        assert stall.shows_work(before, after, frozenset({1}), 1) is True

    def test_a_changed_pid_set_is_work_even_with_no_cpu_delta(self):
        # The build shape: children too short-lived to accrue measurable time
        # and gone before the next sample. `ps` cannot recover a reaped
        # child's CPU, so churn is the only evidence this workload leaves.
        #
        # Every pid reports zero CPU on purpose. Giving the replacement child
        # a non-zero reading would let the CPU path answer this, and the test
        # would then pass with the churn signal deleted — which it did, before
        # the fixture was written this way.
        before = Sample(cpu_by_pid={1: 0.0, 2: 0.0})
        after = Sample(cpu_by_pid={1: 0.0, 3: 0.0})
        assert stall.shows_work(before, after, frozenset({1})) is True

    def test_neither_signal_is_a_stall(self):
        before = _sample({1, 2}, 10.0)
        after = _sample({1, 2}, 10.0)
        assert stall.shows_work(before, after, frozenset({1})) is False

    def test_cpu_below_the_floor_is_not_work(self):
        before = _sample({1, 2}, 10.0)
        after = _sample({1, 2}, 10.0 + stall.CPU_FLOOR / 2)
        assert stall.shows_work(before, after, frozenset({1})) is False

    def test_work_predating_the_call_does_not_count_as_this_call_working(self):
        # A background process left running by an earlier turn would otherwise
        # answer "is anything happening" forever on behalf of a call that has
        # stopped. Both samples hold it; only the baseline exclusion keeps the
        # verdict about the current call.
        before = _sample({1, 99}, 10.0)
        after = _sample({1, 99}, 500.0)
        assert stall.shows_work(before, after, frozenset({1, 99})) is False


class TestAnUnreadableSample:
    """A watchdog that cannot see must not be the thing that ends a run.

    `ps` failing looks exactly like an idle tree if the two are not told
    apart: no pids, no CPU, no churn. That would make the detector's own
    instrument a source of false aborts — the one false positive the CPU
    reading cannot argue away, because it is not about the workload.
    """

    def test_an_empty_sample_is_marked_unreadable(self):
        assert Sample(cpu_by_pid={}).readable is False

    def test_a_sample_holding_the_root_is_readable(self):
        # A live run always has at least the Pi process in its own tree, so
        # this is what makes the empty case unambiguous rather than merely
        # unusual.
        assert Sample(cpu_by_pid={1: 0.0}).readable is True

    def test_a_failed_reading_does_not_end_the_run(self, fast_watch, monkeypatch):
        recorder = _Recorder()
        monkeypatch.setattr(
            stall, "sample_subtree", lambda _root: Sample(cpu_by_pid={}),
        )
        watch = StallWatch(root_pid=1, send=recorder)
        watch.start()
        try:
            watch.arm("bash")
            time.sleep(1.0)
        finally:
            watch.stop()
        assert recorder.types == []
        assert watch.aborted_reason == ""

    def test_ps_failing_reads_as_unreadable_rather_than_as_idle(self, monkeypatch):
        # Driven through `_ps_rows` rather than by stubbing `sample_subtree`,
        # so the path from a failed command to an unreadable sample is the
        # one under test.
        monkeypatch.setattr(stall, "_ps_rows", lambda: [])
        assert stall.sample_subtree(1).readable is False


class TestSampleSubtree:
    """CPU is attributed by walking PPID, because PGID cannot see the child.

    Pi spawns every bash child `detached`, so each leads its own process
    group. A pgid filter reads zero for a maximally busy child, which would
    make the detector fire on every long command rather than on none.
    """

    @staticmethod
    def _spin():
        return subprocess.Popen(
            [sys.executable, "-c", "x=0\nwhile True: x+=1"], start_new_session=True,
        )

    def test_a_detached_busy_descendant_is_attributed_to_the_root(self):
        parent = subprocess.Popen(
            [sys.executable, "-c",
             "import subprocess,sys,time;"
             "subprocess.Popen([sys.executable,'-c','x=0\\nwhile True: x+=1'],"
             " start_new_session=True);"
             "time.sleep(30)"],
        )
        try:
            time.sleep(1.0)
            first = stall.sample_subtree(parent.pid)
            time.sleep(2.0)
            second = stall.sample_subtree(parent.pid)
            burned = second.cpu_over(second.pids) - first.cpu_over(first.pids)
            assert burned >= stall.CPU_FLOOR
        finally:
            parent.kill()
            parent.wait()

    def test_an_idle_subtree_accrues_nothing(self):
        sleeper = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            first = stall.sample_subtree(sleeper.pid)
            time.sleep(2.0)
            second = stall.sample_subtree(sleeper.pid)
            burned = second.cpu_over(second.pids) - first.cpu_over(first.pids)
            assert burned < stall.CPU_FLOOR
        finally:
            sleeper.kill()
            sleeper.wait()


class _Recorder:
    """Stands in for the RPC channel, recording what the watch sent."""

    def __init__(self):
        self.commands = []
        self.lock = threading.Lock()

    def __call__(self, command):
        with self.lock:
            self.commands.append(command)
        return True

    @property
    def types(self):
        with self.lock:
            return [c["type"] for c in self.commands]


@pytest.fixture
def fast_watch(monkeypatch):
    """The real timings with three zeroes knocked off, so a test can run."""
    monkeypatch.setattr(stall, "GAP_WINDOW", 0.10)
    monkeypatch.setattr(stall, "SAMPLE_INTERVAL", 0.02)
    monkeypatch.setattr(stall, "POLL_INTERVAL", 0.02)
    monkeypatch.setattr(stall, "CONFIRM_SAMPLES", 2)


def _settle(recorder, deadline=3.0):
    """Wait for the watch to reach a verdict, or give up and let the test fail."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if "abort" in recorder.types:
            return True
        time.sleep(0.02)
    return False


class TestStallWatch:
    def _watch(self, recorder, monkeypatch, *, working: bool):
        """A watch whose sampler reports a subtree that is or is not working."""
        counter = {"n": 0}

        def fake_sample(_root):
            counter["n"] += 1
            cpu = counter["n"] * 1.0 if working else 0.0
            return Sample(cpu_by_pid={1: cpu})

        monkeypatch.setattr(stall, "sample_subtree", fake_sample)
        return StallWatch(root_pid=1, send=recorder)

    def test_a_stall_is_confirmed_by_sampling_rather_than_by_the_gap_alone(
        self, fast_watch, monkeypatch,
    ):
        # The gap only arms suspicion. A command that prints nothing while it
        # works produces the same gap as a hang, so a detector that concluded
        # from the gap would end it — which is the objection `core.timeouts`
        # raises against bounding duration, reached by a different route.
        recorder = _Recorder()
        samples = []

        def fake_sample(_root):
            samples.append(1)
            return Sample(cpu_by_pid={1: 0.0})

        monkeypatch.setattr(stall, "sample_subtree", fake_sample)
        watch = StallWatch(root_pid=1, send=recorder)
        watch.start()
        try:
            watch.arm("bash")
            assert _settle(recorder)
        finally:
            watch.stop()
        # One for the baseline, one to open the confirm window, and one per
        # confirming sample. Anything fewer means a verdict was reached
        # without looking.
        assert len(samples) >= stall.CONFIRM_SAMPLES + 2

    def test_an_idle_armed_call_is_aborted(self, fast_watch, monkeypatch):
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=False)
        watch.start()
        try:
            watch.arm("bash")
            assert _settle(recorder), "a stalled call was never aborted"
        finally:
            watch.stop()
        assert watch.aborted_reason

    def test_a_working_call_is_never_aborted(self, fast_watch, monkeypatch):
        # The false positive that matters: a command that prints nothing while
        # it works looks exactly like a hang on the event stream, and only the
        # liveness reading separates them.
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=True)
        watch.start()
        try:
            watch.arm("bash")
            time.sleep(1.0)
        finally:
            watch.stop()
        assert recorder.types == []
        assert watch.aborted_reason == ""

    def test_an_unarmed_watch_never_aborts(self, fast_watch, monkeypatch):
        # Waiting on the model is silent and burns no local CPU. A watch left
        # armed across it would end healthy runs on every slow turn.
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=False)
        watch.start()
        try:
            time.sleep(0.8)
        finally:
            watch.stop()
        assert recorder.types == []

    def test_disarming_stops_a_verdict_already_under_way(
        self, fast_watch, monkeypatch,
    ):
        # The call can finish between the watcher deciding and the write
        # landing; an abort delivered then kills the *next* call.
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=False)
        watch.start()
        try:
            watch.arm("bash")
            watch.disarm()
            time.sleep(0.8)
        finally:
            watch.stop()
        assert recorder.types == []

    def test_a_verdict_is_dropped_when_the_call_it_judged_has_ended(
        self, fast_watch, monkeypatch,
    ):
        # The race the generation counter exists for: the call ends after the
        # watcher has decided to abort but before the write lands, so the
        # abort would reach whatever call came next.
        #
        # The window is narrower than it looks, which is why this drives it
        # from inside the sampler. `_confirm_stalled` re-reads the generation
        # before each sample, so a disarm landing *during* the final sample is
        # the one moment the loop cannot observe — it returns "stalled" for a
        # call that has already ended, and only the check inside `_abort`
        # stops the command going out.
        recorder = _Recorder()
        watch = StallWatch(root_pid=1, send=recorder)
        samples = {"n": 0}

        def fake_sample(_root):
            samples["n"] += 1
            if samples["n"] == stall.CONFIRM_SAMPLES + 2:
                watch.disarm()
            return Sample(cpu_by_pid={1: 0.0})

        monkeypatch.setattr(stall, "sample_subtree", fake_sample)
        watch.start()
        try:
            watch.arm("bash")
            time.sleep(1.0)
        finally:
            watch.stop()
        assert recorder.types == []

    def test_an_abort_disarms_so_it_cannot_fire_twice(
        self, fast_watch, monkeypatch,
    ):
        # Without this the watch stays armed after aborting, and every
        # subsequent poll re-sends abort and follow_up to a run that is
        # already ending — each one a command Pi answers and the stream loop
        # has to read.
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=False)
        watch.start()
        try:
            watch.arm("bash")
            assert _settle(recorder)
            time.sleep(0.5)
        finally:
            watch.stop()
        assert recorder.types.count("abort") == 1

    def test_the_abort_is_followed_by_the_salvage_prompt(
        self, fast_watch, monkeypatch,
    ):
        # An aborted run still reaches agent_end, and this is what makes the
        # ending worth anything: the agent is asked for what it already had.
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=False)
        watch.start()
        try:
            watch.arm("bash")
            assert _settle(recorder)
        finally:
            watch.stop()
        assert recorder.types == ["abort", "follow_up"]

    def test_the_agent_is_told_not_to_re_run_the_call(self, fast_watch, monkeypatch):
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=False)
        watch.start()
        try:
            watch.arm("bash -c 'sleep 999'")
            assert _settle(recorder)
        finally:
            watch.stop()
        message = recorder.commands[1]["message"]
        assert "bash -c 'sleep 999'" in message
        assert "Do not re-run it unchanged" in message

    def test_a_spinning_call_is_capped_rather_than_read_as_live(
        self, fast_watch, monkeypatch,
    ):
        # A busy-wait deadlock burns CPU, so the liveness test correctly reads
        # it as running and never fires. Nothing else catches it either: the
        # turn cap is counted at turn_end, which the call never reaches. The
        # absolute cap is the only thing between it and an unbounded run.
        monkeypatch.setattr(stall, "ABSOLUTE_CAP", 0.05)
        recorder = _Recorder()
        watch = self._watch(recorder, monkeypatch, working=True)
        watch.start()
        try:
            watch.arm("bash")
            assert _settle(recorder), "a spinning call ran past the absolute cap"
        finally:
            watch.stop()
        assert "longest a single call may run" in recorder.commands[1]["message"]
