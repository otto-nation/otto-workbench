"""The review fix pass commits finding anchors and their tests, not every PR file."""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review_fix_pass_support import (
    _PUSHED, _committed_paths, _make_job, git_wt, _run,
)
from review_record_support import finding, record

SERVICES = "ansible/inventory/group_vars/homelab/services.yml"


class TestLandingGuard:
    @patch("git.push.push", return_value=_PUSHED)
    def test_a_pr_file_that_is_not_an_anchor_stays_in_the_worktree(
        self, _push, git_wt, tmp_path, capsys,
    ):
        services = git_wt / SERVICES
        services.parent.mkdir(parents=True)
        services.write_text("services:\n  foo: bar\n")
        git_out(git_wt, "add", "-A")
        git_out(git_wt, "commit", "-qm", "inventory")
        rec = record(finding("M1", "Missing helper", path="helper.py", checkbox=True))
        job = _make_job(
            git_wt, tmp_path, rec=rec, files=["helper.py", SERVICES],
        )

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            services.write_text("fixture\nline2\n")

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert _committed_paths(git_wt) == {"helper.py"}
        assert "fixture" in services.read_text()
        err = capsys.readouterr().err
        assert SERVICES in err
        assert "finding anchors" in err

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_anchor_and_its_test_are_still_committed(self, _push, git_wt, tmp_path):
        rec = record(finding("M1", "Needs a test", path="src.py", checkbox=True))
        job = _make_job(git_wt, tmp_path, rec=rec, files=["src.py"])

        def agent_run():
            (git_wt / "src.py").write_text("x = 2\n")
            (git_wt / "tests").mkdir()
            (git_wt / "tests" / "src_test.py").write_text("def test_src(): assert True\n")

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert _committed_paths(git_wt) == {"src.py", "tests/src_test.py"}
