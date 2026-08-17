"""The trail-root sandbox proves itself: no test may write to the real root."""

import os
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import workbench_paths


def test_state_root_is_sandboxed_per_test(tmp_path):
    """Every test gets its own state root, so nothing lands in ~/.local/state."""
    assert os.environ["WORKBENCH_STATE_DIR"] == str(tmp_path / "state")


def test_state_dir_resolves_through_the_sandbox(tmp_path):
    """The env var is the whole mechanism — it reaches subprocesses too."""
    assert workbench_paths.state_dir() == tmp_path / "state"
