"""Pi refuses an unlisted model id instead of rewriting it onto a parent."""

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

UNLISTED = "claude-sonnet-5-5"
LISTED = "claude-sonnet-5"
PROVIDER = "google-vertex-claude"


class _DeadStdin:
    def write(self, _data):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        pass

    def close(self):
        pass


def _dead():
    proc = _RefusingProc([])
    proc.stdin = _DeadStdin()
    return proc


class TestParseListedIds:
    def test_skips_the_header_and_other_providers(self):
        ids = agent.pi_models.parse_listed_ids(LISTING, PROVIDER)
        assert ids == frozenset({"claude-opus-5-5", LISTED})

    def test_an_unfiltered_parse_keeps_every_provider(self):
        ids = agent.pi_models.parse_listed_ids(LISTING)
        assert "gemini-2.5-flash" in ids
        assert LISTED in ids
        assert "xai/grok-4.6" in ids


class TestRequireListedModel:
    def test_an_unlisted_id_names_id_source_and_suggestions(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)
        with pytest.raises(agent.pi_models.ModelNotServed) as caught:
            agent.pi_models.require_listed_model(UNLISTED, PROVIDER)
        msg = str(caught.value)
        assert UNLISTED in msg
        assert "AI_SONNET_MODEL" in msg
        assert PROVIDER in msg
        assert LISTED in msg
        assert "--model" in msg
        assert "agent.model" in msg

    def test_a_listed_id_passes_untouched(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        assert agent.pi_models.require_listed_model(LISTED, PROVIDER) is None

    def test_an_empty_catalogue_skips_validation(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: "")
        assert agent.pi_models.require_listed_model(UNLISTED, PROVIDER) is None


class TestNotServedMessage:
    def test_names_the_model_and_the_override(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)
        msg = agent.pi_models.not_served_message(
            UNLISTED,
            'Warning: No models match pattern "google-vertex-claude/claude-sonnet-5-5"\n'
            'Error: Model "claude-sonnet-5-5" not found. Use --list-models to see available models.\n',
            PROVIDER,
        )
        assert msg is not None
        assert UNLISTED in msg
        assert "AI_SONNET_MODEL" in msg
        assert LISTED in msg
        assert "--model" in msg
        assert "agent.model" in msg

    def test_other_stderr_is_not_this_failure(self):
        assert agent.pi_models.not_served_message(
            UNLISTED, "No API key found for amazon-bedrock.\n", PROVIDER,
        ) is None

    def test_an_unrelated_not_found_is_not_this_failure(self):
        assert agent.pi_models.not_served_message(
            UNLISTED, "Error: extension file not found: review.ts\n", PROVIDER,
        ) is None


class TestDefaultProvider:
    def test_falls_back_when_the_settings_file_is_unreadable(self, monkeypatch):
        monkeypatch.setenv("WORKBENCH_PI_SETTINGS", "/nonexistent/settings.json")
        assert agent.pi_models.default_provider() == agent.pi_models.DEFAULT_PROVIDER

    def test_reads_the_overridden_settings_file(self, monkeypatch, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"defaultProvider": "custom-provider"}))
        monkeypatch.setenv("WORKBENCH_PI_SETTINGS", str(settings))
        assert agent.pi_models.default_provider() == "custom-provider"


class TestInvokeUsesTheConfiguredId:
    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_a_listed_id_is_passed_untouched(
        self, monkeypatch, tmp_path, entry_point,
    ):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        seen = []

        def fake_popen(cmd, **_kw):
            seen.append(cmd)
            return _dead()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
            model=LISTED, provider=PROVIDER,
        ))
        assert seen, "pi was not spawned"
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == LISTED

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_an_unlisted_id_fails_without_spawning_and_logs_the_session(
        self, monkeypatch, tmp_path, entry_point,
    ):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)

        def fake_popen(*_a, **_kw):
            raise AssertionError("spawned pi with an unlisted model")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        session_log = str(tmp_path / "s.jsonl")
        exit_code = getattr(agent.backend_pi, entry_point)(
            agent.backend_pi.AgentInvocation(
                prompt="p", cwd=str(tmp_path), session_log=session_log,
                model=UNLISTED, provider=PROVIDER,
            )
        )
        assert exit_code != 0
        logged = Path(session_log).read_text()
        assert UNLISTED in logged
        assert "AI_SONNET_MODEL" in logged
        assert LISTED in logged
        assert '"is_error": true' in logged

    def test_prompt_passes_a_listed_id_untouched(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        seen = []

        def fake_run(cmd, **_kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent.backend_pi.prompt(
            "hi", cwd=str(tmp_path), model=LISTED, provider=PROVIDER,
        )
        assert seen, "pi was not spawned"
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == LISTED

    def test_prompt_fails_without_spawning_on_an_unlisted_id(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: LISTING)
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)

        def fake_run(*_a, **_kw):
            raise AssertionError("spawned pi with an unlisted model")

        monkeypatch.setattr(subprocess, "run", fake_run)
        reply, exit_code, usage = agent.backend_pi.prompt(
            "hi", cwd=str(tmp_path), model=UNLISTED, provider=PROVIDER,
        )
        assert exit_code != 0
        assert usage is None

    def test_prompt_surfaces_the_not_served_message_in_its_reply(
        self, monkeypatch, tmp_path,
    ):
        # catalogue is unreadable, so require_listed_model skips the pre-spawn
        # check and pi itself refuses the id over stderr.
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: "")

        def fake_run(cmd, **_kw):
            return subprocess.CompletedProcess(
                cmd, returncode=1, stdout="",
                stderr=f'Error: Model "{UNLISTED}" not found. Use --list-models '
                "to see available models.\n",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        reply, exit_code, usage = agent.backend_pi.prompt(
            "hi", cwd=str(tmp_path), model=UNLISTED, provider=PROVIDER,
        )
        assert exit_code != 0
        assert UNLISTED in reply

    def test_an_empty_catalogue_spawns_the_configured_id(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: "")
        seen = []

        def fake_popen(cmd, **_kw):
            seen.append(cmd)
            return _dead()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        agent.backend_pi.invoke_agent(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
            model=UNLISTED, provider=PROVIDER,
        ))
        assert seen, "pi was not spawned"
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == UNLISTED


class TestUndeliveredNamesTheModel:
    def test_model_not_found_stderr_replaces_the_stdin_symptom(
        self, monkeypatch, tmp_path,
    ):
        proc = _RefusingProc([])
        proc.stdin = _DeadStdin()
        proc.stderr = io.StringIO(
            'Warning: No models match pattern "google-vertex-claude/claude-sonnet-5-5"\n'
            'Error: Model "claude-sonnet-5-5" not found. Use --list-models '
            "to see available models.\n"
        )
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda: "")
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)
        log = tmp_path / "s.jsonl"
        agent.backend_pi.invoke_agent(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
            model=UNLISTED, provider=PROVIDER,
        ))
        text = log.read_text()
        assert UNLISTED in text
        assert "Model \"claude-sonnet-5-5\" not found" in text
        assert "--model" in text
        assert "agent.model" in text
        assert "AI_SONNET_MODEL" in text
        assert "stdin was already closed" not in text
        record = json.loads(text.splitlines()[0])
        assert record["success"] is False
        assert "not found" in record["error"].lower() or UNLISTED in record["error"]
