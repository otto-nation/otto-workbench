"""Tests for the one resolver of a repo's agent instructions file.

Review preflight, `workbench-rules project`, the SessionStart hook and the
`ai sync` migration nudge all ask `core.project_context`; what each does with
the answer is tested with that caller, what the answer *is* is tested here.
"""

import re
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
REPO_ROOT = LIB_DIR.parent.parent
sys.path.insert(0, str(LIB_DIR))

import core.project_context as pc  # noqa: E402

CLI = LIB_DIR / "core" / "project_context.py"


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_agents_md_is_the_instructions_file(tmp_path):
    _write(tmp_path, "AGENTS.md")
    ctx = pc.resolve(tmp_path)
    assert (ctx.path, ctx.state) == ("AGENTS.md", pc.ContextState.AGENTS)
    assert not ctx.needs_migration


def test_a_repo_still_on_claude_md_resolves_to_it_and_needs_migration(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    ctx = pc.resolve(tmp_path)
    assert (ctx.path, ctx.state) == ("CLAUDE.md", pc.ContextState.LEGACY)
    assert ctx.needs_migration


def test_the_legacy_nested_claude_md_is_still_found(tmp_path):
    _write(tmp_path, ".claude/CLAUDE.md")
    assert pc.resolve(tmp_path).path == ".claude/CLAUDE.md"


def test_root_claude_md_wins_over_the_nested_one(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    _write(tmp_path, ".claude/CLAUDE.md")
    assert pc.resolve(tmp_path).path == "CLAUDE.md"


def test_both_files_resolve_to_agents_md_and_report_the_split(tmp_path):
    # Claude Code reads only CLAUDE.md here and Pi only AGENTS.md: the two
    # harnesses follow different instructions, which the hint has to say.
    _write(tmp_path, "AGENTS.md")
    _write(tmp_path, "CLAUDE.md")
    ctx = pc.resolve(tmp_path)
    assert (ctx.path, ctx.state, ctx.shadowed) == ("AGENTS.md", pc.ContextState.SPLIT, "CLAUDE.md")
    assert "Claude Code reads only CLAUDE.md" in pc.migration_hint(ctx)


def test_no_instructions_file_at_all(tmp_path):
    ctx = pc.resolve(tmp_path)
    assert (ctx.path, ctx.state, ctx.found) == ("", pc.ContextState.NONE, False)
    assert pc.migration_hint(ctx) == ""


def test_a_directory_named_like_the_file_is_not_the_file(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    _write(tmp_path, "CLAUDE.md")
    assert pc.resolve(tmp_path).path == "CLAUDE.md"


def test_the_legacy_hint_names_the_rename_and_never_performs_it(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    hint = pc.migration_hint(pc.resolve(tmp_path))
    assert "git mv CLAUDE.md AGENTS.md" in hint
    assert (tmp_path / "CLAUDE.md").is_file()
    assert not (tmp_path / "AGENTS.md").exists()


def test_cli_prints_path_then_state(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    out = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout
    assert out == "CLAUDE.md\nlegacy\n"


def test_cli_hint_is_empty_for_a_repo_on_agents_md(tmp_path):
    _write(tmp_path, "AGENTS.md")
    r = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path), "--hint"],
                       capture_output=True, text=True)
    assert (r.returncode, r.stdout) == (0, "")


def test_cli_refuses_a_root_that_is_not_a_directory(tmp_path):
    r = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path / "nope")],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "not a directory" in r.stderr


def test_bash_and_python_agree_on_the_minimum_claude_code_version():
    # lib/constants.sh carries the value for bash (the ai sync warning); this
    # module carries it for Python. One drifting from the other fails here.
    constants = (REPO_ROOT / "lib" / "constants.sh").read_text()
    m = re.search(r'^CLAUDE_CODE_MIN_VERSION="([^"]+)"$', constants, re.M)
    assert m, "lib/constants.sh defines no CLAUDE_CODE_MIN_VERSION"
    assert m.group(1) == pc.CLAUDE_CODE_MIN_VERSION
