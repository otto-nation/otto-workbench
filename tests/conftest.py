import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_POST = REPO_ROOT / "ai" / "claude" / "bin" / "review-post"


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
