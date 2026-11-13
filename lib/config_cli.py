"""Read and write the workbench config, checked against the keys it reads.

Usage: python3 lib/config_cli.py set KEY VALUE [--project|--container]
       otto-workbench config set KEY VALUE [--project|--container]
       otto-workbench config model PHASE|TIER MODEL [--project|--container]
       otto-workbench config model PHASE|TIER --unset [--project|--container]
       otto-workbench config status
       otto-workbench config get KEY [DIR ...]
       otto-workbench config models
       otto-workbench config phases [--json] [DIR]
       otto-workbench config context [--pi-listing FILE]
       python3 lib/config_cli.py pi-catalogue
       python3 lib/config_cli.py run-bounded [--tier NAME] [--relay] -- CMD...

`get` is the read side for a caller that is not Python.  `ai/lib/
workbench_config.py` owns which files a key is resolved from; bash has no
container-aware resolver of its own, and the two partial ones it used to carry
disagreed with the typed loader about the same repo in the same session — the
machine profile printed `unset` for a tracker recorded above the worktrees
while the SessionStart line named it.  So there is one resolver and this is how
the other language reaches it.

One line per DIR, in the order given, three tab-separated fields:

    SCOPE <TAB> VALUE <TAB> DIR

SCOPE is the name of the file that answered — `project`, `container`, `global`
— or `default` when no file did.  VALUE is the resolved value, empty when
nothing is set; it is collapsed onto one line so a caller reading records with
`read` cannot have a row split under it.  DIR is echoed back last so a batch
caller matches answers to repos by name rather than by position, and so a value
holding a tab could still only ever confuse itself.

Every record has both tabs, including the one whose VALUE is empty.  That is
the shape a caller has to split on rather than tokenise: a tab is an IFS
whitespace character, so `IFS=$'\t' read -r scope value dir` folds the two
adjacent tabs of an unset key into one and silently reads the directory as the
value.  `lib/config.sh`'s `wb_config_split_record` is the bash side done
correctly, and is what a bash caller should use.

Each DIR is a repo's work-tree root, and each is resolved on its own: a
directory that is gone, holds no config, or holds one nothing can parse
resolves to the built-in default rather than failing the batch.  A report over
other people's repos cannot stop because one of them has a bad file; `config
status`, run in that repo, is what names it.  With no DIR the caller's own
work-tree root is used, which is what `wb_config_get` asks for.

`status` answers the two questions the files themselves cannot: what is the
value right now, and which file supplied it.  The loader deep-merges every
scope and returns the result, so a value inherited from the machine-wide file
and a value set by the repo in front of you look identical afterwards — and a
key written under a name nothing reads looks exactly like a key nobody ever
set, in both directions, for as long as it takes somebody to notice the rule
it was meant to turn on is not applying.

`set` writes one of the three scopes `status` reports: the machine-wide file by
default, `--project` for the checkout's committed `.workbench.yml`, and
`--container` for the file beside a bare repo's worktrees, which every one of
them reads and `wt remove` cannot delete.  Every scope the report can show has
a flag that writes it — a scope a reader can see and not set is a diagnosis
with no fix at the end of it.

The config files are hand-authorable and the schema modeline is there so an
editor completes them, so for a person this command is a convenience.  For an
agent it is the difference between recording an answer and losing one.

An agent asked to record something — where a repo files its issues, which reuse
level to run at — reads the key out of the checkout it is working in.  A
worktree is routinely weeks behind `main`; that is what worktrees are for.  So
the key it reads is correct for the code in front of it and may be a name the
config moved off two hours ago, and the file it writes is shared by every repo
on the machine.  Nothing about the write says so: `serde` drops a key it does
not know, so the value is simply absent the next time anything looks, and the
rule that depended on it quietly stops applying.

Invoked by its bare name this command *is* the installed workbench — the
launcher on `PATH` is a symlink into the checkout that installed it — so the
key surface it validates against is the current one no matter which worktree
the caller is standing in.  Run out of a stale checkout instead, it still
refuses: `workbench_config_write.check_key` asks the installed schema as well as
its own.  See `ai/lib/config/workbench_config_write.py`.

Exit codes: 0 on a completed write, a report that read every scope, or a `get`
that answered for every DIR; 1 on a refused key, a failed write, or a scope
`status` could not read; 2 on a usage error (argparse's own).

`get` refuses a key this checkout does not define and answers for every other
one, including a DIR whose files it could not read — the two halves of the same
rule.  A key nothing reads is a caller asking a question with no answer, and a
fallback there hides a typo forever; an unreadable file already resolves to the
built-in default everywhere else in the workbench, so reporting that is the
truth rather than a degrade.  Unlike `set`, only this checkout's key surface is
consulted: see `workbench_config.defines_key`.

A stray key is reported and still exits 0.  It is the finding this command
exists for, but the command's job is to show the config, and a report that
fails because the thing it reported is bad is one nobody can put in a script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
_WORKBENCH_DIR = os.path.dirname(_LIB_DIR)
for _path in (_LIB_DIR, os.path.join(_WORKBENCH_DIR, 'ai', 'lib')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import agent.context_ceiling  # noqa: E402
import agent.harness_models  # noqa: E402
import agent.models  # noqa: E402
import agent.phase_report  # noqa: E402
import agent.phases  # noqa: E402
import agent.pi_models  # noqa: E402
import config.workbench_config  # noqa: E402
import config.workbench_config_report  # noqa: E402
import config.workbench_config_write  # noqa: E402
import core.errors  # noqa: E402
import core.proc  # noqa: E402
import core.timeouts  # noqa: E402
from core.phases import Phase, Tier  # noqa: E402
from ansi import BOLD, DIM, GREEN, NC, RED, YELLOW  # noqa: E402
import git_layout  # noqa: E402


def _project_root() -> Path | None:
    """The work-tree root of the repo the caller is in, or ``None`` outside one.

    A bare-repo container is resolved to the checkout on its default branch.
    Run from one, `rev-parse --show-toplevel` exits 128 and every scope below
    the global one disappears — so `config get` reported the machine's answer
    for a repo that had its own, and `config status` showed the repo as having
    said nothing.  A container naming no worktree stays ``None``: there is no
    checkout to read a config out of.
    """
    root = git_layout.project_root()
    return Path(root) if root else None


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="otto-workbench config",
        description="Write one key of the workbench config, checked before it lands.",
    )
    sub = parser.add_subparsers(required=True, metavar="command")
    setter = sub.add_parser("set", help="write one dotted key")
    setter.add_argument("key", help="dotted key, e.g. issues.provider")
    setter.add_argument("value", help="the value to record")
    scope = setter.add_mutually_exclusive_group()
    scope.add_argument(
        "--project", action="store_true",
        help="write the current repo's .workbench.yml instead of the global file",
    )
    scope.add_argument(
        "--container", action="store_true",
        help="write the .workbench.yml above a bare repo's worktrees",
    )
    setter.set_defaults(run=_write)
    modeler = sub.add_parser(
        "model", help="set or clear the model for one phase or tier, checked",
    )
    modeler.add_argument("target", help="a phase (scout) or a tier (standard)")
    modeler.add_argument(
        "ref", nargs="?", help="[provider/]id[:level] or a tier name; omit with --unset",
    )
    modeler.add_argument("--unset", action="store_true", help="remove the setting")
    mscope = modeler.add_mutually_exclusive_group()
    mscope.add_argument("--project", action="store_true",
                        help="write the current repo's .workbench.yml")
    mscope.add_argument("--container", action="store_true",
                        help="write the .workbench.yml above a bare repo's worktrees "
                             "(the scope for trying models out)")
    modeler.set_defaults(run=_model)
    reporter = sub.add_parser(
        "status", help="show every scope, every value, and the file it came from",
    )
    reporter.set_defaults(run=_status)
    reader = sub.add_parser(
        "get", help="resolve one dotted key, for this repo or for each named one",
    )
    reader.add_argument("key", help="dotted key, e.g. issues.provider")
    reader.add_argument(
        "dir", nargs="*", metavar="DIR",
        help="a repo's work-tree root; defaults to the caller's own",
    )
    reader.set_defaults(run=_get)
    harness = sub.add_parser(
        "models", help="print the model tiers as Claude Code and Pi settings, as JSON",
    )
    harness.set_defaults(run=_models)
    phases = sub.add_parser(
        "phases", help="show each phase's tier, model and thinking level, and what set them",
    )
    phases.add_argument(
        "--json", action="store_true", help="print one JSON object instead of a table",
    )
    phases.add_argument(
        "dir", nargs="?", metavar="DIR",
        help="a repo's work-tree root; defaults to the caller's own",
    )
    phases.set_defaults(run=_phases)
    context = sub.add_parser(
        "context", help="print context.compact_at as Claude Code and Pi settings, as JSON",
    )
    context.add_argument(
        "--pi-listing", metavar="FILE",
        help="a `pi --list-models` listing (- for stdin) to size Pi's per-model reserves from",
    )
    context.set_defaults(run=_context)
    catalogue = sub.add_parser(
        "pi-catalogue",
        help="print the bounded `pi --list-models` listing",
    )
    catalogue.set_defaults(run=_pi_catalogue)
    bounded = sub.add_parser(
        "run-bounded",
        help="run a command under a timeouts tier; stdout is discarded unless --relay",
    )
    bounded.add_argument(
        "--tier",
        default="REFRESH",
        choices=_BOUNDED_TIERS,
        help="timeouts tier (default: REFRESH)",
    )
    bounded.add_argument(
        "--relay",
        action="store_true",
        help="stream the command's output to stderr as it is written",
    )
    bounded.add_argument(
        "cmd",
        nargs="+",
        metavar="CMD",
        help="command to run, after --",
    )
    bounded.set_defaults(run=_run_bounded)
    return parser.parse_args(argv)


# ``render_value`` writes "nothing is set" as an em dash, which is a reading for
# a person looking at a table. A record another program parses says it with an
# empty value field, so the only marker in the output is the caller's own — the
# machine profile's "unset", a bash caller's fallback.
_NO_VALUE = config.workbench_config_report.render_value(None)


def _one_line(value: str) -> str:
    """One value as a field of a one-line, tab-separated record.

    Nothing the config surface types can hold a newline or a tab today, so the
    collapse is what keeps that from being load-bearing: a key that grows into
    a free-text string later cannot split the row a caller is reading.
    """
    return " ".join(value.split())


def _resolve(key: str, project_root) -> tuple[str, str]:
    """The scope that answered for ``key`` at ``project_root``, and its value.

    ``config_status`` already collects a file it could not read rather than
    raising, and returns no rows at all when the merged config will not type.
    Both leave the built-in default standing, which is what every other reader
    on the machine gets from ``load_config_or_default`` — so both report
    ``DEFAULT_SCOPE`` here rather than an error the batch would have to carry.
    """
    try:
        status = config.workbench_config_report.config_status(project_root)
    except (config.workbench_config.ConfigError, OSError):
        return config.workbench_config.DEFAULT_SCOPE, ""
    row = next((entry for entry in status.keys if entry.key == key), None)
    if row is None:
        return config.workbench_config.DEFAULT_SCOPE, ""
    value = _one_line(row.value)
    if value == _NO_VALUE:
        value = ""
    if row.is_default:
        return config.workbench_config.DEFAULT_SCOPE, value
    return row.scope.name, value


def _get(args: argparse.Namespace) -> int:
    """Print one record per DIR: the scope that answered, the value, the DIR."""
    if not config.workbench_config.defines_key(args.key):
        raise config.workbench_config.ConfigKeyError(
            f"{args.key} is not a key WorkbenchConfig defines",
        )
    for target in args.dir or [_project_root()]:
        scope, value = _resolve(args.key, target)
        print(f"{scope}\t{value}\t{target if target is not None else ''}")
    return 0


def _models(_args: argparse.Namespace) -> int:
    """Print the machine-wide tiers as each harness's settings, for the syncs.

    Machine-wide only: a harness's settings are the machine's, so no repo the
    sync happens to run from may move them. A key nothing names is absent from
    `pi`, because Pi's sync overlays that object on the live file and an absent
    key is one it leaves as it is. `claude.tiers` holds each tier that names a
    Claude model, as the `model` and `effort` an agent declaring that tier is
    installed with; a tier absent there installs its agents as `inherit`.
    `claude.tier_names` is every tier an agent may declare, so sync can tell a
    tier that names no model from one that does not exist. `tiers` is each set
    tier's reference, for the Pi model router; neither sync reads it.
    """
    cfg = config.workbench_config.load_config(None)
    view = agent.harness_models.harness_models(cfg.models)
    claude, pi = view.claude, view.pi
    pi_keys = {
        "defaultModel": pi.default_model,
        "defaultProvider": pi.default_provider,
        "defaultThinkingLevel": pi.default_thinking,
        "enabledModels": pi.enabled or None,
        "modelThinkingLevels": pi.thinking_levels or None,
    }
    print(json.dumps({
        "claude": {
            "env": claude.env,
            "managed_env": list(agent.harness_models.CLAUDE_MANAGED_ENV),
            "model": claude.model,
            "effortLevel": claude.effort,
            "skipped": claude.skipped,
            "tiers": {
                tier.value: {"model": entry.model, "effort": entry.effort}
                for tier, entry in claude.tiers.items()
            },
            "tier_names": [tier.value for tier in agent.harness_models.Tier],
        },
        "pi": {key: value for key, value in pi_keys.items() if value is not None},
        "tiers": {
            tier.value: {"provider": ref.provider, "id": ref.id, "level": ref.level}
            for tier, ref in view.tiers.items()
        },
    }, separators=(",", ":")))
    return 0


def _context(args: argparse.Namespace) -> int:
    """Print the machine-wide ``context.compact_at`` as each harness's settings.

    `compact_at` is the ceiling as written, null when unset.
    `claude.autoCompactWindow` is null when the ceiling is unset, which the sync
    reads as "leave Claude Code's own"; `claude.skipped` explains a ceiling the
    CLI cannot take as written. `pi.reserveTokens` maps each listed
    `provider/id` to the reserve that compacts it at the ceiling, or to null
    where its window is already at or below it. It is empty when the ceiling is
    unset or no listing was given, and the sync then leaves Pi's file alone.
    Machine-wide only, for the reason `config models` gives.
    """
    compact_at = config.workbench_config.load_config(None).context.compact_at
    claude = agent.context_ceiling.claude_window(compact_at)
    reserves: dict[str, int | None] = {}
    if compact_at is not None and args.pi_listing:
        listing = sys.stdin.read() if args.pi_listing == "-" else Path(args.pi_listing).read_text()
        reserves = agent.context_ceiling.pi_reserve_tokens(compact_at, listing)
    print(json.dumps({
        "compact_at": compact_at,
        "claude": {
            "autoCompactWindow": claude.window,
            "skipped": [claude.note] if claude.note else [],
        },
        "pi": {"reserveTokens": reserves},
    }, separators=(",", ":")))
    return 0


_BOUNDED_TIERS = tuple(
    name
    for name, value in vars(core.timeouts).items()
    if name.isupper() and isinstance(value, float)
)


def _pi_catalogue(_args: argparse.Namespace) -> int:
    """Print the bounded ``pi --list-models`` listing, or the reason it cannot be read.

    Stdout is the listing and nothing else; a failure writes ``str(exc)`` to
    stderr and exits 1, so a shell caller can capture each stream. stdin is
    not inherited: ``catalogue_text`` runs through ``core.proc.run``, which
    opens the child on ``DEVNULL``.
    """
    try:
        sys.stdout.write(agent.pi_models.catalogue_text(("pi",)))
    except agent.pi_models.CatalogueUnreadable as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _run_bounded(args: argparse.Namespace) -> int:
    """Run CMD under a timeouts tier, discarding its stdout unless ``--relay``.

    Success exits 0 with nothing on either stream. A failure, including an
    expired bound, writes a one-line reason to stderr and exits 1. stdin is
    not inherited: ``core.proc.run`` opens the child on ``DEVNULL``. The
    child is its own process group so an expired bound SIGTERMs git/npm and
    SIGKILLs after ``TERM_GRACE``, which is how git drops its locks.

    ``--relay`` copies each line the command writes, from either stream, to
    stderr as it arrives, so a caller whose log is the only record of a long
    command sees its warnings instead of a 200-character failure summary.

    A stop signal is answered here, not left to its default: the child is in
    a session of its own, so an outer bound that SIGTERMs this process would
    otherwise end it and leave the child running with nothing watching it.
    The handler's ``SystemExit`` unwinds through ``core.proc.run``, which
    kills the child's group on the way out.
    """
    core.proc.install_stop_handler(lambda: None)
    result = core.proc.run(
        list(args.cmd),
        timeout=getattr(core.timeouts, args.tier),
        kill_process_group=True,
        relay=sys.stderr if args.relay else None,
    )
    if result.ok:
        return 0
    print(core.proc.failure_message(" ".join(args.cmd), result), file=sys.stderr)
    return 1


def _phases(args: argparse.Namespace) -> int:
    """Print every tier and registry phase as this repo's config resolves them.

    The answer is `agent.phase_report`'s, built on the resolver every phase runs
    through, so a caller reads the precedence chain instead of re-implementing
    it. The JSON shape is `PhaseReport.as_json`'s, documented on
    `agent.phase_report`. Unset tiers and a phase that cannot resolve are both
    data and exit 0; a config that cannot be read raises `ConfigError`, which
    `main` turns into exit 1 with nothing on stdout.
    """
    root = Path(args.dir) if args.dir else _project_root()
    report = agent.phase_report.phase_report(root)
    if args.json:
        print(json.dumps(report.as_json(), separators=(",", ":")))
    else:
        _print_phase_table(report)
    return 0


def _describe(source: agent.phase_report.Source) -> str:
    """One source as a reader edits it: ``KEY (scope)``, then ``→`` the tier it borrowed."""
    if source.layer == agent.phase_report.UNSET_LAYER:
        return "unset"
    text = f"{source.key or source.layer} ({source.scope})"
    return f"{text} → {_describe(source.via)}" if source.via is not None else text


def _print_phase_table(report: agent.phase_report.PhaseReport) -> None:
    print(f"{BOLD}Tiers{NC}")
    width = max(len(row.tier.value) for row in report.tiers)
    for row in report.tiers:
        print(f"  {row.tier.value:<{width}}  {row.value or _NO_VALUE}  {DIM}{row.scope or 'unset'}{NC}")

    print(f"\n{BOLD}Phases{NC}")
    width = max(len(row.phase.value) for row in report.phases)
    tier_width = max(len(row.tier.value) for row in report.phases)
    for row in report.phases:
        head = f"  {row.phase.value:<{width}}  {row.tier.value:<{tier_width}}"
        if row.error is not None:
            print(f"{head}  {RED}✗{NC} {row.error}")
            continue
        model = row.model.spec() if row.model is not None else _NO_VALUE
        print(f"{head}  {model}  {DIM}{_describe(row.model_source)}{NC}  "
              f"thinking {row.thinking or _NO_VALUE}  {DIM}{_describe(row.thinking_source)}{NC}")


def _status(_args: argparse.Namespace) -> int:
    """Print every scope, every resolved value, and the file each came from.

    All of it on stdout, including the problems. The exit code is what a script
    reads, and splitting the report across two streams only guarantees that the
    line explaining why a section is missing lands somewhere other than where
    the section would have been.
    """
    status = config.workbench_config_report.config_status(_project_root())

    print(f"{BOLD}Scopes{NC} {DIM}— highest precedence first{NC}")
    name_width = max(len(scope.name) for scope in status.scopes)
    for scope in status.scopes:
        mark = f"{GREEN}✓{NC}" if scope.exists else f"{DIM}·{NC}"
        note = "" if scope.exists else f"  {DIM}(no file){NC}"
        print(f"  {mark} {scope.name:<{name_width}} {scope.path}{note}")

    for problem in status.problems:
        print(f"  {RED}✗{NC} {problem}")

    if status.keys:
        key_width = max(len(row.key) for row in status.keys)
        value_width = max(len(row.value) for row in status.keys)
        print(f"\n{BOLD}Values{NC}")
        for row in status.keys:
            source = (config.workbench_config.DEFAULT_SCOPE if row.is_default
                      else row.scope.name)
            tint = DIM if row.is_default else NC
            print(f"  {row.key:<{key_width}}  {row.value:<{value_width}}  "
                  f"{tint}{source}{NC}")

    if status.strays:
        print(f"\n{BOLD}Keys nothing reads{NC} {DIM}— the value is dropped on load{NC}")
        width = max(len(stray.key) for stray in status.strays)
        for stray in status.strays:
            print(f"  {YELLOW}✗{NC} {stray.key:<{width}}  {DIM}{stray.scope.path}{NC}")

    if status.dropped:
        print(f"\n{BOLD}Values nothing can read{NC} "
              f"{DIM}— the key is real, the value is not{NC}")
        width = max(len(row.key) for row in status.dropped)
        for row in status.dropped:
            print(f"  {YELLOW}✗{NC} {row.key:<{width}}  "
                  f"file says {row.held}, loads as {row.read}  "
                  f"{DIM}{row.scope.path}{NC}")

    return 0 if status.ok else 1


def _write(args: argparse.Namespace) -> int:
    if not (args.project or args.container):
        config.workbench_config_write.set_value(args.key, args.value)
        return _wrote(args, config.workbench_config.global_config_path(), "every repo")

    root = _project_root()
    if root is None:
        flag = "--container" if args.container else "--project"
        print(f"{RED}✗{NC} {flag} needs a git repo, and this is not one", file=sys.stderr)
        return 1
    if args.container:
        return _write_container(args, root)
    config.workbench_config_write.set_project_value(args.key, args.value, root)
    return _wrote(args, config.workbench_config.project_config_path(root),
                  "commit it so the repo keeps the answer")


def _model_key(target: str, ref: str | None) -> str:
    """The config key *target* names, after checking *ref* is fit for it.

    A phase writes ``agent.phases.<phase>.model`` and a tier ``models.<tier>``.
    A review phase is held to the review floor, and so is the standard or deep
    tier, since the review phases read their model from them; the light tier
    may be anything.
    """
    try:
        phase = Phase(target)
    except ValueError:
        phase = None
    if phase is not None:
        if ref is not None:
            agent.phases.phase_model_overrides([f"{target}={ref}"], source="config model")
        return f"agent.phases.{target}.model"
    try:
        tier = Tier(target)
    except ValueError:
        phases = ", ".join(p.value for p in Phase)
        tiers = ", ".join(t.value for t in Tier)
        raise config.workbench_config.ConfigError(
            f"'{target}' is neither a phase ({phases}) nor a tier ({tiers})") from None
    if ref is not None:
        if ref in {t.value for t in Tier}:
            raise config.workbench_config.ConfigError(
                f"models.{target} takes a model id, not the tier name '{ref}'")
        parsed = agent.models.parse_ref(ref)
        if tier is not Tier.LIGHT and agent.phases.is_light_model(parsed.id):
            raise config.workbench_config.ConfigError(
                f"models.{target} cannot be '{parsed.id}': Haiku is too light, "
                "and review phases read their model from this tier")
    return f"models.{target}"


def _model(args: argparse.Namespace) -> int:
    if args.unset == (args.ref is not None):
        print(f"{RED}✗{NC} give a model to set, or --unset to clear, not both or neither",
              file=sys.stderr)
        return 2
    root = None
    if args.project or args.container:
        root = _project_root()
        if root is None:
            print(f"{RED}✗{NC} --project and --container need a git repo", file=sys.stderr)
            return 1
    key = _model_key(args.target, args.ref)
    if root is not None:
        target = (config.workbench_config.container_config_path(root) if args.container
                  else config.workbench_config.project_config_path(root))
        if target is None:
            print(f"{RED}✗{NC} --container needs a bare-repo worktree", file=sys.stderr)
            return 1
    else:
        target = config.workbench_config.global_config_path()
    if args.unset:
        removed = config.workbench_config_write.unset_value(key, target)
        print(f"{GREEN}✓{NC} {key} " + ("removed" if removed else "was not set"))
        print(f"  {DIM}{target}{NC}")
        return 0
    args.key, args.value = key, args.ref
    return _write(args)


def _write_container(args: argparse.Namespace, root: Path) -> int:
    """Write the file above a bare repo's worktrees, or say there is none.

    The path is resolved here rather than left to ``set_container_value``'s own
    refusal so the message can name the flag the caller passed. The write still
    goes through that function, which is where the scope's rules live.
    """
    target = config.workbench_config.container_config_path(root)
    if target is None:
        print(f"{RED}✗{NC} --container needs a bare-repo worktree — {root} is a "
              f"plain checkout, whose repo has no container", file=sys.stderr)
        return 1
    config.workbench_config_write.set_container_value(args.key, args.value, root)
    return _wrote(args, target, "every worktree of this repo")


def _wrote(args: argparse.Namespace, target: Path, reach: str) -> int:
    print(f"{GREEN}✓{NC} {args.key} = {args.value}")
    print(f"  {DIM}{target} — {reach}{NC}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return args.run(args)
    except config.workbench_config.ConfigKeyError as exc:
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        print(
            f"  {DIM}every key the config accepts is listed at "
            f"{config.workbench_config.SCHEMA_URL}{NC}",
            file=sys.stderr,
        )
        return 1
    except (config.workbench_config.ConfigError, core.errors.OperatorError,
            agent.models.UnsetTier,
            agent.harness_models.AutoNeedsTiers, agent.harness_models.AutoTakesNoLevel) as exc:
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
