"""Tests for reading session transcripts across harnesses.

That the shell and Python sides agree on discovery and slugs is asserted
separately, in tests/sessions_ssot.bats. What is exercised here is the part
that has no shell twin: parsing two harnesses' record shapes into one, telling
a human turn from a pipeline's, and dating a turn from its own record.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

from core import sessions  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────────


def write_transcript(home: Path, harness: str, slug: str, name: str, records: list[dict]) -> Path:
    roots = {
        "claude": Path(".claude") / "projects",
        "pi": Path(".pi") / "agent" / "sessions",
    }
    session_dir = home / roots[harness] / slug
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def claude_user(text: str, when: str = "2026-09-16T14:30:00.000Z", cwd: str = "/repo") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text},
            "timestamp": when, "cwd": cwd}


def pi_user(text: str, when: str = "2026-09-16T14:30:00.000Z") -> dict:
    return {
        "type": "message",
        "timestamp": when,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
            "timestamp": 1789575000000,
        },
    }


def messages_of(path: Path, harness: str) -> list[sessions.UserMessage]:
    return list(sessions.iter_user_messages(sessions.Session(path=path, harness=harness)))


# ── Record shapes ────────────────────────────────────────────────────────────


def test_reads_claude_string_content(tmp_path):
    p = write_transcript(tmp_path, "claude", "-repo", "s", [claude_user("fix the failing test")])
    assert [m.text for m in messages_of(p, "claude")] == ["fix the failing test"]


def test_reads_claude_block_content(tmp_path):
    record = {
        "type": "user",
        "timestamp": "2026-09-16T14:30:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": "rebase onto main"}]},
    }
    p = write_transcript(tmp_path, "claude", "-repo", "s", [record])
    assert [m.text for m in messages_of(p, "claude")] == ["rebase onto main"]


def test_reads_pi_block_content(tmp_path):
    p = write_transcript(tmp_path, "pi", "--repo--", "s", [pi_user("dream needs to work with pi")])
    assert [m.text for m in messages_of(p, "pi")] == ["dream needs to work with pi"]


def test_ignores_assistant_and_tool_turns(tmp_path):
    records = [
        pi_user("the only human turn here"),
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "a reply"}]}},
        {"type": "message", "message": {"role": "toolResult", "content": [{"type": "text", "text": "output"}]}},
    ]
    p = write_transcript(tmp_path, "pi", "--repo--", "s", records)
    assert [m.text for m in messages_of(p, "pi")] == ["the only human turn here"]


def test_tolerates_malformed_lines(tmp_path):
    session_dir = tmp_path / ".pi/agent/sessions/--repo--"
    session_dir.mkdir(parents=True)
    path = session_dir / "s.jsonl"
    path.write_text("not json\n" + json.dumps(pi_user("a real prompt here")) + "\n\n")
    assert [m.text for m in messages_of(path, "pi")] == ["a real prompt here"]


def test_missing_file_yields_nothing(tmp_path):
    assert messages_of(tmp_path / "gone.jsonl", "pi") == []


# ── Automation filtering ─────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "### Project context\n\n#### CLAUDE.md",
    "You are an adversarial reviewer. Your job is to FALSIFY",
    "You are completing the final self-review of changes on branch x",
    "You are resolving merge conflicts during a git rebase onto origin/main",
    "You are a code review triage assistant. Analyze these PR review comments",
    "Self-review of changes on branch isaac/feat/x in otto-workbench",
    "Fix review findings for branch isaac/feat/x",
    "IMPORTANT: A previous attempt ran out of turns reading files",
    "This is a RETRY of a prior fix pass. The tracking file below",
    "<command-name>/dream</command-name>",
    "<skill name=\"pr-rebase\" location=\"/Users/x\">",
    "<system-reminder>something</system-reminder>",
    "Caveat: The messages below were generated",
    "Job finished: re-run self-review at new HEAD",
    # A slash-command body. Pi expands one into a plain user turn with no
    # marker of any kind, and the bodies live in each repo rather than here.
    "# Address PR Review Comments\n\nAddress review comments on the current branch",
    "# Update Config Skill\n\nWalk the config surface and update the skill",
])
def test_rejects_automation_prompts(text):
    assert sessions.is_automation_prompt(text)


@pytest.mark.parametrize("text", [
    "dream needs to work with pi",
    "prioritize ssot and better design patterns",
    "can we do everything now? no follow-ups",
    "why did the reviewer say 'You are an adversarial reviewer' in that thread?",
    "the ### Project context block is too long, trim it",
    # Only the opening line is structural: a turn discussing or quoting a
    # heading is a real turn.
    "rename the heading to # Address PR Review Comments and re-run",
    "the doc needs a title:\n\n# Session Handling\n\nwhat do you think?",
])
def test_keeps_human_prompts(text):
    assert not sessions.is_automation_prompt(text)


# ── Coverage of the prompt templates ─────────────────────────────────────────


def _rendered_openings() -> list[tuple[str, str]]:
    """The first non-empty line of every prompt template, name and text.

    Placeholders are filled with something innocuous so a template opening with
    a substituted value is judged on the prose around it rather than on a bare
    `${branch_name}` no session would ever contain.
    """
    from string import Template

    template_dir = REPO_ROOT / "ai" / "lib" / "review-templates"
    filled = {
        "branch_name": "isaac/feat/x",
        "repo": "otto-nation/otto-workbench",
        "pr_number": "1234",
        # group.md opens with two substituted blocks rather than prose. The
        # holistic one is empty whenever there was no scout pass, so the first
        # line an agent actually receives is the project context header that
        # review.collect builds — which is itself a covered prefix.
        "holistic_block": "",
        "project_context": "### Project context",
    }
    return [
        (path.name, _first_line(Template(path.read_text()).safe_substitute(**filled)))
        for path in sorted(template_dir.glob("*.md"))
    ]


def _first_line(body: str) -> str:
    """The first line with anything on it, which is what an agent reads first."""
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def test_every_prompt_template_is_recognised_as_automation():
    """A template whose opening no prefix matches reaches dream as a signal.

    The list in AUTOMATION_PREFIXES is hand-written because core cannot import
    the layer that renders these. This is what keeps it honest: six templates
    were uncovered when the check was added, and `pr comments --fix` preambles
    were being classified as corrections because "Fix ... comment suggestions"
    contains no pattern but the surrounding text did.
    """
    uncovered = [
        name for name, opening in _rendered_openings()
        if not sessions.is_automation_prompt(opening)
    ]
    assert not uncovered, (
        f"prompt templates not covered by AUTOMATION_PREFIXES: {uncovered}. "
        "Add a prefix for each in ai/lib/core/sessions.py."
    )


def test_template_openings_are_not_matched_by_accident():
    """Every prefix must earn its place by matching a template or a known form.

    Guards the other direction: a prefix broad enough to match anything would
    pass the coverage test above while dropping human turns.
    """
    assert not sessions.is_automation_prompt("Fix the flaky test in push_test.py")
    assert not sessions.is_automation_prompt("Review PR feedback with me before I reply")


def test_quoting_a_preamble_mid_sentence_survives(tmp_path):
    """Prefix matching, not substring: a human discussing a preamble is a turn."""
    quoted = "our agents open with 'You are an adversarial reviewer' which is too blunt"
    p = write_transcript(tmp_path, "pi", "--repo--", "s", [pi_user(quoted)])
    assert [m.text for m in messages_of(p, "pi")] == [quoted]


def test_automation_is_dropped_from_a_transcript(tmp_path):
    records = [
        pi_user("### Project context\n\n#### CLAUDE.md\n\n# otto-workbench"),
        pi_user("actually lets do it the other way"),
    ]
    p = write_transcript(tmp_path, "pi", "--repo--", "s", records)
    assert [m.text for m in messages_of(p, "pi")] == ["actually lets do it the other way"]


def test_drops_turns_too_short_to_carry_signal(tmp_path):
    records = [pi_user("ok"), pi_user("yes"), pi_user("this one is long enough to keep")]
    p = write_transcript(tmp_path, "pi", "--repo--", "s", records)
    assert [m.text for m in messages_of(p, "pi")] == ["this one is long enough to keep"]


# ── Dating ───────────────────────────────────────────────────────────────────


def test_dates_come_from_the_record_not_the_file(tmp_path):
    """A session spanning midnight dates each turn to when it was typed.

    Dating by file mtime — what dream-scan did before this module — gave every
    message in a session the date the session last ended, and dream weighs a
    signal by how many distinct dates mention it.

    The two turns straddle midnight *locally*, computed rather than written as a
    UTC literal: a fixed pair of UTC instants falls on one local date or two
    depending on the machine's offset, so a literal would pass here and fail in
    another timezone.
    """
    from datetime import timedelta

    midnight = (datetime.now() + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    before = (midnight - timedelta(minutes=10)).astimezone()
    after = (midnight + timedelta(minutes=10)).astimezone()

    records = [
        pi_user("before midnight", when=before.isoformat()),
        pi_user("after midnight", when=after.isoformat()),
    ]
    p = write_transcript(tmp_path, "pi", "--repo--", "s", records)
    dates = {m.date for m in messages_of(p, "pi")}
    assert len(dates) == 2


def test_utc_instants_are_dated_by_local_calendar_day(tmp_path):
    """Grouping is by the date the user experienced, not the UTC date.

    These two instants are 20 minutes apart across UTC midnight. West of
    Greenwich they are the same evening, and counting them as two dates would
    overstate how many separate days mentioned a signal.
    """
    records = [
        pi_user("before utc midnight", when="2026-09-15T23:50:00.000Z"),
        pi_user("after utc midnight", when="2026-09-16T00:10:00.000Z"),
    ]
    p = write_transcript(tmp_path, "pi", "--repo--", "s", records)
    expected = {
        sessions._parse_iso("2026-09-15T23:50:00.000Z").strftime("%Y-%m-%d"),
        sessions._parse_iso("2026-09-16T00:10:00.000Z").strftime("%Y-%m-%d"),
    }
    assert {m.date for m in messages_of(p, "pi")} == expected


def test_claude_iso_timestamp_is_read(tmp_path):
    p = write_transcript(tmp_path, "claude", "-repo", "s",
                         [claude_user("a prompt here", when="2026-03-04T12:00:00.000Z")])
    assert messages_of(p, "claude")[0].when.year == 2026


def test_falls_back_to_mtime_when_a_record_has_no_timestamp(tmp_path):
    record = {"type": "message", "message": {"role": "user",
              "content": [{"type": "text", "text": "no timestamp anywhere"}]}}
    p = write_transcript(tmp_path, "pi", "--repo--", "s", [record])
    assert isinstance(messages_of(p, "pi")[0].when, datetime)


# ── Project path ─────────────────────────────────────────────────────────────


def test_project_path_read_from_pi_session_record(tmp_path):
    records = [{"type": "session", "cwd": "/Users/dev/git/repo"}, pi_user("a prompt here")]
    p = write_transcript(tmp_path, "pi", "--repo--", "s", records)
    session = sessions.Session(path=p, harness="pi")
    assert sessions.project_path_of(session) == Path("/Users/dev/git/repo")


def test_project_path_read_from_claude_record(tmp_path):
    p = write_transcript(tmp_path, "claude", "-repo", "s",
                         [claude_user("a prompt here", cwd="/Users/dev/git/other")])
    session = sessions.Session(path=p, harness="claude")
    assert sessions.project_path_of(session) == Path("/Users/dev/git/other")


def test_project_path_is_not_guessed_from_an_ambiguous_slug(tmp_path):
    """The slug is written, never read.

    Claude's transform maps `_` and `-` to the same character, so this directory
    name has no single correct decoding. The cwd on the record is the only
    answer, and its absence must read as unknown rather than as a guess.
    """
    record = {"type": "message", "message": {"role": "user",
              "content": [{"type": "text", "text": "a prompt with no cwd"}]}}
    p = write_transcript(tmp_path, "claude", "-Users-dev-git-a-b", "s", [record])
    session = sessions.Session(path=p, harness="claude")
    assert sessions.project_path_of(session) is None


# ── Discovery ────────────────────────────────────────────────────────────────


def test_discovers_both_harnesses(tmp_path):
    write_transcript(tmp_path, "claude", "-repo", "a", [claude_user("one prompt here")])
    write_transcript(tmp_path, "pi", "--repo--", "b", [pi_user("another prompt here")])
    assert len(sessions.discover_sessions(tmp_path)) == 2


def test_session_counts_are_reported_per_harness(tmp_path):
    write_transcript(tmp_path, "claude", "-repo", "a", [claude_user("one prompt here")])
    write_transcript(tmp_path, "pi", "--repo--", "b", [pi_user("two prompt here")])
    write_transcript(tmp_path, "pi", "--repo--", "c", [pi_user("three prompt here")])
    assert sessions.session_counts(tmp_path) == {"claude": 1, "pi": 2}


def test_a_harness_that_stops_being_discovered_reads_as_zero(tmp_path):
    """The visible failure mode the layout ceiling names."""
    write_transcript(tmp_path, "claude", "-repo", "a", [claude_user("one prompt here")])
    assert sessions.session_counts(tmp_path)["pi"] == 0


def test_pi_flat_subagent_files_are_not_sessions(tmp_path):
    write_transcript(tmp_path, "pi", "--repo--", "real", [pi_user("a real prompt")])
    flat = tmp_path / ".pi/agent/sessions/subagent-123.jsonl"
    flat.write_text("{}\n")
    assert len(sessions.discover_sessions(tmp_path)) == 1


def test_since_filters_on_modification_time(tmp_path):
    import os
    import time
    p = write_transcript(tmp_path, "pi", "--repo--", "old", [pi_user("an old prompt")])
    old = time.time() - 60 * 60 * 24 * 30
    os.utime(p, (old, old))
    write_transcript(tmp_path, "pi", "--repo--", "new", [pi_user("a new prompt")])
    recent = datetime.fromtimestamp(time.time() - 60 * 60 * 24)
    assert len(sessions.discover_sessions(tmp_path, since=recent)) == 1
