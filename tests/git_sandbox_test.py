"""The pytest git sandbox proves itself.

The bats suite has `tests/test_helper_isolation.bats` for the same job. This is
its counterpart: a repo a test creates reads none of the machine's git
configuration, and the sandbox is demonstrably what makes that true.
"""

import os
import subprocess

from conftest import GIT_TIMEOUT, init_repo, seed_repo

# Each of these is on for real on a workbench machine, and each costs a test
# something it never asked for: an orphaned `git fsmonitor--daemon` per temp
# repo, an index rewrite, a gitleaks scan of every staged file.
INHERITED_NOTHING = ("core.fsmonitor", "core.untrackedCache")


def _config(repo, key):
    """What the repo at *repo* reads for *key*, or "" when it reads nothing.

    Not `git_out`: a key nothing sets exits 1, which `run_checked` beneath it
    raises on, and an unset key is the expected result of half these cases.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", key],
        capture_output=True, text=True, timeout=GIT_TIMEOUT,
    )
    return result.stdout.strip()


def test_a_temp_repo_inherits_none_of_the_machines_git_config(tmp_path):
    repo = init_repo(tmp_path / "repo")

    assert [_config(repo, key) for key in INHERITED_NOTHING] == ["", ""]


def test_a_temp_repo_reads_the_sandboxed_hooks_path(tmp_path):
    """The hook override is the one setting the sandbox writes rather than hides.

    Emptying the config files would leave `core.hooksPath` unset, which is not
    the same thing: a few suites set it repo-locally, and the `-c` is what
    outranks those.
    """
    repo = init_repo(tmp_path / "repo")

    assert _config(repo, "core.hooksPath") == os.devnull


# The cases above would pass just as well on a machine that never set any of
# these, so they report nothing until the sandbox is known to be what git reads.
# Lifting it is the only way to say that.
def test_the_sandbox_is_what_hides_them(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "repo")
    fake_global = tmp_path / "gitconfig"
    fake_global.write_text("[core]\n\tfsmonitor = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_global))

    assert _config(repo, "core.fsmonitor") == "true"


def test_committing_in_a_temp_repo_starts_no_fsmonitor_daemon(tmp_path):
    """The cost the sandbox exists to prevent, asserted where it is paid.

    A daemon outlives the repo that started it, so this is not a leak the test
    that causes it ever notices — it surfaces as unrelated tests timing out once
    enough of them have accumulated to saturate FSEvents.
    """
    repo = seed_repo(tmp_path / "repo")

    assert not (repo / ".git" / "fsmonitor--daemon.ipc").exists()
