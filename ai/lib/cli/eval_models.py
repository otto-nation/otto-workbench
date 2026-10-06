"""Evaluation runner for AI calls made through the workbench.

Runs each corpus case through the task its manifest declares — review today,
ci-fix next — and scores the result against that case's expectations.

Usage:
  eval-models --corpus eval/corpus/ --models sonnet,opus --effort medium
  eval-models --entry unchecked-error-go --runs 3
  eval-models --dry-run
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import core.workbench_paths
import eval.run
import eval.task


def build_parser() -> argparse.ArgumentParser:
    """The eval-models parser, for `main` and for rendering its usage and flag tables."""
    parser = argparse.ArgumentParser(
        description="Model evaluation runner for the review pipeline",
    )
    parser.add_argument(
        "--corpus", default="eval/corpus/",
        help="Path to corpus directory (default: eval/corpus/)",
    )
    parser.add_argument(
        "--models", default="",
        help="Comma-separated model names (default: the task's production "
             "phase model, via agent.phases.phase_model)",
    )
    parser.add_argument(
        "--effort", default="low",
        help="Effort level for review-orchestrate (default: low)",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="Number of runs per entry per model (default: 1)",
    )
    parser.add_argument(
        "--output", default="",
        help="Path to write results JSON",
    )
    parser.add_argument(
        "--entry", default="",
        help="Filter to a specific corpus entry by name",
    )
    parser.add_argument(
        "--task", default="",
        help="Filter to one task kind (review, ci-fix, skill)",
    )
    parser.add_argument(
        "--conditions", default="full",
        help="Comma-separated rule-prefix arms: full, trimmed",
    )
    parser.add_argument(
        "--timeout", type=int, default=eval.task.EVAL_CASE_BUDGET,
        help=f"Timeout in seconds per review run (default: {eval.task.EVAL_CASE_BUDGET})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate corpus and show what would run without invoking LLM",
    )
    parser.add_argument(
        "--keep-temp", action="store_true",
        help="Keep temp repos for debugging",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print review-orchestrate stdout/stderr",
    )
    parser.add_argument(
        "--save-baselines", action="store_true",
        help="Write per-model baseline files to eval/results/ "
             "(refuses when the high-water floors would drop)",
    )
    parser.add_argument(
        "--seed-floors", action="store_true",
        help="Reseed missing floors.json from this run's scores, not history",
    )
    parser.add_argument(
        "--accept-regression", action="append", default=[],
        metavar="SPEC",
        help="Allow a floor drop: entry/metric=value — reason (repeatable)",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Compare results against committed baselines",
    )
    parser.add_argument(
        "--results-dir", default="eval/results/",
        help="Directory for baseline result files (default: eval/results/)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checkout = core.workbench_paths.source_checkout()
    if checkout is None:
        # There is nothing to resolve against, and guessing the cwd would read
        # a different corpus depending on where the command was typed.
        relative = [
            (flag, value)
            for flag, value in (("--corpus", args.corpus), ("--results-dir", args.results_dir))
            if not Path(value).is_absolute()
        ]
        for flag, value in relative:
            print(
                f"error: {flag} {value} is relative and no "
                "workbench checkout holds this code; pass an absolute path",
                file=sys.stderr,
            )
        if relative:
            return 1
    _, exit_code = eval.run.run_eval(args, checkout)
    return exit_code
