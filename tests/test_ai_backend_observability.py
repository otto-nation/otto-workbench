"""Tests for machine-readable output from the prompt and fix entry points.

Only invoke_agent asked for --output-format, so prompt() and invoke_fix() produced
nothing parseable and every pr-rebase and review-threads call went unmeasured.

The AST guards in the second half share that subject from the other side: what a
call site has to supply for the record to be worth anything, and — since one
owner is cheaper to police than thirty — which module is allowed to make the
call at all.
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

from agent import backend as ai_backend
from agent import backend_claude as ai_backend_claude
from agent import usage as ai_usage

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

    def test_a_thinking_level_is_accepted_and_dropped(self, monkeypatch, tmp_path):
        """The CLI has no flag for it, and dispatch passes one to both backends.

        Raising here instead would make the prompt shape unrunnable on Claude
        the moment an operator set a thinking level for one of its phases.
        """
        seen = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                            subprocess.CompletedProcess(cmd, 0, "answer", ""))
        text, code, _ = ai_backend_claude.prompt(
            "ask", cwd=str(tmp_path), thinking="high", provider="bedrock",
        )
        assert (text, code) == ("answer", 0)
        assert "--thinking" not in seen[0]
        assert "--provider" not in seen[0]


class TestBuildFixCmd:
    def test_requests_stream_json_output(self):
        cmd = ai_backend_claude._build_fix_cmd(ai_backend.AgentInvocation(prompt="p"))
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"

    def test_denies_gh_so_the_agent_cannot_write_to_github(self):
        """Every outward write waits for --post; `gh` would route around it."""
        cmd = ai_backend_claude._build_fix_cmd(ai_backend.AgentInvocation(prompt="p"))
        assert "--disallowedTools" in cmd
        assert cmd[cmd.index("--disallowedTools") + 1] == "Bash(gh:*)"

    def test_the_review_agent_keeps_gh(self):
        """Only the fix pass is barred — a review agent reads the PR with it."""
        cmd = ai_backend_claude._build_agent_cmd(ai_backend.AgentInvocation(prompt="p"))
        assert "--disallowedTools" not in cmd


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
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(ai_usage, "_warned", False)
        return tmp_path / ai_usage.LEDGER_DIRNAME

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
    """Exercises the claude backend directly, beneath ai_backend's cwd guard.

    The invocations here deliberately carry no cwd: these are backend-module tests
    with Popen faked, so the value never reaches a syscall, and routing them through
    ai_backend.invoke_fix would test the guard instead of the session-log writing.
    """

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


def _lib_sources() -> list[Path]:
    """All .py files across the lib packages, excluding package __init__ files."""
    return [p for p in (AI_DIR / "lib").glob("*/*.py") if p.stem != "__init__"]


def _ai_sources() -> list[Path]:
    candidates = list(_lib_sources())
    candidates.extend((AI_DIR / "bin").iterdir())
    return sorted(p for p in candidates if _is_python_source(p))


def test_lib_sources_discovered():
    """A flat glob after the package move finds nothing; the bin scripts merged in
    above would keep every call-site scan non-empty while all 101 lib modules
    silently drop out of it."""
    assert len(_lib_sources()) > 90


def _backend_bindings(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """Local names that reach ai_backend: module aliases, and `from`-imported members.

    `ai_backend` seeds the set because `import ai_backend` binds it and the scanner's
    own unit tests parse bare snippets with no import line. Real call sites reach the
    module through its package home, `agent.backend`, after the layer move — checked
    alongside the flat form rather than in place of it, since the scanner's own tests
    still parse bare `ai_backend` snippets.
    """
    modules = {"ai_backend"}
    direct: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                a.asname or a.name for a in node.names if a.name == "ai_backend"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "ai_backend":
            direct.update((a.asname or a.name, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module == "agent":
            modules.update(
                a.asname or a.name for a in node.names if a.name == "backend"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "agent.backend":
            direct.update((a.asname or a.name, a.name) for a in node.names)
    return modules, direct


def _reaches_backend(
    fn: ast.expr, names: frozenset[str], modules: set[str], direct: dict[str, str],
) -> bool:
    """Whether a callee resolves to one of ai_backend's `names`, alias or not."""
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return fn.attr in names and fn.value.id in modules
    if isinstance(fn, ast.Name):
        return direct.get(fn.id) in names
    return False


def _backend_calls(tree: ast.Module, names: frozenset[str]):
    """Yield every call reaching ``ai_backend.<name>(...)`` in a parsed module.

    Import aliases are resolved rather than assumed. Matching the literal name
    would let `import ai_backend as ab` hide `ab.prompt(...)` from the scan, and
    the guard would keep passing while an unscoped call site shipped — the exact
    failure it exists to catch.
    """
    modules, direct = _backend_bindings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _reaches_backend(
            node.func, names, modules, direct,
        ):
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
        # would make this test pass forever. The population is small because
        # `agent_invoke` owns every invocation the workbench makes — what stops
        # a sixth appearing elsewhere is `TestOneOwnerForBackendCalls`.
        assert found >= 5, f"expected to find spawning call sites, found {found}"

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

    @pytest.mark.parametrize("src", [
        "import ai_backend as ab\nab.prompt(text, task='t')",
        "from ai_backend import prompt\nprompt(text, task='t')",
        "from ai_backend import prompt as ask\nask(text, task='t')",
    ])
    def test_scanner_resolves_import_aliases(self, src):
        """An alias must not hide a call site — that would silence the guard."""
        node = next(_cwd_bearing_nodes(ast.parse(src)))
        assert _has_kwarg(node, "cwd") is False


_SPAWNING = frozenset({"prompt", "invoke_agent", "invoke_fix"})

# The owner, and the two modules deliberately left outside it. An eval scores a
# named model against a fixed corpus, so resolving the phase would let an
# operator's config decide what the run measures — the eval would report their
# settings rather than the model under test.
_MAY_REACH_THE_BACKEND = {
    "invoke.py",
    "scoring_cifix.py",
    "scoring_skill.py",
}


def _unowned_backend_calls(source: Path) -> list[str]:
    """"name:line" for every call that reaches a spawning entry point directly."""
    tree = ast.parse(source.read_text(), filename=str(source))
    return [
        f"  - {source.name}:{call.lineno}"
        for call in _backend_calls(tree, _SPAWNING)
    ]


class TestOneOwnerForBackendCalls:
    """Only ``agent_invoke`` reaches the three entry points that spend money.

    Each call site used to assemble its own invocation, and each did it slightly
    differently: a hardcoded model here, a missing retry ceiling there, a ledger
    label spelled a third way. Every guard in this file exists because one of
    them forgot something the others remembered — so the durable fix is that
    there is one place left to forget it in.

    The list of exceptions is short and hand-written on purpose. Unlike the
    conventions elsewhere in this repo, a new entry here should cost a deliberate
    edit: adding one is a decision to opt a module out of phase resolution, the
    usage ledger, and the retry guard at once.
    """

    def test_no_module_but_the_owner_reaches_the_backend(self):
        offenders = [
            line
            for source in _ai_sources()
            if source.name not in _MAY_REACH_THE_BACKEND
            for line in _unowned_backend_calls(source)
        ]
        assert not offenders, (
            "ai_backend entry points reached outside agent_invoke (these skip "
            "phase resolution, the usage ledger and the retry guard) — call "
            "agent_invoke.run_prompt/run_agent/run_fix instead:\n"
            + "\n".join(offenders)
        )

    def test_the_owner_reaches_all_three(self):
        """A shape whose runner stopped calling the backend would pass silently."""
        owner = AI_DIR / "lib" / "agent" / "invoke.py"
        tree = ast.parse(owner.read_text(), filename=str(owner))
        reached = {
            call.func.attr for call in _backend_calls(tree, _SPAWNING)
        }
        assert reached == set(_SPAWNING)

    @pytest.mark.parametrize("src", [
        "ai_backend.prompt(text, cwd=d)",
        "import ai_backend as ab\nab.invoke_agent(inv)",
        "from ai_backend import invoke_fix\ninvoke_fix(inv)",
    ])
    def test_scanner_catches_every_spelling(self, src):
        assert list(_backend_calls(ast.parse(src), _SPAWNING))

    def test_a_call_through_the_owner_is_not_a_backend_call(self):
        tree = ast.parse("agent_invoke.run_prompt(Phase.DESCRIBE, text, cwd=d)")
        assert list(_backend_calls(tree, _SPAWNING)) == []
