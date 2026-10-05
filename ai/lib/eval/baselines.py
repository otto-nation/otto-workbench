"""What a finished eval session writes down and is judged against.

That is the session document (`build_output`), the per-backend, per-model
baseline files, the comparison against them, and the refusals that keep a
bad pass from overwriting a good baseline (`run_post_eval`). Not: running
arms (`eval.run`), the floors ratchet (`eval.floors`), metrics and tables
(`eval.scoring`), argument parsing (`cli.eval_models`).
"""

# doc-group: eval

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import agent.backend
import eval.floors
import eval.scoring


def _serialize_run(r: eval.scoring.ScoringResult) -> dict:
    return {
        "outcome": r.outcome.value,
        "recall": r.recall,
        "precision": r.precision,
        "severity_accuracy": r.severity_accuracy,
        "false_positive_count": r.false_positive_count,
        "false_positive_ok": r.false_positive_ok,
        "cost_usd": r.cost_usd,
        "duration_ms": r.duration_ms,
        "billed_input": r.billed_input,
        "output_tokens": r.output_tokens,
        "matched_ids": [m.matched_finding_id for m in r.matches if m.matched],
        "false_positive_ids": r.false_positive_ids,
    }


_UNRESOLVED_MODEL_LABELS = frozenset({"", "(default)"})


def backend_label() -> str | None:
    """The selected backend's filename token, or None when nothing is selected.

    None is a missing identity, not the string "None": a file named
    ``None-sonnet.json`` would look like a recorded backend and compare as one.
    """
    selected = agent.backend.selected_backend()
    if selected is None:
        return None
    return selected.value


def _baseline_filename(backend: str, model_label: str) -> str:
    """``{backend}-{sanitized-model}.json``, e.g. ``claude-sonnet.json``."""
    return f"{eval.floors.baseline_stem(backend, model_label)}.json"


def _unresolved_model_labels(output: dict) -> list[str]:
    """Model keys that are still the empty/--models-omitted placeholder."""
    labels: list[str] = []
    for models in output.get("entries", {}).values():
        labels.extend(models)
    return sorted({label for label in labels if label in _UNRESOLVED_MODEL_LABELS})


def _baseline_document(
    model_label: str,
    effort: str,
    runs_per_entry: int,
    entry_results: dict[str, dict],
    backend: str,
) -> dict:
    """A per-backend, per-model baseline file: schema 3, entries[name] -> metrics."""
    return {
        "schema_version": eval.scoring.SCHEMA_VERSION,
        "backend": backend,
        "model": model_label,
        "effort": effort,
        "runs_per_entry": runs_per_entry,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entry_results,
    }


def _save_baselines(
    output: dict,
    results_dir: str,
) -> list[str]:
    results_path = Path(results_dir)
    results_path.mkdir(parents=True, exist_ok=True)
    backend = output["backend"]

    models_data: dict[str, dict[str, dict]] = {}
    for entry_name, entry_models in output.get("entries", {}).items():
        for model_label, model_data in entry_models.items():
            models_data.setdefault(model_label, {})[entry_name] = model_data

    written = []
    for model_label, entry_data in sorted(models_data.items()):
        baseline = _baseline_document(
            model_label, output["effort"], output["runs_per_entry"],
            entry_data, backend,
        )
        filepath = results_path / _baseline_filename(backend, model_label)
        filepath.write_text(json.dumps(baseline, indent=2) + "\n")
        written.append(str(filepath))

    return written


def _load_baselines(results_dir: str, backend: str | None) -> dict[str, dict]:
    """Load baselines recorded on ``backend``. Other backends are not compared."""
    results_path = Path(results_dir)
    baselines: dict[str, dict] = {}
    if not backend or not results_path.is_dir():
        return baselines
    for f in sorted(results_path.glob("*.json")):
        if f.name == eval.floors.FLOORS_FILENAME:
            continue
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: skipping {f}: {e}", file=sys.stderr)
            continue
        if data.get("backend") != backend:
            continue
        model = data.get("model")
        if model is None:
            model = f.stem
            print(
                f"warning: {f}: missing 'model' field, using filename '{model}' as label",
                file=sys.stderr,
            )
        baselines[model] = data
    return baselines


def build_output(
    all_results: dict[tuple[str, str, str], list[eval.scoring.ScoringResult]],
    effort: str,
    runs_per_entry: int,
) -> dict:
    entries: dict[str, dict] = {}
    for (entry_name, model_label, condition), results in all_results.items():
        model_out = entries.setdefault(entry_name, {}).setdefault(model_label, {})
        agg = eval.scoring.aggregate_runs(results)
        model_out[condition] = {
            **agg,
            "runs": [_serialize_run(r) for r in results],
        }
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": eval.scoring.SESSION_SCHEMA_VERSION,
        "effort": effort,
        "runs_per_entry": runs_per_entry,
        "entries": entries,
    }


def _full_arm_row(data) -> dict:
    if isinstance(data, dict) and "full" in data and "recall_mean" not in data:
        return data["full"]
    return data


def _flatten_full_arm(output: dict) -> dict:
    """Session output nests condition; save/compare stay entries[name][model]."""
    entries: dict[str, dict] = {}
    for name, models in output.get("entries", {}).items():
        for model, data in models.items():
            entries.setdefault(name, {})[model] = _full_arm_row(data)
    flat = dict(output)
    flat["entries"] = entries
    flat["schema_version"] = eval.scoring.SCHEMA_VERSION
    return flat


def _missing_full_pair(name: str, model: str, data) -> list[tuple[str, str]]:
    row = _full_arm_row(data)
    if not isinstance(row, dict) or "recall_mean" not in row:
        return [(name, model)]
    return []


def _entries_without_full_arm(output: dict) -> list[tuple[str, str]]:
    """(entry, model) pairs whose flattened row is not a full-arm metric dict."""
    missing: list[tuple[str, str]] = []
    for name, models in output.get("entries", {}).items():
        for model, data in models.items():
            missing.extend(_missing_full_pair(name, model, data))
    return missing


def _resolve_results_dir(args: argparse.Namespace, repo_root: Path | None) -> str:
    """Relative --results-dir is only resolved when a checkout exists."""
    results_dir = args.results_dir
    if not Path(results_dir).is_absolute():
        results_dir = str(repo_root / results_dir)
    return results_dir


def _pluralize(word: str, count: int) -> str:
    """Pluralize an English noun ending in a consonant + "y" (e.g. "entry")."""
    return word if count == 1 else word[:-1] + "ies"


def run_post_eval(
    args: argparse.Namespace, output: dict, repo_root: Path | None,
) -> int:
    output = _flatten_full_arm(output)
    exit_code = 0
    accepts, accept_err = _accepts_from_args(args)
    if accept_err:
        print(f"error: {accept_err}", file=sys.stderr)
        return 3

    floor_regression = False
    if args.compare or args.save_baselines:
        exit_code = _run_comparison(args, output, repo_root)
    if args.save_baselines:
        floor_code = _run_floors_gate(args, output, repo_root, accepts)
        if floor_code:
            exit_code = floor_code
            floor_regression = True

    if not args.save_baselines:
        return exit_code

    if exit_code != 0:
        if floor_regression:
            print(
                "warning: skipping baseline save — floor regression detected "
                "(pass --accept-regression to allow it)",
                file=sys.stderr,
            )
        else:
            print(
                "warning: skipping baseline save — comparison regression detected "
                "(check the model/corpus)",
                file=sys.stderr,
            )
        return exit_code

    # A trimmed-only session nests condition and has no full arm. Flattening
    # that document is identity, so a save would overwrite the schema-3 ratchet
    # with a nested file labelled schema 3. Refuse rather than record.
    missing_full = _entries_without_full_arm(output)
    if missing_full:
        print(
            "error: refusing to save baselines — session has no full arm "
            "(the canonical baseline is the full-arm ratchet):",
            file=sys.stderr,
        )
        for entry, model in missing_full:
            print(f"  {entry} / {model}", file=sys.stderr)
        return 3

    # A baseline is written wholesale from one pass, so a single window of
    # backend failures would replace a good file with zeros and nothing
    # downstream would catch it: validate-eval-baselines checks coverage, not
    # plausibility. Refuse rather than record — the runs can be re-attempted,
    # the overwritten baseline cannot.
    unresolved = _unresolved_model_labels(output)
    if unresolved:
        print(
            "error: refusing to save baselines — model was not resolved "
            "(pass --models, or the session log must name exactly one served model):",
            file=sys.stderr,
        )
        for model in unresolved:
            print(f"  {model or '(empty)'}", file=sys.stderr)
        return 3

    backend = output.get("backend") or backend_label()
    if not backend:
        print(
            "error: refusing to save baselines — no backend selected",
            file=sys.stderr,
        )
        return 3
    output["backend"] = backend

    incomplete = eval.scoring.incomplete_entries(output)
    if incomplete:
        print(
            f"error: refusing to save baselines — {len(incomplete)} "
            f"{_pluralize('entry', len(incomplete))} had runs that never executed:",
            file=sys.stderr,
        )
        for entry, model, measured, attempted in incomplete:
            print(f"  {entry} / {model}: {measured}/{attempted} runs measured", file=sys.stderr)
        print(
            "  re-run the full corpus before saving — --save-baselines rebuilds each "
            "model's file wholesale, so a --entry re-run would drop every other entry",
            file=sys.stderr,
        )
        return 3

    results_dir = _resolve_results_dir(args, repo_root)
    missing_floors = eval.floors.floors_absent_with_baselines(results_dir)
    if missing_floors and not getattr(args, "seed_floors", False):
        print(
            "error: refusing to save baselines — floors.json is absent "
            "while baselines exist; pass --seed-floors to reseed from this run",
            file=sys.stderr,
        )
        return 1
    if missing_floors:
        print(eval.floors.SEED_FLOORS_WARNING, file=sys.stderr)
    written = _save_baselines(output, results_dir)
    for path in written:
        print(f"Baseline written: {path}", file=sys.stderr)
    floors_file = eval.floors.ratchet_floors(output, results_dir, accepts)
    print(f"Floors written: {floors_file}", file=sys.stderr)

    return exit_code


def _accepts_from_args(args: argparse.Namespace) -> tuple[list, str]:
    specs = list(getattr(args, "accept_regression", None) or [])
    if not specs:
        return [], ""
    parsed = []
    for spec in specs:
        try:
            parsed.append(eval.floors.parse_accept_regression(spec))
        except ValueError as exc:
            return [], str(exc)
    return parsed, ""


def _run_floors_gate(
    args: argparse.Namespace, output: dict, repo_root: Path | None, accepts: list,
) -> int:
    code, messages = eval.floors.save_floor_gate_messages(
        _resolve_results_dir(args, repo_root), output, accepts,
    )
    for message in messages:
        print(message, file=sys.stderr)
    return code


def _run_comparison(
    args: argparse.Namespace, output: dict, repo_root: Path | None,
) -> int:
    results_dir = _resolve_results_dir(args, repo_root)
    backend = output.get("backend") or backend_label()
    baselines = _load_baselines(results_dir, backend)
    if not baselines:
        print("\nNo baselines found for comparison.", file=sys.stderr)
        return 0
    comparison = eval.scoring.compare_baselines(baselines, output)
    # The whole table goes out, gated metrics and ungated alike — a gate that
    # hides the numbers it did not act on trains people to ignore it.
    print("\n" + eval.scoring.format_comparison_table(comparison))

    unmeasured = comparison["unmeasured_entries"]
    if unmeasured:
        print(
            f"\n{len(unmeasured)} {_pluralize('entry', len(unmeasured))} produced no "
            "measurement and were not compared:",
            file=sys.stderr,
        )
        for entry, model in unmeasured:
            print(f"  {entry} / {model}", file=sys.stderr)

    regressions = comparison["regressions"]
    if not regressions:
        return 0
    print(f"\n{len(regressions)} regression(s) detected:", file=sys.stderr)
    for entry, model, metric, delta in regressions:
        print(f"  {entry} / {model}: {metric} {delta:+,.2f}", file=sys.stderr)
    return 2
