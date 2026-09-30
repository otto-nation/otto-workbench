"""What version of the workbench is running, answered in one place.

This used to live in `ai/bin/_version.py` alone, and nothing under `ai/lib`
could import it — so every library caller that wanted a version took one by
injection, and every caller that was not handed one fell back to the literal
string `unknown`. That fallback is invisible at the call site and permanent on
disk: a review written through `pr review` recorded `generator: review
unknown` for months, because the in-process dispatch path had no shim to inject
from, while the same review written through `ai/bin/review` recorded the real
version. Two spellings of one fact, decided by which entry point the operator
happened to use.

Resolution is by layout, so it answers in both places the code runs:

* A checkout has `.github/.release-please-manifest.json` three levels above
  this file, and a git directory to take a short SHA from.
* The packaged tarball has neither. It ships a `VERSION` file beside `lib/`,
  which is what `ai/bin` fell back to reading, and no `.git` at all.

`unknown` survives as the last resort — a version nobody can resolve is still
better recorded than crashed on — but it is now the answer to "neither layout
is present", rather than to "no caller passed me anything".
"""

# doc-group: platform

from __future__ import annotations

import json
import subprocess
from functools import cache
from pathlib import Path

from core import timeouts

UNKNOWN = "unknown"

# ai/lib/core/version.py → ai/lib/core → ai/lib → ai → the workbench root. The
# packaged tarball stops one short: there is no `ai/` there, so `_AI_DIR` is
# the package root and `_ROOT` is whatever sits above it, which the manifest
# lookup below simply fails to find.
_AI_DIR = Path(__file__).resolve().parents[2]
_ROOT = _AI_DIR.parent

MANIFEST_PATH = _ROOT / ".github" / ".release-please-manifest.json"
VERSION_PATH = _AI_DIR / "VERSION"

# The manifest keys release-please writes: the AI tooling's own version, and
# the workbench's.
_TOOL_KEY = "ai/claude"
_ROOT_KEY = "."


def _manifest() -> dict[str, str]:
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _short_sha() -> str:
    """The checkout's short HEAD, or `unknown` where there is no checkout.

    Not an error path worth reporting: the packaged tarball has no `.git`, and
    a version line is not the place to complain about it.
    """
    try:
        return subprocess.check_output(
            ["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, timeout=timeouts.LOCAL,
        ).strip() or UNKNOWN
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return UNKNOWN


@cache
def tool_version() -> str:
    """The AI tooling's version: the manifest's, else the shipped `VERSION`, else `unknown`.

    Cached because it is read once per run at most in the common case and
    shells out in the uncommon one, and because the answer cannot change
    within a process — the files it reads are part of the installation.
    """
    if version := _manifest().get(_TOOL_KEY):
        return version
    try:
        return VERSION_PATH.read_text().strip() or UNKNOWN
    except OSError:
        return UNKNOWN


@cache
def workbench_version() -> str:
    """The workbench's own version and short SHA — `1.47.0 (f4ea2023)`.

    Empty where no manifest is present, so a packaged install records the tool
    version alone rather than the word `unknown` dressed up as a release.
    """
    version = _manifest().get(_ROOT_KEY)
    return f"{version} ({_short_sha()})" if version else ""


def generator(name: str) -> str:
    """`name` and the versions behind it, on one line — the value a `generator:` marker states.

    One line, because it goes in an HTML comment in a review document and a
    header key is a single value. `version_string` renders the same facts over
    two lines for a human reading `--version`; this is the machine-readable
    spelling of them, and both come from here so the two cannot disagree.
    """
    parts = [name, tool_version()]
    if wb := workbench_version():
        parts.append(f"/ otto-workbench {wb}")
    return " ".join(parts)


def version_string(name: str) -> str:
    """The two-line answer `--version` prints.

    The second line is dropped rather than printed as `unknown` when there is
    no manifest: a packaged install has no workbench release to name, and
    saying so with a word that also means "lookup failed" is worse than
    staying quiet.
    """
    head = f"{name} {tool_version()}"
    wb = workbench_version()
    return f"{head}\notto-workbench {wb}" if wb else head
