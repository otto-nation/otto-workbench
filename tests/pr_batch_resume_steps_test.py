"""`pr batch resume` is gone; usage exit 2."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_batch_resume_fails_usage(capsys):
    assert _main(["batch", "resume", "r1", "--steps", "rebase,ci,comments"]) == 2
    err = capsys.readouterr().err.lower()
    assert "invalid choice" in err or "unrecognized" in err or "invalid" in err
