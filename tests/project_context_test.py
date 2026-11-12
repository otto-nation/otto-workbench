"""Tests for the one resolver of a repo's agent instructions file.

Review preflight, `workbench-rules project`, the SessionStart hook and the
`ai sync` shadow report all ask `core.project_context`; what each does with
the answer is tested with that caller, what the answer *is* is tested here.
"""

import re
import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
REPO_ROOT = LIB_DIR.parent.parent
sys.path.insert(0, str(LIB_DIR))

import core.project_context  # noqa: E402

CLI = LIB_DIR / "core" / "project_context.py"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_agents_md_is_the_instructions_file(tmp_path):
    _write(tmp_path, "AGENTS.md")
    ctx = core.project_context.resolve(tmp_path)
    assert (ctx.path, ctx.state, ctx.shadows) == ("AGENTS.md", core.project_context.ContextState.AGENTS, ())
    assert core.project_context.migration_hint(ctx) == ""


def test_a_claude_md_alone_is_never_read_as_instructions(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    ctx = core.project_context.resolve(tmp_path)
    assert (ctx.path, ctx.state, ctx.found) == ("", core.project_context.ContextState.LEGACY, False)
    assert ctx.shadows == ("CLAUDE.md",)


def test_legacy_hint_with_two_shadows_says_what_to_do_with_the_rest(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    _write(tmp_path, ".claude/CLAUDE.md")
    hint = core.project_context.migration_hint(core.project_context.resolve(tmp_path))
    assert "fold the others into AGENTS.md, then delete them" in hint


def test_the_nested_claude_md_is_a_shadow_too(tmp_path):
    _write(tmp_path, ".claude/CLAUDE.md")
    ctx = core.project_context.resolve(tmp_path)
    assert ctx.shadows == (".claude/CLAUDE.md",)
    assert "git mv .claude/CLAUDE.md AGENTS.md" in core.project_context.migration_hint(ctx)


def test_both_files_resolve_to_agents_md_and_report_the_shadow(tmp_path):
    _write(tmp_path, "AGENTS.md")
    _write(tmp_path, "CLAUDE.md")
    ctx = core.project_context.resolve(tmp_path)
    assert (ctx.path, ctx.state, ctx.shadows) == (
        "AGENTS.md", core.project_context.ContextState.SPLIT, ("CLAUDE.md",))
    hint = core.project_context.migration_hint(ctx)
    assert "shadows AGENTS.md" in hint and "delete it" in hint


def test_a_tracked_nested_claude_md_shadows_the_root_agents_md(tmp_path):
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "AGENTS.md")
    _write(tmp_path, "pkg/sub/CLAUDE.md")
    _git(tmp_path, "add", "-A")
    ctx = core.project_context.resolve(tmp_path)
    assert (ctx.state, ctx.shadows) == (core.project_context.ContextState.SPLIT, ("pkg/sub/CLAUDE.md",))


def test_the_workbenchs_guidelines_source_is_not_named_claude_md():
    # ai/claude/guidelines.md is installed as the user-level ~/.claude/CLAUDE.md;
    # its source keeps a different name so the repo tracks no CLAUDE.md at all.
    assert (REPO_ROOT / "ai" / "claude" / "guidelines.md").is_file()
    assert not (REPO_ROOT / "ai" / "claude" / "CLAUDE.md").exists()


def test_no_instructions_file_at_all(tmp_path):
    ctx = core.project_context.resolve(tmp_path)
    assert (ctx.path, ctx.state, ctx.found) == ("", core.project_context.ContextState.NONE, False)
    assert core.project_context.migration_hint(ctx) == ""


def test_a_directory_named_like_the_file_is_not_the_file(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    ctx = core.project_context.resolve(tmp_path)
    assert (ctx.path, ctx.state) == ("", core.project_context.ContextState.NONE)


def test_the_legacy_hint_names_the_rename_and_never_performs_it(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    hint = core.project_context.migration_hint(core.project_context.resolve(tmp_path))
    assert "git mv CLAUDE.md AGENTS.md" in hint
    assert (tmp_path / "CLAUDE.md").is_file()
    assert not (tmp_path / "AGENTS.md").exists()


def test_cli_prints_path_then_state(tmp_path):
    _write(tmp_path, "CLAUDE.md")
    out = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout
    assert out == "\nlegacy\n"


def test_cli_shadows_lists_each_claude_md(tmp_path):
    _write(tmp_path, "AGENTS.md")
    _write(tmp_path, "CLAUDE.md")
    out = subprocess.run([sys.executable, str(CLI), "--root", str(tmp_path), "--shadows"],
                         capture_output=True, text=True, check=True).stdout
    assert out == "CLAUDE.md\n"


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
    assert m.group(1) == core.project_context.CLAUDE_CODE_MIN_VERSION


def test_hint_prefers_root_claude_md_and_pluralises():
    ctx = core.project_context.ProjectContext(
        "", core.project_context.ContextState.LEGACY,
        ("Apps/CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.md"))
    assert "git mv CLAUDE.md AGENTS.md" in core.project_context.migration_hint(ctx)
    split = core.project_context.ProjectContext(
        "AGENTS.md", core.project_context.ContextState.SPLIT, ("CLAUDE.md", "a/CLAUDE.md"))
    hint = core.project_context.migration_hint(split)
    assert "fold them" in hint and "delete them" in hint
