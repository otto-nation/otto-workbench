"""Tests for `cli.registry` — the one declaration of what a `pr` subcommand is.

Most of these ran through `pr_cli_test.py`'s script shim while the registry was
four tables inside `ai/bin/pr`. They are here now because the declaration is
importable, which is the point of the move: what a command needs, what backs
it, and whether it takes a target are all readable without executing anything.
"""

import importlib
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

from cli import registry  # noqa: E402
from cli.needs import LOCAL, NONE, REMOTE, Need  # noqa: E402
from cli.registry import COMMANDS, CommandSpec, need_for, validate_needs  # noqa: E402
from core import timeouts, tool_parser  # noqa: E402


# ── what the registry declares ────────────────────────────────────────────


def test_the_registry_names_every_subcommand():
    assert set(COMMANDS) == {"create", "status", "ci", "review", "comments",
                             "fix", "rebase", "describe", "gc"}


def test_no_command_is_declared_twice():
    """The tuple is keyed afterwards, so a duplicate name would vanish silently."""
    assert len(registry._SPECS) == len(COMMANDS)


def test_the_key_is_the_spec_s_own_name():
    for name, spec in COMMANDS.items():
        assert spec.name == name


def test_every_command_carries_a_help_line():
    for name, spec in COMMANDS.items():
        assert spec.help and isinstance(spec.help, str), name


def test_the_delegating_commands_name_their_script():
    """Which commands are backed by a script, pinned as literals.

    Read off the registry the spec would be asserting against, this would pass
    for any partition of the nine — including one that quietly stopped
    delegating `comments` and ran it in-process.
    """
    backed = {name: spec.script for name, spec in COMMANDS.items() if spec.script}
    assert backed == {
        "ci": "ci-check",
        "review": "claude-review",
        "comments": "review-threads",
        "rebase": "pr-rebase",
        "describe": "pr-describe",
    }


def test_the_internal_commands_name_no_script():
    for name in ("create", "status", "fix", "gc"):
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
        == {"create"}


# ── handler paths ─────────────────────────────────────────────────────────
#
# Pinned as literals, not read off COMMANDS: a test that expected whatever
# the registry already says cannot fail. `validate-ai-layers` cannot see
# through these strings (they are resolved by importlib at dispatch), so the
# import-and-callable check below is what keeps them honest until commit 8's
# join check.
#
# `review` and `comments` are None on purpose. Their wrappers still live in
# `ai/bin/pr` and call `_run_delegate`; pointing at `cli.claude_review:main`
# / `cli.review_threads:main` would name the wrong callable. Filling those
# two is T7 commit 4c.

_HANDLERS = {
    "create":   "cli.pr_commands:cmd_create",
    "status":   "cli.pr_commands:cmd_status",
    "ci":       "cli.ci_check:main",
    "review":   None,
    "comments": None,
    "fix":      "cli.pr_commands:cmd_fix",
    "rebase":   "cli.pr_rebase:main",
    "describe": "cli.pr_describe:main",
    "gc":       "cli.pr_commands:cmd_gc",
}


def test_every_command_declares_the_pinned_handler():
    assert {name: spec.handler for name, spec in COMMANDS.items()} == _HANDLERS


def test_every_handler_path_resolves_to_a_callable():
    """The strings are the contract; importing them is the only check they exist.

    Skips the two Nones — those wrappers are still binary-local.
    """
    for name, path in _HANDLERS.items():
        if path is None:
            continue
        module_name, attr = path.split(":", 1)
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr)), f"{name}: {path}"


# ── the registry is the only list of subcommands ──────────────────────────


def test_the_registry_is_the_only_list_of_subcommands():
    """Every subparser `pr` builds comes from a spec, and every spec builds one.

    The set assertion above hardcodes the nine names and never looks at the
    parser, so a subcommand added directly to `_build_parser` would pass it.
    """
    from conftest import load_script

    pr_cli = load_script("pr_cli", BIN_DIR / "pr")
    assert set(tool_parser.subparsers(pr_cli._build_parser())) == set(COMMANDS)


# The order the nine are declared in, spelled out rather than read off
# `_SPECS`. Reading it off the tuple makes every assertion below a tautology:
# the order tracks whatever the registry says, which is the one thing the
# reordering would change.
_DISPLAY_ORDER = ["create", "status", "ci", "review", "comments",
                  "fix", "rebase", "describe", "gc"]


def test_the_declaration_order_is_the_display_order():
    """Reordering `_SPECS` is user-visible in three places at once.

    `pr --help`, the subparsers and the MCP `command` enum all read the
    registry in sequence, and nothing else pins the order. It is the lifecycle
    order a reader expects — create, then inspect, then act — not alphabetical
    and not arbitrary.
    """
    from conftest import load_script

    pr_cli = load_script("pr_cli", BIN_DIR / "pr")
    declared = _DISPLAY_ORDER
    assert [s.name for s in registry._SPECS] == declared
    assert list(COMMANDS) == declared
    assert list(tool_parser.subparsers(pr_cli._build_parser())) == declared
    assert pr_cli._tool_schema()["input_schema"]["properties"]["command"]["enum"] \
        == declared
    usage = pr_cli._build_usage().splitlines()
    body = usage[usage.index("Commands:") + 1:]
    helped = [line.split()[0] for line in body[:len(declared)]]
    assert helped == declared


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
    """`review` is the one spec whose declaration its argv resolves. A bare
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
        capture_output=True, text=True, timeout=timeouts.QUICK, check=True,
    )
    loaded = {m for m in out.stdout.strip().split(",") if m}
    assert loaded, "the probe loaded no cli module at all — it did not run `pr`"
    # `cli.pr_commands` is the four internal handlers, imported by the binary
    # the same way `cli.review_modes` is — not a delegate. A delegate showing
    # up here (`cli.ci_check`, `cli.claude_review`, …) is the regression.
    assert loaded <= {"cli.needs", "cli.registry", "cli.review_modes",
                      "cli.pr_commands"}, loaded


# ── process-level state belongs to the process ────────────────────────────


def test_no_cli_module_installs_a_signal_handler():
    """A handler installed mid-run replaces the caller's without restoring it.

    `signal.signal` neither chains nor stacks, so whichever installer runs last
    owns SIGINT for the life of the process. Under in-process dispatch the last
    one is a library reached partway through a command, which is why the only
    legitimate installer is the entry point that owns the process —
    `proc.install_interrupt_handler`, called from a `main` or a shim.
    """
    offenders = []
    for path in sorted((LIB_DIR / "cli").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "signal"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "signal"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], offenders
