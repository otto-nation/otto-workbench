"""Tests for ai/lib/config/tool_registry.py — which scripts the registries name."""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

from config.tool_registry import (  # noqa: E402
    RegistryEntry,
    Visibility,
    load_registry_entries,
)


def _write_registry(root: Path, relpath: str, body: str) -> Path:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


BINDIR = """\
    meta:
      section: "Fixture"
      validation: bindir
      source: bin

    tools:
      - name: shown-tool
        permission: true
        visibility: brief
        description: "A tool with a one-line entry"

      - name: inner-tool
        permission: false
        visibility: hidden
        description: "An implementation detail"

      - name: documented-tool
        permission: true
        visibility: full
        description: "A tool with the long form"
        when_to_use: "The moment arises"
        usage: "documented-tool --now"
"""


# ── what the mapping holds ───────────────────────────────────────────────


def test_a_bindir_registry_keys_its_tools_by_script_path(tmp_path):
    _write_registry(tmp_path, "bin/registry.yml", BINDIR)

    entries = load_registry_entries(tmp_path)

    assert set(entries) == {
        tmp_path.resolve() / "bin" / name
        for name in ("shown-tool", "inner-tool", "documented-tool")
    }


def test_an_entry_carries_what_the_registry_said(tmp_path):
    _write_registry(tmp_path, "bin/registry.yml", BINDIR)

    entry = load_registry_entries(tmp_path)[tmp_path.resolve() / "bin" / "documented-tool"]

    assert entry == RegistryEntry(
        name="documented-tool",
        description="A tool with the long form",
        visibility=Visibility.FULL,
        when_to_use="The moment arises",
        usage="documented-tool --now",
    )


def test_a_nested_component_registry_is_read(tmp_path):
    """ai/claude/registry.yml sits a level deeper than git/bin/registry.yml."""
    _write_registry(tmp_path, "ai/claude/registry.yml", """\
        meta:
          validation: bindir
          source: ai/claude/bin

        tools:
          - name: deep-tool
            permission: true
            visibility: brief
            description: "Registered from the second tier"
    """)

    entries = load_registry_entries(tmp_path)

    assert set(entries) == {tmp_path.resolve() / "ai" / "claude" / "bin" / "deep-tool"}


def test_registries_of_other_kinds_name_no_scripts(tmp_path):
    """A brew stack's meta.source is a Brewfile, so its names are not paths."""
    _write_registry(tmp_path, "brew/registry.yml", """\
        meta:
          validation: brewfile
          source: brew/Brewfile

        tools:
          - name: jq
            permission: true
            visibility: brief
            description: "JSON processor"
    """)

    assert load_registry_entries(tmp_path) == {}


def test_a_bindir_registry_with_no_source_names_nothing(tmp_path):
    _write_registry(tmp_path, "bin/registry.yml", """\
        meta:
          validation: bindir

        tools:
          - name: homeless-tool
            permission: true
            visibility: brief
            description: "Nowhere to resolve against"
    """)

    assert load_registry_entries(tmp_path) == {}


def test_a_tree_with_no_registries_names_nothing(tmp_path):
    (tmp_path / "bin").mkdir()

    assert load_registry_entries(tmp_path) == {}


def test_a_registry_that_will_not_parse_costs_only_its_own_tools(tmp_path, caplog):
    """One file mid-edit must not take the other components' tools down with it."""
    _write_registry(tmp_path, "broken/registry.yml", "- not\n- a mapping\n")
    _write_registry(tmp_path, "bin/registry.yml", BINDIR)

    with caplog.at_level("WARNING"):
        entries = load_registry_entries(tmp_path)

    assert set(entries) == {
        tmp_path.resolve() / "bin" / name
        for name in ("shown-tool", "inner-tool", "documented-tool")
    }
    assert "broken/registry.yml" in caplog.text


def test_a_registry_shaped_like_something_else_names_nothing(tmp_path):
    """meta and tools have to be the kinds of thing they are read as."""
    _write_registry(tmp_path, "odd/registry.yml", """\
        meta: bindir
        tools: "shown-tool"
    """)
    _write_registry(tmp_path, "other/registry.yml", """\
        meta:
          validation: bindir
          source: bin
        tools:
          - just a name
    """)

    assert load_registry_entries(tmp_path) == {}


# ── visibility ───────────────────────────────────────────────────────────


def test_full_and_brief_are_offered_and_hidden_is_not(tmp_path):
    _write_registry(tmp_path, "bin/registry.yml", BINDIR)

    entries = load_registry_entries(tmp_path)

    offered = {path.name for path, entry in entries.items() if entry.offered}
    assert offered == {"shown-tool", "documented-tool"}


def test_an_unreadable_visibility_falls_back_to_hidden(tmp_path):
    """Fail closed: a tool whose audience nobody stated is not one to advertise."""
    _write_registry(tmp_path, "bin/registry.yml", """\
        meta:
          validation: bindir
          source: bin

        tools:
          - name: odd-tool
            permission: true
            visibility: sometimes
            description: "Hand-edited past validate-registries"

          - name: bare-tool
            permission: true
            description: "No visibility at all"
    """)

    entries = load_registry_entries(tmp_path)

    assert not any(entry.offered for entry in entries.values())


def test_the_enum_holds_the_values_the_shell_validator_accepts():
    """Two spellings of one fixed set — adding a value to either alone fails here."""
    text = (REPO_ROOT / "bin" / "local" / "validate-registries").read_text()
    named = re.search(r"must be ([a-z|]+)\)", text)

    assert named, "validate-registries no longer names the visibility values it accepts"
    assert set(named.group(1).split("|")) == {v.value for v in Visibility}


# ── the description a client reads ───────────────────────────────────────


def test_a_brief_entry_describes_itself_in_one_line():
    entry = RegistryEntry(name="t", description="What it is", visibility=Visibility.BRIEF)

    assert entry.tool_description == "What it is"


def test_a_full_entry_answers_when_to_use_it_and_how():
    entry = RegistryEntry(
        name="t", description="What it is", visibility=Visibility.FULL,
        when_to_use="The moment arises", usage="t --now",
    )

    assert entry.tool_description == (
        "What it is\n\nWhen to use: The moment arises\n\nUsage: t --now")


# ── the real repo ────────────────────────────────────────────────────────


def test_the_checkout_names_the_scripts_it_ships():
    """The mapping has to land on real files, not on paths that merely look right."""
    entries = load_registry_entries(REPO_ROOT)

    assert entries, "no registry entry was read from the checkout"
    missing = [str(path) for path in entries if not path.exists()]
    assert missing == []


def test_the_pr_family_is_registered_the_way_the_server_reads_it():
    """`pr` is the tool; `pr rebase` and `pr ci` run the other two."""
    entries = load_registry_entries(REPO_ROOT)
    bin_dir = REPO_ROOT / "ai" / "bin"

    assert entries[bin_dir / "pr"].visibility is Visibility.FULL
    assert entries[bin_dir / "pr-rebase"].visibility is Visibility.HIDDEN
    assert entries[bin_dir / "ci-check"].visibility is Visibility.HIDDEN
