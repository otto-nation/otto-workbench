"""GitHub token resolution for the commands that publish a PR.

One owner for the per-org PAT routing ``task pr:*`` has used since the
automation token was split from the interactive ``gh`` login. Resolution order,
first match wins:

1. ``GH_TOKEN`` in ``<repo>/.taskfile/taskfile.env`` — a per-repo pin
2. ``GH_TOKEN__<ORG>`` in ``~/.config/task/taskfile.env``, ORG from ``origin``
3. ``GH_TOKEN`` in ``~/.config/task/taskfile.env``
4. a non-empty ``GH_TOKEN`` already in the environment (CI, ``~/.env.local``)

Tiers 2 and 3 always read the global file. A local file that pins nothing does
not disable org routing.

Org and host come from ``pr.target.repo_identity_from_origin`` — the remote
parser every other ``pr`` command keys on — so every remote spelling git
accepts routes the same way, and a GitHub Enterprise remote links its own PAT
page in the failure guidance.

Run as a script, the token is the only thing on stdout (for ``load_gh_token``
in ``lib/ai/core.sh``) and the guidance goes to stderr, so a failure can never
be captured into ``GH_TOKEN``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Run as a script, sys.path[0] is this file's directory. `pr.target` resolves
# against ai/lib, one level up — added here rather than by the caller for the
# reason git/push.py gives: a mise shim can replace PYTHONPATH before exec.
_AI_LIB = Path(__file__).resolve().parent.parent
if _AI_LIB.is_dir() and str(_AI_LIB) not in sys.path:
    sys.path.insert(0, str(_AI_LIB))

import pr.target

TOKEN_VAR = "GH_TOKEN"
LOCAL_ENV_PATH = Path(".taskfile") / "taskfile.env"

_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz-", "ABCDEFGHIJKLMNOPQRSTUVWXYZ_")


class TokenSource(StrEnum):
    """Which tier answered."""

    LOCAL = "local"
    ORG = "org"
    DEFAULT = "default"
    ENVIRONMENT = "environment"


@dataclass(frozen=True)
class Token:
    """A resolved token and where it came from."""

    value: str
    source: TokenSource
    variable: str


class TokenNotConfigured(Exception):
    """No tier answered. ``guidance`` is what to print to the operator."""

    def __init__(self, guidance: str):
        super().__init__(guidance)
        self.guidance = guidance


def global_env_path(home: Path | None = None) -> Path:
    """``~/.config/task/taskfile.env`` — ``TASKFILE_ENV`` in lib/constants.sh."""
    return (home or Path.home()) / ".config" / "task" / "taskfile.env"


def org_variable(org: str) -> str:
    """``otto-nation`` → ``GH_TOKEN__OTTO_NATION``. ASCII-only by construction."""
    return f"{TOKEN_VAR}__{org.translate(_UPPER)}"


def read_env_value(path: Path, key: str) -> str | None:
    """The value of the first ``KEY=`` line in *path*, verbatim, or None.

    A missing file is "not set". Any other read error propagates — an
    unreadable credentials file is a fault to report, not a tier to skip.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    prefix = f"{key}="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def _org(identity: pr.target.RepoIdentity | None) -> str:
    """The first path segment of a hosted remote, or ``""``.

    A local remote canonicalises to a single segment (see
    ``pr.target._canonical``), so it names no org and is not guessed at.
    """
    if identity is None or "/" not in identity.label:
        return ""
    return identity.label.partition("/")[0]


def _guidance(env_file: Path, org: str, host: str) -> str:
    lines = [f"✗ {TOKEN_VAR} not configured for AI automation."]
    if org:
        lines.append(f"  Set {org_variable(org)} (for {org}) or {TOKEN_VAR} (default) in {env_file}")
    else:
        lines.append(f"  Set {TOKEN_VAR} in {env_file}")
    # The PAT is issued by the instance the repo lives on. A GHES user sent to
    # github.com creates a token for the wrong instance and gets a 401.
    lines.append(f"  Create a fine-grained PAT: {pr.target.forge_base_url(host)}/settings/tokens/new")
    lines.append("  Required: Contents (read/write), Pull requests (read/write) — scoped to specific repos")
    lines.append("  Run: task --global ai:setup")
    return "\n".join(lines)


def resolve(
    cwd: Path,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Token:
    """The token for the repo at *cwd*. Raises ``TokenNotConfigured``."""
    env = os.environ if environ is None else environ

    local = read_env_value(cwd / LOCAL_ENV_PATH, TOKEN_VAR)
    if local is not None:
        return Token(local, TokenSource.LOCAL, TOKEN_VAR)

    env_file = global_env_path(home)
    identity = pr.target.repo_identity_from_origin(str(cwd))
    org = _org(identity)
    if org:
        variable = org_variable(org)
        value = read_env_value(env_file, variable)
        if value is not None:
            return Token(value, TokenSource.ORG, variable)

    value = read_env_value(env_file, TOKEN_VAR)
    if value is not None:
        return Token(value, TokenSource.DEFAULT, TOKEN_VAR)

    if env.get(TOKEN_VAR):
        return Token(env[TOKEN_VAR], TokenSource.ENVIRONMENT, TOKEN_VAR)

    raise TokenNotConfigured(_guidance(env_file, org, identity.host if identity else ""))


def main(argv: list[str] | None = None) -> int:
    """Print the resolved token, or the guidance on stderr and exit 1."""
    parser = argparse.ArgumentParser(description="Resolve GH_TOKEN for AI automation.")
    parser.add_argument("--cwd", default=".", help="repo whose origin selects the org (default: .)")
    args = parser.parse_args(argv)
    try:
        token = resolve(Path(args.cwd).resolve())
    except TokenNotConfigured as exc:
        print(exc.guidance, file=sys.stderr)
        return 1
    print(token.value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
