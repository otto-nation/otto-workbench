"""Tests for pr-state-update CLI helper.

These test the script's argument parsing and error handling.
The core state logic is covered by pr_state_test.py (apply_state_update).
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "claude" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
SCRIPT = str(BIN_DIR / "pr-state-update")

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Import the extensionless script via importlib
_loader = importlib.machinery.SourceFileLoader("pr_state_update_cli", SCRIPT)
_spec = importlib.util.spec_from_loader("pr_state_update_cli", _loader, origin=SCRIPT)
pr_state_update_cli = importlib.util.module_from_spec(_spec)
pr_state_update_cli.__file__ = SCRIPT
_spec.loader.exec_module(pr_state_update_cli)


def test_invalid_domain_rejected(capsys):
    """argparse rejects unknown domains before main logic runs."""
    with patch("sys.argv", ["pr-state-update", "--domain", "bogus", "--repo-dir", "/tmp"]):
        try:
            pr_state_update_cli.main()
        except SystemExit as e:
            assert e.code == 2


def test_valid_domains_accepted():
    """Argparse choices match registered domains exactly."""
    import pr_state
    assert set(pr_state._DOMAIN_DESERIALIZERS.keys()) == {"ci", "review", "comments", "triage", "rebase"}
