"""The machine's environment for an agent, without the mise config of the repo it runs in.

`pr` runs under ``#!/usr/bin/env python3``. Where ``python3`` is a mise shim and
`pr` starts inside a repo whose mise config is trusted, the shim applies that
config's ``[env]`` before any workbench code runs, and every agent inherits it.
A shell running ``mise activate`` leaks the same way from whichever directory
it last entered. A repo that sets its own ``GOOGLE_CLOUD_PROJECT`` then sends
every agent's model calls to a project the review identity cannot use: each
agent fails with a 403 and spends nothing.

`base_env` undoes that. mise records what it changed, with the values it
overwrote, in ``__MISE_DIFF``, and ``mise hook-env`` run from ``$HOME`` turns
that record into the shell that restores the machine's state — global config
only. The shell is evaluated by bash and the environment is compared before and
after inside that same bash, so the format mise writes is never parsed here.
What the process added since it started (a ``PATH`` entry, a token) survives,
because only the keys mise changed move.

It also ignores, never trusts, every untrusted mise config at or above the
agent's working directory. pi itself is started under a node that is not a
mise shim (`agent.pi_binary.PiBinary.spawn`) — undoing the parent's changes here
cannot stop a shim from applying the worktree's trusted ``[env]`` afresh, so pi
must not reach one at all. The commands an agent runs in a fresh review
worktree do reach shims, and mise refuses each one outright while the
worktree's config is untrusted. Ignoring means mise does not read the file at
all, so nothing in it runs; trusted configs stay active, so a fix agent running
the repo's tests still gets the repo's toolchain.
"""

# doc-group: backend

from __future__ import annotations

import functools
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import core.errors
import core.log
import core.proc
import core.timeouts

# What mise leaves behind when it has applied a config to this process.
MISE_DIFF_ENV = "__MISE_DIFF"
MISE_IGNORED_ENV = "MISE_IGNORED_CONFIG_PATHS"

# Separates the two environment listings. Every entry holds an `=`, so an entry
# without one cannot be mistaken for a variable.
_MARKER = "--workbench-mise-hook-env--"

# Prints the environment NUL-separated. This interpreter rather than `env -0`,
# which BSD userland does not reliably offer.
_PRINT_ENV = (
    "import os, sys; "
    "sys.stdout.write(''.join(f'{k}={v}\\0' for k, v in os.environ.items()))"
)

# `hook=$(…) || exit` because an `eval "$(failing)"` evaluates nothing and
# succeeds, which would report the repo's environment as the machine's. The
# eval's own output goes to stderr: the completion functions mise emits are not
# part of the environment and must not land between the listings. `$1` is the
# interpreter, `$2` the listing program.
_RESTORE_SCRIPT = (
    f'"$1" -c "$2" && printf "%s\\0" "{_MARKER}" && '
    'hook=$(mise hook-env -s bash --force) || exit $?; '
    'eval "$hook" >&2; exec "$1" -c "$2"'
)

# bash rewrites `_` after every command, so it differs between the listings
# without mise touching it.
_SHELL_NOISE = frozenset({"_"})

_UNTRUSTED_SUFFIX = ": untrusted"


class MachineEnvUnavailable(core.errors.OperatorError):
    """mise applied a repo's environment to this process and could not undo it."""


@dataclass(frozen=True)
class EnvDelta:
    """What restoring the machine's environment changes: keys to set and keys to drop."""

    set: Mapping[str, str]
    unset: frozenset[str]

    def apply(self, base: Mapping[str, str]) -> dict[str, str]:
        """A copy of *base* with this delta applied; *base* is not modified."""
        env = {k: v for k, v in base.items() if k not in self.unset}
        env.update(self.set)
        return env


def _listing(raw: str) -> dict[str, str]:
    return dict(entry.split("=", 1) for entry in raw.split("\0") if "=" in entry)


def _delta(stdout: str) -> EnvDelta:
    before_raw, sep, after_raw = stdout.partition(f"\0{_MARKER}\0")
    if not sep:
        raise MachineEnvUnavailable(
            "mise hook-env printed no environment to compare against the repo's",
        )
    before, after = _listing(before_raw), _listing(after_raw)
    return EnvDelta(
        set={k: v for k, v in after.items() if before.get(k) != v and k not in _SHELL_NOISE},
        unset=frozenset(before) - frozenset(after) - _SHELL_NOISE,
    )


@functools.lru_cache(maxsize=8)
def _restore_delta(base: frozenset[tuple[str, str]]) -> EnvDelta:
    """The delta that restores *base*, as items. Raises `MachineEnvUnavailable`.

    Keyed on the whole environment, because which keys the undo drops depends
    on which are present. Cached because the bash and mise round trip costs a
    quarter of a second and a review spawns a dozen agents from an environment
    that rarely changes between them. Any change to any variable
    (`GH_TOKEN` set for publishing, say) is a new key and pays the cost again.
    """
    if not shutil.which("mise"):
        raise MachineEnvUnavailable(
            f"{MISE_DIFF_ENV} says mise changed this process's environment, but mise is "
            "not on PATH to undo it",
        )
    r = core.proc.run(
        ["bash", "-c", _RESTORE_SCRIPT, "bash", sys.executable, _PRINT_ENV],
        cwd=os.path.expanduser("~"), timeout=core.timeouts.LOCAL, env=dict(base),
    )
    if not r.ok:
        raise MachineEnvUnavailable(core.proc.failure_message(
            "undo the repo's mise environment with `mise hook-env` from $HOME", r,
        ))
    return _delta(r.stdout)


def machine_env(base: Mapping[str, str]) -> dict[str, str]:
    """*base* with every change mise's record in it says a repo config made, undone.

    A *base* without ``__MISE_DIFF`` is returned as a copy, unchanged: mise
    touched nothing. Raises `MachineEnvUnavailable` when the record is there
    and mise cannot read it, rather than handing an agent the repo's
    credentials.
    """
    if not base.get(MISE_DIFF_ENV):
        return dict(base)
    return _restore_delta(frozenset(base.items())).apply(base)


@functools.lru_cache(maxsize=64)
def untrusted_dirs(cwd: str) -> tuple[str, ...]:
    """The directories at or above *cwd* whose mise config is untrusted, absolute.

    Empty when mise is not installed. When ``mise trust --show`` fails the
    failure is logged and the answer is empty: the agent then runs as it did
    before this existed, and each refused command reports the untrusted file
    itself, so nothing is hidden.
    """
    if not shutil.which("mise"):
        return ()
    # ceiling: `mise trust --show` reports *cwd* and its parents only, so an
    # untrusted config in a subdirectory (a monorepo package) is not ignored and
    # an agent command run there is still refused. Upgrade trigger: once a
    # review reports such a refusal, walk the worktree for mise config files.
    r = core.proc.run(["mise", "trust", "--show"], cwd=cwd, timeout=core.timeouts.LOCAL)
    if not r.ok:
        core.log.warn(core.proc.failure_message(f"list untrusted mise configs in {cwd}", r))
        return ()
    return tuple(
        os.path.expanduser(line[: -len(_UNTRUSTED_SUFFIX)])
        for line in r.stdout.splitlines()
        if line.endswith(_UNTRUSTED_SUFFIX)
    )


def base_env(cwd: str) -> dict[str, str]:
    """The environment an agent working in *cwd* inherits when its caller supplied none.

    This process's environment with the repo's mise changes undone
    (`machine_env`) and the untrusted configs at or above *cwd* added to
    ``MISE_IGNORED_CONFIG_PATHS``, after any entries the operator exported.
    Raises `MachineEnvUnavailable`.
    """
    env = machine_env(os.environ)
    ignored = untrusted_dirs(cwd)
    if ignored:
        prior = env.get(MISE_IGNORED_ENV, "")
        env[MISE_IGNORED_ENV] = os.pathsep.join(filter(None, [prior, *ignored]))
    return env
