"""An unrecovered dirty tree is a refused commit, named on stderr."""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import git.land  # noqa: E402
from git.land import CommitStatus  # noqa: E402
from fix_containment_support import _repo  # noqa: E402


def test_an_unrecovered_dirty_tree_names_the_uncommitted_path(tmp_path, capsys):
    """paths=[] plus recover_from=HEAD is the shape that used to report silence."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("dirty\n")
    before = git_out(wt, "rev-parse", "HEAD").strip()

    with patch("git.push.push") as mock_push:
        landed = git.land.land(
            wt, message="fix: work", gated=False, owner_check=None,
            paths=[], recover_from=before,
        )

    assert landed.status is CommitStatus.COMMIT_FAILED
    err = capsys.readouterr().err
    assert "commit failed: changes remain uncommitted" in err
    assert "a.py" in err
    mock_push.assert_not_called()
