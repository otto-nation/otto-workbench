"""Claude Code permission rules — the matcher, coverage, and file discovery.

Two callers share this module and neither owns it:

    bin/local/validate-permissions   this repo's gate, run by validate-all
    lib/permission_sweep.py          the machine-wide sweep across every repo

They ask the same three questions — does this rule match that command, does a
tracked rule already grant what a local one grants, and which settings files
are untracked — so the answers live here once.  A second copy of the matcher is
the failure mode the sweep was written to avoid.

Claude Code classifies a permission rule before matching it, and the two forms
are not interchangeable:

    Bash(npm run:*)    prefix rule   — literal startsWith, no globbing
    Bash(npm run *)    wildcard rule — compiled to a regex

Both facts were read off the matcher in the shipped Claude Code bundle rather
than documentation: the rule classifier tests `/^(.+):\\*$/` before it looks for
an unescaped `*`, and the wildcard branch compiles the pattern to a `^…$`
regex.  Re-derive them the same way if a release ever changes the behaviour.

Where a live rule is written down is the second question.  CONTRIBUTING.md
§ Permission grants puts the grants for a repo's own scripts in the tracked
.claude/settings.json, so a copy of one of those grants sitting in an untracked
settings.local.json is a bug report: the tracked rule should have matched, and
the local copy dies with the worktree.  Two drift classes come out of that:

    a local `allow` a tracked `allow` already makes   → safe to delete
    a local `allow` over a tracked `ask` rule         → only a human deletes it

The second is never pruned.  Removing it restores a deliberate gate on
credential access, and `ask` outranks `allow` precisely so that a person sees
the call; deciding to drop that gate is not a script's to make.

A file that carries the gate itself is not in that class.  `ask` outranking
`allow` is what makes the override a fault in the first place, and a settings
file declaring both has kept the gate rather than opened it — so the `ask`
rules read for the second class come from the tracked file *and* from the file
under inspection.

A grant with no tracked home — a one-afternoon WebFetch domain, a /tmp scratch
script — is nobody's bug and is left alone by both classes.

`deny` needs no class of its own: deny outranks allow unconditionally, so a
local allow cannot hand back a denied command.  `ask` can be handed back, which
is why it is the class a script must never prune.

Nothing here prints.  Both callers render their own findings, and a library
that wrote to stderr could not be used by the one that reports per repo.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from gitenv import git_env_clear  # noqa: E402

BASH_RULE = re.compile(r'^Bash\((.*)\)$', re.DOTALL)

# Buckets under `permissions` holding rule strings.
RULE_BUCKETS = ('allow', 'deny', 'ask')

# The tracked project file that owns a repo's grants, and the untracked name
# Claude Code writes an approved one-off into.
TRACKED_SETTINGS = os.path.join('.claude', 'settings.json')
LOCAL_SETTINGS = 'settings.local.json'

# The key the workbench stamps on a settings file it generates, recording what
# it wrote so the next run can tell its own entries from a user's.
# `ai/claude/sync-settings.jq` writes the same key into ~/.claude/settings.json.
MANIFEST_KEY = '_workbench'


@dataclasses.dataclass(frozen=True)
class Grant:
    """A local grant, paired with the tracked rule that speaks for it."""

    rule: str
    tracked: str
    line: int = 0


@dataclasses.dataclass(frozen=True)
class Drift:
    """The reportable grants in one untracked settings file."""

    overrides: list[Grant] = dataclasses.field(default_factory=list)
    covered: list[Grant] = dataclasses.field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.overrides and not self.covered


@dataclasses.dataclass(frozen=True)
class TrackedRules:
    """The Bash rule bodies a settings file declares, by bucket."""

    allow: list[str] = dataclasses.field(default_factory=list)
    ask: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Settings:
    """One settings file, read and parsed once.

    Both halves of the read travel together because every check needs both: the
    rules come from the parsed object and the line number a finding is reported
    at comes from the raw lines.
    """

    lines: list[str] = dataclasses.field(default_factory=list)
    permissions: dict = dataclasses.field(default_factory=dict)


# ── Matching ────────────────────────────────────────────────────────────────

def matches(body: str, command: str) -> bool:
    """Does a rule body match a command, by Claude Code's own matcher?

    Three forms: `X:*` is a literal startsWith, a `*` anywhere else compiles to
    an anchored regex, and a body with neither names one exact command.
    """
    if body.endswith(':*'):
        return command.startswith(body[:-2])
    if '*' in body:
        pattern = '.*'.join(re.escape(part) for part in body.split('*'))
        return re.fullmatch(pattern, command, re.DOTALL) is not None
    return command == body


def required_prefix(body: str) -> str:
    """The text every command the rule can match has to start with.

    Both matching forms are anchored at the front — a prefix rule by
    definition, a wildcard rule because its regex is — so the text before the
    first `*` is a hard requirement whichever form the rule takes.
    """
    stem = body[:-2] if body.endswith(':*') else body
    return stem.split('*', 1)[0]


def covered_by(local: str, tracked: str) -> bool:
    """Does the tracked rule already grant everything the local rule grants?

    Sound in the one direction that matters: it answers yes only when every
    command the local rule can match is matched by the tracked rule too.

    An exact local rule names a single command, so running that command through
    the tracked matcher settles it.  A local rule that can match more than one
    command is only safe to judge against a tracked rule that is open-ended — a
    prefix rule and a wildcard body ending in `*` both keep matching as the
    command grows to the right, so covering the local rule's required prefix
    covers every command built on it.  Anything else answers no: a false
    positive trains the warning away, a miss only leaves an entry unreported.
    """
    open_ended = tracked.endswith(':*') or tracked.endswith('*')
    exact_local = not local.endswith(':*') and '*' not in local
    if not (exact_local or open_ended):
        return False
    return matches(tracked, required_prefix(local))


def gate_kept(gated: str, own_ask: list[str]) -> bool:
    """Does a file's own `ask` bucket still gate everything a tracked ask does?

    `gated` is the tracked ask rule's body and `own_ask` the ask bodies declared
    by the file being inspected.

    Whichever way Claude Code resolves two settings files, a file that declares
    the gate beside the grant has not removed it: the ask rule sits at the same
    precedence as the allow next to it, and `ask` beats `allow` within a set.
    Coverage has to run the whole way — a local ask naming one invocation does
    not stand in for a tracked prefix rule gating every command it spells.
    """
    return any(covered_by(gated, own) for own in own_ask)


def re_grants(local: str, gated: str) -> bool:
    """Does a local allow rule hand back a command a tracked `ask` rule gates?

    Either direction of overlap counts.  The shortest command the ask rule
    matches — its body with the wildcards taken out — is a witness both rules
    match, and a local rule the ask rule covers outright is the same fault
    written the other way round.
    """
    witness = gated[:-2] if gated.endswith(':*') else gated.replace('*', '')
    return matches(local, witness) or covered_by(local, gated)


# ── Reading settings files ──────────────────────────────────────────────────

def bodies(permissions: dict, bucket: str) -> list[str]:
    """The Bash rule bodies in one permissions bucket, wrapper stripped."""
    found = (BASH_RULE.match(rule) for rule in permissions.get(bucket) or [])
    return [m.group(1) for m in found if m]


def rules_in(settings: dict) -> list[str]:
    """Collect every permission rule string declared by a settings object."""
    permissions = settings.get('permissions') or {}
    rules = [r for bucket in RULE_BUCKETS for r in permissions.get(bucket) or []]
    # A rule repeated across buckets is one mistake, reported once.
    return list(dict.fromkeys(rules))


def line_of(lines: list[str], rule: str) -> int:
    """1-based line the rule is written on, or 0 if it cannot be located."""
    quoted = json.dumps(rule)
    found = (i for i, line in enumerate(lines, 1) if quoted in line)
    return next(found, 0)


def read_settings(filepath: str) -> Settings | None:
    """Read and parse one settings file, or None if it cannot be read.

    Unreadable covers both halves of the same answer: a file that is not there
    makes no grants, and one holding invalid JSON makes none this can prove.
    Neither is this module's to complain about — the validator reads its own
    files eagerly and a sweep across other people's repos must not abort on a
    checkout with a half-written settings file in it.
    """
    try:
        with open(filepath, encoding='utf-8') as f:
            text = f.read()
        permissions = json.loads(text).get('permissions') or {}
    except (OSError, ValueError):
        return None
    return Settings(text.splitlines(), permissions)


def rules_of(filepath: str) -> TrackedRules:
    """The allow and ask bodies a settings file declares, or none if unreadable."""
    settings = read_settings(filepath)
    if settings is None:
        return TrackedRules()
    return TrackedRules(bodies(settings.permissions, 'allow'),
                        bodies(settings.permissions, 'ask'))


def tracked_rules(repo_root: str) -> TrackedRules:
    """Read the grants a repo's tracked project settings file already makes.

    A repo with no tracked settings file makes no grants, so nothing local can
    duplicate one — every grant there is a one-off and the drift check is a
    no-op.  Per CONTRIBUTING.md § Permission grants that is the normal case for
    a repo `otto-workbench ai init` scaffolded, which is why the sweep pairs
    this with the machine-wide file rather than relying on it alone.
    """
    return rules_of(os.path.join(repo_root, TRACKED_SETTINGS))


def machine_rules(home: str) -> TrackedRules:
    """Read the grants ~/.claude/settings.json makes, which apply everywhere.

    Same relative path as the tracked project file, resolved against the home
    directory instead of a repo root — Claude Code layers the two, and a rule
    written here is in force in every repo on the machine.  `otto-workbench ai
    sync` owns the file's contents; nothing here writes to it.
    """
    return rules_of(os.path.join(home, TRACKED_SETTINGS))


def drift_of(settings: Settings, tracked: TrackedRules) -> Drift:
    """Return the grants in an already-read settings file with a tracked home.

    Only a grant with a tracked home is reportable.  The tracked rules are the
    owner of that list — the directories they grant are read from them, never
    repeated here — and a grant naming something outside them has nowhere to
    move to, so it stays silent.  Flagging every local entry would make the
    check noise, which costs more than the entries it would catch.

    A tracked `ask` rule the file re-declares for itself is not an override —
    see `gate_kept`.  The grant may still be reported as covered, which it is:
    deleting it leaves both the tracked allow and the gate standing.
    """
    own_ask = bodies(settings.permissions, 'ask')
    overrides: list[Grant] = []
    covered: list[Grant] = []
    for body in dict.fromkeys(bodies(settings.permissions, 'allow')):
        rule = f'Bash({body})'
        gated = next((t for t in tracked.ask
                      if re_grants(body, t) and not gate_kept(t, own_ask)), None)
        home = gated or next((t for t in tracked.allow if covered_by(body, t)), None)
        if home is None:
            continue
        grant = Grant(rule, f'Bash({home})', line_of(settings.lines, rule))
        (overrides if gated else covered).append(grant)

    return Drift(overrides, covered)


def drift_in(filepath: str, tracked: TrackedRules) -> Drift:
    """Read a settings file and return the grants in it with a tracked home.

    The path-taking half of `drift_of`, for the caller that reads one file and
    asks one question of it.  It raises what reading the file raises, which is
    what this repo's own gate wants: a settings file it cannot parse is a
    failure, not an empty answer.  A caller asking several questions of the same
    file reads it once with `read_settings` and calls `drift_of` instead.
    """
    with open(filepath, encoding='utf-8') as f:
        text = f.read()
    settings = Settings(text.splitlines(), json.loads(text).get('permissions') or {})
    return drift_of(settings, tracked)


def prune(filepath: str, grants: list[Grant]) -> None:
    """Delete covered grants from an untracked settings file, in place.

    Provably a no-op on effective permissions: every grant passed here is one
    `covered_by` proved a tracked rule already matches for every command the
    local rule can match, so the set of commands the file permits is the same
    before and after.  Only the covered class is ever passed in — an override
    would change permissions, which is why it is reported and left alone.

    Everything else in the file survives: other top-level keys, the other
    buckets, and every grant with no tracked home are written back unchanged.

    Raises whatever reading the file raises.  The caller read it once already to
    find the grants, but Claude Code appends to `settings.local.json` live and
    the sweep runs across repos somebody is working in, so the content here is
    not the content that was classified.
    """
    with open(filepath, encoding='utf-8') as f:
        settings = json.load(f)

    doomed = {grant.rule for grant in grants}
    permissions = settings.get('permissions') or {}
    # An emptied bucket keeps its key rather than being dropped.  `allow` is
    # Claude Code's own and it appends to that list on the next approval, so
    # leaving the key is the smaller edit to a file this script does not own.
    permissions['allow'] = [rule for rule in permissions.get('allow') or []
                            if rule not in doomed]
    settings['permissions'] = permissions
    write_json(filepath, settings)


def write_json(filepath: str, settings: dict) -> None:
    """Write a settings object to a path, replacing any file already there.

    Written beside the target and renamed over it, because both callers write
    unattended across repos the user did not ask them to touch: an interrupted
    write straight to `filepath` would leave a settings file Claude Code can no
    longer parse.  `os.replace` is atomic within a filesystem, and the temp file
    is a sibling to stay on one.

    `ensure_ascii=False` keeps a rule containing an em dash or any other
    non-ASCII character written the way Claude Code writes it.  Escaping it to
    \\uXXXX would rewrite entries the run is not touching, turning a one-line
    deletion into a diff across the whole file.

    An existing file's mode is carried over; a new one keeps the private mode
    `mkstemp` gives it.
    """
    body = json.dumps(settings, indent=2, ensure_ascii=False) + '\n'
    directory = os.path.dirname(filepath) or '.'
    handle, temp = tempfile.mkstemp(dir=directory, prefix='.settings-', suffix='.json')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as f:
            f.write(body)
        if os.path.exists(filepath):
            shutil.copymode(filepath, temp)
        os.replace(temp, filepath)
    except OSError:
        os.unlink(temp)
        raise


# ── Discovery ───────────────────────────────────────────────────────────────

def declares_bash_rules(path: str) -> bool:
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return 'Bash(' in f.read()
    except OSError:
        return False


def discover_settings(repo_root: str) -> list[str]:
    """Find every settings JSON in the repo that declares Bash rules."""
    found: list[str] = []

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in ('.git', 'node_modules')]
        names = [f for f in filenames if f.startswith('settings') and f.endswith('.json')]
        found.extend(os.path.join(dirpath, f) for f in names)

    return sorted(p for p in found if declares_bash_rules(p))


def git(repo_root: str, *args: str) -> str | None:
    """Run a read-only git query in the repo, or None if git cannot answer.

    The environment is cleared of git's own overrides first, because they beat
    `-C`. The pre-push hook exports `GIT_DIR`, and with one set every question
    below is answered for the hook's repository instead of the directory asked
    about — `rev-parse --show-toplevel` at the container answers the container,
    so the no-working-tree guard holds and the container is skipped in exactly
    the run that had to see it.
    """
    try:
        result = subprocess.run(('git', '-C', repo_root, *args),
                                capture_output=True, text=True, check=False,
                                env=git_env_clear())
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def container_dir(repo_root: str) -> str | None:
    """The directory holding the shared git dir, when it is not the worktree.

    In a bare-repo worktree layout every worktree is a peer of the bare `.git`
    inside a container directory, so a `.claude/` written at the container sits
    above anything a walk rooted in a worktree can see — and Claude Code roots
    a session wherever it was launched, the container included.

    `--git-common-dir` names the shared git dir and its parent is the
    container.  In a normal clone that parent is the worktree itself, so the
    comparison makes the extra scan a no-op instead of a special case.  It is
    the comparison rather than an unconditional `..` because the parent of a
    plain checkout belongs to somebody else.

    A container holds no working tree, which is the second half of the test:
    linked worktrees added to an ordinary clone put the shared git dir inside
    the main checkout, and that checkout's `.claude/settings.json` is a tracked
    file with an owner, not an unreviewed local one.
    """
    common = git(repo_root, 'rev-parse', '--git-common-dir')
    toplevel = git(repo_root, 'rev-parse', '--show-toplevel')
    if not common or not toplevel:
        return None
    container = os.path.dirname(os.path.realpath(os.path.join(repo_root, common)))
    if container == os.path.realpath(toplevel):
        return None
    return None if git(container, 'rev-parse', '--show-toplevel') else container


def discover_container_settings(container: str | None) -> list[str]:
    """Find every settings JSON declaring Bash rules in the container's .claude.

    A container with no `.claude/` — or no container at all — contributes
    nothing, which is the normal-clone case CI runs in.
    """
    if container is None:
        return []

    claude = os.path.join(container, '.claude')
    try:
        names = os.listdir(claude)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []

    paths = [os.path.join(claude, f) for f in names
             if f.startswith('settings') and f.endswith('.json')]
    return sorted(p for p in paths if os.path.isfile(p) and declares_bash_rules(p))


def at_container(path: str, container: str | None) -> bool:
    """Is this settings file the container's own, rather than a worktree's?

    Directory equality, not a prefix test: every worktree lives *inside* the
    container, so a prefix test would call the tracked project file local.
    """
    if container is None:
        return False
    return os.path.dirname(os.path.realpath(path)) == os.path.join(container, '.claude')


def manifest(path: str) -> dict | None:
    """The `_workbench` stamp a generated settings file carries, if it has one.

    None whenever there is nothing this can vouch for: the file is unreadable,
    holds invalid JSON, is valid JSON that is not an object, or carries no
    stamp.  Every one of those is a file the workbench did not write in the
    shape it writes, and telling them apart would give a caller nothing to do
    differently.
    """
    try:
        with open(path, encoding='utf-8') as f:
            found = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict):
        return None
    stamp = found.get(MANIFEST_KEY)
    return stamp if isinstance(stamp, dict) else None


def is_managed(path: str) -> bool:
    """Was this settings file generated by the workbench rather than written by hand?

    A generated file carries the `MANIFEST_KEY` stamp naming what the workbench
    put in it.  That is what separates the mirror written at a bare-repo
    container from a grant somebody approved there: the mirror's rules were
    reviewed in the tracked file they were copied from, so they are not drift.

    Unreadable or invalid answers no, which keeps the file in the class that
    gets checked — a settings file this cannot parse is not one it can vouch
    for.

    ceiling: the stamp is a plain key, so a hand-written `_workbench` at a
    container would escape the drift check.  The file is already writable by
    whoever runs the session and this is a hygiene gate rather than a security
    boundary.  Upgrade to comparing the file's rules against the tracked source
    it names if a hand-written stamp ever turns up in a sweep.
    """
    return manifest(path) is not None


def is_local(path: str, in_container: bool) -> bool:
    """Is this an untracked settings file — one whose grants nobody reviews?

    `settings.local.json` always is, wherever it sits: Claude Code appends an
    approved one-off to it and nothing reads the result back.  A settings file
    at a bare-repo container is too by default, because the container holds no
    working tree and nothing there can be tracked — unless the workbench
    generated it, which gives its grants the tracked owner the location cannot.
    """
    if os.path.basename(path) == LOCAL_SETTINGS:
        return True
    return in_container and not is_managed(path)
