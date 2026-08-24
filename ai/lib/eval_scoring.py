"""Evaluation scoring, aggregation, and baseline comparison.

Task-agnostic: what a run *is* and how it is scored belongs to the task
(`eval_scoring_review`, `eval_scoring_cifix`, ...). What lives here is the shape
of a score, the statistics over repeated runs, and the baseline diff — the parts
every task shares.

`eval-models --compare` diffs a run against the baselines in `eval/results/` and
exits `2` on a regression. The gate is deliberately narrow, because a gate that
flaps gets disabled:

| Metric | Gate |
|---|---|
| billed input tokens | fail past 15% growth |
| output tokens | fail past 15% growth |
| recall, precision, severity accuracy | fail on any drop past the noise threshold |
| false positives | fail past +0.5 per case |
| cache-read ratio | fail below 60% |
| cost, duration | reported, never gated |

Tokens are gated and cost is not because tokens are what a change controls; the
dollar figure also moves with model prices, and duration moves with machine
load. The cache-read floor is an absolute minimum rather than a delta: a
prompt-prefix change that silently disables caching shows up as the ratio
collapsing, and the value it collapsed from is not the interesting number.

A baseline written before a metric existed leaves it ungated rather than
failing, so an older baseline still loads. The comparison table marks every
metric `pass`, `fail`, or `ungated` — including the ones that cannot fail.
"""

# doc-group: eval

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
            "billed_input_mean": 0.0, "output_tokens_mean": 0.0,
            "cache_read_ratio_mean": 0.0,
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
        "billed_input_mean": _mean([float(r.billed_input) for r in results]),
        "output_tokens_mean": _mean([float(r.output_tokens) for r in results]),
        "cache_read_ratio_mean": _mean([r.cache_read_ratio for r in results]),
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


# Version 2 added the token metrics the CI ratchet gates on. Version 1 baselines
# still load: a metric they never recorded is ungated, not failing.
SCHEMA_VERSION = 2
_SUPPORTED_SCHEMA_VERSIONS = (1, 2)

_ENTRY_METRIC_KEYS = {"recall_mean", "precision_mean"}
_OPTIONAL_METRIC_KEYS = {
    "billed_input_mean", "output_tokens_mean", "cache_read_ratio_mean",
}


def _validate_entry(name: str, entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return [f"entry '{name}' must be a JSON object"]
    errors: list[str] = []
    for key in _ENTRY_METRIC_KEYS:
        if key not in entry:
            errors.append(f"entry '{name}': missing {key}")
        elif not isinstance(entry[key], (int, float)):
            errors.append(f"entry '{name}': {key} must be a number")
    for key in _OPTIONAL_METRIC_KEYS & set(entry):
        if not isinstance(entry[key], (int, float)):
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
    elif data["schema_version"] not in _SUPPORTED_SCHEMA_VERSIONS:
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


# Tokens are where the money is: 15% headroom on the two metrics that carry the
# bill. The cache-read floor is a separate shape — an absolute minimum, not a
# delta — because a caching regression shows up as the ratio collapsing, and the
# baseline it collapsed from is not the interesting number.
TOKEN_REGRESSION_RATIO = 0.15
CACHE_READ_FLOOR = 0.60

_RELATIVE_TOKEN_METRICS = ("billed_input_mean", "output_tokens_mean")
_DISPLAY_METRICS = ("cost_mean", "duration_mean_ms")


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
        "gated": True,
    }


def _compare_relative(baseline_val: float, current_val: float, ratio: float) -> dict:
    """Growth past `ratio` of the baseline. Exactly at the ratio is still within budget."""
    return {
        "baseline": baseline_val,
        "current": current_val,
        "delta": current_val - baseline_val,
        "regression": current_val > baseline_val * (1 + ratio),
        "gated": True,
    }


def _compare_floor(baseline_val: float, current_val: float, floor: float) -> dict:
    """An absolute minimum. Movement above the floor is noise, not a regression."""
    return {
        "baseline": baseline_val,
        "current": current_val,
        "delta": current_val - baseline_val,
        "regression": current_val < floor,
        "gated": True,
    }


def _gate_quality_metrics(base: dict, cur: dict, threshold: float) -> dict[str, dict]:
    """Finding quality — unchanged behavior: any drop past the noise floor gates."""
    metrics = {
        key: _compare_metric(base.get(key, 0.0), cur.get(key, 0.0),
                             threshold, higher_is_better=True)
        for key in ("recall_mean", "precision_mean", "severity_accuracy_mean")
    }
    metrics["false_positive_mean"] = _compare_metric(
        base.get("false_positive_mean", 0.0), cur.get("false_positive_mean", 0.0),
        0.5, higher_is_better=False,
    )
    return metrics


def _gate_token_metrics(base: dict, cur: dict) -> dict[str, dict]:
    """Token spend. A metric absent from either side is unknown, so it stays ungated."""
    metrics: dict[str, dict] = {}
    for key in _RELATIVE_TOKEN_METRICS:
        # A zero baseline means the run was never measured — nothing to ratchet against.
        if key not in base or key not in cur or base[key] <= 0:
            continue
        metrics[key] = _compare_relative(base[key], cur[key], TOKEN_REGRESSION_RATIO)

    key = "cache_read_ratio_mean"
    if key in base and key in cur:
        metrics[key] = _compare_floor(base[key], cur[key], CACHE_READ_FLOOR)
    return metrics


def _display_metrics(base: dict, cur: dict) -> dict[str, dict]:
    """Reported, never gated: model prices and machine load make these flap."""
    return {
        key: {
            "baseline": base.get(key, 0.0),
            "current": cur.get(key, 0.0),
            "delta": cur.get(key, 0.0) - base.get(key, 0.0),
            "regression": False,
            "gated": False,
        }
        for key in _DISPLAY_METRICS if key in base or key in cur
    }


def _compare_entry_pair(
    base_entry: dict, cur_model: dict,
    entry_name: str, model_label: str,
    threshold: float,
) -> tuple[dict[str, dict], list[tuple[str, str, str, float]]]:
    metrics = _gate_quality_metrics(base_entry, cur_model, threshold)
    metrics.update(_gate_token_metrics(base_entry, cur_model))
    metrics.update(_display_metrics(base_entry, cur_model))

    regs = [
        (entry_name, model_label, key, m["delta"])
        for key, m in metrics.items() if m["regression"]
    ]
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


_COUNT_METRICS = {
    "billed_input_mean", "output_tokens_mean", "duration_mean_ms",
    "false_positive_mean",
}


def _format_values(metric_name: str, m: dict) -> tuple[str, str, str]:
    if metric_name == "cost_mean":
        return (f"${m['baseline']:.2f}", f"${m['current']:.2f}",
                f"{m['delta']:+.2f}")
    if metric_name in _COUNT_METRICS:
        return (f"{m['baseline']:,.1f}", f"{m['current']:,.1f}",
                f"{m['delta']:+,.1f}")
    return (f"{m['baseline']:.0%}", f"{m['current']:.0%}", f"{m['delta']:+.0%}")


def _format_metric_row(
    entry: str, model: str, metric_name: str, m: dict,
) -> str:
    base_s, cur_s, delta_s = _format_values(metric_name, m)

    # "changed", not "improved": a delta that stays inside the threshold can
    # still be movement in the wrong direction.
    if m["regression"]:
        status = "REGRESSED"
    else:
        status = "changed" if m["delta"] != 0 else "-"

    gate = "ungated" if not m.get("gated", True) else \
        ("fail" if m["regression"] else "pass")

    display_name = metric_name.replace("_mean", "").replace("_", " ")
    return (
        f"| {entry} | {model} | {display_name} "
        f"| {base_s} | {cur_s} | {delta_s} | {status} | {gate} |"
    )


def format_comparison_table(comparison: dict) -> str:
    header = (
        "| Entry | Model | Metric | Baseline | Current | Delta | Status | Gate |"
    )
    sep = "|---|---|---|---|---|---|---|---|"
    rows = [header, sep]

    for (entry, model), metrics in sorted(comparison.get("comparisons", {}).items()):
        for metric_name, m in sorted(metrics.items()):
            rows.append(_format_metric_row(entry, model, metric_name, m))

    for entry, model in comparison.get("new_entries", []):
        rows.append(f"| {entry} | {model} | - | - | - | - | new | - |")
    for entry, model in comparison.get("missing_entries", []):
        rows.append(f"| {entry} | {model} | - | - | - | - | missing | - |")

    return "\n".join(rows)
