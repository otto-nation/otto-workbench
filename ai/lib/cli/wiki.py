"""Mechanical operations over a compiled knowledge base.

Usage:
  wiki init   [--vault|--in-repo|--wiki DIR] [--domain TEXT] [--audience TEXT] [DIR]
  wiki path   [--wiki DIR] [DIR]
  wiki status [--wiki DIR] [--json] [DIR]
  wiki lint   [--wiki DIR] [--json] [--signals] [DIR]
  wiki signals [--wiki DIR] [--json] [DIR]
  wiki sources [--wiki DIR] [--new] [--json] [DIR]
  wiki index  [--wiki DIR] [--check] [DIR]
  wiki ingest --stage SRC [--type T] [--title TEXT] [--wiki DIR] [DIR]
  wiki archive SLUG [--force] [--wiki DIR] [DIR]

A knowledge base is a directory holding SCHEMA.md, an `articles/` tree of compiled
markdown, and a `raw/` tree of the immutable sources they were compiled from.
`_index.md`, `_sources.md`, and `_log.md` are the generated bookkeeping.

It lives in one of two places, and `init` asks which when a repo has not said.
In the repo, committed and shared with whoever clones it; or in this machine's
vault, private, outside every worktree, and one folder per repo. The vault is
found through `wiki.root` rather than by searching, so it reads the same from
every worktree of a repo and survives the worktree being removed.

This CLI owns only what is decidable without reading for meaning: link graphs,
word counts, file hashes, dates. Compiling a source into an article, answering a
question from the wiki, and judging whether two articles say the same thing are
the skill's work, not this script's. The division matters most for `sources`:
incremental compilation keys off the sha256 of each raw file, and asking a model
to produce that hash yields a plausible-looking fabrication, so the hash is
computed here or not at all.

Exit codes:
  0  success; for `lint` and `index --check`, also means no findings
  1  a finding was reported (lint findings, stale index), or the operation failed
  2  the knowledge base could not be located: no base found, or — from `init`
     alone — no location chosen to create one at. Both mean the same thing to a
     caller, that no base is resolvable here and a person has to say what to do.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from cli import wiki_placement
from config.workbench_config import WIKI_ROOT_KEY, load_config_or_default
from core import module_proxy
from core import proc
from wiki import backup as wiki_backup
from wiki import create as wiki_create
from wiki import model as wiki_model
from wiki import parsing as wiki_parsing
from wiki import paths as wiki_paths
from wiki import report as wiki_report


# Bound here so the command bodies and this script's tests read the same
# names, while the imports above stay in the `from <package> import <module>`
# form the tarball's reachability walk follows.
ARTICLES_DIR = wiki_paths.ARTICLES_DIR
DEFAULT_SETTINGS = wiki_paths.DEFAULT_SETTINGS
DEFAULT_WIKI_DIRNAME = wiki_paths.DEFAULT_WIKI_DIRNAME
HASH_PREFIX_LEN = wiki_parsing.HASH_PREFIX_LEN
INDEX_FILE = wiki_paths.INDEX_FILE
MAX_PARENT_DEPTH = wiki_paths.MAX_PARENT_DEPTH
RAW_DIR = wiki_paths.RAW_DIR
SCHEMA_FILE = wiki_paths.SCHEMA_FILE
ArchiveResult = wiki_create.ArchiveResult
ArticleNotFoundError = wiki_create.ArticleNotFoundError
ArticleReferencedError = wiki_create.ArticleReferencedError
QueryGap = wiki_parsing.QueryGap
Wiki = wiki_model.Wiki
WikiExistsError = wiki_create.WikiExistsError
archive_article = wiki_create.archive_article
backups_dir = wiki_backup.backups_dir
build_index = wiki_report.build_index
is_overdue = wiki_backup.is_overdue
latest = wiki_backup.latest
collect_lint = wiki_report.collect_lint
collect_signals = wiki_report.collect_signals
collect_status = wiki_report.collect_status
find_wiki = wiki_paths.find_wiki
hash_file = wiki_parsing.hash_file
init_wiki = wiki_create.init_wiki
is_wiki = wiki_paths.is_wiki
manifest_row = wiki_create.manifest_row
prune = wiki_backup.prune
read_text = wiki_parsing.read_text
restore = wiki_backup.restore
slugify_title = wiki_create.slugify_title
snapshot = wiki_backup.snapshot
snapshots = wiki_backup.snapshots
stage_source = wiki_create.stage_source
vault_subpath = wiki_paths.vault_subpath

# The SCHEMA template ships with the skill, the layer that owns the prose. It is
# absent when the CLI is installed without the skill, so init falls back to a
# minimal schema rather than failing.
_SCHEMA_TEMPLATE = Path(__file__).resolve().parents[2] / "skills" / "wiki" / "assets" / "SCHEMA.template.md"


def _schema_template() -> Path | None:
    return _SCHEMA_TEMPLATE if _SCHEMA_TEMPLATE.is_file() else None

# Re-exported so `wiki <command>` and the store it reads are one import for a
# caller, and so the names this script's own usage text cites resolve here.
__all__ = [
    "DEFAULT_SETTINGS",
    "DEFAULT_WIKI_DIRNAME",
    "HASH_PREFIX_LEN",
    "MAX_PARENT_DEPTH",
    "ArchiveResult",
    "QueryGap",
    "Wiki",
    "archive_article",
    "build_index",
    "collect_lint",
    "collect_signals",
    "apply_link",
    "backups_dir",
    "collect_status",
    "configured_dirname",
    "container_link_path",
    "find_wiki",
    "hash_file",
    "init_wiki",
    "is_overdue",
    "latest",
    "main",
    "prune",
    "restore",
    "snapshot",
    "snapshots",
    "manifest_row",
    "repo_root",
    "resolve_wiki",
    "slugify_title",
    "stage_source",
    "vault_dir",
    "vault_subpath",
]

# Placement lives in wiki_placement; rebound here so `wiki <command>` and this
# script's tests read the same names. Tests patch several of these on this
# module — the proxy below forwards the write so the functions that moved
# still see it.
SCRIPT = wiki_placement.SCRIPT
ConfigWriteError = wiki_placement.ConfigWriteError
InitPlacementError = wiki_placement.InitPlacementError
LinkOutcome = wiki_placement.LinkOutcome
apply_link = wiki_placement.apply_link
configured_dirname = wiki_placement.configured_dirname
container_link_path = wiki_placement.container_link_path
default_vault_root = wiki_placement.default_vault_root
repo_root = wiki_placement.repo_root
resolve_wiki = wiki_placement.resolve_wiki
set_value = wiki_placement.set_value
vault_dir = wiki_placement.vault_dir
vault_entry = wiki_placement.vault_entry
wiki_dir_is_declared = wiki_placement.wiki_dir_is_declared
_init_target = wiki_placement._init_target
_link_resolves_to = wiki_placement._link_resolves_to
_record_vault_root = wiki_placement._record_vault_root

_SUBMODULES = (wiki_placement,)
module_proxy.install(__name__, _SUBMODULES)


# ── Commands ──────────────────────────────────────────────────────────────


def cmd_path(wiki: Wiki, args: argparse.Namespace) -> int:
    print(wiki.root)
    return 0


def cmd_status(wiki: Wiki, args: argparse.Namespace) -> int:
    status = collect_status(wiki)
    if args.json:
        print(json.dumps(status, indent=2))
        return 0

    print(f"{status['path']}")
    if status["domain"]:
        print(f"  domain            {status['domain']}")
    print(f"  articles          {status['articles']}")
    print(f"  drafts            {status['drafts']}")
    if status["archived"]:
        print(f"  archived          {status['archived']}")
    print(f"  sources           {status['sources']}")
    print(f"  uncompiled        {status['uncompiled_sources']}")
    print(f"  unprocessed log   {status['unprocessed_log_entries']}")
    for label, key in (("last compile      ", "last_compile"), ("last ingest       ", "last_ingest")):
        if status[key]:
            print(f"  {label}{status[key]}")

    hints = []
    if status["uncompiled_sources"]:
        hints.append("/wiki compile — uncompiled sources")
    if status["drafts"]:
        hints.append("/wiki promote — drafts awaiting review")
    # Text only: the JSON shape is a contract the skill reads, and a prompt is
    # for a person.
    if wiki_backup.is_overdue(wiki.root):
        hints.append(f"wiki backup — no snapshot in the last {wiki_backup.STALE_BACKUP_DAYS} days")
    if hints:
        print()
        for hint in hints:
            print(f"  → {hint}")
    return 0


def cmd_lint(wiki: Wiki, args: argparse.Namespace) -> int:
    findings = collect_lint(wiki)
    if args.json:
        report = {"path": str(wiki.root), "findings": findings}
        if args.signals:
            report["signals"] = collect_signals(wiki)
        print(json.dumps(report, indent=2))
        return 1 if findings else 0

    if not findings:
        print(f"{wiki.root}: clean")
    else:
        _print_findings(wiki, findings)

    if args.signals:
        _print_signals(collect_signals(wiki))
    return 1 if findings else 0


def _print_findings(wiki: Wiki, findings: list[dict]) -> None:
    """The lint findings as text, grouped by check in the order they were raised."""
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = len(findings) - errors
    print(f"{wiki.root}: {errors} error(s), {warnings} warning(s)")
    for check in dict.fromkeys(f["check"] for f in findings):
        group = [f for f in findings if f["check"] == check]
        print(f"\n{check} ({len(group)})")
        for finding in group:
            where = f"{finding['where']}: " if finding["where"] else ""
            print(f"  {where}{finding['message']}")


def cmd_signals(wiki: Wiki, args: argparse.Namespace) -> int:
    """Counted evidence for the judgements lint leaves to the reader.

    Exit 0 whichever way it comes out: a tag table and a gap log are not
    findings, and nothing here is a defect to be fixed.
    """
    signals = collect_signals(wiki)
    if args.json:
        print(json.dumps({"path": str(wiki.root), "signals": signals}, indent=2))
        return 0

    print(f"{wiki.root}")
    _print_signals(signals)
    return 0


def _print_signals(signals: dict) -> None:
    """The four signal tables as text, shared by `signals` and `lint --signals`."""
    print(f"\ntags ({len(signals['tags'])})")
    for row in signals["tags"]:
        print(f"  {row['count']:>3}  {row['tag']}")

    pairs = signals["similar_articles"]
    print(f"\nsimilar articles ({len(pairs)})")
    for pair in pairs:
        left, right = pair["articles"]
        print(f"  {pair['similarity']:.2f}  {left} / {right}")

    clusters = signals["gap_clusters"]
    print(f"\nquery gap clusters ({len(clusters)})")
    for cluster in clusters:
        topics = ", ".join(cluster["topics"][:5]) or "(no topic words)"
        print(f"  {cluster['count']:>3}  {topics}")
        for gap in cluster["gaps"]:
            print(f"       {gap['date'] or '—'}  {gap['question']}")

    drafts = signals["drafts"]
    print(f"\ndrafts ({len(drafts)})")
    for draft in drafts:
        age = "undated" if draft["age_days"] is None else f"{draft['age_days']}d"
        origin = f"  {draft['origin']}" if draft["origin"] else ""
        print(f"  {age:>8}  {draft['slug']}{origin}")


def cmd_archive(wiki: Wiki, args: argparse.Namespace) -> int:
    """Retire one article. The only `wiki` subcommand that moves content."""
    try:
        result = archive_article(wiki, args.slug, force=args.force)
    except ArticleNotFoundError:
        print(f"{SCRIPT}: no article named '{args.slug}'", file=sys.stderr)
        return 1
    except ArticleReferencedError as exc:
        print(f"{SCRIPT}: {exc}", file=sys.stderr)
        print(
            "  remove those links first, or pass --force to keep them as tombstones",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"{SCRIPT}: cannot archive {args.slug}: {exc}", file=sys.stderr)
        return 1

    where = result.path.relative_to(wiki.root).as_posix()
    if not result.moved:
        print(f"{where}: already archived, nothing to do")
        return 0

    print(f"{where}: archived")
    print("  run `wiki index` to drop it from the master index")
    return 0


def cmd_sources(wiki: Wiki, args: argparse.Namespace) -> int:
    recorded = wiki.recorded_source_hashes()
    rows = []
    for source in wiki.sources:
        known = recorded.get(source.rel)
        state = "new" if known is None else ("changed" if known != source.content_hash else "compiled")
        if args.new and state == "compiled":
            continue
        rows.append({"path": source.rel, "hash": source.content_hash, "state": state})

    if args.json:
        print(json.dumps({"path": str(wiki.root), "sources": rows}, indent=2))
        return 0
    if not rows:
        print("no sources" if not args.new else "no new or changed sources")
        return 0
    width = max(len(r["state"]) for r in rows)
    for row in rows:
        print(f"{row['state']:<{width}}  {row['hash']}  {row['path']}")
    return 0


def cmd_init(start: Path, args: argparse.Namespace) -> int:
    """Create a knowledge base. The one command that runs without one existing."""
    try:
        target = _init_target(start, args)
    except InitPlacementError as exc:
        print(f"{SCRIPT}: {exc}", file=sys.stderr)
        return 2

    # `--wiki` names one directory outright and is the documented way to keep a
    # second base, so it is not held to the one-base-per-repo guard below.
    existing, _ = (None, None) if args.wiki else resolve_wiki(start, None)
    if existing is not None and existing != target:
        print(
            f"{SCRIPT}: this repo already has a knowledge base at {existing}",
            file=sys.stderr,
        )
        print("  move it yourself, or pass --wiki DIR to create a second one", file=sys.stderr)
        return 1

    adopting = args.vault and not load_config_or_default(repo_root(start)).wiki.root.strip()
    if adopting:
        problem = _record_vault_root(default_vault_root())
        if problem is not None:
            print(f"{SCRIPT}: cannot record {WIKI_ROOT_KEY}, so a vault base would be", file=sys.stderr)
            print("  unreachable — nothing walks to a vault, config is what finds it", file=sys.stderr)
            print(f"  {problem}", file=sys.stderr)
            return 1

    try:
        root = init_wiki(target, args.domain, args.audience, template=_schema_template())
    except WikiExistsError as exc:
        print(f"{SCRIPT}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{SCRIPT}: cannot create {target}: {exc}", file=sys.stderr)
        return 1

    print(f"{root}: created")
    print(f"  edit {SCHEMA_FILE} to say what this covers, then stage a source")

    # The link is an affordance, so a failure to place one is reported and does
    # not fail the command: the base is what `init` promised, and it is made.
    if args.vault and load_config_or_default(repo_root(start)).wiki.link:
        outcome = apply_link(repo_root(start), root, True)
        print(f"  {outcome.message}", file=sys.stdout if outcome.ok else sys.stderr)
    return 0


def cmd_backup(wiki: Wiki, args: argparse.Namespace) -> int:
    """Snapshot the knowledge base, list snapshots, or restore one.

    Restoring extracts beside the base rather than over it, and prints where it
    landed. A restore happens when something has already gone wrong, and the
    wrong snapshot written over the live base is the one mistake here that
    cannot be undone.
    """
    if args.list:
        return _list_snapshots(wiki)
    if args.restore:
        return _restore_snapshot(wiki, args.restore)
    return _take_snapshot(wiki, args.keep)


def _list_snapshots(wiki: Wiki) -> int:
    found = wiki_backup.snapshots(wiki.root)
    if not found:
        print(f"no snapshots yet: {wiki_backup.backups_dir(wiki.root)}")
        return 0
    for archive in found:
        print(f"{archive.name}  {archive.stat().st_size:>10,}  {archive.parent}")
    return 0


def _restore_snapshot(wiki: Wiki, name: str) -> int:
    archive = _named_snapshot(wiki, name)
    if archive is None:
        print(f"{SCRIPT}: no snapshot named '{name}'", file=sys.stderr)
        return 1
    try:
        landed = wiki_backup.restore(wiki.root, archive)
    except OSError as exc:
        print(f"{SCRIPT}: cannot restore {archive.name}: {exc}", file=sys.stderr)
        return 1
    print(f"{landed}: restored from {archive.name}")
    print("  the live base is untouched — move this into place yourself")
    return 0


def _take_snapshot(wiki: Wiki, keep: int) -> int:
    try:
        created = wiki_backup.snapshot(wiki.root, keep=keep)
    except OSError as exc:
        print(f"{SCRIPT}: cannot snapshot {wiki.root}: {exc}", file=sys.stderr)
        return 1
    print(f"{created}: {created.stat().st_size:,} bytes")
    print("  same disk as the base — it survives a bad edit, not a lost drive")
    return 0


def _named_snapshot(wiki: Wiki, name: str) -> Path | None:
    """The snapshot *name* refers to, where `latest` means the newest."""
    if name == "latest":
        return wiki_backup.latest(wiki.root)
    return next(
        (a for a in wiki_backup.snapshots(wiki.root) if a.name == name),
        None,
    )


def cmd_link(wiki: Wiki, args: argparse.Namespace) -> int:
    """Create or remove the browsing symlink from this repo to its base.

    Honours `wiki.link` rather than taking a flag: there is one answer to
    whether this repo keeps a link, and it is the setting. Running the command
    with the key off is how a link gets removed.

    Only a vault base gets one. An in-tree base is already in the repo, so a
    link beside the worktrees would point at a directory inside one of them and
    would go stale the moment that worktree was removed.
    """
    root = repo_root(Path(args.directory))
    entry = vault_dir(root)
    if entry != wiki.root:
        print(
            f"{SCRIPT}: {wiki.root} is not this repo's vault base, so there is"
            " nothing to link to",
            file=sys.stderr,
        )
        return 1

    outcome = apply_link(root, entry, load_config_or_default(root).wiki.link)
    print(outcome.message if outcome.ok else f"{SCRIPT}: {outcome.message}",
          file=sys.stdout if outcome.ok else sys.stderr)
    return 0 if outcome.ok else 1


def cmd_ingest(wiki: Wiki, args: argparse.Namespace) -> int:
    """Stage one source. Reading it and compiling it is the skill's work."""
    try:
        staged = stage_source(wiki.root, Path(args.stage).expanduser(), args.source_type, args.title)
    except FileNotFoundError:
        print(f"{SCRIPT}: no such file: {args.stage}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{SCRIPT}: cannot stage {args.stage}: {exc}", file=sys.stderr)
        return 1

    print(f"{staged.relative_to(wiki.root).as_posix()}: staged")
    print(f"  manifest row: {manifest_row(wiki.root, staged, args.source_type).strip()}")
    return 0


def cmd_index(wiki: Wiki, args: argparse.Namespace) -> int:
    rendered = build_index(wiki)
    target = wiki.root / INDEX_FILE
    if args.check:
        current = read_text(target)
        if current == rendered:
            print(f"{target}: current")
            return 0
        print(f"{target}: stale — run `wiki index` to rebuild")
        return 1
    try:
        target.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        print(f"{SCRIPT}: cannot write {target}: {exc}", file=sys.stderr)
        return 1
    print(f"{target}: rebuilt from {len(wiki.published())} article(s)")
    return 0


# ── Entry point ─────────────────────────────────────────────────────────────

# `init` is handled before resolution in `main`, since it is the one command
# that runs without a knowledge base already existing. Its entry is here so it
# appears in `--help` beside the rest.
_COMMANDS = {
    "init": (None, "Create a knowledge base"),
    "path": (cmd_path, "Print the resolved knowledge base directory"),
    "status": (cmd_status, "Counts, uncompiled sources, and recent activity"),
    "lint": (cmd_lint, "Mechanical health checks over articles and sources"),
    "signals": (cmd_signals, "Counted evidence for the judgements lint leaves open"),
    "sources": (cmd_sources, "Raw sources with their hashes and compile state"),
    "index": (cmd_index, "Rebuild the master index from article frontmatter"),
    "backup": (cmd_backup, "Snapshot the knowledge base, list snapshots, or restore one"),
    "link": (cmd_link, "Create or remove the browsing symlink from this repo to its base"),
    "ingest": (cmd_ingest, "Copy a source into raw/ with frontmatter and a real hash"),
    "archive": (cmd_archive, "Retire an article to archive/, keeping it readable"),
}


def build_parser(version_string: Callable[[str], str] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wiki",
        description="Mechanical operations over a compiled knowledge base.",
    )
    version_of = version_string or (lambda name: f"{name} unknown")
    parser.add_argument("-V", "--version", action="version", version=version_of(SCRIPT))
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, (_, help_text) in _COMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text)
        # A subcommand's own required positional is registered before the optional
        # trailing directory, so `wiki archive SLUG DIR` binds each to the right
        # one. Registered the other way round, argparse gives the first word to
        # `directory` and then has nothing left for the slug.
        if name == "archive":
            sub.add_argument("slug", help="Article to retire")
        sub.add_argument("directory", nargs="?", default=".", help="Where to start looking (default: cwd)")
        sub.add_argument("--wiki", metavar="DIR", help="Use this knowledge base instead of searching")
        if name in ("status", "lint", "signals", "sources"):
            sub.add_argument("--json", action="store_true", help="Emit JSON")
        if name == "lint":
            sub.add_argument(
                "--signals",
                action="store_true",
                help="Also report the counted signals",
            )
        if name == "archive":
            sub.add_argument(
                "--force",
                action="store_true",
                help="Archive even while live articles link to it",
            )
        if name == "sources":
            sub.add_argument("--new", action="store_true", help="Only new or changed sources")
        if name == "index":
            sub.add_argument("--check", action="store_true", help="Report staleness without writing")
        if name == "backup":
            sub.add_argument("--list", action="store_true", help="List snapshots instead of making one")
            sub.add_argument(
                "--restore", metavar="NAME",
                help="Extract a snapshot beside the base; NAME or 'latest'",
            )
            sub.add_argument(
                "--keep", type=int, default=wiki_backup.KEEP_DEFAULT,
                help="How many snapshots to keep (default: %(default)s)",
            )
        if name == "init":
            sub.add_argument("--domain", default="", help="What the knowledge base is about")
            sub.add_argument("--audience", default="", help="Who will use it")
            placement = sub.add_mutually_exclusive_group()
            placement.add_argument(
                "--vault", action="store_true",
                help="Keep it in the machine-level vault, private to this machine",
            )
            placement.add_argument(
                "--in-repo", action="store_true", dest="in_repo",
                help="Keep it in the repo, committed and shared with whoever clones it",
            )
        if name == "ingest":
            sub.add_argument("--stage", metavar="SRC", required=True, help="File to copy into raw/")
            sub.add_argument(
                "--type", default="file", dest="source_type", help="Source type recorded in frontmatter"
            )
            sub.add_argument("--title", default="", help="Title, used for the filename and frontmatter")
    return parser


def main_entry(version_string: Callable[[str], str] | None = None) -> None:
    try:
        sys.exit(main(version_string=version_string))
    except KeyboardInterrupt:
        sys.exit(proc.INTERRUPT_RETURNCODE)


def main(argv: list[str] | None = None, *,
         version_string: Callable[[str], str] | None = None) -> int:
    """Parse *argv* and run the subcommand it names.

    `version_string` is injected rather than imported: it resolves the release
    manifest beside `ai/bin`, which this layer cannot reach. The default keeps
    `--version` answering for a caller that does not supply one — a test, or an
    import that only wants the parser.
    """
    args = build_parser(version_string).parse_args(argv)

    start = Path(args.directory)
    if args.command == "init":
        return cmd_init(start, args)

    root, vault = resolve_wiki(start, args.wiki)
    if root is None:
        where = args.wiki or args.directory
        vault_note = f"\n  no vault base at {vault}" if vault else ""
        print(
            f"{SCRIPT}: no knowledge base found from {where} "
            f"(looked for a directory holding {SCHEMA_FILE}, "
            f"{ARTICLES_DIR}/, and {RAW_DIR}/)"
            f"{vault_note}",
            file=sys.stderr,
        )
        return 2

    handler, _ = _COMMANDS[args.command]
    return handler(Wiki(root), args)
