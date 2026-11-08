"""A suite that rewrites a tracked file is restored, and the tally says so.

Containment already puts pre-suite bytes back. These cases hold the other half:
the pass stays unfailed, the warning names the tracked files, and the green
verdict is reported as the existing unverified/partial form so CI is not told
the commit matches the tree the checks saw.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke
import fix.engine
import fix.suite
from config.workbench_config import FixConfig, WorkbenchConfig
from pr.fix import CHECKS_TRAILER, FixOutcome

from fix_containment_support import _repo
from fix_engine_support import StubAdapter, _answer
from review_fix_pass_support import _PUSHED, _committed_paths, _make_job, _run
from review_record_support import finding, record

REWRITE_WARN = "The repo's checks rewrote"
REWRITE_TALLY = "unverified: the checks rewrote"


def _script(tmp_path, body):
    path = tmp_path / "checks"
    path.write_text(f"#!/usr/bin/env bash\nset -e\n{body}\n")
    path.chmod(0o755)
    return str(path)


def _configured(wt, command):
    adapter = StubAdapter(wt)
    adapter.config = WorkbenchConfig(
        fix=FixConfig(verify_command=command, verify_timeout=30))
    return adapter


def _fix_writing(adapter, wt, **files):
    def run_fix(phase, prompt, **kwargs):
        for rel, text in files.items():
            (wt / rel).write_text(text)
        return _answer(adapter)(phase, prompt, **kwargs)
    return run_fix


def _head_blob(wt, rel):
    return git_out(wt, "show", f"HEAD:{rel}")


def _message(wt):
    return git_out(wt, "log", "-1", "--format=%B")


class TestSuiteRewroteTracked:
    @patch("git.push.push", return_value=_PUSHED)
    def test_a_tracked_rewrite_is_restored_and_reported(
        self, _push, tmp_path, capsys,
    ):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        clobber = _script(tmp_path, f"printf '%s\\n' formatted > {wt / 'a.py'}")
        adapter = _configured(wt, clobber)
        trail = _RecordingTrail()

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(adapter, wt, **{"a.py": "fixed\n"}),
        ):
            run = fix.engine.run(adapter, trail=trail)

        assert (wt / "a.py").read_text() == "fixed\n"
        assert _head_blob(wt, "a.py") == "fixed\n"
        assert run.suite.status is fix.suite.SuiteStatus.PARTIAL
        assert run.suite.rewrote == ("a.py",)
        assert run.outcomes[0].outcome is FixOutcome.FIXED
        err = capsys.readouterr().err
        assert REWRITE_WARN in err
        assert "a.py" in err
        assert f"{CHECKS_TRAILER}: partial" in _message(wt)
        assert any(
            event[1] == "fix_suite_rewrote_files" for event in trail.events
        )
        assert REWRITE_TALLY in fix.suite.qualify_tally(
            "1 fixed, 0 skipped", run.suite,
        )

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_ignored_output_does_not_qualify(
        self, _push, tmp_path, capsys,
    ):
        wt = _repo(tmp_path, {".gitignore": "build/\n", "a.py": "orig\n"})
        (wt / "build").mkdir()
        clobber = _script(tmp_path, f"printf '%s\\n' out > {wt / 'build' / 'out'}")
        adapter = _configured(wt, clobber)

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(adapter, wt, **{"a.py": "fixed\n"}),
        ):
            run = fix.engine.run(adapter)

        assert run.suite.status is fix.suite.SuiteStatus.GREEN
        assert run.suite.rewrote == ()
        assert REWRITE_WARN not in capsys.readouterr().err
        assert f"{CHECKS_TRAILER}: green" in _message(wt)
        assert fix.suite.qualify_tally("1 fixed, 0 skipped", run.suite) == (
            "1 fixed, 0 skipped"
        )

    @patch("git.push.push", return_value=_PUSHED)
    def test_an_untracked_output_does_not_qualify(
        self, _push, tmp_path, capsys,
    ):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        clobber = _script(tmp_path, f"printf '%s\\n' gen > {wt / 'generated.txt'}")
        adapter = _configured(wt, clobber)

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(adapter, wt, **{"a.py": "fixed\n"}),
        ):
            run = fix.engine.run(adapter)

        assert not (wt / "generated.txt").exists()
        assert run.suite.status is fix.suite.SuiteStatus.GREEN
        assert run.suite.rewrote == ()
        assert REWRITE_WARN not in capsys.readouterr().err
        assert fix.suite.qualify_tally("1 fixed, 0 skipped", run.suite) == (
            "1 fixed, 0 skipped"
        )

    @patch("git.push.push", return_value=_PUSHED)
    def test_a_suite_that_changes_nothing_stays_green(self, _push, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        adapter = _configured(wt, _script(tmp_path, "exit 0"))

        with patch.object(
            agent.invoke, "run_fix",
            side_effect=_fix_writing(adapter, wt, **{"a.py": "fixed\n"}),
        ):
            run = fix.engine.run(adapter)

        assert (wt / "a.py").read_text() == "fixed\n"
        assert _head_blob(wt, "a.py") == "fixed\n"
        assert run.suite.status is fix.suite.SuiteStatus.GREEN
        assert run.suite.rewrote == ()
        assert REWRITE_WARN not in capsys.readouterr().err
        assert f"{CHECKS_TRAILER}: green" in _message(wt)
        assert fix.suite.qualify_tally("1 fixed, 0 skipped", run.suite) == (
            "1 fixed, 0 skipped"
        )


class TestWithRewrites:
    def test_a_head_path_demotes_green_to_partial(self, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        trail = _RecordingTrail()

        result = fix.suite.with_rewrites(
            fix.suite.SuiteResult(status=fix.suite.SuiteStatus.GREEN, command="c"),
            ("a.py",), wt, trail,
        )

        assert result.status is fix.suite.SuiteStatus.PARTIAL
        assert result.rewrote == ("a.py",)
        assert any(event[1] == "fix_suite_rewrote_files" for event in trail.events)
        err = capsys.readouterr().err
        assert REWRITE_WARN in err
        assert "a.py" in err

    def test_an_untracked_path_is_not_this_case(self, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        trail = _RecordingTrail()

        result = fix.suite.with_rewrites(
            fix.suite.SuiteResult(status=fix.suite.SuiteStatus.GREEN),
            ("generated.txt",), wt, trail,
        )

        assert result.status is fix.suite.SuiteStatus.GREEN
        assert result.rewrote == ()
        assert trail.events == []
        assert REWRITE_WARN not in capsys.readouterr().err

    def test_red_stays_red(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})

        result = fix.suite.with_rewrites(
            fix.suite.SuiteResult(status=fix.suite.SuiteStatus.RED, command="c"),
            ("a.py",), wt,
        )

        assert result.status is fix.suite.SuiteStatus.RED
        assert result.rewrote == ("a.py",)

    def test_empty_paths_leave_green_alone(self, tmp_path, capsys):
        wt = _repo(tmp_path, {"a.py": "orig\n"})

        result = fix.suite.with_rewrites(
            fix.suite.SuiteResult(status=fix.suite.SuiteStatus.GREEN),
            (), wt,
        )

        assert result.status is fix.suite.SuiteStatus.GREEN
        assert result.rewrote == ()
        assert REWRITE_WARN not in capsys.readouterr().err


class TestReviewPassTally:
    @patch("git.push.push", return_value=_PUSHED)
    def test_the_commit_tally_names_the_restored_rewrite(
        self, _push, tmp_path, capsys,
    ):
        wt = _repo(tmp_path, {"src.py": "original\n"})
        src = wt / "src.py"
        clobber = _script(tmp_path, f"printf '%s\\n' formatted > {src}")
        (wt / ".workbench.yml").write_text(
            "fix:\n"
            f"  verify_command: {json.dumps(clobber)}\n"
            "  verify_timeout: 30\n"
        )
        rec = record(finding("M1", "Missing helper", path="src.py", checkbox=True))
        job = _make_job(wt, tmp_path, rec=rec, files=["src.py"])

        def agent_run():
            src.write_text("fixed\n")

        _run(job, {"M1": "fixed"}, work=agent_run)

        assert src.read_text() == "fixed\n"
        assert _head_blob(wt, "src.py") == "fixed\n"
        assert "src.py" in _committed_paths(wt)
        message = _message(wt)
        assert REWRITE_TALLY in message
        assert f"{CHECKS_TRAILER}: partial" in message
        err = capsys.readouterr().err
        assert REWRITE_WARN in err
        assert "src.py" in err


class _RecordingTrail:
    """A trail that keeps what it was told, so a test can read it back."""

    def __init__(self):
        self.events = []

    def info(self, action, detail, data=None):
        self.events.append(("info", action, detail, data or {}))

    def warn(self, action, detail, data=None):
        self.events.append(("warn", action, detail, data or {}))

    def error(self, action, detail, data=None):
        self.events.append(("error", action, detail, data or {}))

    def failure(self, action, detail, **kwargs):
        self.events.append(("failure", action, detail, kwargs))
        return None
