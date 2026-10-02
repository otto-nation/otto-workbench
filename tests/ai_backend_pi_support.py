"""Helpers shared by the ai_backend_pi_* suites: RPC event lines and the captured Pi fixtures."""

import json

from conftest import FIXTURES_DIR


def _event(event_type):
    return json.dumps({"type": event_type}) + "\n"


# ── Fixtures captured from a live Pi run ─────────────────────────────────────
# Hand-written fixtures agree with whatever the code already does, which is how
# every bug these cover survived: `args` read as `arguments`, session stats read
# off the response envelope, one prompt's two message costs read as one. See
# tests/fixtures/README-pi-fixtures.md for the recapture commands.

FIXTURES = FIXTURES_DIR


def _stats_response() -> dict:
    return json.loads((FIXTURES / "pi_rpc_stats_response.json").read_text())
