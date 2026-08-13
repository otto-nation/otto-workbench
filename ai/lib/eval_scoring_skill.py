"""The skill eval task: drive a SKILL.md against a fixture, grade what it ran.

A skill is a procedure a session follows, not a subprocess, so there is no
artifact to diff. What there is, is the sequence of shell commands it issued —
and both skills covered here state their constraints as commands not to issue.
So the trace is the oracle: stubs on PATH record every call, and the manifest
declares which groups of tokens must appear, in order, and which must not.

The SKILL.md is read live from `ai/claude/skills/`, never copied into a case.
The file is the single source of truth; a copy would let the eval keep passing
against a skill that no longer says what the copy says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TraceMatch:
    """One `requires` group and whether the trace satisfied it.

    `matched` and `matched_finding_id` are not this task's names for these
    things — they are the duck-typed contract `eval-models._serialize_run`
    reads off every element of `ScoringResult.matches`. `ci-fix` sidesteps it
    by leaving `matches` empty, which is why its run report reads `(0/0)`.
    """

    pattern: tuple[str, ...]
    matched: bool = False
    matched_finding_id: str = ""


def group_matches(group: list[str], line: str) -> bool:
    """True when every token in `group` is a substring of `line`.

    Substring rather than exact argv element, so a group need not spell out
    the flags around the part it cares about. An empty group is never a match:
    `all([])` is True, and an empty `forbids` entry would then fail every run.
    """
    return bool(group) and all(token in line for token in group)


def match_required(groups: list[list[str]], lines: list[str]) -> list[TraceMatch]:
    """Match each group against a later line than the group before it.

    Ordering is the point. "Drafted, then published" is a claim about sequence;
    both commands merely appearing somewhere in the trace is not evidence for it.
    """
    matches: list[TraceMatch] = []
    start = 0
    for group in groups:
        found = TraceMatch(pattern=tuple(group))
        for i in range(start, len(lines)):
            if group_matches(group, lines[i]):
                found = TraceMatch(tuple(group), True, lines[i])
                start = i + 1
                break
        matches.append(found)
    return matches


def match_forbidden(groups: list[list[str]], lines: list[str]) -> list[str]:
    """The joined text of every group that fired, at most once per group.

    Two calls that break one rule are one broken rule. Counting them twice
    would let a retry loop dominate the false-positive figure.
    """
    return [
        " ".join(group)
        for group in groups
        if any(group_matches(group, line) for line in lines)
    ]


def load_trace(trace_file: str) -> list[str]:
    """The recorded argv lines, space-joined.

    A missing file means the session ran nothing a shim saw — that scores zero,
    which is a result, not an error. A single unparseable line is skipped for
    the same reason: a shim killed mid-write should cost one command, not the
    whole run's score.
    """
    path = Path(trace_file)
    if not path.is_file():
        return []
    lines = []
    for raw in path.read_text().splitlines():
        try:
            argv = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(argv, list):
            lines.append(" ".join(str(part) for part in argv))
    return lines


# A distinctive code, not 1: an unanticipated call is a fixture gap, and it
# should not read like the stubbed command reporting an ordinary failure.
NO_MATCH_EXIT = 97

# The trace path and the rules are baked in rather than passed through the
# environment, so nothing in the driven session can retarget the recorder.
_SHIM = '''#!/usr/bin/env python3
"""Generated eval shim. Records the call, then replays a canned response."""
import json
import os
import sys

NAME = {name!r}
TRACE = {trace!r}
RULES = {rules!r}
ON_NO_MATCH = {policy!r}
NO_MATCH_EXIT = {no_match_exit!r}

argv = [NAME, *sys.argv[1:]]
with open(TRACE, "a") as fh:
    fh.write(json.dumps(argv) + "\\n")

line = " ".join(argv)
for rule in RULES:
    # An empty match is never a match, mirroring group_matches: all([]) is
    # True, and an empty list would otherwise fire on every call.
    if rule["match"] and all(token in line for token in rule["match"]):
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))

if ON_NO_MATCH == "passthrough":
    here = os.path.dirname(os.path.abspath(__file__))
    os.environ["PATH"] = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep) if p != here
    )
    os.execvp(NAME, argv)

sys.stderr.write("eval shim: no rule for: " + line + "\\n")
sys.exit(NO_MATCH_EXIT)
'''


def _resolve_rules(name: str, rules: list[dict], case_dir: Path) -> list[dict]:
    """Inline every `stdout_file` so the shim never reads the case directory.

    A rule with no `match` key is a malformed fixture, not a catch-all — a
    default of `[]` here would silently make the rule fire on every call.
    """
    resolved = []
    for rule in rules:
        if "match" not in rule:
            raise ValueError(f"{name}: rule missing 'match': {rule!r}")
        out = dict(rule)
        source = out.pop("stdout_file", "")
        if source:
            out["stdout"] = (case_dir / source).read_text()
        out["match"] = list(out["match"])
        resolved.append(out)
    return resolved


def write_shims(
    responses: dict, bin_dir: Path, case_dir: Path, trace_file: Path,
) -> None:
    """Write one recording shim per named binary into `bin_dir`.

    `fail` is the default policy on purpose. A stubbed CLI that quietly exits 0
    on a call nobody anticipated turns a fixture gap into a passing run.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, spec in responses.items():
        shim = bin_dir / name
        shim.write_text(_SHIM.format(
            name=name,
            trace=str(trace_file),
            rules=_resolve_rules(name, spec.get("rules", []), case_dir),
            policy=spec.get("on_no_match", "fail"),
            no_match_exit=NO_MATCH_EXIT,
        ))
        shim.chmod(0o755)
