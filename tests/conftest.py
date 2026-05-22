import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEW_POST = REPO_ROOT / "ai" / "claude" / "bin" / "review-post"


@pytest.fixture(scope="session")
def rp():
    loader = importlib.machinery.SourceFileLoader("rp", str(REVIEW_POST))
    spec = importlib.util.spec_from_loader("rp", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rp"] = mod
    spec.loader.exec_module(mod)
    return mod
