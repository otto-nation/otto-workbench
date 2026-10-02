"""Fixtures shared by the workbench config suites.

Every one of them reads or writes a config under a sandboxed root, so the root,
the writer and the PyYAML skip marker are defined once here. `roots` is a
fixture: a suite imports it by name, which is how pytest finds it there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_report
import config.workbench_config_write

# The PyYAML write path only exists for a machine without yq, so the tests for
# it only run where PyYAML is installed — the same shape review_grouping_test
# uses for the reader.
needs_yaml = pytest.mark.skipif(config.workbench_config.yaml is None, reason="PyYAML not installed")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A sandboxed config root plus an empty project directory."""
    config_root = tmp_path / "config"
    config_root.mkdir()
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(config_root))
    project = tmp_path / "project"
    project.mkdir()
    return config_root, project


def _write(path: Path, text: str) -> None:
    path.write_text(text.lstrip("\n"))


def _row(status: config.workbench_config_report.ConfigStatus, key: str) -> config.workbench_config_report.ResolvedKey:
    return next(row for row in status.keys if row.key == key)
