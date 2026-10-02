"""Tests for `pr.create.run_create` — preflight, gate, push, content, `gh pr create`.

Ported from the bash `create_pr` suite (gh's exit code decides, only a real
`/pull/<n>` URL is reported) and `tests/load_pr_context.bats` (default-branch
and base refusals), plus D5, D7-order, D10 and D13 and the dry-run contract.

Real temp repo with a bare origin, so the push is a real push. `gh`, the AI
call and the publishing token are stubbed; the nesting gate is a fake
`validate-nesting` first on PATH. Every stub appends to one events file, the
fake script included, so the order across Python and the subprocess is one
record.
"""

from __future__ import annotations

import os
import sys
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
from pr.create import PR_URL_RE, CreateOptions, run_create  # noqa: E402

BRANCH = "feat/work"
URL = "https://github.com/otto-nation/otto-workbench/pull/644"
AI_TITLE = "feat: generated title"
AI_BODY = "## Summary\n\ngenerated body"

_FAKE_NESTING = """#!/usr/bin/env bash
echo "nesting $*" >> "$PR_CREATE_EVENTS"
echo "nesting report line"
exit "${NESTING_STATUS:-0}"
"""


# ── harness ─────────────────────────────────────────────────────────────────


class Harness:
    """The repo, the events record, and what each stub saw."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.events_file = tmp_path / "events.log"
        self.events_file.write_text("")
        self.gh_calls: list[tuple[str, ...]] = []
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

    def gh_run(*args, cwd=None, **_kw):
        harness.event("gh")
        harness.gh_calls.append(args)
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


# ── ported from the bash create_pr suite ───────────────────────────────────


def test_reports_the_pr_url_when_gh_succeeds(h, capsys):
    h.repo()
    assert run_create(h.ctx(), CreateOptions()) == 0
    out, err = capsys.readouterr()
    assert "✓ Pull request created" in err
    assert out.strip() == URL


def test_args_reach_gh_pr_create_including_draft_assignee_and_base(h):
    h.repo()
    assert run_create(h.ctx(), CreateOptions(draft=True, title="fix: thing")) == 0
    argv = _gh_argv(h)
    assert argv[:2] == ("pr", "create")
    assert _flag(argv, "--title") == "fix: thing"
    assert _flag(argv, "--body") == AI_BODY
    assert _flag(argv, "--assignee") == "@me"
    assert "--draft" in argv


def test_base_is_passed_to_gh_even_when_not_given(h):
    """D5: the base the gate and the content measured is the base the PR targets."""
    h.repo()
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert _flag(_gh_argv(h), "--base") == "main"
    assert "--draft" not in _gh_argv(h)


def test_fails_on_ghs_connectivity_error_instead_of_reporting_githubstatus(h, capsys):
    h.repo()
    h.gh_result = CmdResult(1, "", "error connecting to api.github.com\n"
                                   "check your internet connection or https://githubstatus.com")
    assert run_create(h.ctx(), CreateOptions()) == 1
    out, err = capsys.readouterr()
    assert "✗ PR creation failed (gh exited 1)" in err
    assert "error connecting to api.github.com" in err
    assert "Pull request created" not in err
    assert out == ""


def test_fails_when_gh_exits_nonzero_even_if_the_output_has_a_pr_url(h, capsys):
    h.repo()
    h.gh_result = CmdResult(1, URL + "\n", "GraphQL: something went wrong")
    assert run_create(h.ctx(), CreateOptions()) == 1
    out, err = capsys.readouterr()
    assert "✗ PR creation failed" in err
    assert "✓ Pull request created" not in err
    assert out == ""


def test_fails_when_gh_succeeds_but_prints_no_pull_request_url(h, capsys):
    h.repo()
    h.gh_result = CmdResult(0, "Warning: 1 uncommitted change\n"
                               "https://github.com/otto-nation/otto-workbench\n", "")
    assert run_create(h.ctx(), CreateOptions()) == 1
    err = capsys.readouterr().err
    assert "✗ PR creation failed — gh reported success but printed no pull request URL" in err
    assert "✓ Pull request created" not in err


def test_picks_the_pull_request_url_out_of_surrounding_gh_output(h, capsys):
    h.repo()
    url = "https://github.com/otto-nation/otto-workbench/pull/700"
    h.gh_result = CmdResult(0, "Warning: 1 uncommitted change\n"
                               "See https://githubstatus.com for API status\n"
                               f"{url}\n", "")
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert capsys.readouterr().out.splitlines()[-1] == url


@pytest.mark.parametrize("url", [
    "https://ghe.acme.com/acme/widget/pull/12",
    "https://git.internal.acme:8443/acme/widget/pull/7",
])
def test_reports_an_enterprise_or_self_hosted_pr_url(h, capsys, url):
    h.repo()
    h.gh_result = CmdResult(0, url + "\n", "")
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert capsys.readouterr().out.strip() == url


# passes-at-base: the old pattern rejected this too; guards the widening
def test_still_rejects_a_non_pr_enterprise_url(h, capsys):
    h.repo()
    h.gh_result = CmdResult(0, "opened an issue at https://ghe.acme.com/acme/widget/issues/12", "")
    assert run_create(h.ctx(), CreateOptions()) == 1
    assert "printed no pull request URL" in capsys.readouterr().err


def test_pr_url_re_takes_the_first_pull_url():
    text = "x https://a.example/o/r/pull/1 y https://a.example/o/r/pull/2"
    assert PR_URL_RE.search(text).group(0) == "https://a.example/o/r/pull/1"


# ── tests/load_pr_context.bats ──────────────────────────────────────────────


def test_falls_back_to_main_when_origin_head_is_missing(h):
    wt = h.repo("main")
    assert run_checked(["git", "-C", str(wt), "symbolic-ref", "refs/remotes/origin/HEAD"],
                       check=False).returncode != 0
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert _flag(_gh_argv(h), "--base") == "main"


def test_resolves_origin_head_to_a_non_main_default_branch(h):
    h.repo("trunk", set_head=True)
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert _flag(_gh_argv(h), "--base") == "trunk"
    assert h.nesting_argv == ["--diff origin/trunk --quiet"]


def test_refuses_when_the_resolved_default_branch_has_no_remote_tracking_ref(h, capsys):
    h.repo("trunk")
    assert run_create(h.ctx(), CreateOptions()) == 1
    err = capsys.readouterr().err
    assert ("✗ origin/main does not resolve — cannot open a PR against a base "
            "that doesn't exist") in err
    assert "→ Fix with: git fetch origin" in err
    assert ("→ If main is a guess and the real default branch differs, also run: "
            "git remote set-head origin -a") in err
    assert h.events == []


def test_a_resolving_base_succeeds_even_when_the_guessed_default_does_not(h):
    h.repo("trunk")
    assert run_create(h.ctx(), CreateOptions(base="trunk")) == 0
    assert _flag(_gh_argv(h), "--base") == "trunk"


def test_a_non_resolving_base_refuses_naming_the_branch_passed(h, capsys):
    h.repo("main")
    assert run_create(h.ctx(), CreateOptions(base="does-not-exist")) == 1
    err = capsys.readouterr().err
    assert "origin/does-not-exist does not resolve" in err
    assert "origin/main does not resolve" not in err
    assert "set-head" not in err


# ── preflight ───────────────────────────────────────────────────────────────


def test_refuses_to_run_from_the_default_branch(h, capsys):
    h.repo()
    assert run_create(h.ctx(branch="main"), CreateOptions()) == 1
    assert "✗ PR operations cannot be run from the main branch" in capsys.readouterr().err
    assert h.events == []


def test_a_branch_with_no_checkout_is_refused(h, capsys):
    """D13: content and push need a worktree."""
    assert run_create(h.ctx(worktree_root=None), CreateOptions()) == 1
    assert ("✗ feat/work has no checkout — run from its worktree or pass --repo-dir"
            in capsys.readouterr().err)
    assert h.events == []


def test_a_rejected_closes_fails_before_anything_runs(h, capsys):
    h.repo()
    assert run_create(h.ctx(), CreateOptions(closes=("abc",))) == 1
    assert "✗ --closes abc: expected a GitHub issue number" in capsys.readouterr().err
    assert h.events == []
    assert not h.remote_has_branch()


def test_closes_refs_are_normalised_and_appended_to_the_body(h, capsys):
    h.repo()
    assert run_create(h.ctx(), CreateOptions(closes=("941", "#941", "942"))) == 0
    assert _flag(_gh_argv(h), "--body").endswith("Closes #941\n\nCloses #942")
    assert "✓ Linked for auto-close on merge: Closes #941 Closes #942" in capsys.readouterr().err


def test_no_publishing_token_prints_the_guidance_and_stops(h, monkeypatch, capsys):
    """D10: `use_for_publishing` decides; its refusal is the guidance, on stderr."""
    h.repo()

    def refuse(cwd):
        raise pr.gh_token.TokenNotConfigured("set GH_TOKEN in taskfile.env")

    monkeypatch.setattr(pr.gh_token, "use_for_publishing", refuse)
    assert run_create(h.ctx(), CreateOptions()) == 1
    assert "set GH_TOKEN in taskfile.env" in capsys.readouterr().err
    assert h.events == []
    assert not h.remote_has_branch()


# ── nesting gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,message", [
    ("2", "✗ Nesting check could not run — fix before creating PR"),
    ("1", "✗ New code introduces nesting violations — fix before creating PR"),
])
def test_a_failing_nesting_gate_stops_before_the_push(h, monkeypatch, capsys, status, message):
    h.repo()
    monkeypatch.setenv("NESTING_STATUS", status)
    assert run_create(h.ctx(), CreateOptions()) == 1
    err = capsys.readouterr().err
    assert message in err
    assert "nesting report line" in err
    assert h.events == ["token", "nesting"]
    assert not h.remote_has_branch()


def test_nesting_runs_before_push_before_ai_before_gh(h):
    h.repo()
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert h.events == ["token", "nesting", "push", "ai", "gh"]
    assert h.remote_has_branch()


def test_a_refused_push_stops_before_ai_and_gh(h, capsys):
    """BEHIND is a message sync_branch returns rather than logs; it is printed once."""
    wt = h.repo()
    git_in(wt, "push", "-q", "-u", "origin", BRANCH)
    git_in(wt, "reset", "-q", "--hard", "HEAD~1")
    assert run_create(h.ctx(), CreateOptions()) == 1
    err = capsys.readouterr().err
    assert err.count("✗ Remote has commits not in local branch") == 1
    assert h.events == ["token", "nesting", "push"]


def test_a_new_branch_push_line_is_printed_once(h, capsys):
    h.repo()
    assert run_create(h.ctx(), CreateOptions()) == 0
    assert capsys.readouterr().err.count("→ Pushing new branch to remote...") == 1


# ── --dry-run ───────────────────────────────────────────────────────────────


def test_dry_run_runs_no_gate_no_push_no_gh_and_prints_the_content(h, capsys):
    h.repo()
    assert run_create(h.ctx(), CreateOptions(dry_run=True)) == 0
    out, err = capsys.readouterr()
    assert h.events == ["ai"]
    assert not h.remote_has_branch()
    assert out == f"→ PR Title:\n   {AI_TITLE}\n\n→ PR Description:\n{AI_BODY}\n"
    assert f"→ Analyzing changes: {BRANCH}" in err
    assert "✓ Content ready" in err


# ── refusals before anything leaves the machine ─────────────────────────────


@pytest.mark.parametrize("opts", [
    CreateOptions(),
    CreateOptions(title="fix: given", body="given body"),
], ids=["generated", "both-overrides"])
def test_no_commits_ahead_refuses_before_token_gate_push_or_ai(h, capsys, opts):
    """D7 refuses in preflight, so even a fully overridden PR pays for nothing."""
    wt = h.repo()
    git_in(wt, "reset", "-q", "--hard", "main")
    assert run_create(h.ctx(), opts) == 1
    assert (f"✗ No commits on {BRANCH} ahead of origin/main — nothing to open a PR for"
            in capsys.readouterr().err)
    assert h.events == []
    assert not h.remote_has_branch()


def test_a_failed_ahead_count_is_refused_naming_the_command(h, monkeypatch, capsys):
    h.repo()
    import git.client
    real = git.client.run

    def run(*args, **kwargs):
        if args[:2] == ("rev-list", "--count"):
            return CmdResult(128, "", "fatal: bad revision")
        return real(*args, **kwargs)

    monkeypatch.setattr(git.client, "run", run)
    assert run_create(h.ctx(), CreateOptions()) == 1
    err = capsys.readouterr().err
    assert "✗ git rev-list --count origin/main..HEAD failed: fatal: bad revision" in err
    assert h.events == []


@pytest.mark.parametrize("dry_run", [False, True], ids=["create", "dry-run"])
def test_an_existing_pr_refuses_before_anything_runs(h, capsys, dry_run):
    h.repo()
    assert run_create(h.ctx(pr_number=812), CreateOptions(dry_run=dry_run)) == 1
    assert (f"✗ PR #812 already exists for {BRANCH} — use pr describe to revise it"
            in capsys.readouterr().err)
    assert h.events == []
    assert not h.remote_has_branch()

