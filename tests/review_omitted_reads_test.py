"""Omitted-file guidance is role-aware and the only omitted-file instruction."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.phases import Mode, Phase
from review.budget import fit_files
from review.prompt_sections import (
    OmittedReads, _build_env_section, _build_omitted_guidance,
)
from review.registry import build_prompt

from review_contracts_support import AGENTS_DIR, TEMPLATE_DIR
from review_prompt_support import _make_job, _make_preflight

_WRITE_FIRST = "do not investigate before that first write"
_READ_SENTENCE = "Then, as your first investigation"
_GONE = (
    "First, read all files",
    "read a file when a finding depends on what surrounds the change",
    "(read directly)",
    "use Read only for files NOT in the PR",
    "read only those critical",
    "must be read directly from this path",
    "Read every file listed under",
)
_GROUP_EXTRA = {
    "group_idx": 1, "group_count": 1, "group_name": "core",
    "group_files_formatted": "  - a.py",
    "group_file_paths": ["a.py"],
}
_SYNTH_EXTRA = {"group_count": 2, "merged_content": "x"}


def _dropped(preflight):
    return fit_files(preflight.file_contents, preflight.file_permissions, 0)


def _turn_budget(prompt: str) -> str:
    start = prompt.index("## Turn budget")
    return prompt[start:].split("\n\n", 1)[0]


@pytest.mark.parametrize("reads", list(OmittedReads))
@pytest.mark.parametrize("skip", [False, True])
@pytest.mark.parametrize("dropped", [False, True])
def test_guidance_is_one_wording_per_role_and_skip(reads, skip, dropped):
    pf = _make_preflight(omitted_files=["big.go"])
    files = _dropped(pf) if dropped else None
    text = _build_omitted_guidance(
        pf, skip_omitted=skip, files=files, reads=reads,
    )
    if skip and not dropped and reads is not OmittedReads.REVIEW:
        assert text == ""
        return
    if reads is OmittedReads.REVIEW:
        assert "File Triage" in text
    else:
        assert "File Triage" not in text
    if reads is OmittedReads.SURVEY:
        assert "read only those" in text
    else:
        assert "read only those" not in text
    if skip and not dropped:
        assert "not reviewed at this effort level" in text
    else:
        assert "not reviewed at this effort level" not in text
        if reads is not OmittedReads.SURVEY:
            assert "first investigation" in text


def test_self_review_turn_budget_reads_without_triage():
    job = _make_job(
        _make_preflight(omitted_files=["big.go"]), mode=Mode.SELF,
    )
    prompt = build_prompt(Phase.SINGLE, job, max_turns=15)
    budget = _turn_budget(prompt)
    assert "File Triage" not in budget
    assert "first investigation" in budget
    assert "Files not pre-collected" in budget


@pytest.mark.parametrize("phase", [Phase.SCOUT, Phase.HOLISTIC])
def test_survey_prompts_read_only_those_without_triage(phase):
    job = _make_job(_make_preflight(omitted_files=["big.go"]))
    prompt = build_prompt(phase, job, max_turns=15)
    assert "File Triage" not in prompt
    assert prompt.count("read only those") == 1


@pytest.mark.parametrize(
    "phase,mode,extra",
    [
        (Phase.SINGLE, Mode.PR, {}),
        (Phase.GROUP, Mode.PR, _GROUP_EXTRA),
        (Phase.SINGLE, Mode.SELF, {}),
    ],
)
def test_omitted_read_comes_after_write_first(phase, mode, extra):
    job = _make_job(
        _make_preflight(omitted_files=["big.go"]), mode=mode,
    )
    prompt = build_prompt(phase, job, max_turns=15, **extra)
    assert prompt.index(_WRITE_FIRST) < prompt.index(_READ_SENTENCE)


def test_old_omitted_phrases_are_gone():
    blobs = [path.read_text() for path in sorted(TEMPLATE_DIR.glob("*.md"))]
    blobs += [path.read_text() for path in sorted(AGENTS_DIR.glob("reviewer*.md"))]
    job = _make_job(_make_preflight(omitted_files=["big.go"]))
    extras = {
        Phase.SINGLE: {},
        Phase.GROUP: _GROUP_EXTRA,
        Phase.SCOUT: {},
        Phase.HOLISTIC: {},
        Phase.SYNTHESIS: _SYNTH_EXTRA,
    }
    blobs += [
        build_prompt(phase, job, max_turns=15, **extra)
        for phase, extra in extras.items()
    ]
    combined = "\n".join(blobs)
    for phrase in _GONE:
        assert phrase not in combined


def test_templates_do_not_name_files_not_pre_collected():
    for path in sorted(TEMPLATE_DIR.glob("*.md")):
        assert "Files not pre-collected" not in path.read_text()


@pytest.mark.parametrize("drop_all", [True, False], ids=["dropped-everything", "partial"])
def test_env_section_only_says_where_omitted_files_are(drop_all):
    """Neither omission branch of the env section instructs a read.

    `_build_omitted_guidance` owns what a phase does with omitted files; the
    env section is phase-blind, so it may only say where they live. A fit that
    drops every file takes the dropped-everything branch; a preflight with a
    never-collected file and no fit takes the partial one.
    """
    pf = _make_preflight(file_contents={"a.py": "x"}, omitted_files=["big.go"])
    env = _build_env_section("/wt", pf, files=_dropped(pf) if drop_all else None)
    if drop_all:
        assert "file contents are not" in env
    else:
        assert "some file contents are pre-collected" in env
    assert 'Files listed under "Files not pre-collected" are at this path.' in env
    assert "Read every file" not in env
