"""`pr review`'s mutually-exclusive mode flags, and what each one does.

The table and the four handlers it names, in one importable module. They were
written inside `ai/bin/pr`, where `review`'s need — the one command declaration
an argv resolves rather than a constant — could not be stated anywhere a
library module could read it. `cli.registry` imports `need_for` from here for
exactly that reason.

Still spawning: `--post` and `--repair` run `review-post` and `review-rebuild`
as child processes, exactly as the binary did. #909 T7 commit 4 makes them
calls.

Each handler is *given* the directory to spawn from rather than deriving one,
matching `review.publish.post`. Under `WORKBENCH_AI_LIB_DIR` this module sits
in the pinned checkout while the entry point does not, so a path derived here
would spawn a different tree's delegates than `ai/bin/pr` does.
"""

# doc-group: cli

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from cli import needs
from cli.needs import NONE as _NONE
from cli.needs import Need, ReviewMode
from core import log
from core import timeouts
from pr import context as pr_context
from pr.review_sync import sync_review_domain
from review import listing as review_listing
from review.paths import find_review_file, review_file_path
from review.summary import ReviewSummaryReport, build_review_summary, json_summary


def parse_review_summary(output: str) -> dict | None:
    """Extract REVIEW_SUMMARY:{json} from claude-review output."""
    prefix = "REVIEW_SUMMARY:"
    lines = [l for l in output.splitlines() if l.startswith(prefix)]
    if not lines:
        return None
    try:
        return json.loads(lines[0][len(prefix):])
    except (json.JSONDecodeError, TypeError):
        return None


def update_review_state_from_output(output: str,
                                    ctx: pr_context.ResolvedContext) -> None:
    """Parse REVIEW_SUMMARY from output and update pr_state.

    review-rebuild does not emit the marker today, so this is a no-op on that
    path. The marker protocol itself should be deleted once the JSON-marker
    protocol it implements is retired.
    """
    summary_data = parse_review_summary(output)
    if not summary_data:
        return
    sync_review_domain(ctx, ReviewSummaryReport(
        repo=summary_data.get("repo", ""),
        pr_number=summary_data.get("pr_number"),
        head_sha=summary_data.get("head_sha"),
        head_ref=summary_data.get("head_ref"),
        base_ref=summary_data.get("base_ref"),
        review_type=summary_data.get("review_type"),
        review_file=summary_data.get("review_file", ""),
        review_content=summary_data.get("review_content"),
        findings=summary_data.get("findings") or {},
        verdict=summary_data.get("verdict", ""),
        status=summary_data.get("status", ""),
        failure_detail=summary_data.get("failure_detail", ""),
        recoverable=summary_data.get("recoverable"),
        cost_usd=summary_data.get("cost_usd", 0.0),
        input_tokens=summary_data.get("input_tokens", 0),
        output_tokens=summary_data.get("output_tokens", 0),
        cache_read_tokens=summary_data.get("cache_read_tokens", 0),
        cache_write_tokens=summary_data.get("cache_write_tokens", 0),
        duration_ms=summary_data.get("duration_ms", 0),
    ))


def post(argv: list[str], ctx: pr_context.ResolvedContext, *,
         bin_dir: Path, **_kw) -> int:
    """Post an existing review directly to GitHub via review-post."""
    pr_num = str(ctx.pr_number) if ctx.pr_number else None
    if not pr_num:
        log.error("Cannot determine PR number")
        return 1

    review = find_review_file(ctx.repo, pr_num)
    if not review:
        log.error(f"No review file found for {ctx.repo}#{pr_num}")
        log.dim("Run: pr review")
        return 1

    cmd = [str(bin_dir / "review-post"), "--pr", pr_num, "--review-file", str(review)]
    # What this run is publishing for. The review file is found by PR number
    # while the run lock keys on the branch, so naming the branch is what lets
    # review-post tell this run's review from one written by a run the lock
    # never made contend with it.
    if ctx.branch:
        cmd += ["--expect-ref", ctx.branch]
    if "--submit" in argv:
        cmd.append("--submit")
        argv = [a for a in argv if a != "--submit"]
    cmd += argv
    return subprocess.run(cmd, timeout=timeouts.UNBOUNDED).returncode


def repair(argv: list[str], ctx: pr_context.ResolvedContext, *,
           bin_dir: Path, **_kw) -> int:
    """Repair broken review artifacts via summary or rebuild."""
    pr_num = str(ctx.pr_number) if ctx.pr_number else None
    if not pr_num:
        log.error("Cannot determine PR number")
        return 1

    review = find_review_file(ctx.repo, pr_num)
    if review:
        sync_review_domain(ctx, build_review_summary(ctx.repo, pr_num, str(review)))
        return 0

    log.info("No review file found, trying rebuild...")
    review_dir = review_file_path(ctx.repo, pr_num).parent
    if not review_dir.is_dir():
        log.error(f"No review directory found: {review_dir}")
        return 1

    cmd = [str(bin_dir / "review-rebuild"), "--review-dir", str(review_dir), "--pr", pr_num]
    cmd += argv
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeouts.UNBOUNDED)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode == 0:
        update_review_state_from_output(result.stdout, ctx)
    return result.returncode


def summary(argv: list[str], ctx: pr_context.ResolvedContext, **_kw) -> int:
    """Output JSON summary of an existing review."""
    pr_num = str(ctx.pr_number) if ctx.pr_number else None
    if not pr_num:
        log.error("Cannot determine PR number")
        return 1

    review = find_review_file(ctx.repo, pr_num)
    if not review:
        log.error(f"No review file found for {ctx.repo}#{pr_num}")
        return 1

    print(json_summary(ctx.repo, pr_num, str(review)))
    return 0


def listing(argv: list[str], _ctx: pr_context.ResolvedContext, *,
            schema_version: int | None = None, **_kw) -> int:
    """List every review in the user's state root.

    The contract other repos read review state through — see
    `ai/lib/review/listing.py` for what a row carries and why it is a query
    rather than a path they derive.

    Two audiences, told apart by the handshake. With `--schema-version` this is
    a consumer, and the versioned document goes to stdout. Without it this is a
    person, and the table goes to stderr with stdout left empty — so a consumer
    that forgets the handshake gets a parse failure from an empty stream rather
    than a table it half-understands.

    The context is unresolved and the parameter is named for it: `--list`
    declares `ContextDepth.NONE`, so there is no repo, branch, or target here
    and nothing in this function may look for one.
    """
    if schema_version is None:
        print("\n".join(review_listing.render_table(review_listing.rows())),
              file=sys.stderr)
        return 0
    json.dump(review_listing.document(schema_version), sys.stdout, indent=2)
    print()
    return 0


# The one mode table. `cli.registry` resolves `review`'s need through it,
# `cmd_review` routes through it, and the two MCP schema helpers derive the
# versioned invocations from it — none of them restates the set of flags.
MODES: dict[str, ReviewMode] = {
    "--post":    ReviewMode(post),
    "--repair":  ReviewMode(repair),
    "--summary": ReviewMode(summary),
    "--recover": ReviewMode(),
    "--list":    ReviewMode(listing,
                            need=Need(_NONE, update=False, lock=False),
                            schema_versions=review_listing.SCHEMA_VERSIONS),
}


def flags_given(argv: Sequence[str]) -> list[str]:
    """The mode flags in *argv*, in table order."""
    return needs.review_modes(argv, MODES)


def need_for(argv: Sequence[str]) -> Need:
    """`review`'s need, which its mode flag decides."""
    return needs.review_need(argv, MODES)


def flags_prose() -> str:
    """The mode flags as an English list, for the exclusivity error."""
    flags = list(MODES)
    return f"{', '.join(flags[:-1])}, and {flags[-1]}"
