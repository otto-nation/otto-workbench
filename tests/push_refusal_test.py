"""git.push refusals: what one records, how it is classified, and the ssh auth diagnosis."""

import signal
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import git.client  # noqa: E402
import core.proc  # noqa: E402
import git.push  # noqa: E402
from core.trail import Trail  # noqa: E402

from conftest import _last_event  # noqa: E402

from push_support import _never_runs, _HOOK_DUMP, _RESET_DUMP_FULL


# ── what a refusal records ──────────────────────────────────────────────────


def test_a_refusal_records_the_verdict_not_the_banner(monkeypatch):
    """The banner is the first 500 characters; the verdict is the last line."""
    monkeypatch.setattr(git.client, "run",
                        lambda *a, **k: core.proc.CmdResult(1, "", _HOOK_DUMP))
    trail = Trail.start(script="test", context={})

    result = git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x",
                       trail=trail)

    recorded = _last_event()["data"]["error"]
    assert "✗ pre-commit: lint:ts failed" in recorded
    assert "Running pre-push checks" not in recorded
    assert result.status is git.push.PushStatus.REFUSED


def test_a_refusal_keeps_the_whole_hook_output(monkeypatch):
    monkeypatch.setattr(git.client, "run",
                        lambda *a, **k: core.proc.CmdResult(1, "", _HOOK_DUMP))
    trail = Trail.start(script="test", context={})

    result = git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x",
                       trail=trail)

    assert result.log is not None
    assert "Running pre-push checks" in result.log.read_text()
    assert "✗ pre-commit: lint:ts failed" in result.log.read_text()


def test_a_refusal_still_records_the_sha_and_branch(monkeypatch):
    monkeypatch.setattr(git.client, "run",
                        lambda *a, **k: core.proc.CmdResult(1, "", "denied"))
    trail = Trail.start(script="test", context={})

    git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x", trail=trail)

    data = _last_event()["data"]
    assert data["sha"] == "1a2b3c4d"
    assert data["branch"] == "feat/x"


def test_a_push_without_a_trail_still_reports_the_refusal(monkeypatch):
    monkeypatch.setattr(git.client, "run",
                        lambda *a, **k: core.proc.CmdResult(1, "", "denied"))

    result = git.push.push("/tmp/wt", gated=False, sha="1a2b3c4d", branch="feat/x")

    assert result.status is git.push.PushStatus.REFUSED
    assert result.log is None


def test_the_refused_report_names_the_full_output(capsys, tmp_path):
    artifact = tmp_path / "214e9758c739-1-push.log"
    artifact.write_text("everything the hook said")
    result = git.push.PushResult(
        git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.HOOK, output="✗ lint:ts failed", log=artifact,
    )

    git.push.report(result, "/tmp/wt")

    assert f"full output: {artifact}" in capsys.readouterr().err


def test_the_refused_report_omits_the_line_when_there_is_no_artifact(capsys):
    result = git.push.PushResult(
        git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
        refusal=git.push.Refusal.HOOK, output="✗ lint:ts failed",
    )

    git.push.report(result, "/tmp/wt")

    assert "full output:" not in capsys.readouterr().err


# ── the refusal classifier ──────────────────────────────────────────────────


@pytest.mark.parametrize("output,expected", [
    ("! [rejected]  main -> main (non-fast-forward)", git.push.Refusal.DIVERGED),
    ("Updates were rejected because the remote contains work.\nfetch first",
     git.push.Refusal.DIVERGED),
    ("! [rejected] main -> main (stale info)", git.push.Refusal.DIVERGED),
    ("Updates were rejected because the tip is behind its remote counterpart.",
     git.push.Refusal.DIVERGED),
    ("ssh: Could not resolve host: github.com", git.push.Refusal.UNREACHABLE),
    ("ssh: connect to host github.com port 22: Connection timed out",
     git.push.Refusal.UNREACHABLE),
    ("fatal: Could not read from remote repository.", git.push.Refusal.TRANSPORT),
    ("Permission denied (publickey).", git.push.Refusal.AUTH),
    ("ERROR: Repository not found.", git.push.Refusal.AUTH),
    ("validate-all failed\nerror: failed to push some refs to 'origin'",
     git.push.Refusal.HOOK),
    ("something nobody has seen before", git.push.Refusal.OTHER),
    ("Read from remote host github.com: Connection reset by peer",
     git.push.Refusal.DROPPED),
    ("client_loop: send disconnect: Broken pipe", git.push.Refusal.DROPPED),
    ("fatal: The remote end hung up unexpectedly", git.push.Refusal.DROPPED),
])
def test_classify_names_the_refusal(output, expected):
    assert git.push.classify(output) == expected


def test_a_hook_rejection_outranks_nothing_it_shares_words_with():
    """A transport failure also prints the generic refusal line; it wins."""
    output = ("fatal: Could not read from remote repository.\n"
              "error: failed to push some refs to 'origin'")
    assert git.push.classify(output) is git.push.Refusal.TRANSPORT


def test_an_auth_denial_outranks_the_generic_line_underneath_it():
    """The real shape of a refused key, which is why AUTH is checked first.

    ssh prints the denial and git prints "Could not read from remote
    repository" underneath — so the generic transport marker matches every auth
    failure too, and checking it first reported a wrong credential as a network
    fault. This is verbatim what a push with an unauthorised key produces.
    """
    output = ("git@github.com: Permission denied (publickey).\n"
              "fatal: Could not read from remote repository.\n"
              "Please make sure you have the correct access rights\n"
              "and the repository exists.")
    assert git.push.classify(output) is git.push.Refusal.AUTH


def test_a_drop_outranks_the_refusal_line_it_prints_too():
    """Neither the tests nor the remote said no, and the output must not read so.

    Classified as HOOK it tells the operator their checks failed, which is the
    misdirection this ordering removes — the gates had all printed a tick.
    """
    assert git.push.classify(_RESET_DUMP_FULL) is git.push.Refusal.DROPPED


@pytest.mark.parametrize("output", [
    "Permission denied (publickey).",
    "ssh: Could not resolve host: github.com",
    "ERROR: Repository not found.",
])
def test_an_auth_failure_is_not_a_drop(output):
    """Nothing was established, so the remote has nothing to be asked about.

    Verifying these would ask `ls-remote` to reach a remote the credentials just
    failed against, and report a push that never happened as one that could not
    be confirmed. Which of the three non-drop refusals they land on is the
    parametrisation above's business; what matters here is that none is
    `DROPPED`.
    """
    assert git.push.classify(output) is not git.push.Refusal.DROPPED


# ── the drop predicate ──────────────────────────────────────────────────────


def test_a_signal_death_with_no_output_is_a_drop():
    """A git that took a signal on the way down usually says nothing at all."""
    assert git.push._dropped(core.proc.CmdResult(-signal.SIGPIPE, "", ""))


def test_a_plain_refusal_is_not_a_drop():
    assert not git.push._dropped(
        core.proc.CmdResult(1, "", "error: failed to push some refs to 'origin'"))


def test_a_shells_141_is_not_how_a_signal_arrives_here():
    """`proc.run` reports a signal as the negated number, never as 128 + it.

    141 is the shell's rendering of SIGPIPE and reaches no Python here, so a
    predicate written against it would be dead code that never fires.
    """
    assert not git.push._dropped(core.proc.CmdResult(128 + signal.SIGPIPE, "", ""))


def test_a_killed_push_speaks_through_the_signal_when_it_said_nothing():
    """An empty excerpt under the headline is a failure nobody can read."""
    spoken = git.push._push_output(core.proc.CmdResult(-signal.SIGPIPE, "", ""))
    assert "SIGPIPE" in spoken


def test_a_killed_push_that_did_speak_is_quoted_rather_than_summarised():
    assert git.push._push_output(
        core.proc.CmdResult(-signal.SIGPIPE, "", _RESET_DUMP_FULL)) == _RESET_DUMP_FULL


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGINT, signal.SIGPIPE])
def test_a_killed_push_names_the_signal_without_asserting_a_cause(sig):
    """The drop predicate fires on any signal, so the account may not name one cause.

    A git the OOM killer or an operator ended has nothing to do with the
    connection, and saying it died mid-transfer asserts a cause the return code
    does not carry — the overclaiming the headlines here take care to avoid.
    What every signal death does establish is the same, narrower thing: git
    stopped before it could say what the remote received.
    """
    spoken = git.push._push_output(core.proc.CmdResult(-sig, "", ""))

    assert sig.name in spoken
    assert "connection" not in spoken.lower()
    assert "unconfirmed" in spoken


# ── ssh auth diagnosis ──────────────────────────────────────────────────────
#
# `Permission denied (publickey)` is one sentence for causes with different
# remedies, and the one it hides completely is an agent that will not sign for
# the calling process. A push made from a sandboxed subprocess, a cron job, or a
# a detached shell fails exactly like a key the remote has never seen — the
# distinction lives only in `ssh -v`, which nobody runs before concluding their
# key is wrong.


def _ssh_probe(monkeypatch, output: str, *, url: str = "git@github.com:o/r.git"):
    """Stub the remote URL read and the ssh probe that follows it."""
    monkeypatch.setattr(git.client, "out", lambda *a, **k: url)
    monkeypatch.setattr(
        core.proc, "run", lambda *a, **k: core.proc.CmdResult(255, "", output),
    )


_KEY_ACCEPTED_LINE = (
    "debug1: Server accepts key: /home/u/.ssh/id_ed25519 ED25519 SHA256:abc\n"
)

_KEY_ACCEPTED_TRACE = (
    "debug1: Offering public key: /home/u/.ssh/id_ed25519 ED25519 SHA256:abc\n"
    "debug1: Server accepts key: /home/u/.ssh/id_ed25519 ED25519 SHA256:abc\n"
    "git@github.com: Permission denied (publickey).\n"
)

_KEY_REJECTED_TRACE = (
    "debug1: Offering public key: /home/u/.ssh/id_ed25519 ED25519 SHA256:abc\n"
    "debug1: Authentications that can continue: publickey\n"
    "git@github.com: Permission denied (publickey).\n"
)


def test_a_key_the_remote_accepted_names_the_agent(monkeypatch):
    """The failure that is invisible without `ssh -v`.

    The server took the key and the signature failed after it, which means the
    key is authorised and the local agent would not sign. Reported as a rejected
    key it sends the operator into the remote's settings for a fault on their
    own machine.
    """
    _ssh_probe(monkeypatch, _KEY_ACCEPTED_TRACE)
    hint = git.push.diagnose_ssh_auth("/tmp/wt", "origin")
    assert "agent" in hint
    assert "not a key the remote rejected" in hint


_AUTH_WORKS_TRACE = (
    "debug1: Server accepts key: /home/u/.ssh/id_ed25519 ED25519 SHA256:abc\n"
    "Hi someone! You've successfully authenticated, but GitHub does not "
    "provide shell access.\n"
)


def test_a_working_agent_is_not_blamed_for_a_missing_repository(monkeypatch):
    """The false positive the accept-line check alone produces.

    A repository that does not exist is denied with the same sentence as a bad
    credential, and its key is accepted just as readily — so a probe that stops
    at "Server accepts key" reports a healthy agent as broken. Authenticating
    all the way through is what tells the two apart, and it sends the reader to
    the remote path instead.
    """
    _ssh_probe(monkeypatch, _AUTH_WORKS_TRACE)
    hint = git.push.diagnose_ssh_auth("/tmp/wt", "origin")
    assert "credentials are not the problem" in hint
    assert "agent" not in hint


@pytest.mark.parametrize("greeting", [
    "Hi someone! You've successfully authenticated, but GitHub does not "
    "provide shell access.",
    "Welcome to Gitea, someone!",
    "logged in as someone.",
    "a greeting from a forge nobody here has seen",
])
def test_any_authenticated_host_clears_the_agent(monkeypatch, greeting):
    """Safe by default for a forge whose success wording is not known here.

    Deciding this by listing the greetings that mean success is a guess about
    text every forge spells differently, and one missing from the list reads as
    a failure — blaming a healthy agent on a host that authenticated fine. A
    Gitea remote did exactly that. Matching the denial instead puts every
    unrecognised greeting on the safe side.
    """
    _ssh_probe(monkeypatch, _KEY_ACCEPTED_LINE + greeting + "\n")
    hint = git.push.diagnose_ssh_auth("/tmp/wt", "origin")
    assert "credentials are not the problem" in hint
    assert "agent" not in hint


def test_a_key_the_remote_never_took_says_nothing(monkeypatch):
    """git's own message is already right here, so the probe adds nothing.

    A second sentence restating it would be noise on the one path where the
    obvious reading is the correct one.
    """
    _ssh_probe(monkeypatch, _KEY_REJECTED_TRACE)
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


def test_an_https_remote_is_not_probed(monkeypatch):
    """An ssh probe says nothing true about a credential helper's token."""
    _ssh_probe(monkeypatch, _KEY_ACCEPTED_TRACE,
               url="https://github.com/o/r.git")
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


def test_a_probe_that_cannot_run_says_nothing(monkeypatch):
    """Best-effort: a silent probe leaves the real error as the only claim."""
    _ssh_probe(monkeypatch, "")
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


def test_a_remote_with_no_url_is_not_probed(monkeypatch):
    monkeypatch.setattr(git.client, "out", lambda *a, **k: "")
    monkeypatch.setattr(core.proc, "run", _never_runs)
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


@pytest.mark.parametrize("output", [
    "Host key verification failed.",
    "ssh: Could not resolve hostname ghe.acme.com",
    "ssh: connect to host ghe.acme.com port 22: Connection refused",
    "ssh: connect to host ghe.acme.com port 22: Operation timed out",
])
def test_a_probe_that_never_reached_auth_says_nothing(monkeypatch, output):
    """Silence is not evidence the credentials are fine.

    Every one of these is a probe that failed before authentication was ever
    attempted, so it carries no denial — and the "no denial found" branch reads
    that as success and tells the operator their credentials are good. A host
    key it will not verify is the live case: GitLab and Bitbucket both answer
    that way from a machine that has never connected to them.
    """
    _ssh_probe(monkeypatch, output)
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


def test_a_timed_out_probe_says_nothing(monkeypatch):
    """`proc.run` reports a timeout as a return code, not an exception.

    Its stderr names the bound rather than anything ssh said, so the output is
    non-empty and carries no denial — which lands on the "credentials are fine"
    branch unless the code is checked.
    """
    monkeypatch.setattr(git.client, "out",
                         lambda *a, **k: "git@github.com:o/r.git")
    monkeypatch.setattr(
        core.proc, "run",
        lambda *a, **k: core.proc.CmdResult(
            core.proc.TIMEOUT_RETURNCODE, "", "timed out after 30.0s"),
    )
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


def test_the_probe_does_not_write_to_known_hosts(monkeypatch):
    """A diagnostic must not pin trust as a side effect of explaining an error.

    `StrictHostKeyChecking=accept-new` writes an unknown host into known_hosts,
    which changes the machine's trust state because a push failed — and makes
    the probe's second run behave differently from its first.
    """
    seen = {}
    monkeypatch.setattr(git.client, "out",
                         lambda *a, **k: "git@github.com:o/r.git")

    def _capture(cmd, **kwargs):
        seen["cmd"] = cmd
        return core.proc.CmdResult(255, "", "Permission denied (publickey).")

    monkeypatch.setattr(core.proc, "run", _capture)
    git.push.diagnose_ssh_auth("/tmp/wt", "origin")

    assert "accept-new" not in seen["cmd"]
    assert "UserKnownHostsFile=/dev/null" in seen["cmd"]


def test_a_missing_ssh_binary_says_nothing_rather_than_raising(monkeypatch):
    """A container or sandbox with no `ssh` on `PATH` is a best-effort miss.

    `proc.run` does not catch `FileNotFoundError` the way `gh.client.run` does,
    so the caller must — otherwise a missing binary crashes the whole refusal
    report instead of leaving it as it was.
    """
    monkeypatch.setattr(git.client, "out",
                         lambda *a, **k: "git@github.com:o/r.git")

    def _raise(*a, **k):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(core.proc, "run", _raise)
    assert git.push.diagnose_ssh_auth("/tmp/wt", "origin") == ""


@pytest.mark.parametrize("url,host,port", [
    ("git@github.com:o/r.git", "git@github.com", ""),
    ("ssh://git@github.com/o/r.git", "git@github.com", ""),
    ("ssh://git@ghe.acme.com:2222/o/r.git", "git@ghe.acme.com", "2222"),
    # A colon in the path rather than the authority, which is not a port.
    ("ssh://git@ghe.acme.com/o/r:x.git", "git@ghe.acme.com", ""),
    ("https://github.com/o/r.git", "", ""),
    ("/srv/local/repo.git", "", ""),
])
def test_the_ssh_host_is_read_from_the_remote_url(monkeypatch, url, host, port):
    monkeypatch.setattr(git.client, "out", lambda *a, **k: url)
    target = git.push._ssh_host("/tmp/wt", "origin")
    assert (target.host, target.port) == (host, port)


def test_a_port_reaches_ssh_as_a_flag_not_part_of_the_hostname():
    """`host:2222` is not an ssh destination — it resolves as a hostname.

    Passed through whole, ssh answers "Could not resolve hostname
    ghe.acme.com:2222" and the probe reports a DNS fault for every enterprise
    remote on a non-default port. That is the wrong-diagnosis class this change
    exists to remove, so it must not be reintroduced by the diagnosis itself.
    """
    target = git.push.SshTarget("git@ghe.acme.com", "2222")
    assert target.args == ["-p", "2222", "git@ghe.acme.com"]
    assert not any(":" in arg for arg in target.args)


def test_an_unprobeable_target_builds_no_ssh_arguments():
    """Empty rather than a destination of "", which ssh would try to resolve."""
    assert git.push.SshTarget().args == []
    assert not git.push.SshTarget().probeable


def test_an_auth_refusal_reports_the_agent_hint(monkeypatch, capsys):
    """The hint reaches the operator, under git's own words rather than instead."""
    _ssh_probe(monkeypatch, _KEY_ACCEPTED_TRACE)
    git.push.report(
        git.push.PushResult(
            git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
            refusal=git.push.Refusal.AUTH,
            output="git@github.com: Permission denied (publickey).",
        ),
        "/tmp/wt",
    )
    out = capsys.readouterr().err
    assert "would not accept your credentials" in out
    assert "Permission denied" in out
    assert "agent" in out


def test_a_non_auth_refusal_runs_no_probe(monkeypatch, capsys):
    """A hook rejection costs no round trip to ssh."""
    monkeypatch.setattr(git.push, "diagnose_ssh_auth", _never_runs)
    git.push.report(
        git.push.PushResult(
            git.push.PushStatus.REFUSED, sha="1a2b3c4d", branch="feat/x",
            refusal=git.push.Refusal.HOOK, output="validate-all failed",
        ),
        "/tmp/wt",
    )
    assert "validate-all failed" in capsys.readouterr().err
