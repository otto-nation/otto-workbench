"""The config's three modules import in one direction only.

``workbench_config`` is what the config *is*, ``workbench_config_report`` is how
it is shown, ``workbench_config_write`` is how it is changed. Both of the
latter import the former and neither is imported back, which is what keeps the
module every Claude hook loads on every prompt from dragging the schema walk,
the docs renderer and the yq writer in behind it.

Nothing enforces that at runtime — Python would happily accept a cycle here, and
the first import added in the wrong direction would cost a hook on every prompt
without failing anything. So it is asserted against the source text.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "ai" / "lib"

CORE = "config.workbench_config"
REPORT = "config.workbench_config_report"
WRITE = "config.workbench_config_write"


def _imported_modules(name: str) -> set[str]:
    """Every module a source file imports, at any nesting depth.

    Walks the whole tree rather than the top level, so a deferred import inside
    a function counts too — a cycle costs the same whichever line it is on.

    A sibling within the same package is imported by its full dotted path
    (``from config.workbench_config import X``); a cross-package consumer aliases
    the submodule back to its flat name (``from config import workbench_config as
    wc``). Both are normalized to the dotted form so either style resolves.
    """
    tree = ast.parse((_LIB / Path(*name.split(".")).with_suffix(".py")).read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_config_imports_neither_of_the_modules_built_on_it():
    assert not _imported_modules(CORE) & {REPORT, WRITE}


@pytest.mark.parametrize("name", [REPORT, WRITE])
def test_each_module_built_on_the_config_imports_it(name):
    assert CORE in _imported_modules(name)


def test_showing_the_config_and_changing_it_do_not_import_each_other():
    assert REPORT not in _imported_modules(WRITE)
    assert WRITE not in _imported_modules(REPORT)
