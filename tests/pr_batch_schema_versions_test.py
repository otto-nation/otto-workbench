"""`pr --schema-version` matches what each batch subcommand emits.

Revert: served_schema_versions returning (2,) for every batch subcommand
except plan.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.schema  # noqa: E402


def test_batch_schema_versions_match_what_each_subcommand_emits():
    # Revert: `return (1,) if argv and argv[0] == "plan" else (2,)`.
    assert cli.schema.served_schema_versions("batch", ["plan"]) == (1,)
    assert cli.schema.served_schema_versions("batch", ["status"]) == (2,)
    assert cli.schema.served_schema_versions("batch", ["report"]) == (2,)
    assert cli.schema.served_schema_versions("batch", ["run"]) == (1,)
    assert cli.schema.served_schema_versions("batch", ["resume"]) == (1,)
    assert cli.schema.served_schema_versions("batch", ["cancel"]) == ()
    assert cli.schema.served_schema_versions("batch", ["release"]) == ()
    assert cli.schema.served_schema_versions("batch", ["session"]) == (1,)
    assert cli.schema.served_schema_versions("batch", ["note"]) == ()
    assert cli.schema.served_schema_versions("batch", ["status", "--full"]) == (2,)
