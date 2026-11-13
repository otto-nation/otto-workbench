"""Tests for `core.command_contract.union_schema`."""

import json

import pytest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.command_contract  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def _toy(*, mcp_exclude: tuple[str, ...] = ()) -> core.command_contract.CommandContract:
    return core.command_contract.CommandContract(
        summary="Do the thing.",
        groups=(),
        exits=(
            core.command_contract.ExitOutcome(ExitCode.OK, "done"),
            core.command_contract.ExitOutcome(ExitCode.FAILURE, "broke"),
            core.command_contract.ExitOutcome(ExitCode.USAGE, "bad flags"),
        ),
        examples=(core.command_contract.Example("toy alpha", "does it"),),
        mcp_exclude=mcp_exclude,
    )


def _two_command_doc(**contracts: core.command_contract.CommandContract) -> dict:
    return core.command_contract.union_schema(
        "toy",
        "A toy dispatcher",
        ("alpha", "beta"),
        contracts,
        context_properties={
            "repo_dir": {"type": "string", "description": "worktree", "x-context": True},
        },
    )


def test_union_schema_enumerates_commands_and_contracts():
    doc = _two_command_doc(alpha=_toy(), beta=_toy())
    command = doc["input_schema"]["properties"]["command"]
    assert command["enum"] == ["alpha", "beta"]
    assert command["x-positional"] is True
    assert command["description"] == "Subcommand to run"
    assert doc["input_schema"]["required"] == ["command"]
    contracts = doc["input_schema"]["x-contracts"]
    assert list(contracts) == ["alpha", "beta"]
    assert contracts["alpha"] == core.command_contract.schema(_toy())
    assert contracts["beta"] == core.command_contract.schema(_toy())
    assert list(doc) == ["name", "description", "input_schema"]
    assert list(doc["input_schema"]) == ["type", "properties", "required", "x-contracts"]


def test_mcp_exclude_drops_a_command_from_the_enum_and_contracts():
    doc = _two_command_doc(alpha=_toy(), beta=_toy(mcp_exclude=("beta",)))
    command = doc["input_schema"]["properties"]["command"]
    assert command["enum"] == ["alpha"]
    assert list(doc["input_schema"]["x-contracts"]) == ["alpha"]
    blob = json.dumps(doc)
    assert "beta" not in blob
    assert "beta" not in command["enum"]
    assert "beta" not in doc["input_schema"]["x-contracts"]


def test_a_contract_excludes_only_its_own_command():
    doc = _two_command_doc(alpha=_toy(mcp_exclude=("beta",)), beta=_toy())
    assert doc["input_schema"]["properties"]["command"]["enum"] == ["alpha", "beta"]


def test_unknown_mcp_exclude_name_raises():
    with pytest.raises(ValueError):
        _two_command_doc(alpha=_toy(mcp_exclude=("gamma",)), beta=_toy())
