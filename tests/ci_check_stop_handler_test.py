"""ci-check owns the stop handler only when it is the process."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.ci_check  # noqa: E402
import core.log  # noqa: E402
import core.proc  # noqa: E402

from pr_cli_support import _run_main  # noqa: E402


def test_the_direct_entry_installs_the_stop_handler(monkeypatch):
    """ai/bin/ci-check is the whole process, so it owns SIGTERM.

    The batch stops a step with os.killpg(SIGTERM) then SIGKILL. Without a
    handler, ci-check dies with no Python code run, and children started
    start_new_session=True survive in a worktree the batch believes idle.
    """
    installed = []
    monkeypatch.setattr(core.proc, "install_stop_handler",
                        lambda announce: installed.append(announce))
    with patch.object(cli.ci_check, "build_parser", side_effect=RuntimeError("stop")):
        with pytest.raises(RuntimeError):
            cli.ci_check.main([])
    assert installed == [core.log.interrupted]


def test_an_in_process_caller_does_not_install_a_second_stop_handler(monkeypatch):
    """signal.signal overwrites without chaining; pr already installed one."""
    installed = []
    monkeypatch.setattr(core.proc, "install_stop_handler",
                        lambda announce: installed.append(announce))
    with patch.object(cli.ci_check, "build_parser", side_effect=RuntimeError("stop")):
        with pytest.raises(RuntimeError):
            cli.ci_check.main([], install_signal_handler=False)
    assert installed == []


@patch("core.publishing.call_entry_point", return_value=0)
@patch("pr.context.resolve")
def test_pr_ci_does_not_install_a_second_stop_handler(mock_resolve, mock_call):
    """The in-process `pr ci` path declines the handler the shim would install."""
    mock_resolve.return_value = make_ctx()
    _run_main("ci")
    assert mock_call.call_args.kwargs["install_signal_handler"] is False
