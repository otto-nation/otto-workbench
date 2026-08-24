"""Copy a repo's tracked grants into the bare-repo container above its worktrees.

Usage: python3 lib/permission_mirror.py [--dry-run] [--verbose]
       otto-workbench permissions mirror [--dry-run] [--verbose]

Claude Code keys a project by the directory the session was launched in.  In a
bare-repo worktree layout that is usually the container — the directory holding
the bare `.git` with the worktrees as peers — and a container holds no working
tree, so the `.claude/settings.json` a repo keeps its grants in is invisible
from there.  Nothing walks between the two: not upward from a worktree, not
downward from the container.

The effect is that a repo's own scripts prompt in exactly the sessions people
run.  Claude offers a one-off, that one-off is an exact full command string, and
the next invocation carrying a different argument prompts again — which is how a
container accumulates a hundred grants a single tracked wildcard already covers.

`lib/permission_sweep.py` reports that accumulation and prunes what some other
rule already grants.  This is the other half: it writes the tracked rules *to*
the container, so the grants are there before a prompt has to be answered.

The file it writes is generated, never hand-edited.  It carries the
`MANIFEST_KEY` stamp naming what the workbench put there, which is what tells
`lib/permissions.is_local` that these rules have a reviewed owner even though
their location cannot be tracked, and what lets a later run replace them while
leaving a user's own additions alone.

Both buckets travel.  Mirroring `allow` without `ask` would drop the gate on the
scripts that reach credentials — the grant would arrive and the prompt guarding
it would not, which is a worse outcome than the prompts this exists to remove.

One container, one source.  A container holds many worktrees on many branches,
each with its own copy of the tracked file and any of them mid-edit; the
worktree on the branch the shared repository's HEAD names is the reviewed one,
so it is the only one that writes.  A container whose registered worktrees are
all on feature branches is skipped and said so, rather than mirrored from
whichever the registry happened to list first.

Exit code is 0 whenever the run completed.  This is a sync step, not a gate.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
_WORKBENCH_DIR = os.path.dirname(_LIB_DIR)
for _path in (_LIB_DIR, os.path.join(_WORKBENCH_DIR, 'ai', 'lib')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import workbench_projects  # noqa: E402
from ansi import CYAN, DIM, GREEN, NC, YELLOW  # noqa: E402
from permissions import (  # noqa: E402
    MANIFEST_KEY, TRACKED_SETTINGS, TrackedRules,
    container_dir, git, rules_of, write_json,
)

# The buckets a mirror carries. `deny` is absent because a tracked project file
# declares none, and writing an empty one would claim the mirror speaks for a
# bucket nothing has asked it to.
MIRRORED = ('allow', 'ask')

# Where a mirror is written, relative to the container. The same path as the
# tracked file it copies, and for the same reason: this is the one name Claude
# Code loads project settings from. Only the root it hangs off differs.
MIRROR_SETTINGS = TRACKED_SETTINGS

NO_SOURCE = 'no registered worktree is on the branch its HEAD names'


@dataclasses.dataclass(frozen=True)
class Result:
    """What one container got, for the caller printing a line per container."""

    container: str
    source: str = ''
    changed: bool = False
    skipped: str = ''

    @property
    def ok(self) -> bool:
        return not self.skipped


# ── Choosing the source worktree ────────────────────────────────────────────

def head_branch(container: str) -> str | None:
    """The branch the container's shared repository points HEAD at.

    A bare repository's HEAD is its default branch, which is the reviewed one
    and so the one whose worktree holds the tracked file worth copying.  Read
    from the repository rather than from `origin`, so a container with no remote
    — a local experiment, a test fixture — still answers.

    This is one of two spellings of that rule; `bin/resolve-worktree` is the
    bash one, used to redirect a session launched at the container.  They have
    to agree: a session redirected to a worktree this did not mirror from is a
    session missing the grants the mirror exists to deliver.
    `tests/container_source.bats` fails if they diverge.
    """
    ref = git(container, 'symbolic-ref', '--quiet', 'HEAD')
    return ref.rsplit('/', 1)[-1] if ref else None


def is_source(repo_root: str, branch: str | None) -> bool:
    """Is this worktree checked out on the branch that speaks for its container?"""
    if not branch:
        return False
    return git(repo_root, 'rev-parse', '--abbrev-ref', 'HEAD') == branch


def containers(repos: list[str]) -> dict[str, list[str]]:
    """Group registered worktrees by the bare-repo container they share.

    A repo that is not in such a layout has no container and contributes
    nothing: its tracked file already applies to sessions rooted in it.
    """
    found: dict[str, list[str]] = {}
    for repo in repos:
        container = container_dir(repo)
        if container is not None:
            found.setdefault(container, []).append(repo)
    return found


def source_of(container: str, members: list[str]) -> str | None:
    """Which of a container's registered worktrees its mirror is copied from.

    None when none of them is on the branch HEAD names — see the module
    docstring on why a runner-up is not taken.
    """
    branch = head_branch(container)
    return next((repo for repo in members if is_source(repo, branch)), None)


# ── Building the mirror ─────────────────────────────────────────────────────

def _managed(tracked: TrackedRules) -> dict[str, list[str]]:
    """The tracked bodies as whole rules, by bucket."""
    return {bucket: [f'Bash({body})' for body in getattr(tracked, bucket)]
            for bucket in MIRRORED}


def read_json(filepath: str) -> dict | None:
    """The JSON object at a path, or None if there is not one to be read.

    None covers the file being absent, unparsable, or not an object at all.
    Callers that must tell those apart test for the path themselves — this
    answers only whether there is a settings object here to merge into.
    """
    try:
        with open(filepath, encoding='utf-8') as f:
            found = json.load(f)
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def mergeable(existing: dict) -> bool:
    """Is a settings object in the shape a mirror can be merged into?

    Both keys the merge reads have to be objects: `permissions`, whose buckets
    it rewrites, and the `MANIFEST_KEY` stamp, which says which of those entries
    the last run put there.  A file where either is something else was not
    written by any run of this, and rewriting it would have to guess at what a
    user meant — so the caller leaves it alone and reports it instead.
    """
    return all(isinstance(existing.get(key, {}), dict)
               for key in ('permissions', MANIFEST_KEY))


def _bucket(permissions: dict, bucket: str) -> list[str]:
    """One bucket's rules, or none when it is absent or not a list."""
    rules = permissions.get(bucket)
    return rules if isinstance(rules, list) else []


def mirror_body(tracked: TrackedRules, source: str, existing: dict | None) -> dict:
    """The settings object a container should hold, given what it holds now.

    `tracked` is the source worktree's rules, `source` the path they were read
    from — recorded in the file so a reader can find the one to edit — and
    `existing` whatever is already at the target, or None for a first write.
    A non-None `existing` has to satisfy `mergeable`.

    A user's own additions survive: an entry the previous stamp does not claim
    is kept, and the managed rules go in front of it.  That is the contract
    `ai/claude/sync-settings.jq` keeps for ~/.claude/settings.json, so the two
    generated settings files on a machine behave the same way.
    """
    existing = existing or {}
    was = existing.get(MANIFEST_KEY, {}).get('permissions')
    was = was if isinstance(was, dict) else {}
    held = existing.get('permissions', {})
    managed = _managed(tracked)

    permissions = dict(held)
    for bucket in MIRRORED:
        mine = managed[bucket]
        theirs = [rule for rule in _bucket(held, bucket)
                  if rule not in _bucket(was, bucket) and rule not in mine]
        permissions[bucket] = mine + theirs

    return {**existing,
            'permissions': permissions,
            MANIFEST_KEY: {'source': source, 'permissions': managed}}


def write_mirror(container: str, repo_root: str, dry_run: bool = False) -> Result:
    """Write one container's settings file, and say whether it changed.

    Idempotent by comparison rather than by timestamp: the body is rebuilt from
    the tracked file every run and written only when it differs from what is
    already there, so a repeat run touches nothing and reports nothing.

    A file already at the target that cannot be parsed, or that `mergeable`
    rejects, is left alone and reported.  It may hold grants somebody approved,
    and overwriting it to install rules that only remove prompts is not a trade
    worth making blind.
    """
    source = os.path.join(repo_root, TRACKED_SETTINGS)
    tracked = rules_of(source)
    target = os.path.join(container, MIRROR_SETTINGS)
    existing = read_json(target)

    if existing is None and os.path.exists(target):
        return Result(container, source, skipped=f'{MIRROR_SETTINGS} is not readable JSON')
    if existing is not None and not mergeable(existing):
        return Result(container, source,
                      skipped=f'{MIRROR_SETTINGS} is not in a shape this can merge into')
    if existing is None and not any(getattr(tracked, b) for b in MIRRORED):
        # Nothing to say and nothing said: most repos keep no tracked grants at
        # all, so an empty mirror everywhere would be the report's whole output.
        return Result(container, source)

    body = mirror_body(tracked, source, existing)
    if existing == body:
        return Result(container, source)

    if not dry_run:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        write_json(target, body)
    return Result(container, source, changed=True)


def has_stake(container: str, members: list[str]) -> bool:
    """Would a mirror for this container carry anything at all?

    No, when none of its worktrees keeps a tracked grant and no mirror is
    already there — which is most of the machine, since `otto-workbench ai init`
    deliberately writes no `settings.json` into the repos it scaffolds.  Failing
    to pick a source among worktrees there is not a finding, and reporting it
    would fill the report with containers that had nothing to receive.

    A mirror already at the container is a stake whichever way: its managed
    rules may now need emptying.
    """
    if os.path.exists(os.path.join(container, MIRROR_SETTINGS)):
        return True
    tracked = [rules_of(os.path.join(member, TRACKED_SETTINGS)) for member in members]
    return any(getattr(rules, bucket) for rules in tracked for bucket in MIRRORED)


def mirror(repos: list[str], dry_run: bool = False) -> list[Result]:
    """Write a mirror for every container one of the repos speaks for."""
    results: list[Result] = []
    for container, members in sorted(containers(repos).items()):
        source = source_of(container, members)
        if source:
            results.append(write_mirror(container, source, dry_run))
        elif has_stake(container, members):
            results.append(Result(container, skipped=NO_SOURCE))
    return results


# ── Reporting ───────────────────────────────────────────────────────────────

def _tilde(path: str) -> str:
    home = str(Path.home())
    return '~' + path[len(home):] if path.startswith(home + os.sep) else path


def report(results: list[Result], dry_run: bool, verbose: bool) -> None:
    """Print the containers that changed, then the ones that could not."""
    verb = 'would write' if dry_run else 'wrote'
    for result in [r for r in results if r.ok and r.changed]:
        print(f'  {GREEN}✓{NC} {verb} {_tilde(os.path.join(result.container, MIRROR_SETTINGS))} '
              f'{DIM}from {_tilde(result.source)}{NC}')
    for result in [r for r in results if not r.ok]:
        print(f'  {YELLOW}⚠{NC}  {_tilde(result.container)} {DIM}— {result.skipped}{NC}')

    # Counts only the containers that were actually looked at and found current.
    # A skipped one is already reported above, and folding it in here would say
    # a mirror is current when nothing was written to compare against.
    current = [r for r in results if r.ok and not r.changed]
    if verbose and current and not any(r.changed for r in results):
        print(f'  {CYAN}{len(current)}{NC} container(s), every mirror already current')


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='otto-workbench permissions mirror',
        description="Copy each repo's tracked grants into the bare-repo container above its "
                    'worktrees, so a session rooted there is not asked for them one at a time.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would be written without writing it')
    parser.add_argument('--verbose', action='store_true',
                        help='Say so when every mirror is already current')
    args = parser.parse_args(argv)

    repos = [str(p) for p in workbench_projects.registered()]
    report(mirror(repos, args.dry_run), args.dry_run, args.verbose)
    return 0


if __name__ == '__main__':
    sys.exit(main())
