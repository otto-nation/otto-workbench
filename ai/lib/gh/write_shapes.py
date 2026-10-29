"""Which shell statements, and which gh argvs, write to GitHub.

The shape half of the owner boundary. Both agent guards run a cheap in-process
superset pre-filter (``_command_might_write_github`` in
``ai/claude/bin/claude-bash-guard``, ``mightWriteGithub`` in
``ai/pi/extensions/_shared/github-writes.ts``) and ask this module through
``owners-check --classify`` for the verdict, so the write shapes live in one
language. A command the pre-filter does not mention never pays a Python
start-up. ``tests/fixtures/github-write-shapes.tsv`` holds the pre-filter and
this classifier to one answer: a write the pre-filter misses fails there as a
write that was allowed.

A write is:

* ``gh GROUP VERB`` with a verb in ``GH_WRITES`` — issue, pr, release, label,
  repo, workflow, run, secret, variable, cache, gist and project. ``-R/--repo``
  is gh's own flag and is skipped before the group and between the group and
  the verb.
* ``gh api graphql`` unless its query is in hand and holds no ``mutation``.
  The query is in hand when it is a literal ``query=`` field — not
  ``-F query=@file``, not a ``$VAR``, ``$(…)`` or backtick — or a ``--input``
  body the caller passed as text. A query from a file or the shell is a write:
  writing a mutation to a file to dodge quoting is an ordinary habit.
* ``gh api`` with ``-X/--method`` POST, PATCH, PUT or DELETE (``-X=POST`` too) — or, with no
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

``env``, ``sudo``, ``time``, ``timeout``, ``nice`` and the other wrappers
``gh.shell_wrappers`` lists are stripped in a loop by ``gh.shell_wrappers.peel`` — the one peeling function the target resolver
uses too — and the program they run is classified. A wrapper option that
cannot be read with confidence, or that changes directory or re-parses a
string (``env -C``, ``env -S``, ``sudo -D``), makes the statement opaque.

``bash|sh|zsh -c STRING`` (any cluster holding ``c``: ``-lc``, ``-ec``),
``eval WORDS`` and ``env -S STRING`` run text the shell parses again;
``statements``/``expand_statement`` read that text as statements of its own,
up to ``REPARSE_DEPTH`` levels deep, so ``bash -lc 'ls ai/lib/review'`` is a
read and ``bash -c 'gh issue comment 1 --repo o/r -b x'`` is a write whose
target resolves. Past that depth, or when the text cannot be read exactly (a
``-c`` string after a non-option, an ``env -S`` string using env's own
escapes), the statement stays opaque: it is a write when its words mention gh
or a workbench posting command, and the policy refuses it because its target
cannot be resolved. ``xargs`` wrapping a GitHub write is the same — the
arguments come from stdin.

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

import gh.shell_wrappers

GH_WRITES: Mapping[str, frozenset[str]] = {
    "issue": frozenset({
        "create", "comment", "edit", "reopen", "close", "delete", "transfer",
        "lock", "unlock", "pin", "unpin", "develop"}),
    "pr": frozenset({
        "create", "comment", "review", "edit", "merge", "close", "reopen", "ready",
        "revert", "lock", "unlock", "update-branch"}),
    "release": frozenset({"create", "edit", "delete", "upload", "delete-asset"}),
    "label": frozenset({"create", "edit", "delete", "clone"}),
    "repo": frozenset({
        "create", "edit", "delete", "archive", "unarchive", "rename", "fork", "sync"}),
    "workflow": frozenset({"run", "enable", "disable"}),
    "run": frozenset({"rerun", "cancel", "delete"}),
    "secret": frozenset({"set", "delete"}),
    "variable": frozenset({"set", "delete"}),
    "cache": frozenset({"delete"}),
    "gist": frozenset({"create", "edit", "delete", "rename"}),
    "ssh-key": frozenset({"add", "delete"}),
    "gpg-key": frozenset({"add", "delete"}),
    "project": frozenset({
        "create", "edit", "delete", "close", "copy", "link", "unlink", "mark-template",
        "field-create", "field-delete", "item-add", "item-archive", "item-create",
        "item-delete", "item-edit"}),
}
# Writes one level deeper: gh <group> <subgroup> <verb>, aimed at the usual repository.
GH_NESTED_WRITES: Mapping[tuple[str, str], frozenset[str]] = {
    ("repo", "deploy-key"): frozenset({"add", "delete"}),
    ("repo", "autolink"): frozenset({"create", "delete"}),
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
_SHELLS = frozenset({"bash", "sh", "zsh"})
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O"})
_SPLIT_STRING = frozenset({"-S", "--split-string"})
REPARSE_DEPTH = 3
_GITHUB_COMMANDS = frozenset({
    "gh", "pr", "review", "pr-describe", "review-post", "review-threads",
})
_TOKEN_SPLIT = re.compile(r"[\s='\"]+")
_OPERATOR_CHARS = ";&|()\n"
_HEREDOC_OPEN = re.compile(r"<<(-?)\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Invocation:
    """One statement's ``NAME=value`` prefixes and the words after them."""

    env: Mapping[str, str] = field(default_factory=dict)
    words: tuple[str, ...] = ()

    @property
    def program(self) -> str:
        return gh.shell_wrappers.basename(self.words[0]) if self.words else ""

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
    start = gh.shell_wrappers.lift_assignments(statement, env)
    return Invocation(env=env, words=tuple(statement[start:]))


def peeled_statement(statement: Sequence[str]) -> gh.shell_wrappers.Peeled:
    """*statement* with its assignments lifted and its transparent wrappers peeled off."""
    inv = invocation(statement)
    return gh.shell_wrappers.peel(inv.env, inv.words)


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


_API_LONG = {
    "--method": "-X", "--input": "--input", "--field": "-F", "--raw-field": "-f",
    "--header": "", "--jq": "", "--template": "", "--hostname": "", "--cache": "", "--preview": "",
}
_API_SHORT = re.compile(r"^-i*([XfFHqtp])(=?)(.*)$")
_UNREADABLE_QUERY = ("$(", "${", "`")


def _api_option(args: Sequence[str], i: int) -> tuple[str | None, str, int]:
    """``(flag, value, next)`` when *args[i]* is a value-taking gh api option, else ``(None, "", i + 1)``.

    *flag* is canonical: ``-X``, ``-f``, ``-F``, ``--input``, or empty for one
    whose value does not matter. Every pflag spelling is read: ``-X POST``,
    ``-XPOST``, ``-X=POST``, ``-iXPOST``, ``--method POST``, ``--method=POST``.
    """
    word = args[i]
    name, eq, attached = word.partition("=")
    if name in _API_LONG and word.startswith("--"):
        if eq:
            return _API_LONG[name], attached, i + 1
        return _API_LONG[name], args[i + 1] if i + 1 < len(args) else "", i + 2
    short = _API_SHORT.match(word)
    if short is None or word.startswith("--"):
        return None, "", i + 1
    flag = f"-{short.group(1)}" if short.group(1) in "XfF" else ""
    if short.group(2) or short.group(3):
        return flag, short.group(3), i + 1
    return flag, args[i + 1] if i + 1 < len(args) else "", i + 2


def _query_is_literal(flag: str, text: str) -> bool:
    """Whether a ``query`` field's text is the query itself, not a file, variable or substitution."""
    if flag == "-F" and text.startswith("@"):
        return False
    stripped = text.lstrip()
    if not stripped or stripped[0] in "$@`":
        return False
    return not any(marker in text for marker in _UNREADABLE_QUERY)


def _api_is_write(args: Sequence[str], input_text: str | None) -> bool:
    method = ""
    field_values: list[tuple[str, str]] = []
    fields = has_input = graphql = mutation = False
    i = 0
    while i < len(args):
        flag, value, nxt = _api_option(args, i)
        if "mutation" in args[i] or "mutation" in value:
            mutation = True
        if flag is None:
            graphql = graphql or args[i] == "graphql"
        elif flag == "-X":
            method = value
        elif flag == "--input":
            fields = has_input = True
        elif flag in ("-f", "-F"):
            fields = True
            field_values.append((flag, value))
        i = nxt
    if input_text is not None and "mutation" in input_text:
        mutation = True
    if graphql:
        if mutation or has_input:
            return mutation or input_text is None
        queries = [(flag, value.partition("=")[2]) for flag, value in field_values
                   if value.partition("=")[0] == "query"]
        return not queries or not all(_query_is_literal(flag, text) for flag, text in queries)
    if method:
        return method.upper() in MUTATING_METHODS
    return fields


def gh_subcommands(args: Sequence[str], depth: int = 2) -> tuple[str, ...]:
    """gh's command group and verb, with ``-R/--repo`` skipped wherever it sits before the verb.

    gh's flag parser accepts a persistent flag between the group and the verb
    (``gh pr -R o/r comment 1``), so a repo flag there is not the verb.
    """
    found: list[str] = []
    i = 0
    while i < len(args) and len(found) < depth:
        _, nxt = take_repo_flag(args, i)
        if nxt != i:
            i = nxt
            continue
        found.append(args[i])
        i += 1
    return tuple(found)


def gh_is_write(args: Sequence[str], input_text: str | None = None) -> bool:
    """Whether ``gh *args`` writes to GitHub. *input_text* is the body gh reads on stdin."""
    i = skip_repo_flags(args)
    if i < len(args) and args[i] == "api":
        return _api_is_write(args[i + 1:], input_text)
    path = gh_subcommands(args)
    verbs = GH_WRITES.get(path[0]) if path else None
    if verbs is not None and len(path) == 2 and path[1] in verbs:
        return True
    return _nested_write(gh_subcommands(args, 3))


def _nested_write(path: Sequence[str]) -> bool:
    return len(path) == 3 and path[2] in GH_NESTED_WRITES.get((path[0], path[1]), frozenset())


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
    return [gh.shell_wrappers.basename(token) for token in tokens]


def _tokens_mention_github(words: Sequence[str]) -> bool:
    return bool(set(_basename_tokens(words)) & _GITHUB_COMMANDS)


def _cluster_mentions_github(token: str) -> bool:
    """``-Sgh``: an option cluster whose tail could be an attached ``gh`` command string."""
    if not token.startswith("-") or token.startswith("--"):
        return False
    return any(token[at:] in _GITHUB_COMMANDS for at in range(2, len(token)))


def _opaque_words_mention_github(words: Sequence[str]) -> bool:
    """The word search for an opaque wrapper, which also looks inside ``--opt=`` and clusters."""
    tokens = [gh.shell_wrappers.basename(t) for w in words for t in _TOKEN_SPLIT.split(w) if t]
    return bool(set(tokens) & _GITHUB_COMMANDS) or any(map(_cluster_mentions_github, tokens))


def _has_command_string_flag(args: Sequence[str]) -> bool:
    """True when a short-option cluster contains ``c`` (``-c``, ``-lc``, ``-ec``)."""
    for word in args:
        if word == "--":
            return False
        if word.startswith("-") and not word.startswith("--") and "c" in word:
            return True
    return False


def _reparsed_github_write(words: Sequence[str]) -> bool:
    program = gh.shell_wrappers.basename(words[0]) if words else ""
    rest = words[1:]
    if program == "eval":
        return _tokens_mention_github(rest)
    if program in _SHELLS and _has_command_string_flag(rest):
        return _tokens_mention_github(rest)
    if program != "xargs":
        return False
    options = gh.shell_wrappers.parse_options(gh.shell_wrappers.XARGS, rest)
    if options is None or not options.rest:
        return _opaque_words_mention_github(rest)
    return statement_is_write(options.rest)


def _shell_command_string(args: Sequence[str]) -> str | None:
    """The ``-c`` string of ``bash|sh|zsh ARGS`` when every word before it is an option, else None."""
    i = 0
    while i < len(args):
        word = args[i]
        if word in _SHELL_VALUE_OPTIONS:
            i += 2
            continue
        if not word.startswith(("-", "+")) or word in ("-", "--"):
            return None
        if word.startswith("-") and not word.startswith("--") and "c" in word:
            following = args[i + 1] if i + 1 < len(args) else "-"
            return None if following.startswith("-") else following
        i += 1
    return None


def _env_option_words(found: Sequence[tuple[str, str]]) -> list[str]:
    spec = gh.shell_wrappers.ENV
    words: list[str] = []
    for name, value in found:
        if name in spec.long_values or (name in spec.long_attached and value):
            words.append(f"{name}={value}")
        elif len(name) == 2 and name[1] in spec.values:
            words.extend([name, value])
        else:
            words.append(name)
    return words


def _split_string_statement(words: Sequence[str]) -> list[str] | None:
    """``env … -S STRING …`` with STRING split into words in place, as env does; None when unsure.

    A string using env's own escapes, ``${VAR}`` expansion or comments is not
    split here — the statement stays opaque.
    """
    spec = gh.shell_wrappers.ENV
    options = gh.shell_wrappers.parse_options(spec, words[1:])
    if options is None:
        return None
    splits = [value for name, value in options.found if name in _SPLIT_STRING]
    others = [(name, value) for name, value in options.found if name not in _SPLIT_STRING]
    if len(splits) != 1 or any(name in spec.opaque for name, _ in others):
        return None
    if any(char in splits[0] for char in "\\$#"):
        return None
    try:
        inner = shlex.split(splits[0])
    except ValueError:
        return None
    return [words[0], *_env_option_words(others), *inner, *options.rest]


def _reparsed_statements(peeled: gh.shell_wrappers.Peeled) -> list[list[str]] | None:
    """The statements a re-parsing wrapper runs, or None when they cannot be read exactly."""
    program = gh.shell_wrappers.basename(peeled.words[0])
    if peeled.opaque:
        statement = _split_string_statement(peeled.words) if program == "env" else None
        return None if statement is None else [statement]
    if program == "eval":
        text: str | None = " ".join(peeled.words[1:])
    elif program in _SHELLS:
        text = _shell_command_string(peeled.words[1:])
    else:
        return None
    if text is None:
        return None
    try:
        return split_statements(text)
    except ValueError:
        return None


def expand_statement(statement: Sequence[str], depth: int = REPARSE_DEPTH) -> list[list[str]]:
    """*statement*, or the statements a re-parsing wrapper in it runs, read *depth* levels deep.

    ``bash -c STRING``, ``eval WORDS`` and ``env -S STRING`` run text the
    shell parses again. Each inner statement keeps the outer statement's
    assignments and wrappers in front of it, so ``GH_REPO=x sudo bash -c 'gh …'``
    is classified and resolved as ``GH_REPO=x sudo gh …``. Past *depth*, or
    when the text cannot be read exactly, the statement is returned as it is
    and the opaque word search answers for it.
    """
    if depth <= 0:
        return [list(statement)]
    peeled = peeled_statement(statement)
    if peeled.lookup or not peeled.words:
        return [list(statement)]
    inner = _reparsed_statements(peeled)
    if inner is None:
        return [list(statement)]
    prefix = list(statement[:len(statement) - len(peeled.words)])
    return [expanded for one in inner for expanded in expand_statement(prefix + one, depth - 1)]


def statements(command: str) -> list[list[str]]:
    """*command*'s statements with every re-parsing wrapper expanded (ValueError if unparseable)."""
    return [expanded for statement in split_statements(command)
            for expanded in expand_statement(statement)]


def opaque_github_write(statement: Sequence[str]) -> bool:
    """Whether *statement* reaches gh through a wrapper whose target cannot be resolved."""
    peeled = peeled_statement(statement)
    if peeled.opaque:
        return _opaque_words_mention_github(statement)
    if peeled.lookup:
        return False
    return _reparsed_github_write(peeled.words)


def statement_is_write(statement: Sequence[str]) -> bool:
    """Whether one statement's words write to GitHub."""
    if opaque_github_write(statement):
        return True
    peeled = peeled_statement(statement)
    if peeled.opaque or peeled.lookup or not peeled.words:
        return False
    inv = Invocation(env=peeled.env, words=peeled.words)
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
    return any(statement_is_write(statement) for statement in statements(command))
