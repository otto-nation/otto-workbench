"""Content-snapshot containment around the verify and suite phases.

Reproduces the real failure: a verify agent overwrote a tracked PR file with a
two-line fixture, and the pass committed it. These cases drive a real git repo
and a fake agent — they do not mock the restore function they exist to hold.
"""

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
import fix.verify
import git.land
import review.fix
from config.workbench_config import FixConfig, WorkbenchConfig
from git.land import CommitStatus

from fix_engine_support import StubAdapter, _answer
from review_fix_pass_support import (
    _PUSHED, _committed_paths, _make_job, git_wt, _answer as _review_answer,
)
from review_record_support import finding, record

SERVICES = "ansible/inventory/group_vars/homelab/services.yml"
ORIGINAL = "services:\n  foo: bar\n"
CLOBBER = "fixture\nline2\n"


def _repo(tmp_path, files):
    wt = tmp_path / "wt"
    wt.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(wt, "init", "-q", "-b", "main")
    git_out(wt, "config", "user.email", "test@example.com")
    git_out(wt, "config", "user.name", "Test")
    git_out(wt, "config", "commit.gpgsign", "false")
    git_out(wt, "config", "core.hooksPath", str(hooks))
    for rel, text in files.items():
        path = wt / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git_out(wt, "add", "-A")
    git_out(wt, "commit", "-qm", "initial")
    return wt


def _script(tmp_path, body):
    path = tmp_path / "checks"
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
    return str(path)


class TestSnapshot:
    def test_a_content_change_is_visible(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = fix.containment.take(wt)
        (wt / "a.py").write_text("two\n")
        after = fix.containment.take(wt)
        assert before.ok and after.ok
        assert before.by_path()["a.py"].sha != after.by_path()["a.py"].sha

    def test_a_gitignored_path_is_invisible(self, tmp_path):
        wt = _repo(tmp_path, {".gitignore": "*.cache\n", "a.py": "x\n"})
        (wt / "x.cache").write_text("nope\n")
        snap = fix.containment.take(wt)
        assert "x.cache" not in snap.by_path()

    def test_a_failed_read_is_not_an_empty_tree(self, tmp_path):
        snap = fix.containment.take(tmp_path)
        assert snap.ok is False
        assert snap.files == ()


class TestRestore:
    def test_a_failed_snapshot_reverts_nothing(self, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        before = fix.containment.take(wt)
        (wt / "a.py").write_text(CLOBBER)
        result = fix.containment.restore(
            wt, before, fix.containment.Snapshot(ok=False),
            artifacts=tmp_path / "art", phase="verify", trail=None,
        )
        assert result.paths == ()
        assert (wt / "a.py").read_text() == CLOBBER
        assert "leaving it as-is" in capsys.readouterr().err

    def test_restore_puts_pre_phase_bytes_back_and_saves_the_clobber(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        artifacts = tmp_path / "art"
        before = fix.containment.take(wt)
        (wt / "a.py").write_text(CLOBBER)
        after = fix.containment.take(wt)
        result = fix.containment.restore(
            wt, before, after, artifacts=artifacts, phase="verify", trail=None,
        )
        assert result.paths == ("a.py",)
        assert (wt / "a.py").read_text() == "one\n"
        assert (result.saved_to / "a.py").read_text() == CLOBBER


    def test_restore_returns_binary_content_byte_for_byte(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "one\n"})
        blob = bytes(range(256)) + b"\xff\xfe"
        (wt / "img.bin").write_bytes(blob)
        before = fix.containment.take(wt)
        (wt / "img.bin").write_bytes(b"clobbered")
        after = fix.containment.take(wt)
        fix.containment.restore(
            wt, before, after, artifacts=tmp_path / "art", phase="verify", trail=None,
        )
        assert (wt / "img.bin").read_bytes() == blob


class TestVerifyOverwrite:
    """The incident: verify overwrote a tracked PR file with a two-line fixture."""

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_tree_keeps_the_original_and_the_commit_does_not_carry_it(
        self, _push, git_wt, tmp_path, capsys,
    ):
        services = git_wt / SERVICES
        services.parent.mkdir(parents=True)
        services.write_text(ORIGINAL)
        git_out(git_wt, "add", "-A")
        git_out(git_wt, "commit", "-qm", "inventory")
        rec = record(finding("M1", "Missing helper", path="helper.py", checkbox=True))
        job = _make_job(
            git_wt, tmp_path, rec=rec, files=["helper.py", SERVICES],
        )

        def agent_run():
            (git_wt / "helper.py").write_text("def helper(): pass\n")

        def verify(*_a, **_k):
            services.write_text(CLOBBER)
            return {}

        with patch.object(fix.verify, "run", side_effect=verify):
            with patch.object(
                agent.invoke, "run_fix",
                side_effect=_review_answer(job, {"M1": "fixed"}, work=agent_run),
            ):
                review.fix.run_fix_pass(job)

        assert services.read_text() == ORIGINAL
        assert SERVICES not in _committed_paths(git_wt)
        assert "helper.py" in _committed_paths(git_wt)
        err = capsys.readouterr().err
        assert SERVICES in err
        saved = Path(job.artifact_dir) / "containment" / "verify" / SERVICES
        assert saved.read_text() == CLOBBER


class TestSuiteAndDirty:
    @patch("git.push.push", return_value=_PUSHED)
    def test_a_suite_write_to_an_in_scope_file_is_reverted(self, _push, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        clobber = _script(tmp_path, f"printf '%s\\n' suite > {wt / 'a.py'}")
        adapter = StubAdapter(wt)
        adapter.config = WorkbenchConfig(
            fix=FixConfig(verify_command=clobber, verify_timeout=30))

        def run_fix(_phase, _prompt, **_k):
            (wt / "a.py").write_text("fixed\n")
            return _answer(adapter)(_phase, _prompt, **_k)

        with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
            fix.engine.run(adapter)

        assert (wt / "a.py").read_text() == "fixed\n"

    def test_verify_rewrite_of_an_already_dirty_file_restores_pre_verify(
        self, tmp_path,
    ):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "a.py").write_text("dirty\n")
        adapter = StubAdapter(wt)

        def verify(*_a, **_k):
            (wt / "a.py").write_text(CLOBBER)
            return {}

        with patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)):
            fix.engine.run(adapter, verify=verify)

        assert (wt / "a.py").read_text() == "dirty\n"

    @patch("git.push.push", return_value=_PUSHED)
    def test_the_fix_agents_edit_to_an_anchor_survives_verify(self, _push, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        adapter = StubAdapter(wt)

        def run_fix(_phase, _prompt, **_k):
            (wt / "a.py").write_text("fixed\n")
            return _answer(adapter)(_phase, _prompt, **_k)

        def verify(*_a, **_k):
            (wt / "a.py").write_text(CLOBBER)
            return {}

        with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
            fix.engine.run(adapter, verify=verify)

        assert (wt / "a.py").read_text() == "fixed\n"


class TestHeadMoved:
    def test_an_agent_commit_refuses_to_land(self, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        adapter = StubAdapter(wt)
        before = git_out(wt, "rev-parse", "HEAD").strip()

        def run_fix(_phase, _prompt, **_k):
            (wt / "a.py").write_text("fixed\n")
            git_out(wt, "add", "a.py")
            git_out(wt, "commit", "-qm", "agent")
            return _answer(adapter)(_phase, _prompt, **_k)

        with patch.object(git.land, "land") as land:
            with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
                run = fix.engine.run(adapter)

        land.assert_not_called()
        assert run.landed.status is CommitStatus.COMMIT_FAILED
        now = git_out(wt, "rev-parse", "HEAD").strip()
        assert now != before
        err = capsys.readouterr().err
        assert "HEAD moved" in err
        assert before[:7] in err
        assert now[:7] in err

    def test_recover_still_lands(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        adapter = StubAdapter(
            wt, spec=fix.engine.LandSpec(message="fix: stub", recover=True),
        )

        def run_fix(_phase, _prompt, **_k):
            (wt / "a.py").write_text("fixed\n")
            git_out(wt, "add", "a.py")
            git_out(wt, "commit", "-qm", "agent")
            return _answer(adapter)(_phase, _prompt, **_k)

        with patch.object(
            git.land, "land",
            return_value=git.land.LandResult(CommitStatus.PUSHED, "abc1234"),
        ) as land:
            with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
                fix.engine.run(adapter)

        land.assert_called_once()
        assert land.call_args.kwargs["recover_from"]
