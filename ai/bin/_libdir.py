"""Where ``ai/lib`` is loaded from, and the one place WORKBENCH_AI_LIB_DIR is read.

Every entry point under ``ai/bin`` puts ``ai/lib`` on ``sys.path`` before it
imports anything, resolved from its own file. ``~/.local/bin/pr`` is a symlink
into ``main/ai/bin/pr`` and ``Path(__file__).resolve()`` follows it, so that
directory is always ``main``'s whatever branch the caller stands in — which
means a change to ``ai/lib`` cannot be exercised end to end from the branch that
makes it, the self-review pass that gates such a branch included.
WORKBENCH_AI_LIB_DIR names the checkout a run should load instead::

    WORKBENCH_AI_LIB_DIR=/path/to/worktree pr review --self --fix

This is the Python half of what WORKBENCH_LIB_DIR does for the shell half, and
is deliberately not that variable. ``lib/ai/core.sh`` derives WORKBENCH_ROOT
from WORKBENCH_LIB_DIR, so one name would move the shell libraries too for a
caller who only meant to move ``ai/lib``, and a value left behind in an
environment would start redirecting both halves at once. A run that wants both
sets both.

It pins a checkout root rather than ``ai/lib`` itself, because ``ai/lib`` is not
self-contained: ``config/workbench_config.py``, ``pr/push_intent.py``,
``git/topology.py`` and ``review/static_analysis.py`` each reach up to
``<root>/lib``, and ``agent/templates.py`` reads ``<root>/ai/lib/review-templates``.
Pinning the package directory alone would load a branch's modules over main's
``lib/``, which is a build neither tree has.

This module sits beside the entry points rather than in ``ai/lib`` because it
has to answer before ``ai/lib`` is importable. Its caller opens a ``sys.path``
window to reach it and closes the window again, so an unset pin and a valid pin
both end with exactly one entry added — an unset pin never loads this module at
all.

# ceiling-permanent: loaded out of the entry point's own tree, never the pinned
# one, so a change to this file is the one thing WORKBENCH_AI_LIB_DIR cannot
# exercise from its own branch. There is no upgrade path and the alternative is
# worse: the pin is what is being vouched for, so a tree trusted to supply its
# own validator is not being checked — and a pin that is not a checkout has no
# copy to supply, which turns the refusal back into the traceback it exists to
# replace. ``_lib-dir-guard`` accepts the same trade for the shell half, and
# ``tests/ai_lib_pin.bats`` drives this file against temporary trees instead.

Stdlib only, like the entry points' own headers: this runs before ``ai/lib``
exists on the path, so it has nothing else to reach for.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

PIN_VAR = "WORKBENCH_AI_LIB_DIR"

# Four paths that together witness a whole checkout, in the shape
# `_lib-dir-guard` (Taskfile.global.yml) uses for the shell half: three stand
# for a directory the pin must supply at all, and the fourth stands for a
# silence.
#
# `ai/lib/core/__init__.py` stands for the layered package tree. A file rather
# than the directory, because an empty `ai/lib` passes a directory test and then
# fails every import after it — and that `__init__.py` carries the layer
# declaration, so it witnesses a tree shaped the way the imports expect.
#
# `lib/git_remote.py` stands for `<root>/lib`, the sibling tree `ai/lib` reaches
# up into. It is imported unconditionally by `git/topology.py` and
# `pr/push_intent.py`, both of which load on nearly every `pr` invocation, so a
# root supplying only `ai/` is refused here rather than several frames into a
# push.
#
# `ai/lib/review-templates/self-review.md` stands for the template directory,
# which sits outside every package and so is witnessed by nothing above.
# `agent.templates.template_dir` resolves it, and a pin missing it fails inside
# `render` — after the worktree checkout and the diff. Late, which is what earns
# a path a place in this list.
#
# `lib/nesting/__init__.py` stands for the silence. `review/static_analysis.py`
# imports `nesting` under try/except and sets `_NESTING_AVAILABLE = False`, so a
# pin missing it drops the nesting sweep out of every review and reports nothing
# at all.
#
# `bin/resolve-branch` is deliberately absent: `git.topology.resolve_branch`
# already treats a missing resolver as "the hint is the branch name", which is
# the documented behaviour and the common case. So are `lib/git_layout.py` and
# `lib/config_cli.py` — `git_remote.py` already stands for that directory, and
# this is a witness set rather than the closure of what an entry point reaches.
WITNESSES = (
    "ai/lib/core/__init__.py",
    "lib/git_remote.py",
    "ai/lib/review-templates/self-review.md",
    "lib/nesting/__init__.py",
)

# 2 rather than the shell guard's 1. These are CLIs with published exit codes —
# `wiki` documents 1 as "a finding was reported", `pr` exits 1 on a busy lock —
# so a refusal that never ran must not be spelled the same as a run that did.
# 2 is what this surface already uses for "you invoked me wrong".
REFUSAL_EXIT = 2


def _refuse(message: str) -> NoReturn:
    """Print the refusal and exit.

    ``SystemExit`` rather than an exception: this runs during an import, and a
    traceback is not a diagnosis of a misspelled environment variable.
    """
    print(message, file=sys.stderr)
    raise SystemExit(REFUSAL_EXIT)


def pinned_ai_lib_dir() -> Path:
    """The ``ai/lib`` of the checkout WORKBENCH_AI_LIB_DIR names, or exit 2.

    Reached only when the variable holds a non-empty value, so an unset pin
    never calls this and stats nothing — the default path stays the single
    insert it always was. An empty value reads as unset, since a variable
    exported to nothing is nobody asking for a pin.

    The value is checked as given rather than resolved, so the refusal names the
    path the caller typed. Unlike ``_lib-dir-guard`` there is no equality
    short-circuit: the default here is not this variable, so a pin naming the
    entry point's own root is checked like any other and passes.
    """
    pin = os.environ[PIN_VAR]
    root = Path(pin)
    if not root.is_absolute():
        _refuse(f"{PIN_VAR} must be an absolute path: {pin}")
    for witness in WITNESSES:
        if not (root / witness).is_file():
            _refuse(f"{PIN_VAR} does not contain {witness}: {pin}")
    return root / "ai" / "lib"
