"""Tests for bin/local/validate-prose-refs."""

from pathlib import Path

import pytest

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-prose-refs"

vpr = load_script("validate_prose_refs", SCRIPT)


@pytest.fixture(scope="module")
def tools():
    return vpr.load_tools(REPO_ROOT)


@pytest.fixture(scope="module")
def names():
    return vpr.load_slash_names(REPO_ROOT)


def _reasons(tmp_path, text, tools, names):
    page = tmp_path / "page.md"
    page.write_text(text)
    return [v.reason for v in vpr.check_file(page, tools, names)]


def test_the_repo_prose_resolves():
    assert vpr.main(["--quiet"]) == 0


def test_a_deleted_skill_named_as_a_slash_command_fails(tmp_path, tools, names):
    reasons = _reasons(tmp_path, "then use `/pr-review` to post it\n", tools, names)
    assert reasons == ["`/pr-review`: no skill, hook command or harness built-in has that name"]


@pytest.mark.parametrize("ref", ["`/wiki compile`", "`/reuse ultra`", "`/model`", "`/tmp`"])
def test_skills_hook_commands_builtins_and_paths_resolve(tmp_path, tools, names, ref):
    assert _reasons(tmp_path, f"run {ref}\n", tools, names) == []


def test_an_unknown_flag_on_a_real_command_fails(tmp_path, tools, names):
    assert _reasons(tmp_path, "`pr review --foo`\n", tools, names) == [
        "`pr review`: no such flag --foo"]


def test_an_unknown_command_fails(tmp_path, tools, names):
    assert _reasons(tmp_path, "`pr bogus --x`\n", tools, names) == ["`pr bogus`: no such command"]


def test_pr_review_mode_flags_resolve_but_not_on_the_bare_review_binary(tmp_path, tools, names):
    assert _reasons(tmp_path, "`pr review --post --submit`\n", tools, names) == []
    assert _reasons(tmp_path, "`review --summary`\n", tools, names) == [
        "`review`: no such flag --summary"]


def test_nested_commands_aliases_and_global_flags_resolve(tmp_path, tools, names):
    text = ("`pr batch run --checkout DIR`\n"
            "`pr rebase --onto main`\n"
            "`pr create --draft --repo-dir /wt`\n")
    assert _reasons(tmp_path, text, tools, names) == []


def test_synopsis_notation_is_read_as_notation(tmp_path, tools, names):
    text = ("`pr [global flags] <command> [flags]`\n"
            "`pr batch resolve RUN_ID DECISION_ID --action A [--reason/--body-file/--commit]`\n")
    assert _reasons(tmp_path, text, tools, names) == []


def test_alternative_flags_are_each_checked(tmp_path, tools, names):
    assert _reasons(tmp_path, "`pr comments [--triage/--nope]`\n", tools, names) == [
        "`pr comments`: no such flag --nope"]


def test_fenced_lines_are_checked_and_their_comments_ignored(tmp_path, tools, names):
    text = "```bash\nwiki backup   # --list, --restore\npr review --nope\n```\n"
    assert _reasons(tmp_path, text, tools, names) == ["`pr review`: no such flag --nope"]


def test_a_longer_fence_is_not_closed_by_a_shorter_one_inside_it(tmp_path, tools, names):
    text = "````md\n```\npr review --nope\n```\n````\n"
    assert _reasons(tmp_path, text, tools, names) == ["`pr review`: no such flag --nope"]


def test_a_tilde_fence_is_not_closed_by_a_backtick_fence_inside_it(tmp_path, tools, names):
    text = "~~~md\n```\npr review --nope\n```\n~~~\nthen `pr review --foo`\n"
    assert _reasons(tmp_path, text, tools, names) == [
        "`pr review`: no such flag --nope", "`pr review`: no such flag --foo"]


def test_a_backtick_run_with_a_backtick_in_its_info_string_is_not_a_fence(tmp_path, tools, names):
    text = "```foo``` bar\nthen `pr review --nope`\n"
    assert _reasons(tmp_path, text, tools, names) == ["`pr review`: no such flag --nope"]


def test_framework_flags_are_read_from_the_parsers():
    assert {"-h", "--help", "--tool-schema", "--debug"} <= vpr.FRAMEWORK_FLAGS


def test_unregistered_commands_and_arguments_are_not_invocations(tmp_path, tools, names):
    text = "`git push --force` and `pr ENG-123` and `review`\n"
    assert _reasons(tmp_path, text, tools, names) == []


def test_discover_skips_generated_and_composed_pages(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("a.md", "a.src.md", "b.md", "rules.generated.md"):
        (docs / name).write_text("x\n")
    found = {p.name for p in vpr.discover(tmp_path)}
    assert found == {"a.src.md", "b.md"}
