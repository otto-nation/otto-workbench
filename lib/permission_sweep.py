"""Report Claude Code permission-grant drift across every registered repo.

Usage: python3 lib/permission_sweep.py [--prune] [--verbose]
       otto-workbench permissions sweep [--prune] [--verbose]

`bin/local/validate-permissions` sees this drift in one repo — otto-workbench's
own — because validate-all is otto-workbench's gate.  Neither of the things
that run it is a sensible vehicle for the rest of the machine: CI clones this
repo alone and would have nothing to find, and this repo's pre-push should not
fail over a stale grant in `homelab`.

The drift is not this repo's, though.  `otto-workbench ai init` scaffolds
`.claude/` into every registered project and the same accumulation happens in
each one, invisibly: a bare-repo container is not a working tree, so no tracked
file there can apply, and a walk rooted in a worktree never climbs to it.  This
sweep walks the project registry instead, so the unit is the machine.

Three signals, in the order a reader should act on them:

    overrides  a local `allow` handing back a command an `ask` rule gates
    covered    a local `allow` some other rule already makes, so it is dead weight
    stale      a rule naming an absolute path whose directory is gone

`validate-permissions` has only the first two, and it measures coverage against
the repo's tracked `.claude/settings.json`.  That file is the exception rather
than the rule elsewhere: per CONTRIBUTING.md § Permission grants, `ai init`
deliberately writes no `settings.json` into other projects, so measuring only
against it would call every grant in most repos uncovered.  The machine-wide
`~/.claude/settings.json` is the signal that works everywhere, and coverage
here is the union of the two — a grant is dead weight if *either* file already
makes it.

Staleness needs neither file.  A grant naming `/…/some-worktree/bin/thing` when
that directory is gone outlived the thing it was written for whatever else the
machine grants, and it is the class that explains why the counts only ever go
up.

Only the covered class is ever deleted, and only under `--prune`.  Deleting a
covered grant is provably a no-op on effective permissions, which is what makes
it defensible to do inside a repo the user did not ask this to touch.  An
override changes permissions by definition, and a stale path is found by a
heuristic — a rule can name a directory for reasons this cannot see — so both
are reported and left alone.

Exit code is 0 whenever the sweep ran, drift or not.  This reports; it is not a
gate, and `otto-workbench maintenance` must not start failing the day a repo
accumulates its first covered grant.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from pathlib import Path

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
_WORKBENCH_DIR = os.path.dirname(_LIB_DIR)
for _path in (_LIB_DIR, os.path.join(_WORKBENCH_DIR, 'ai', 'lib')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import workbench_projects  # noqa: E402
from ansi import BOLD, CYAN, DIM, GREEN, NC, RED, YELLOW  # noqa: E402
from permissions import (  # noqa: E402
    Drift, Grant, Settings, TrackedRules,
    at_container, bodies, container_dir, discover_container_settings,
    discover_settings, drift_of, is_local, line_of, machine_rules, prune, read_settings,
    tracked_rules,
)

# A rule body is a command line, so an absolute path inside it ends at
# whitespace or a quote.  `=` splits too, which is what reaches the path in an
# env-var prefix (`PYTHONPATH=/repo/lib …`) and in a long flag written with one
# (`--repo-dir=/repo`).  Everything from the first glob metacharacter on is
# pattern rather than path and is cut before the directory test.
_TOKEN_SPLIT = re.compile(r'[\s"\'=]+')
_GLOB_CHARS = '*?['


@dataclasses.dataclass(frozen=True)
class Stale:
    """A grant naming an absolute path whose directory no longer exists."""

    rule: str
    missing: str
    line: int = 0


@dataclasses.dataclass(frozen=True)
class FileReport:
    """What one untracked settings file holds."""

    path: str
    grants: int = 0
    overrides: list[Grant] = dataclasses.field(default_factory=list)
    covered: list[Grant] = dataclasses.field(default_factory=list)
    stale: list[Stale] = dataclasses.field(default_factory=list)
    pruned: list[Grant] = dataclasses.field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.overrides or self.covered or self.stale or self.pruned)


@dataclasses.dataclass(frozen=True)
class RepoReport:
    """Every untracked settings file one registered repo answers for."""

    root: str
    files: list[FileReport] = dataclasses.field(default_factory=list)

    @property
    def empty(self) -> bool:
        return all(f.empty for f in self.files)


# ── Signals ─────────────────────────────────────────────────────────────────

def _merge(first: Drift, second: Drift) -> Drift:
    """Both drift readings of one file, with the first reading's attribution.

    A grant can be covered by the repo's tracked file and by the machine file
    at once.  Reporting it twice would double every count, and the repo's own
    file is the more useful of the two names to print, so it wins.

    An override outranks a covered reading of the same rule for the reason the
    classes exist: an `ask` anywhere means a person is meant to see the call,
    and an `allow` elsewhere does not cancel that.
    """
    overrides = list({g.rule: g for g in second.overrides + first.overrides}.values())
    gated = {g.rule for g in overrides}
    covered = {g.rule: g for g in second.covered + first.covered if g.rule not in gated}
    return Drift(overrides, list(covered.values()))


def _topmost_missing(path: str) -> str:
    """The highest ancestor of path that does not exist.

    The grant names a file several levels down, but what actually went away is
    usually a whole worktree or a throwaway virtualenv.  Naming the deepest
    missing directory would print four levels of a path the reader has to walk
    back up themselves; naming the top of the gone subtree says what happened.
    """
    missing = path
    while True:
        parent = os.path.dirname(missing)
        if parent in ('', '/') or os.path.exists(parent):
            return missing
        missing = parent


def _named_directory(body: str) -> str | None:
    """The gone directory an absolute path in the rule names, if there is one.

    A token is only judged when both it and its parent directory are missing.
    A rule whose parent directory still exists is left alone deliberately: a
    prefix rule names a *string*, not a file, so `Bash(/repo/bin/run:*)` is live
    whether or not `/repo/bin/run` is a real path, and only the directory around
    it disappearing settles that the grant outlived its subject.

    Returns the directory, so the report can name what went away rather than
    making the reader diff two paths.
    """
    stem = body[:-2] if body.endswith(':*') else body
    for token in _TOKEN_SPLIT.split(stem):
        if not token.startswith('/'):
            continue
        cut = min((token.find(c) for c in _GLOB_CHARS if c in token), default=len(token))
        path = token[:cut].rstrip(':/')
        parent = os.path.dirname(path)
        if not path or parent in ('', '/'):
            continue
        if not os.path.exists(path) and not os.path.isdir(parent):
            return _topmost_missing(parent)
    return None


def stale_of(settings: Settings, skip: set[str]) -> list[Stale]:
    """Every allow grant in the file that names a directory that is gone.

    `skip` holds the rules already classified as covered or as an override.
    Those have a named home and a named fix; saying they are also stale would
    only make the report harder to act on.
    """
    found: list[Stale] = []
    for body in dict.fromkeys(bodies(settings.permissions, 'allow')):
        rule = f'Bash({body})'
        if rule in skip:
            continue
        missing = _named_directory(body)
        if missing is not None:
            found.append(Stale(rule, missing, line_of(settings.lines, rule)))
    return found


# ── Sweeping ────────────────────────────────────────────────────────────────

def local_settings_files(repo_root: str) -> list[str]:
    """Every untracked settings file this repo answers for.

    The repo's own walk plus the bare-repo container above it, which is where
    the counts actually accumulate — a container holds no working tree, so
    nothing tracked applies there and nothing prunes it.
    """
    container = container_dir(repo_root)
    found = discover_settings(repo_root) + discover_container_settings(container)
    return [p for p in found if is_local(p, at_container(p, container))]


def scan_file(filepath: str, tracked: TrackedRules, machine: TrackedRules,
              do_prune: bool) -> FileReport:
    """Classify one untracked settings file, pruning the covered class if asked.

    Read once, asked three questions.  A settings file that cannot be read is
    not a finding this owns: the sweep visits repos nobody asked it to touch,
    and one half-written JSON file there must not cost the reader every other
    repo behind it.
    """
    settings = read_settings(filepath)
    if settings is None:
        return FileReport(filepath)

    grants = len(bodies(settings.permissions, 'allow'))
    drift = _merge(drift_of(settings, tracked), drift_of(settings, machine))
    stale = stale_of(settings, {g.rule for g in drift.overrides + drift.covered})
    unpruned = FileReport(filepath, grants, drift.overrides, drift.covered, stale)

    if not (do_prune and drift.covered):
        return unpruned

    try:
        prune(filepath, drift.covered)
    except (OSError, ValueError):
        # Claude Code appends to settings.local.json live, so a file classified
        # a moment ago can be mid-write now. Reporting the grants as covered but
        # not pruned is the honest answer, and the next run prunes them.
        return unpruned

    return FileReport(filepath, grants, drift.overrides, [], stale, drift.covered)


def sweep(repos: list[str], machine: TrackedRules, do_prune: bool = False) -> list[RepoReport]:
    """Classify the untracked settings files of every repo, each file once.

    Sibling worktrees of one bare repo share a container, so the same file is
    reachable from several registered roots.  It is scanned under the first one
    that reaches it, whose tracked rules stand in for the repo — every worktree
    of a repo carries the same `.claude/settings.json` but for uncommitted
    edits, and the machine-wide half of coverage is the same for all of them.
    """
    seen: set[str] = set()
    reports: list[RepoReport] = []
    for repo_root in repos:
        fresh = [p for p in local_settings_files(repo_root)
                 if os.path.realpath(p) not in seen]
        seen.update(os.path.realpath(p) for p in fresh)
        if not fresh:
            continue
        tracked = tracked_rules(repo_root)
        reports.append(RepoReport(
            repo_root, [scan_file(p, tracked, machine, do_prune) for p in fresh]))
    return reports


# ── Reporting ───────────────────────────────────────────────────────────────

def _tilde(path: str) -> str:
    home = str(Path.home())
    return '~' + path[len(home):] if path.startswith(home + os.sep) else path


def _detailed(entries: list, verbose: bool) -> list:
    """The entries to print one line each for: all of them, or none.

    Selecting the list is what keeps each caller below flat.  Wrapping the loops
    in `if verbose:` instead would put them three levels deep, which is one more
    than `bin/validate-nesting` allows.
    """
    return entries if verbose else []


def _report_file(report: FileReport, repo_root: str, verbose: bool) -> None:
    where = os.path.relpath(report.path, repo_root)
    if where.startswith(os.pardir):
        where = _tilde(report.path)
    print(f'    {where} {DIM}— {report.grants} grant(s){NC}')

    if report.pruned:
        print(f'      {GREEN}✓{NC} pruned {len(report.pruned)} grant(s) another rule already makes')
        for grant in _detailed(report.pruned, verbose):
            print(f'          {DIM}{grant.rule} — covered by {grant.tracked}{NC}')
    if report.overrides:
        print(f'      {RED}✗{NC} {len(report.overrides)} re-grant(s) an ask-gated command '
              f'{DIM}— delete by hand{NC}')
        # Never abridged: this is the class a person has to read and act on.
        for grant in report.overrides:
            where_line = f'line {grant.line}: ' if grant.line else ''
            print(f'          {where_line}{grant.rule} {DIM}over {grant.tracked}{NC}')
    if report.covered:
        print(f'      {YELLOW}⚠{NC}  {len(report.covered)} grant(s) another rule already makes')
        for grant in _detailed(report.covered, verbose):
            where_line = f'line {grant.line}: ' if grant.line else ''
            print(f'          {where_line}{grant.rule} {DIM}covered by {grant.tracked}{NC}')
    if report.stale:
        print(f'      {YELLOW}⚠{NC}  {len(report.stale)} grant(s) name a directory that is gone')
        for entry in _detailed(report.stale, verbose):
            where_line = f'line {entry.line}: ' if entry.line else ''
            print(f'          {where_line}{entry.rule} {DIM}→ {_tilde(entry.missing)}{NC}')


def _totals(reports: list[RepoReport]) -> dict[str, int]:
    files = [f for report in reports for f in report.files]
    return {
        'grants': sum(f.grants for f in files),
        'overrides': sum(len(f.overrides) for f in files),
        'covered': sum(len(f.covered) for f in files),
        'stale': sum(len(f.stale) for f in files),
        'pruned': sum(len(f.pruned) for f in files),
    }


def report(reports: list[RepoReport], repos: int, verbose: bool) -> None:
    """Print the sweep, repo by repo, then the machine-wide totals."""
    drifting = [r for r in reports if not r.empty]

    for entry in drifting:
        print(f'\n  {BOLD}{_tilde(entry.root)}{NC}')
        for f in (f for f in entry.files if not f.empty):
            _report_file(f, entry.root, verbose)

    totals = _totals(reports)
    print(f'\n{CYAN}Machine total{NC}  {repos} registered repo(s), '
          f'{len(drifting)} with drift, {totals["grants"]} local grant(s)')

    if totals['pruned']:
        print(f'  {GREEN}✓{NC} pruned {totals["pruned"]} grant(s) another rule already makes')
    if totals['overrides']:
        print(f'  {RED}✗{NC} {totals["overrides"]} re-grant an ask-gated command — '
              f'only a person should delete these')
    if totals['covered']:
        print(f'  {YELLOW}⚠{NC}  {totals["covered"]} already granted elsewhere — '
              f'run with --prune to delete them')
    if totals['stale']:
        print(f'  {YELLOW}⚠{NC}  {totals["stale"]} name a directory that is gone — '
              f'delete by hand; a path can be named for reasons this cannot see')
    if not any(totals[k] for k in ('overrides', 'covered', 'stale', 'pruned')):
        print(f'  {GREEN}✓{NC} no drift')
    elif not verbose:
        print(f'  {DIM}run with --verbose to list every grant{NC}')


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='otto-workbench permissions sweep',
        description='Report Claude Code permission-grant drift across every registered repo.')
    parser.add_argument('--prune', action='store_true',
                        help='Delete grants another rule already makes (never the other classes)')
    parser.add_argument('--verbose', action='store_true',
                        help='List every grant, not just the per-file counts')
    args = parser.parse_args(argv)

    repos = [str(p) for p in workbench_projects.registered()]
    if not repos:
        print('No repos registered yet — one joins the list the first time a workbench '
              'command runs in it')
        return 0

    machine = machine_rules(str(Path.home()))
    report(sweep(repos, machine, args.prune), len(repos), args.verbose)
    return 0


if __name__ == '__main__':
    sys.exit(main())
