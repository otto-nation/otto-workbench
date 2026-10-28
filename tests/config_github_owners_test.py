"""github.owners: how the owner list is parsed, scoped, and written."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import config.github_owners
import config.workbench_config
import config.workbench_config_write

from workbench_config_support import roots

KEY = config.workbench_config.GITHUB_OWNERS_KEY


def test_a_bare_owner_is_on_public_github():
    assert config.github_owners.parse_owner("Otto-Nation") == config.github_owners.Owner("github.com", "otto-nation")


def test_a_host_qualified_owner_keeps_its_host():
    parsed = config.github_owners.parse_owner("GHE.corp.example/Platform")
    assert parsed == config.github_owners.Owner("ghe.corp.example", "platform")
    assert str(parsed) == "ghe.corp.example/platform"


def test_the_same_name_on_two_hosts_is_two_owners():
    assert config.github_owners.parse_owner("foo") != config.github_owners.parse_owner("ghe.corp/foo")


@pytest.mark.parametrize("spec", ["", "a/b/c", "has space", "/foo", "foo/"])
def test_a_malformed_owner_is_refused(spec):
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.github_owners.parse_owner(spec)


def test_an_owner_in_two_groups_is_refused_case_insensitively():
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.github_owners.owner_index({"work": ["Acme"], "personal": ["acme"]})
    message = str(exc.value)
    assert "acme" in message and "work" in message and "personal" in message


def test_a_group_name_that_cannot_be_a_key_segment_is_refused():
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.github_owners.owner_index({"my.group": ["acme"]})


def test_owner_index_maps_each_owner_to_its_group():
    index = config.github_owners.owner_index({"work": ["acme", "ghe.corp/platform"], "personal": ["me"]})
    assert index[config.github_owners.owner("github.com", "acme")] == "work"
    assert index[config.github_owners.owner("ghe.corp", "platform")] == "work"
    assert index[config.github_owners.owner("github.com", "me")] == "personal"


def test_a_group_is_written_as_a_list_at_global_scope(roots):
    config.workbench_config_write.set_value(f"{KEY}.work", "[otto-nation, ghe.corp/platform]")
    loaded = config.workbench_config.load_config().github.owners
    assert loaded == {"work": ["otto-nation", "ghe.corp/platform"]}


def test_a_group_is_refused_at_project_scope(roots):
    """The rule sits on github.owners; a write to one of its groups inherits it."""
    _, project = roots
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_project_value(f"{KEY}.work", "[acme]", project)
    assert not (project / config.workbench_config.PROJECT_CONFIG_NAME).exists()


def test_a_group_is_refused_at_container_scope(roots, container):
    with pytest.raises(config.workbench_config.ConfigScopeError):
        config.workbench_config_write.set_container_value(f"{KEY}.work", "[acme]", container / "main")


def test_a_second_group_repeating_an_owner_is_refused_and_nothing_is_written(roots):
    config_root, _ = roots
    config.workbench_config_write.set_value(f"{KEY}.work", "[acme]")
    before = (config_root / config.workbench_config.CONFIG_NAME).read_text()
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.set_value(f"{KEY}.personal", "[ACME]")
    assert "work" in str(exc.value)
    assert (config_root / config.workbench_config.CONFIG_NAME).read_text() == before


def test_rewriting_a_group_with_its_own_owner_is_not_a_duplicate(roots):
    config.workbench_config_write.set_value(f"{KEY}.work", "[acme]")
    config.workbench_config_write.set_value(f"{KEY}.work", "[acme, other]")
    assert config.workbench_config.load_config().github.owners == {"work": ["acme", "other"]}


def test_the_whole_map_is_refused_as_one_value(roots):
    with pytest.raises(config.workbench_config.ConfigValueError) as exc:
        config.workbench_config_write.set_value(KEY, "{work: [acme]}")
    assert f"{KEY}.<" in str(exc.value)


def test_a_key_nested_below_a_group_is_refused(roots):
    with pytest.raises(config.workbench_config.ConfigValueError):
        config.workbench_config_write.set_value(f"{KEY}.work.extra", "acme")
