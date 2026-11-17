"""Host and budget fixtures shared by the batch and queue admission suites."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.admission  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402

GiB = 1024 ** 3

CFG = BatchConfig(pool_max=3, mem_reserve="2G", cpu_pressure_max=30.0, mem_pressure_max=5.0)

HEALTHY = otto_queue.admission.HostSample(mem_available=8 * GiB, cpu_some_avg10=1.0, mem_some_avg10=0.0)

BUSY = otto_queue.admission.HostSample(mem_available=1 * GiB, cpu_some_avg10=99.0, mem_some_avg10=50.0)
