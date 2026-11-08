"""Containment of extra worktrees a fix adapter grants the agent.

The comments pass adds the default-branch checkout as ``--add-dir`` / Pi
``REVIEW_ALLOWED_DIRS``. That is a write grant on both backends. These cases
drive two real git repos and a fake agent — they do not mock the restore they
exist to hold.
"""

import sys
from pathlib import Path
from unittest.mock import patch

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke
import fix.engine
import git.land
from git.land import CommitStatus

from fix_containment_support import _repo
from fix_engine_support import StubAdapter, _answer
from review_threads_support import _fix_adapter

CLOBBER = "agent-wrote-this\n"
OPERATOR = "operator-dirty\n"
ORIGINAL = "main\n"


class ExtraAdapter(StubAdapter):
    """A stub domain that also grants a sibling checkout."""

    def __init__(self, wt_path, extra, **kwargs):
        super().__init__(wt_path, **kwargs)
        self._extra = extra

    def contain_worktrees(self):
        return [self._extra]


def _trees(tmp_path, *, extra_files=None):
    pr_root = tmp_path / "pr"
    main_root = tmp_path / "main"
    pr_root.mkdir()
    main_root.mkdir()
    pr_wt = _repo(pr_root, {"a.py": "pr\n"})
    main_wt = _repo(main_root, extra_files or {"ref.py": ORIGINAL})
    return pr_wt, main_wt


def _run(adapter, run_fix):
    with (
        patch.object(git.land, "land",
                     return_value=git.land.LandResult(CommitStatus.PUSHED, "abc1234")),
        patch.object(agent.invoke, "run_fix", side_effect=run_fix),
    ):
        return fix.engine.run(adapter)


class TestExtraWorktreeRestore:
    def test_an_agent_write_into_the_extra_tree_is_reverted_and_saved(
        self, tmp_path, capsys,
    ):
        pr_wt, main_wt = _trees(tmp_path)
        adapter = ExtraAdapter(pr_wt, main_wt)

        def run_fix(_phase, _prompt, **_k):
            (main_wt / "ref.py").write_text(CLOBBER)
            return _answer(adapter)(_phase, _prompt, **_k)

        _run(adapter, run_fix)

        assert (main_wt / "ref.py").read_text() == ORIGINAL
        saved = adapter.artifacts / "containment" / "extra-worktree" / "ref.py"
        assert saved.read_text() == CLOBBER
        err = capsys.readouterr().err
        assert "ref.py" in err
        assert str(main_wt) in err

    def test_operator_dirt_the_agent_does_not_touch_is_left_alone(self, tmp_path):
        pr_wt, main_wt = _trees(tmp_path)
        (main_wt / "ref.py").write_text(OPERATOR)
        adapter = ExtraAdapter(pr_wt, main_wt)

        _run(adapter, _answer(adapter))

        assert (main_wt / "ref.py").read_text() == OPERATOR
        saved = adapter.artifacts / "containment" / "extra-worktree" / "ref.py"
        assert not saved.exists()

    def test_operator_dirt_the_agent_changes_is_restored_to_pre_pass(
        self, tmp_path,
    ):
        pr_wt, main_wt = _trees(tmp_path)
        (main_wt / "ref.py").write_text(OPERATOR)
        adapter = ExtraAdapter(pr_wt, main_wt)

        def run_fix(_phase, _prompt, **_k):
            (main_wt / "ref.py").write_text(CLOBBER)
            return _answer(adapter)(_phase, _prompt, **_k)

        _run(adapter, run_fix)

        assert (main_wt / "ref.py").read_text() == OPERATOR
        saved = adapter.artifacts / "containment" / "extra-worktree" / "ref.py"
        assert saved.read_text() == CLOBBER

    def test_the_pass_workdir_is_not_reverted_when_listed_as_extra(
        self, tmp_path,
    ):
        pr_root = tmp_path / "pr"
        pr_root.mkdir()
        pr_wt = _repo(pr_root, {"a.py": "orig\n"})
        adapter = ExtraAdapter(pr_wt, pr_wt)

        def run_fix(_phase, _prompt, **_k):
            (pr_wt / "a.py").write_text("fixed\n")
            return _answer(adapter)(_phase, _prompt, **_k)

        _run(adapter, run_fix)

        assert (pr_wt / "a.py").read_text() == "fixed\n"


class TestAdaptersWithoutExtraWorktrees:
    def test_the_base_adapter_lists_none(self, tmp_path):
        assert StubAdapter(tmp_path).contain_worktrees() == []

    def test_a_sibling_the_adapter_did_not_list_is_not_reverted(self, tmp_path):
        pr_wt, other = _trees(tmp_path)
        adapter = StubAdapter(pr_wt)

        def run_fix(_phase, _prompt, **_k):
            (other / "ref.py").write_text(CLOBBER)
            (pr_wt / "a.py").write_text("fixed\n")
            return _answer(adapter)(_phase, _prompt, **_k)

        _run(adapter, run_fix)

        assert (other / "ref.py").read_text() == CLOBBER
        assert (pr_wt / "a.py").read_text() == "fixed\n"


class TestCommentsAdapterWiring:
    def test_the_comments_adapter_contains_main_wt(self, tmp_path):
        adapter = _fix_adapter(tmp_path)
        main = tmp_path / "main-wt"
        main.mkdir()
        adapter.__dict__["main_wt"] = main
        assert adapter.contain_worktrees() == [main]

    def test_no_main_worktree_means_nothing_to_contain(self, tmp_path):
        adapter = _fix_adapter(tmp_path)
        adapter.__dict__["main_wt"] = None
        assert adapter.contain_worktrees() == []
