"""Tests for bin/local/validate-magic-values."""

import sys
from pathlib import Path

import pytest
from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-magic-values"

vm = load_script("validate_magic_values", SCRIPT)

EXITS = vm.resolve_owners(str(REPO_ROOT), vm.EXIT_OWNERS)
CAPS = vm.resolve_owners(str(REPO_ROOT), vm.CAP_OWNERS)


def _check(tmp_path, source):
    path = tmp_path / "sample.py"
    path.write_text(source)
    return vm.check_file(str(path), EXITS, CAPS)


# ── owners ───────────────────────────────────────────────────────────────


def test_the_watched_values_come_from_the_owning_modules():
    """The check must not carry its own copy of a number it polices."""
    import proc
    import trail
    assert EXITS[proc.INTERRUPT_RETURNCODE] == ["proc.INTERRUPT_RETURNCODE"]
    assert "trail.EXCERPT_LIMIT" in CAPS[trail.EXCERPT_LIMIT]


def test_a_renamed_owner_fails_the_check_itself():
    """A watched constant that is gone must not quietly stop being watched."""
    with pytest.raises(SystemExit) as exc:
        vm.resolve_owners(str(REPO_ROOT), (("proc", "GONE_RETURNCODE"),))
    assert "proc.GONE_RETURNCODE is gone" in str(exc.value)


# ── an abbreviated sha ───────────────────────────────────────────────────


def test_a_sliced_sha_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def line(commit):
    return commit["sha"][:7]
""")
    assert [(v.line, v.found) for v in violations] == [(3, "commit['sha'][:7]")]
    assert violations[0].suggestion == "git_client.abbrev(...)"


@pytest.mark.parametrize("expr", [
    "sha[:7]",
    "head_sha[:7]",
    "job.recorded_sha[:7]",
    'r.get("sha", "")[:7]',
])
def test_every_way_a_sha_is_spelled_is_covered(tmp_path, expr):
    assert [v.line for v in _check(tmp_path, f"x = {expr}\n")] == [1]


def test_another_length_is_flagged_too(tmp_path):
    """How many characters is the owner's decision, not the call site's."""
    assert [v.line for v in _check(tmp_path, "x = sha[:8]\n")] == [1]


def test_the_owner_itself_is_clean(tmp_path):
    assert _check(tmp_path, "x = sha[:_ABBREV]\n") == []


def test_a_digest_is_not_a_sha(tmp_path):
    """`sha256` names an algorithm; its output is nobody's abbreviation."""
    assert _check(tmp_path, "x = sha256(body)[:16]\n") == []


# ── a truncation cap ─────────────────────────────────────────────────────


def test_a_slice_at_an_owned_cap_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def record(out):
    return {"stderr": out[:500]}
""")
    assert [(v.line, v.found) for v in violations] == [(3, "out[:500]")]
    assert "trail.EXCERPT_LIMIT" in violations[0].suggestion


def test_a_tail_keeps_the_sign_it_was_written_with(tmp_path):
    violations = _check(tmp_path, "x = out[-200:]\n")
    assert violations[0].found == "out[-200:]"
    assert violations[0].suggestion.startswith("-")


def test_every_owner_of_a_shared_value_is_offered(tmp_path):
    """Which cap a site means is a question only its author can answer."""
    suggestion = _check(tmp_path, "x = body[:200]\n")[0].suggestion
    assert "proc.DETAIL_LIMIT" in suggestion
    assert "review_budget.MAX_REVIEW_BODY_LEN" in suggestion


def test_a_bound_no_owner_claims_is_clean(tmp_path):
    """This reports a value being respelled, not every number that could be named."""
    assert _check(tmp_path, "x = desc[:80]\n") == []


def test_a_structural_index_is_clean(tmp_path):
    assert _check(tmp_path, "head, tail = parts[:2], parts[2:]\n") == []


def test_a_named_cap_is_clean(tmp_path):
    assert _check(tmp_path, "x = out[:EXCERPT_LIMIT]\n") == []


# ── an exit code ─────────────────────────────────────────────────────────


def test_an_exit_call_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def handler():
    sys.exit(130)
""")
    assert [(v.line, v.found) for v in violations] == [(3, "130")]
    assert violations[0].suggestion == "proc.INTERRUPT_RETURNCODE"


def test_a_returncode_comparison_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def missing(r):
    return r.returncode == 127
""")
    assert [(v.line, v.found) for v in violations] == [(3, "127")]
    assert violations[0].suggestion == "proc.MISSING_RETURNCODE"


def test_an_exit_code_assignment_is_flagged(tmp_path):
    violations = _check(tmp_path, """
def timed_out():
    exit_code = 124
    return exit_code
""")
    assert [(v.line, v.found) for v in violations] == [(3, "124")]


def test_an_exit_code_recorded_on_the_trail_is_flagged(tmp_path):
    """A trail record keys the code by name rather than passing it as one."""
    violations = _check(tmp_path, 'trail.error("ci", data={"cmd": 1, "exit_code": 130})\n')
    assert [v.found for v in violations] == ["130"]


def test_an_exit_code_keyword_is_flagged(tmp_path):
    violations = _check(tmp_path, "r = CompletedProcess(args, returncode=127)\n")
    assert [v.found for v in violations] == ["127"]


def test_a_named_code_is_clean(tmp_path):
    assert _check(tmp_path, "sys.exit(proc.INTERRUPT_RETURNCODE)\n") == []


def test_the_same_number_elsewhere_is_clean(tmp_path):
    """124 is only an exit code where something exits with it or reads it back."""
    assert _check(tmp_path, "width = 124\nfor i in range(130):\n    pass\n") == []


def test_a_module_level_constant_is_where_a_number_belongs(tmp_path):
    assert _check(tmp_path, "INTERRUPT_RETURNCODE = 130\n") == []


def test_an_annotated_constant_is_a_constant_too(tmp_path):
    assert _check(tmp_path, "INTERRUPT_RETURNCODE: int = 130\n") == []


# ── reporting ────────────────────────────────────────────────────────────


def test_every_offending_site_is_reported_in_order(tmp_path):
    violations = _check(tmp_path, """
def a(commit, out, r):
    short = commit.sha[:7]
    excerpt = out[:500]
    if r.returncode == 127:
        sys.exit(130)
""")
    assert [v.line for v in violations] == [3, 4, 5, 6]


def test_an_unparseable_file_answers_neither_clean_nor_dirty(tmp_path, capsys):
    assert _check(tmp_path, "def f(sha:\n    return sha[:7]\n") is None
    assert "unparseable, not checked" in capsys.readouterr().err


# ── discovery and CLI ────────────────────────────────────────────────────


def test_discover_finds_extensionless_python_scripts():
    scripts = vm.discover_scripts(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "claude" / "bin" / "pr") in scripts
    assert str(REPO_ROOT / "ai" / "lib" / "proc.py") in scripts


def test_a_named_non_python_file_is_not_reported_unparseable(monkeypatch, capsys, tmp_path):
    """Naming a file the check never covered says nothing about it."""
    other = tmp_path / "hook.sh"
    other.write_text("#!/usr/bin/env bash\nexec gh pr view \"$@\"\n")
    monkeypatch.setattr(sys, "argv", ["validate-magic-values", str(other)])
    vm.main()
    captured = capsys.readouterr()
    assert "unparseable" not in captured.err
    assert "0 files checked" in captured.out


def test_repo_is_clean():
    """The rule this validator enforces holds across the tree it walks."""
    results = {
        path: vm.check_file(path, EXITS, CAPS)
        for path in vm.discover_scripts(str(REPO_ROOT))
    }
    assert {p: v for p, v in results.items() if v != []} == {}


def test_an_unparseable_file_fails_the_run(tmp_path, monkeypatch, capsys):
    """A file that went unchecked must not leave the run green."""
    bad = tmp_path / "broken.py"
    bad.write_text("def f(sha:\n    return sha[:7]\n")
    monkeypatch.setattr(sys, "argv", ["validate-magic-values", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vm.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "could not be parsed and went unchecked" in captured.err
    assert "✓" not in captured.out


def test_main_exits_1_on_a_violation(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("def f(commit):\n    return commit.sha[:7]\n")
    monkeypatch.setattr(sys, "argv", ["validate-magic-values", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        vm.main()
    assert exc.value.code == 1
    assert "a value written out instead of named" in capsys.readouterr().err
