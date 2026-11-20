"""Characterization tests for `otto_queue.admission`: host reading, `room`, class limits and `QueuePolicy`.

Moved unchanged from `batch_admission_test.py` when the policy moved down a layer.
"""

import sys
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


import otto_queue.admission  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402
from otto_queue.model import EntryClass  # noqa: E402

from admission_support import BUSY, CFG, GiB, HEALTHY  # noqa: E402


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
    assert otto_queue.admission.parse_size(text) == value


def test_read_host_parses_meminfo_and_psi(tmp_path):
    s = otto_queue.admission.read_host(_proc(tmp_path))
    assert s.mem_available == 7_000_000 * 1024
    assert (s.cpu_some_avg10, s.mem_some_avg10) == (0.7, 0.0)
    assert s.supports_admission


def test_read_host_takes_the_tighter_cgroup_limit(tmp_path):
    assert otto_queue.admission.read_host(_proc(tmp_path, cg_max=3 * GiB, cg_cur=2 * GiB)).mem_available == GiB


def test_read_host_without_psi_does_not_support_admission(tmp_path):
    (tmp_path / "proc").mkdir()
    (tmp_path / "proc" / "meminfo").write_text("MemAvailable: 100 kB\n")
    assert otto_queue.admission.read_host(tmp_path).supports_admission is False


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
    assert otto_queue.admission.read_host(root).mem_available == int(6.5 * GiB) - int(5.4 * GiB)


def test_read_host_takes_the_tightest_ancestor_not_only_the_nearest(tmp_path):
    root = _proc(tmp_path)
    _self_cgroup(root, "/workbench.slice/otto-queue-7.service")
    _cgroup(root, "workbench.slice/otto-queue-7.service", limit=4 * GiB, current=GiB)
    _cgroup(root, "workbench.slice", limit=2 * GiB, current=int(1.5 * GiB))
    assert otto_queue.admission.read_host(root).mem_available == 2 * GiB - int(1.5 * GiB)


# passes-at-base: a process in the root cgroup must keep reading the root files
def test_read_host_in_the_root_cgroup_reads_the_root_files(tmp_path):
    root = _proc(tmp_path, cg_max=3 * GiB, cg_cur=2 * GiB)
    _self_cgroup(root, "/")
    assert otto_queue.admission.read_host(root).mem_available == GiB


# passes-at-base: a cgroup with no limit anywhere leaves MemAvailable in charge
def test_read_host_with_no_limit_on_the_chain_uses_meminfo(tmp_path):
    root = _proc(tmp_path, mem_kb=1_000_000)
    _self_cgroup(root, USER_SCOPE)
    _cgroup(root, USER_SCOPE.lstrip("/"), current=GiB)
    assert otto_queue.admission.read_host(root).mem_available == 1_000_000 * 1024


WORKBENCH_SLICE = "user.slice/user-1000.slice/user@1000.service/workbench.slice"


def test_read_host_admits_below_memory_high_not_only_memory_max(tmp_path):
    root = _proc(tmp_path)
    _self_cgroup(root, "/workbench.slice/otto-queue-7-1.scope")
    _cgroup(root, "workbench.slice", limit=int(2.5 * GiB), current=int(1.5 * GiB))
    (root / "sys" / "fs" / "cgroup" / "workbench.slice" / "memory.high").write_text(f"{2 * GiB}\n")
    assert otto_queue.admission.read_host(root).mem_available == 2 * GiB - int(1.5 * GiB)


def test_read_host_reads_the_named_cgroup_chain_rather_than_its_own(tmp_path):
    root = _proc(tmp_path)
    _self_cgroup(root, USER_SCOPE)
    _cgroup(root, WORKBENCH_SLICE, limit=int(2.5 * GiB), current=2 * GiB)
    sample = otto_queue.admission.read_host(root, cgroup=PurePosixPath("/" + WORKBENCH_SLICE))
    assert sample.mem_available == int(2.5 * GiB) - 2 * GiB


def test_slice_cgroup_is_a_child_of_the_user_manager(tmp_path):
    _self_cgroup(tmp_path, USER_SCOPE)
    assert otto_queue.admission.slice_cgroup("workbench.slice", tmp_path) == PurePosixPath("/" + WORKBENCH_SLICE)


def test_slice_cgroup_outside_a_user_manager_is_none(tmp_path):
    _self_cgroup(tmp_path, "/user.slice/user-1000.slice/session-3.scope")
    assert otto_queue.admission.slice_cgroup("workbench.slice", tmp_path) is None


VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   129580.
Pages active:                                 953633.
Pages inactive:                               950313.
Pages speculative:                              1962.
Pages throttled:                                   0.
"""


VM_STAT_AVAILABLE = 16384 * (129580 + 950313 + 1962)


def test_vm_stat_available_counts_free_inactive_and_speculative_pages():
    assert otto_queue.admission._vm_stat_available(VM_STAT) == VM_STAT_AVAILABLE


def test_vm_stat_available_is_none_for_output_it_cannot_read():
    assert otto_queue.admission._vm_stat_available("") is None
    assert otto_queue.admission._vm_stat_available("Pages free: 1.\n") is None


def test_read_host_on_macos_reads_vm_stat_and_reports_no_pressure(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(otto_queue.admission, "_vm_stat", lambda: VM_STAT)
    s = otto_queue.admission.read_host()
    assert s.mem_available == VM_STAT_AVAILABLE
    assert (s.cpu_some_avg10, s.mem_some_avg10) == (None, None)
    assert s.supports_admission is False


MAC = otto_queue.admission.HostSample(16 * GiB, None, None)


def test_room_checks_only_the_metrics_a_sample_has():
    assert otto_queue.admission.room(MAC, estimate=GiB, cfg=CFG).admit
    short = otto_queue.admission.HostSample(2 * GiB, None, None)
    assert not otto_queue.admission.room(short, estimate=GiB, cfg=CFG).admit
    assert otto_queue.admission.room(otto_queue.admission.HostSample(None, None, None), estimate=GiB, cfg=CFG).admit


@pytest.mark.parametrize("platform,limit", [("linux", 2), ("darwin", 4), ("freebsd14", 2)])
def test_the_heavy_limit_is_per_platform(platform, limit):
    assert otto_queue.admission.class_limit(EntryClass.HEAVY, platform=platform, cores=18) == limit


def test_the_light_limit_is_every_core_but_one():
    assert otto_queue.admission.class_limit(EntryClass.LIGHT, platform="linux", cores=4) == 3


def _admit(sample, entry_class, *, host_room=True):
    return otto_queue.admission.admit_entry(
        sample, entry_class=entry_class, cfg=CFG, host_room=host_room)


def test_light_work_is_admitted_on_a_starved_host():
    assert _admit(BUSY, EntryClass.LIGHT).admit


def test_a_heavy_job_needs_room():
    assert not _admit(BUSY, EntryClass.HEAVY).admit
    assert _admit(HEALTHY, EntryClass.HEAVY).admit
    assert _admit(MAC, EntryClass.HEAVY).admit


def test_a_heavy_ticket_skips_room():
    v = _admit(BUSY, EntryClass.HEAVY, host_room=False)
    assert v.admit
    assert v.reason == "in-process work is bounded by count alone"


def test_queue_policy_reads_the_host_and_its_platform_limit():
    policy = otto_queue.admission.QueuePolicy(CFG, host=lambda: BUSY, platform="linux", cores=4)
    assert (policy.limit(EntryClass.HEAVY), policy.limit(EntryClass.LIGHT)) == (2, 3)
    assert not policy.verdict(EntryClass.HEAVY, 1).admit
    assert policy.verdict(EntryClass.HEAVY, 1, host_room=False).admit
