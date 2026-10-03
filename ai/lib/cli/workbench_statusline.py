"""Claude Code's status line: the reuse level when it differs from the
default, then the PR segment. A broken `pr` package blanks the PR segment
and leaves the reuse segment. The help text is the shim's docstring, printed
before `ai/lib` is loaded.
"""

# doc-group: cli

from __future__ import annotations

import config.reuse_levels

try:
    import pr.statusline
except Exception:
    # The status line renders on every turn and has no way to report a fault.
    # A broken ai/lib blanks the PR segment rather than killing the line.
    #
    # import pr.statusline binds the package, so `pr is None` is the one test.
    pr = None


def main(argv: list[str] | None = None) -> int:
    pieces: list[str] = []
    level = config.reuse_levels.read_level()
    default = config.reuse_levels.read_default()
    if level != default:
        pieces.append(f"reuse:{level}")
    try:
        pr_piece = pr.statusline.segment() if pr is not None else ""
    except Exception:
        # Same policy as the guarded import above, applied to the whole segment:
        # the status line has nowhere to report a fault, so a broken PR segment
        # blanks itself instead of taking the line — and `reuse:` — down with it.
        pr_piece = ""
    if pr_piece:
        pieces.append(pr_piece)
    if pieces:
        print(" ".join(pieces), end="")
    return 0
