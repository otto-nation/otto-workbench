"""Omitted-file guidance is role-aware: only triage phases triage."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.phases import Mode, Phase
from review.budget import fit_files
from review.prompt_sections import OmittedReads, _build_omitted_guidance
from review.registry import build_prompt

from review_prompt_support import _make_job, _make_preflight


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
