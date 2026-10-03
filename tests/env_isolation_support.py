"""Autouse fixtures that keep a test's environment variables from leaking.

Each one saves what the developer's shell exported, runs the test, and puts
back exactly that, so neither the shell nor an earlier test answers a later
test's assertions. Registered from `conftest.py` as a plugin.
"""

import os
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")


def _agent_env_keys() -> list[str]:
    """The agent-config vars exported right now, by the prefix their owner defines.

    Imported lazily, like the other fixtures that reach into ai/lib: a
    module-scope import here would make every test's collection depend on
    phases importing cleanly.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from core.phases import ENV_PREFIX
    return [k for k in os.environ if k.startswith(ENV_PREFIX)]


@pytest.fixture(autouse=True)
def _clear_agent_env():
    """Run every test with the agent config env unset.

    Model, thinking, and provider settings are read straight from the
    environment with no injection point, so a developer who exports
    WORKBENCH_AI_THINKING for their own runs answers those tests' assertions
    from their shell. Modules that resolve config guard themselves today; this
    is the floor, so the next one does not have to remember.

    Matching on the prefix rather than a list is what makes it a floor: the
    per-phase keys are generated from the Phase enum, so a new phase brings new
    keys that no list here would know about. Teardown drops whatever the test
    left behind before restoring, so a test that writes os.environ directly
    cannot leak into the next one either.
    """
    saved = {k: os.environ.pop(k) for k in _agent_env_keys()}
    yield
    for key in _agent_env_keys():
        del os.environ[key]
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _restore_gh_token():
    """Restore GH_TOKEN after every test, whatever the test did to it.

    ``pr.gh_token.use_for_publishing`` writes ``os.environ`` directly, and a
    test that drives it through any caller leaks the token into every test
    after it — which then authenticates ``gh`` with a fixture string. Saved
    and restored here, like ``_clear_agent_env``, so the next caller does not
    have to remember.
    """
    saved = os.environ.get("GH_TOKEN")
    yield
    if saved is None:
        os.environ.pop("GH_TOKEN", None)
    else:
        os.environ["GH_TOKEN"] = saved
