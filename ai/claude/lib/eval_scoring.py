"""Evaluation scoring for model review benchmarks.

Matches actual review findings against expected findings from a corpus
manifest and produces precision, recall, severity accuracy, and cost metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from review_findings import Finding


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


@dataclass
class ScoringResult:
    entry_name: str
    model: str
    run_index: int
    matches: list[MatchResult] = field(default_factory=list)
    false_positive_ids: list[str] = field(default_factory=list)
    recall: float = 0.0
    precision: float = 0.0
    severity_accuracy: float = 0.0
    false_positive_count: int = 0
    false_positive_ok: bool = True
    cost_usd: float = 0.0
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


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
    )


def aggregate_runs(results: list[ScoringResult]) -> dict:
    if not results:
        return {
            "recall_mean": 0.0, "recall_std": 0.0,
            "precision_mean": 0.0, "precision_std": 0.0,
            "severity_accuracy_mean": 0.0,
            "false_positive_mean": 0.0,
            "cost_mean": 0.0, "duration_mean_ms": 0,
        }

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals)

    def _std(vals: list[float]) -> float:
        if len(vals) < 2:
            return 0.0
        m = _mean(vals)
        return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))

    recalls = [r.recall for r in results]
    precisions = [r.precision for r in results]
    sev_accs = [r.severity_accuracy for r in results]
    fps = [float(r.false_positive_count) for r in results]
    costs = [r.cost_usd for r in results]
    durations = [float(r.duration_ms) for r in results]

    return {
        "recall_mean": _mean(recalls),
        "recall_std": _std(recalls),
        "precision_mean": _mean(precisions),
        "precision_std": _std(precisions),
        "severity_accuracy_mean": _mean(sev_accs),
        "false_positive_mean": _mean(fps),
        "cost_mean": _mean(costs),
        "duration_mean_ms": int(_mean(durations)),
    }


def format_summary_table(
    all_results: dict[tuple[str, str], list[ScoringResult]],
) -> str:
    header = (
        "| Entry | Model | Recall | Precision | Sev.Acc | FP | Cost | Duration |"
    )
    sep = "|---|---|---|---|---|---|---|---|"
    rows = [header, sep]

    for (entry, model), results in sorted(all_results.items()):
        agg = aggregate_runs(results)
        recall_s = f"{agg['recall_mean']:.0%}"
        if agg["recall_std"] > 0:
            recall_s += f" ±{agg['recall_std']:.0%}"
        prec_s = f"{agg['precision_mean']:.0%}"
        if agg["precision_std"] > 0:
            prec_s += f" ±{agg['precision_std']:.0%}"
        rows.append(
            f"| {entry} | {model} "
            f"| {recall_s} "
            f"| {prec_s} "
            f"| {agg['severity_accuracy_mean']:.0%} "
            f"| {agg['false_positive_mean']:.1f} "
            f"| ${agg['cost_mean']:.2f} "
            f"| {agg['duration_mean_ms'] / 1000:.0f}s |"
        )

    return "\n".join(rows)


_BASELINE_REQUIRED_KEYS = {"schema_version", "model", "entries"}
_ENTRY_METRIC_KEYS = {"recall_mean", "precision_mean"}


def _validate_entry(name: str, entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return [f"entry '{name}' must be a JSON object"]
    errors: list[str] = []
    for key in _ENTRY_METRIC_KEYS:
        if key not in entry:
            errors.append(f"entry '{name}': missing {key}")
        elif not isinstance(entry[key], (int, float)):
            errors.append(f"entry '{name}': {key} must be a number")
    if "runs" in entry and not isinstance(entry["runs"], list):
        errors.append(f"entry '{name}': runs must be a list")
    return errors


def validate_baseline_schema(data: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be a JSON object"]

    if "schema_version" not in data:
        errors.append("missing required field: schema_version")
    elif data["schema_version"] != 1:
        errors.append(f"unsupported schema_version: {data['schema_version']}")

    if "model" not in data:
        errors.append("missing required field: model")
    elif not isinstance(data["model"], str) or not data["model"]:
        errors.append("model must be a non-empty string")

    if "entries" not in data:
        errors.append("missing required field: entries")
    elif not isinstance(data["entries"], dict):
        errors.append("entries must be a JSON object")
    else:
        for name, entry in data["entries"].items():
            errors.extend(_validate_entry(name, entry))

    return errors


def _compare_metric(
    baseline_val: float, current_val: float,
    threshold: float, higher_is_better: bool,
) -> dict:
    delta = current_val - baseline_val
    if higher_is_better:
        regression = delta < -threshold
    else:
        regression = delta > threshold
    return {
        "baseline": baseline_val,
        "current": current_val,
        "delta": delta,
        "regression": regression,
    }


def _compare_entry_pair(
    base_entry: dict, cur_model: dict,
    entry_name: str, model_label: str,
    threshold: float,
) -> tuple[dict[str, dict], list[tuple[str, str, str, float]]]:
    metrics: dict[str, dict] = {}
    regs: list[tuple[str, str, str, float]] = []

    for key in ("recall_mean", "precision_mean", "severity_accuracy_mean"):
        m = _compare_metric(
            base_entry.get(key, 0.0), cur_model.get(key, 0.0),
            threshold, higher_is_better=True,
        )
        metrics[key] = m
        if m["regression"]:
            regs.append((entry_name, model_label, key, m["delta"]))

    fp_m = _compare_metric(
        base_entry.get("false_positive_mean", 0.0),
        cur_model.get("false_positive_mean", 0.0),
        0.5, higher_is_better=False,
    )
    metrics["false_positive_mean"] = fp_m
    if fp_m["regression"]:
        regs.append((entry_name, model_label, "false_positive_mean", fp_m["delta"]))

    cost_base = base_entry.get("cost_mean", 0.0)
    cost_threshold = cost_base * 0.5 if cost_base > 0 else 0.5
    cost_m = _compare_metric(
        cost_base, cur_model.get("cost_mean", 0.0),
        cost_threshold, higher_is_better=False,
    )
    metrics["cost_mean"] = cost_m

    return metrics, regs


def _compare_model_baseline(
    model_label: str,
    baseline_entries: dict,
    current_entries: dict,
    threshold: float,
    out: dict,
) -> None:
    all_entry_names = sorted(set(baseline_entries) | set(current_entries))
    for entry_name in all_entry_names:
        cur_model = current_entries.get(entry_name, {}).get(model_label)
        base_entry = baseline_entries.get(entry_name)

        if cur_model is None and base_entry is not None:
            out["missing_entries"].append((entry_name, model_label))
        elif cur_model is not None and base_entry is None:
            out["new_entries"].append((entry_name, model_label))
        elif cur_model is not None and base_entry is not None:
            metrics, regs = _compare_entry_pair(
                base_entry, cur_model, entry_name, model_label, threshold,
            )
            out["comparisons"][(entry_name, model_label)] = metrics
            out["regressions"].extend(regs)


def compare_baselines(
    baselines: dict[str, dict],
    current: dict,
    threshold: float = 0.05,
) -> dict:
    out: dict = {
        "regressions": [],
        "comparisons": {},
        "new_entries": [],
        "missing_entries": [],
    }
    current_entries = current.get("entries", {})

    for model_label, baseline in baselines.items():
        baseline_entries = baseline.get("entries", {})
        _compare_model_baseline(
            model_label, baseline_entries, current_entries, threshold, out,
        )

    return out


def _format_metric_row(
    entry: str, model: str, metric_name: str, m: dict,
) -> str:
    if metric_name == "cost_mean":
        base_s = f"${m['baseline']:.2f}"
        cur_s = f"${m['current']:.2f}"
        delta_s = f"{m['delta']:+.2f}"
    elif metric_name == "false_positive_mean":
        base_s = f"{m['baseline']:.1f}"
        cur_s = f"{m['current']:.1f}"
        delta_s = f"{m['delta']:+.1f}"
    else:
        base_s = f"{m['baseline']:.0%}"
        cur_s = f"{m['current']:.0%}"
        delta_s = f"{m['delta']:+.0%}"

    if m["regression"]:
        status = "REGRESSED"
    elif m["delta"] != 0:
        status = "improved"
    else:
        status = "-"

    display_name = metric_name.replace("_mean", "").replace("_", " ")
    return (
        f"| {entry} | {model} | {display_name} "
        f"| {base_s} | {cur_s} | {delta_s} | {status} |"
    )


def format_comparison_table(comparison: dict) -> str:
    header = "| Entry | Model | Metric | Baseline | Current | Delta | Status |"
    sep = "|---|---|---|---|---|---|---|"
    rows = [header, sep]

    for (entry, model), metrics in sorted(comparison.get("comparisons", {}).items()):
        for metric_name, m in sorted(metrics.items()):
            rows.append(_format_metric_row(entry, model, metric_name, m))

    for entry, model in comparison.get("new_entries", []):
        rows.append(f"| {entry} | {model} | - | - | - | - | new |")
    for entry, model in comparison.get("missing_entries", []):
        rows.append(f"| {entry} | {model} | - | - | - | - | missing |")

    return "\n".join(rows)
