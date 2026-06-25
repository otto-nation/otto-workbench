import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "claude" / "lib")
REVIEW_POST = REPO_ROOT / "ai" / "claude" / "bin" / "review-post"
REVIEW_ORCHESTRATE = REPO_ROOT / "ai" / "claude" / "bin" / "review-orchestrate"
REVIEW_THREADS = REPO_ROOT / "ai" / "claude" / "bin" / "review-threads"


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
