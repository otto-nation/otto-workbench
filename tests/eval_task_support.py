"""Helpers shared by the eval.task and eval.run suites.

A corpus-case builder and the `run_eval` argument namespace are used by more
than one suite, so each is defined once here rather than imported from another
test module.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _make_case(root: Path, name: str, task: str = "review") -> Path:
    """A corpus case is a directory with manifest.json and src/.

    Shape matches eval/corpus/<name>/.
    """
    case = root / name
    src = case / "src"
    src.mkdir(parents=True)
    (src / "file.py").write_text("x = 1\n")
    (case / "manifest.json").write_text(
        json.dumps({"name": name, "task": task}) + "\n",
    )
    return case


def _args(tmp_path, **overrides):
    """Namespace for run_eval."""
    ns = dict(
        corpus=str(tmp_path / "corpus"),
        entry="",
        task="",
        models="",
        effort="low",
        timeout=42,
        verbose=False,
        keep_temp=False,
        dry_run=False,
        runs=1,
        conditions="full",
        output="",
        save_baselines=False,
        compare=False,
        results_dir=str(tmp_path / "results"),
    )
    ns.update(overrides)
    return argparse.Namespace(**ns)
