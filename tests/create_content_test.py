"""Tests for `pr.create_content` — title and body for a new PR.

Ported from `tests/pr_body_template.bats` plus the D1/D3/D4/D6/D7 deltas.
Bash stays until PR 3; these pin the Python owner to the same acceptance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from conftest import git_in, init_repo, run_checked

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import core.pr_template  # noqa: E402
from agent.invoke import PromptResult  # noqa: E402
from core.phases import Phase  # noqa: E402
from core.pr_template import PRTemplate  # noqa: E402
from pr.create_content import (  # noqa: E402
    ContentError, ContentRequest, extract, generate, missing_sections,
    resolve_issue, template_refusal,
)


# ── helpers ─────────────────────────────────────────────────────────────────


_WHAT_WHY = "## What\n\n## Why\n"
_ON_TEMPLATE = "## What\n\ndid a thing\n\n## Why\n\nit was broken"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _repo(tmp_path: Path) -> Path:
    """A one-commit `main`, with `origin` a bare repo, pushed."""
    remote = tmp_path / "remote.git"
    run_checked(["git", "init", "-q", "--bare", "-b", "main", str(remote)])
    wt = init_repo(tmp_path / "wt")
    git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", "init")
    git_in(wt, "remote", "add", "origin", str(remote))
    git_in(wt, "push", "-q", "-u", "origin", "main")
    return wt


def _feature(wt: Path, name: str = "feat/work") -> None:
    git_in(wt, "checkout", "-q", "-b", name)


def _empty(wt: Path, message: str) -> None:
    git_in(wt, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)


def _file_commit(wt: Path, rel: str, content: str, message: str) -> None:
    path = wt / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git_in(wt, "add", "--", rel)
    git_in(wt, "commit", "-q", "--no-verify", "-m", message)


def _req(**kw) -> ContentRequest:
    values = dict(
        branch="feat/work", base="main", issue="", title="", body="", closes=(),
    )
    values.update(kw)
    return ContentRequest(**values)


def _desc(body: str) -> str:
    return f"<<<DESCRIPTION>>>\n{body}\n<<<END_DESCRIPTION>>>"


def _both(title: str, body: str) -> str:
    return (
        f"<<<TITLE>>>\n{title}\n<<<END_TITLE>>>\n"
        f"{_desc(body)}"
    )


def _stub(monkeypatch, text: str = "", *, ok: bool = True, record: list | None = None):
    """Replace `agent.invoke.run_prompt`. `record` collects the prompt text."""
    calls = record if record is not None else []

    def run_prompt(phase, prompt, **kwargs):
        calls.append({"phase": phase, "prompt": prompt, "kwargs": kwargs})
        if not ok:
            return PromptResult("", 1, False)
        return PromptResult(text, 0, kwargs["usable"](text))

    monkeypatch.setattr(agent.invoke, "run_prompt", run_prompt)
    return calls


def _generate(wt: Path, req: ContentRequest, log=None, repo: str = "acme/widget"):
    return generate(wt, req, repo=repo, log=log or (lambda _: None))


# ── extract (D6) ────────────────────────────────────────────────────────────


def test_extract_returns_none_when_the_end_marker_is_missing():
    assert extract("<<<TITLE>>>\nfeat: x\n", "TITLE") is None


def test_extract_strips_the_span_between_markers():
    assert extract("<<<TITLE>>>\n  feat: x  \n<<<END_TITLE>>>", "TITLE") == "feat: x"


# ── resolve_issue ───────────────────────────────────────────────────────────


def test_resolve_issue_prefers_the_override():
    assert resolve_issue("isaac/ENG-123/thing", "PROJ-9") == "PROJ-9"


def test_resolve_issue_scrapes_the_first_tracker_key():
    assert resolve_issue("isaac/ENG-123/thing", "") == "ENG-123"


def test_resolve_issue_is_empty_when_nothing_matches():
    assert resolve_issue("isaac/feat/thing", "") == ""


# ── missing_sections / template_refusal (bats) ──────────────────────────────


def _tpl(text: str, path: str = ".github/PULL_REQUEST_TEMPLATE.md") -> PRTemplate:
    return PRTemplate(text=text, path=path)


def test_a_body_carrying_the_template_sections_is_accepted():
    assert missing_sections(_tpl(_WHAT_WHY), _ON_TEMPLATE) == []


def test_a_body_using_its_own_headers_is_refused():
    missing = missing_sections(_tpl(_WHAT_WHY), "## Summary\n\n## Changes\n\n## Testing")
    assert missing == ["## What", "## Why"]


def test_the_refusal_names_every_missing_section():
    message = template_refusal(_tpl(_WHAT_WHY), ["## What", "## Why"])
    assert "## What" in message
    assert "## Why" in message
    assert "does not use this repo's template" in message


def test_the_refusal_names_only_the_section_that_is_missing():
    missing = missing_sections(_tpl(_WHAT_WHY), "## What\n\nhalf of it")
    assert missing == ["## Why"]
    message = template_refusal(_tpl(_WHAT_WHY, "docs/pull_request_template.md"), missing)
    assert "## Why" in message
    assert "Missing section(s) from docs/pull_request_template.md:" in message
    assert "Missing section(s)" in message and "## What" not in message.split("Missing section(s)", 1)[1]


def test_a_template_header_survives_a_backslash_elsewhere_in_the_file():
    text = "## What\n\nuse C:\\path or \\n in your description\n\n## Why\n"
    assert missing_sections(_tpl(text), "## Summary") == ["## What", "## Why"]


def test_no_template_accepts_any_body():
    assert missing_sections(_tpl("## Summary\n", path=""), "## Anything At All") == []


def test_extra_sections_beyond_the_template_are_allowed():
    assert missing_sections(_tpl(_WHAT_WHY), "## What\n\n## Why\n\n## Testing") == []


def test_a_header_appearing_only_inside_prose_satisfies_the_template():
    """Substring match, matching bash: a prose mention counts."""
    body = "## What\n\ntalked about ## Why without having one"
    assert missing_sections(_tpl(_WHAT_WHY), body) == []


# ── generate: template discovery ────────────────────────────────────────────


def test_the_template_is_found_from_a_subdirectory(tmp_path):
    wt = init_repo(tmp_path / "wt")
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    deep = wt / "lib" / "deep"
    deep.mkdir(parents=True)
    with pytest.raises(ContentError) as excinfo:
        _generate(deep, _req(title="fix: thing", body="## Summary\noff template"))
    assert "does not use this repo's template" in str(excinfo.value)


def test_the_template_is_found_under_an_exported_git_dir(tmp_path, monkeypatch):
    wt = init_repo(tmp_path / "wt")
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    deep = wt / "lib" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.setenv("GIT_DIR", str(wt / ".git"))
    with pytest.raises(ContentError) as excinfo:
        _generate(deep, _req(title="fix: thing", body="## Summary\noff template"))
    assert "does not use this repo's template" in str(excinfo.value)


def test_a_docs_template_is_named_in_the_refusal(tmp_path):
    wt = init_repo(tmp_path / "wt")
    _write(wt, "docs/pull_request_template.md", "## Context\n\n## Risk\n")
    with pytest.raises(ContentError) as excinfo:
        _generate(wt, _req(title="fix: thing", body="## Summary\noff template"))
    assert "docs/pull_request_template.md" in str(excinfo.value)


def test_a_repo_with_no_template_accepts_any_body(tmp_path, monkeypatch):
    wt = init_repo(tmp_path / "wt")
    calls = _stub(monkeypatch, ok=False)
    result = _generate(wt, _req(title="fix: thing", body="## Anything At All"))
    assert result.title == "fix: thing"
    assert result.body == "## Anything At All"
    assert calls == []


def test_an_empty_template_file_is_found_with_empty_text(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _write(wt, ".github/pull_request_template.md", "")
    _feature(wt)
    _empty(wt, "feat: one")
    calls = _stub(monkeypatch, _desc("filled"))
    result = _generate(wt, _req())
    assert calls, "empty file is found, so the single-commit path still asks AI"
    assert result.body == "filled"


def test_a_resolver_that_cannot_answer_raises(tmp_path, monkeypatch):
    wt = init_repo(tmp_path / "wt")
    monkeypatch.setattr(core.pr_template, "load", mock.Mock(side_effect=RuntimeError("boom")))
    with pytest.raises(ContentError) as excinfo:
        _generate(wt, _req(title="fix: thing", body=_ON_TEMPLATE))
    assert str(excinfo.value) == "✗ Could not resolve this repo's PR template"


# ── generate: both overrides ────────────────────────────────────────────────


def test_both_overrides_do_not_call_ai(tmp_path, monkeypatch):
    wt = init_repo(tmp_path / "wt")
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    calls = _stub(monkeypatch, ok=False)
    result = _generate(wt, _req(title="fix: thing", body=_ON_TEMPLATE))
    assert calls == []
    assert result.title == "fix: thing"
    assert result.body == _ON_TEMPLATE


def test_an_off_template_body_override_leaves_no_content(tmp_path, monkeypatch):
    wt = init_repo(tmp_path / "wt")
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    _stub(monkeypatch, ok=False)
    with pytest.raises(ContentError) as excinfo:
        _generate(wt, _req(title="fix: thing", body="## Summary\nno template here"))
    assert "does not use this repo's template" in str(excinfo.value)


def test_both_overrides_append_closes(tmp_path, monkeypatch):
    wt = init_repo(tmp_path / "wt")
    _stub(monkeypatch, ok=False)
    result = _generate(wt, _req(title="fix: thing", body="## Summary", closes=("#941",)))
    assert result.linked == ("#941",)
    assert "Closes #941" in result.body
    assert result.already == ()


# ── generate: single commit ─────────────────────────────────────────────────


def test_single_commit_with_template_uses_subject_and_ai_body(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    _feature(wt)
    _empty(wt, "feat: one")
    logs: list[str] = []
    _stub(monkeypatch, _desc(_ON_TEMPLATE))
    result = _generate(wt, _req(), log=logs.append)
    assert result.title == "feat: one"
    assert result.body == _ON_TEMPLATE
    assert any("AI filling template" in line for line in logs)


def test_single_commit_no_template_keeps_blank_lines_in_the_body(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt)
    git_in(
        wt, "commit", "-q", "--allow-empty", "--no-verify",
        "-m", "feat: one", "-m", "para one\n\npara two",
    )
    calls = _stub(monkeypatch, ok=False)
    logs: list[str] = []
    result = _generate(wt, _req(), log=logs.append)
    assert calls == []
    assert result.title == "feat: one"
    assert result.body == "para one\n\npara two"
    assert any("skipping AI" in line for line in logs)


def test_single_commit_no_template_no_body_uses_the_skeleton(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt)
    _file_commit(wt, "src/a.py", "x", "feat: one")
    _stub(monkeypatch, ok=False)
    result = _generate(wt, _req())
    assert result.title == "feat: one"
    assert "## Summary" in result.body
    assert "## Changes" in result.body
    assert "- src/a.py" in result.body
    assert "## Testing" in result.body


# ── generate: multi commit ──────────────────────────────────────────────────


def test_multi_commit_extracts_title_and_body_from_markers(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt)
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    _stub(monkeypatch, _both("feat: generated", "## Summary\n\nhello"))
    result = _generate(wt, _req())
    assert result.title == "feat: generated"
    assert result.body == "## Summary\n\nhello"


def test_multi_commit_prompt_contains_the_issue_scraped_from_the_branch(
        tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt, "isaac/ENG-123/thing")
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    logs: list[str] = []
    calls = _stub(monkeypatch, _both("feat: generated", "## Summary"))
    _generate(wt, _req(branch="isaac/ENG-123/thing"), log=logs.append)
    assert "Issue: ENG-123" in calls[0]["prompt"]
    assert "✓ Found issue number: ENG-123" in logs


def test_issue_override_wins_over_the_branch(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt, "isaac/ENG-123/thing")
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    logs: list[str] = []
    calls = _stub(monkeypatch, _both("feat: generated", "## Summary"))
    _generate(
        wt, _req(branch="isaac/ENG-123/thing", issue="PROJ-9"), log=logs.append,
    )
    assert "Issue: PROJ-9" in calls[0]["prompt"]
    assert "✓ Using issue: PROJ-9" in logs


def test_multi_commit_prompt_says_none_when_there_is_no_issue(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt)
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    logs: list[str] = []
    calls = _stub(monkeypatch, _both("feat: generated", "## Summary"))
    _generate(wt, _req(), log=logs.append)
    assert "Issue: None" in calls[0]["prompt"]
    assert not any("issue" in line.lower() for line in logs)
    assert calls[0]["phase"] == Phase.CREATE
    assert calls[0]["kwargs"]["task"] == "pr-create"
    assert calls[0]["kwargs"]["pr"] is None


# ── D1 / D4 / D7 / half-overrides ───────────────────────────────────────────


def test_ai_failure_raises_the_d1_message(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt)
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    _stub(monkeypatch, ok=False)
    with pytest.raises(ContentError) as excinfo:
        _generate(wt, _req())
    assert str(excinfo.value) == "✗ Could not generate the PR title and description"


def test_files_use_the_three_dot_range(tmp_path, monkeypatch):
    """A file changed on base after the fork is not in `files` (D4)."""
    wt = _repo(tmp_path)
    _feature(wt)
    _file_commit(wt, "feature.txt", "branch", "feat: branch")
    git_in(wt, "checkout", "-q", "main")
    _file_commit(wt, "extra.txt", "later", "feat: later on base")
    git_in(wt, "push", "-q", "origin", "main")
    git_in(wt, "checkout", "-q", "feat/work")
    # A second commit so the multi-commit prompt carries Changed files.
    _empty(wt, "feat: two")
    calls = _stub(monkeypatch, _both("feat: generated", "## Summary"))
    _generate(wt, _req())
    prompt = calls[0]["prompt"]
    assert "feature.txt" in prompt
    assert "extra.txt" not in prompt


def test_zero_commits_ahead_raises_before_any_ai_call(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _feature(wt)
    calls = _stub(monkeypatch, ok=False)
    with pytest.raises(ContentError) as excinfo:
        _generate(wt, _req(branch="feat/work", base="main"))
    assert str(excinfo.value) == (
        "✗ No commits on feat/work ahead of origin/main — nothing to open a PR for"
    )
    assert calls == []


def test_title_only_override_keeps_the_ai_body(tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    _feature(wt)
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    _stub(monkeypatch, _both("feat: generated", _ON_TEMPLATE))
    result = _generate(wt, _req(title="fix: override"))
    assert result.title == "fix: override"
    assert result.body == _ON_TEMPLATE


def test_body_only_override_keeps_the_ai_title_and_is_template_checked(
        tmp_path, monkeypatch):
    wt = _repo(tmp_path)
    _write(wt, ".github/PULL_REQUEST_TEMPLATE.md", _WHAT_WHY)
    _feature(wt)
    _empty(wt, "feat: one")
    _empty(wt, "feat: two")
    _stub(monkeypatch, _both("feat: generated", _ON_TEMPLATE))
    result = _generate(wt, _req(body=_ON_TEMPLATE))
    assert result.title == "feat: generated"
    assert result.body == _ON_TEMPLATE
    with pytest.raises(ContentError) as excinfo:
        _generate(wt, _req(body="## Summary\noff"))
    assert "does not use this repo's template" in str(excinfo.value)
