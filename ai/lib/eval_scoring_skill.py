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
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import ai_backend
import ai_usage
from eval_scoring import ScoringResult
from eval_task import RunArtifacts, RunOptions, clean_env, create_temp_repo


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


SKILL_MAX_TURNS = 20
SKILL_MAX_BUDGET = 1.0

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "claude" / "skills"

_PROMPT = """You are working in a Claude Code session. The repository is at {repo_dir}.

The following skill has been invoked for this request. Follow it exactly.

--- BEGIN SKILL ---
{skill}
--- END SKILL ---

User request: {request}"""


def skill_body(name: str) -> str:
    """The SKILL.md body for `name`, with its YAML frontmatter stripped.

    Read live from ai/claude/skills/ rather than copied into a case: the file is
    the single source of truth, and a copy would let a case keep passing against
    a skill that no longer says what the copy says.

    The frontmatter is routing metadata — trigger, skip, invocation — that a real
    session uses to decide whether to load the skill, not instructions it follows
    once loaded. Including it would grade the model on text it never sees.
    """
    path = _SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"no SKILL.md for skill {name!r}: {path}")
    text = path.read_text()
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else text


class SkillTask:
    """Drive a SKILL.md against a fixture and grade the commands it issued."""

    name = "skill"

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        manifest = json.loads((case_dir / "manifest.json").read_text())
        if "skill" not in manifest:
            raise ValueError(f"{case_dir}: manifest missing 'skill'")
        if "prompt" not in manifest:
            raise ValueError(f"{case_dir}: manifest missing 'prompt'")
        # Resolved before either temp dir exists: a bad skill name or a
        # malformed responses.json below would otherwise leak a git repo and a
        # work dir on every raise, since the runner's cleanup only covers the
        # code after run() returns (ai/claude/bin/eval-models:221-232).
        skill = skill_body(manifest["skill"])
        prompt = manifest["prompt"]

        repo_dir = create_temp_repo(str(case_dir / "src"), prefix="eval-skill-")
        work_dir = Path(tempfile.mkdtemp(prefix="eval-skill-work-"))
        try:
            trace_file = work_dir / "trace.jsonl"
            session_log = str(work_dir / "session.jsonl")
            bin_dir = work_dir / "bin"

            responses_path = case_dir / "responses.json"
            responses = (
                json.loads(responses_path.read_text()) if responses_path.is_file() else {}
            )
            write_shims(responses, bin_dir, case_dir, trace_file)

            env = clean_env()
            env["PATH"] = os.pathsep.join(
                p for p in (str(bin_dir), env.get("PATH", "")) if p
            )

            rc = ai_backend.invoke_fix(ai_backend.AgentInvocation(
                prompt=_PROMPT.format(repo_dir=repo_dir, skill=skill, request=prompt),
                cwd=repo_dir,
                session_log=session_log,
                add_dirs=[repo_dir],
                max_turns=SKILL_MAX_TURNS,
                max_budget=SKILL_MAX_BUDGET,
                model=opts.model or "",
                env=env,
                task="eval-skill",
                repo="eval/corpus",
            ))

            lines = load_trace(str(trace_file))
            matches = match_required(manifest.get("requires", []), lines)
            violations = match_forbidden(manifest.get("forbids", []), lines)
            satisfied = sum(1 for m in matches if m.matched)
        except Exception:
            # A raise past this point leaves both temp dirs behind: the
            # runner's own cleanup only runs once run() has already returned
            # artifacts naming them (ai/claude/bin/eval-models:221-232).
            shutil.rmtree(repo_dir, ignore_errors=True)
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

        return RunArtifacts(
            exit_code=rc,
            usage=ai_usage.parse_session_log(session_log),
            temp_dirs=[repo_dir, str(work_dir)],
            data={
                "matches": matches,
                "violations": violations,
                "summary": (
                    f"{satisfied}/{len(matches)} required, "
                    f"{len(violations)} forbidden"
                ),
            },
        )

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        matches = artifacts.data.get("matches", [])
        violations = artifacts.data.get("violations", [])
        satisfied = sum(1 for m in matches if m.matched)
        usage = artifacts.usage
        return ScoringResult(
            entry_name="", model="", run_index=0,
            matches=matches,
            false_positive_ids=violations,
            # 0.0 is a floor for a manifest the corpus rejects, not a score any
            # shipped case can earn: Task 5's corpus guard asserts `requires`
            # is non-empty, so `matches` is never empty for a real case.
            recall=satisfied / len(matches) if matches else 0.0,
            # Binary on purpose: a run that broke a constraint does not get
            # graded on how few it broke. severity_accuracy stays at its zero
            # default — it has no meaning for this task.
            precision=0.0 if violations else 1.0,
            false_positive_count=len(violations),
            false_positive_ok=len(violations) <= manifest.get("false_positives_max", 0),
            cost_usd=usage.cost,
            duration_ms=usage.duration_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            billed_input=usage.billed_input,
            cache_read_ratio=usage.cache_read_ratio,
        )
