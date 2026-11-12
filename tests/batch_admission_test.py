import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
from batch.model import Step  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402
from otto_queue.model import EntryClass  # noqa: E402

GiB = 1024 ** 3


def _proc(root, *, mem_kb=7_000_000, cpu=0.7, mem_psi=0.0, cg_max=None, cg_cur=None):
    (root / "proc" / "pressure").mkdir(parents=True)
    (root / "proc" / "meminfo").write_text(f"MemTotal: 16000000 kB\nMemAvailable: {mem_kb} kB\n")
    (root / "proc" / "pressure" / "cpu").write_text(
        f"some avg10={cpu} avg60=0.8 avg300=0.9 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=0\n")
    (root / "proc" / "pressure" / "memory").write_text(
        f"some avg10={mem_psi} avg60=0 avg300=0 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=1\n")
    cg = root / "sys" / "fs" / "cgroup"
    cg.mkdir(parents=True)
    (cg / "memory.max").write_text(f"{cg_max}\n" if cg_max else "max\n")
    if cg_cur is not None:
        (cg / "memory.current").write_text(f"{cg_cur}\n")
    return root


@pytest.mark.parametrize("text,value", [("2G", 2 * GiB), ("512M", 512 * 1024 ** 2),
                                         ("1536", 1536), ("1.5G", int(1.5 * GiB))])
def test_parse_size(text, value):
    assert batch.admission.parse_size(text) == value


def test_read_host_parses_meminfo_and_psi(tmp_path):
    s = batch.admission.read_host(_proc(tmp_path))
    assert s.mem_available == 7_000_000 * 1024
    assert (s.cpu_some_avg10, s.mem_some_avg10) == (0.7, 0.0)
    assert s.supports_admission


def test_read_host_takes_the_tighter_cgroup_limit(tmp_path):
    assert batch.admission.read_host(_proc(tmp_path, cg_max=3 * GiB, cg_cur=2 * GiB)).mem_available == GiB


def test_read_host_without_psi_does_not_support_admission(tmp_path):
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "meminfo").write_text("MemAvailable: 100 kB\n")
    assert batch.admission.read_host(tmp_path).supports_admission is False


def test_ceiling_clamps_to_pool_max_with_a_floor_of_one():
    cfg = BatchConfig(pool_max=2)
    assert (batch.admission.ceiling(5, cfg), batch.admission.ceiling(None, cfg), batch.admission.ceiling(0, cfg)) == (2, 2, 1)


CFG = BatchConfig(pool_max=3, mem_reserve="2G", cpu_pressure_max=30.0, mem_pressure_max=5.0)
HEALTHY = batch.admission.HostSample(mem_available=8 * GiB, cpu_some_avg10=1.0, mem_some_avg10=0.0)


def test_admits_when_healthy():
    assert batch.admission.decide(HEALTHY, running=1, limit=3, estimate=2 * GiB, cfg=CFG).admit


def test_refuses_at_the_limit():
    v = batch.admission.decide(HEALTHY, running=3, limit=3, estimate=1, cfg=CFG)
    assert not v.admit and "limit" in v.reason


def test_refuses_when_memory_short_and_says_how_much():
    s = batch.admission.HostSample(mem_available=5 * GiB, cpu_some_avg10=1.0, mem_some_avg10=0.0)
    v = batch.admission.decide(s, running=1, limit=3, estimate=4 * GiB, cfg=CFG)
    assert not v.admit and v.reason == "waiting for memory: 3.0 of 4.0 GB"


def test_refuses_on_cpu_pressure():
    s = batch.admission.HostSample(mem_available=8 * GiB, cpu_some_avg10=42.0, mem_some_avg10=0.0)
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=CFG)
    assert not v.admit and v.reason == "CPU pressure 42% >= 30%"


def test_refuses_on_memory_pressure():
    s = batch.admission.HostSample(mem_available=8 * GiB, cpu_some_avg10=1.0, mem_some_avg10=9.0)
    assert not batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=CFG).admit


BUSY = batch.admission.HostSample(mem_available=1 * GiB, cpu_some_avg10=99.0, mem_some_avg10=50.0)


def test_the_floor_slot_admits_one_even_on_a_busy_host():
    assert batch.admission.decide(BUSY, running=0, limit=3, estimate=4 * GiB, cfg=CFG,
                                  floor=True).admit


def test_nothing_running_no_longer_admits_on_its_own():
    """Regression: every driver admitted its first step, so N batches started N
    steps on a host with no memory for one."""
    v = batch.admission.decide(BUSY, running=0, limit=3, estimate=4 * GiB, cfg=CFG)
    assert not v.admit and "waiting for memory" in v.reason


def test_without_metrics_uses_the_static_pool_default():
    s = batch.admission.HostSample(None, None, None)
    cfg = BatchConfig(pool_max=3, pool_default=1)
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=cfg)
    assert not v.admit and "pool_default" in v.reason


def test_without_metrics_the_pool_default_counts_every_driver_on_the_machine():
    s = batch.admission.HostSample(None, None, None)
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


USER_SCOPE = "/user.slice/user-1000.slice/user@1000.service/app.slice/otto.scope"


def _self_cgroup(root, path):
    (root / "proc" / "self").mkdir(parents=True, exist_ok=True)
    (root / "proc" / "self" / "cgroup").write_text(f"0::{path}\n")


def _cgroup(root, rel, *, limit=None, current=None):
    node = root / "sys" / "fs" / "cgroup" / rel
    node.mkdir(parents=True, exist_ok=True)
    (node / "memory.max").write_text(f"{limit}\n" if limit is not None else "max\n")
    if current is not None:
        (node / "memory.current").write_text(f"{current}\n")


def test_read_host_reads_the_cap_on_its_own_nested_user_slice(tmp_path):
    root = _proc(tmp_path, mem_kb=15_000_000)
    _self_cgroup(root, USER_SCOPE)
    _cgroup(root, "user.slice/user-1000.slice", limit=int(6.5 * GiB), current=int(5.4 * GiB))
    _cgroup(root, USER_SCOPE.lstrip("/"), current=GiB)
    assert batch.admission.read_host(root).mem_available == int(6.5 * GiB) - int(5.4 * GiB)


def test_read_host_takes_the_tightest_ancestor_not_only_the_nearest(tmp_path):
    root = _proc(tmp_path)
    _self_cgroup(root, "/workbench.slice/otto-queue-7.service")
    _cgroup(root, "workbench.slice/otto-queue-7.service", limit=4 * GiB, current=GiB)
    _cgroup(root, "workbench.slice", limit=2 * GiB, current=int(1.5 * GiB))
    assert batch.admission.read_host(root).mem_available == 2 * GiB - int(1.5 * GiB)


# passes-at-base: a process in the root cgroup must keep reading the root files
def test_read_host_in_the_root_cgroup_reads_the_root_files(tmp_path):
    root = _proc(tmp_path, cg_max=3 * GiB, cg_cur=2 * GiB)
    _self_cgroup(root, "/")
    assert batch.admission.read_host(root).mem_available == GiB


# passes-at-base: a cgroup with no limit anywhere leaves MemAvailable in charge
def test_read_host_with_no_limit_on_the_chain_uses_meminfo(tmp_path):
    root = _proc(tmp_path, mem_kb=1_000_000)
    _self_cgroup(root, USER_SCOPE)
    _cgroup(root, USER_SCOPE.lstrip("/"), current=GiB)
    assert batch.admission.read_host(root).mem_available == 1_000_000 * 1024


VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   129580.
Pages active:                                 953633.
Pages inactive:                               950313.
Pages speculative:                              1962.
Pages throttled:                                   0.
"""
VM_STAT_AVAILABLE = 16384 * (129580 + 950313 + 1962)


def test_vm_stat_available_counts_free_inactive_and_speculative_pages():
    assert batch.admission._vm_stat_available(VM_STAT) == VM_STAT_AVAILABLE


def test_vm_stat_available_is_none_for_output_it_cannot_read():
    assert batch.admission._vm_stat_available("") is None
    assert batch.admission._vm_stat_available("Pages free: 1.\n") is None


def test_read_host_on_macos_reads_vm_stat_and_reports_no_pressure(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(batch.admission, "_vm_stat", lambda: VM_STAT)
    s = batch.admission.read_host()
    assert s.mem_available == VM_STAT_AVAILABLE
    assert (s.cpu_some_avg10, s.mem_some_avg10) == (None, None)
    assert s.supports_admission is False


# passes-at-base: batch on macOS keeps its pool_default path now that memory is known
def test_decide_with_memory_but_no_pressure_keeps_the_pool_default_path():
    s = batch.admission.HostSample(64 * GiB, None, None)
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=BatchConfig(pool_default=1))
    assert not v.admit and "pool_default" in v.reason


MAC = batch.admission.HostSample(16 * GiB, None, None)


def test_room_checks_only_the_metrics_a_sample_has():
    assert batch.admission.room(MAC, estimate=GiB, cfg=CFG).admit
    short = batch.admission.HostSample(2 * GiB, None, None)
    assert not batch.admission.room(short, estimate=GiB, cfg=CFG).admit
    assert batch.admission.room(batch.admission.HostSample(None, None, None), estimate=GiB, cfg=CFG).admit


@pytest.mark.parametrize("platform,limit", [("linux", 2), ("darwin", 4), ("freebsd14", 2)])
def test_the_heavy_limit_is_per_platform(platform, limit):
    assert batch.admission.class_limit(EntryClass.HEAVY, platform=platform, cores=18) == limit


def test_the_light_limit_is_every_core_but_one():
    assert batch.admission.class_limit(EntryClass.LIGHT, platform="linux", cores=4) == 3


def _admit(sample, entry_class, running):
    return batch.admission.admit_entry(sample, entry_class=entry_class, running=running, cfg=CFG)


def test_light_work_is_admitted_on_a_starved_host():
    assert _admit(BUSY, EntryClass.LIGHT, running=1).admit


def test_one_heavy_entry_is_always_admitted_when_none_is_running():
    assert _admit(BUSY, EntryClass.HEAVY, running=0).admit


def test_a_second_heavy_entry_needs_room():
    assert not _admit(BUSY, EntryClass.HEAVY, running=1).admit
    assert _admit(HEALTHY, EntryClass.HEAVY, running=1).admit
    assert _admit(MAC, EntryClass.HEAVY, running=1).admit


def test_queue_policy_reads_the_host_and_its_platform_limit():
    policy = batch.admission.QueuePolicy(CFG, host=lambda: BUSY, platform="linux", cores=4)
    assert (policy.limit(EntryClass.HEAVY), policy.limit(EntryClass.LIGHT)) == (2, 3)
    assert not policy.verdict(EntryClass.HEAVY, 1).admit
