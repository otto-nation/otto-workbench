"""cli.owners_check: the guards' entry point to the owner boundary."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import cli.owners_check
import gh.owners

from conftest import REPO_ROOT, remote_repo, run_checked


def test_a_read_exits_zero_unconfigured(tmp_path, capsys):
    assert cli.owners_check.main(["--cwd", str(tmp_path), "--", "gh pr view 1"]) == 0


def test_an_upstream_write_exits_two_with_the_reason(github_owners, tmp_path, capsys):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:stranger/lib.git")
    code = cli.owners_check.main(["--cwd", str(repo), "--", "gh issue create --title x"])
    assert code == cli.owners_check.REFUSED
    assert "stranger" in capsys.readouterr().err


def test_an_own_write_exits_zero(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:otto-nation/svc.git")
    assert cli.owners_check.main(["--cwd", str(repo), "--", "gh issue create --title x"]) == 0


def test_split_words_are_rejoined_with_their_quoting(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    repo = remote_repo(tmp_path / "r", "git@github.com:otto-nation/svc.git")
    assert cli.owners_check.main(
        ["--cwd", str(repo), "--", "gh", "issue", "create", "--title", "a; cd /"]) == 0
    assert cli.owners_check.main(
        ["--cwd", str(repo), "--", "gh", "issue", "comment", "1", "--repo", "stranger/lib"]
    ) == cli.owners_check.REFUSED


def test_the_override_does_not_reach_the_guards_check(github_owners, tmp_path, monkeypatch):
    github_owners({"work": ["otto-nation"]})
    monkeypatch.setenv(gh.owners.ALLOW_UPSTREAM_ENV, "1")
    code = cli.owners_check.main(["--cwd", str(tmp_path), "--", "gh issue create --repo stranger/lib"])
    assert code == cli.owners_check.REFUSED


def test_configured_reports_unset_with_the_fix(capsys):
    assert cli.owners_check.main(["--configured"]) == cli.owners_check.NOT_CONFIGURED
    assert gh.owners.FIX_COMMAND in capsys.readouterr().err


def test_configured_reports_a_broken_map(github_owners, capsys):
    github_owners({"work": ["acme"], "personal": ["acme"]})
    assert cli.owners_check.main(["--configured"]) == cli.owners_check.NOT_CONFIGURED
    assert "acme" in capsys.readouterr().err


def test_configured_is_quiet_when_set(github_owners, capsys):
    github_owners({"work": ["acme"]})
    assert cli.owners_check.main(["--configured"]) == 0
    assert capsys.readouterr().err == ""


def test_the_shim_runs_the_same_check(github_owners, tmp_path):
    github_owners({"work": ["otto-nation"]})
    result = run_checked(
        [str(REPO_ROOT / "ai" / "bin" / "owners-check"), "--cwd", str(tmp_path), "--",
         "gh issue create --repo stranger/lib"],
        env=dict(os.environ), check=False)
    assert result.returncode == 2
    assert "stranger" in result.stderr


def test_classify_answers_write_on_a_wrapped_write(capsys):
    assert cli.owners_check.main(
        ["--classify", "--", "sudo gh issue comment 1 -b x"]) == 0
    assert capsys.readouterr().out == "write\n"


def test_classify_answers_read_and_exits_zero_on_a_read(capsys):
    assert cli.owners_check.main(["--classify", "--", "gh pr list"]) == 0
    assert capsys.readouterr().out == "read\n"


def test_classify_answers_write_on_an_unparseable_command(capsys):
    assert cli.owners_check.main(["--classify", "--", "gh pr comment 'unterminated"]) == 0
    assert capsys.readouterr().out == "write\n"


def test_classify_does_not_load_owners(monkeypatch, capsys):
    monkeypatch.setattr(
        gh.owners.OwnerMap, "load",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("classify must not load owners")))
    assert cli.owners_check.main(["--classify", "--", "gh pr list"]) == 0
    assert capsys.readouterr().out == "read\n"


def test_the_shim_classifies_with_one_line_on_stdout():
    shim = str(REPO_ROOT / "ai" / "bin" / "owners-check")
    write = run_checked([shim, "--classify", "--", "gh issue close 3"],
                        env=dict(os.environ), check=False)
    read = run_checked([shim, "--classify", "--", "gh issue view 3"],
                       env=dict(os.environ), check=False)
    assert (write.returncode, write.stdout) == (0, "write\n")
    assert (read.returncode, read.stdout) == (0, "read\n")
