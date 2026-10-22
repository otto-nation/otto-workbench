"""gh.write_shapes: which statements and gh argvs write to GitHub."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

import gh.client
import gh.write_shapes

CORPUS = REPO_ROOT / "tests" / "fixtures" / "github-write-shapes.tsv"


def _rows():
    rows = []
    for line in CORPUS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        expected, command = line.split("\t", 1)
        rows.append(pytest.param(expected, command, id=command))
    return rows


def test_every_corpus_row_is_well_formed():
    for line in CORPUS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        expected, sep, command = line.partition("\t")
        assert sep and expected in ("write", "read") and command.strip(), line


@pytest.mark.parametrize("expected,command", _rows())
def test_the_python_classifier_agrees_with_the_shared_corpus(expected, command):
    got = "write" if gh.write_shapes.command_is_write(command) else "read"
    assert got == expected


def test_statements_split_on_operators_and_newlines_but_not_inside_quotes():
    assert gh.write_shapes.split_statements("cd x && gh pr comment 1 --body 'a; b'\nls") == [
        ["cd", "x"], ["gh", "pr", "comment", "1", "--body", "a; b"], ["ls"],
    ]


def test_an_unbalanced_quote_is_a_value_error():
    with pytest.raises(ValueError):
        gh.write_shapes.split_statements("gh issue create --title 'x")


def test_a_heredoc_body_with_an_unbalanced_quote_is_dropped():
    command = "gh pr comment 1 --body-file - <<'EOF'\nIt's done\nEOF"
    assert gh.write_shapes.split_statements(command) == [
        ["gh", "pr", "comment", "1", "--body-file", "-", "<<EOF"],
    ]
    assert gh.write_shapes.command_is_write(command)


def test_an_env_prefix_is_carried_apart_from_the_words():
    inv = gh.write_shapes.invocation(["GH_REPO=o/r", "GH_HOST=ghe.corp", "/opt/bin/gh", "issue", "create"])
    assert dict(inv.env) == {"GH_REPO": "o/r", "GH_HOST": "ghe.corp"}
    assert inv.program == "gh"
    assert inv.args == ("issue", "create")


def test_a_graphql_body_on_stdin_is_judged_by_its_text():
    args = ("api", "graphql", "--input", "-")
    assert gh.write_shapes.gh_is_write(args, input_text='{"query": "mutation { x }"}')
    assert not gh.write_shapes.gh_is_write(args, input_text='{"query": "query { x }"}')


def test_the_clients_own_argv_for_a_post_is_a_write():
    argv = gh.client._api_argv("repos/o/r/issues", "POST", "", False, False, None, None, None,
                               body_on_stdin=True)
    assert gh.write_shapes.gh_is_write(argv)


def test_the_clients_own_argv_for_a_get_is_not():
    argv = gh.client._api_argv("repos/o/r/issues", "GET", "", True, False, None, None, None)
    assert not gh.write_shapes.gh_is_write(argv)


@pytest.mark.parametrize("args,index", [
    (("issue", "comment", "1"), 0),
    (("-R", "o/r", "issue", "comment", "1"), 2),
    (("-Ro/r", "issue", "comment", "1"), 1),
    (("-R=o/r", "issue", "comment", "1"), 1),
    (("--repo", "o/r", "issue", "comment", "1"), 2),
    (("--repo=o/r", "issue", "comment", "1"), 1),
])
def test_skip_repo_flags_covers_every_pflag_form(args, index):
    assert gh.write_shapes.skip_repo_flags(args) == index


@pytest.mark.parametrize("command", [
    "gh -Rstranger/lib issue comment 1",
    "gh -R=stranger/lib issue comment 1",
])
def test_an_attached_short_repo_flag_is_a_write(command):
    assert gh.write_shapes.command_is_write(command)


@pytest.mark.parametrize("args,path", [
    (("pr", "comment", "1"), ("pr", "comment")),
    (("pr", "-R", "o/r", "comment", "1"), ("pr", "comment")),
    (("-R", "o/r", "pr", "--repo=o/r", "comment"), ("pr", "comment")),
    (("issue", "-Ro/r", "create"), ("issue", "create")),
    (("pr",), ("pr",)),
])
def test_gh_subcommands_skips_repo_flags_before_the_verb(args, path):
    assert gh.write_shapes.gh_subcommands(args) == path


@pytest.mark.parametrize("query,write", [
    ("\n  query { viewer { login } }", False),
    ("query($owner: String!) { repositoryOwner(login: $owner) { id } }", False),
    ("mutation { x }", True),
    ("$Q", True),
    ("@m.graphql", True),
    ("query { x } $(cat extra)", True),
    ("", True),
])
def test_a_graphql_query_counts_as_a_read_only_when_it_is_literal(query, write):
    assert gh.write_shapes.gh_is_write(("api", "graphql", "-f", f"query={query}")) is write


def test_a_graphql_query_read_from_a_file_is_a_write():
    assert gh.write_shapes.gh_is_write(("api", "graphql", "-F", "query=@m.graphql"))
    assert not gh.write_shapes.gh_is_write(("api", "graphql", "-F", "query=query { x }"))


@pytest.mark.parametrize("command,expanded", [
    ("bash -lc 'ls ai/lib/review'", [["ls", "ai/lib/review"]]),
    ("GH_REPO=o/r sudo bash -c 'a; gh pr view'", [["GH_REPO=o/r", "sudo", "a"], ["GH_REPO=o/r", "sudo", "gh", "pr", "view"]]),
    ("eval eval 'gh pr view'", [["gh", "pr", "view"]]),
    ("eval eval eval eval 'gh pr view'", [["eval", "gh", "pr", "view"]]),
    ("env -i -S 'GH_REPO=o/r gh pr view' 1", [["env", "-i", "GH_REPO=o/r", "gh", "pr", "view", "1"]]),
    ("env -S '${X} gh pr view'", [["env", "-S", "${X} gh pr view"]]),
    ("bash -o pipefail -c 'gh pr view'", [["gh", "pr", "view"]]),
    ("bash run.sh -c 'gh pr view'", [["bash", "run.sh", "-c", "gh pr view"]]),
    ("bash -c \"echo 'unbalanced\"", [["bash", "-c", "echo 'unbalanced"]]),
])
def test_a_reparsed_string_is_expanded_into_its_statements(command, expanded):
    assert gh.write_shapes.statements(command) == expanded
