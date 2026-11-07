"""The git PATH shim refuses mutating subcommands in the fix worktree."""

import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import GIT_TIMEOUT, git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.backend
import agent.git_guard
from fix_containment_support import _repo

_COMMIT = ("commit", "-am", "x")


def _dirty(wt: Path) -> Path:
    (wt / "a.py").write_text("dirty\n")
    return wt


def _git(env, args, *, cwd=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )


def _head(wt: Path) -> str:
    return git_out(wt, "rev-parse", "HEAD").strip()


class TestShim:
    @pytest.mark.parametrize("shape", [
        "dash_c", "no_pager", "config_and_c", "git_dir", "subdir",
    ])
    def test_a_flagged_commit_in_the_fix_worktree_is_refused(self, tmp_path, shape):
        wt = _dirty(_repo(tmp_path, {"a.py": "one\n"}))
        before = _head(wt)
        (wt / "sub").mkdir()
        args, cwd = {
            "dash_c": (("-C", str(wt), *_COMMIT), tmp_path),
            "no_pager": (("--no-pager", *_COMMIT), wt),
            "config_and_c": (
                ("-c", "core.pager=cat", "-C", str(wt), *_COMMIT), tmp_path,
            ),
            "git_dir": (
                (f"--git-dir={wt / '.git'}", f"--work-tree={wt}", *_COMMIT),
                tmp_path,
            ),
            "subdir": (_COMMIT, wt / "sub"),
        }[shape]
        with agent.git_guard.guarded(dict(os.environ), str(wt)) as env:
            result = _git(env, args, cwd=cwd)
        assert result.returncode != 0
        assert "git-guard: refusing" in result.stderr
        assert _head(wt) == before

    def test_push_reset_checkout_stash_clean_are_refused(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        calls = (
            ("push",),
            ("reset", "--hard"),
            ("checkout", "-b", "elsewhere"),
            ("stash",),
            ("clean", "-fd"),
        )
        with agent.git_guard.guarded(dict(os.environ), str(wt)) as env:
            for args in calls:
                result = _git(env, ("-C", str(wt), *args), cwd=tmp_path)
                assert result.returncode != 0, args
                assert "git-guard: refusing" in result.stderr, args

    def test_a_commit_in_another_repo_passes_through(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        other_root = tmp_path / "other"
        other_root.mkdir()
        other = _repo(other_root, {"b.py": "two\n"})
        before = _head(other)
        with agent.git_guard.guarded(dict(os.environ), str(wt)) as env:
            result = _git(
                env, ("-C", str(other), "commit", "--allow-empty", "-m", "x"),
                cwd=tmp_path,
            )
        assert result.returncode == 0, result.stderr
        assert _head(other) != before

    def test_reads_and_dry_runs_pass_through(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        calls = (
            ("status",),
            ("log", "-1"),
            ("diff",),
            ("stash", "list"),
            ("push", "--dry-run"),
        )
        real = dict(os.environ)
        with agent.git_guard.guarded(real, str(wt)) as env:
            for args in calls:
                argv = ("-C", str(wt), *args)
                guarded_rc = _git(env, argv, cwd=tmp_path).returncode
                plain_rc = _git(real, argv, cwd=tmp_path).returncode
                assert guarded_rc == plain_rc, args

    def test_an_alias_to_commit_is_refused(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = _head(wt)
        with agent.git_guard.guarded(dict(os.environ), str(wt)) as env:
            result = _git(
                env,
                ("-C", str(wt), "-c", "alias.ci=commit", "ci",
                 "--allow-empty", "-m", "x"),
                cwd=tmp_path,
            )
        assert result.returncode != 0
        assert "git-guard: refusing" in result.stderr
        assert _head(wt) == before

    def test_a_help_looking_message_value_does_not_disarm(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = _head(wt)
        with agent.git_guard.guarded(dict(os.environ), str(wt)) as env:
            for args in (("commit", "--allow-empty", "-m", "-h"),
                         ("commit", "--allow-empty", "-m", "--help"),
                         ("commit", "--allow-empty", "--", "-h"),
                         ("commit", "--allow-empty", "-am", "-h"),
                         ("commit", "--allow-empty", "-am", "--help"),
                         ("commit", "--allow-empty", "-m", "--dry-run"),
                         ("commit", "--allow-empty", "-am", "--dry-run")):
                result = _git(env, ("-C", str(wt), *args), cwd=tmp_path)
                assert result.returncode != 0, args
                assert "git-guard: refusing" in result.stderr, args
        assert _head(wt) == before

    def test_an_alias_chain_to_commit_is_refused(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = _head(wt)
        with agent.git_guard.guarded(dict(os.environ), str(wt)) as env:
            result = _git(
                env,
                ("-C", str(wt), "-c", "alias.a=b", "-c", "alias.b=commit",
                 "a", "--allow-empty", "-m", "x"),
                cwd=tmp_path,
            )
        assert result.returncode != 0
        assert "git-guard: refusing" in result.stderr
        assert _head(wt) == before

    def test_the_shim_is_executable(self):
        assert os.access(agent.git_guard.SHIM, os.X_OK)

    def test_a_hand_commit_outside_the_agent_env_is_unaffected(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = _head(wt)
        with agent.git_guard.guarded(dict(os.environ), str(wt)):
            git_out(wt, "commit", "--allow-empty", "-m", "hand")
        assert _head(wt) != before

    def test_no_repo_means_no_guard(self, tmp_path):
        with agent.git_guard.guarded(dict(os.environ), str(tmp_path)) as env:
            assert agent.git_guard.ENV_GUARDED_GIT_DIR not in env


class TestInvokeFixGuard:
    def test_invoke_fix_hands_the_backend_a_guarded_env(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = _head(wt)
        seen = {}

        def invoke_fix(inv):
            seen["inv"] = inv
            return subprocess.run(
                ["git", "-C", inv.cwd, "commit", "--allow-empty", "-m", "x"],
                env=agent.backend.agent_env(inv),
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT,
            ).returncode

        fake = types.SimpleNamespace(invoke_fix=invoke_fix)
        with patch.object(agent.backend, "_get_module", return_value=fake):
            code = agent.backend.invoke_fix(agent.backend.AgentInvocation(
                prompt="p", cwd=str(wt),
            ))
        assert code != 0
        assert _head(wt) == before
        assert seen["inv"].env is not None
        assert agent.git_guard.ENV_GUARDED_GIT_DIR in seen["inv"].env
