"""Review orchestration for review.

Handles everything between "worktree is ready" and "the review directory holds
only its deliverable": PR metadata fetching, prompt template rendering, Claude
agent invocation, stream progress display, file grouping, review merging, the
static analysis section, and the optional fix pass.

Phase order is this script's alone, and so is the cleanup that order decides —
no phase cleans up after itself.

Called by the `review` entry point, which handles worktree lifecycle, archive
management, and interactive prompts.

Usage:
  review-orchestrate --pr NUMBER --review-file PATH \
    --repo-dir PATH [--target-dir PATH] [--session-log PATH] \
    [--prior-review PATH] [--issue URL] [--issue-context JSON]
"""

# doc-group: cli

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from core.trail import Trail, add_trail_args
import agent.diagnosis
import agent.invoke
import agent.phases
import agent.usage
import review.prompt
import review.prompt_prior
import review.prompt_sections
import review.registry
import agent.session
import review.pipeline
import review.finding_issue
import review.fix
import review.gc
import review.paths
import review.phases
import review.steps
import review.orchestrate
import review.outcome
import review.retry
import review.state
import review.types

import core.log
import core.module_proxy
import pr.state
import core.proc

import pr.target
from pr.context import detect_repo
from agent.registry import add_phase_skip_flags, phase_skips
from core.phases import Effort, Mode
from review.document import (
    SECTION_STATIC_ANALYSIS, SECTION_VERDICT, set_section,
)
from review.paths import (
    FILENAME_SESSION, read_review_meta, review_artifact_path, stamp_reviewed,
)
from core.tool_parser import enum_arg
# The function rather than the module: `git.client` binds `run` and `ok`, which
# `core.proc` and `core.log` also bind, and the proxy cannot patch a name that
# means two things. `abbrev` is pure formatting of a sha already in hand.
import git.numstat
from git.client import abbrev
# Same collision: `core.publishing.run` is not `core.proc.run`.
from core.publishing import run as publishing_run
from review.budget import UnknownModelWindow, prompt_budget_bytes
from review.collect import collect_preflight_data
from review.outcome import write_unchanged_review
from review.reply_threads import fetch_reply_threads
from review.types import DeltaAttribution, Pipeline, ReviewJob, ViewerRole
from agent.invoke import QuotaThrottle
from review.fix import run_fix_pass
from review.gc import cleaned_on_success
from agent.phases import ModelAlias, collect_phase_models, resolve_effort
from review.pipeline import (
    DEFAULT_MAX_COST, DEFAULT_MAX_PARALLEL, EFFORT_PRESETS, fetch_metadata,
    run_multi_phase, run_single_agent,
)
from review.static_analysis import (
    added_lines, format_static_analysis, run_static_analysis,
)
import agent.backend

# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "review-orchestrate"

_SUBMODULES = (
    review.orchestrate,
    agent.diagnosis, agent.invoke, agent.phases, agent.usage, review.prompt, review.prompt_prior, review.prompt_sections, review.registry, agent.session, review.pipeline, review.finding_issue, review.fix, review.gc,
    review.paths, review.phases, review.steps, review.outcome, review.retry, review.state, review.types,
    agent.backend, core.log, core.module_proxy, git.numstat, pr.state, pr.target, core.proc,
)

core.module_proxy.install(__name__, _SUBMODULES)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=SCRIPT, description="Review orchestration for review")
    parser.add_argument("--pr", default="", help="PR number (optional in self mode)")
    parser.add_argument("--review-file", required=True, help="Output review file path")
    parser.add_argument("--repo-dir", "--worktree",
                        required=True, help="Worktree path")
    parser.add_argument("--target-dir", default="",
                        help="Where the run's state lives (see pr_target)")
    parser.add_argument("--repo", default="", help="Repository name (owner/repo)")
    parser.add_argument("--session-log", help="Session log path")
    parser.add_argument("--prior-review", help="Path to prior review file for iterative context")
    parser.add_argument("--issue", default="", help="Issue URL")
    parser.add_argument("--issue-context", default="", help="Issue context JSON")
    parser.add_argument(
        "--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL,
        help="Max concurrent group reviews (default: from the machine slot pool, cap 4)",
    )
    add_phase_skip_flags(parser)
    parser.add_argument("--disprove", action="store_true", default=None,
                        help="Enable disprove-it gate (default: effort-based)")
    parser.add_argument("--generator-version", default="",
                        help="Version string to embed in review metadata")
    parser.add_argument("--mode", type=enum_arg(Mode), choices=list(Mode), default=Mode.PR,
                        help="Review mode: pr (default) or self")
    parser.add_argument("--base", default="",
                        help="Branch to measure the diff against, as a bare name "
                             "(resolved as origin/<name>). Default: the PR's base, "
                             "else the branch this one is stacked on, else the "
                             "repo's default branch")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                        help=f"Max total review cost in USD (default: {DEFAULT_MAX_COST})")
    parser.add_argument("--model", default="",
                        help="Override model for all agents (e.g. sonnet, opus)")
    parser.add_argument("--effort", type=enum_arg(Effort), choices=list(Effort), default=None,
                        help="Effort preset controlling thinking, budget, phase skipping "
                             "(default: review.effort in config.yml, else medium)")
    parser.add_argument("--max-groups", type=int, default=None,
                        help="Max file groups in multi-phase reviews (default: effort-based)")
    parser.add_argument("--fix", action="store_true",
                        help="Run fix pass after review to apply findings")
    parser.add_argument("--post", action="store_true",
                        help="Let the fix pass push its commit; without it the "
                             "push is drafted")
    parser.add_argument("--track", action="append", default=[], metavar="FINDING_ID",
                        help="File this open finding on a tracking issue "
                             "(repeatable). Deferral is a per-finding decision, "
                             "so nothing is filed unless told which findings")
    parser.add_argument("--track-all", action="store_true",
                        help="File every finding the fix pass left open. Only "
                             "for a set the user has actually reviewed")
    parser.add_argument("--generated", action="store_true",
                        help="Include tier3-generated files (skipped by default)")
    parser.add_argument("--recover-sha", default="",
                        help="Commit a --recover run must complete against "
                             "(pins metadata to the failed run's HEAD)")
    add_trail_args(parser)
    args = parser.parse_args(argv)

    # Before anything runs. `review` decides whether this run may publish
    # and forwards the answer here, because the fix pass lives in this process
    # and must see the same gate.
    with publishing_run(post=args.post):
        if args.mode == Mode.PR and not args.pr:
            core.log.error("--pr is required in pr mode")
            return 1

        repo = args.repo or detect_repo(cwd=args.repo_dir)

        session_log = args.session_log or review_artifact_path(args.review_file, FILENAME_SESSION)

        trail = Trail.start(
            script=SCRIPT,
            context={"repo": repo, "pr": args.pr, "mode": args.mode},
            debug=args.debug,
        )

        try:
            return review.orchestrate.run_orchestrate(trail, args, repo, session_log)
        except KeyboardInterrupt:
            return core.proc.INTERRUPT_RETURNCODE
        except Exception as exc:
            trail.error("unexpected_error", str(exc))
            raise
        finally:
            trail.finish()
