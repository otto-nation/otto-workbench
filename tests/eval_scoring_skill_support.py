"""Helpers shared by the eval.scoring_skill suites.

The shim runner, the on-disk case builder, the artifacts builder and the corpus
root are each used by more than one of them, so each is defined once here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from eval.task import RunArtifacts


def _run(bin_dir, name, *args):
    """Invoke a generated shim the way the session would — by name, off PATH."""
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        [name, *args], capture_output=True, text=True, env=env)


def _artifacts(matches, violations):
    return RunArtifacts(data={"matches": matches, "violations": violations})


def _skill_case(tmp_path, **manifest_fields):
    """A minimal on-disk case: a one-file src/ tree plus a manifest.json.

    create_temp_repo commits src/'s contents as "add buggy code"; an empty
    tree leaves nothing to commit and git exits non-zero.
    """
    case_dir = tmp_path / "case"
    (case_dir / "src").mkdir(parents=True)
    (case_dir / "src" / "placeholder.txt").write_text("fixture\n")
    (case_dir / "manifest.json").write_text(json.dumps(manifest_fields))
    return case_dir


CORPUS = REPO_ROOT / "eval" / "corpus"
