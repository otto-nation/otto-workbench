"""Tests for `cli.queue_gate` and the registry's queue class table."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.ci_check  # noqa: E402
import cli.pr  # noqa: E402
import cli.queue_gate  # noqa: E402
import cli.review_entry  # noqa: E402
import core.timeouts  # noqa: E402
import otto_queue.ticket  # noqa: E402
from cli.queue_gate import QUEUED_MARKER, class_for, gate, label_for  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryClass, EntryState  # noqa: E402
from otto_queue.ticket import TicketOutcome, TicketResult  # noqa: E402

PY = sys.executable
BIN_DIR = REPO_ROOT / "ai" / "bin"


@pytest.fixture(autouse=True)
def _clear_gate_env(monkeypatch):
    monkeypatch.delenv(QUEUED_MARKER, raising=False)
    monkeypatch.delenv(cli.queue_gate.NO_QUEUE_ENV, raising=False)
    monkeypatch.delenv(otto_queue.ticket.LABEL_ENV, raising=False)


_CLASS_CASES = (
    ("create", [], EntryClass.LIGHT),
    ("create", ["--dry-run"], EntryClass.LIGHT),
    ("create", ["--title", "t"], EntryClass.LIGHT),
    ("create", ["--title", "t", "--body", "b"], None),
    ("status", [], None),
    ("ci", [], None),
    ("ci", ["--fix"], EntryClass.HEAVY),
    ("review", [], EntryClass.HEAVY),
    ("review", ["--self", "--fix"], EntryClass.HEAVY),
    ("review", ["--post"], None),
    ("review", ["--publish", "approve"], None),
    ("review", ["--repair"], None),
    ("review", ["--summary"], None),
    ("review", ["--dismiss"], None),
    ("review", ["--list"], None),
    ("review", ["--recover"], EntryClass.HEAVY),
    ("review", ["--self", "--track"], None),
    ("review", ["--self", "--track", "--fix"], EntryClass.HEAVY),
    ("comments", [], None),
    ("comments", ["--fix"], EntryClass.HEAVY),
    ("comments", ["--triage"], EntryClass.LIGHT),
    ("comments", ["--fix", "--finish"], EntryClass.HEAVY),
    ("comments", ["--triage", "--finish"], EntryClass.LIGHT),
    ("comments", ["--finish"], None),
    ("comments", ["--finish", "--post"], None),
    ("comments", ["--reply", "1"], None),
    ("comments", ["--settle", "1"], None),
    ("fix", [], EntryClass.HEAVY),
    ("rebase", [], None),
    ("rebase", ["--fix"], EntryClass.HEAVY),
    ("rebase", ["--fix", "--abort"], None),
    ("rebase", ["--fix", "--push-only"], None),
    ("push", [], None),
    ("describe", [], EntryClass.LIGHT),
    ("describe", ["--body", "x"], None),
    ("describe", ["--body-file", "f"], None),
    ("stack", [], None),
    ("board", [], None),
    ("models", [], None),
    ("batch", [], None),
    ("gc", [], None),
)


@pytest.mark.parametrize("command,argv,expected", _CLASS_CASES)
def test_class_for_matches_the_queue_table(command, argv, expected):
    assert class_for(command, argv) is expected


def test_gate_skips_when_there_is_no_class(monkeypatch):
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    assert gate("status", [], reexec=(PY, "-c", "pass"), entry_class=None, label="L") is None


def test_gate_skips_when_no_queue_is_set(monkeypatch):
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    assert gate("create", [], reexec=(PY, "-c", "pass"),
                entry_class=EntryClass.LIGHT, label="L", no_queue=True) is None


def test_gate_skips_when_the_env_opt_out_is_set(monkeypatch):
    monkeypatch.setenv(cli.queue_gate.NO_QUEUE_ENV, "1")
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    assert gate("create", [], reexec=(PY, "-c", "pass"),
                entry_class=EntryClass.LIGHT, label="L") is None


def test_gate_skips_when_already_inside_a_ticket(monkeypatch):
    monkeypatch.setenv(otto_queue.ticket.ENTRY_ENV, "7")
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    assert gate("create", [], reexec=(PY, "-c", "pass"),
                entry_class=EntryClass.LIGHT, label="L") is None


def test_gate_skips_when_the_queued_marker_is_set(monkeypatch):
    monkeypatch.setenv(QUEUED_MARKER, "1")
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    assert gate("create", [], reexec=(PY, "-c", "pass"),
                entry_class=EntryClass.LIGHT, label="L") is None


def test_gate_sets_the_marker_and_returns_the_ticket_status(monkeypatch):
    seen = {}

    def fake_run(request, _policy, **_kw):
        seen["argv"] = request.argv
        seen["class"] = request.entry_class
        seen["label"] = request.label
        seen["marker"] = os.environ.get(QUEUED_MARKER)
        return TicketResult(7, TicketOutcome.RAN, 1)

    monkeypatch.setattr(otto_queue.ticket, "run_ticket", fake_run)
    reexec = (PY, "-c", "pass")
    assert gate("comments", ["--fix"], reexec=reexec,
                entry_class=EntryClass.HEAVY, label="pr comments --fix") == 7
    assert seen["argv"] == reexec
    assert seen["class"] is EntryClass.HEAVY
    assert seen["label"] == "pr comments --fix"
    assert seen["marker"] == "1"


def test_label_for_prefers_the_env_override(monkeypatch):
    monkeypatch.setenv(otto_queue.ticket.LABEL_ENV, "x")
    assert label_for("comments", ["--fix"], pr_ref="123", branch="b",
                     repo_dir=None) == "x"


def test_label_for_includes_the_pr_number(monkeypatch):
    monkeypatch.delenv(otto_queue.ticket.LABEL_ENV, raising=False)
    monkeypatch.setattr(
        "pr.target.repo_identity_from_origin",
        lambda *_a, **_k: type("I", (), {"label": "acme/widget"})(),
    )
    text = label_for("comments", ["--fix"], pr_ref="123", branch="feat", repo_dir=None)
    assert text.startswith("pr comments --fix")
    assert "#123" in text


def test_label_for_uses_the_branch_when_no_pr(monkeypatch):
    monkeypatch.delenv(otto_queue.ticket.LABEL_ENV, raising=False)
    monkeypatch.setattr(
        "pr.target.repo_identity_from_origin",
        lambda *_a, **_k: type("I", (), {"label": "acme/widget"})(),
    )
    text = label_for("review", ["--self", "--fix"], pr_ref=None, branch="feat",
                     repo_dir=None)
    assert text == "pr review --self --fix · acme/widget@feat"


def test_a_ticket_under_a_broken_ledger_does_not_reenter(tmp_path, monkeypatch):
    state = Path(os.environ["WORKBENCH_STATE_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    (state / "queue").write_text("not a directory")
    monkeypatch.setenv("PROBE_LIB", str(LIB_DIR))
    probe = textwrap.dedent(
        """
        import os, sys
        sys.path.insert(0, os.environ["PROBE_LIB"])
        import cli.queue_gate
        from otto_queue.model import EntryClass
        code = cli.queue_gate.gate(
            "status", [],
            reexec=(sys.executable, "-c", "raise SystemExit(4)"),
            entry_class=EntryClass.LIGHT, label="inner")
        raise SystemExit(3 if code is not None else 0)
        """
    )
    driver = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, sys.argv[1])
        import cli.queue_gate
        from otto_queue.model import EntryClass
        code = cli.queue_gate.gate(
            "status", [],
            reexec=(sys.executable, "-c", sys.argv[2]),
            entry_class=EntryClass.LIGHT, label="outer")
        sys.exit(0 if code == 0 else (code if isinstance(code, int) else 1))
        """
    )
    result = subprocess.run(
        [PY, "-c", driver, str(LIB_DIR), probe],
        timeout=core.timeouts.LAUNCH, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_a_real_ticket_records_the_label_and_sets_the_entry_env(capsys):
    probe = "import os,sys; sys.exit(0 if os.environ.get('OTTO_QUEUE_ENTRY') else 3)"
    code = gate(
        "create", [],
        reexec=(PY, "-c", probe),
        entry_class=EntryClass.LIGHT, label="L",
    )
    assert code == 0
    [entry] = Ledger.open().entries()
    assert (entry.state, entry.label) == (EntryState.DONE, "L")
    assert capsys.readouterr().out == ""


def _fake_ticket(seen, code=9):
    def run(request, _policy, **_kw):
        seen["argv"] = request.argv
        seen["class"] = request.entry_class
        seen["label"] = request.label
        seen["marker"] = os.environ.get(QUEUED_MARKER)
        return TicketResult(code, TicketOutcome.RAN, 1)
    return run


def test_pr_main_queues_before_locks(monkeypatch):
    seen = {}
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", _fake_ticket(seen))
    monkeypatch.setattr(
        "pr.context.resolve_at",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("resolved")),
    )
    monkeypatch.setattr(
        "cli.locks.hold_declared",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("locked")),
    )
    code = cli.pr.main(["comments", "--fix"], bin_dir=BIN_DIR)
    assert code == 9
    assert seen["argv"] == (sys.executable, str(BIN_DIR / "pr"), "comments", "--fix")
    assert seen["class"] is EntryClass.HEAVY
    assert seen["label"].startswith("pr comments --fix")
    assert seen["marker"] == "1"


def test_pr_main_label_uses_pr_number_and_env(monkeypatch):
    seen = {}
    monkeypatch.setattr(otto_queue.ticket, "run_ticket", _fake_ticket(seen))
    monkeypatch.setattr(
        "pr.target.repo_identity_from_origin",
        lambda *_a, **_k: type("I", (), {"label": "acme/widget"})(),
    )
    assert cli.pr.main(["comments", "--fix", "--pr", "123"], bin_dir=BIN_DIR) == 9
    assert "#123" in seen["label"]
    monkeypatch.delenv(QUEUED_MARKER, raising=False)
    monkeypatch.setenv(otto_queue.ticket.LABEL_ENV, "x")
    assert cli.pr.main(["comments", "--fix"], bin_dir=BIN_DIR) == 9
    assert seen["label"] == "x"


def test_pr_main_skips_the_gate_when_asked(monkeypatch):
    dispatched = []

    def lock(*_a, **_k):
        dispatched.append(True)
        return 0

    monkeypatch.setattr(otto_queue.ticket, "run_ticket",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    monkeypatch.setattr(cli.pr, "_lock_resolve_and_dispatch", lock)
    assert cli.pr.main(["status"], bin_dir=BIN_DIR) == 0
    assert cli.pr.main(["--no-queue", "comments", "--fix"], bin_dir=BIN_DIR) == 0
    monkeypatch.setenv(cli.queue_gate.NO_QUEUE_ENV, "1")
    assert cli.pr.main(["comments", "--fix"], bin_dir=BIN_DIR) == 0
    monkeypatch.delenv(cli.queue_gate.NO_QUEUE_ENV, raising=False)
    monkeypatch.setenv(otto_queue.ticket.ENTRY_ENV, "7")
    assert cli.pr.main(["comments", "--fix"], bin_dir=BIN_DIR) == 0
    monkeypatch.delenv(otto_queue.ticket.ENTRY_ENV, raising=False)
    monkeypatch.setenv(QUEUED_MARKER, "1")
    assert cli.pr.main(["comments", "--fix"], bin_dir=BIN_DIR) == 0
    assert len(dispatched) == 5


def test_pr_review_help_never_queues(monkeypatch, capsys):
    monkeypatch.setattr(otto_queue.ticket, "run_ticket",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ran")))
    monkeypatch.setattr(
        cli.pr, "_lock_resolve_and_dispatch",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("dispatched")),
    )
    assert cli.pr.main(["review", "--help"], bin_dir=BIN_DIR) == 0
    assert "--self" in capsys.readouterr().out


def test_pr_help_documents_no_queue(capsys):
    assert cli.pr.main(["--help"], bin_dir=BIN_DIR) == 0
    assert "--no-queue" in capsys.readouterr().out


def test_ci_check_gates_only_when_it_is_the_process(monkeypatch):
    seen = []

    def run(request, _policy, **_kw):
        seen.append(request.argv)
        return TicketResult(0, TicketOutcome.RAN, 1)

    monkeypatch.setattr(otto_queue.ticket, "run_ticket", run)

    class Entered(Exception):
        pass

    monkeypatch.setattr("core.publishing.run", lambda **_k: (_ for _ in ()).throw(Entered))
    assert cli.ci_check.main(["--fix"]) == 0
    assert seen and seen[0][1] == os.path.abspath(sys.argv[0])
    seen.clear()
    with pytest.raises(Entered):
        cli.ci_check.main(["--fix"], install_signal_handler=False)
    with pytest.raises(Entered):
        cli.ci_check.main([])
    assert seen == []


def test_review_shim_gates_only_when_it_is_the_process(monkeypatch):
    seen = []

    def run(request, _policy, **_kw):
        seen.append(request.argv)
        return TicketResult(0, TicketOutcome.RAN, 1)

    monkeypatch.setattr(otto_queue.ticket, "run_ticket", run)
    monkeypatch.setattr(cli.review_entry, "_main_body", lambda *_a, **_k: 99)
    assert cli.review_entry.main(["--self", "--fix"]) == 0
    assert seen and seen[0][1] == os.path.abspath(sys.argv[0])
    seen.clear()
    assert cli.review_entry.main(
        ["--self", "--fix"], install_signal_handler=False) == 99
    assert seen == []
    assert cli.review_entry.main(["--version"]) == 0
    assert seen == []
