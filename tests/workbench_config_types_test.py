"""How a config write types the value it was given, and how a read types it back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.workbench_config
import config.workbench_config_report
import config.workbench_config_write

from workbench_config_support import needs_yaml, roots, _write


# ── The value guard ─────────────────────────────────────────────────────────
#
# The same loss as the key guard catches, one level down. Every value arrives
# as a string off a command line, and `serde` replaces a scalar it cannot
# convert with the field's default — so a boolean written as `"true"` is a
# write that reports success and leaves the setting off.


def test_a_boolean_key_is_written_as_a_boolean(roots):
    config_root, _ = roots
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "true")
    assert "ssh_over_443: true" in (config_root / config.workbench_config.CONFIG_NAME).read_text()
    assert config.workbench_config.load_config().github.ssh_over_443 is True


def test_a_boolean_key_round_trips_back_to_false(roots):
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "true")
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "false")
    assert config.workbench_config.load_config().github.ssh_over_443 is False


def test_a_boolean_key_refuses_a_value_that_is_neither(roots):
    """`bool("yes")` is True and so is `bool("no")`, which is why nothing guesses."""
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "yes")
    assert config.workbench_config.GITHUB_SSH_443_KEY in str(exc.value)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_a_refused_value_is_a_config_error_too(roots):
    """A caller that only handles the general failure still catches this one."""
    assert issubclass(config.workbench_config.ConfigValueError, config.workbench_config.ConfigError)
    with pytest.raises(config.workbench_config.ConfigError):
        config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "sideways")


def test_a_refused_value_is_not_a_refused_key(roots):
    """The two failures owe the caller different advice, so they are different types."""
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "sideways")
    assert not issubclass(config.workbench_config.ConfigValueError, config.workbench_config.ConfigKeyError)


def test_a_string_key_is_written_as_the_string_it_was_given(roots):
    """A value that looks like a bool under a string field stays a string."""
    _, project = roots
    config.workbench_config_write.set_project_value("issues.team", "true", project)
    assert config.workbench_config.load_config(project).issues.team == "true"


@needs_yaml
def test_the_pyyaml_fallback_writes_the_same_types(roots, monkeypatch):
    """Two writers, one coercion — the fallback cannot disagree about the type."""
    monkeypatch.setattr(config.workbench_config_write.shutil, "which", lambda _: None)
    config.workbench_config_write.set_value(config.workbench_config.GITHUB_SSH_443_KEY, "true")
    config.workbench_config_write.set_value("agent.model", "sonnet")
    cfg = config.workbench_config.load_config()
    assert cfg.github.ssh_over_443 is True
    assert cfg.agent.model == "sonnet"


def test_a_list_key_refuses_the_scalar_this_writer_can_offer(roots):
    """`serde` reads a string where a list belongs as `[]`, so a write would vanish."""
    config_root, _ = roots
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.set_value(config.workbench_config.ISSUE_LABELS_KEY, "follow-up")
    assert config.workbench_config.ISSUE_LABELS_KEY in str(exc.value)
    assert not (config_root / config.workbench_config.CONFIG_NAME).exists()


def test_a_hand_written_list_still_loads(roots):
    """The refusal is the writer's alone — the file itself holds a list fine."""
    _, project = roots
    _write(
        project / config.workbench_config.PROJECT_CONFIG_NAME,
        "issues:\n  labels:\n    - follow-up\n    - needs-triage\n",
    )
    assert config.workbench_config.load_config(project).issues.labels == ["follow-up", "needs-triage"]


def test_labels_default_to_the_follow_up_label(roots):
    """A repo that says nothing still labels what its automation files."""
    _, project = roots
    assert config.workbench_config.load_config(project).issues.labels == [config.workbench_config.FOLLOW_UP_LABEL]


def test_an_empty_label_list_is_kept_as_the_opt_out_it_is(roots):
    """`labels: []` has to outrank the default, or opting out is impossible."""
    _, project = roots
    _write(project / config.workbench_config.PROJECT_CONFIG_NAME, "issues:\n  labels: []\n")
    assert config.workbench_config.load_config(project).issues.labels == []


def test_a_numeric_field_is_parsed_into_the_number_it_names():
    """Against a real integer key, so the writer is read through the surface.

    This used to monkeypatch `schema_type` because nothing on the surface was
    numeric. `fix.verify_timeout` is, so the coercion is now reached the way a
    caller reaches it — a patched type would keep passing if the key stopped
    being an integer.
    """
    assert config.workbench_config_write.coerce_value(config.workbench_config.FIX_VERIFY_TIMEOUT_KEY, "30") == 30


# The float half of the numeric test this change split in two. The integer half
# now runs against a real key. `batch.cpu_pressure_max` / `batch.mem_pressure_max`
# are floats on the surface; this case still uses a patched type so it covers a
# key that is not.
# passes-at-base: it is the pre-existing patched-type case, carried over intact
def test_a_float_field_is_parsed_through_a_patched_type(monkeypatch):
    """A key typed as number (not an int field) is coerced to float."""
    monkeypatch.setattr(config.workbench_config_write, "schema_type", lambda _: "number")
    assert config.workbench_config_write.coerce_value("some.ratio", "1.5") == 1.5


def test_the_fix_verification_keys_are_on_the_surface():
    """`fix.engine` reads these off a loaded config; an absent key reads as unset."""
    assert config.workbench_config.defines_key(config.workbench_config.FIX_VERIFY_COMMAND_KEY)
    assert config.workbench_config.defines_key(config.workbench_config.FIX_VERIFY_TIMEOUT_KEY)


def test_a_repo_that_declares_no_verify_command_gets_the_empty_default():
    """Empty is "this repo has not said", which `fix.suite` reports as such.

    A default command here would point every repo at a script only one of them
    has, which is the reason the key exists instead of a hardcoded runner.
    """
    assert config.workbench_config.WorkbenchConfig().fix.verify_command == ""


def test_the_declared_command_round_trips_from_the_project_scope(roots):
    _, project = roots
    _write(project / config.workbench_config.PROJECT_CONFIG_NAME,
           "fix:\n  verify_command: bin/local/run-tests --changed\n"
           "  verify_timeout: 120\n")

    loaded = config.workbench_config.load_config(project)

    assert loaded.fix.verify_command == "bin/local/run-tests --changed"
    assert loaded.fix.verify_timeout == 120


def test_the_verify_timeout_refuses_a_value_that_is_not_a_number():
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.workbench_config_write.coerce_value(config.workbench_config.FIX_VERIFY_TIMEOUT_KEY, "ten minutes")


def test_the_verification_keys_are_refused_at_the_machine_scope():
    """They name a path inside one checkout and bound that repo's own checks.

    A machine-wide command points every other repo at a script it does not
    have, and the pass would report ERROR on repos that never opted in.
    """
    for key in (config.workbench_config.FIX_VERIFY_COMMAND_KEY, config.workbench_config.FIX_VERIFY_TIMEOUT_KEY):
        assert config.workbench_config_write.check_scope(key, config.workbench_config.GLOBAL_SCOPE).ok is False
        assert config.workbench_config_write.check_scope(key, config.workbench_config.PROJECT_SCOPE).ok is True
        assert config.workbench_config_write.check_scope(key, config.workbench_config.CONTAINER_SCOPE).ok is True


def test_a_numeric_field_refuses_a_value_that_is_not_a_number(monkeypatch):
    monkeypatch.setattr(config.workbench_config_write, "schema_type", lambda _: "integer")
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.coerce_value("some.count", "a few")
    assert "some.count" in str(exc.value)


def test_a_numeric_field_refuses_the_floats_yaml_cannot_spell(monkeypatch):
    """`float("nan")` parses, and `.key = nan` is a bare word yq reads as nothing."""
    monkeypatch.setattr(config.workbench_config_write, "schema_type", lambda _: "number")
    for spelled in ("nan", "inf", "-inf"):
        with pytest.raises(config.workbench_config.ConfigValueError):
            config.workbench_config_write.coerce_value("some.ratio", spelled)


def test_an_optional_field_is_typed_through_its_null_half():
    """`str | None` is a union in the schema and a string to a writer."""
    schema = config.workbench_config.surface_schema()
    assert config.workbench_config.schema_type(config.workbench_config.schema_at(schema, "reuse.level")) == "string"
    assert config.workbench_config.schema_type(config.workbench_config.schema_at(schema, config.workbench_config.GITHUB_SSH_443_KEY)) == "boolean"


def test_a_fragment_that_names_no_type_is_left_permissive():
    """`schema_gen` emits an open fragment for a hint it cannot describe.

    A check that cannot see the type must not be the thing that refuses a
    write, which is the same direction the key walk is permissive in.
    """
    assert config.workbench_config.schema_type({}) is None
