"""One eval pass over the corpus.

It discovers the cases, seeds one rule tree per arm, runs each case through
the task its manifest names, prints the summary and A/B tables, and hands the
session to `eval.baselines`. Not: scoring (`eval.task` and its scorers),
seeding policy (`eval.conditions`), what may be written afterwards
(`eval.baselines`), argument parsing (`cli.eval_models`).
"""

# doc-group: eval

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
from itertools import product
from pathlib import Path

import agent.backend
import eval.baselines
import eval.conditions
import eval.scoring
import eval.task


def _no_match_message(entry_filter: str, task_filter: str) -> str:
    if entry_filter and not task_filter:
        return f"error: corpus entry not found: {entry_filter}"
    parts = []
    if entry_filter:
        parts.append(f"entry={entry_filter}")
    if task_filter:
        parts.append(f"task={task_filter}")
    return f"error: no corpus entries matching {', '.join(parts)}"


def discover_entries(corpus_dir: str, entry_filter: str, task_filter: str = "") -> list[dict]:
    corpus = Path(corpus_dir)
    if not corpus.is_dir():
        print(f"error: corpus directory not found: {corpus_dir}", file=sys.stderr)
        sys.exit(1)

    entries = []
    for d in sorted(corpus.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        name = manifest.get("name", d.name)
        if entry_filter and name != entry_filter:
            continue
        if task_filter and eval.task.task_name(manifest) != task_filter:
            continue
        src_dir = d / "src"
        if not src_dir.is_dir():
            print(f"warning: no src/ directory in {d.name}, skipping", file=sys.stderr)
            continue
        entries.append({
            "name": name,
            "manifest_path": str(manifest_path),
            "case_dir": str(d),
            "src_dir": str(src_dir),
            "manifest": manifest,
        })

    if not entries and (entry_filter or task_filter):
        print(_no_match_message(entry_filter, task_filter), file=sys.stderr)
        sys.exit(1)

    return entries


def _unique_served_model(usage) -> str:
    """The session-log model when exactly one served; else unresolved."""
    models = [m for m in usage.cost_by_model if m]
    if len(models) == 1:
        return models[0]
    return ""


def _cleanup(temp_dirs: list[str], keep: bool) -> None:
    for path in temp_dirs:
        if keep:
            print(f"  temp dir: {path}", file=sys.stderr)
        else:
            shutil.rmtree(path, ignore_errors=True)


def _report_run(artifacts, result: eval.scoring.ScoringResult) -> None:
    usage = artifacts.usage
    summary = artifacts.data.get("summary", "")
    print(
        f"  {summary + ', ' if summary else ''}"
        f"cost: ${usage.cost:.2f}, "
        f"duration: {usage.duration_ms / 1000:.0f}s",
        file=sys.stderr,
    )
    # A run that never executed has no score to report. Printing recall 0% for
    # it is exactly the confusion this guard exists to remove.
    if not result.measured:
        print(
            "  not scored — the invocation produced no work",
            file=sys.stderr,
        )
        return
    matched = sum(1 for m in result.matches if m.matched)
    # Without this note false_positives_max sets a field nobody reads: the budget
    # is recorded in the baseline JSON and never surfaces where a human looks.
    over = "" if result.false_positive_ok else " (over budget)"
    print(
        f"  recall: {result.recall:.0%} ({matched}/{len(result.matches)}), "
        f"FP: {result.false_positive_count}{over}",
        file=sys.stderr,
    )


def _run_arm(
    entry: dict, model: str, label: str, arm: str,
    args: argparse.Namespace, rules_home: str,
) -> list[eval.scoring.ScoringResult]:
    return [
        _run_single(
            entry, model, label, run_idx, args,
            arm, rules_home,
        )
        for run_idx in range(args.runs)
    ]


def _store_arm(
    all_results: dict[tuple[str, str, str], list],
    entry: dict, model: str, label: str, arm: str,
    args: argparse.Namespace, rules_home: str,
) -> None:
    """Run one arm and index each result by the model it actually recorded."""
    for result in _run_arm(entry, model, label, arm, args, rules_home):
        key = (entry["name"], result.model, arm)
        all_results.setdefault(key, []).append(result)


def _run_single(
    entry: dict, model: str, label: str, run_idx: int,
    args: argparse.Namespace,
    arm: str = "full", rules_home: str = "",
) -> eval.scoring.ScoringResult:
    run_label = f"{entry['name']} / {label}"
    if arm != "full":
        run_label += f" / {arm}"
    if args.runs > 1:
        run_label += f" (run {run_idx + 1}/{args.runs})"
    print(f"\n>> {run_label}", file=sys.stderr)

    manifest = entry["manifest"]
    task = eval.task.get_task(eval.task.task_name(manifest))
    opts = eval.task.RunOptions(
        model=model, effort=args.effort, timeout=args.timeout,
        verbose=args.verbose, condition=arm, rules_home=rules_home,
    )

    artifacts = task.run(Path(entry["case_dir"]), opts)
    try:
        if artifacts.exit_code != 0:
            print(f"  {task.name} exited {artifacts.exit_code}", file=sys.stderr)
        result = task.score(artifacts, manifest)
        recorded = label
        if not model:
            recorded = _unique_served_model(artifacts.usage) or label
        result = dataclasses.replace(
            result, entry_name=entry["name"], model=recorded,
            run_index=run_idx, condition=arm,
        )
        _report_run(artifacts, result)
        return result
    finally:
        _cleanup(artifacts.temp_dirs, args.keep_temp)


def _dry_run(entries: list[dict], runs_per_entry: int) -> tuple[dict, int]:
    """Describe what would run. Resolves each task so an unknown one fails here."""
    print("\n-- Dry run --", file=sys.stderr)
    for entry in entries:
        manifest = entry["manifest"]
        task = eval.task.get_task(eval.task.task_name(manifest))
        src_files = list(Path(entry["src_dir"]).iterdir())
        detail = f"{len(src_files)} source files, tags={manifest.get('tags', [])}"
        # Only review cases carry expectations; ci-fix is scored by its own command.
        if manifest.get("expected"):
            detail = f"{len(manifest['expected'])} expectations, {detail}"
        print(f"  {entry['name']} [{task.name}]: {detail}", file=sys.stderr)
    print(f"\nTotal runs: {len(entries) * runs_per_entry}", file=sys.stderr)
    return {}, 0


def _explicit_models(args: argparse.Namespace) -> list[str] | None:
    """`--models` as typed, or None to resolve each entry through its phase."""
    if not args.models:
        return None
    return [m.strip() for m in args.models.split(",")]


def _resolve_entry_model(entry: dict) -> str:
    """The production model the entry's task measures, chosen up front."""
    task = eval.task.get_task(eval.task.task_name(entry["manifest"]))
    return eval.task.resolved_model(task, "")


def _model_banner(explicit: list[str] | None, entries: list[dict]) -> list[str]:
    """Labels for the Models: line — resolved ids, never `(default)`."""
    if explicit is not None:
        return explicit
    labels: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        model = _resolve_entry_model(entry)
        if model not in seen:
            seen.add(model)
            labels.append(model)
    return labels


def _seed_kind() -> str:
    return "pi" if agent.backend.selected_backend() is agent.backend.Backend.PI else "claude"


def _seed_arms(repo_root: Path | None, tmpdir: Path, arms: list[str]) -> dict[str, Path]:
    """One seeded tree per arm. Fails loudly when the backend's source is missing."""
    try:
        source = eval.conditions.prepare_seed_source(
            _seed_kind(), tmpdir / "source", workbench_dir=repo_root,
        )
    except eval.conditions.MissingRuleSource as exc:
        sys.exit(f"error: {exc}")
    return {
        arm: eval.conditions.seed_config_tree(source, tmpdir / f"cc-{arm}", arm)
        for arm in arms
    }


def run_eval(args: argparse.Namespace, repo_root: Path | None) -> tuple[dict, int]:
    """Run one pass over the corpus.

    `repo_root` is the checkout that relative `--corpus` / `--results-dir`
    resolve against and that Pi rule layers seed from. It is `None` outside a
    checkout, in which case the caller has made both paths absolute.
    """
    corpus_dir = args.corpus
    if not Path(corpus_dir).is_absolute():
        corpus_dir = str(repo_root / corpus_dir)

    entries = discover_entries(corpus_dir, args.entry, args.task)
    explicit = _explicit_models(args)
    model_labels = _model_banner(explicit, entries)

    arms = [c.strip() for c in args.conditions.split(",")]
    for arm in arms:
        if arm not in eval.conditions.CONDITIONS:
            sys.exit(f"error: unknown condition: {arm}")

    print(f"Corpus: {len(entries)} entries", file=sys.stderr)
    print(f"Models: {', '.join(model_labels)}", file=sys.stderr)
    print(f"Conditions: {', '.join(arms)}", file=sys.stderr)
    print(f"Effort: {args.effort}, Runs: {args.runs}", file=sys.stderr)

    if args.dry_run:
        n_models = len(explicit) if explicit is not None else 1
        return _dry_run(entries, n_models * len(arms) * args.runs)

    with tempfile.TemporaryDirectory(prefix="eval-cc-") as tmpdir:
        seeded = _seed_arms(repo_root, Path(tmpdir), arms)

        all_results: dict[tuple[str, str, str], list] = {}
        cells = [
            (entry, model) for entry in entries
            for model in explicit or [_resolve_entry_model(entry)]
        ]
        for (entry, model), arm in product(cells, arms):
            _store_arm(
                all_results, entry, model, model, arm, args, str(seeded[arm]),
            )

        print("\n" + eval.scoring.format_summary_table(all_results))
        if len(arms) > 1:
            print("\n" + eval.scoring.format_ab_table(all_results))

        output = eval.baselines.build_output(all_results, args.effort, args.runs)
        output["backend"] = eval.baselines.backend_label()

        if args.output:
            Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
            print(f"\nResults written to {args.output}", file=sys.stderr)

        exit_code = eval.baselines.run_post_eval(args, output, repo_root)
        return output, exit_code
