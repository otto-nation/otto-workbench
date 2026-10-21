"""Which shell statements, and which gh argvs, write to GitHub.

The shape half of the owner boundary. Both agent guards carry their own copy of
this predicate — ``_command_writes_github`` in ``ai/claude/bin/claude-bash-guard``
and ``isGithubWrite`` in ``ai/pi/extensions/_shared/github-writes.ts`` — because
they run on every shell call and cannot afford a Python start-up to find out a
command is a read. Three copies of one predicate are held to one answer by
``tests/fixtures/github-write-shapes.tsv``: a row any of them disagrees with
fails that copy's suite.

A write is:

* ``gh issue|pr|release|label`` with a verb in ``GH_WRITES``. A leading
  ``-R/--repo`` is gh's own flag and is skipped.
* ``gh api graphql`` whose text holds ``mutation``, or whose body comes from
  ``--input`` and is not in hand.
* ``gh api`` with ``-X/--method`` POST, PATCH, PUT or DELETE — or, with no
  method, with a field or body (``-f/-F/--field/--raw-field/--input``): gh
  sends POST then, and that is the call that lands.
* The workbench's own posting commands: ``pr create``; ``pr describe|comments|fix|ci``
  with ``--post``; ``pr review`` and ``review`` unless ``--self``,
  ``--no-post``, ``--summary`` or ``--list`` says it posts nothing. The
  hyphenated shims of those commands — ``pr-describe --post``,
  ``review-post``, ``review-threads --post`` — are the same writes.

``NAME=value`` prefixes are carried on ``Invocation.env`` (``GH_REPO`` and
``GH_HOST`` decide a target), and the program is matched on its basename, so
``/usr/local/bin/gh`` is gh.

``eval``, ``env`` with options, ``bash|sh|zsh -c``, ``command``, ``exec``,
``xargs``, ``sudo`` and ``time`` that mention gh or a workbench posting command
are writes whose target cannot be resolved — the policy refuses them.

ceiling: a gh reached through a shell alias or a wrapper script whose name is
not in that set is not matched. Upgrade trigger: the first such spelling seen
in an agent transcript; the guards read it the same way, so all three change
together through the corpus.
"""

# doc-group: platform

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

GH_WRITES: Mapping[str, frozenset[str]] = {
    "issue": frozenset({"create", "comment", "edit", "reopen", "close", "delete", "transfer"}),
    "pr": frozenset({"create", "comment", "review", "edit", "merge", "close", "reopen", "ready"}),
    "release": frozenset({"create", "edit", "delete", "upload"}),
    "label": frozenset({"create", "edit", "delete"}),
}
MUTATING_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
DIRECTORY_CHANGERS = frozenset({"cd", "pushd", "popd"})

_FIELD_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field"})
_FIELD_PREFIXES = ("--field=", "--raw-field=")
_WORKBENCH_PR_SUBCOMMANDS = frozenset({
    "create", "describe", "comments", "review", "fix", "ci", "status",
    "rebase", "push", "batch", "gc",
})
_POSTING_SUBCOMMANDS = frozenset({"describe", "comments", "fix", "ci"})
_REVIEW_READ_FLAGS = frozenset({"--self", "--no-post", "--summary", "--list"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_OPAQUE_WRAPPERS = frozenset({"eval", "command", "exec", "xargs", "sudo", "time"})
_SHELLS = frozenset({"bash", "sh", "zsh"})
_GITHUB_COMMANDS = frozenset({
    "gh", "pr", "review", "pr-describe", "review-post", "review-threads",
})
_OPERATOR_CHARS = ";&|()\n"
_HEREDOC_OPEN = re.compile(r"<<(-?)\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Invocation:
    """One statement's ``NAME=value`` prefixes and the words after them."""

    env: Mapping[str, str] = field(default_factory=dict)
    words: tuple[str, ...] = ()

    @property
    def program(self) -> str:
        return self.words[0].rsplit("/", 1)[-1] if self.words else ""

    @property
    def args(self) -> tuple[str, ...]:
        return self.words[1:]


def _heredoc_terminator(dash: str, marker: str) -> re.Pattern[str]:
    indentable = r"\s*" if dash else ""
    return re.compile(rf"^{indentable}{re.escape(marker)}\s*$")


def _after_heredoc_line(line: str, terminator: re.Pattern[str]) -> re.Pattern[str] | None:
    """None once *line* is the terminator; otherwise keep skipping."""
    if terminator.match(line):
        return None
    return terminator


def _drop_heredoc_bodies(command: str) -> str:
    """Drop heredoc bodies the same way the agent guards do.

    Only ``<<-`` allows an indented terminator. Accepting indentation for a
    plain ``<<`` would end the heredoc early on a body line that happens to be
    the marker word, and scan the rest of the body as commands.
    """
    kept: list[str] = []
    terminator: re.Pattern[str] | None = None
    for line in command.split("\n"):
        if terminator is not None:
            terminator = _after_heredoc_line(line, terminator)
            continue
        kept.append(line)
        opened = _HEREDOC_OPEN.search(line)
        if opened is not None:
            terminator = _heredoc_terminator(opened.group(1), opened.group(2))
    return "\n".join(kept)


def split_statements(command: str) -> list[list[str]]:
    """*command* as statements of words, split on shell operators and newlines.

    POSIX quoting applies, so an operator inside quotes is an argument. Heredoc
    bodies are dropped first — they are content, not commands, and an
    unbalanced quote in one must not make the shape unknown. Raises
    ``ValueError`` on an unbalanced quote — a command whose shape is unknown.
    """
    lexer = shlex.shlex(_drop_heredoc_bodies(command), posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    statements: list[list[str]] = [[]]
    for token in lexer:
        if token and set(token) <= set(_OPERATOR_CHARS):
            statements.append([])
            continue
        statements[-1].append(token)
    return [statement for statement in statements if statement]


def invocation(statement: Sequence[str]) -> Invocation:
    """*statement* with its leading ``NAME=value`` assignments lifted off."""
    env: dict[str, str] = {}
    start = 0
    while start < len(statement) and _ASSIGNMENT.match(statement[start]):
        name, _, value = statement[start].partition("=")
        env[name.removesuffix("+")] = value
        start += 1
    return Invocation(env=env, words=tuple(statement[start:]))


def take_repo_flag(args: Sequence[str], i: int) -> tuple[str | None, int]:
    """If *args[i]* is ``-R/--repo`` in any pflag form, return ``(value, next_index)``.

    Forms: ``-R VALUE``, ``-RVALUE``, ``-R=VALUE``, ``--repo VALUE``, ``--repo=VALUE``.
    When it is not a repo flag, return ``(None, i)``.
    """
    if i >= len(args):
        return None, i
    word = args[i]
    if word in ("-R", "--repo"):
        if i + 1 < len(args):
            return args[i + 1], i + 2
        return None, i + 1
    if word.startswith("--repo="):
        return word[len("--repo="):], i + 1
    if word.startswith("-R="):
        return word[len("-R="):], i + 1
    if word.startswith("-R") and len(word) > 2:
        return word[2:], i + 1
    return None, i


def skip_repo_flags(args: Sequence[str]) -> int:
    """Index of the first word that is not a leading ``-R/--repo`` flag or its value."""
    i = 0
    while i < len(args):
        _, nxt = take_repo_flag(args, i)
        if nxt == i:
            break
        i = nxt
    return i


def with_env_builtin(inv: Invocation) -> Invocation:
    """Lift ``env NAME=value ... cmd`` the same way leading ``NAME=value`` prefixes are."""
    if inv.program != "env":
        return inv
    env = dict(inv.env)
    i = 0
    args = inv.args
    while i < len(args) and _ASSIGNMENT.match(args[i]):
        name, _, value = args[i].partition("=")
        env[name.removesuffix("+")] = value
        i += 1
    return Invocation(env=env, words=tuple(args[i:]))


def _api_is_write(args: Sequence[str], input_text: str | None) -> bool:
    method = ""
    fields = has_input = graphql = mutation = False
    i = 0
    while i < len(args):
        word = args[i]
        if "mutation" in word:
            mutation = True
        if word in ("-X", "--method"):
            method = args[i + 1] if i + 1 < len(args) else ""
            i += 2
            continue
        if word.startswith("--method="):
            method = word[len("--method="):]
        elif word.startswith("-X") and len(word) > 2:
            method = word[2:]
        elif word == "--input" or word.startswith("--input="):
            fields = has_input = True
        elif word in _FIELD_FLAGS or word.startswith(_FIELD_PREFIXES) or (
                word[:2] in ("-f", "-F") and len(word) > 2):
            fields = True
        elif word == "graphql":
            graphql = True
        i += 1
    if input_text is not None and "mutation" in input_text:
        mutation = True
    if graphql:
        return mutation or (has_input and input_text is None)
    if method:
        return method.upper() in MUTATING_METHODS
    return fields


def gh_is_write(args: Sequence[str], input_text: str | None = None) -> bool:
    """Whether ``gh *args`` writes to GitHub. *input_text* is the body gh reads on stdin."""
    i = skip_repo_flags(args)
    sub = args[i] if i < len(args) else ""
    if sub == "api":
        return _api_is_write(args[i + 1:], input_text)
    verbs = GH_WRITES.get(sub)
    return verbs is not None and i + 1 < len(args) and args[i + 1] in verbs


def _review_posts(args: Sequence[str]) -> bool:
    return not (_REVIEW_READ_FLAGS & set(args))


def _workbench_pr_posts(args: Sequence[str]) -> bool:
    at = next((i for i, word in enumerate(args) if word in _WORKBENCH_PR_SUBCOMMANDS), -1)
    if at < 0:
        return False
    sub = args[at]
    if sub == "create":
        return True
    if sub in _POSTING_SUBCOMMANDS:
        return "--post" in args
    if sub == "review":
        return _review_posts(args[at + 1:])
    return False


def _basename_tokens(words: Sequence[str]) -> list[str]:
    tokens: list[str] = []
    for word in words:
        tokens.extend(word.split())
    return [token.rsplit("/", 1)[-1] for token in tokens]


def _tokens_mention_github(words: Sequence[str]) -> bool:
    return bool(set(_basename_tokens(words)) & _GITHUB_COMMANDS)


def _env_has_option(args: Sequence[str]) -> bool:
    for word in args:
        if word.rsplit("/", 1)[-1] in _GITHUB_COMMANDS:
            return False
        if word.startswith("-"):
            return True
    return False


def opaque_github_write(statement: Sequence[str]) -> bool:
    """Whether *statement* reaches gh through a wrapper whose target cannot be resolved."""
    inv = invocation(statement)
    rest = inv.args
    if not _tokens_mention_github(rest):
        return False
    if inv.program in _OPAQUE_WRAPPERS:
        return True
    if inv.program in _SHELLS and "-c" in rest:
        return True
    return inv.program == "env" and _env_has_option(rest)


def statement_is_write(statement: Sequence[str]) -> bool:
    """Whether one statement's words write to GitHub."""
    if opaque_github_write(statement):
        return True
    inv = with_env_builtin(invocation(statement))
    if inv.program == "gh":
        return gh_is_write(inv.args)
    if inv.program == "pr":
        return _workbench_pr_posts(inv.args)
    if inv.program == "pr-describe":
        return "--post" in inv.args
    if inv.program == "review":
        return _review_posts(inv.args)
    if inv.program == "review-post":
        return True
    if inv.program == "review-threads":
        return "--post" in inv.args
    return False


def command_is_write(command: str) -> bool:
    """Whether any statement of *command* writes to GitHub (ValueError if unparseable)."""
    return any(statement_is_write(statement) for statement in split_statements(command))
