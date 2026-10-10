"""Tests for `cli.registry` — the one declaration of what a `pr` subcommand is.

Most of these ran through `pr_cli_test.py`'s script shim while the registry was
four tables inside `ai/bin/pr`. They are here now because the declaration is
importable, which is the point of the move: what a command needs, what backs
it, and whether it takes a target are all readable without executing anything.
"""

import importlib
import json
import subprocess
import sys
import ast
import textwrap
from pathlib import Path

import pytest

from conftest import command_spec, exec_fresh

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.registry  # noqa: E402
import cli.schema  # noqa: E402
from cli.needs import LOCAL, NONE, REMOTE, Need  # noqa: E402
from cli.registry import COMMANDS, CommandSpec, need_for, validate_needs  # noqa: E402
import core.timeouts  # noqa: E402
import core.tool_parser  # noqa: E402


# ── what the registry declares ────────────────────────────────────────────


def test_the_registry_names_every_subcommand():
    assert set(COMMANDS) == {"create", "status", "ci", "review", "comments",
                             "fix", "rebase", "push", "describe", "batch", "gc"}


def test_no_command_is_declared_twice():
    """The tuple is keyed afterwards, so a duplicate name would vanish silently."""
    assert len(cli.registry._SPECS) == len(COMMANDS)


def test_the_key_is_the_spec_s_own_name():
    for name, spec in COMMANDS.items():
        assert spec.name == name


def test_every_command_carries_a_help_line():
    for name, spec in COMMANDS.items():
        assert spec.help and isinstance(spec.help, str), name


def test_the_delegating_commands_name_their_script():
    """Which commands are backed by a script, pinned as literals.

    Read off the registry the spec would be asserting against, this would pass
    for any partition of the eleven — including one that quietly stopped
    delegating `comments` and ran it in-process.
    """
    backed = {name: spec.script for name, spec in COMMANDS.items() if spec.script}
    assert backed == {
        "ci": "ci-check",
        "review": "review",
        "comments": "review-threads",
        "rebase": "pr-rebase",
        "describe": "pr-describe",
    }


def test_the_internal_commands_name_no_script():
    for name in ("create", "status", "fix", "gc", "push"):
        assert COMMANDS[name].script is None, name


def test_a_script_is_a_name_and_not_a_path():
    """The caller joins it to its own BIN_DIR.

    Under WORKBENCH_AI_LIB_DIR `cli/` resolves inside the pinned checkout while
    the entry point does not, so a path built here would name a different
    tree's delegates than `ai/bin/pr` spawns.
    """
    for name, spec in COMMANDS.items():
        if spec.script:
            assert "/" not in spec.script, name


def test_only_create_takes_no_target():
    """`takes_target` replaced a hand-maintained set; the membership is the same."""
    assert {name for name, spec in COMMANDS.items() if not spec.takes_target} \
        == {"create", "batch"}


# ── handler paths ─────────────────────────────────────────────────────────
#
# Pinned as literals, not read off COMMANDS: a test that expected whatever
# the registry already says cannot fail. `validate-ai-layers` cannot see
# through these strings (they are resolved by importlib at dispatch), so the
# import-and-callable check below is what keeps them honest until commit 8's
# join check.
#
# `review` and `comments` name the delegates, not `ai/bin/pr`'s `cmd_review` /
# `cmd_comments`. Those two are the entry point's argv shaping — `--self`
# injection, mode routing — ahead of the callable named here.

_HANDLERS = {
    "create":   "cli.pr_create:cmd_create",
    "status":   "cli.pr_commands:cmd_status",
    "ci":       "cli.ci_check:main",
    "review":   "cli.review_entry:main",
    "comments": "cli.review_threads:main",
    "fix":      "cli.pr_commands:cmd_fix",
    "rebase":   "cli.pr_rebase:main",
    "push":     "cli.pr_push:main",
    "describe": "cli.pr_describe:main",
    "batch":    "cli.pr_batch:cmd_batch",
    "gc":       "cli.pr_commands:cmd_gc",
}


def test_every_command_declares_the_pinned_handler():
    assert {name: spec.handler for name, spec in COMMANDS.items()} == _HANDLERS


def test_every_handler_path_resolves_to_a_callable():
    """The strings are the contract; importing them is the only check they exist."""
    for name, path in _HANDLERS.items():
        module_name, attr = path.split(":", 1)
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr)), f"{name}: {path}"


# ── the registry is the only list of subcommands ──────────────────────────


def test_the_registry_is_the_only_list_of_subcommands():
    """Every subparser `pr` builds comes from a spec, and every spec builds one.

    The set assertion above hardcodes the eleven names and never looks at the
    parser, so a subcommand added directly to `_build_parser` would pass it.
    """
    import cli.pr
    assert set(core.tool_parser.subparsers(cli.pr._build_parser())) == set(COMMANDS)


# The order the eleven are declared in, spelled out rather than read off
# `_SPECS`. Reading it off the tuple makes every assertion below a tautology:
# the order tracks whatever the registry says, which is the one thing the
# reordering would change.
_DISPLAY_ORDER = ["create", "status", "ci", "review", "comments",
                  "fix", "rebase", "push", "describe", "batch", "gc"]


def test_the_declaration_order_is_the_display_order():
    """Reordering `_SPECS` is user-visible in three places at once.

    `pr --help`, the subparsers and the MCP `command` enum all read the
    registry in sequence, and nothing else pins the order. It is the lifecycle
    order a reader expects — create, then inspect, then act — not alphabetical
    and not arbitrary.
    """
    import cli.pr
    declared = _DISPLAY_ORDER
    assert [s.name for s in cli.registry._SPECS] == declared
    assert list(COMMANDS) == declared
    assert list(core.tool_parser.subparsers(cli.pr._build_parser())) == declared
    assert cli.schema.tool_schema()["input_schema"]["properties"]["command"]["enum"] \
        == declared
    usage = cli.pr._build_usage().splitlines()
    body = usage[usage.index("Commands:") + 1:]
    helped = [line.split()[0] for line in body[:len(declared)]]
    assert helped == declared


def test_the_usage_text_names_every_global_flag_the_parser_declares():
    """`pr --help` is rendered from `build_global_parser`, not a copy of it.

    The hand-written block omitted the `--worktree` alias; every option string
    the global parser declares must appear in the text.
    """
    usage = cli.pr._build_usage()
    declared = [s for a in cli.pr.build_global_parser()._actions for s in a.option_strings]
    assert "--worktree" in declared
    for flag in declared:
        assert flag in usage, f"{flag} is global but `pr --help` does not name it"


def test_the_global_flags_block_is_bare_flags_with_no_argparse_heading():
    """`_global_flags_block` leans on private argparse API (`_get_formatter`,
    `_actions`, a `start_section(None)` heading-less section); this is the case
    that fails first when an upgrade changes any of them.
    """
    block = cli.pr._global_flags_block()
    assert "usage:" not in block
    assert "options:" not in block and "optional arguments:" not in block
    assert block.lstrip().startswith("-")


# ── the per-subcommand schema ─────────────────────────────────────────────


# The three delegates whose parser is a `ToolParser` and therefore declares an
# output contract. Pinned as literals rather than derived from
# `PARSER_FACTORIES`: deriving the expectation from the thing under test is
# how a schema test passes while reporting nothing, and these three are the
# reason `subcommand_schema` exists.
_COMMANDS_WITH_AN_OUTPUT_CONTRACT = ("ci", "rebase", "describe")


@pytest.mark.parametrize("command", _COMMANDS_WITH_AN_OUTPUT_CONTRACT)
def test_a_delegate_reports_its_own_output_contract(command):
    """`pr ci` answers with CIDomain, which `pr --tool-schema` cannot carry.

    The union schema declares no `output_schema` at all: one subcommand
    prints a document and the rest print prose, so one declaration for every
    invocation made the MCP server reject the rest. This is the per-command
    answer, and it is what lets a skill cite `pr ci` rather than
    `ai/bin/ci-check`.
    """
    doc = cli.schema.subcommand_schema(command)

    assert doc is not None, f"{command} declares a ToolParser but reported nothing"
    assert doc["name"] == f"pr {command}", (
        "the document names the invocation a user types, not the backing script"
    )
    assert "output_schema" in doc
    assert doc["output_schema"]["type"] == "object"
    assert doc["output_schema"]["properties"], "an empty contract is not a contract"


def test_a_named_subcommand_answers_the_flag_for_itself(tmp_path):
    """`pr ci --tool-schema` is the string the skill docs tell a reader to run.

    Without this the narrower document is reachable by no invocation at all:
    the flag is read before dispatch, so `pr ci --tool-schema` used to return
    the union — a schema with no `output_schema`, for a command that has one.
    """
    out = subprocess.run(
        [str(BIN_DIR / "pr"), "ci", "--tool-schema"],
        capture_output=True, text=True, timeout=core.timeouts.QUICK, check=True,
    )
    doc = json.loads(out.stdout)
    assert doc["name"] == "pr ci"
    assert doc["output_schema"]["properties"], "the subcommand's contract is missing"


def test_the_bare_flag_still_answers_for_the_whole_command(tmp_path):
    """MCP discovery reads this one, and a subcommand must not shadow it."""
    out = subprocess.run(
        [str(BIN_DIR / "pr"), "--tool-schema"],
        capture_output=True, text=True, timeout=core.timeouts.QUICK, check=True,
    )
    doc = json.loads(out.stdout)
    assert doc["name"] == "pr"
    assert "output_schema" not in doc
    assert doc["input_schema"]["properties"]["command"]["enum"] == _DISPLAY_ORDER


def test_a_command_with_no_contract_falls_back_to_the_union():
    """`pr status --tool-schema` answers rather than failing.

    `status` reports no subcommand schema, and a consumer that asked for one
    is better served the union than an error: the flag is a discovery
    protocol, and refusing mid-handshake is the failure it exists to avoid.
    """
    out = subprocess.run(
        [str(BIN_DIR / "pr"), "status", "--tool-schema"],
        capture_output=True, text=True, timeout=core.timeouts.QUICK, check=True,
    )
    assert json.loads(out.stdout)["name"] == "pr"


def test_the_union_schema_still_declares_no_output_contract():
    """Adding the per-command answer must not put a false one on the union."""
    assert "output_schema" not in cli.schema.tool_schema()


def test_rebase_carries_the_exit_codes_that_are_not_failures():
    """A refusal and a conflict are answers, and a consumer must not read
    either as the command having broken."""
    assert cli.schema.subcommand_schema("rebase")["ok_exit_codes"] == [3, 4]


@pytest.mark.parametrize("command", ["status", "fix", "gc"])
def test_a_command_pr_runs_itself_reports_no_subcommand_schema(command):
    """No delegate parser, so nothing to report — the union already answered."""
    assert cli.schema.subcommand_schema(command) is None


@pytest.mark.parametrize("command", ["review", "comments"])
def test_a_delegate_without_a_declared_contract_reports_nothing(command):
    """Honest silence beats an advertised contract the command does not keep.

    Both have a delegate parser, but a plain `ArgumentParser`: they print
    prose, not a document. Reporting a schema for them would be the same
    overclaim the union schema avoids by declaring none.
    """
    assert cli.schema.subcommand_schema(command) is None


# ── declared dispatch needs ───────────────────────────────────────────────


def test_every_command_declares_a_need():
    """No command may be silent about depth, fetch, and lock.

    Read through `need_for` rather than off the spec, because a command whose
    axes vary by flag declares a callable: the invariant is that a Need can be
    obtained for any invocation, not that one is stored literally.
    """
    for name, spec in COMMANDS.items():
        assert isinstance(need_for(spec, []), Need), f"{name} declares no need"


def test_review_declares_a_need_per_invocation():
    """`review` declares its need per invocation, off its argv. A bare
    invocation is about to review a PR, so it wants the branch current and it
    needs `gh` to name the PR.

    `--self` keeps the fetch and the lock — its subject is still the branch's
    current state, and a `--fix` pass still commits to the worktree — but drops
    to LOCAL, because a self-review runs before a PR exists and has no PR for
    `gh` to name.
    """
    spec = COMMANDS["review"]
    assert callable(spec.need)

    plain = need_for(spec, [])
    assert plain == Need(REMOTE, update=True, lock=True)

    self_need = need_for(spec, ["--self"])
    assert self_need == Need(LOCAL, update=True, lock=True)
    assert (self_need.update, self_need.lock) == (plain.update, plain.lock)


def test_create_takes_the_run_lock_only_when_it_may_publish():
    """`--dry-run` pushes nothing and creates nothing, so it holds no lock: a
    preview must not contend with a real run on the same branch."""
    spec = COMMANDS["create"]
    assert need_for(spec, []) == Need(REMOTE, update=False, lock=True)
    assert need_for(spec, ["--title", "t", "--draft"]) == Need(REMOTE, update=False, lock=True)
    assert need_for(spec, ["--dry-run"]) == Need(REMOTE, update=False, lock=False)
    assert need_for(spec, ["--title", "x", "--dry-run"]) == Need(REMOTE, update=False, lock=False)


@pytest.mark.parametrize("mode", ["--post", "--repair", "--summary", "--recover"])
def test_a_mode_acting_on_an_existing_review_does_not_fetch(mode):
    """A mode flag's subject is a review already on disk, at the commit that
    review describes. Fast-forwarding under it leaves `--summary` and `--post`
    reporting a review of a commit the worktree no longer sits on, and pushes
    `--recover` off the SHA it then has to pin a worktree back to. The PR still
    has to be resolved and the lock still has to be held."""
    assert need_for(COMMANDS["review"], [mode]) == Need(REMOTE, update=False, lock=True)


def test_a_fix_run_that_may_publish_still_fetches():
    """It is about to review and push, so it wants the branch current — the
    no-fetch rule is for a mode reading a review already on disk."""
    assert need_for(COMMANDS["review"], ["--fix", "--post"]) \
        == Need(REMOTE, update=True, lock=True)


def test_review_list_resolves_nothing_and_takes_no_lock():
    """The listing answers from the reviews root, so it owes no target."""
    assert need_for(COMMANDS["review"], ["--list"]) \
        == Need(NONE, update=False, lock=False)


# ── the registry checks itself ────────────────────────────────────────────


def test_a_spec_cannot_be_built_without_a_need():
    """`need` has no default, so a command silent about the axes cannot exist.

    A default would answer for a command nobody thought about, and would do it
    before `validate_needs` ever ran — which is the whole failure the check
    replaced the two opt-out sets to prevent.
    """
    with pytest.raises(TypeError):
        CommandSpec(name="listing", help="list reviews")


def test_a_resolver_returning_a_non_need_is_rejected():
    """A callable declaration is checked by resolving it, not by trusting it."""
    with pytest.raises(RuntimeError, match="listing"):
        validate_needs({"listing": command_spec(name="listing",
                                                need=lambda argv: True)})


def test_a_need_of_the_wrong_shape_is_rejected():
    """A spec carrying anything but a Need is undeclared too — the axes have to
    be readable off the declaration, not guessed from a truthy."""
    with pytest.raises(RuntimeError, match="listing"):
        validate_needs({"listing": command_spec(name="listing", need=True)})


def test_the_real_registry_passes_its_own_check():
    validate_needs(COMMANDS)


def test_the_check_runs_at_this_module_s_import(tmp_path):
    """A consumer that imports COMMANDS without running `pr` still gets a
    checked registry — which is what commit 6's MCP discovery will be.

    Driven by executing a copy of the module with an undeclared command spliced
    in, rather than by reading the source for the call: the question is whether
    a bad registry can survive an import, and only an import answers it.

    Through `exec_fresh`, which conftest owns — it is the "throwaway copy, for a
    test about import time" case by construction, and building the module here
    would be the second executor `validate-script-loading` forbids.
    """
    source = (LIB_DIR / "cli" / "registry.py").read_text()
    broken = source.replace(
        "COMMANDS: dict[str, CommandSpec] = {s.name: s for s in _SPECS}",
        "COMMANDS: dict[str, CommandSpec] = dict(\n"
        "    {s.name: s for s in _SPECS},\n"
        "    listing=CommandSpec('listing', 'list reviews', need=None),\n"
        ")",
    )
    assert broken != source, "the registry no longer keys _SPECS as expected"

    copy = tmp_path / "registry_probe.py"
    copy.write_text(broken)
    with pytest.raises(RuntimeError, match="listing"):
        exec_fresh("registry_probe", copy)


# ── the entry point stays cheap ───────────────────────────────────────────


def test_pr_help_imports_no_delegate():
    """`pr --help` dispatches nothing, so it must import no delegate.

    Written against `cli.*` rather than `review.pipeline`, which §2.3 named:
    `review.pipeline` is reachable only from `cli.review_orchestrate`, which
    `pr` never dispatches to, so a test asserting its absence is green on a
    registry that eagerly imports all five delegates — the failure it exists to
    catch.
    """
    probe = textwrap.dedent(
        """
        import contextlib, io, runpy, sys
        sys.argv = ["pr", "--help"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                runpy.run_path(sys.argv[0], run_name="__main__")
            except SystemExit:
                pass
        sys.stdout.write(",".join(sorted(
            m for m in sys.modules if m.startswith("cli."))))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", probe.replace('sys.argv[0]', repr(str(BIN_DIR / "pr")))],
        capture_output=True, text=True, timeout=core.timeouts.QUICK, check=True,
    )
    loaded = {m for m in out.stdout.strip().split(",") if m}
    assert loaded, "the probe loaded no cli module at all — it did not run `pr`"
    # `cli.pr` is the entry point the shim imports; `cli.pr_commands` is the
    # four internal handlers, `cli.dispatch` the seam that calls one, and
    # `cli.schema` the document `--tool-schema` serves. None is a delegate.
    # A delegate showing up here (`cli.ci_check`, `cli.review_entry`, …) is
    # the regression: those are what `handler` keeps as a string so that
    # dispatch, not import, pays for them.
    assert loaded <= {"cli.pr", "cli.needs", "cli.registry", "cli.review_modes",
                      "cli.pr_commands", "cli.pr_batch", "cli.dispatch",
                      "cli.schema"}, loaded


# ── process-level state belongs to the process ────────────────────────────


def test_no_cli_module_installs_a_signal_handler():
    """A handler installed mid-run replaces the caller's without restoring it.

    `signal.signal` neither chains nor stacks, so whichever installer runs last
    owns SIGINT for the life of the process. Under in-process dispatch the last
    one is a library reached partway through a command, which is why the only
    legitimate installer is the entry point that owns the process —
    `proc.install_stop_handler`, called from a `main` or a shim.
    """
    def installs_a_handler(node):
        return (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "signal"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "signal")

    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sorted((LIB_DIR / "cli").glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if installs_a_handler(node)
    ]
    assert offenders == [], offenders


def test_only_batch_parses_its_own_argv():
    assert {n for n, s in COMMANDS.items() if s.parses_own_argv} == {"batch"}
