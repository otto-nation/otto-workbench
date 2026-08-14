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


def match_tokens(argv: list[str]) -> set[str]:
    """The tokens a group may match against on one recorded line.

    Every argv element, plus — for an element holding an `=` — the two halves
    around the first one. `--track=T-3` is one element on the wire but two
    tokens to a manifest, and a session that wrote the joined form did not do
    anything different from one that wrote `--track T-3`. The element itself
    stays in the set, so a group naming the literal `--track=T-3` still works.

    The split cannot resurrect either failure exact matching fixes: neither
    `--push` nor `pr-rebase` contains an `=`, so neither gains a token here.

    An empty half is dropped. The harness issues `-c core.fsmonitor=` at
    startup, and an empty token in the set would make a malformed group like
    `["git", ""]` fire on that line — turning a manifest typo into a forbid
    that always matches instead of one that never does.
    """
    tokens = set(argv)
    for element in argv:
        head, sep, tail = element.partition("=")
        if sep:
            tokens.update(half for half in (head, tail) if half)
    return tokens


def check_group(label: str, group: object) -> None:
    """Reject a token group that is not a non-empty list of strings.

    A group written one level too shallow — `["--post"]` where `[["--post"]]`
    was meant — is not a type error to any code downstream: matching iterates
    the string `"--post"` character by character, so each "group" is a
    one-character string whose own iteration yields characters that are never
    argv elements. The rule can then never fire. Nothing else reports it — a
    `requires` typo at least surfaces as recall 0.0, but a `forbids` typo
    surfaces as a gate that was silently never armed.

    An empty group is the same failure reached by a different typo, so it is
    rejected here rather than merely never matched. It is not harmless in a
    shim rule either: under `passthrough` a rule whose `match` came out empty
    stops intercepting and hands the call to the real binary, which is how a
    `git` stub meant to refuse a push would quietly perform one.
    """
    if not isinstance(group, list) or not all(isinstance(t, str) for t in group):
        raise ValueError(f"{label} must be a list of strings, got {group!r}")
    if not group:
        raise ValueError(f"{label} must not be empty: {group!r}")


def check_groups(label: str, groups: object) -> None:
    """Reject a `requires`/`forbids` value that is not a list of valid groups."""
    if not isinstance(groups, list):
        raise ValueError(f"{label} must be a list of token groups, got {groups!r}")
    for group in groups:
        check_group(f"{label} group", group)


def group_matches(group: list[str], argv: list[str]) -> bool:
    """True when every token in `group` matches one of `argv`'s elements.

    Exact elements, not substrings. Substring matching cannot tell a subcommand
    from a flag that merely contains it — the harness issues
    `git remote get-url --push origin` at session startup, which a `["git",
    "push"]` forbids group matched as a substring, zeroing precision on a
    session that never pushed. It also cannot tell a command from a lookalike:
    `["pr", "rebase"]` sat inside the single word `pr-rebase`.

    An empty group is never a match: `all([])` is True, and an empty `forbids`
    entry would then fail every run. `check_group` rejects one before it ever
    reaches here; this guard keeps the matcher safe for callers that skipped
    validation, which the tests do deliberately.
    """
    if not group:
        return False
    tokens = match_tokens(argv)
    return all(token in tokens for token in group)


def _first_match_from(group: list[str], lines: list[list[str]], start: int) -> int:
    """The index of the first line at or after `start` that `group` matches.

    `-1` when no such line exists, mirroring `str.find`'s failure value.
    """
    for i in range(start, len(lines)):
        if group_matches(group, lines[i]):
            return i
    return -1


def match_required(groups: list[list[str]], lines: list[list[str]]) -> list[TraceMatch]:
    """Match each group against a later line than the group before it.

    Ordering is the point. "Drafted, then published" is a claim about sequence;
    both commands merely appearing somewhere in the trace is not evidence for it.

    The satisfying line is recorded space-joined: matching reads argv elements,
    but run reports and baselines read the finding id, and those stay text.
    """
    matches: list[TraceMatch] = []
    start = 0
    for group in groups:
        i = _first_match_from(group, lines, start)
        if i == -1:
            matches.append(TraceMatch(pattern=tuple(group)))
            continue
        matches.append(TraceMatch(tuple(group), True, " ".join(lines[i])))
        start = i + 1
    return matches


def match_forbidden(groups: list[list[str]], lines: list[list[str]]) -> list[str]:
    """The joined text of every group that fired, at most once per group.

    Two calls that break one rule are one broken rule. Counting them twice
    would let a retry loop dominate the false-positive figure.
    """
    return [
        " ".join(group)
        for group in groups
        if any(group_matches(group, line) for line in lines)
    ]


def load_trace(trace_file: str) -> list[list[str]]:
    """The recorded argv lines, one list of elements per line.

    Kept as lists rather than joined: matching compares argv elements, and
    joining first would erase the element boundaries it needs.

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
            lines.append([str(part) for part in argv])
    return lines


# A distinctive code, not 1: an unanticipated call is a fixture gap, and it
# should not read like the stubbed command reporting an ordinary failure.
NO_MATCH_EXIT = 97

# The only two policies the shim implements. A typo silently reads as "fail",
# which is the safe direction but the wrong one: a stub meant to passthrough
# would exit 97 on every call it has no rule for, and the case fails as a
# scenario bug rather than as the fixture typo it is.
POLICIES = ("fail", "passthrough")

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

# The same tokens match_tokens builds, over the same normalized argv that was
# recorded, so a rule and a manifest group mean the same thing on the same line.
TOKENS = set(argv)
for element in argv:
    head, sep, tail = element.partition("=")
    if sep:
        TOKENS.update(half for half in (head, tail) if half)

for rule in RULES:
    # Whole tokens, not substrings. A substring rule fired on flags that merely
    # contained the subcommand it named: a rule matching ["push"] intercepted
    # the harness's own `git remote get-url --push origin`.
    # An empty match is never a match, mirroring group_matches: all([]) is
    # True, and an empty list would otherwise fire on every call. write_shims
    # rejects one before generating this file; the guard stays because the
    # generated file cannot import the validator that made that true.
    if rule["match"] and all(token in TOKENS for token in rule["match"]):
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))

if ON_NO_MATCH == "passthrough":
    here = os.path.dirname(os.path.abspath(__file__))
    os.environ["PATH"] = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep) if p != here
    )
    os.execvp(NAME, argv)

sys.stderr.write("eval shim: no rule for: " + " ".join(argv) + "\\n")
sys.exit(NO_MATCH_EXIT)
'''


def _resolve_rules(name: str, rules: list[dict], case_dir: Path) -> list[dict]:
    """Inline every `stdout_file` so the shim never reads the case directory.

    A rule with no `match` key is a malformed fixture, not a catch-all. A
    `match` that is empty, or that is not a list of strings, is the same class
    of fixture bug in the other direction: `"push"` explodes to
    `['p','u','s','h']`, a rule that can never fire. To stub a binary purely so
    its calls are traced, give it no rules at all rather than an empty one.
    """
    resolved = []
    for rule in rules:
        if "match" not in rule:
            raise ValueError(f"{name}: rule missing 'match': {rule!r}")
        check_group(f"{name}: rule 'match'", rule["match"])
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
        policy = spec.get("on_no_match", "fail")
        if policy not in POLICIES:
            raise ValueError(
                f"{name}: on_no_match must be one of {POLICIES}, got {policy!r}")
        shim = bin_dir / name
        shim.write_text(_SHIM.format(
            name=name,
            trace=str(trace_file),
            rules=_resolve_rules(name, spec.get("rules", []), case_dir),
            policy=policy,
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
        # Checked here rather than where they are matched: a malformed group
        # costs a whole paid run before it reports anything at match time, and
        # a malformed `forbids` group reports nothing even then.
        check_groups(f"{case_dir}: requires", manifest.get("requires", []))
        check_groups(f"{case_dir}: forbids", manifest.get("forbids", []))
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
