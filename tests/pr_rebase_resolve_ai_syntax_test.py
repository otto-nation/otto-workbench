"""A marker-valid resolution that does not parse is not written or staged."""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import rebase.conflicts
import rebase.resolve_ai
import rebase.types

from pr_rebase_support import _TARGET, _unconfigured

_ORIGINAL = "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> abc\n"
_BROKEN_PY = "def f(\n"
_WRAPPED = (
    f"{rebase.conflicts.RESOLVE_BEGIN}\n{_BROKEN_PY}"
    f"{rebase.conflicts.RESOLVE_END}\n"
)


def _fake_run(handler=None):
    def fake_run(cmd, **kwargs):
        unconfigured = _unconfigured(cmd)
        if unconfigured[:3] == ["git", "cat-file", "blob"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="x = 1\n", stderr="",
            )
        if unconfigured[:2] == ["git", "diff"] and "REBASE_HEAD^" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="diff\n", stderr="",
            )
        if handler:
            result = handler(cmd, **kwargs)
            if result is not None:
                return result
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr="",
        )
    return fake_run


def test_a_marker_valid_unparseable_resolution_is_not_written():
    """The defect: structurally broken Python read as a clean merge.

    Markers parse; `ast.parse` does not. The file on disk must still hold
    the conflict, and git add must not have run.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_file = Path(tmpdir) / "mod.py"
        conflict_file.write_text(_ORIGINAL)
        adds = []

        def handler(cmd, **kwargs):
            if cmd[:3] == ["claude", "-p", "--bare"]:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=_WRAPPED, stderr="",
                )
            if cmd[:2] == ["git", "add"]:
                adds.append(cmd)
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="",
                )

        with mock.patch("subprocess.run", side_effect=_fake_run(handler)):
            result = rebase.resolve_ai.resolve_file_conflicts(
                ["mod.py"], tmpdir, "abc123", "feat: x",
                target_ref=_TARGET,
            )

        assert result.failed == ["mod.py"]
        assert result.files == []
        assert conflict_file.read_text() == _ORIGINAL
        assert adds == []


def test_judge_answer_names_the_syntax_failure():
    stages = rebase.conflicts.StageTexts(
        base="x = 1\n", target="x = 1\n", replayed="x = 2\n",
    )
    verdict = rebase.resolve_ai.judge_answer("mod.py", stages, _WRAPPED)

    assert not verdict.usable
    assert verdict.resolved is None
    assert verdict.losses == ()
    assert verdict.reason.startswith(rebase.types.ParseFailure.DOES_NOT_PARSE)
    assert "line" in verdict.reason


def test_a_file_that_never_parsed_is_not_refused():
    """A side that already fails the checker makes the check uninformative.

    A Helm template is `.yaml` and is not YAML. Refusing every resolution of
    one would stop the rebase over something the merge did not change.
    """
    template = "replicas: {{ .Values.replicas }\n"
    stages = rebase.conflicts.StageTexts(
        base=template, target=template, replayed=template,
    )
    wrapped = (
        f"{rebase.conflicts.RESOLVE_BEGIN}\n{template}"
        f"{rebase.conflicts.RESOLVE_END}\n"
    )
    assert not rebase.resolve_ai.core.syntax.check("t.yaml", template).ok

    verdict = rebase.resolve_ai.judge_answer("templates/t.yaml", stages, wrapped)

    assert verdict.usable
    assert verdict.resolved == template
