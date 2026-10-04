"""The status line's PR segment, read from the per-target state file and never
from ``gh`` or the network.

Not: the reuse segment or the degradation when ``pr`` will not import
(``cli.workbench_statusline``), the state schema (``pr.state``), target
resolution (``pr.target``).
"""

# doc-group: pr-state

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pr.state
import pr.target


def segment() -> str:
    # No `gh` and no network: this runs on every prompt render.
    try:
        target = pr.target.target_dir_for_checkout(Path.cwd())
    except Exception:
        return ""
    if target is None:
        return ""

    # load_state warns on stderr when the file will not parse. The status line
    # has nowhere to put that, so it is swallowed here; `pr status` and every
    # write path still surface it.
    with contextlib.redirect_stderr(io.StringIO()):
        state = pr.state.load_state(target)

    if state is None or not state.identity.pr_number:
        return ""

    label = f"PR#{state.identity.pr_number}"
    details = _pr_details(state)
    if details:
        label = f"{label} {details}"
    return label

def _pr_details(state: pr.state.PRState) -> str:
    parts: list[str] = []

    if state.ci.conclusion == "failure" and state.ci.failure_count > 0:
        parts.append(f"CI:{state.ci.failure_count}F")
    elif state.ci.conclusion == "success":
        parts.append("CI:ok")
    elif state.ci.conclusion:
        parts.append(f"CI:{state.ci.conclusion}")

    if state.review.verdict:
        parts.append(f"review:{state.review.verdict}")

    open_threads = state.comments.by_state.get("open") or 0
    if open_threads > 0:
        parts.append(f"{open_threads}open")

    return " ".join(parts)

