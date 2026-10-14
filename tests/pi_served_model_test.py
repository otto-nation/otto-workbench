"""Pi rewrites a requested model onto an id the configured provider lists."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi
import agent.pi_models
from ai_backend_pi_rpc_test import _RefusingProc


LISTING = """\
provider              model                               context  max-out  thinking  images
google-vertex         gemini-2.5-flash                    1.0M     65.5K    yes       yes
google-vertex-claude  claude-opus-5-5                     1M       128K     yes       yes
google-vertex-claude  claude-sonnet-5                     1M       128K     yes       yes
google-vertex-grok    xai/grok-4.6                        120K     32K      yes       no
"""


class TestParseListedIds:
    def test_skips_the_header_and_other_providers(self):
        ids = agent.pi_models.parse_listed_ids(LISTING, "google-vertex-claude")
        assert ids == frozenset({"claude-opus-5-5", "claude-sonnet-5"})

    def test_an_unfiltered_parse_keeps_every_provider(self):
        ids = agent.pi_models.parse_listed_ids(LISTING)
        assert "gemini-2.5-flash" in ids
        assert "claude-sonnet-5" in ids
        assert "xai/grok-4.6" in ids


class TestResolveServedModel:
    def test_an_exact_hit_is_kept(self):
        listed = frozenset({"claude-sonnet-5-5", "claude-sonnet-5"})
        assert agent.pi_models.resolve_served_model(
            "claude-sonnet-5-5", listed,
        ) == "claude-sonnet-5-5"

    def test_a_missing_id_falls_back_to_its_listed_parent(self):
        """Vertex lists claude-sonnet-5 while AI_SONNET_MODEL names 5-5."""
        listed = frozenset({"claude-sonnet-5", "claude-opus-5-5"})
        assert agent.pi_models.resolve_served_model(
            "claude-sonnet-5-5", listed,
        ) == "claude-sonnet-5"

    def test_a_parent_that_is_not_listed_is_not_invented(self):
        listed = frozenset({"claude-opus-5-5"})
        assert agent.pi_models.resolve_served_model(
            "claude-sonnet-5-5", listed,
        ) == "claude-sonnet-5-5"

    def test_an_empty_catalogue_does_not_rewrite(self):
        assert agent.pi_models.resolve_served_model(
            "claude-sonnet-5-5", frozenset(),
        ) == "claude-sonnet-5-5"

    def test_one_strip_only(self):
        # `claude-sonnet-4-5` → `claude-sonnet-4`, not `claude-sonnet`.
        listed = frozenset({"claude-sonnet"})
        assert agent.pi_models.resolve_served_model(
            "claude-sonnet-4-5", listed,
        ) == "claude-sonnet-4-5"


class TestNotServedMessage:
    def test_names_the_model_and_the_override(self):
        msg = agent.pi_models.not_served_message(
            "claude-sonnet-5-5",
            'Warning: No models match pattern "google-vertex-claude/claude-sonnet-5-5"\n'
            'Error: Model "claude-sonnet-5-5" not found. Use --list-models to see available models.\n',
        )
        assert msg is not None
        assert "claude-sonnet-5-5" in msg
        assert "--model" in msg
        assert "agent.model" in msg

    def test_other_stderr_is_not_this_failure(self):
        assert agent.pi_models.not_served_message(
            "claude-sonnet-5-5", "No API key found for amazon-bedrock.\n",
        ) is None


class TestServedModelUsesTheCatalogue:
    def test_rewrites_against_the_provider_listing(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "_catalogue_text", lambda: LISTING)
        monkeypatch.setattr(
            agent.pi_models, "default_provider", lambda: "google-vertex-claude",
        )
        assert agent.pi_models.served_model(
            "claude-sonnet-5-5", "google-vertex-claude",
        ) == "claude-sonnet-5"

    def test_a_bare_alias_does_not_consult_the_catalogue(self, monkeypatch):
        def _must_not_run():
            raise AssertionError("consulted pi --list-models for a bare alias")

        monkeypatch.setattr(agent.pi_models, "_catalogue_text", _must_not_run)
        assert agent.pi_models.served_model("sonnet") == "sonnet"


class TestInvokeUsesTheServedId:
    def _dead(self):
        proc = _RefusingProc([])
        proc.stdin = TestUndeliveredNamesTheModel._DeadStdin()
        return proc

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_invoke_passes_the_parent_id(self, monkeypatch, tmp_path, entry_point):
        monkeypatch.setattr(
            agent.backend_pi, "served_model",
            lambda model, provider=None: "claude-sonnet-5",
        )
        seen = []

        def fake_popen(cmd, **_kw):
            seen.append(cmd)
            return self._dead()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
            model="claude-sonnet-5-5",
        ))
        assert seen, "pi was not spawned"
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"

    def test_prompt_passes_the_parent_id(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            agent.backend_pi, "served_model",
            lambda model, provider=None: "claude-sonnet-5",
        )
        seen = []

        def fake_run(cmd, **_kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent.backend_pi.prompt("hi", cwd=str(tmp_path), model="claude-sonnet-5-5")
        assert seen, "pi was not spawned"
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"


class TestUndeliveredNamesTheModel:
    class _DeadStdin:
        def write(self, _data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

        def close(self):
            pass

    def test_model_not_found_stderr_replaces_the_stdin_symptom(
        self, monkeypatch, tmp_path,
    ):
        proc = _RefusingProc([])
        proc.stdin = self._DeadStdin()
        proc.stderr = io.StringIO(
            'Warning: No models match pattern "google-vertex-claude/claude-sonnet-5-5"\n'
            'Error: Model "claude-sonnet-5-5" not found. Use --list-models '
            "to see available models.\n"
        )
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        monkeypatch.setattr(
            agent.backend_pi, "served_model", lambda model, provider=None: model,
        )
        log = tmp_path / "s.jsonl"
        agent.backend_pi.invoke_agent(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
            model="claude-sonnet-5-5",
        ))
        text = log.read_text()
        assert "claude-sonnet-5-5" in text
        assert "--model" in text
        assert "agent.model" in text
        assert "stdin was already closed" not in text
        record = json.loads(text.splitlines()[0])
        assert record["success"] is False
