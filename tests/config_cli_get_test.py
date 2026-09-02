"""Tests for `otto-workbench config get` — the read side bash goes through.

Every scope the typed loader merges has to be reachable from here, because
this command is now the only reader the non-Python half of the workbench has.
What it replaced resolved the project file alone, so a repo whose answer lived
above its worktrees read as though it had never given one — in a table
rendered from the same machine whose SessionStart line named it correctly.

The batch cases matter as much as the scope cases. A report over other
people's repos visits directories it does not control: one of them is deleted,
one has a config nothing can parse, and neither is a reason for the other rows
to be missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import _load_lib, seed_repo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import workbench_config as wc
import workbench_config_write as wcw

config_cli = _load_lib("config_cli")

KEY = wc.ISSUE_PROVIDER_KEY


def _records(capsys) -> list[tuple[str, str, str]]:
    """Every record printed so far, split the way the format documents."""
    out = capsys.readouterr().out
    return [tuple(line.split("\t")) for line in out.splitlines()]


def _run(capsys, *argv) -> tuple[int, list[tuple[str, str, str]]]:
    code = config_cli.main(["get", *argv])
    return code, _records(capsys)


def _declare(path: Path, provider: str) -> None:
    (path / wc.PROJECT_CONFIG_NAME).write_text(
        f"issue_tracker:\n  provider: {provider}\n")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A plain checkout, with no config of its own yet."""
    return seed_repo(tmp_path / "repo")


# ─── The scopes ──────────────────────────────────────────────────────────────

def test_reads_a_named_repos_own_config(capsys, repo):
    _declare(repo, "github")
    code, records = _run(capsys, KEY, str(repo))
    assert code == 0
    assert records == [(wc.PROJECT_SCOPE, "github", str(repo))]


def test_names_the_global_scope_when_the_repo_inherits(capsys, repo):
    wcw.set_value(KEY, "linear")
    code, records = _run(capsys, KEY, str(repo))
    assert code == 0
    assert records == [(wc.GLOBAL_SCOPE, "linear", str(repo))]


def test_the_repos_own_answer_outranks_the_machines(capsys, repo):
    wcw.set_value(KEY, "linear")
    _declare(repo, "github")
    code, records = _run(capsys, KEY, str(repo))
    assert code == 0
    assert records == [(wc.PROJECT_SCOPE, "github", str(repo))]


def test_reads_the_scope_above_a_bare_repos_worktrees(capsys, container):
    """The scope the reader this replaced could not see at all.

    A container file is the answer for every worktree of the repo and lives in
    none of them, which is exactly the shape a project-file reader reports as
    "never declared".
    """
    _declare(container, "linear")
    worktree = container / "main"
    code, records = _run(capsys, KEY, str(worktree))
    assert code == 0
    assert records == [(wc.CONTAINER_SCOPE, "linear", str(worktree))]


def test_the_worktrees_own_answer_outranks_the_containers(capsys, container):
    _declare(container, "linear")
    worktree = container / "main"
    _declare(worktree, "github")
    code, records = _run(capsys, KEY, str(worktree))
    assert code == 0
    assert records == [(wc.PROJECT_SCOPE, "github", str(worktree))]


def test_with_no_dir_it_answers_for_the_callers_own_repo(capsys, repo, monkeypatch):
    _declare(repo, "github")
    monkeypatch.chdir(repo)
    code, records = _run(capsys, KEY)
    assert code == 0
    assert records == [(wc.PROJECT_SCOPE, "github", str(repo))]


# ─── Nothing to report ───────────────────────────────────────────────────────
#
# Each of these is the built-in default standing, which is what every other
# reader on the machine gets from `load_config_or_default`. The caller's own
# marker — the machine profile's "unset", a bash fallback — is the only thing
# that says so, so the record has to be reporting the default rather than an
# error the batch would have to carry.

def test_a_repo_with_no_config_resolves_to_the_default(capsys, repo):
    code, records = _run(capsys, KEY, str(repo))
    assert code == 0
    assert records == [(wc.DEFAULT_SCOPE, "", str(repo))]


def test_a_directory_that_is_gone_resolves_to_the_default(capsys, tmp_path):
    absent = tmp_path / "never-existed"
    code, records = _run(capsys, KEY, str(absent))
    assert code == 0
    assert records == [(wc.DEFAULT_SCOPE, "", str(absent))]


def test_an_unparseable_config_resolves_to_the_default(capsys, repo):
    (repo / wc.PROJECT_CONFIG_NAME).write_text("issue_tracker:\n  provider: [unclosed\n")
    code, records = _run(capsys, KEY, str(repo))
    assert code == 0
    assert records == [(wc.DEFAULT_SCOPE, "", str(repo))]


# ─── The batch ───────────────────────────────────────────────────────────────

def test_a_bad_repo_costs_only_its_own_row(capsys, tmp_path):
    bad = seed_repo(tmp_path / "bad")
    (bad / wc.PROJECT_CONFIG_NAME).write_text("issue_tracker:\n  provider: [unclosed\n")
    good = seed_repo(tmp_path / "good")
    _declare(good, "github")
    gone = tmp_path / "gone"

    code, records = _run(capsys, KEY, str(bad), str(gone), str(good))
    assert code == 0
    assert records == [
        (wc.DEFAULT_SCOPE, "", str(bad)),
        (wc.DEFAULT_SCOPE, "", str(gone)),
        (wc.PROJECT_SCOPE, "github", str(good)),
    ]


def test_every_dir_is_echoed_back_in_the_order_given(capsys, tmp_path):
    """What lets a caller key its answers by name rather than by position.

    A row mislabelled by an off-by-one is the same class of bug as the one this
    command exists to fix: a value reported against a repo that never held it.
    """
    repos = [seed_repo(tmp_path / name) for name in ("c", "a", "b")]
    for repo, provider in zip(repos, ("github", "linear", "jira")):
        _declare(repo, provider)

    code, records = _run(capsys, KEY, *(str(repo) for repo in repos))
    assert code == 0
    assert [record[2] for record in records] == [str(repo) for repo in repos]
    assert [record[1] for record in records] == ["github", "linear", "jira"]


# ─── The record format ───────────────────────────────────────────────────────

def test_an_empty_value_still_leaves_three_fields(capsys, repo):
    """The shape a caller splitting on tabs depends on.

    Bash folds a run of tabs into one delimiter, so a record that dropped the
    empty field would hand `IFS=$'\\t' read -r scope value dir` the directory as
    the value and nothing as the directory — silently, on the commonest record
    there is. `lib/config.sh`'s `wb_config_split_record` is the other half.
    """
    code = config_cli.main(["get", KEY, str(repo)])
    assert code == 0
    assert capsys.readouterr().out == f"{wc.DEFAULT_SCOPE}\t\t{repo}\n"


def test_a_value_spanning_lines_is_collapsed_onto_one(capsys, repo):
    (repo / wc.PROJECT_CONFIG_NAME).write_text('agent:\n  model: "a\\nb"\n')
    code, records = _run(capsys, "agent.model", str(repo))
    assert code == 0
    assert records == [(wc.PROJECT_SCOPE, "a b", str(repo))]


# ─── The guard ───────────────────────────────────────────────────────────────

def test_a_key_the_config_surface_does_not_define_is_refused(capsys, repo):
    """A read has no fallback here, and deliberately so.

    A key nothing reads is a caller asking a question with no answer. Reporting
    it as unset would hide the typo for as long as it takes somebody to notice
    the rule it was meant to turn on is not applying — which is the failure the
    whole config surface check exists to prevent.
    """
    code = config_cli.main(["get", "issue_tracker.provdier", str(repo)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "is not a key WorkbenchConfig defines" in captured.err


def test_a_refused_key_answers_for_no_dir_at_all(capsys, tmp_path):
    """The refusal comes before the batch rather than per row.

    Half a report is worse than none: a caller reading records back would take
    the rows that did print as the whole answer.
    """
    good = seed_repo(tmp_path / "good")
    _declare(good, "github")
    code = config_cli.main(["get", "nope.nothing", str(good)])
    assert code == 1
    assert capsys.readouterr().out == ""


def test_a_key_only_this_checkout_defines_is_readable(capsys, repo, monkeypatch):
    """Unlike a write, a read is not judged by the installed workbench.

    A write outlives the checkout that made it and is read by whatever is on
    PATH afterwards, which is why `check_key` asks both. A read resolves here
    and now, so a key this branch adds is one this branch may ask for — and a
    branch whose own tests could not read its own new key would be unable to
    test it at all.
    """
    monkeypatch.setattr(
        config_cli.workbench_config_write, "check_key", _refuse_everything,
    )
    _declare(repo, "github")
    code, records = _run(capsys, KEY, str(repo))
    assert code == 0
    assert records == [(wc.PROJECT_SCOPE, "github", str(repo))]


def _refuse_everything(key: str):
    raise AssertionError(f"a read must not consult the installed schema for {key}")
