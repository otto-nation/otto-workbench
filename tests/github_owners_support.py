"""Declare which GitHub owners are ours, in the sandboxed global config."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")


@pytest.fixture
def github_owners():
    """Declare which GitHub owners are ours, in the sandboxed global config.

    Every test runs against an empty config root (`_isolate_workbench_config`),
    and an empty root is the unconfigured case — which refuses every GitHub
    write `gh.client` makes. A test that drives a write through the client
    declares its owners here, in the one file the owner policy reads.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import config.workbench_config

    def _declare(groups: dict[str, list[str]]) -> Path:
        path = config.workbench_config.global_config_path()
        data = config.workbench_config.read_yaml(path)
        section = data.get("github") if isinstance(data.get("github"), dict) else {}
        data["github"] = {**section, "owners": groups}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config.workbench_config.yaml_dump(data))
        return path

    return _declare
