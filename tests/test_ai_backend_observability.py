"""Tests for machine-readable output from the prompt and fix entry points.

Only invoke_agent asked for --output-format, so prompt() and invoke_fix() produced
nothing parseable and every pr-rebase and review-threads call went unmeasured.
"""

import ast
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

AI_DIR = Path(__file__).resolve().parent.parent / "ai"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend
import ai_backend_claude
import ai_usage

RESULT_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "the answer",
    "total_cost_usd": 0.42,
    "duration_ms": 1800,
    "modelUsage": {
        "claude-sonnet-4-6": {
            "inputTokens": 12, "outputTokens": 340,
            "cacheReadInputTokens": 9000, "cacheCreationInputTokens": 100,
            "costUSD": 0.42,
        },
    },
}


class TestBuildPromptCmd:
    def test_requests_json_output(self):
        cmd = ai_backend_claude._build_prompt_cmd()
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"

    def test_output_format_requires_print_flag(self):
        """--output-format only works with --print; -p must already be present."""
        cmd = ai_backend_claude._build_prompt_cmd()
        assert "-p" in cmd

    def test_model_still_passed(self):
        cmd = ai_backend_claude._build_prompt_cmd(model="claude-opus-4-6")
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"


class TestBuildFixCmd:
    def test_requests_stream_json_output(self):
        cmd = ai_backend_claude._build_fix_cmd(ai_backend.AgentInvocation(prompt="p"))
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"


class TestPromptParsesEnvelope:
    def _run(self, monkeypatch, tmp_path, stdout, returncode=0):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        return ai_backend_claude.prompt("hi", cwd=str(tmp_path))

    def test_returns_result_text_not_raw_json(self, monkeypatch, tmp_path):
        text, code, usage = self._run(monkeypatch, tmp_path, json.dumps(RESULT_ENVELOPE))
        assert text == "the answer"
        assert code == 0

    def test_extracts_usage(self, monkeypatch, tmp_path):
        _, _, usage = self._run(monkeypatch, tmp_path, json.dumps(RESULT_ENVELOPE))
        assert usage.cost == pytest.approx(0.42)
        assert usage.input_tokens == 12
        assert usage.cache_read_tokens == 9000
        assert usage.cost_by_model == pytest.approx({"claude-sonnet-4-6": 0.42})

    def test_non_json_stdout_falls_back_to_raw(self, monkeypatch, tmp_path):
        """A CLI output change must degrade to today's behavior, not break callers."""
        text, code, usage = self._run(monkeypatch, tmp_path, "plain text reply")
        assert text == "plain text reply"
        assert code == 0
        assert usage is None

    def test_envelope_without_result_key_falls_back_to_raw(self, monkeypatch, tmp_path):
        payload = json.dumps({"type": "result", "total_cost_usd": 0.1})
        text, _, usage = self._run(monkeypatch, tmp_path, payload)
        assert text == payload
        assert usage.cost == pytest.approx(0.1)

    def test_failure_exit_code_preserved(self, monkeypatch, tmp_path):
        _, code, _ = self._run(monkeypatch, tmp_path, "", returncode=2)
        assert code == 2


class TestPromptRecordsToLedger:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        d = tmp_path / "usage"
        monkeypatch.setattr(ai_usage, "LEDGER_DIR", d)
        monkeypatch.setattr(ai_usage, "_warned", False)
        return d

    def test_prompt_records_usage(self, ledger, monkeypatch, tmp_path):
        mod = types.SimpleNamespace(
            prompt=lambda text, **kw: (
                "answer", 0,
                ai_usage.SessionUsage(cost=0.42, input_tokens=12, cache_read_tokens=9000),
            ),
        )
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        text, code = ai_backend.prompt(
            "hi", cwd=str(tmp_path), task="conflict-resolve",
        )
        assert (text, code) == ("answer", 0)
        rec = json.loads(next(ledger.glob("*.jsonl")).read_text().strip())
        assert rec["entry_point"] == "prompt"
        assert rec["task"] == "conflict-resolve"
        assert rec["cost"] == pytest.approx(0.42)
        assert rec["cache_read_tokens"] == 9000

    def test_unmeasured_prompt_records_nothing(self, ledger, monkeypatch, tmp_path):
        """A zero row reads as a free call; an absent row reads as unmeasured."""
        mod = types.SimpleNamespace(prompt=lambda text, **kw: ("answer", 0, None))
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        assert ai_backend.prompt("hi", cwd=str(tmp_path)) == ("answer", 0)
        assert list(ledger.glob("*.jsonl")) == []

    def test_agent_session_log_without_result_records_nothing(
        self, ledger, monkeypatch, tmp_path,
    ):
        """An agent that died before its result record is unmeasured, not free."""
        session_log = tmp_path / "session.jsonl"
        session_log.write_text('{"type":"assistant"}\n')
        mod = types.SimpleNamespace(invoke_agent=lambda inv: 1)
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        ai_backend.invoke_agent(ai_backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(session_log),
        ))
        assert list(ledger.glob("*.jsonl")) == []

    def test_backend_returning_pair_still_works(self, ledger, monkeypatch, tmp_path):
        """A backend that has not adopted the usage triple must not crash dispatch."""
        mod = types.SimpleNamespace(prompt=lambda text, **kw: ("answer", 0))
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        assert ai_backend.prompt("hi", cwd=str(tmp_path)) == ("answer", 0)
        assert list(ledger.glob("*.jsonl")) == []


class TestFixWritesSessionLog:
    def _fake_proc(self, monkeypatch, lines, returncode=0):
        class FakeProc:
            def __init__(self):
                self.stdout = iter(lines)
                self.stdin = types.SimpleNamespace(write=lambda s: None, close=lambda: None)
                self.returncode = returncode

            def wait(self):
                return self.returncode

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: FakeProc())

    def test_writes_session_log(self, monkeypatch, tmp_path, capsys):
        log = tmp_path / "fix.jsonl"
        lines = [json.dumps(RESULT_ENVELOPE) + "\n"]
        self._fake_proc(monkeypatch, lines)
        ai_backend_claude.invoke_fix(ai_backend.AgentInvocation(
            prompt="p", session_log=str(log),
        ))
        assert ai_usage.parse_session_log(str(log)).cost == pytest.approx(0.42)

    def test_no_session_log_path_does_not_crash(self, monkeypatch, capsys):
        self._fake_proc(monkeypatch, [json.dumps(RESULT_ENVELOPE) + "\n"])
        assert ai_backend_claude.invoke_fix(ai_backend.AgentInvocation(prompt="p")) == 0

    def test_streams_assistant_text_not_raw_json(self, monkeypatch, capsys):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "patching the file"}]},
        }
        self._fake_proc(monkeypatch, [json.dumps(event) + "\n"])
        ai_backend_claude.invoke_fix(ai_backend.AgentInvocation(prompt="p"))
        err = capsys.readouterr().err
        assert "patching the file" in err
        assert '"type"' not in err

    def test_streams_tool_labels(self, monkeypatch, capsys):
        event = {
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/main.go"}},
            ]},
        }
        self._fake_proc(monkeypatch, [json.dumps(event) + "\n"])
        ai_backend_claude.invoke_fix(ai_backend.AgentInvocation(prompt="p"))
        assert "Read main.go" in capsys.readouterr().err


def _is_python_source(path: Path) -> bool:
    """A .py file, or an extensionless bin script with a python3 shebang."""
    if path.suffix == ".py":
        return True
    if not path.is_file() or path.suffix:
        return False
    try:
        return "python3" in path.read_text().split("\n", 1)[0]
    except (UnicodeDecodeError, OSError):
        return False


def _ai_sources() -> list[Path]:
    candidates = list((AI_DIR / "lib").glob("*.py"))
    candidates.extend((AI_DIR / "claude" / "bin").iterdir())
    return sorted(p for p in candidates if _is_python_source(p))


def _backend_calls(tree: ast.Module, names: frozenset[str]):
    """Yield every ``ai_backend.<name>(...)`` Call node in a parsed module."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in names:
            continue
        if isinstance(fn.value, ast.Name) and fn.value.id == "ai_backend":
            yield node


_FIX_ONLY = frozenset({"invoke_fix"})
_PROMPT_ONLY = frozenset({"prompt"})


def _invoke_fix_calls(tree: ast.Module):
    return _backend_calls(tree, _FIX_ONLY)


def _cwd_bearing_nodes(tree: ast.Module):
    """Yield every node that must name a directory for the backend to run in.

    Two shapes, checked where the directory is chosen rather than where it is
    used: a stateless ``ai_backend.prompt(...)`` call, and any
    ``AgentInvocation(...)`` construction. Checking constructions rather than
    ``invoke_*`` call sites is what makes the forwarding wrappers in
    review_agent.py — which take an already-built ``inv`` — correctly pass,
    while still covering the invocation built in review_pipeline.invocation()
    and handed to them.
    """
    yield from _backend_calls(tree, _PROMPT_ONLY)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_agent_invocation(node):
            yield node


def _scan_call_sites(source: Path) -> tuple[int, list[str]]:
    """Return (call count, "name:line" for each call missing a session_log)."""
    tree = ast.parse(source.read_text(), filename=str(source))
    calls = list(_invoke_fix_calls(tree))
    missing = [
        f"  - {source.name}:{call.lineno}"
        for call in calls if not _has_session_log(call)
    ]
    return len(calls), missing


def _scan_cwd_call_sites(source: Path) -> tuple[int, list[str]]:
    """Return (node count, "name:line" for each node missing a cwd)."""
    tree = ast.parse(source.read_text(), filename=str(source))
    nodes = list(_cwd_bearing_nodes(tree))
    missing = [
        f"  - {source.name}:{node.lineno}"
        for node in nodes if not _has_kwarg(node, "cwd")
    ]
    return len(nodes), missing


def _is_agent_invocation(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    return name == "AgentInvocation"


def _invocation_node(call: ast.Call) -> ast.Call:
    """The node whose keywords carry the invocation's settings.

    Call sites build an AgentInvocation inline and pass it positionally, so
    session_log sits on that constructor rather than on invoke_fix itself.
    """
    return next((a for a in call.args if _is_agent_invocation(a)), call)


def _has_kwarg(call: ast.Call, name: str) -> bool:
    """Whether the call supplies `name` as a non-empty-literal keyword.

    Reads through to the inline AgentInvocation when there is one, so the same
    check covers `prompt(cwd=...)` and `invoke_fix(AgentInvocation(cwd=...))`.
    """
    for kw in _invocation_node(call).keywords:
        if kw.arg != name:
            continue
        empty = isinstance(kw.value, ast.Constant) and not kw.value.value
        return not empty
    return False


def _has_session_log(call: ast.Call) -> bool:
    return _has_kwarg(call, "session_log")


class TestFixCallSitesPassSessionLog:
    """Every ai_backend.invoke_fix() call site must supply a session_log.

    session_log defaults to "" and _usage_from_log("") short-circuits to None, so an
    omission writes zero ledger rows while the call still succeeds and still carries
    its task= label. The gap is invisible until someone audits spend, which is how
    `pr ci --fix` and `pr comments --fix` went unmeasured.
    """

    def test_all_call_sites_supply_a_session_log(self):
        offenders: list[str] = []
        found = 0
        for count, missing in [_scan_call_sites(s) for s in _ai_sources()]:
            found += count
            offenders.extend(missing)

        assert not offenders, (
            "invoke_fix() call sites missing a non-empty session_log "
            "(these produce no usage ledger records):\n" + "\n".join(offenders)
        )
        # Guard the scanner itself: a matcher that silently matches nothing
        # would make this test pass forever.
        assert found >= 3, f"expected to find invoke_fix call sites, found {found}"

    @pytest.mark.parametrize("src,expected", [
        ('ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt=p, '
         'session_log=str(d / "s.jsonl")))', True),
        ('ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt=p, task="ci-fix"))',
         False),
        ('ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt=p, session_log=""))',
         False),
        ('ai_backend.invoke_fix(AgentInvocation(prompt=p, session_log=log))', True),
    ])
    def test_scanner_detects_missing_session_log(self, src, expected):
        call = next(_invoke_fix_calls(ast.parse(src)))
        assert _has_session_log(call) is expected


class TestAgentCallSitesPassCwd:
    """Every spawning ai_backend entry point must be told which directory to run in.

    A backend CLI with no cwd inherits the interpreter's. On a bare repo with
    sibling worktrees that is another live branch, and the agent has write access
    to it: a `pr rebase --fix` launched from worktree B against worktree A ran its
    tests in B and truncated B's copy of pr-rebase from ~1800 lines to 431.

    add_dirs does not substitute for this. It appends --add-dir, which widens the
    allowed set; there is no flag that narrows it back.
    """

    def test_all_call_sites_supply_a_cwd(self):
        offenders: list[str] = []
        found = 0
        for count, missing in [_scan_cwd_call_sites(s) for s in _ai_sources()]:
            found += count
            offenders.extend(missing)

        assert not offenders, (
            "ai_backend call sites missing a cwd (these run in whichever "
            "directory the process was launched from):\n" + "\n".join(offenders)
        )
        # Guard the scanner itself: a matcher that silently matches nothing
        # would make this test pass forever.
        assert found >= 8, f"expected to find spawning call sites, found {found}"

    @pytest.mark.parametrize("src,expected", [
        ('ai_backend.prompt(text, cwd=cwd, task="conflict-resolve")', True),
        ('ai_backend.prompt(text, task="conflict-resolve")', False),
        ('ai_backend.prompt(text, cwd="", task="t")', False),
        ('ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt=p, cwd=str(wt)))',
         True),
        ('ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt=p, add_dirs=[d]))',
         False),
        ('ai_backend.invoke_agent(AgentInvocation(prompt=p, cwd=d))', True),
    ])
    def test_scanner_detects_missing_cwd(self, src, expected):
        node = next(_cwd_bearing_nodes(ast.parse(src)))
        assert _has_kwarg(node, "cwd") is expected

    def test_scanner_skips_wrappers_that_forward_a_built_invocation(self):
        """review_agent's retry wrappers take an `inv` whose cwd was set elsewhere."""
        tree = ast.parse("rc = ai_backend.invoke_agent(inv)")
        assert list(_cwd_bearing_nodes(tree)) == []
