"""Evaluation scoring, aggregation, and baseline comparison.

Task-agnostic: what a run *is* and how it is scored belongs to the task
(`eval_scoring_review`, `eval_scoring_cifix`, ...). What lives here is the shape
of a score, the statistics over repeated runs, and the baseline diff — the parts
every task shares.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ScoringResult:
    """One scored run. `matches` holds task-defined match records, opaque here."""
    entry_name: str
    model: str
    run_index: int
    matches: list = field(default_factory=list)
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
    billed_input: int = 0
    cache_read_ratio: float = 0.0


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


def validate_baseline_schema(data: object) -> list[str]:
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
    # ceiling: cost is informational-only — not added to regs because relative
    # thresholds are model-dependent and unsuitable for hard gating
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
