"""Tests for bin/local/validate-tool-schema."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-tool-schema"

sys.path.insert(0, str(REPO_ROOT / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

import server  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("validate_tool_schema", str(SCRIPT))
_spec = importlib.util.spec_from_loader("validate_tool_schema", _loader)
vts = importlib.util.module_from_spec(_spec)
sys.modules["validate_tool_schema"] = vts
_spec.loader.exec_module(vts)


def _write_script(root: Path, relpath: str, body: str) -> Path:
    """Write an executable script at *relpath* under *root*, creating its dir."""
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _answers(keys: str) -> str:
    """A python tool that answers --tool-schema with a document holding *keys*."""
    return f"""\
        #!/usr/bin/env python3
        import json, sys
        if "--tool-schema" in sys.argv:
            json.dump({{{keys}}}, sys.stdout)
            sys.exit(0)
    """


GOOD = _answers('"name": "good", "input_schema": {"type": "object"}')


def _reasons(root: Path) -> dict[str, str | None]:
    """{script name: failure reason or None} for every candidate under *root*."""
    return {r.script.name: r.reason for r in vts.check_root(str(root))}


# ── failure modes ────────────────────────────────────────────────────────


def test_a_tool_that_exits_nonzero_is_caught(tmp_path):
    _write_script(tmp_path, "bin/broken-tool",
                  '#!/bin/bash\n# answers --tool-schema\necho boom >&2\nexit 3\n')

    reason = _reasons(tmp_path)["broken-tool"]

    assert "exited 3" in reason
    assert "boom" in reason


def test_a_tool_emitting_malformed_json_is_caught(tmp_path):
    _write_script(tmp_path, "bin/garbled-tool",
                  '#!/bin/bash\n# answers --tool-schema\necho not json\n')

    assert "JSONDecodeError" in _reasons(tmp_path)["garbled-tool"]


def test_a_tool_omitting_name_is_caught(tmp_path):
    _write_script(tmp_path, "bin/nameless-tool",
                  _answers('"input_schema": {"type": "object"}'))

    assert _reasons(tmp_path)["nameless-tool"] == "schema is missing name"


def test_a_tool_omitting_input_schema_is_caught(tmp_path):
    _write_script(tmp_path, "bin/partial-tool", _answers('"name": "partial"'))

    assert _reasons(tmp_path)["partial-tool"] == "schema is missing input_schema"


def test_a_tool_that_outruns_the_probe_timeout_is_caught(tmp_path, monkeypatch):
    """The probe timeout is the server's, shortened here so the suite is not."""
    monkeypatch.setattr(server, "DISCOVERY_TIMEOUT", 0.2)
    _write_script(tmp_path, "bin/slow-tool",
                  '#!/bin/bash\n# answers --tool-schema\nsleep 5\n')

    assert "TimeoutExpired" in _reasons(tmp_path)["slow-tool"]


def test_every_broken_tool_is_reported_not_just_the_first(tmp_path):
    _write_script(tmp_path, "bin/broken-one",
                  '#!/bin/bash\n# answers --tool-schema\nexit 1\n')
    _write_script(tmp_path, "bin/broken-two",
                  '#!/bin/bash\n# answers --tool-schema\necho nope\n')

    assert [r.ok for r in vts.check_root(str(tmp_path))] == [False, False]


# ── what counts as a candidate ───────────────────────────────────────────


def test_a_working_tool_passes(tmp_path):
    _write_script(tmp_path, "bin/good-tool", GOOD)

    results = vts.check_root(str(tmp_path))

    assert [r.ok for r in results] == [True]
    assert results[0].schema["name"] == "good"


def test_a_script_with_no_marker_is_not_probed(tmp_path):
    """Most executables are not tools, and probing one would run it."""
    marker = tmp_path / "side-effect"
    _write_script(tmp_path, "bin/plain-script", f"#!/bin/bash\ntouch '{marker}'\n")

    assert vts.check_root(str(tmp_path)) == []
    assert not marker.exists()


def test_a_non_executable_file_is_not_probed(tmp_path):
    path = tmp_path / "bin" / "draft-tool"
    path.parent.mkdir(parents=True)
    path.write_text('#!/bin/bash\n# answers --tool-schema\nexit 1\n')

    assert vts.check_root(str(tmp_path)) == []


def test_an_underscore_prefixed_helper_is_not_probed(tmp_path):
    _write_script(tmp_path, "bin/_helper", '#!/bin/bash\n# answers --tool-schema\nexit 1\n')

    assert vts.check_root(str(tmp_path)) == []


def test_a_script_outside_a_bin_dir_is_not_probed(tmp_path):
    """Discovery scans component bin/ directories, so the check does too."""
    _write_script(tmp_path, "lib/broken-tool",
                  '#!/bin/bash\n# answers --tool-schema\nexit 1\n')

    assert vts.check_root(str(tmp_path)) == []


def test_candidates_are_found_at_every_component_level(tmp_path):
    """Root bin/, <component>/bin, and <component>/<sub>/bin — the server's glob."""
    _write_script(tmp_path, "bin/root-tool", GOOD)
    _write_script(tmp_path, "git/bin/component-tool", GOOD)
    _write_script(tmp_path, "terminals/ghostty/bin/nested-tool", GOOD)

    found = {r.script.name for r in vts.check_root(str(tmp_path))}

    assert found == {"root-tool", "component-tool", "nested-tool"}


# ── the rules come from the server ───────────────────────────────────────


def test_the_checks_are_the_servers_own():
    """Drift guard: a copy of any of these rules here could disagree at runtime."""
    assert vts.probe_tool is server.probe_tool
    assert vts.tool_candidates is server.tool_candidates
    assert vts.discover_tool_dirs is server.discover_tool_dirs


# ── the real repo ────────────────────────────────────────────────────────


def test_repo_is_clean():
    """Every marked script in the checkout answers the probe."""
    broken = {str(r.script): r.reason for r in vts.check_root(str(REPO_ROOT)) if not r.ok}
    assert broken == {}


def test_the_repos_real_tools_are_reached():
    """A check that finds nothing would also be green, so name what it must find."""
    found = {r.schema["name"] for r in vts.check_root(str(REPO_ROOT)) if r.ok}

    assert {"pr", "pr-rebase", "ci-check"} <= found


# ── CLI ──────────────────────────────────────────────────────────────────


def test_main_exits_1_and_names_the_reason(tmp_path, monkeypatch, capsys):
    _write_script(tmp_path, "bin/broken-tool",
                  '#!/bin/bash\n# answers --tool-schema\necho boom >&2\nexit 3\n')
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema", "--quiet"])

    with pytest.raises(SystemExit) as exc:
        vts.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "bin/broken-tool" in err
    assert "exited 3" in err
    assert "1 of 1 tools" in err


def test_main_exits_0_on_a_clean_tree(tmp_path, monkeypatch, capsys):
    _write_script(tmp_path, "bin/good-tool", GOOD)
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema"])

    vts.main()

    out = capsys.readouterr().out
    assert "bin/good-tool" in out
    assert "1 tools answer" in out


def test_quiet_suppresses_the_passing_lines(tmp_path, monkeypatch, capsys):
    _write_script(tmp_path, "bin/good-tool", GOOD)
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema", "--quiet"])

    vts.main()

    out = capsys.readouterr().out
    assert "bin/good-tool" not in out
    assert "1 tools answer" in out


def test_a_tree_with_no_candidates_passes(tmp_path, monkeypatch, capsys):
    """A fixture tree, or a checkout that ships no tools, is not a failure."""
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema"])

    vts.main()

    assert "no --tool-schema candidates found" in capsys.readouterr().out


def test_validator_root_defaults_to_the_checkout(monkeypatch):
    monkeypatch.delenv("VALIDATOR_ROOT", raising=False)

    assert vts.validator_root() == str(REPO_ROOT)


def test_validate_all_discovers_this_validator():
    """The entry point globs bin/local/validate-*, so no list needs editing."""
    listed = subprocess.run(
        [str(REPO_ROOT / "bin" / "local" / "validate-all"), "--list"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )

    assert str(SCRIPT) in listed.stdout.splitlines()
