"""Pi refuses an unlisted model id instead of rewriting it onto a parent."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi
import agent.pi_binary
import agent.pi_models
from ai_backend_pi_rpc_test import _RefusingProc
from conftest import TEST_PI_ARGV as PI


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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)
        with pytest.raises(agent.pi_models.ModelNotServed) as caught:
            agent.pi_models.require_listed_model(UNLISTED, PROVIDER, pi=PI)
        msg = str(caught.value)
        assert UNLISTED in msg
        assert "WORKBENCH_AI_MODEL" in msg
        assert "AI_SONNET_MODEL" not in msg
        assert PROVIDER in msg
        assert LISTED in msg
        assert "--model" in msg
        assert "models.<tier>" in msg

    def test_a_listed_id_passes_untouched(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
        assert agent.pi_models.require_listed_model(LISTED, PROVIDER, pi=PI) is None

    def test_an_empty_catalogue_skips_validation(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: "")
        assert agent.pi_models.require_listed_model(UNLISTED, PROVIDER, pi=PI) is None


class TestNotServedMessage:
    def test_names_the_model_and_the_override(self, monkeypatch):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
        monkeypatch.setenv("AI_SONNET_MODEL", UNLISTED)
        msg = agent.pi_models.not_served_message(
            UNLISTED,
            'Warning: No models match pattern "google-vertex-claude/claude-sonnet-5-5"\n'
            'Error: Model "claude-sonnet-5-5" not found. Use --list-models to see available models.\n',
            PROVIDER, pi=PI,
        )
        assert msg is not None
        assert UNLISTED in msg
        assert "WORKBENCH_AI_MODEL" in msg
        assert "AI_SONNET_MODEL" not in msg
        assert LISTED in msg
        assert "--model" in msg
        assert "models.<tier>" in msg

    def test_other_stderr_is_not_this_failure(self):
        assert agent.pi_models.not_served_message(
            UNLISTED, "No API key found for amazon-bedrock.\n", PROVIDER, pi=PI,
        ) is None

    def test_an_unrelated_not_found_is_not_this_failure(self):
        assert agent.pi_models.not_served_message(
            UNLISTED, "Error: extension file not found: review.ts\n", PROVIDER, pi=PI,
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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
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
        assert "WORKBENCH_AI_MODEL" in logged
        assert "AI_SONNET_MODEL" not in logged
        assert LISTED in logged
        assert '"is_error": true' in logged

    def test_prompt_passes_a_listed_id_untouched(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)
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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: "")

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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: "")
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


class TestOneBinaryForCatalogueAndSpawn:
    """The model is checked by the binary that will run it.

    Two pi versions serve different models: a catalogue read from whichever
    `pi` is first on PATH refused a model the chosen binary serves.
    """

    CHOSEN = "/opt/pi-1.2.0/bin/pi"

    @pytest.fixture(autouse=True)
    def _chosen(self, monkeypatch):
        self.resolved_for = []

        def resolve(cwd):
            self.resolved_for.append(cwd)
            return agent.pi_binary.PiBinary(
                self.CHOSEN, "1.2.0", agent.pi_binary.Source.MANAGED, "test",
            )

        monkeypatch.setattr(agent.pi_binary, "resolve", resolve)
        self.listed_by = []

        def catalogue(pi):
            self.listed_by.append(pi)
            return LISTING

        monkeypatch.setattr(agent.pi_models, "catalogue_text", catalogue)

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_agent_runs_check_and_spawn_the_chosen_binary(
        self, monkeypatch, tmp_path, entry_point,
    ):
        seen = []
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **_kw: seen.append(cmd) or _dead())
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
            model=LISTED, provider=PROVIDER,
        ))
        assert self.resolved_for == [str(tmp_path)]
        assert self.listed_by == [(self.CHOSEN,)]
        assert seen[0][0] == self.CHOSEN

    def test_a_prompt_checks_and_spawns_the_chosen_binary(self, monkeypatch, tmp_path):
        seen = []

        def fake_run(cmd, **_kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent.backend_pi.prompt("hi", cwd=str(tmp_path), model=LISTED, provider=PROVIDER)
        assert self.listed_by == [(self.CHOSEN,)]
        assert seen[0][0] == self.CHOSEN

    def test_the_catalogue_lists_with_the_binary_it_is_given(self, monkeypatch):
        import core.proc
        from conftest import _REAL_CATALOGUE_TEXT

        ran = []
        monkeypatch.setattr(core.proc, "run", lambda cmd, **_kw: ran.append(cmd) or core.proc.CmdResult(
            returncode=0, stdout=LISTING,
        ))
        _REAL_CATALOGUE_TEXT.cache_clear()
        try:
            assert _REAL_CATALOGUE_TEXT((self.CHOSEN,)) == LISTING
        finally:
            _REAL_CATALOGUE_TEXT.cache_clear()
        assert ran == [[self.CHOSEN, "--list-models"]]


class TestNoChoosablePi:
    @pytest.fixture(autouse=True)
    def _unresolvable(self, monkeypatch):
        def resolve(_cwd):
            raise agent.pi_binary.PiUnresolvable("cannot read a pi version from current-version")

        monkeypatch.setattr(agent.pi_binary, "resolve", resolve)

        def no_spawn(*_a, **_kw):
            raise AssertionError("spawned pi with no binary chosen")

        monkeypatch.setattr(subprocess, "Popen", no_spawn)
        monkeypatch.setattr(subprocess, "run", no_spawn)

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_agent_runs_fail_with_the_reason_in_the_session_log(
        self, tmp_path, entry_point,
    ):
        session_log = tmp_path / "s.jsonl"
        exit_code = getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(session_log),
            model=LISTED, provider=PROVIDER,
        ))
        assert exit_code != 0
        logged = session_log.read_text()
        assert "current-version" in logged
        assert '"is_error": true' in logged

    def test_a_prompt_fails_without_spawning(self, tmp_path, capsys):
        reply, exit_code, usage = agent.backend_pi.prompt(
            "hi", cwd=str(tmp_path), model=LISTED, provider=PROVIDER,
        )
        assert (reply, usage) == ("", None)
        assert exit_code != 0
        assert "cannot choose a pi binary" in capsys.readouterr().err


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
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: "")
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
        assert "models.<tier>" in text
        assert "WORKBENCH_AI_MODEL" in text
        assert "AI_SONNET_MODEL" not in text
        assert "stdin was already closed" not in text
        record = json.loads(text.splitlines()[0])
        assert record["success"] is False
        assert "not found" in record["error"].lower() or UNLISTED in record["error"]


class TestModelWithProvider:
    """Pi refuses --provider without --model; the workbench says so first, clearly."""

    def test_a_provider_without_a_model_is_refused_naming_the_keys(self):
        with pytest.raises(agent.pi_models.ModelNotConfigured) as caught:
            agent.pi_models.require_model_with_provider("xai", None)
        msg = str(caught.value)
        assert "xai" in msg
        assert "models.<tier>" in msg
        assert "WORKBENCH_AI_MODEL" in msg
        assert "--model" in msg

    def test_an_empty_model_counts_as_none(self):
        with pytest.raises(agent.pi_models.ModelNotConfigured):
            agent.pi_models.require_model_with_provider("xai", "")

    @pytest.mark.parametrize("provider, model", [(None, None), (None, "grok-4.7"), ("xai", "grok-4.7")])
    def test_other_combinations_pass(self, provider, model):
        assert agent.pi_models.require_model_with_provider(provider, model) is None

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_agent_and_fix_refuse_before_spawning(self, monkeypatch, tmp_path, entry_point):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)

        def fake_popen(*_a, **_kw):
            raise AssertionError("spawned pi with a provider and no model")

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        session_log = str(tmp_path / "s.jsonl")
        exit_code = getattr(agent.backend_pi, entry_point)(
            agent.backend_pi.AgentInvocation(
                prompt="p", cwd=str(tmp_path), session_log=session_log,
                model=None, provider=PROVIDER,
            )
        )
        assert exit_code != 0
        logged = Path(session_log).read_text()
        assert "models.<tier>" in logged
        assert '"is_error": true' in logged

    def test_prompt_refuses_before_spawning(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent.pi_models, "catalogue_text", lambda _pi: LISTING)

        def fake_run(*_a, **_kw):
            raise AssertionError("spawned pi with a provider and no model")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _reply, exit_code, usage = agent.backend_pi.prompt(
            "hi", cwd=str(tmp_path), model=None, provider=PROVIDER,
        )
        assert exit_code != 0
        assert usage is None

    def test_the_refusal_line_does_not_blame_pi(self):
        line = agent.backend_pi._refusal_line(agent.pi_models.ModelNotConfigured("xai"))
        assert "pi refused" not in line
        assert "models.<tier>" in line
