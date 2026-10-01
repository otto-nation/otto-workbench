"""Which item a red suite is probably complaining about.

`fix.suite` can tell the tree is broken and not which of sixteen items broke
it, so a red run marks every claimed fix unverified. That is the honest answer
and it is a poor one to read: seven hedged rows, one of them wrong.

A failing test carries more than the path-attribution ceiling in
`fix.reconcile` has to work with. That ceiling is about a *file having moved* —
"something changed here", with nothing to say what. A failure has a name, an
error, and the values the runner printed. Both regressions that motivated the
suite named the symbol the pass had just deleted:

    AttributeError: module 'cli.pr' has no attribute 'EXIT_BUDGET_EXHAUSTED'

So: intersect the symbols an item's anchor file *lost* in this pass with the
text of the failure. No test selector, no import graph, no structured runner
output, no new cross-repo contract — a diff and a string.

**This points, it does not judge.** Nothing here changes an outcome. A wrong
pointer costs a reader one wasted look; a wrong demotion records the right fix
as the defect, which is the mistake `fix.suite.apply_to` already refuses to
make. Every claimed fix stays `verified = False` on a red run exactly as before
— this only says where to start.

**Why the two filters, measured rather than guessed.** Across 319 changed files
from 25 real fix passes in this repo, plain token intersection implicated 11%
of them against a failure none of them caused. The noise is prose: a diff
carries comments and markdown, a pytest run is full of English, and `gate`,
`review` and `could` all match. Two filters close it:

  - **Symbol-shaped only.** A token counts if it contains an underscore or an
    uppercase letter — snake_case, CamelCase, SCREAMING_CASE — and never if it
    is a plain lowercase word. 11% to 0.9%, catching both known regressions.
  - **Rare in the repo.** `read_text`, `Path` and `ArgumentParser` were the
    entire remaining tail. A name a hundred files mention says nothing about
    who removed one reference to it. 0.9% to 0%.

**Two ceilings, both real.**

`// ceiling: attribution is per anchor *file*, not per item. Two findings in
one file are indistinguishable, which is the genuine residue of the ceiling
`fix.reconcile` documents. Upgrade trigger: if a pass is seen attributing to
the wrong one of two same-file items, carry the agent's hunk ranges per item
and rank by distance to the anchor line.`

`// ceiling: the signal is the runner's verbosity, not this rule's. pytest
rewrites assertions and echoes values, so a symbol shows up; bats prints only
the failing assertion's source line unless `--print-output-on-failure` is set,
which `bin/local/run-tests` now passes. Upgrade trigger: when a repo declares
a verify command whose runner prints neither, this returns nothing and should
keep returning nothing rather than guessing from exit codes.`

`// ceiling: the rarity threshold is fitted on one repo's naming, so 0% false
positives is a measurement here and not a guarantee elsewhere. Upgrade
trigger: if a repo reports pointers that are routinely wrong, raise
`_MAX_FILES` for it or drop the pointer line rather than keeping a confident
wrong answer.`
"""

# doc-group: pipeline

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from git import client as git_client

# An identifier of at least four characters. The length floor is what keeps
# `ai/bin/pr` from contributing `ai`, `bin` and `pr` when a path is reworded.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

# A symbol rather than an English word: it has to carry an underscore or an
# uppercase letter somewhere. This is the filter that does most of the work —
# a diff is full of prose and so is a test runner's output, and matching
# `review` against a failure that merely discusses reviewing is how a
# plausible-looking pointer gets built out of nothing.
_SYMBOLISH = re.compile(r"^(?=.*[_A-Z])[A-Za-z_][A-Za-z0-9_]*$")

# How many files may mention a symbol before it stops being evidence. A name
# this common in the tree is vocabulary, not a fingerprint: seeing `read_text`
# in a traceback says nothing about which item removed one call to it.
_MAX_FILES = 8

# How many candidates to spend a `git grep` on. Reached only by a pass that
# deleted a great many distinct symbols *and* whose failure text names them
# all, which in the measured corpus never happened — the cap is here so a
# pathological diff cannot turn one red run into a hundred subprocesses.
_MAX_CANDIDATES = 12


@dataclass(frozen=True)
class Pointer:
    """One item the failure text appears to be talking about."""

    item_id: str
    file: str
    # What the file lost that the failure names, in the order found.
    symbols: tuple[str, ...]

    def describe(self) -> str:
        names = ", ".join(f"`{s}`" for s in self.symbols)
        return f"[{self.item_id}] {self.file} \u2014 lost {names}"


def lost_symbols(diff: str, post_image: str) -> set[str]:
    """Symbol-shaped identifiers this diff removed and the file no longer has.

    Three subtractions, each closing a way the naive reading invents a loss:
    what the same change added back (a reflow, or a rename's new spelling), what
    survives elsewhere in the file (removing one of five references is not a
    deletion), and anything not symbol-shaped (prose).
    """
    # Content lines are recognised by position, not by counting dashes. A
    # removed `-- note` (SQL, Lua) arrives as `--- note` and a removed `--- x`
    # (a markdown rule, a YAML separator) as `---- x`, both of which a
    # prefix test reads as the `--- a/path` header and silently drops. The
    # header only ever appears before the first `@@`, so tracking that is
    # both cheaper and exact.
    removed: list[str] = []
    added: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            in_hunk = True
        elif line.startswith("diff --git "):
            in_hunk = False
        elif not in_hunk:
            continue
        elif line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])

    gone = {t for line in removed for t in _TOKEN.findall(line)}
    gone -= {t for line in added for t in _TOKEN.findall(line)}
    gone -= set(_TOKEN.findall(post_image))
    return {t for t in gone if _SYMBOLISH.match(t)}


def _mentions(workdir: Path, symbol: str) -> int:
    """How many tracked files mention `symbol`, as a whole word.

    `git grep -w` rather than a substring search: `positional_index` is a
    substring of `_positional_index`, and counting the one against the other
    would make a renamed symbol look common enough to discard.
    """
    result = git_client.run(
        "grep", "--files-with-matches", "--fixed-strings", "--word-regexp",
        symbol, cwd=workdir,
    )
    # Exit 1 is "no match", which git reports rather than raises, so an empty
    # stdout is the honest zero and not an error to recover from.
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _diff_of(workdir: Path, path: str) -> str:
    """The pass's own change to `path`, as it stands uncommitted in the tree.

    Against HEAD rather than the index, so an agent that staged part of its
    work is read the same as one that staged none. Zero context lines: the
    surrounding unchanged code is exactly what must not be read as removed.
    """
    return git_client.run(
        "diff", "HEAD", "--unified=0", "--", path, cwd=workdir,
    ).stdout


def pointers(
    workdir: Path,
    anchors: Mapping[str, str],
    failure_text: str,
) -> tuple[Pointer, ...]:
    """Items whose anchor file lost a distinctive symbol the failure names.

    `anchors` maps item id to the file the item points at. An item with no
    anchor, or one whose file the pass never touched, cannot be pointed at and
    is simply absent from the result — an empty tuple is the ordinary answer
    and means "nothing to add", never "nothing is wrong".

    Ordered by item id so two runs over the same tree read the same.
    """
    if not failure_text.strip():
        return ()

    found: list[Pointer] = []
    rarity: dict[str, bool] = {}
    for item_id, anchor in sorted(anchors.items()):
        if not anchor:
            continue
        diff = _diff_of(Path(workdir), anchor)
        if not diff:
            continue
        # A fix can delete the file outright rather than edit it. The diff
        # still carries what was removed; there is just no post-image left to
        # subtract survivors from, so an absent file reads as empty rather
        # than being skipped. (Not what the motivating regression did — that
        # one removed an import line — but the same evidence is available and
        # skipping would throw it away.)
        anchor_path = Path(workdir) / anchor
        post = anchor_path.read_text(errors="ignore") if anchor_path.exists() else ""

        # Intersect before asking the repo anything. The failure text filters
        # a loss set of dozens down to nought or one in the measured corpus,
        # so the rarity check below runs a couple of greps rather than a scan
        # proportional to the size of the diff.
        # ceiling: truncated alphabetically before the rarity check runs, so
        # a loss past `_MAX_CANDIDATES` drops whichever symbols sort last
        # rather than whichever would have failed `_distinctive` — the cap
        # could discard the one rare symbol in favor of twelve common ones.
        # Unreached in the measured corpus (losing more than a dozen symbols
        # the failure text also names), so left ordered for determinism
        # rather than ranked by a rarity check this loop exists to avoid
        # running until the cheap intersection has already narrowed things.
        named = sorted(t for t in lost_symbols(diff, post) if t in failure_text)
        hits = _distinctive(Path(workdir), named[:_MAX_CANDIDATES], rarity)
        if hits:
            found.append(Pointer(item_id, anchor, tuple(hits)))
    return tuple(found)


def _distinctive(
    workdir: Path, symbols: list[str], rarity: dict[str, bool],
) -> list[str]:
    """Those of `symbols` few enough files mention to be a fingerprint.

    `rarity` is the caller's memo across items, so two items losing the same
    name cost one `git grep` rather than two.
    """
    keep = []
    for symbol in symbols:
        if symbol not in rarity:
            rarity[symbol] = _mentions(workdir, symbol) <= _MAX_FILES
        if rarity[symbol]:
            keep.append(symbol)
    return keep


def describe(found: tuple[Pointer, ...]) -> list[str]:
    """The lines a commit body carries about where to start, or none.

    Worded as a lead and not a verdict, because that is all the evidence
    supports: the symbol is gone and the failure mentions it, which is a good
    reason to look there first and not a finding that the item is wrong.
    """
    if not found:
        return []
    lead = (
        "The failure names something this pass removed \u2014 start here:"
        if len(found) == 1 else
        "The failure names things this pass removed \u2014 start here:"
    )
    return [lead, *(f"  {p.describe()}" for p in found)]


__all__ = ["Pointer", "describe", "lost_symbols", "pointers"]
