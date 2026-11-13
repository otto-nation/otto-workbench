"""Pin one-answer policies to the commands the tools actually print."""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import pr.create  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402

GIT_OPS = REPO_ROOT / "ai" / "guidelines" / "rules" / "git-operations.md"
SUBAGENTS = REPO_ROOT / "ai" / "guidelines" / "rules" / "subagents.md"
SELF_REVIEW = REPO_ROOT / "ai" / "guidelines" / "rules" / "self-review.md"
PR_REBASE_SKILL = REPO_ROOT / "ai" / "skills" / "pr-rebase" / "SKILL.md"
AI_AUTOMATION = REPO_ROOT / "docs" / "ai-automation.src.md"

_BARE_REBASE = "pr rebase --branch <branch>"


def _skip_review_form() -> str:
    src = inspect.getsource(pr.create._self_review_gate)
    match = re.search(r'pr create --draft --skip-review "[^"]+"', src)
    assert match, src
    return match.group(0)


def _section(text: str, heading: str) -> str:
    match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", text)
    assert match, heading
    start = match.end()
    nxt = re.search(r"(?m)^## ", text[start:])
    return text[start:start + nxt.start()] if nxt else text[start:]


def _exit1_push_only_finish() -> str:
    lines = cli.pr_rebase.CONTRACT.next_steps(
        int(ExitCode.FAILURE), branch="<branch>",
    )
    found = [line for line in lines if "pr rebase --push-only" in line]
    assert found, lines
    match = re.search(r"pr rebase --push-only", found[0])
    assert match, found[0]
    return match.group(0)


def test_unpushed_rebase_finish_is_push_only_from_the_contract():
    finish = _exit1_push_only_finish()
    pushing = _section(GIT_OPS.read_text(), "Pushing")
    skill = PR_REBASE_SKILL.read_text()
    step3 = skill.split("### 3. Finish the push", 1)[1].split("## Constraints", 1)[0]
    docs = AI_AUTOMATION.read_text()

    for prose in (pushing, step3, docs):
        assert finish in prose
        assert _BARE_REBASE not in prose


def test_a_rebase_request_is_pr_rebase():
    rebase = _section(GIT_OPS.read_text(), "Rebase, Cherry-Pick, Merge Conflicts")
    assert "is `pr rebase`" in rebase

    flags = [
        flag
        for action in cli.pr_rebase.build_parser()._actions
        for flag in action.option_strings
    ]
    assert "--squash" not in flags

    implementation = _section(SUBAGENTS.read_text(), "Implementation")
    assert "pr rebase --repo-dir" in implementation
    assert "git rebase" in implementation


@pytest.mark.parametrize("path", [GIT_OPS, SELF_REVIEW])
def test_skip_review_form_matches_the_create_gate(path: Path):
    form = _skip_review_form()
    assert form in path.read_text(), path
