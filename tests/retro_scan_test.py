"""Tests for retro.scan — registry, remotes, annotate, since, dedup.

In-process cases moved from tests/retro_scan.bats. CLI behaviour stays in
that suite; this file reads the library, not the binary.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import retro.rules  # noqa: E402
import retro.scan  # noqa: E402
from retro_support import make_machine_md, make_rules_dir  # noqa: E402


def test_parse_project_registry_extracts_project_paths(tmp_path):
    make_machine_md(
        tmp_path,
        "| myapp | /Users/test/git/myapp | Go | yes |",
        "| infra | /Users/test/git/infra | bash | no |",
    )
    repos = retro.scan.parse_project_registry(str(tmp_path / ".claude/machine/machine.md"))
    by_name = {r["name"]: r["path"] for r in repos}
    assert by_name["myapp"] == "/Users/test/git/myapp"
    assert by_name["infra"] == "/Users/test/git/infra"


def test_parse_project_registry_expands_a_home_abbreviated_path(tmp_path):
    make_machine_md(tmp_path, "| myapp | ~/git/myapp | Go | yes |")
    repos = retro.scan.parse_project_registry(str(tmp_path / ".claude/machine/machine.md"))
    assert repos[0]["path"] == os.path.expanduser("~/git/myapp")


def test_parse_project_registry_leaves_an_absolute_path_alone(tmp_path):
    make_machine_md(tmp_path, "| myapp | /Users/test/git/myapp | Go | yes |")
    repos = retro.scan.parse_project_registry(str(tmp_path / ".claude/machine/machine.md"))
    assert repos[0]["path"] == "/Users/test/git/myapp"


def test_parse_project_registry_returns_empty_list_for_missing_file():
    assert retro.scan.parse_project_registry("/nonexistent/machine.md") == []


def test_parse_project_registry_skips_table_header_separator_row(tmp_path):
    make_machine_md(tmp_path, "| myapp | /Users/test/git/myapp | Go | yes |")
    repos = retro.scan.parse_project_registry(str(tmp_path / ".claude/machine/machine.md"))
    assert len(repos) == 1


def test_get_repo_remote_a_path_that_is_not_there_reports_itself(tmp_path, capsys):
    assert retro.scan.get_repo_remote(str(tmp_path / "absent")) is None
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_get_repo_remote_a_repo_with_no_origin_is_none_without_a_path_warning(tmp_path, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=bare, check=True)
    assert retro.scan.get_repo_remote(str(bare)) is None
    captured = capsys.readouterr()
    assert "does not exist" not in captured.err


def test_get_repo_remote_reads_the_origin_of_a_real_checkout(tmp_path):
    repo = tmp_path / "withremote"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:otto-nation/myapp.git"],
        cwd=repo,
        check=True,
    )
    assert retro.scan.get_repo_remote(str(repo)) == "git@github.com:otto-nation/myapp.git"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("git@github.com:otto-nation/myapp.git", "otto-nation/myapp"),
        ("https://github.com/otto-nation/myapp.git", "otto-nation/myapp"),
        ("https://github.com/otto-nation/myapp", "otto-nation/myapp"),
        ("git@gitlab.com:org/repo.git", None),
    ],
)
def test_resolve_github_remote(url, expected):
    assert retro.scan.resolve_github_remote(url) == expected


def _annotate(tmp_path, comment, pr_author, git_user):
    make_rules_dir(tmp_path / "wb")
    rules = retro.rules.load_rules(tmp_path / "wb")
    counts = {r["filename"]: {"matched": 0} for r in rules}
    weights = retro.rules.term_weights(rules)
    status = retro.scan._annotate_comment(
        comment, pr_author, git_user, rules, counts, weights,
    )
    return status, comment


def test_annotate_comment_skips_self_replies(tmp_path):
    comment = {
        "author": "isaac",
        "body": "Responding to my own review",
        "path": None,
        "line": None,
    }
    status, _ = _annotate(tmp_path, comment, "isaac", "isaac")
    assert status == "skip"


def test_annotate_comment_gave_direction_when_user_is_comment_author(tmp_path):
    comment = {
        "author": "isaac",
        "body": "This needs more test coverage for general solutions",
        "path": None,
        "line": None,
    }
    _, comment = _annotate(tmp_path, comment, "other-dev", "isaac")
    assert comment["direction"] == "gave"


def test_annotate_comment_received_direction_when_user_is_pr_author(tmp_path):
    comment = {
        "author": "reviewer1",
        "body": "This needs more test coverage for general solutions",
        "path": None,
        "line": None,
    }
    _, comment = _annotate(tmp_path, comment, "isaac", "isaac")
    assert comment["direction"] == "received"


def test_annotate_comment_observed_direction_for_third_party(tmp_path):
    comment = {
        "author": "reviewer1",
        "body": "This needs more test coverage for general solutions",
        "path": None,
        "line": None,
    }
    _, comment = _annotate(tmp_path, comment, "other-dev", "isaac")
    assert comment["direction"] == "observed"


def test_parse_since_parses_7d_into_timestamp_about_7_days_ago():
    ts = retro.scan._parse_since("7d")
    diff = int(time.time()) - ts
    assert 6 * 86400 < diff < 8 * 86400


def test_parse_since_parses_24h_into_timestamp_about_24_hours_ago():
    ts = retro.scan._parse_since("24h")
    diff = int(time.time()) - ts
    assert 23 * 3600 < diff < 25 * 3600


def test_dedup_local_against_github_removes_duplicate_comments():
    github_repos = [{
        "github": "org/repo",
        "prs": [{
            "number": 1, "title": "t", "merged_at": "",
            "comments": [
                {
                    "author": "r",
                    "body": "This secret token should not be in the code",
                    "path": "src/auth.go",
                    "line": 10,
                },
            ],
        }],
    }]
    local_repos = [{
        "github": "local/repo",
        "prs": [{
            "number": "local:repo-self-1", "title": "Local review", "merged_at": "",
            "comments": [
                {
                    "author": "self-review",
                    "body": "This secret token should not be in the code here",
                    "path": "src/auth.go",
                    "line": 10,
                },
                {
                    "author": "self-review",
                    "body": "Completely different comment about naming",
                    "path": "src/api.go",
                    "line": 5,
                },
            ],
        }],
    }]
    deduped = retro.scan._dedup_local_against_github(github_repos, local_repos)
    total = sum(len(pr["comments"]) for r in deduped for pr in r["prs"])
    assert total == 1


def test_dedup_local_against_github_keeps_all_when_no_overlap():
    github_repos = [{
        "github": "org/repo",
        "prs": [{
            "number": 1, "title": "t", "merged_at": "",
            "comments": [
                {
                    "author": "r",
                    "body": "Fix the authentication flow",
                    "path": "src/auth.go",
                    "line": 10,
                },
            ],
        }],
    }]
    local_repos = [{
        "github": "local/repo",
        "prs": [{
            "number": "local:1", "title": "t", "merged_at": "",
            "comments": [
                {
                    "author": "self-review",
                    "body": "Variable naming is inconsistent",
                    "path": "src/api.go",
                    "line": 5,
                },
            ],
        }],
    }]
    deduped = retro.scan._dedup_local_against_github(github_repos, local_repos)
    total = sum(len(pr["comments"]) for r in deduped for pr in r["prs"])
    assert total == 1


def test_a_registry_resolving_to_nothing_returns_1_rather_than_exiting(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    assert retro.scan.run_scan(str(tmp_path), str(tmp_path / "wb")) == 1
