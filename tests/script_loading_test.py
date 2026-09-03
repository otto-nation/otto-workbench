"""Tests for conftest's owner of executed scripts.

`bin/`, `bin/local/` and `ai/bin/` hold extensionless scripts, so a test
reaches one by executing the file rather than importing it. Executing it twice
builds two module objects for one script, and the two answer different
questions: `mock.patch("<name>.f")` rewrites whatever `sys.modules[<name>]`
points at, while a caller holding its own reference never consults that table.
The patch then lands on a copy nothing calls, and the assertion it was set up
for fails somewhere else entirely — but only when both callers are live in the
same process, which under `pytest -n` is a matter of how the files were
distributed across workers.

`load_script` is the single owner that keeps name, file and module object in
one-to-one correspondence. `exec_fresh` is its deliberate opposite, for a test
whose subject is what a module does while its body runs.
"""

import sys
import types
from unittest import mock

import pytest

from conftest import exec_fresh, load_script


def _script(tmp_path, body: str, name: str = "probe"):
    """An extensionless script, the shape the loaders exist for."""
    path = tmp_path / name
    path.write_text(body)
    return path


def test_one_script_yields_one_module_object(tmp_path):
    script = _script(tmp_path, "value = object()\n")

    first = load_script("one_object_probe", script)
    second = load_script("one_object_probe", script)

    assert first is second
    assert first.value is second.value


def test_the_module_is_the_one_the_name_resolves_to(tmp_path):
    """The property the whole design exists for: a string patch target and a
    held reference name the same module, so a mock is never applied to a copy
    the caller does not use."""
    script = _script(tmp_path, "def answer():\n    return 'real'\n")

    module = load_script("patch_target_probe", script)

    assert sys.modules["patch_target_probe"] is module
    with mock.patch("patch_target_probe.answer", return_value="patched"):
        assert module.answer() == "patched"


def test_a_second_name_for_one_file_is_refused(tmp_path):
    """Renaming the module is how the collision gets papered over; it produces
    the second copy rather than preventing it."""
    script = _script(tmp_path, "value = 1\n")
    load_script("first_name_probe", script)

    with pytest.raises(RuntimeError, match="already loaded as 'first_name_probe'"):
        load_script("second_name_probe", script)


def test_a_second_file_under_one_name_is_refused(tmp_path):
    script = _script(tmp_path, "value = 1\n", name="a")
    other = _script(tmp_path, "value = 2\n", name="b")
    load_script("taken_name_probe", script)

    with pytest.raises(RuntimeError, match="already belongs to"):
        load_script("taken_name_probe", other)


def test_a_failed_execution_leaves_the_name_free(tmp_path):
    """A half-executed module left registered imports cleanly and behaves like
    nothing in the file, which reads as a defect somewhere else."""
    broken = _script(tmp_path, "raise RuntimeError('boom')\n", name="broken")

    with pytest.raises(RuntimeError, match="boom"):
        load_script("half_executed_probe", broken)

    assert "half_executed_probe" not in sys.modules
    working = _script(tmp_path, "value = 1\n", name="working")
    assert load_script("half_executed_probe", working).value == 1


def test_argv_is_the_script_name_while_the_body_runs(tmp_path):
    """A script that reads arguments at import time would otherwise read
    pytest's, which names the test files being collected."""
    script = _script(tmp_path, "import sys\nARGV = list(sys.argv)\n", name="argv-probe")
    before = list(sys.argv)

    module = load_script("argv_probe", script)

    assert module.ARGV == ["argv-probe"]
    assert sys.argv == before


def test_exec_fresh_runs_the_body_again(tmp_path, monkeypatch):
    script = _script(tmp_path, "import os\nSEEN = os.environ.get('PROBE_VALUE')\n")

    monkeypatch.setenv("PROBE_VALUE", "first")
    first = exec_fresh("fresh_probe", script)
    monkeypatch.setenv("PROBE_VALUE", "second")
    second = exec_fresh("fresh_probe", script)

    assert (first.SEEN, second.SEEN) == ("first", "second")
    assert first is not second


def test_exec_fresh_does_not_claim_the_name(tmp_path):
    """Its copy is the caller's alone — the table keeps answering for whatever
    `load_script` owns."""
    script = _script(tmp_path, "value = 1\n")

    exec_fresh("unclaimed_probe", script)

    assert "unclaimed_probe" not in sys.modules


def test_exec_fresh_refuses_a_name_load_script_owns(tmp_path):
    """Borrowing an owned name is the one route back to the defect: for the
    length of the execution `sys.modules` would answer with the throwaway copy
    while `_SCRIPTS` still held the shared module, so a patch and a call would
    once again reach different objects."""
    script = _script(tmp_path, "value = 1\n", name="owned")
    owned = load_script("owned_name_probe", script)
    before = dict(sys.modules)

    with pytest.raises(RuntimeError, match="owned by load_script"):
        exec_fresh("owned_name_probe", script)

    assert sys.modules["owned_name_probe"] is owned
    assert sys.modules == before


def test_exec_fresh_puts_back_a_name_it_displaced(tmp_path, monkeypatch):
    """Only `load_script` owns a name here, but an ordinary import holds one
    too. Deleting the entry outright would drop that module out of the table
    and leave whoever imported it holding a reference nothing resolves to."""
    script = _script(tmp_path, "value = 1\n", name="displaced")
    placeholder = types.ModuleType("displaced_name_probe")
    monkeypatch.setitem(sys.modules, "displaced_name_probe", placeholder)

    fresh = exec_fresh("displaced_name_probe", script)

    assert fresh is not placeholder
    assert sys.modules["displaced_name_probe"] is placeholder


def test_a_failed_exec_fresh_puts_the_displaced_name_back(tmp_path, monkeypatch):
    """The restore is in a finally for the same reason `load_script` releases a
    half-executed name: a raising body must not leave the table rearranged."""
    broken = _script(tmp_path, "raise RuntimeError('boom')\n", name="broken")
    placeholder = types.ModuleType("failed_fresh_probe")
    monkeypatch.setitem(sys.modules, "failed_fresh_probe", placeholder)

    with pytest.raises(RuntimeError, match="boom"):
        exec_fresh("failed_fresh_probe", broken)

    assert sys.modules["failed_fresh_probe"] is placeholder


def test_the_ci_check_fixture_and_its_test_module_share_one_object(cc):
    """The regression this owner was written for: `ci_check_test` executed the
    script at import time and the `cc` fixture executed it again, so whichever
    ran second owned the name. `patch("ci_check.…")` then rewrote one copy
    while the call under test read the other's globals, and the pairing that
    exposed it depended on how xdist distributed the two files."""
    import ci_check_test

    assert cc is ci_check_test.ci_check
    assert sys.modules["ci_check"] is cc
