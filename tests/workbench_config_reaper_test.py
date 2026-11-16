"""Reaper and queue-guard keys on WorkbenchConfig.

A sibling of ``workbench_config_maintenance_test.py``: defaults, ranges, and
the machine-only scope on every key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_write

from workbench_config_support import roots, _write

MACHINE_ONLY_KEYS = [
    "reaper.enabled",
    "reaper.grace_s",
    "reaper.max_minutes.lint",
    "reaper.max_minutes.go",
    "reaper.max_minutes.gh",
    "reaper.max_minutes.tasks",
    "queue.heavy_repos",
]


def _global(config_root, text):
    _write(config_root / config.workbench_config.CONFIG_NAME, text)


def test_defaults(roots):
    _, project = roots
    cfg = config.workbench_config.load_config(project)
    assert cfg.reaper.enabled is True
    assert cfg.reaper.grace_s == 30
    assert (cfg.reaper.max_minutes.lint, cfg.reaper.max_minutes.go) == (30, 30)
    assert (cfg.reaper.max_minutes.gh, cfg.reaper.max_minutes.tasks) == (10, 45)
    assert cfg.queue.heavy_repos == []


def test_values_are_read_from_the_global_file(roots):
    config_root, project = roots
    _global(config_root, "reaper:\n  grace_s: 60\n  max_minutes:\n    gh: 5\nqueue:\n  heavy_repos: [maximum, other]\n")
    cfg = config.workbench_config.load_config(project)
    assert cfg.reaper.grace_s == 60
    assert cfg.reaper.max_minutes.gh == 5
    assert cfg.reaper.max_minutes.lint == 30
    assert cfg.queue.heavy_repos == ["maximum", "other"]


def test_grace_outside_the_range_is_refused_and_the_edges_pass(roots):
    config_root, project = roots
    for bad in (4, 301):
        _global(config_root, f"reaper:\n  grace_s: {bad}\n")
        with pytest.raises(config.workbench_config.ConfigError, match="grace_s"):
            config.workbench_config.load_config(project)
    for good in (5, 300):
        _global(config_root, f"reaper:\n  grace_s: {good}\n")
        assert config.workbench_config.load_config(project).reaper.grace_s == good


@pytest.mark.parametrize("name", ["lint", "go", "gh", "tasks"])
def test_a_zero_max_minutes_is_refused(roots, name):
    config_root, project = roots
    _global(config_root, f"reaper:\n  max_minutes:\n    {name}: 0\n")
    with pytest.raises(config.workbench_config.ConfigError, match=f"max_minutes.{name}"):
        config.workbench_config.load_config(project)


@pytest.mark.parametrize("key", MACHINE_ONLY_KEYS)
def test_every_key_is_refused_at_project_scope(roots, key):
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_project_value(key, "1", project)


@pytest.mark.parametrize("key", MACHINE_ONLY_KEYS)
def test_every_key_is_defined(key):
    assert config.workbench_config.defines_key(key)
