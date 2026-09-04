"""Tests for bin/local/validate-tool-schema."""

from __future__ import annotations

import stat
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import load_script, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-tool-schema"

sys.path.insert(0, str(REPO_ROOT / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

import server  # noqa: E402
from config import tool_registry  # noqa: E402

vts = load_script("validate_tool_schema", SCRIPT)


def _write_script(root: Path, relpath: str, body: str) -> Path:
    """Write an executable script at *relpath* under *root*, creating its dir."""
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_registry(root: Path, source: str, *names: str,
                    visibility: str = "brief") -> Path:
    """A bindir registry under *source* naming each of *names*."""
    path = root / source / "registry.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'meta:\n  section: "Fixture"\n  validation: bindir\n  source: {source}\n\ntools:\n'
        + "".join(f'  - name: {name}\n    permission: false\n'
                  f'    visibility: {visibility}\n'
                  f'    description: "the {name} fixture"\n' for name in names))
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

# The bound a case runs under when every tool in it is meant to time out.
# Nothing there turns on a fixture being scheduled inside the bound — a script
# the kernel has not started yet and a script sleeping far past it both produce
# the timeout the case is about — so it can stay short enough to cost tenths.
# A case holding a tool that must *answer* takes the shipped bound instead: the
# two share one bound, and shortening it to hurry the sleeper along is how the
# answering tool starts timing out too.
TIMEOUT_BOUND = 0.2

# A tool that never answers. `exec` so the sleep takes over the pid the prober
# kills, rather than outliving it as an orphan for half a minute.
SLEEPS_FOREVER = '#!/bin/bash\n# answers --tool-schema\nexec sleep 30\n'


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
    monkeypatch.setattr(server, "DISCOVERY_TIMEOUT", TIMEOUT_BOUND)
    _write_script(tmp_path, "bin/slow-tool", SLEEPS_FOREVER)

    assert "did not answer within" in _reasons(tmp_path)["slow-tool"]


def test_a_timeout_is_not_reported_as_a_broken_tool(tmp_path):
    """A wedged probe and a wrong answer want different people to look.

    On a build runner a breach of a bound the script should not need is the
    runner being oversubscribed far more often than the script being wrong, and
    "cannot answer --tool-schema" sent readers after a script that was fine.

    The shipped bound, not TIMEOUT_BOUND: the broken tool has to get far enough
    to exit 3, and under a short one it times out like its neighbour — which is
    this very finding, arriving as a green test.
    """
    _write_script(tmp_path, "bin/slow-tool", SLEEPS_FOREVER)
    _write_script(tmp_path, "bin/broken-tool",
                  '#!/bin/bash\n# answers --tool-schema\nexit 3\n')

    outcomes = {r.script.name: r.failure for r in vts.check_root(str(tmp_path))}

    assert outcomes == {"slow-tool": server.ProbeFailure.TIMED_OUT,
                        "broken-tool": server.ProbeFailure.BROKEN}


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


# ── every marked script needs a registry entry ───────────────────────────


def test_a_tool_no_registry_names_is_caught(tmp_path):
    """The server offers what the registries document, so an absent entry hides it."""
    _write_script(tmp_path, "bin/stray-tool", GOOD)

    assert [p.name for p in vts.unregistered(str(tmp_path))] == ["stray-tool"]


def test_a_registered_tool_is_not_flagged(tmp_path):
    _write_script(tmp_path, "bin/good-tool", GOOD)
    _write_registry(tmp_path, "bin", "good-tool")

    assert vts.unregistered(str(tmp_path)) == []


def test_a_hidden_tool_is_registered_and_so_passes(tmp_path):
    """Visibility is a decision somebody made; only a missing entry is a mistake.

    `pr rebase` runs pr-rebase, which is registered hidden for that reason —
    failing the build on it would make the check unsatisfiable.
    """
    _write_script(tmp_path, "bin/inner-tool", GOOD)
    _write_registry(tmp_path, "bin", "inner-tool", visibility="hidden")

    assert vts.unregistered(str(tmp_path)) == []


def test_an_entry_in_another_components_registry_does_not_count(tmp_path):
    """Entries key on the script's own path, not on a name matching somewhere."""
    _write_script(tmp_path, "git/bin/good-tool", GOOD)
    _write_registry(tmp_path, "bin", "good-tool")

    assert [p.name for p in vts.unregistered(str(tmp_path))] == ["good-tool"]


def test_an_unmarked_script_needs_no_entry(tmp_path):
    """Most executables are not tools — the gate follows the marker, not the dir."""
    _write_script(tmp_path, "bin/plain-script", "#!/bin/bash\necho hello\n")

    assert vts.unregistered(str(tmp_path)) == []


# ── colliding names ──────────────────────────────────────────────────────


SHARES_A_NAME = _answers('"name": "shared", "input_schema": {"type": "object"}')


def _colliding_tree(root: Path) -> None:
    """Two registered scripts in two tool dirs, both answering to `shared`."""
    _write_script(root, "bin/alpha", SHARES_A_NAME)
    _write_registry(root, "bin", "alpha")
    _write_script(root, "sub/bin/beta", SHARES_A_NAME)
    _write_registry(root, "sub/bin", "beta")


def test_two_scripts_answering_to_one_name_are_a_collision(tmp_path):
    _colliding_tree(tmp_path)

    dupes = vts.duplicate_names(vts.check_root(str(tmp_path)))

    assert sorted(p.name for p in dupes["shared"]) == ["alpha", "beta"]


def test_one_script_per_name_is_not_a_collision(tmp_path):
    """A check that called everything a collision would also be green here."""
    _write_script(tmp_path, "bin/good-tool", GOOD)
    _write_registry(tmp_path, "bin", "good-tool")

    assert vts.duplicate_names(vts.check_root(str(tmp_path))) == {}


def test_a_script_that_never_answered_cannot_collide(tmp_path):
    """A broken script has no name to claim — it is already counted as broken."""
    _write_script(tmp_path, "bin/broken-tool",
                  '#!/bin/bash\n# answers --tool-schema\nexit 3\n')
    _write_registry(tmp_path, "bin", "broken-tool")

    assert vts.duplicate_names(vts.check_root(str(tmp_path))) == {}


def test_main_exits_1_and_names_both_colliding_scripts(tmp_path, monkeypatch, capsys):
    _colliding_tree(tmp_path)
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema", "--quiet"])

    with pytest.raises(SystemExit) as exc:
        vts.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "bin/alpha" in err
    assert "sub/bin/beta" in err
    assert "claimed by more than one script" in err


# ── the rules come from the server ───────────────────────────────────────


def test_the_checks_are_the_servers_own():
    """Drift guard: a copy of any of these rules here could disagree at runtime."""
    assert vts.probe_tools is server.probe_tools
    assert vts.tool_candidates is server.tool_candidates
    assert vts.discover_tool_dirs is server.discover_tool_dirs
    assert vts.load_registry_entries is tool_registry.load_registry_entries


# ── the real repo ────────────────────────────────────────────────────────


def test_repo_is_clean():
    """Every marked script in the checkout answers the probe."""
    broken = {str(r.script): r.reason for r in vts.check_root(str(REPO_ROOT)) if not r.ok}
    assert broken == {}


def test_the_repos_real_tools_are_reached():
    """A check that finds nothing would also be green, so name what it must find."""
    found = {r.schema["name"] for r in vts.check_root(str(REPO_ROOT)) if r.ok}

    assert {"pr", "pr-rebase", "ci-check"} <= found


def test_every_marked_script_in_the_repo_is_registered():
    assert vts.unregistered(str(REPO_ROOT)) == []


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


def test_main_blames_the_machine_not_the_tool_for_a_timeout(tmp_path, monkeypatch, capsys):
    """Still a failure — a tool nobody could verify may not reach a client.

    What changes is the diagnosis: the summary counts it apart from the broken
    ones and points at the runner's load rather than at the script.
    """
    monkeypatch.setattr(server, "DISCOVERY_TIMEOUT", TIMEOUT_BOUND)
    _write_script(tmp_path, "bin/slow-tool", SLEEPS_FOREVER)
    _write_registry(tmp_path, "bin", "slow-tool")
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema", "--quiet"])

    with pytest.raises(SystemExit) as exc:
        vts.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "1 of 1 tools did not answer --tool-schema in time" in err
    assert "look at the load on this runner" in err
    assert "cannot answer it" not in err


def test_main_exits_1_and_names_an_unregistered_tool(tmp_path, monkeypatch, capsys):
    _write_script(tmp_path, "bin/stray-tool", GOOD)
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema", "--quiet"])

    with pytest.raises(SystemExit) as exc:
        vts.main()

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "bin/stray-tool" in err
    assert "no registry entry" in err
    assert "meta.source" in err


def test_main_exits_0_on_a_clean_tree(tmp_path, monkeypatch, capsys):
    _write_script(tmp_path, "bin/good-tool", GOOD)
    _write_registry(tmp_path, "bin", "good-tool")
    monkeypatch.setenv("VALIDATOR_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["validate-tool-schema"])

    vts.main()

    out = capsys.readouterr().out
    assert "bin/good-tool" in out
    assert "1 tools answer" in out


def test_quiet_suppresses_the_passing_lines(tmp_path, monkeypatch, capsys):
    _write_script(tmp_path, "bin/good-tool", GOOD)
    _write_registry(tmp_path, "bin", "good-tool")
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
    listed = run_checked([str(REPO_ROOT / "bin" / "local" / "validate-all"), "--list"],
                         cwd=REPO_ROOT)

    assert str(SCRIPT) in listed.stdout.splitlines()
