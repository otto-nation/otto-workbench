"""Which worktrees and PRs a pi session touched, read from its own entries.

``tool_calls(entries, cwd)`` turns pi's assistant ``toolCall`` blocks (bash,
edit, write) into ``pr.agent_worktree.ToolCall``s — the payload shape the
agent-worktree hook reads — pairing each bash call with its ``toolResult`` text
as stdout, so which worktree a call targeted stays ``pr.agent_worktree``'s
answer. ``worktrees_of(calls, cwd)`` is the session checkout's worktree, then
every other worktree a call targeted, first seen first. ``prs_in_command(command,
stdout, default_repo, host)`` reads PR references from one ``pr …`` / ``gh pr …``
command and its output: PR URLs, ``gh pr <view|checkout|…> N`` (``--repo`` /
``-R`` naming the repo), ``pr <review|comments|…> N`` and ``--pr N``; a bare number
needs a repo and is dropped without one. ``collect(path)`` does all of it for one
session file and returns ``Facts``. ``pr_from_board_row`` reads a cached
``pr board`` listing row; ``merge_prs(seen, board)`` keeps one ``PrRef`` per
(repo, number), board rows first, so their titles win.
"""

# ceiling: every brief re-reads the whole session file and resolves each
# candidate with git; upgrade to an incremental cursor if a brief on a long
# session takes more than a few seconds.

# doc-group: platform

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import gh.shell_lex
import git.topology
import pr.agent_worktree
import pr.target
from pi_sessions.session_file import iter_entries, read_header

_PR_URL = re.compile(r"https?://([^/\s]+)/([^/\s]+/[^/\s]+?)/pull/(\d+)")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GH_NUMBERED = frozenset({"view", "checkout", "merge", "close", "edit", "review",
                          "comment", "diff", "ready", "reopen", "checks"})
_PR_NUMBERED = frozenset({"review", "comments", "ci", "fix", "describe", "status", "rebase"})
_TOOL_NAMES = {"bash": "Bash", "edit": "Edit", "write": "Write"}


@dataclass(frozen=True)
class PrRef:
    """One PR a session is attributed: its repo slug, number, link and (from the board) title."""

    repo: str
    number: int
    url: str
    title: str = ""

    def to_json(self) -> dict:
        return {"repo": self.repo, "number": self.number, "url": self.url, "title": self.title}


@dataclass(frozen=True)
class WorktreeRef:
    """A worktree the session worked in, with the branch and board key to look its PR up by."""

    path: str
    branch: str | None
    repo_key: str | None


@dataclass(frozen=True)
class Facts:
    worktrees: tuple[WorktreeRef, ...]
    seen_prs: tuple[PrRef, ...]


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, Mapping))


def _payload(block: Mapping, stdout: str, cwd: str) -> dict | None:
    name = _TOOL_NAMES.get(str(block.get("name", "")))
    args = block.get("arguments") if isinstance(block.get("arguments"), Mapping) else {}
    if name == "Bash" and isinstance(args.get("command"), str) and args["command"]:
        return {"tool_name": name, "tool_input": {"command": args["command"]},
                "tool_response": {"stdout": stdout}, "cwd": cwd}
    if name in ("Edit", "Write") and isinstance(args.get("path"), str) and args["path"]:
        path = os.path.normpath(os.path.join(cwd, os.path.expanduser(args["path"])))
        return {"tool_name": name, "tool_input": {"file_path": path}, "cwd": cwd}
    return None


def tool_calls(entries: Iterable[Mapping], cwd: str) -> list[pr.agent_worktree.ToolCall]:
    blocks: list[Mapping] = []
    outputs: dict[str, str] = {}
    for entry in entries:
        message = entry.get("message") if entry.get("type") == "message" else None
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "toolResult":
            outputs[str(message.get("toolCallId", ""))] = _text(message.get("content"))
        elif message.get("role") == "assistant" and isinstance(message.get("content"), list):
            blocks += [b for b in message["content"] if isinstance(b, Mapping) and b.get("type") == "toolCall"]
    env = {"CLAUDE_PROJECT_DIR": cwd}
    calls = []
    for block in blocks:
        payload = _payload(block, outputs.get(str(block.get("id", "")), ""), cwd)
        if payload is not None:
            calls.append(pr.agent_worktree.ToolCall.from_payload(payload, env))
    return calls


def worktrees_of(calls: Sequence[pr.agent_worktree.ToolCall], cwd: str) -> list[Path]:
    found: list[Path] = []
    home = pr.agent_worktree.resolve(cwd, cwd) if Path(cwd).is_dir() else None
    if home is not None:
        found.append(home)
    for call in calls:
        # candidates() is pure; only calls that name a place pay for git resolution.
        if not pr.agent_worktree.candidates(call):
            continue
        target = pr.agent_worktree.target_worktree(call)
        if target is not None and target not in found:
            found.append(target)
    return found


def _url_refs(text: str) -> list[PrRef]:
    return [PrRef(repo=m.group(2), number=int(m.group(3)), url=m.group(0)) for m in _PR_URL.finditer(text)]


def _flag_equals(word: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        if word.startswith(name + "="):
            return word[len(name) + 1:]
    return None


def _flag(words: Sequence[str], names: tuple[str, ...]) -> str | None:
    for i, word in enumerate(words):
        if word in names and i + 1 < len(words):
            return words[i + 1]
        equals = _flag_equals(word, names)
        if equals is not None:
            return equals
    return None


def _statement_refs(words: Sequence[str], default_repo: str | None, host: str) -> list[PrRef] | None:
    """PR refs in one statement, or None when it is not a `pr` / `gh pr` command."""
    command = list(words)
    while command and _ASSIGNMENT.match(command[0]):
        command.pop(0)
    if command[:2] == ["gh", "pr"]:
        rest, numbered = command[2:], _GH_NUMBERED
        repo = _flag(command, ("--repo", "-R")) or default_repo
    elif command[:1] == ["pr"]:
        rest, numbered, repo = command[1:], _PR_NUMBERED, default_repo
    else:
        return None
    refs = _url_refs(" ".join(command))
    numbers: list[int] = []
    if len(rest) > 1 and rest[0] in numbered and rest[1].isdigit():
        numbers.append(int(rest[1]))
    pr_flag = _flag(command, ("--pr",))
    if pr_flag and pr_flag.isdigit():
        numbers.append(int(pr_flag))
    if repo:
        refs += [PrRef(repo=repo, number=n, url=f"https://{host}/{repo}/pull/{n}") for n in numbers]
    return refs


def _dedupe(refs: Iterable[PrRef]) -> list[PrRef]:
    seen: set[tuple[str, int]] = set()
    out: list[PrRef] = []
    for ref in refs:
        key = (ref.repo.lower(), ref.number)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def prs_in_command(command: str, stdout: str, default_repo: str | None, host: str) -> list[PrRef]:
    try:
        statements = gh.shell_lex.split_statements(command)
    except ValueError:
        return []
    refs: list[PrRef] = []
    is_pr = False
    for words in statements:
        found = _statement_refs(words, default_repo, host)
        if found is not None:
            is_pr = True
            refs += found
    if is_pr:
        refs += _url_refs(stdout)
    return _dedupe(refs)


def pr_from_board_row(row: Mapping) -> PrRef | None:
    number, repo = row.get("number"), row.get("repo")
    if not isinstance(number, int) or not isinstance(repo, str) or not repo:
        return None
    url = row.get("url") if isinstance(row.get("url"), str) else f"https://github.com/{repo}/pull/{number}"
    return PrRef(repo=repo, number=number, url=url, title=str(row.get("title") or ""))


def merge_prs(seen: Iterable[PrRef], board: Iterable[PrRef]) -> list[PrRef]:
    return _dedupe([*board, *seen])


def collect(path: Path) -> Facts:
    header = read_header(path)
    if header is None:
        return Facts(worktrees=(), seen_prs=())
    cwd = header.cwd
    calls = tool_calls(iter_entries(path), cwd)
    identity = pr.target.repo_identity_from_origin(cwd) if Path(cwd).is_dir() else None
    repo = identity.label if identity else None
    host = identity.host if identity and identity.host else "github.com"
    seen: list[PrRef] = []
    for call in calls:
        if call.tool_name == "Bash":
            seen += prs_in_command(call.command, call.stdout, repo, host)
    worktrees = tuple(
        WorktreeRef(path=str(p), branch=git.topology.current_branch_quiet(str(p)),
                    repo_key=pr.target.repo_key_from_origin(str(p)))
        for p in worktrees_of(calls, cwd))
    return Facts(worktrees=worktrees, seen_prs=tuple(_dedupe(seen)))
