"""High-water ratchet for committed eval baselines.

A predecessor-relative gate cannot catch slow decay: one run flipping on a
3-run mean is ±0.333, and each observed decay step was -0.222. Any threshold
that is noise-safe misses the decay; any threshold that catches it false-fires.
This module compares against the best value ever recorded. Two -0.222 steps
sum to -0.444, which clears the 0.334 noise floor.

`eval-models --save-baselines` and `bin/local/validate-eval-floors` both call
the functions here. An entry in a baseline with no floor record fails — deleting
a key must not defeat the gate. `floors.json` is the high-water across committed
history of each lineage, not the current file; `seed_floors` folds historical
baselines and never drops `best`.
"""

# doc-group: eval

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from eval.scoring import (
    AGGREGATE_TOLERANCE,
    CACHE_READ_FLOOR,
    ENTRY_FALSE_POSITIVE_TOLERANCE,
    ENTRY_SEVERITY_TOLERANCE,
    TOKEN_REGRESSION_RATIO,
    entry_recall_tolerance,
)

FLOORS_FILENAME = "floors.json"
FLOORS_SCHEMA_VERSION = 1

HIGHER_IS_BETTER = (
    "recall_mean", "precision_mean", "severity_accuracy_mean", "cache_read_ratio_mean",
)
LOWER_IS_BETTER = (
    "false_positive_mean", "billed_input_mean", "output_tokens_mean",
)
RATCHET_METRICS = HIGHER_IS_BETTER + LOWER_IS_BETTER
# Observed ±0.08 drift on unchanged cases — recorded, never gated per entry.
UNGATED_PER_ENTRY = frozenset({"precision_mean"})

_SEP_RE = re.compile(r" (—|–|-+) ")
_MODEL_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9._-]")


@dataclass(frozen=True)
class FloorRecord:
    floor: float
    best: float
    lowered: str | None = None


@dataclass(frozen=True)
class FloorSet:
    schema_version: int
    backends: Mapping[str, Mapping[str, Mapping[str, FloorRecord]]]


@dataclass(frozen=True)
class FloorBreach:
    entry: str
    model: str
    metric: str
    floor: float
    current: float
    std: float
    tolerance: float


@dataclass(frozen=True)
class FloorComparison:
    breaches: tuple[FloorBreach, ...]
    aggregate_breaches: tuple[FloorBreach, ...]
    stale_reasons: tuple[str, ...]
    unfloored_entries: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.breaches or self.aggregate_breaches or self.unfloored_entries)


@dataclass(frozen=True)
class FloorAccept:
    entry: str
    metric: str
    value: float
    reason: str


def baseline_stem(backend: str, model: str) -> str:
    """Filename stem matching eval-models: ``{backend}-{sanitized-model}``."""
    cleaned = model.replace("(", "").replace(")", "")
    cleaned = _MODEL_SANITIZE_RE.sub("-", cleaned).strip("-") or "default"
    return f"{backend}-{cleaned}"


def empty_floors() -> FloorSet:
    return FloorSet(schema_version=FLOORS_SCHEMA_VERSION, backends={})


def load_floors(path: Path | str) -> FloorSet:
    data = json.loads(Path(path).read_text())
    errors = validate_floors_document(data)
    if errors:
        raise ValueError("invalid floors document:\n" + "\n".join(errors))
    return parse_floors(data)


def parse_floors(data: dict) -> FloorSet:
    backends: dict[str, dict[str, dict[str, FloorRecord]]] = {}
    for stem, entries in data.get("backends", {}).items():
        backends[stem] = {
            name: {metric: _record_from(rec) for metric, rec in metrics.items()}
            for name, metrics in entries.items()
        }
    return FloorSet(int(data["schema_version"]), backends)


def _record_from(rec: dict) -> FloorRecord:
    lowered = rec.get("lowered")
    return FloorRecord(
        floor=float(rec["floor"]),
        best=float(rec["best"]),
        lowered=lowered if isinstance(lowered, str) else None,
    )


def write_floors(path: Path | str, floors: FloorSet) -> None:
    path = Path(path)
    path.write_text(
        json.dumps(_floors_to_dict(floors), indent=2, ensure_ascii=False) + "\n",
    )


def _floors_to_dict(floors: FloorSet) -> dict:
    backends = {}
    for stem in sorted(floors.backends):
        entries = floors.backends[stem]
        backends[stem] = {
            name: {metric: _record_to_dict(entries[name][metric])
                   for metric in sorted(entries[name])}
            for name in sorted(entries)
        }
    return {"schema_version": floors.schema_version, "backends": backends}


def _record_to_dict(rec: FloorRecord) -> dict:
    out: dict = {"floor": rec.floor, "best": rec.best}
    if rec.lowered is not None:
        out["lowered"] = rec.lowered
    return out


def lowered_reason(text: str) -> str | None:
    """The reason after a valid separator, or None if the marker declares nothing."""
    match = _SEP_RE.search(text)
    if match is None:
        return None
    reason = text[match.end():].strip()
    return reason or None


def validate_floors_document(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["root must be a JSON object"]
    errors: list[str] = []
    version = data.get("schema_version")
    if version is None:
        errors.append("missing required field: schema_version")
    elif version != FLOORS_SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {version}")
    backends = data.get("backends")
    if backends is None:
        errors.append("missing required field: backends")
        return errors
    if not isinstance(backends, dict):
        errors.append("backends must be a JSON object")
        return errors
    for stem, entries in backends.items():
        errors.extend(_validate_backend(stem, entries))
    return errors


def _validate_backend(stem: object, entries: object) -> list[str]:
    if not isinstance(stem, str) or not stem:
        return ["backend key must be a non-empty string"]
    if not isinstance(entries, dict):
        return [f"backend '{stem}' must be a JSON object"]
    errors: list[str] = []
    for name, metrics in entries.items():
        errors.extend(_validate_entry_floors(stem, name, metrics))
    return errors


def _validate_entry_floors(stem: str, name: object, metrics: object) -> list[str]:
    prefix = f"{stem}/{name}"
    if not isinstance(name, str) or not name:
        return [f"backend '{stem}': entry key must be a non-empty string"]
    if not isinstance(metrics, dict):
        return [f"{prefix} must be a JSON object"]
    errors: list[str] = []
    for metric, rec in metrics.items():
        errors.extend(_validate_record(prefix, metric, rec))
    return errors


def _validate_record(prefix: str, metric: object, rec: object) -> list[str]:
    loc = f"{prefix}/{metric}"
    if not isinstance(metric, str) or not metric:
        return [f"{prefix}: metric key must be a non-empty string"]
    if not isinstance(rec, dict):
        return [f"{loc} must be a JSON object"]
    errors = _number_field_errors(loc, rec, "floor") + _number_field_errors(loc, rec, "best")
    if errors:
        return errors
    return _floor_best_errors(
        loc, metric, rec["floor"], rec["best"], rec.get("lowered"),
    )


def _needs_lowered(metric: str, floor: float, best: float) -> bool:
    if metric in LOWER_IS_BETTER:
        return floor > best
    return floor < best


def _floor_best_errors(
    loc: str, metric: str, floor: float, best: float, lowered: object,
) -> list[str]:
    inverted = metric in LOWER_IS_BETTER
    wrong_way = floor < best if inverted else floor > best
    if wrong_way:
        relation = "is below" if inverted else "exceeds"
        return [f"{loc}: floor {floor} {relation} best {best}"]
    if lowered is None:
        if _needs_lowered(metric, floor, best):
            side = "above" if inverted else "below"
            return [f"{loc}: floor is {side} best with no lowered reason"]
        return []
    if not isinstance(lowered, str):
        return [f"{loc}: lowered must be a string"]
    if lowered_reason(lowered) is None:
        return [f"{loc}: lowered declares nothing — need '<what> — <why>'"]
    return []


def _number_field_errors(loc: str, rec: dict, field: str) -> list[str]:
    if field not in rec:
        return [f"{loc}: missing {field}"]
    value = rec[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [f"{loc}: {field} must be a number"]
    return []


def compare_against_floors(
    floors: FloorSet, baseline: dict, runs_per_entry: int,
) -> FloorComparison:
    stem = baseline_stem(str(baseline.get("backend", "")), str(baseline.get("model", "")))
    model = str(baseline.get("model", ""))
    entries = baseline.get("entries") or {}
    recorded = floors.backends.get(stem, {})
    unfloored = tuple(name for name in sorted(entries) if name not in recorded)
    breaches: list[FloorBreach] = []
    for name, metrics in entries.items():
        if name not in recorded:
            continue
        breaches.extend(
            _entry_breaches(name, model, recorded[name], metrics, runs_per_entry),
        )
    return FloorComparison(
        breaches=tuple(breaches),
        aggregate_breaches=tuple(_aggregate_breaches(recorded, entries, model)),
        stale_reasons=tuple(_stale_reasons(stem, recorded)),
        unfloored_entries=unfloored,
    )


def _entry_breaches(
    entry: str, model: str,
    records: Mapping[str, FloorRecord],
    metrics: dict,
    runs_per_entry: int,
) -> list[FloorBreach]:
    breaches: list[FloorBreach] = []
    for metric, rec in records.items():
        if metric in UNGATED_PER_ENTRY or metric not in metrics:
            continue
        current = float(metrics[metric])
        tolerance = _tolerance_for(metric, rec, runs_per_entry)
        if not _breached(metric, rec.floor, current, tolerance):
            continue
        breaches.append(FloorBreach(
            entry=entry, model=model, metric=metric,
            floor=rec.floor, current=current,
            std=float(metrics.get("recall_std", 0.0) or 0.0),
            tolerance=tolerance,
        ))
    return breaches


def _tolerance_for(metric: str, rec: FloorRecord, runs_per_entry: int) -> float:
    if metric == "recall_mean":
        return entry_recall_tolerance(runs_per_entry)
    if metric == "severity_accuracy_mean":
        return ENTRY_SEVERITY_TOLERANCE
    if metric == "false_positive_mean":
        return ENTRY_FALSE_POSITIVE_TOLERANCE
    if metric in ("billed_input_mean", "output_tokens_mean"):
        return rec.floor * TOKEN_REGRESSION_RATIO
    if metric == "cache_read_ratio_mean":
        return 0.0
    return 0.0


def _breached(metric: str, floor: float, current: float, tolerance: float) -> bool:
    if metric == "cache_read_ratio_mean":
        return floor >= CACHE_READ_FLOOR and current < CACHE_READ_FLOOR
    if metric in LOWER_IS_BETTER:
        return current > floor + tolerance
    return current < floor - tolerance


def _aggregate_breaches(
    recorded: Mapping[str, Mapping[str, FloorRecord]],
    entries: dict,
    model: str,
) -> list[FloorBreach]:
    shared = sorted(set(recorded) & set(entries))
    breaches: list[FloorBreach] = []
    for metric in ("recall_mean", "precision_mean"):
        breach = _mean_breach(shared, recorded, entries, model, metric)
        if breach is not None:
            breaches.append(breach)
    return breaches


def _mean_breach(
    shared: list[str],
    recorded: Mapping[str, Mapping[str, FloorRecord]],
    entries: dict,
    model: str,
    metric: str,
) -> FloorBreach | None:
    pairs = [
        (recorded[name][metric].floor, float(entries[name][metric]))
        for name in shared
        if metric in recorded[name] and metric in entries[name]
    ]
    if not pairs:
        return None
    mean_floor = sum(p[0] for p in pairs) / len(pairs)
    mean_current = sum(p[1] for p in pairs) / len(pairs)
    if mean_current >= mean_floor - AGGREGATE_TOLERANCE:
        return None
    return FloorBreach(
        entry="(aggregate)", model=model, metric=metric,
        floor=mean_floor, current=mean_current, std=0.0,
        tolerance=AGGREGATE_TOLERANCE,
    )


def _stale_reasons(
    stem: str, recorded: Mapping[str, Mapping[str, FloorRecord]],
) -> list[str]:
    stale: list[str] = []
    for name, metrics in recorded.items():
        stale.extend(_stale_in_entry(stem, name, metrics))
    return stale


def _stale_in_entry(
    stem: str, name: str, metrics: Mapping[str, FloorRecord],
) -> list[str]:
    return [
        f"{stem}/{name}/{metric}: lowered is stale (floor at best)"
        for metric, rec in metrics.items()
        if rec.lowered is not None and not _needs_lowered(metric, rec.floor, rec.best)
    ]


def seed_floors(baselines: list[dict]) -> FloorSet:
    """High-water seed: fold ``raise_floors`` over historical baselines in order.

    A later worse value never drops ``best``. Callers pass already-loaded
    documents — this function does not read git.
    """
    floors = empty_floors()
    for baseline in baselines:
        floors = raise_floors(floors, baseline)
    return floors


def raise_floors(floors: FloorSet, baseline: dict) -> FloorSet:
    """Raise `best` (and `floor`, when not lowered) on improvement. Never drops `best`."""
    stem = baseline_stem(str(baseline.get("backend", "")), str(baseline.get("model", "")))
    backends = _copy_backends(floors.backends)
    bucket = backends.setdefault(stem, {})
    for name, metrics in (baseline.get("entries") or {}).items():
        bucket[name] = _raised_entry(bucket.get(name, {}), metrics)
    return FloorSet(floors.schema_version, backends)


def _raised_entry(
    old: Mapping[str, FloorRecord], metrics: dict,
) -> dict[str, FloorRecord]:
    recs = dict(old)
    for metric in RATCHET_METRICS:
        if metric not in metrics:
            continue
        recs[metric] = _raised_record(recs.get(metric), float(metrics[metric]), metric)
    return recs


def _raised_record(old: FloorRecord | None, current: float, metric: str) -> FloorRecord:
    if old is None:
        return FloorRecord(floor=current, best=current)
    higher = metric in HIGHER_IS_BETTER
    improved = current > old.best if higher else current < old.best
    if not improved:
        return old
    if old.lowered:
        return FloorRecord(floor=old.floor, best=current, lowered=old.lowered)
    return FloorRecord(floor=current, best=current)


def _copy_backends(
    backends: Mapping[str, Mapping[str, Mapping[str, FloorRecord]]],
) -> dict[str, dict[str, dict[str, FloorRecord]]]:
    return {
        stem: {name: dict(metrics) for name, metrics in entries.items()}
        for stem, entries in backends.items()
    }


def parse_accept_regression(spec: str) -> FloorAccept:
    """Parse ``entry/metric=value — reason``. Raises ValueError with no reason."""
    if "=" not in spec:
        raise ValueError(
            "--accept-regression requires 'entry/metric=value — reason'",
        )
    left, right = spec.split("=", 1)
    if "/" not in left:
        raise ValueError(
            "--accept-regression requires 'entry/metric=value — reason'",
        )
    entry, metric = left.rsplit("/", 1)
    entry, metric = entry.strip(), metric.strip()
    reason = lowered_reason(right)
    value_text = right
    match = _SEP_RE.search(right)
    if match is not None:
        value_text = right[:match.start()]
    if reason is None:
        raise ValueError(
            "--accept-regression requires a reason after the separator "
            f"(got {spec!r})",
        )
    try:
        value = float(value_text.strip())
    except ValueError as exc:
        raise ValueError(
            f"--accept-regression value is not a number: {value_text!r}",
        ) from exc
    if not entry or not metric:
        raise ValueError("--accept-regression needs a non-empty entry and metric")
    return FloorAccept(entry=entry, metric=metric, value=value, reason=reason)


def apply_accept_regressions(
    floors: FloorSet, accepts: list[FloorAccept], output: dict,
) -> FloorSet:
    backend = str(output.get("backend", ""))
    stems = {
        baseline_stem(backend, model)
        for models in output.get("entries", {}).values()
        for model in models
    }
    backends = _copy_backends(floors.backends)
    for accept in accepts:
        marker = f"{accept.value} — {accept.reason}"
        for stem in stems:
            bucket = backends.setdefault(stem, {})
            recs = dict(bucket.get(accept.entry, {}))
            old = recs.get(accept.metric)
            best = old.best if old is not None else accept.value
            recs[accept.metric] = FloorRecord(
                floor=accept.value, best=best, lowered=marker,
            )
            bucket[accept.entry] = recs
    return FloorSet(floors.schema_version, backends)


def floors_path(results_dir: str | Path) -> Path:
    return Path(results_dir) / FLOORS_FILENAME


def load_floors_or_empty(path: Path | str) -> FloorSet:
    path = Path(path)
    if path.is_file():
        return load_floors(path)
    return empty_floors()


def baselines_from_output(output: dict) -> list[dict]:
    backend = output.get("backend") or ""
    by_model: dict[str, dict] = {}
    for name, models in output.get("entries", {}).items():
        for model, metrics in models.items():
            by_model.setdefault(model, {})[name] = metrics
    return [
        {"backend": backend, "model": model, "entries": entries}
        for model, entries in sorted(by_model.items())
    ]


def ratchet_floors(
    output: dict, results_dir: str | Path, accepts: list[FloorAccept],
) -> Path:
    path = floors_path(results_dir)
    floors = load_floors_or_empty(path)
    if accepts:
        floors = apply_accept_regressions(floors, accepts, output)
    for baseline in baselines_from_output(output):
        floors = raise_floors(floors, baseline)
    write_floors(path, floors)
    return path


def format_floor_breaches(comparison: FloorComparison) -> str:
    lines: list[str] = []
    for breach in comparison.breaches + comparison.aggregate_breaches:
        lines.append(
            f"  {breach.entry} / {breach.model}: {breach.metric} "
            f"floor={breach.floor:g} current={breach.current:g} "
            f"std={breach.std:g} tolerance={breach.tolerance:g}",
        )
    for name in comparison.unfloored_entries:
        lines.append(f"  {name}: no floor record")
    for note in comparison.stale_reasons:
        lines.append(f"  stale: {note}")
    return "\n".join(lines)
