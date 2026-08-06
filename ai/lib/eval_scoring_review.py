"""The review eval task: run review-orchestrate, score findings against a manifest.

Everything here is specific to reviewing code. The runner, the fixture repo, and
the aggregation over runs live in `eval_task` and `eval_scoring` and know nothing
about findings.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import ai_usage
from eval_scoring import ScoringResult
from eval_task import RunArtifacts, RunOptions, create_temp_repo, clean_env
from review_findings import Finding, parse_findings

_REVIEW_ORCHESTRATE = (
    Path(__file__).resolve().parent.parent / "claude" / "bin" / "review-orchestrate"
)


@dataclass(frozen=True)
class ExpectedFinding:
    severity: tuple[str, ...]
    path: str
    line_range: tuple[int, int]
    category: str
    description_contains: str = ""


@dataclass
class MatchResult:
    expected: ExpectedFinding
    matched: bool = False
    matched_finding_id: str = ""
    severity_exact: bool = False
    matched_severity: str = ""


def parse_manifest(
    manifest: dict,
) -> tuple[list[ExpectedFinding], int, list[str]]:
    expected = []
    for e in manifest.get("expected", []):
        lr = e.get("line_range", [0, 0])
        expected.append(ExpectedFinding(
            severity=tuple(e.get("severity", [])),
            path=e.get("path", ""),
            line_range=(lr[0], lr[1]),
            category=e.get("category", ""),
            description_contains=e.get("description_contains", ""),
        ))
    fp_max = manifest.get("false_positives_max", 0)
    tags = manifest.get("tags", [])
    return expected, fp_max, tags


def _path_matches(actual_path: str, expected_path: str) -> bool:
    if actual_path == expected_path:
        return True
    return actual_path.endswith("/" + expected_path)


def _finding_matches(actual: Finding, exp: ExpectedFinding) -> bool:
    if not _path_matches(actual.path, exp.path):
        return False
    if actual.line is None:
        return False
    lo, hi = exp.line_range
    if not (lo <= actual.line <= hi):
        return False
    if actual.severity not in exp.severity:
        return False
    if exp.description_contains:
        if exp.description_contains.lower() not in actual.body.lower():
            return False
    return True


def _find_match(
    exp: ExpectedFinding, actuals: list[Finding], used: set[int],
) -> MatchResult:
    mr = MatchResult(expected=exp)
    for i, actual in enumerate(actuals):
        if i in used or not _finding_matches(actual, exp):
            continue
        mr.matched = True
        mr.matched_finding_id = actual.id
        mr.matched_severity = actual.severity
        mr.severity_exact = actual.severity == exp.severity[0]
        used.add(i)
        break
    return mr


def match_findings(
    expected: list[ExpectedFinding],
    actuals: list[Finding],
) -> tuple[list[MatchResult], list[str]]:
    used: set[int] = set()
    results = [_find_match(exp, actuals, used) for exp in expected]

    false_positive_ids = [
        actuals[i].id for i in range(len(actuals)) if i not in used
    ]
    return results, false_positive_ids


def score_entry(
    entry_name: str,
    model: str,
    run_index: int,
    expected: list[ExpectedFinding],
    actuals: list[Finding],
    false_positives_max: int,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    billed_input: int = 0,
    cache_read_ratio: float = 0.0,
) -> ScoringResult:
    matches, fp_ids = match_findings(expected, actuals)
    matched_count = sum(1 for m in matches if m.matched)
    total_expected = len(expected)
    total_actual = len(actuals)

    recall = matched_count / total_expected if total_expected else 0.0
    precision = matched_count / total_actual if total_actual else (
        1.0 if total_expected == 0 else 0.0
    )
    sev_exact = sum(1 for m in matches if m.severity_exact)
    sev_acc = sev_exact / matched_count if matched_count else 0.0

    return ScoringResult(
        entry_name=entry_name,
        model=model,
        run_index=run_index,
        matches=matches,
        false_positive_ids=fp_ids,
        recall=recall,
        precision=precision,
        severity_accuracy=sev_acc,
        false_positive_count=len(fp_ids),
        false_positive_ok=len(fp_ids) <= false_positives_max,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        billed_input=billed_input,
        cache_read_ratio=cache_read_ratio,
    )


def _run_orchestrate(
    repo_dir: str, reviews_dir: str, review_file: str, opts: RunOptions,
) -> int:
    cmd = [
        sys.executable, str(_REVIEW_ORCHESTRATE),
        "--mode", "self",
        "--effort", opts.effort,
        "--repo-dir", repo_dir,
        "--review-file", review_file,
        "--reviews-dir", reviews_dir,
        "--repo", "eval/corpus",
    ]
    if opts.model:
        cmd += ["--model", opts.model]
    try:
        result = subprocess.run(
            cmd,
            capture_output=not opts.verbose,
            timeout=opts.timeout,
            env=clean_env(),
        )
    except subprocess.TimeoutExpired:
        print("  timeout expired", file=sys.stderr)
        return 1
    return result.returncode


def parse_review_output(
    review_file: str, session_log: str,
) -> tuple[list[Finding], ai_usage.SessionUsage]:
    review_path = Path(review_file)
    if not review_path.exists():
        return [], ai_usage.SessionUsage()
    findings = parse_findings(review_path.read_text())
    return findings, ai_usage.parse_session_log(session_log)


class ReviewTask:
    """Review a corpus case and score the findings it produced."""

    name = "review"

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        repo_dir = create_temp_repo(str(case_dir / "src"), prefix="eval-review-")
        reviews_dir = tempfile.mkdtemp(prefix="eval-reviews-")
        review_file = str(Path(reviews_dir) / "review.md")
        session_log = str(Path(reviews_dir) / "session.jsonl")

        exit_code = _run_orchestrate(repo_dir, reviews_dir, review_file, opts)
        findings, usage = parse_review_output(review_file, session_log)

        return RunArtifacts(
            exit_code=exit_code,
            usage=usage,
            temp_dirs=[repo_dir, reviews_dir],
            data={"findings": findings, "summary": f"findings: {len(findings)}"},
        )

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        expected, fp_max, _ = parse_manifest(manifest)
        usage = artifacts.usage
        return score_entry(
            entry_name="", model="", run_index=0,
            expected=expected,
            actuals=artifacts.data.get("findings", []),
            false_positives_max=fp_max,
            cost_usd=usage.cost,
            duration_ms=usage.duration_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            billed_input=usage.billed_input,
            cache_read_ratio=usage.cache_read_ratio,
        )
