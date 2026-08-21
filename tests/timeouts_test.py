"""Tests for the subprocess timeout table.

Not the numbers themselves — those are a judgement call and asserting them
here would only restate the module. What is worth holding is the shape the
rest of `ai/` depends on: that the tiers order the way their names claim, that
opting out is a value rather than an omission, and that a module every client
imports stays importable from anywhere.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import timeouts  # noqa: E402

TIERS = ["QUICK", "LOCAL", "NETWORK", "TRANSFER"]


def test_the_bounded_tiers_ascend():
    """The names promise an ordering; a table that violates it misleads."""
    values = [getattr(timeouts, name) for name in TIERS]
    assert values == sorted(values)
    assert len(set(values)) == len(values), "two tiers with one value is one tier"


def test_every_bounded_tier_is_a_positive_number_of_seconds():
    for name in TIERS:
        value = getattr(timeouts, name)
        assert isinstance(value, float), name
        assert value > 0, name


def test_unbounded_is_none_so_it_reaches_subprocess_unchanged():
    """subprocess.run reads None as "no bound", so the constant is not a sentinel."""
    assert timeouts.UNBOUNDED is None


def test_the_table_imports_nothing():
    """`proc` and `git_client` import this, so an import here risks a cycle.

    Read from the source rather than from the loaded module: by the time this
    test runs, `sys.modules` says nothing about who imported what.
    """
    tree = ast.parse((LIB_DIR / "timeouts.py").read_text())
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = [getattr(n, "module", None) or "" for n in imports]
    assert names == ["__future__"], f"timeouts.py imports {names}"
