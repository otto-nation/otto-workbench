"""Running review-orchestrate and reporting what it did.

The argv both review flows build, the spawn, and the two guards that decide a
run failed. One module because the flows differ in how they *reach* this point
— which worktree, which checks, which prompts — and not at all in what happens
once they are here.

It is also the whole of the process boundary. #909's tranche 4 replaces the
spawn with an in-process call, and when it does, this file is what it rewrites:
nothing above it names `review-orchestrate`, constructs argv, or knows that a
review is produced by a subprocess at all.

The boundary is not incidental. `--post` on that argv does not mean "publish
this review" — it tells the orchestrate process that its fix pass may push,
because `core.publishing`'s gate is process-wide and has no `disable()`. Today
a subprocess is what scopes it to one run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from core import log
from core import timeouts
from core.phases import Phase
from agent.registry import phase_skip_argv


@dataclass(frozen=True)
class OrchestrateRequest:
    """One review's invocation of the pipeline.

    Every field a flow varies, and nothing it does not. `bin_dir` and
    `generator_version` are passed in rather than derived: both resolve against
    ``ai/bin``, which a module under ``ai/lib`` must not reach — see
    ``retro/report.py`` for the same constraint stated at its other site.
    """

    repo: str
    pr_number: str
    review_file: Path
    wt_path: str
    target_dir: Path
    session_log: str
    bin_dir: Path
    generator_version: str
    mode: str = ""
    prior_review_path: str = ""
    issue_link: str = ""
    issue_context: str = ""
    max_parallel: int = 1
    max_cost: float | None = None
    model: str | None = None
    effort: str | None = None
    max_groups: int | None = None
    skip_phases: frozenset[Phase] = frozenset()
    disprove: bool | None = None
    generated: bool = False
    recover_sha: str = ""
    fix_pass: bool = False
    may_publish: bool = False


def build_argv(request: OrchestrateRequest) -> list[str]:
    """The argv for *request*.

    Flag order is part of the contract only insofar as the goldens in
    `review_flow_entry_test.py` assert it; review-orchestrate itself parses in
    any order. Kept stable so a diff to this function shows up as a diff to the
    golden rather than as a silently reordered command line.
    """
    args = [
        str(request.bin_dir / "review-orchestrate"),
        "--repo", request.repo,
        "--review-file", str(request.review_file),
        "--repo-dir", request.wt_path,
        "--target-dir", str(request.target_dir),
        "--session-log", request.session_log,
    ]
    if request.pr_number:
        args += ["--pr", request.pr_number]
    if request.mode:
        args += ["--mode", request.mode]
    if request.prior_review_path and Path(request.prior_review_path).is_file():
        args += ["--prior-review", request.prior_review_path]
    if request.issue_link:
        args += ["--issue", request.issue_link]
    if request.issue_context:
        args += ["--issue-context", request.issue_context]
    args += ["--max-parallel", str(request.max_parallel)]
    args += ["--generator-version", request.generator_version]
    if request.fix_pass:
        args.append("--fix")
    # The publishing gate is process-wide and the fix pass runs in there, so the
    # only way it learns this run may publish is to be told on its own argv.
    if request.may_publish:
        args.append("--post")
    if request.max_cost:
        args += ["--max-cost", str(request.max_cost)]
    if request.model:
        args += ["--model", request.model]
    # Forwarded only when asked for: an absent flag lets review-orchestrate
    # fall through to review.effort in config.yml.
    if request.effort:
        args += ["--effort", request.effort]
    if request.max_groups is not None:
        args += ["--max-groups", str(request.max_groups)]
    args += phase_skip_argv(request.skip_phases)
    if request.disprove is True:
        args.append("--disprove")
    if request.generated:
        args.append("--generated")
    if request.recover_sha:
        args += ["--recover-sha", request.recover_sha]
    return args


def fail(message: str, session_log: str) -> None:
    """Report a failed orchestration and exit.

    Exits rather than raising because the session log is the only thing worth
    saying at this point and every caller would re-raise to the same place.
    `review.gc` also reads `SystemExit` as "this run did not finish", which is
    what keeps a failed run's artifacts on disk for `pr review --recover`.
    """
    log.error(message)
    if Path(session_log).is_file():
        log.dim(f"Session log: {session_log}")
    sys.exit(1)


def run(request: OrchestrateRequest) -> int:
    """Run the pipeline for *request* and return its wall-clock milliseconds.

    Exits, via `fail`, when the pipeline reports failure or produces no review:
    both mean there is nothing downstream to display, summarise or record. The
    wall clock covers the spawn alone, so the figure in the summary is time the
    pipeline spent rather than time the operator spent answering prompts.
    """
    argv = build_argv(request)

    wall_start = time.monotonic()
    rc = subprocess.run(argv, timeout=timeouts.UNBOUNDED).returncode
    wall_ms = int((time.monotonic() - wall_start) * 1000)

    if rc != 0:
        fail("Review orchestration failed", request.session_log)
    if not request.review_file.is_file():
        fail("Review orchestration completed but produced no review file",
             request.session_log)

    return wall_ms
