"""Helpers shared by the suites split out of the former pr_cli_test.py."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "ai" / "bin"
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402

import core.run_lock  # noqa: E402


def _cmd_fix(argv, ctx, *, worktree_head=None, **kw):
    """Call cmd_fix with the entry point's BIN_DIR, as `_dispatch` does.

    `worktree_head` pins what the review gate believes the checkout's HEAD is.
    It has to be injected rather than left to run: `cmd_fix` asks git for it
    (the review child reads the worktree, not the PR's remote head), and the
    tests point `worktree_root` at a path that does not exist. Defaults to the
    context's own head, which is what a checkout sitting on the reviewed
    commit would answer.
    """
    kw.setdefault("bin_dir", BIN_DIR)
    with patch("cli.pr_commands._worktree_head",
               return_value=worktree_head if worktree_head is not None
               else ctx.head_sha):
        return cli.pr.cmd_fix(argv, ctx, **kw)


# Shared fixture values for the positional-vs-flag-value tests below.
_TEST_PR = "3057"


def _run_main(*argv):
    """Run `cli.pr.main` with the given argv and return its exit code.

    `main` returns rather than exiting now, matching every other
    `cli.<name>.main`; the `ai/bin/pr` shim does the `sys.exit`. SystemExit
    is still caught because the refusal paths below it — an unserved
    `--schema-version`, a target a command cannot resolve — raise it from a
    library, and in production `publishing.call_entry_point` is what turns
    those back into a code.

    `bin_dir` is passed the way the shim passes it. Patching the branch check
    makes `update_to_remote` treat the checkout as being on the wrong branch
    and return early, without swallowing the call that tests of the fetch
    axis still need to observe.
    """
    mock_trail = MagicMock()
    with patch("cli.pr.Trail.start", return_value=mock_trail), \
         patch("git.topology.current_branch_quiet", return_value=None):
        try:
            return cli.pr.main(list(argv), bin_dir=BIN_DIR)
        except SystemExit as e:
            return e.code


def _delegate_cmd(mock_call):
    """The argv of the last dispatched in-process call."""
    assert mock_call.call_args_list, "no delegate was dispatched"
    return mock_call.call_args_list[-1].args[1]


def _lock_file(target_dir):
    return Path(target_dir) / core.run_lock.LOCK_FILE


def _dispatch_stage(*argv, ctx):
    """Run main() as far as dispatch, with the handler stubbed out.

    Resolution, fetch and lock all happen before _dispatch, so stubbing it is
    what lets one parametrized test cover every command without nine sets of
    handler mocks.
    """
    with patch("pr.context.resolve", return_value=ctx) as remote, \
         patch("pr.context.resolve_local", return_value=ctx) as local, \
         patch("pr.sync.update_to_remote", return_value=ctx) as update, \
         patch("cli.pr._dispatch", return_value=0):
        _run_main(*argv)
    return SimpleNamespace(remote=remote, local=local, update=update)
