"""Interrupted comments-pass leftovers are resumed when HEAD and blobs still match."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
import fix.resume  # noqa: E402
import git.client  # noqa: E402
from fix_containment_support import _repo  # noqa: E402
from fix_engine_support import StubAdapter, _answer  # noqa: E402
from review_fix_pass_support import _PUSHED  # noqa: E402


def test_a_matching_blob_at_the_same_head_is_dropped_from_dirty_before(tmp_path, capsys):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    artifacts = tmp_path / "art"
    head = git.client.head_sha(cwd=wt)
    dirty = {"a.py", "other.py"}
    trail = Mock()

    fix.resume.record(artifacts, wt, head, ["a.py"])
    out = fix.resume.adopt(artifacts, wt, dirty, head, trail)

    assert "a.py" not in out
    assert "other.py" in out
    assert not (artifacts / "interrupted-pass.json").exists()
    assert "Resuming 1 edit" in capsys.readouterr().err
    trail.info.assert_called()
    assert trail.info.call_args.args[0] == "fix_leftovers_resumed"


def test_a_changed_blob_stays_in_dirty_before(tmp_path):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    artifacts = tmp_path / "art"
    head = git.client.head_sha(cwd=wt)
    fix.resume.record(artifacts, wt, head, ["a.py"])
    (wt / "a.py").write_text("changed again\n")

    out = fix.resume.adopt(artifacts, wt, {"a.py"}, head, None)

    assert "a.py" in out
    assert not (artifacts / "interrupted-pass.json").exists()


def test_a_moved_head_keeps_the_dirt_and_clears_the_file(tmp_path):
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    artifacts = tmp_path / "art"
    head = git.client.head_sha(cwd=wt)
    fix.resume.record(artifacts, wt, head, ["a.py"])
    (wt / "b.py").write_text("other\n")
    git_out(wt, "add", "b.py")
    git_out(wt, "commit", "-qm", "move HEAD")

    out = fix.resume.adopt(
        artifacts, wt, {"a.py"}, git.client.head_sha(cwd=wt), None)

    assert "a.py" in out
    assert not (artifacts / "interrupted-pass.json").exists()


class _Resuming(StubAdapter):
    resumes_interrupted = True

    def landing(self, outcomes, changed):
        self.landing_scope = changed
        return fix.engine.LandSpec(message="fix: stub", paths=changed)


@patch("git.push.push", return_value=_PUSHED)
def test_resumed_edits_appear_in_the_landing_paths(_push, tmp_path):
    """The leftover is attributed to the pass only because adopt dropped it."""
    wt = _repo(tmp_path, {"a.py": "orig\n"})
    (wt / "a.py").write_text("edited\n")
    adapter = _Resuming(wt)
    head = git.client.head_sha(cwd=wt)
    fix.resume.record(adapter.artifacts, wt, head, ["a.py"])

    with patch.object(agent.invoke, "run_fix", side_effect=_answer(adapter)):
        fix.engine.run(adapter)

    assert adapter.landing_scope is not None
    assert "a.py" in adapter.landing_scope
