"""A pass says what it changed but did not commit, and what was dirty beforehand."""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke
import fix.containment
import fix.engine
import fix.leftovers
import git.land
from fix_containment_support import _repo
from fix_engine_support import StubAdapter, _answer
from review_fix_pass_support import (
    _PUSHED, _committed_paths, _make_job, git_wt, _run,
)
from review_record_support import finding, record

SERVICES = "ansible/inventory/group_vars/homelab/services.yml"
WARNING = "not in its commit"


def _scoped(wt, *paths):
    """An adapter whose commit is scoped to *paths*, as a real domain's is."""
    spec = fix.engine.LandSpec(message="fix: stub", paths=tuple(paths))
    return StubAdapter(wt, spec=spec)


def _fix_writing(adapter, wt, **files):
    def run_fix(phase, prompt, **kwargs):
        for rel, text in files.items():
            (wt / rel).write_text(text)
        return _answer(adapter)(phase, prompt, **kwargs)
    return run_fix


class TestLeftoversReported:
    @patch("git.push.push", return_value=_PUSHED)
    def test_a_fix_to_an_already_dirty_file_is_reported(self, _push, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "a.py").write_text("dirty\n")
        adapter = _scoped(wt, "b.py")

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(
                adapter, wt, **{"a.py": "fixed\n", "b.py": "new\n"}),
        ):
            fix.engine.run(adapter)

        err = capsys.readouterr().err
        assert "a.py" in err
        assert WARNING in err
        assert "already dirty" in err
        assert "a.py" not in git_out(wt, "show", "--name-only", "--format=", "HEAD")

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_non_anchor_pr_file_left_out_is_reported(
        self, _push, git_wt, tmp_path, capsys,
    ):
        services = git_wt / SERVICES
        services.parent.mkdir(parents=True)
        services.write_text("services:\n  foo: bar\n")
        git_out(git_wt, "add", "-A")
        git_out(git_wt, "commit", "-qm", "inventory")
        rec = record(finding("M1", "Missing helper", path="helper.py", checkbox=True))
        job = _make_job(git_wt, tmp_path, rec=rec, files=["helper.py", SERVICES])

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")
            services.write_text("fixture\nline2\n")

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert _committed_paths(git_wt) == {"helper.py"}
        err = capsys.readouterr().err
        leftovers_line = next(line for line in err.splitlines() if WARNING in line)
        assert SERVICES in leftovers_line

    @patch("git.push.push", return_value=_PUSHED)
    def test_artifacts_inside_the_worktree_are_not_leftovers(
        self, _push, tmp_path, capsys,
    ):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        adapter = _scoped(wt, "a.py")

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(adapter, wt, **{"a.py": "fixed\n"}),
        ):
            fix.engine.run(adapter)

        assert adapter.artifacts.is_dir()
        assert WARNING not in capsys.readouterr().err

    # passes-at-base: a clean landing must stay silent
    @patch("git.push.push", return_value=_PUSHED)
    def test_a_fully_committed_pass_reports_nothing(self, _push, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        adapter = StubAdapter(wt)

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(adapter, wt, **{"a.py": "fixed\n"}),
        ):
            fix.engine.run(adapter)

        assert "a.py" in git_out(wt, "show", "--name-only", "--format=", "HEAD")
        assert WARNING not in capsys.readouterr().err


class TestFind:
    def test_find_is_content_level(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "a.py").write_text("dirty\n")
        pre = fix.containment.take(wt)
        (wt / "a.py").write_text("changed again\n")

        found = fix.leftovers.find(wt, pre, {"a.py"})

        assert found.paths == ("a.py",)
        assert found.already_dirty == ("a.py",)

    def test_an_unreadable_tree_is_unknown_not_clean(self, tmp_path):
        found = fix.leftovers.find(
            tmp_path, fix.containment.Snapshot(ok=False), set(),
        )
        assert found.known is False
