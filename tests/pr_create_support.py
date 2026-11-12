"""Shared harness for `pr.create.run_create` suites."""

from __future__ import annotations

import os
import sys
from functools import partial
from pathlib import Path

import pytest

from conftest import git_in, git_out, init_repo, make_ctx, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import gh.client  # noqa: E402
import pr.branch_sync  # noqa: E402
import pr.gh_token  # noqa: E402
from agent.invoke import PromptResult  # noqa: E402
from core.proc import CmdResult  # noqa: E402
from pr.create import PR_URL_RE, CreateOptions, run_create as run_create_ungated  # noqa: E402
from pr.self_review_gate import SelfReviewVerdict  # noqa: E402

BRANCH = "feat/work"
URL = "https://github.com/otto-nation/otto-workbench/pull/644"
AI_TITLE = "feat: generated title"
AI_BODY = "## Summary\n\ngenerated body"


def COVERED(_head: str) -> SelfReviewVerdict:
    return SelfReviewVerdict(covered=True, reason="self-review is current")


def UNCOVERED(_head: str) -> SelfReviewVerdict:
    return SelfReviewVerdict(
        covered=False, reason="no self-review yet", review_file="/reviews/missing.md",
    )


run_create = partial(run_create_ungated, self_review=COVERED)

_FAKE_NESTING = """#!/usr/bin/env bash
echo "nesting $*" >> "$PR_CREATE_EVENTS"
echo "nesting report line"
exit "${NESTING_STATUS:-0}"
"""


class Harness:
    """The repo, the events record, and what each stub saw."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.events_file = tmp_path / "events.log"
        self.events_file.write_text("")
        self.gh_calls: list[tuple[str, ...]] = []
        self.gh_inputs: list[str | None] = []
        self.gh_result = CmdResult(0, URL + "\n", "")
        self.wt: Path | None = None

    def event(self, name: str) -> None:
        with self.events_file.open("a") as fh:
            fh.write(name + "\n")

    @property
    def events(self) -> list[str]:
        return [line.split(" ", 1)[0] for line in self.events_file.read_text().splitlines()]

    @property
    def nesting_argv(self) -> list[str]:
        return [line.split(" ", 1)[1] for line in self.events_file.read_text().splitlines()
                if line.startswith("nesting ")]

    def repo(self, remote_branch: str = "main", *, set_head: bool = False) -> Path:
        """`main` committed locally and pushed as *remote_branch*, plus BRANCH with two commits."""
        remote = self.tmp / "remote.git"
        run_checked(["git", "init", "-q", "--bare", "-b", remote_branch, str(remote)])
        wt = init_repo(self.tmp / "wt")
        git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
        git_in(wt, "remote", "add", "origin", str(remote))
        # The push updates `origin/<remote_branch>` and nothing else. No fetch:
        # a recent git's fetch writes `origin/HEAD` itself, which is the ref
        # the fallback cases need absent.
        git_in(wt, "push", "-q", "origin", f"main:{remote_branch}")
        if set_head:
            git_in(wt, "remote", "set-head", "origin", remote_branch)
        git_in(wt, "checkout", "-q", "-b", BRANCH)
        for n in (1, 2):
            (wt / f"f{n}.txt").write_text(f"{n}\n")
            git_in(wt, "add", "--", f"f{n}.txt")
            git_in(wt, "commit", "-q", "--no-verify", "-m", f"feat: change {n}")
        self.wt = wt
        return wt

    def remote_has_branch(self) -> bool:
        return bool(git_out(self.wt, "ls-remote", "origin", f"refs/heads/{BRANCH}").strip())

    def ctx(self, branch: str = BRANCH, worktree_root: Path | None | str = "wt",
            pr_number: int | None = None):
        root = self.wt if worktree_root == "wt" else worktree_root
        return make_ctx(repo="otto-nation/otto-workbench", branch=branch, pr_number=pr_number,
                        worktree_root=root, target_dir=self.tmp / "target")


@pytest.fixture
def h(tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "validate-nesting"
    fake.write_text(_FAKE_NESTING)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PR_CREATE_EVENTS", str(harness.events_file))
    monkeypatch.delenv("NESTING_STATUS", raising=False)

    def use_for_publishing(cwd):
        harness.event("token")
        return None

    def gh_run(*args, cwd=None, input_text=None, **_kw):
        harness.event("gh")
        harness.gh_calls.append(args)
        harness.gh_inputs.append(input_text)
        return harness.gh_result

    def run_prompt(phase, prompt, **kwargs):
        harness.event("ai")
        text = (f"<<<TITLE>>>\n{AI_TITLE}\n<<<END_TITLE>>>\n"
                f"<<<DESCRIPTION>>>\n{AI_BODY}\n<<<END_DESCRIPTION>>>")
        return PromptResult(text, 0, kwargs["usable"](text))

    real_sync = pr.branch_sync.sync_branch

    def sync_branch(*args, **kwargs):
        harness.event("push")
        return real_sync(*args, **kwargs)

    monkeypatch.setattr(pr.gh_token, "use_for_publishing", use_for_publishing)
    monkeypatch.setattr(gh.client, "run", gh_run)
    monkeypatch.setattr(agent.invoke, "run_prompt", run_prompt)
    monkeypatch.setattr(pr.branch_sync, "sync_branch", sync_branch)
    return harness


def _gh_argv(h: Harness) -> tuple[str, ...]:
    assert len(h.gh_calls) == 1, h.gh_calls
    return h.gh_calls[0]


def _flag(argv: tuple[str, ...], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _gh_body(h: Harness) -> str:
    """The body gh was handed: on stdin, through `--body-file -`."""
    argv = _gh_argv(h)
    assert _flag(argv, "--body-file") == "-"
    assert "--body" not in argv
    return h.gh_inputs[0]
