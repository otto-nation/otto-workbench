"""`version_string` for the `ai/bin` scripts that have no `ai/lib` bootstrap.

The resolution itself is `core.version`'s, so a library caller and a shim
cannot report different versions of one installation. What stays here is the
`sys.path` bootstrap: several scripts under `ai/bin` put only their own
directory on the path and import this, and it is this module that puts
`ai/lib` there for them.

A script that already bootstraps `ai/lib` for its own imports does not need
this file at all — it imports `core.version` directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ai/lib, from WORKBENCH_AI_LIB_DIR's checkout when that is set — see ai/bin/_libdir.py
_AI_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if os.environ.get("WORKBENCH_AI_LIB_DIR"):
    sys.path.insert(0, str(_AI_LIB_DIR.parent / "bin"))
    from _libdir import pinned_ai_lib_dir
    del sys.path[0]
    _AI_LIB_DIR = pinned_ai_lib_dir()
sys.path.insert(0, str(_AI_LIB_DIR))

from core.version import version_string  # noqa: E402,F401
