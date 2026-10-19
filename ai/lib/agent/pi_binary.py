"""Which `pi` binary every Pi-backend call runs, chosen once per directory.

A bare ``"pi"`` resolves through ``PATH``, so the version that ran depended on
how the calling shell was set up: inside a repo that pins pi through mise, a
background job without ``~/.pi/agent/bin`` on ``PATH`` got the repo's pin, and a
pin older than the configured model needs refused every run.

The rule is that a repo's pin is a *floor*. The workbench should not run far
from the pi a repo works with, but it should not be held back by a repo that is
slow to update either:

- the managed install (``~/.pi/agent/install``) when it is at least the pin;
- the repo's pin when it is newer than the managed install;
- the managed install when the repo pins nothing;
- ``pi`` on ``PATH`` when there is no managed install and no pin.

Only a pin declared inside the repo counts — a mise config file under the
directory holding the repo's common git dir, which covers both an ordinary
clone and a bare-repo container of worktrees. A global mise pin says nothing
about the repo being worked on, and a repo without mise pins nothing.

A version that cannot be read is an error naming its source, never a guess:
choosing between two binaries on a version nobody could read would be choosing
at random.

Every resolution is logged and, inside a trailed run, recorded on the trail as
a ``pi_binary`` event, so `otto-log show` can say which binary ran and why.

How the chosen binary is *started* is `PiBinary.spawn`'s answer. pi is a
``#!/usr/bin/env node`` script, and ``env node`` finds whatever ``node`` is first
on ``PATH`` — on a machine using mise shims, a shim. A shim applies the ``[env]``
of every trusted mise config at and above its working directory, and an agent is
spawned in the repo it works on, so a repo setting its own
``GOOGLE_CLOUD_PROJECT`` overrode the environment the workbench handed pi and
every model call failed with a 403. So pi is spawned as ``[node, <pi script>]``
with a `real_node` that is not a shim, and ``PATH`` is left as it was: the
commands pi runs still reach the repo's mise toolchain, only pi's own process
does not.
"""

# doc-group: backend

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import core.log
import core.proc
import core.timeouts
import core.trail
import git.rewrites

MANAGED_ROOT_ENV = "PI_MANAGED_INSTALL_ROOT"
# The npm package pi ships as, under its current and its former scope. A mise
# tool key is `npm:<package>`, so the scope-free suffix matches either.
PACKAGE_SUFFIX = "/pi-coding-agent"
TRAIL_ACTION = "pi_binary"

# npm-style: up to three numeric parts, an optional prerelease and build tag.
# Missing parts pad to zero, so a fuzzy mise pin such as `0.87` reads as the
# lower bound it is.
_VERSION_RE = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


class Source(str, Enum):
    MANAGED = "managed"
    REPO_PIN = "repo-pin"
    PATH = "path"


class PiUnresolvable(RuntimeError):
    """No pi binary can be chosen: a version is unreadable or nothing is installed."""


@dataclass(frozen=True)
class PiBinary:
    """The chosen binary, its version, and the evidence the choice was made on."""

    path: str
    version: str
    source: Source
    reason: str
    managed_version: str | None = None
    pin_version: str | None = None
    pin_source: str | None = None
    # A managed install only: the release the launcher at `path` would exec,
    # and the install root the launcher exports to it.
    release: str | None = None
    install_root: str | None = None

    def spawn(self, env: Mapping[str, str]) -> PiSpawn:
        """The argv prefix and environment that start this pi from *env*.

        ``[real_node, script]`` when the script pi runs is an ``env node``
        script and `real_node` finds a node that is not a mise shim; otherwise
        ``[path]``, exactly as before. *env* is not modified, and its ``PATH``
        reaches pi unchanged.

        For a managed install the script is the release and the launcher's own
        environment is reproduced: ``PI_MANAGED_INSTALL_ROOT``, and pi's bundled
        node directory in front of ``PATH`` when that is the node chosen — the
        two things the launcher does besides exec'ing the release.
        """
        script = self.release or self.path
        node = real_node(env) if _runs_on_env_node(script) else None
        if node is None:
            # ceiling: no node but a shim (a machine whose only node is mise's,
            # reached through shims), so `env node` is left to find it and a
            # trusted repo's mise [env] still reaches pi. Upgrade trigger: once
            # a run on such a machine is refused for the repo's environment,
            # resolve the shim's target from mise's global config in $HOME.
            return PiSpawn((self.path,), dict(env))
        out = dict(env)
        if self.install_root:
            out[MANAGED_ROOT_ENV] = self.install_root
            bundled = pi_node(env)
            if node == str(bundled):
                out["PATH"] = os.pathsep.join(filter(None, [str(bundled.parent), env.get("PATH", "")]))
        return PiSpawn((node, script), out)


@dataclass(frozen=True)
class PiSpawn:
    """How one pi process starts: the argv in front of pi's own flags, and its env."""

    argv: tuple[str, ...]
    env: dict[str, str]


@dataclass(frozen=True)
class _Pin:
    version: str
    source: str
    install_path: str
    installed: bool


@dataclass(frozen=True)
class _PinLookup:
    """A repo pin, or None with the reason there is none."""

    pin: _Pin | None
    note: str


@dataclass(frozen=True)
class _Managed:
    launcher: str
    version: str
    root: str
    release: str


def version_key(raw: str, origin: str) -> tuple:
    """A sort key for an npm-style version. Raises `PiUnresolvable` naming *origin*.

    A prerelease sorts below its release, as semver orders them.
    """
    match = _VERSION_RE.match(raw.strip())
    if not match:
        raise PiUnresolvable(f"cannot read a pi version from {origin}: {raw.strip()!r}")
    major, minor, patch = (int(part or 0) for part in match.group(1, 2, 3))
    pre = match.group(4)
    # Numeric prerelease identifiers compare as numbers (rc.9 < rc.10) and sort
    # below alphanumeric ones, as semver orders them.
    ident = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in (pre or "").split("."))
    return (major, minor, patch, 0 if pre else 1, ident)


def managed_root() -> Path:
    """The managed install's root: ``$PI_MANAGED_INSTALL_ROOT``, else ``~/.pi/agent/install``.

    Pi's launcher exports the variable to its own children only, so a shell
    started any other way relies on the default.
    """
    override = os.environ.get(MANAGED_ROOT_ENV)
    return Path(override) if override else Path.home() / ".pi" / "agent" / "install"


def _data_home(env: Mapping[str, str]) -> Path:
    """``${XDG_DATA_HOME:-$HOME/.local/share}`` as *env* spells it, empty meaning unset."""
    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path(env.get("HOME") or Path.home()) / ".local" / "share"


def pi_node(env: Mapping[str, str]) -> Path:
    """The node pi's installer provides, at the path pi's launcher looks for it."""
    return _data_home(env) / "pi-node" / "current" / "bin" / "node"


def _executable_file(path: str | Path) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _is_mise_shim(candidate: str, shims_dir: str) -> bool:
    """Whether *candidate* is a mise shim: a link to the mise binary, or in mise's shims dir.

    Both are compared whole — the resolved file's name against ``mise``, the
    resolved directory against the shims directory — so ``mise-tools/bin`` is
    not mistaken for either.
    """
    if os.path.basename(os.path.realpath(candidate)) == "mise":
        return True
    return os.path.realpath(os.path.dirname(candidate)) == shims_dir


def real_node(env: Mapping[str, str]) -> str | None:
    """A node executable that is not a mise shim, from *env*; None when there is none.

    pi's bundled node (`pi_node`) when it is executable — the one pi's launcher
    puts first — else the first ``node`` on *env*'s ``PATH`` that is not a mise
    shim, else None. Relative ``PATH`` entries are skipped.

    `mise which node` is not asked: it fails where node is not active in the
    global config, and the answer needed is any real node, not mise's.
    """
    bundled = pi_node(env)
    if _executable_file(bundled):
        return str(bundled)
    data = env.get("MISE_DATA_DIR")
    shims_dir = os.path.realpath((Path(data) if data else _data_home(env) / "mise") / "shims")
    for entry in (env.get("PATH") or "").split(os.pathsep):
        if not os.path.isabs(entry):
            continue
        candidate = os.path.join(entry, "node")
        if _executable_file(candidate) and not _is_mise_shim(candidate, shims_dir):
            return candidate
    return None


def _runs_on_env_node(script: str) -> bool:
    """Whether *script*'s first line is exactly ``#!<…>/env node``.

    That is the form that finds node through ``PATH``, and so through a shim. A
    shebang naming node by absolute path never reaches a shim, and one passing
    node flags would lose them if node were put in front of it — both, and
    anything that is not a script, run as they are.
    """
    try:
        with open(script, "rb") as fh:
            first = fh.readline(256)
    except OSError:
        return False
    if not first.startswith(b"#!"):
        return False
    words = first[2:].decode(errors="replace").split()
    return len(words) == 2 and os.path.basename(words[0]) == "env" and words[1] == "node"


def _managed() -> _Managed | None:
    """The managed install, None when there is none, or `PiUnresolvable` when broken.

    The version is the launcher's own ``current-version`` file — the one it
    reads to pick a release — so it names the release that will run. The
    release is spawned directly under a real node (see `PiBinary.spawn`), so
    the release logged is the release that runs; the launcher is kept for the
    fallback when no real node can be found.
    """
    root = managed_root()
    version_file = root / "current-version"
    if not version_file.is_file():
        return None
    try:
        version = version_file.read_text().strip()
    except OSError as exc:
        raise PiUnresolvable(f"cannot read the managed pi version from {version_file}: {exc}") from exc
    version_key(version, str(version_file))
    launcher = root.parent / "bin" / "pi"
    release = root / "releases" / version / "node_modules" / ".bin" / "pi"
    for need in (launcher, release):
        if not os.access(need, os.X_OK):
            raise PiUnresolvable(
                f"the managed pi install at {root} names {version} but {need} is not executable"
            )
    return _Managed(str(launcher), version, str(root), str(release))


def _mise_current(cwd: str) -> dict:
    """``mise ls --current --json`` in *cwd*. Raises `PiUnresolvable` when mise fails."""
    try:
        result = core.proc.run(
            ["mise", "ls", "--current", "--json"], cwd=cwd, timeout=core.timeouts.LOCAL,
        )
    except OSError as exc:
        raise PiUnresolvable(f"cannot run mise to read the repo's pi pin: {exc}") from exc
    if not result.ok:
        raise PiUnresolvable(core.proc.failure_message(
            f"mise could not report the tools pinned in {cwd}", result,
        ))
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PiUnresolvable(f"mise ls --current --json in {cwd} printed unreadable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PiUnresolvable(f"mise ls --current --json in {cwd} printed {type(data).__name__}, not a map")
    return data


def _pin_entry(data: dict) -> dict | None:
    """The first active entry for pi's npm package, or None when mise pins none."""
    for key, entries in data.items():
        if not key.endswith(PACKAGE_SUFFIX) or not isinstance(entries, list) or not entries:
            continue
        # mise allows several versions of one tool; the first is the one on PATH.
        entry = entries[0]
        return entry if isinstance(entry, dict) else None
    return None


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _repo_pin(cwd: str) -> _PinLookup:
    """The pi version *cwd*'s repo pins through mise, or None with the reason."""
    if not shutil.which("mise"):
        return _PinLookup(None, "mise is not installed")
    common = git.rewrites.common_dir(cwd)
    if common is None:
        return _PinLookup(None, f"{cwd} is not in a git repository")
    repo = common.parent
    entry = _pin_entry(_mise_current(cwd))
    if entry is None:
        return _PinLookup(None, "mise pins no pi here")
    source = str((entry.get("source") or {}).get("path") or "")
    if not source:
        raise PiUnresolvable(f"mise reports a pi pin in {cwd} without naming the file that sets it")
    if not _inside(source, repo):
        return _PinLookup(None, f"the pi pin in {source} is outside the repo")
    version = str(entry.get("version") or "")
    version_key(version, f"the repo pin in {source}")
    return _PinLookup(
        _Pin(version, source, str(entry.get("install_path") or ""), bool(entry.get("installed"))),
        "",
    )


def _pin_binary(pin: _Pin, cwd: str) -> str:
    """The pinned binary, asked of mise. Raises when it is missing or not the pin's."""
    if not pin.installed:
        raise PiUnresolvable(
            f"the repo pins pi {pin.version} ({pin.source}) but it is not installed — "
            f"run `mise install` in {cwd}"
        )
    try:
        result = core.proc.run(["mise", "which", "pi"], cwd=cwd, timeout=core.timeouts.LOCAL)
    except OSError as exc:
        raise PiUnresolvable(f"cannot run mise to find the pinned pi binary: {exc}") from exc
    path = result.stdout.strip() if result.ok else ""
    if not path or not pin.install_path or not _inside(path, Path(pin.install_path)):
        raise PiUnresolvable(
            f"mise names no pi binary under {pin.install_path or 'the pin'} for the repo pin "
            f"{pin.version} ({pin.source}): `mise which pi` answered {path or 'nothing'}"
        )
    return path


def _path_binary(note: str, root: Path) -> PiBinary:
    """``pi`` from PATH, with its version asked of the binary itself."""
    path = shutil.which("pi")
    if not path:
        raise PiUnresolvable(
            f"no pi to run: no managed install at {root}, {note}, and no pi on PATH"
        )
    try:
        result = core.proc.run([path, "--version"], timeout=core.timeouts.LOCAL)
    except OSError as exc:
        raise PiUnresolvable(f"cannot run {path} --version: {exc}") from exc
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not result.ok or not lines:
        raise PiUnresolvable(core.proc.failure_message(f"{path} --version gave no version", result))
    version = lines[-1].strip()
    version_key(version, f"{path} --version")
    return PiBinary(
        path, version, Source.PATH,
        f"no managed install at {root} and {note}; using pi from PATH",
    )


def _choose(cwd: str) -> PiBinary:
    managed = _managed()
    lookup = _repo_pin(cwd)
    pin = lookup.pin
    if managed is None and pin is None:
        return _path_binary(lookup.note, managed_root())
    if pin is None:
        return PiBinary(
            managed.launcher, managed.version, Source.MANAGED,
            f"{lookup.note}; using the managed install", managed_version=managed.version,
            release=managed.release, install_root=managed.root,
        )
    pinned = dict(pin_version=pin.version, pin_source=pin.source)
    if managed is None:
        return PiBinary(
            _pin_binary(pin, cwd), pin.version, Source.REPO_PIN,
            f"no managed install at {managed_root()}; using the repo pin", **pinned,
        )
    newer = version_key(pin.version, pin.source) > version_key(managed.version, managed.root)
    if newer:
        return PiBinary(
            _pin_binary(pin, cwd), pin.version, Source.REPO_PIN,
            f"the repo pin {pin.version} is newer than the managed {managed.version}",
            managed_version=managed.version, **pinned,
        )
    # A tie takes the managed install: either satisfies the floor, and this way
    # equal versions run one binary in every repo without asking mise for its own.
    relation = "matches" if managed.version == pin.version else "is newer than"
    return PiBinary(
        managed.launcher, managed.version, Source.MANAGED,
        f"the managed {managed.version} {relation} the repo pin {pin.version}",
        managed_version=managed.version, release=managed.release,
        install_root=managed.root, **pinned,
    )


def _announce(binary: PiBinary, cwd: str) -> None:
    core.log.dim(f"pi {binary.version} ({binary.source.value}, {binary.path}): {binary.reason}")
    # Only inside a trailed run: `record_event` opened with no root to inherit
    # would publish itself as this process's root and adopt every later trail.
    if not core.trail.inherited_root():
        return
    data = {
        "cwd": cwd, "path": binary.path, "version": binary.version,
        "source": binary.source.value, "managed_version": binary.managed_version,
        "pin_version": binary.pin_version, "pin_source": binary.pin_source,
    }
    core.trail.record_event(core.trail.script_name(), TRAIL_ACTION, detail=binary.reason, data=data)


_lock = threading.Lock()


@functools.cache
def _resolved(real_cwd: str) -> PiBinary:
    binary = _choose(real_cwd)
    _announce(binary, real_cwd)
    return binary


def resolve(cwd: str | Path) -> PiBinary:
    """The pi binary to run in *cwd*. Raises `PiUnresolvable` with the reason.

    Cached per directory for the life of the process, so every call in one run
    — the catalogue, the budget, every spawn — agrees, and the choice is logged
    once. Mise resolves per directory, so a process touching two repos gets an
    answer for each. A failure is not cached: the next caller sees it again.

    A *cwd* that is not a directory is refused here rather than left to the
    git and mise subprocesses, which would raise `FileNotFoundError` for the
    working directory — an error the optional budget lookup does not expect.
    """
    real = os.path.realpath(cwd)
    if not os.path.isdir(real):
        raise PiUnresolvable(f"cannot choose a pi for {cwd}: it is not a directory")
    with _lock:
        return _resolved(real)


def any_installed() -> bool:
    """Whether some pi could run: a managed install, or a ``pi`` on PATH.

    A cheap presence check for deciding whether to offer AI work at all. It
    does not read versions: a broken install fails loudly at the spawn, which
    is where its error can be reported, instead of quietly switching AI off.
    """
    return (managed_root() / "current-version").is_file() or shutil.which("pi") is not None
