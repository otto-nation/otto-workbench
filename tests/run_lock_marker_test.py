"""The run-lock marker names every lock the process tree holds, not only the innermost."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import core.run_lock  # noqa: E402
from core.run_lock import LOCK_ENV, LOCK_FILE, TARGET  # noqa: E402


@pytest.mark.parametrize("raw,values", [
    ("", []),
    ("/a", ["/a"]),
    (f"/a{os.pathsep}/b", ["/a", "/b"]),
    (f"{os.pathsep}/a{os.pathsep}", ["/a"]),
])
def test_the_marker_parses_empty_one_and_several(monkeypatch, raw, values):
    monkeypatch.setenv(LOCK_ENV, raw)
    assert core.run_lock._marker_values(LOCK_ENV) == values


def test_a_child_passes_through_any_target_its_parent_named(tmp_path, monkeypatch):
    other, target = tmp_path / "t-other", tmp_path / "t"
    monkeypatch.setenv(LOCK_ENV, os.pathsep.join([str(other), str(target)]))
    core.run_lock.claim_for_process(target, command="pr-rebase", started="t")
    assert not (target / LOCK_FILE).exists()


def test_a_stale_value_among_several_still_reads_as_ownership(tmp_path, monkeypatch):
    """The marker is hearsay: a value whose holder is gone still waves a child
    through. Only the registry proves ownership; the marker is trusted because
    it is the only thing that can speak for a parent."""
    target = tmp_path / "t"
    monkeypatch.setenv(LOCK_ENV, os.pathsep.join(["/gone", str(target)]))
    core.run_lock.claim_for_process(target, command="ci-check", started="t")
    assert not core.run_lock.is_held(target)


def test_the_marker_names_every_lock_this_process_holds(tmp_path):
    first, second = tmp_path / "t-a", tmp_path / "t-b"
    with core.run_lock.hold({TARGET}, target_dir=first, command="pr batch run R", started="t"), \
         core.run_lock.hold({TARGET}, target_dir=second, command="pr batch run R", started="t"):
        assert set(core.run_lock._marker_values(LOCK_ENV)) == {str(first), str(second)}
    assert LOCK_ENV not in os.environ


def test_the_marker_keeps_what_a_parent_exported_beside_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv(LOCK_ENV, "/inherited/target")
    target = tmp_path / "t"
    with core.run_lock.hold({TARGET}, target_dir=target, command="pr rebase", started="t"):
        assert core.run_lock._marker_values(LOCK_ENV) == ["/inherited/target", str(target)]
    assert os.environ[LOCK_ENV] == "/inherited/target"


def test_releasing_one_lock_leaves_the_others_in_the_marker(tmp_path):
    first, second = tmp_path / "t-a", tmp_path / "t-b"
    with core.run_lock.hold({TARGET}, target_dir=first, command="pr fix", started="t"):
        with core.run_lock.hold({TARGET}, target_dir=second, command="pr fix", started="t"):
            pass
        assert core.run_lock._marker_values(LOCK_ENV) == [str(first)]
