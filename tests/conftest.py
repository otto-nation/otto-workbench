import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_GIT_HOOK_VARS = (
    "GIT_DIR", "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


@pytest.fixture(autouse=True, scope="session")
def _clear_git_hook_env():
    """Clear git env vars inherited from hooks (e.g. pre-push)."""
    saved = {k: os.environ.pop(k) for k in _GIT_HOOK_VARS if k in os.environ}
    yield
    os.environ.update(saved)


REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
REVIEW_POST = REPO_ROOT / "ai" / "claude" / "bin" / "review-post"
REVIEW_ORCHESTRATE = REPO_ROOT / "ai" / "claude" / "bin" / "review-orchestrate"
REVIEW_THREADS = REPO_ROOT / "ai" / "claude" / "bin" / "review-threads"
CI_CHECK = REPO_ROOT / "ai" / "claude" / "bin" / "ci-check"


def write_thrash_log(path) -> str:
    """Write the session log of a clean completion that never wrote anything.

    This is the shape the shared thrash guard exists to catch — the agent ended
    on its own terms, so there is no error to blame it for. Every `pr` script
    that drives an agent needs the same fixture, so it lives here rather than
    once per test module. Returns the path as a str, which is what the guard's
    callers take.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}},
        ]}}),
        json.dumps({"type": "result", "subtype": "success", "num_turns": 3}),
    ]) + "\n")
    return str(path)


# Session-scoped: the module is loaded once and shared across all tests.
# Tests must not mutate module-level state.
@pytest.fixture(scope="session")
def rp():
    loader = importlib.machinery.SourceFileLoader("review_post", str(REVIEW_POST))
    spec = importlib.util.spec_from_loader("review_post", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_post"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["review_post"]


@pytest.fixture(autouse=True)
def _clear_bot_login_cache():
    """Clear _get_bot_login lru_cache between tests."""
    yield
    if LIB_DIR in sys.path or "review_dedup" in sys.modules:
        try:
            import review_dedup
            review_dedup._get_bot_login.cache_clear()
        except (ImportError, AttributeError):
            pass


@pytest.fixture(scope="session")
def ro():
    loader = importlib.machinery.SourceFileLoader("review_orchestrate", str(REVIEW_ORCHESTRATE))
    spec = importlib.util.spec_from_loader("review_orchestrate", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_orchestrate"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["review_orchestrate"]


@pytest.fixture(scope="session")
def rt():
    loader = importlib.machinery.SourceFileLoader("review_threads", str(REVIEW_THREADS))
    spec = importlib.util.spec_from_loader("review_threads", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_threads"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["review_threads"]


@pytest.fixture(scope="session")
def cc():
    loader = importlib.machinery.SourceFileLoader("ci_check", str(CI_CHECK))
    spec = importlib.util.spec_from_loader("ci_check", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ci_check"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["ci_check"]
