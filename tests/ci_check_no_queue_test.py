"""ci-check --no-queue skips the otto-queue ticket.

Revert: drop --no-queue from the parser and the gate call.
"""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.ci_check  # noqa: E402
import cli.queue_gate  # noqa: E402


def test_parser_accepts_no_queue():
    parser = cli.ci_check.build_parser()
    assert parser.parse_args(["--no-queue"]).no_queue is True
    assert parser.parse_args([]).no_queue is False


def test_main_passes_no_queue_into_the_gate():
    seen = {}

    def gate(*_a, **kw):
        seen["no_queue"] = kw.get("no_queue")
        return 0

    with patch.object(cli.queue_gate, "gate", gate):
        assert cli.ci_check.main(["--no-queue"]) == 0
        assert seen["no_queue"] is True
        assert cli.ci_check.main(["--fix"]) == 0
        assert seen["no_queue"] is False
