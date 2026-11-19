"""Tests for otto_queue.wait_report: kind classification and quiet wait notices."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from otto_queue.wait_report import (  # noqa: E402
    WaitKind, WaitReporter, format_duration, reason_kind)

# Every Verdict.reason otto_queue.admission can produce, plus the wait strings
# the queue itself adds (slots taken, behind an older entry) and the scheduler's
# per-run concurrency-limit wording reason_kind still classifies.
KIND_CASES = (
    ("at the concurrency limit (4)", WaitKind.SLOTS),
    ("batch.mem_reserve is not a size: 'bogus'", WaitKind.OTHER),
    ("waiting for memory: 0.3 of 1.5 GB", WaitKind.MEMORY),
    ("CPU pressure 31.4% >= 30%", WaitKind.CPU),
    ("memory pressure 41.2% >= 40%", WaitKind.MEMORY),
    ("host has room", WaitKind.OTHER),
    ("light work is bounded by count alone", WaitKind.OTHER),
    ("all 4 heavy slots are taken", WaitKind.SLOTS),
    ("behind entry 107, which queued first", WaitKind.BEHIND),
)

FIRST_BLOCK = (
    "otto-queue: queued as entry 107 (heavy). Waiting for room: memory.",
    "  status: otto-queue status    cancel: otto-queue cancel 107    "
    "Herdr: the Queue panel",
)


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize("reason,kind", KIND_CASES)
def test_reason_kind_classifies_every_admission_string(reason, kind):
    assert reason_kind(reason) is kind


def test_first_wait_prints_the_block_once():
    clock = FakeClock()
    lines: list[str] = []
    reporter = WaitReporter(107, "heavy", lines.append, clock=clock)
    reporter.waiting("waiting for memory: 0.3 of 1.5 GB")
    reporter.waiting("waiting for memory: 0.3 of 1.5 GB")
    assert tuple(lines) == FIRST_BLOCK


def test_numeric_wobble_prints_nothing():
    clock = FakeClock()
    lines: list[str] = []
    reporter = WaitReporter(107, "heavy", lines.append, clock=clock)
    reporter.waiting("waiting for memory: 0.3 of 1.5 GB")
    reporter.waiting("waiting for memory: 0.2 of 1.5 GB")
    reporter.waiting("CPU pressure 31.4% >= 30%")
    reporter.waiting("CPU pressure 32.1% >= 30%")
    assert tuple(lines) == FIRST_BLOCK


def test_kind_change_prints_once_and_is_rate_limited():
    clock = FakeClock()
    lines: list[str] = []
    reporter = WaitReporter(107, "heavy", lines.append, clock=clock)
    reporter.waiting("waiting for memory: 0.3 of 1.5 GB")
    clock.advance(1)
    reporter.waiting("CPU pressure 31.4% >= 30%")
    assert tuple(lines) == FIRST_BLOCK
    clock.advance(29)
    reporter.waiting("CPU pressure 32.1% >= 30%")
    assert lines[-1] == "otto-queue: entry 107 still waiting: CPU pressure"
    assert len(lines) == 3
    clock.advance(1)
    reporter.waiting("all 4 heavy slots are taken")
    assert len(lines) == 3
    clock.advance(30)
    reporter.waiting("all 4 heavy slots are taken")
    assert lines[-1] == "otto-queue: entry 107 still waiting: all slots taken"
    assert len(lines) == 4


def test_admitted_line_names_the_duration():
    clock = FakeClock()
    lines: list[str] = []
    reporter = WaitReporter(107, "heavy", lines.append, clock=clock)
    reporter.waiting("waiting for memory: 0.3 of 1.5 GB")
    clock.advance(4 * 60 + 3)
    reporter.admitted()
    assert lines[-1] == "otto-queue: entry 107 admitted after 4m 3s"
    assert format_duration(4 * 60 + 3) == "4m 3s"


def test_admitted_without_a_wait_prints_nothing():
    lines: list[str] = []
    WaitReporter(107, "heavy", lines.append, clock=FakeClock()).admitted()
    assert lines == []
