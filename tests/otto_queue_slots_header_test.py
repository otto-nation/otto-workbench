"""The --json --slots header: host verdict, uncapped 24 h failure count, schema."""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.otto_queue  # noqa: E402
import config.workbench_config  # noqa: E402
import otto_queue.admission  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402

GB = otto_queue.admission.GB


def _policy(host):
    cfg = config.workbench_config.load_config_or_default(None).batch
    return otto_queue.admission.QueuePolicy(cfg, host=host)


def _header(capsys, monkeypatch, host=None):
    Ledger.open()
    if host is not None:
        monkeypatch.setattr(cli.otto_queue, "machine_policy", lambda: _policy(host))
    assert cli.otto_queue.main(["status", "--json", "--slots"]) == 0
    return json.loads(capsys.readouterr().out.splitlines()[0])


def _insert(*, state, finished_at, n=1):
    root = Ledger.open().root
    conn = sqlite3.connect(str(otto_queue.paths.db_path(root)), isolation_level=None)
    try:
        for _ in range(n):
            conn.execute(
                "INSERT INTO entries (kind, entry_class, argv, cwd, state, created_at, finished_at) "
                "VALUES ('ticket', 'heavy', ?, '/', ?, ?, ?)",
                ('["true"]', state, finished_at, finished_at),
            )
    finally:
        conn.close()


def test_slots_header_admits_heavy_when_the_host_has_room(capsys, monkeypatch):
    sample = otto_queue.admission.HostSample(8 * GB, 1.0, 0.0)
    header = _header(capsys, monkeypatch, host=lambda: sample)
    assert header["host"] == {"admit_heavy": True, "reason": "host has room"}
    assert header["schema"] == 1
    assert "id" not in header


def test_slots_header_refuses_heavy_with_the_admission_reason(capsys, monkeypatch):
    sample = otto_queue.admission.HostSample(int(2.3 * GB), 1.0, 0.0)
    header = _header(capsys, monkeypatch, host=lambda: sample)
    assert header["host"] == {
        "admit_heavy": False,
        "reason": "waiting for memory: 0.3 of 1.5 GB",
    }


@pytest.mark.parametrize(
    "sample, reason",
    (
        (otto_queue.admission.HostSample(8 * GB, 31.0, 0.0), "CPU pressure 31% >= 30%"),
        (otto_queue.admission.HostSample(8 * GB, 1.0, 29.0), "memory pressure 29% >= 5%"),
    ),
)
def test_slots_header_refuses_heavy_on_pressure(capsys, monkeypatch, sample, reason):
    header = _header(capsys, monkeypatch, host=lambda: sample)
    assert header["host"] == {"admit_heavy": False, "reason": reason}


def test_slots_header_host_is_null_when_the_probe_fails(capsys, monkeypatch):
    def boom():
        raise OSError("no host metrics")

    header = _header(capsys, monkeypatch, host=boom)
    assert header["host"] is None
    assert header["slots"]["heavy"]["running"] == 0


def test_slots_header_failed_24h_counts_beyond_recent_and_ignores_the_rest(capsys, monkeypatch):
    now = time.time()
    _insert(state="failed", finished_at=now, n=25)
    _insert(state="failed", finished_at=now - 25 * 3600, n=3)
    _insert(state="done", finished_at=now, n=2)
    _insert(state="cancelled", finished_at=now, n=1)
    header = _header(capsys, monkeypatch, host=lambda: otto_queue.admission.HostSample(8 * GB, 1.0, 0.0))
    assert header["failed_24h"] == 25
    assert header["slots"]["heavy"]["running"] == 0


def test_slots_header_failed_24h_is_null_when_the_ledger_cannot_be_read(capsys, monkeypatch):
    def boom(self, since):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(Ledger, "count_failed_since", boom)
    header = _header(capsys, monkeypatch, host=lambda: otto_queue.admission.HostSample(8 * GB, 1.0, 0.0))
    assert header["failed_24h"] is None
    assert header["schema"] == 1


def test_status_json_without_slots_does_not_print_a_header(capsys):
    Ledger.open()
    assert cli.otto_queue.main(["status", "--json"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == []
