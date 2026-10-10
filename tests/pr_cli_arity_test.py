"""pr CLI: telling a positional target from a flag's value by reading each delegate's arity."""

import contextlib
import importlib
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402

import cli.dispatch  # noqa: E402
import cli.registry  # noqa: E402
import core.tool_parser  # noqa: E402

from pr_cli_support import _TEST_PR, _run_main, _delegate_cmd


_TEST_REPLY_ID = "3777767789"
_TEST_REPLY_BODY_FILE = "/tmp/reply.md"


# ── positional target vs. flag arity ───────────────────────────────────────


@contextlib.contextmanager
def _record_arity_reads():
    """Record which commands `pr` read flag arity for, letting the read happen.

    The point of these tests is that the wrapper reads arity off the delegate's
    own parser, so the real parser has to be the one answering — this spies on
    the read rather than replacing it.

    Not nestable: the inner block would capture the outer spy as `real` and
    double-count every read. Nothing nests it, and a second recorder in one
    test would be asking two questions of one run anyway.
    """
    read: list[str] = []
    real = cli.dispatch.delegate_value_flags

    def spy(spec):
        read.append(spec.name)
        return real(spec)

    with patch("cli.dispatch.delegate_value_flags", side_effect=spy):
        yield read


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_reply_id_is_not_eaten_as_the_positional_target(mock_resolve, mock_call):
    """--reply's value is its argument, not the PR number."""
    mock_resolve.return_value = make_ctx(pr_number=None, branch=None)
    mock_call.return_value = 0
    _run_main("comments", "--reply", _TEST_REPLY_ID,
              "--body-file", _TEST_REPLY_BODY_FILE, "--repo-dir", "/path")
    cmd = _delegate_cmd(mock_call)
    assert cmd[cmd.index("--reply") + 1] == _TEST_REPLY_ID
    assert cmd[cmd.index("--body-file") + 1] == _TEST_REPLY_BODY_FILE
    assert "--pr" not in cmd
    assert mock_resolve.call_args[1]["pr_ref"] is None
    assert mock_resolve.call_args[1]["branch"] is None


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_body_file_path_is_not_eaten_after_an_inline_reply(mock_resolve, mock_call):
    """--reply=ID is self-contained, so --body-file's path survives too."""
    mock_resolve.return_value = make_ctx(pr_number=None, branch=None)
    mock_call.return_value = 0
    _run_main("comments", f"--reply={_TEST_REPLY_ID}", f"--body-file={_TEST_REPLY_BODY_FILE}")
    cmd = _delegate_cmd(mock_call)
    assert f"--reply={_TEST_REPLY_ID}" in cmd
    assert f"--body-file={_TEST_REPLY_BODY_FILE}" in cmd
    assert mock_resolve.call_args[1]["branch"] is None


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_reply_value_survives_an_explicit_branch(mock_resolve, mock_call):
    """An explicit --branch skips classification entirely; extra stays intact."""
    mock_resolve.return_value = make_ctx(branch="some/branch", pr_number=None)
    mock_call.return_value = 0
    with _record_arity_reads() as read:
        _run_main("comments", "--branch", "some/branch",
                  "--reply", "123", "--body-file", "/tmp/x.md")
    cmd = _delegate_cmd(mock_call)
    assert cmd[cmd.index("--reply") + 1] == "123"
    assert cmd[cmd.index("--body-file") + 1] == "/tmp/x.md"
    assert read == [], "no ambiguity, so no arity read"


@pytest.mark.parametrize("flag", ["--fix", "--triage"])
@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_target_after_a_boolean_flag_is_still_the_target(mock_resolve, mock_call, flag):
    """A boolean flag consumes nothing, so the token after it is the PR number."""
    mock_resolve.return_value = make_ctx(pr_number=int(_TEST_PR))
    mock_call.return_value = 0
    with _record_arity_reads() as read:
        _run_main("comments", flag, _TEST_PR)
    cmd = _delegate_cmd(mock_call)
    assert cmd[cmd.index("--pr") + 1] == _TEST_PR
    assert flag in cmd
    assert cmd.count(_TEST_PR) == 1, f"PR number appeared twice: {cmd}"
    assert read == ["comments"]


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_review_takes_a_bare_pr_number(mock_resolve, mock_call):
    mock_resolve.return_value = make_ctx(pr_number=None, branch=None)
    mock_call.return_value = 0
    with _record_arity_reads() as read:
        _run_main("review", _TEST_PR)
    cmd = _delegate_cmd(mock_call)
    assert mock_call.call_args[0][0] == "cli.review_entry:main"
    assert cmd[cmd.index("--pr") + 1] == _TEST_PR
    assert "--self" not in cmd
    assert read == ["review"]


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_no_positional_candidate_skips_the_arity_read(mock_resolve, mock_call):
    """The common case must not pay for a delegate import."""
    mock_resolve.return_value = make_ctx()
    mock_call.return_value = 0
    with _record_arity_reads() as read:
        _run_main("comments", "--triage")
    assert read == []


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve_local")
def test_status_needs_no_delegate_to_classify(mock_resolve, mock_call, worktree):
    """`pr status` is internal, has no delegate, and takes no positional."""
    mock_resolve.return_value = make_ctx(worktree_root=worktree)
    mock_call.return_value = 0
    with _record_arity_reads() as read:
        assert _run_main("--repo-dir", str(worktree), "status") == 0
    assert read == []


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_internal_command_still_classifies_a_positional(mock_resolve, mock_call,
                                                        worktree):
    """`pr fix 3057` has no delegate to ask, but 3057 is still the target."""
    mock_resolve.return_value = make_ctx(worktree_root=worktree, pr_number=int(_TEST_PR))
    mock_call.return_value = 0
    with _record_arity_reads() as read, \
            patch("pr.state.load_state", return_value=None):
        _run_main("--repo-dir", str(worktree), "fix", _TEST_PR)
    assert mock_resolve.call_args[1]["pr_ref"] == _TEST_PR
    assert read == ["fix"], "an internal command is asked, and answers empty"


# ── positional_index ───────────────────────────────────────────────────────


def test_positional_index_skips_a_flag_value():
    extra = ["--reply", _TEST_REPLY_ID, "--body-file", _TEST_REPLY_BODY_FILE]
    assert cli.dispatch.positional_index(extra, frozenset({"--reply", "--body-file"})) == -1


def test_positional_index_finds_a_target_after_a_boolean_flag():
    assert cli.dispatch.positional_index(["--triage", _TEST_PR], frozenset({"--reply"})) == 1


def test_positional_index_treats_inline_values_as_self_contained():
    extra = ["--reply=1", _TEST_PR]
    assert cli.dispatch.positional_index(extra, frozenset({"--reply"})) == 1


def test_positional_index_removes_the_token_it_identified():
    """Index, not value: a target that repeats a flag's value must not misfire."""
    extra = ["--reply", _TEST_PR, "--triage", _TEST_PR]
    idx = cli.dispatch.positional_index(extra, frozenset({"--reply"}))
    assert idx == 3
    extra.pop(idx)
    assert extra == ["--reply", _TEST_PR, "--triage"]


def test_positional_index_without_arity_matches_the_historical_scan():
    assert cli.dispatch.positional_index(["--reply", _TEST_PR], frozenset()) == 1


# ── _delegate_value_flags ──────────────────────────────────────────────────


def test_delegate_value_flags_reads_the_real_delegate():
    flags = cli.dispatch.delegate_value_flags(cli.registry.COMMANDS["comments"])
    assert {"--reply", "--body-file", "--track"} <= flags
    assert "--triage" not in flags
    assert "--fix" not in flags


def test_delegate_value_flags_is_empty_for_an_internal_command():
    assert cli.dispatch.delegate_value_flags(cli.registry.COMMANDS["fix"]) == frozenset()


def test_delegate_value_flags_answers_from_the_delegates_own_parser():
    """The answer is the parser's, not a list mirrored here.

    Each option the delegate declares with a value is in the answer and each
    boolean is not, read off the module the registry names.
    """
    parser = importlib.import_module("cli.review_threads").build_parser()
    assert (cli.dispatch.delegate_value_flags(cli.registry.COMMANDS["comments"])
            == frozenset(core.tool_parser.value_taking_options(parser)))


def test_every_command_with_a_delegate_has_a_parser_factory():
    """A delegate `pr` cannot read arity from misclassifies its own target.

    `batch` and `create` run in-process rather than through a script, and each
    still has a parser of its own to answer `--help` with.
    """
    scripted = {name for name, spec in cli.registry.COMMANDS.items() if spec.script}
    assert set(cli.dispatch.PARSER_FACTORIES) == scripted | {"batch", "create", "push"}


@pytest.mark.parametrize("command", sorted(cli.registry.COMMANDS))
def test_a_parser_factory_takes_no_arguments(command):
    """`pr` calls these with none, mid-classification, before dispatch.

    A factory that grew a parameter — even a defaulted one — would be a
    delegate answering about a parser its caller cannot configure, and one that
    grew a *required* parameter would surface as a TypeError traceback out of
    `pr`'s positional scan rather than from the delegate that owns it. Nothing
    else makes this contract structural.

    Parametrized over the registry rather than over `PARSER_FACTORIES`, whose
    absence at the merge base would fail collection for this whole file and
    take every other test's base result with it.
    """
    factory = cli.dispatch.PARSER_FACTORIES.get(command)
    if factory is None:
        pytest.skip(f"{command} has no delegate parser")
    module_name, attr = factory.split(":", 1)
    build = getattr(importlib.import_module(module_name), attr)
    assert inspect.signature(build).parameters == {}


def test_delegate_value_flags_lets_a_broken_delegate_raise():
    """A module that will not import is a broken install, not a degradation.

    Returning empty here would misclassify the target and then fail dispatch
    two lines later, which is two confusing errors in place of one traceback.
    """
    with patch("cli.dispatch.importlib.import_module", side_effect=ImportError("boom")):
        with pytest.raises(ImportError):
            cli.dispatch.delegate_value_flags(cli.registry.COMMANDS["comments"])


@pytest.mark.parametrize(
    "command",
    sorted(name for name, spec in cli.registry.COMMANDS.items() if spec.script),
)
# passes-at-base: the registry-wide gate the probe had — the contract survives the mechanism change, so it holds on both sides
def test_every_delegate_answers_the_arity_question(command):
    """CI gate for the arity contract: a flag it cannot describe fails here first.

    The refusal in tool_parser only reaches a human who happens to run the
    ambiguous form of the command, so this asserts the whole registry up front —
    adding an unsupported nargs to any delegate breaks the build, not a user.
    """
    flags = cli.dispatch.delegate_value_flags(cli.registry.COMMANDS[command])
    assert flags, f"{command} named no value-taking option"


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_an_empty_arity_answer_still_dispatches_the_command(mock_resolve, mock_call):
    """A command whose delegate names no value-taking flag still runs."""
    mock_resolve.return_value = make_ctx(pr_number=int(_TEST_PR))
    mock_call.return_value = 0
    with patch("cli.dispatch.delegate_value_flags", return_value=frozenset()):
        assert _run_main("comments", "--triage", _TEST_PR) == 0
    cmd = mock_call.call_args[0][1]
    assert mock_call.call_args[0][0] == "cli.review_threads:main"
    assert "--triage" in cmd


# The commands whose scan is arity-blind by construction: no script, so
# _delegate_value_flags has no parser to probe, and not excused from the scan by
# `takes_target=False`. Derived from the registry so a new one is covered on the
# commit that adds it.
_ARITY_BLIND_COMMANDS = sorted(
    name for name, spec in cli.registry.COMMANDS.items()
    if spec.script is None and spec.takes_target
)


@pytest.mark.parametrize("command", _ARITY_BLIND_COMMANDS)
def test_a_command_with_no_delegate_declares_no_value_taking_flag(command):
    """The guard on `takes_target` being declared by hand.

    A command with no delegate has no parser for _delegate_value_flags to read,
    so its positional scan degrades to "first bare token wins" — exactly what ate
    `pr create --title`. None of these declares an option that consumes a value
    today, which is the only reason create was the only one broken. Asserting it
    means the next one fails here rather than at a user's dangling flag.

    Read off the same function the wrapper classifies with, so this cannot drift
    from the arity it acts on.
    """
    subparser = core.tool_parser.subparsers(cli.pr._build_parser())[command]
    offenders = core.tool_parser.value_taking_options(subparser)
    assert not offenders, (
        f"pr {command} declares value-taking options ({', '.join(offenders)}), but "
        f"{command} has no delegate to read arity from — the value would be classified "
        f"as the command's target and dropped from the forwarded argv. Either give "
        f"{command} takes_target=False if it takes no positional target, or give it "
        f"a delegate whose build_parser is registered in PARSER_FACTORIES."
    )
