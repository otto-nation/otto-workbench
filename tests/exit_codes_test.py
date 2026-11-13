"""The exit codes `pr` commands share have one owner: `core.exit_codes`."""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.exit_codes import ExitCode  # noqa: E402

import batch.report  # noqa: E402
import cli.boundary  # noqa: E402
import cli.pr_commands  # noqa: E402
import cli.schema  # noqa: E402
import core.run_lock  # noqa: E402
import core.tool_parser  # noqa: E402
import pr.supersession  # noqa: E402
import rebase.types  # noqa: E402

# Exit constants that are not part of the `pr` command surface, with why.
EXEMPT = {
    "ai/lib/cli/review_positions.py": "validate-review-positions protocol, not a pr command",
    "ai/lib/eval/rules_canary.py": "rules-canary protocol",
    "ai/lib/eval/scoring_skill.py": "generated scorer script protocol",
    "ai/lib/fix/suite.py": "bin/local/run-tests baseline protocol",
    "ai/lib/rebase/replay_audit.py": "git hook protocol (prepare-commit-msg)",
    "ai/lib/core/tree_lock_cli.py": "with-tree-lock protocol",
}


def test_the_codes_and_their_values():
    assert [(c.name, int(c)) for c in ExitCode] == [
        ("OK", 0), ("FAILURE", 1), ("USAGE", 2), ("CONFLICTS", 3),
        ("REFUSED", 4), ("UNAVAILABLE", 69), ("TEMPFAIL", 75), ("CANCELLED", 130),
    ]


def test_every_code_has_a_summary():
    assert all(c.summary for c in ExitCode)


def test_the_scattered_names_are_the_registry_members():
    assert core.tool_parser.EXIT_USAGE is ExitCode.USAGE
    assert cli.schema.EXIT_USAGE is ExitCode.USAGE
    assert core.run_lock.EXIT_BUSY is ExitCode.TEMPFAIL
    assert cli.pr_commands.EXIT_BUDGET_EXHAUSTED is ExitCode.TEMPFAIL
    assert cli.boundary.OWNER_BOUNDARY_EXIT is ExitCode.FAILURE
    assert pr.supersession.EXIT_SUPERSEDED is ExitCode.REFUSED
    assert rebase.types.REFUSAL_EXIT is ExitCode.REFUSED
    assert rebase.types.CONFLICTS_EXIT is ExitCode.CONFLICTS
    assert batch.report.EXIT_OK is ExitCode.OK
    assert batch.report.EXIT_FAILED is ExitCode.FAILURE


def _int_exit_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if not (name.startswith("EXIT_") or name.endswith("_EXIT")):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
            found.append(f"{name} = {node.value.value}")
    return found


def test_no_module_spells_a_pr_exit_code_as_a_literal():
    """A new `FOO_EXIT = 4` is a second owner of 4; alias the registry instead."""
    offenders = []
    for path in sorted(LIB_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == "ai/lib/core/exit_codes.py" or rel in EXEMPT:
            continue
        offenders += [f"{rel}: {c}" for c in _int_exit_constants(path)]
    assert offenders == []
