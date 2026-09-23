"""Where a repo's PR template is, and what it says — resolved in one place.

Three callers need the same answer and each used to work it out for itself:
``lib/ai/pr.sh`` for ``task pr:create``, ``cli/pr_describe.py`` for
``pr describe``, and the SessionStart hook that tells the agent which template
this repo ships. The first two carried the candidate path list and the fallback
template as literals, under a comment asking whoever edited one to remember the
other. They had already drifted from GitHub: neither looked in ``docs/``, which
GitHub has always honoured, so a repo keeping its template there was told it had
none and got the fallback's headers pushed at it instead.

The resolution is a handful of ``stat`` calls against a checkout, so it is
re-derived on every read rather than recorded in config. A path cached in
``.workbench.yml`` is a second answer to a question the filesystem already
answers, and it is wrong the moment someone adds, moves, or deletes the file.
The rediscovery this module exists to stop was never the automation's — it was
the agent's, and the SessionStart line is what settles that.

Bash reads through the CLI below rather than globbing for itself, the same way
it asks ``config_cli.py`` for a config value instead of parsing YAML twice. The
record it prints is:

    <relative path or empty><newline><template text>

The first line is the path relative to the repo root, empty when the repo ships
no template and the text that follows is this module's fallback. A path cannot
contain a newline, so the split is unambiguous — but note that a zero-byte
template yields a record with no newline at all, and a caller splitting on the
first one has to handle that rather than reading the path as the body.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# GitHub's single-file PR template, in all three directories it documents —
# `.github/`, the repo root, and `docs/` — and in both the lowercase and
# uppercase spellings that repos in the wild actually use.
#
# GitHub's filenames are not case sensitive and a checkout on a case-sensitive
# filesystem is, so both spellings are listed rather than one being assumed. The
# order only decides between two templates in one repo, which is a
# misconfiguration either way; what it must not do is miss a directory, which is
# what the two lists this replaces both did with `docs/`.
#
# ceiling: the single-file form only. GitHub also honours a
# `PULL_REQUEST_TEMPLATE/` directory of several templates in any of these three
# folders, and a `.txt` extension alongside `.md`. The directory form is left
# unresolved because GitHub picks between its templates by the `template` query
# parameter on the compare URL, which a resolver reading a checkout has no
# equivalent of — any choice here would be a guess at which one the author
# wanted. Upgrade if a repo we work in adopts the directory form: the resolver
# then needs a way to be told which template to use, not a better guess.
TEMPLATE_PATHS = (
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
)

# What a repo that ships no template gets. Spelled here and rendered into
# `git.generated.md` by `git/bin/local/generate-git-rules`, so the sections the
# rules tell an agent to write are the sections the automation fills.
FALLBACK_TEMPLATE = "## Summary\n\n## Changes\n\n## Testing"

# A section header in a template: `##` followed by whitespace. Nested `###`
# headers are structure inside a section, not sections themselves, so a body is
# never asked to reproduce one.
_HEADER_PREFIX = "## "


@dataclass(frozen=True)
class PRTemplate:
    """A repo's PR template, checked in or fallen back to.

    ``path`` is relative to the repo root and empty when nothing is checked in,
    which is also what ``found`` reports — two readings of one fact, named
    rather than left for each caller to infer from an empty string.
    """

    text: str
    path: str

    @property
    def found(self) -> bool:
        """Whether the repo ships a template of its own."""
        return bool(self.path)

    @property
    def headers(self) -> list[str]:
        """The section headers a body using this template must carry."""
        return [
            line.strip()
            for line in self.text.splitlines()
            if line.startswith(_HEADER_PREFIX)
        ]


def load(root: Path) -> PRTemplate:
    """Resolve the PR template for the checkout at ``root``.

    Every candidate is repo-root-relative, so ``root`` must be the top of the
    work tree — resolved from a subdirectory, every candidate misses and the
    repo looks like one shipping no template.

    An unreadable file is treated as absent rather than raised. The callers are
    a PR description generator and a context line; neither is improved by
    failing over a template whose permissions are wrong, and both degrade to the
    fallback the same way a repo with no template does. A file that exists and
    is readable but is not valid UTF-8 — a binary file mistakenly named like a
    template — degrades the same way: ``read_text`` raises ``UnicodeDecodeError``
    rather than ``OSError`` for that case, so both are caught here.
    """
    for candidate in TEMPLATE_PATHS:
        path = root / candidate
        if not path.is_file():
            continue
        try:
            return PRTemplate(text=path.read_text(), path=candidate)
        except (OSError, UnicodeDecodeError):
            continue
    return PRTemplate(text=FALLBACK_TEMPLATE, path="")


def main(argv: list[str] | None = None) -> int:
    """Print the record documented in the module docstring, for bash.

    Exits 0 whether or not a template was found — "this repo ships none" is an
    answer, not a failure, and the empty first line carries it. A non-zero exit
    is reserved for a caller naming a root that is not a directory, which is a
    caller bug rather than a repo state.
    """
    parser = argparse.ArgumentParser(
        description="Resolve a repo's PR template path and contents.")
    parser.add_argument(
        "--root", default=".",
        help="repo root to resolve against (default: the working directory)")
    parser.add_argument(
        "--fallback", action="store_true",
        help="print the fallback template alone, resolving no repo")
    ns = parser.parse_args(argv)

    # The rules generator wants the fallback itself, not a repo's answer. Asked
    # for outright rather than extracted from a record resolved against an empty
    # directory: that spelling needs a temp directory to clean up and a second
    # stage to strip the path line, and a shell pipeline's exit status is the
    # stripper's rather than this program's.
    if ns.fallback:
        print(FALLBACK_TEMPLATE)
        return 0

    root = Path(ns.root)
    if not root.exists():
        print(f"no such directory: {root}", file=sys.stderr)
        return 1
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    template = load(root)
    print(template.path)
    print(template.text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
