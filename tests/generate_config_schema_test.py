"""Tests for bin/local/generate-config-schema."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "generate-config-schema"


def test_resolves_root_from_its_own_path_when_cwd_is_outside_the_repo(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--emit", "config-reference"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "`reuse.level`" in result.stdout
