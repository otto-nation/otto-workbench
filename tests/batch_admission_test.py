import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


import batch.admission  # noqa: E402
import otto_queue.admission  # noqa: E402
from batch.model import Step  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402

from admission_support import BUSY, CFG, GiB, HEALTHY  # noqa: E402


def test_ceiling_clamps_to_pool_max_with_a_floor_of_one():
    cfg = BatchConfig(pool_max=2)
    assert (batch.admission.ceiling(5, cfg), batch.admission.ceiling(None, cfg), batch.admission.ceiling(0, cfg)) == (2, 2, 1)


def test_admits_when_healthy():
    assert batch.admission.decide(HEALTHY, running=1, limit=3, estimate=2 * GiB, cfg=CFG).admit


def test_refuses_at_the_limit():
    v = batch.admission.decide(HEALTHY, running=3, limit=3, estimate=1, cfg=CFG)
    assert not v.admit and "limit" in v.reason


def test_refuses_when_memory_short_and_says_how_much():
    s = otto_queue.admission.HostSample(mem_available=5 * GiB, cpu_some_avg10=1.0, mem_some_avg10=0.0)
    v = batch.admission.decide(s, running=1, limit=3, estimate=4 * GiB, cfg=CFG)
    assert not v.admit and v.reason == "waiting for memory: 3.0 of 4.0 GB"


def test_refuses_on_cpu_pressure():
    s = otto_queue.admission.HostSample(mem_available=8 * GiB, cpu_some_avg10=42.0, mem_some_avg10=0.0)
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=CFG)
    assert not v.admit and v.reason == "CPU pressure 42% >= 30%"


def test_refuses_on_memory_pressure():
    s = otto_queue.admission.HostSample(mem_available=8 * GiB, cpu_some_avg10=1.0, mem_some_avg10=9.0)
    assert not batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=CFG).admit


def test_the_floor_slot_admits_one_even_on_a_busy_host():
    assert batch.admission.decide(BUSY, running=0, limit=3, estimate=4 * GiB, cfg=CFG,
                                  floor=True).admit


def test_nothing_running_no_longer_admits_on_its_own():
    """Regression: every driver admitted its first step, so N batches started N
    steps on a host with no memory for one."""
    v = batch.admission.decide(BUSY, running=0, limit=3, estimate=4 * GiB, cfg=CFG)
    assert not v.admit and "waiting for memory" in v.reason


def test_without_metrics_uses_the_static_pool_default():
    s = otto_queue.admission.HostSample(None, None, None)
    cfg = BatchConfig(pool_max=3, pool_default=1)
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=cfg)
    assert not v.admit and "pool_default" in v.reason


def test_without_metrics_the_pool_default_counts_every_driver_on_the_machine():
    s = otto_queue.admission.HostSample(None, None, None)
    cfg = BatchConfig(pool_max=3, pool_default=2)
    assert batch.admission.decide(s, running=0, limit=3, estimate=1, cfg=cfg, elsewhere=1).admit
    v = batch.admission.decide(s, running=0, limit=3, estimate=1, cfg=cfg, elsewhere=2)
    assert not v.admit and "2 step(s) running machine-wide" in v.reason


def test_refuses_without_crashing_on_a_malformed_mem_reserve():
    cfg = BatchConfig(pool_max=3, mem_reserve="2 gigs", cpu_pressure_max=30.0, mem_pressure_max=5.0)
    v = batch.admission.decide(HEALTHY, running=1, limit=3, estimate=1, cfg=cfg)
    assert not v.admit and "mem_reserve" in v.reason


def test_estimates_start_at_defaults_and_keep_the_running_max():
    e = batch.admission.Estimates.load()
    assert e.get("o/a", Step.REVIEW) == batch.admission.DEFAULT_ESTIMATES[Step.REVIEW]
    e.observe("o/a", Step.REVIEW, 5 * GiB)
    e.observe("o/a", Step.REVIEW, 1 * GiB)
    assert batch.admission.Estimates.load().get("o/a", Step.REVIEW) == 5 * GiB
    assert batch.admission.Estimates.load().get("o/b", Step.REVIEW) == batch.admission.DEFAULT_ESTIMATES[Step.REVIEW]


def test_concurrent_observers_both_land():
    """Regression: each driver rewrote the file from its own copy, so the last
    writer erased the other's peak."""
    a, b = batch.admission.Estimates.load(), batch.admission.Estimates.load()
    a.observe("o/a", Step.REVIEW, 5 * GiB)
    b.observe("o/b", Step.CI, 3 * GiB)
    fresh = batch.admission.Estimates.load()
    assert (fresh.get("o/a", Step.REVIEW), fresh.get("o/b", Step.CI)) == (5 * GiB, 3 * GiB)


def test_a_merge_keeps_the_larger_peak_whoever_wrote_it():
    a, b = batch.admission.Estimates.load(), batch.admission.Estimates.load()
    a.observe("o/a", Step.REVIEW, 5 * GiB)
    b.observe("o/a", Step.REVIEW, 3 * GiB)
    assert batch.admission.Estimates.load().get("o/a", Step.REVIEW) == 5 * GiB


def test_observe_survives_an_unwritable_lock_file(monkeypatch):
    def deny(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(batch.admission.os, "open", deny)
    e = batch.admission.Estimates.load()
    e.observe("o/a", Step.REVIEW, 5 * GiB)
    assert e.get("o/a", Step.REVIEW) == 5 * GiB


def test_tree_rss_sums_a_process_and_its_descendants(tmp_path):
    for pid, ppid, kb in [(10, 1, 100), (11, 10, 200), (12, 11, 300), (13, 1, 999)]:
        d = tmp_path / "proc" / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} (x (y) z) S {ppid} 0 0\n")
        (d / "status").write_text(f"Name: x\nVmRSS:\t{kb} kB\n")
    assert batch.admission.tree_rss(10, tmp_path) == 600 * 1024


# passes-at-base: batch on macOS keeps its pool_default path now that memory is known
def test_decide_with_memory_but_no_pressure_keeps_the_pool_default_path():
    s = otto_queue.admission.HostSample(64 * GiB, None, None)
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=BatchConfig(pool_default=1))
    assert not v.admit and "pool_default" in v.reason
