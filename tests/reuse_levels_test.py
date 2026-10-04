"""Tests for config.reuse_levels and the two shims that only call it."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN = REPO_ROOT / "ai" / "claude" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import config.reuse_levels  # noqa: E402
import config.workbench_config  # noqa: E402
import config.workbench_config_write  # noqa: E402
from config.workbench_config import ConfigError  # noqa: E402


def test_track_reuse_command_sets_ultra(capsys):
    config.reuse_levels.track_reuse_command(
        io.StringIO('{"prompt":"/reuse ultra"}'),
    )
    out = capsys.readouterr().out
    assert "Reuse level set to: ultra —" in out
    assert config.workbench_config.load_config().reuse.level is (
        config.workbench_config.ReuseLevel.ULTRA
    )


def test_reuse_default_lite_sets_the_default(capsys):
    config.reuse_levels.track_reuse_command(
        io.StringIO('{"prompt":"/reuse default lite"}'),
    )
    assert "Default reuse level set to: lite —" in capsys.readouterr().out
    assert config.workbench_config.load_config().reuse.default is (
        config.workbench_config.ReuseLevel.LITE
    )


def test_unknown_level_prints_and_writes_nothing(capsys):
    config.reuse_levels.track_reuse_command(
        io.StringIO('{"prompt":"/reuse turbo"}'),
    )
    assert "Unknown reuse level" in capsys.readouterr().out
    assert config.workbench_config.load_config().reuse.level is None


@pytest.mark.parametrize("payload", ['{"prompt":"hello"}', "not-json"])
def test_non_reuse_and_invalid_json_print_nothing(payload, capsys):
    config.reuse_levels.track_reuse_command(io.StringIO(payload))
    assert capsys.readouterr().out == ""


def test_unwritable_config_prints_and_does_not_raise(capsys):
    with patch.object(
        config.workbench_config_write,
        "set_value",
        side_effect=ConfigError("nope"),
    ):
        config.reuse_levels.track_reuse_command(
            io.StringIO('{"prompt":"/reuse ultra"}'),
        )
    assert "Could not save the reuse level:" in capsys.readouterr().out
    assert config.workbench_config.load_config().reuse.level is None


def test_announce_to_subagent_prints_lite(capsys):
    config_dir = Path(os.environ["WORKBENCH_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text("reuse:\n  level: lite\n")
    config.reuse_levels.announce_to_subagent()
    assert "Reuse level: lite —" in capsys.readouterr().out


def test_announce_to_subagent_is_silent_with_no_config(capsys):
    config.reuse_levels.announce_to_subagent()
    assert capsys.readouterr().out == ""


def test_the_tracker_shim_reaches_ai_lib():
    result = subprocess.run(
        [sys.executable, str(BIN / "reuse-mode-tracker")],
        input='{"prompt":"/reuse ultra"}',
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "Reuse level set to: ultra" in result.stdout
    assert result.stderr == ""
    config_text = (
        Path(os.environ["WORKBENCH_CONFIG_DIR"]) / "config.yml"
    ).read_text()
    assert "ultra" in config_text


def test_the_subagent_shim_reaches_ai_lib():
    config_dir = Path(os.environ["WORKBENCH_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text("reuse:\n  level: lite\n")
    result = subprocess.run(
        [sys.executable, str(BIN / "reuse-subagent-start")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "Reuse level: lite —" in result.stdout
    assert result.stderr == ""
