"""How a tool writes a machine-readable report to stdout.

A tool's stdout is its contract with whatever launched it, and the shape of
that contract is the same everywhere: one indented JSON object, or a stream of
them a reader can tell apart. Neither is domain vocabulary, so it lives here
rather than being spelled out again in each binary that reports.
"""

# doc-group: platform

from __future__ import annotations

import json
import sys


def emit_json(report: dict) -> None:
    """Write one report to stdout as indented JSON, followed by a newline."""
    json.dump(report, sys.stdout, indent=2)
    print(flush=True)


def emit_stream_json(report: dict, report_type: str) -> None:
    """Write one report of a stream to stdout, tagged with its type.

    A long-running tool emits several reports over one stdout. The `---` line
    before each and the `type` key inside it are what lets a reader split the
    stream and know which report it is holding.
    """
    print("---", flush=True)
    emit_json({**report, "type": report_type})
