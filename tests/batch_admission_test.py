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


def test_floor_admits_one_even_on_a_busy_host():
    s = batch.admission.HostSample(mem_available=1 * GiB, cpu_some_avg10=99.0, mem_some_avg10=50.0)
    assert batch.admission.decide(s, running=0, limit=3, estimate=4 * GiB, cfg=CFG).admit


def test_without_metrics_uses_the_static_pool_default():
    s = batch.admission.HostSample(None, None, None)
    cfg = BatchConfig(pool_max=3, pool_default=1)
    assert batch.admission.decide(s, running=0, limit=3, estimate=1, cfg=cfg).admit
    v = batch.admission.decide(s, running=1, limit=3, estimate=1, cfg=cfg)
    assert not v.admit and "pool_default" in v.reason


def test_estimates_start_at_defaults_and_keep_the_running_max():
    e = batch.admission.Estimates.load()
    assert e.get("o/a", Step.REVIEW) == batch.admission.DEFAULT_ESTIMATES[Step.REVIEW]
    e.observe("o/a", Step.REVIEW, 5 * GiB)
    e.observe("o/a", Step.REVIEW, 1 * GiB)
    assert batch.admission.Estimates.load().get("o/a", Step.REVIEW) == 5 * GiB
    assert batch.admission.Estimates.load().get("o/b", Step.REVIEW) == batch.admission.DEFAULT_ESTIMATES[Step.REVIEW]


def test_tree_rss_sums_a_process_and_its_descendants(tmp_path):
    for pid, ppid, kb in [(10, 1, 100), (11, 10, 200), (12, 11, 300), (13, 1, 999)]:
        d = tmp_path / "proc" / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} (x (y) z) S {ppid} 0 0\n")
        (d / "status").write_text(f"Name: x\nVmRSS:\t{kb} kB\n")
    assert batch.admission.tree_rss(10, tmp_path) == 600 * 1024
