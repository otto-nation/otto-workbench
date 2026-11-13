"""Pin behaviour recent changes left untested."""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import core.run_lock  # noqa: E402
import fix.ci  # noqa: E402
import fix.comments  # noqa: E402
import rebase.fork_point  # noqa: E402
import rebase.prepush  # noqa: E402

# Doubled braces (f-string / format) and whitespace around the selection.
_REVIEW_AUTHOR_TYPENAME = re.compile(r"author\s*\{\{?\s*login\s+__typename")


@pytest.mark.parametrize(
    "adapter",
    [
        fix.comments.CommentFixAdapter,
        fix.ci.CIFixAdapter,
        rebase.prepush.PrePushFixAdapter,
    ],
    ids=["CommentFixAdapter", "CIFixAdapter", "PrePushFixAdapter"],
)
def test_fix_adapters_do_not_require_tests(adapter):
    """Owner: `fix.engine.FixAdapter.requires_tests` on CommentFixAdapter,
    CIFixAdapter, and PrePushFixAdapter."""
    assert adapter.requires_tests is False


@pytest.mark.parametrize(
    "rel",
    [
        "ai/lib/gh/pr_data.py",
        "ai/lib/batch/plan.py",
        "ai/lib/gh/pr_pages.py",
    ],
)
def test_review_author_selection_requests_typename(rel):
    """Owner: the review `author { login __typename }` selection in each module."""
    source = (REPO_ROOT / rel).read_text()
    assert _REVIEW_AUTHOR_TYPENAME.search(source)


def test_run_lock_scopes_are_target_and_checkout():
    """Owner: `core.run_lock.Scope` — target and checkout; no Global lock."""
    assert {s.value for s in core.run_lock.Scope} == {"target", "checkout"}
    assert not hasattr(core.run_lock, "Global")
    assert not hasattr(core.run_lock, "GLOBAL_LOCK_ENV")


def test_rebase_parser_exposes_fork_point_flag():
    """Owner: `rebase.fork_point.FLAG` among `cli.pr_rebase.build_parser()` options."""
    strings = {
        s
        for a in cli.pr_rebase.build_parser()._actions
        for s in a.option_strings
    }
    assert rebase.fork_point.FLAG in strings


def _config_subparser_names() -> list[str]:
    return re.findall(
        r'add_parser\(\s*"([^"]+)"',
        (REPO_ROOT / "lib" / "config_cli.py").read_text(),
    )


def _config_subparser_params():
    params = []
    # pi-catalogue and run-bounded are `python3 lib/config_cli.py` helpers,
    # not `otto-workbench config` commands, so they have no registry row.
    for name in _config_subparser_names():
        if name in {"pi-catalogue", "run-bounded"}:
            continue
        if name == "model":
            params.append(
                pytest.param(
                    name,
                    marks=pytest.mark.xfail(
                        strict=True,
                        reason="added by the live-drift PR",
                    ),
                )
            )
        else:
            params.append(name)
    return params


def _registry_config_leaders() -> set[str]:
    data = yaml.safe_load((REPO_ROOT / "bin" / "registry.yml").read_text())
    tool = next(t for t in data["tools"] if t["name"] == "otto-workbench")
    leaders = set()
    for cmd in tool["commands"]:
        parts = str(cmd["name"]).split()
        if len(parts) >= 2 and parts[0] == "config":
            leaders.add(parts[1])
    return leaders


@pytest.mark.parametrize("subparser", _config_subparser_params())
def test_config_subparser_is_in_otto_workbench_registry(subparser):
    """Owner: each `otto-workbench config` subparser in lib/config_cli.py
    is a leading word after `config ` in bin/registry.yml."""
    assert subparser in _registry_config_leaders()


def test_explicit_model_outranks_phase_env(monkeypatch):
    """Owner: `agent.phases.resolve_phase_model` — --model outranks
    WORKBENCH_AI_<PHASE>_MODEL."""
    import agent.phases
    from config.workbench_config import WorkbenchConfig
    from core.phases import Phase

    phase = Phase.DESCRIBE
    monkeypatch.delenv("WORKBENCH_AI_PROVIDER", raising=False)
    monkeypatch.setenv(phase.model_env_key, "model-b")
    assert agent.phases.phase_model(
        phase, "model-a", WorkbenchConfig(),
    ) == "model-a"


def test_maintenance_path_comes_from_env_sh():
    """Owner: maintenance/bin/otto-workbench-maintenance sources ZSH_ENV_SRC
    and does not prepend .local/bin itself."""
    constants = (REPO_ROOT / "lib" / "constants.sh").read_text()
    env_src = re.search(r'^ZSH_ENV_SRC=(.*)$', constants, re.M)
    assert env_src is not None
    assert "env.sh" in env_src.group(1)
    source = (REPO_ROOT / "maintenance" / "bin" / "otto-workbench-maintenance").read_text()
    assert '. "$ZSH_ENV_SRC"' in source
    assert "workbench_env" in source
    for line in source.splitlines():
        code = line.split("#", 1)[0]
        if ".local/bin" in code and re.search(r"\bPATH=", code):
            pytest.fail(
                "maintenance prepends .local/bin itself: " + line,
            )
