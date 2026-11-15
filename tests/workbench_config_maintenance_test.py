"""Maintenance prune and disk-pressure keys on WorkbenchConfig.

A sibling of ``workbench_config_test.py``: the new nested section, its ranges,
and the machine-only scope on every key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_write

from workbench_config_support import roots, _write


def test_maintenance_defaults(roots):
    _, project = roots
    cfg = config.workbench_config.load_config(project)
    assert cfg.maintenance.prune.go_cache is True
    assert cfg.maintenance.prune.go_cache_gb == 15
    assert cfg.maintenance.prune.mise is False
    assert cfg.maintenance.prune.docker is False
    assert cfg.maintenance.disk_pct == 85


def test_a_nested_value_is_read_from_the_global_file(roots):
    config_root, project = roots
    _write(config_root / config.workbench_config.CONFIG_NAME, """
maintenance:
  prune:
    docker: true
  disk_pct: 90
""")
    cfg = config.workbench_config.load_config(project)
    assert cfg.maintenance.prune.docker is True
    assert cfg.maintenance.disk_pct == 90
    assert cfg.maintenance.prune.go_cache is True


def test_disk_pct_outside_the_range_is_refused(roots):
    config_root, project = roots
    _write(config_root / config.workbench_config.CONFIG_NAME, "maintenance:\n  disk_pct: 49\n")
    with pytest.raises(config.workbench_config.ConfigError, match="disk_pct"):
        config.workbench_config.load_config(project)
    _write(config_root / config.workbench_config.CONFIG_NAME, "maintenance:\n  disk_pct: 100\n")
    with pytest.raises(config.workbench_config.ConfigError, match="disk_pct"):
        config.workbench_config.load_config(project)
    _write(config_root / config.workbench_config.CONFIG_NAME, "maintenance:\n  disk_pct: 50\n")
    assert config.workbench_config.load_config(project).maintenance.disk_pct == 50
    _write(config_root / config.workbench_config.CONFIG_NAME, "maintenance:\n  disk_pct: 99\n")
    assert config.workbench_config.load_config(project).maintenance.disk_pct == 99


def test_go_cache_gb_refuses_a_negative_and_accepts_zero(roots):
    config_root, project = roots
    _write(config_root / config.workbench_config.CONFIG_NAME, "maintenance:\n  prune:\n    go_cache_gb: -1\n")
    with pytest.raises(config.workbench_config.ConfigError, match="go_cache_gb"):
        config.workbench_config.load_config(project)
    _write(config_root / config.workbench_config.CONFIG_NAME, "maintenance:\n  prune:\n    go_cache_gb: 0\n")
    assert config.workbench_config.load_config(project).maintenance.prune.go_cache_gb == 0


def test_disk_pct_is_refused_at_project_scope(roots):
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_project_value(
            config.workbench_config.MAINTENANCE_DISK_PCT_KEY, "90", project,
        )
    config.workbench_config_write.set_value(config.workbench_config.MAINTENANCE_DISK_PCT_KEY, "90")
    assert config.workbench_config.load_config().maintenance.disk_pct == 90


def test_a_bool_key_reads_back_as_true(roots):
    config.workbench_config_write.set_value(config.workbench_config.MAINTENANCE_PRUNE_MISE_KEY, "true")
    assert config.workbench_config.load_config().maintenance.prune.mise is True


def test_every_maintenance_key_is_on_the_surface():
    for key in (
        config.workbench_config.MAINTENANCE_PRUNE_GO_CACHE_KEY,
        config.workbench_config.MAINTENANCE_PRUNE_GO_CACHE_GB_KEY,
        config.workbench_config.MAINTENANCE_PRUNE_MISE_KEY,
        config.workbench_config.MAINTENANCE_PRUNE_DOCKER_KEY,
        config.workbench_config.MAINTENANCE_DISK_PCT_KEY,
    ):
        assert config.workbench_config.defines_key(key)
