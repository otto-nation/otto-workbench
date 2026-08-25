"""The workbench's typed configuration.

One file per scope — global ``config.yml`` under the config root and project
``.workbench.yml`` at a repo root — deep-merged and typed into
``WorkbenchConfig``. The dataclasses here are the single definition: they type
the runtime lookups, they generate ``config.schema.json``
(``bin/local/generate-config-schema``), and their ``Phase``-keyed maps make a
phase a valid config key the moment it becomes an enum member.

The config is layers 4 and 5 of the precedence chain, behind CLI flags and env
vars:

    CLI flag > CLAUDE_REVIEW_<PHASE>_* > CLAUDE_REVIEW_* > project > global

so nothing here overrides a value a caller passed or exported.
"""

# doc-group: platform

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import get_type_hints

import serde
import timeouts
import workbench_paths
from review_common import Effort, Phase, Thinking

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


CONFIG_NAME = "config.yml"
PROJECT_CONFIG_NAME = ".workbench.yml"

# What a reader calls each scope. Beside the filenames because they name the
# same two things, and because ``config_scopes`` pairs them up.
GLOBAL_SCOPE = "global"
PROJECT_SCOPE = "project"

# Where the generated schema lives, repo-relative, and the raw URL that serves
# it. One spelling of the path: bin/local/generate-config-schema writes there,
# the modeline below points there, ``installed_schema_path`` reads it out of
# the installed checkout, and tests/test_workbench_config.py fails if they stop
# agreeing — so moving the file is a one-line change here.
# Pinned to main rather than a release tag: the config on a machine tracks
# whatever workbench is installed, and main is where the schema is regenerated.
SCHEMA_PATH = "config.schema.json"
REPO_RAW_URL = "https://raw.githubusercontent.com/otto-nation/otto-workbench/main"
SCHEMA_URL = f"{REPO_RAW_URL}/{SCHEMA_PATH}"
# The modeline a config file is born with, so an editor's YAML language server
# validates the file against that schema as the user hand-edits it.
# lib/constants.sh spells the same names for the files bash creates;
# tests/config.bats cross-validates every pair.
CONFIG_HEADER = f"# yaml-language-server: $schema={SCHEMA_URL}"

# The doc the key reference is composed into. `lib/config.sh` asks for the block
# by name — `generate-config-schema --emit config-reference` — and
# bin/local/compose-docs expands that directive on the way to this file.
DOCS_PATH = "docs/libraries.md"

# Repo root as a link from inside DOCS_PATH, so a link the block renders to a
# repo-root file survives moving the doc to another depth.
_DOCS_TO_ROOT = "../" * DOCS_PATH.count("/")

# The script that renders both generated files, named in the "do not edit"
# banner each one carries. Spelled here because both banners are rendered here;
# the script checks this against its own path, so a rename that misses this line
# fails loudly rather than pointing readers at a command that does not exist.
GENERATOR_PATH = "bin/local/generate-config-schema"

# The dotted keys written from outside this module. Spelled here rather than at
# the call site so a rename of the dataclass field and a rename of the key are
# the same edit; test_workbench_config.py resolves each one against
# WorkbenchConfig and fails on a key no field answers to.
REUSE_LEVEL_KEY = "reuse.level"
REUSE_DEFAULT_KEY = "reuse.default"
ISSUE_PROVIDER_KEY = "issue_tracker.provider"
# Read from bash rather than written: git/steps.sh asks for this one through
# wb_config_get. lib/constants.sh spells the same string, and tests/config.bats
# cross-validates the pair.
GITHUB_SSH_443_KEY = "github.ssh_over_443"

# The launcher that resolves to the workbench this machine has installed. It is
# a symlink into the checkout that installed it, so resolving it from any
# worktree lands on that one — which is what lets a write be judged by the key
# surface the machine actually reads rather than by the writing checkout's.
# `bin/otto-workbench` derives the same root from its own location; there is no
# equivalent here, so PATH is the link.
INSTALLED_LAUNCHER = "otto-workbench"


class ConfigError(ValueError):
    """A config file exists but cannot be read as one.

    A ``ValueError`` subclass because that is what ``serde`` raises for the
    unusable value underneath, so a caller that already catches ``ValueError``
    keeps working.
    """


class ConfigKeyError(ConfigError):
    """A write named a key the config surface does not have.

    Its own type because a caller owes the two different things. A file that
    would not open costs the recording and nothing else; a key nothing reads
    costs the value itself, silently and in a file every repo on the machine
    shares. A caller that shrugs at the first has to report the second.
    """


class ReuseLevel(StrEnum):
    """How hard the reuse ladder in ``general.md`` is enforced."""

    LITE = "lite"
    FULL = "full"
    ULTRA = "ultra"


class IssueProvider(StrEnum):
    """The issue tracker a review links findings to."""

    LINEAR = "linear"
    GITHUB = "github"
    JIRA = "jira"


@dataclass(frozen=True)
class PhaseOverride:
    """Per-phase settings, matching the two knobs the phase env keys expose."""

    model: str | None = None
    thinking: Thinking | None = None


@dataclass(frozen=True)
class IssueTrackerConfig:
    """Where a repo files its issues.

    Top-level rather than under ``review``: where a repo files issues is a
    fact about the repo, read by the SessionStart context line and by every
    rule in ``issue-tracker.md``, of which only two callers are reviews.

    ``provider`` has no default on purpose. A repo that has never said
    anything about its tracker is unknown, not Linear — the callers that
    need one ask rather than guess, and the one that only enriches a
    review does without.
    """

    provider: IssueProvider | None = None
    team: str = ""
    jira_url: str = ""


@dataclass(frozen=True)
class ReviewConfig:
    """Review pipeline settings.

    ``model``, ``thinking`` and ``provider`` are the file forms of
    CLAUDE_REVIEW_MODEL, _THINKING and _PROVIDER; ``phases`` is the file form
    of the per-phase keys ``Phase.model_env_key`` derives. A phase entry beats
    the section-level value, matching how a phase env key beats the global one.
    """

    model: str | None = None
    thinking: Thinking | None = None
    provider: str | None = None
    effort: Effort | None = None
    phases: dict[Phase, PhaseOverride] = field(default_factory=dict)


@dataclass(frozen=True)
class ReuseConfig:
    """``level`` is the active pick; ``None`` means fall back to ``default``."""

    level: ReuseLevel | None = None
    default: ReuseLevel = ReuseLevel.FULL


@dataclass(frozen=True)
class GitHubConfig:
    """How this machine reaches GitHub.

    ``ssh_over_443`` moves git's SSH traffic to ``ssh.github.com:443``, the
    endpoint GitHub publishes for networks that block or throttle outbound
    TCP/22. Same service, same host keys, one more hop through their edge — so
    it is off by default and turned on per machine, not per repo.
    """

    ssh_over_443: bool = False


@dataclass(frozen=True)
class WorkbenchConfig:
    reuse: ReuseConfig = field(default_factory=ReuseConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    issue_tracker: IssueTrackerConfig = field(default_factory=IssueTrackerConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)


def schema_json() -> str:
    """``WorkbenchConfig`` as the JSON Schema text ``config.schema.json`` holds.

    Here rather than in the generator script so the write and the ``--check``
    comparison render through one code path, and so the schema's wrapper
    metadata sits beside the dataclass it describes.

    Nothing sets ``additionalProperties: false``, so an editor validating
    against this will not flag a misspelled key. That is deliberate: the schema
    is committed at one version while the config on disk may have been written
    by a newer workbench, and a closed schema would turn every new key into an
    error in the editor of anyone who has not pulled yet. ``serde`` drops keys
    it does not know, so an unrecognised key is inert either way.
    """
    import schema_gen

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "otto-workbench configuration",
        "description": (
            f"Generated from WorkbenchConfig by {GENERATOR_PATH}. Do not edit by hand."
        ),
        **schema_gen.dataclass_to_schema(WorkbenchConfig),
    }
    return json.dumps(schema, indent=2)


def _is_enum(hint) -> bool:
    return isinstance(hint, type) and issubclass(hint, Enum)


def _values_column(hint) -> str:
    """How the reference table describes what a key accepts."""
    kind, args = serde.classify(hint)
    if kind is serde.HintKind.OPTIONAL:
        return _values_column(args[0])
    if kind is serde.HintKind.ENUM:
        return ", ".join(f"`{member.value}`" for member in hint)
    if kind is serde.HintKind.SCALAR:
        return {bool: "boolean", int: "integer", float: "number"}.get(hint, "string")
    return "any"


def render_value(value) -> str:
    """One config value as a reader sees it, or an em dash for no value.

    ``None`` and the empty string are both "nothing is set" — a config file
    that leaves the key alone and one that spells it out as empty are the same
    thing to everything downstream.

    A bool is written the way YAML spells it rather than the way Python does,
    since the rendering is read as something to copy into a config file and
    ``True`` is a string there, not a boolean. An enum renders as its value for
    the same reason: that is the spelling the file holds.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _default_column(f: dataclasses.Field) -> str:
    """A field's default as the table writes it, or an em dash for no value."""
    if f.default is dataclasses.MISSING:
        return "—"
    rendered = render_value(f.default)
    return rendered if rendered == "—" else f"`{rendered}`"


def _reference_rows(cls, prefix: str = "") -> list[tuple[str, str, str]]:
    """``(key, values, default)`` for every leaf key under a dataclass.

    Walks through ``serde.classify`` for the same reason ``schema_gen`` does:
    what a hint means is one question with one owner, so the table and the
    schema cannot disagree about which keys exist. A nested dataclass extends
    the dotted prefix; a dict keyed by an enum contributes a ``<placeholder>``
    segment and lists the names it accepts in the row's key.
    """
    rows: list[tuple[str, str, str]] = []
    hints = get_type_hints(cls)
    for f in dataclasses.fields(cls):
        kind, args = serde.classify(hints[f.name])
        key = f"{prefix}{f.name}"
        if kind is serde.HintKind.DATACLASS:
            rows += _reference_rows(hints[f.name], f"{key}.")
        elif kind is serde.HintKind.DICT and args and dataclasses.is_dataclass(args[1]):
            placeholder = f"<{args[0].__name__.lower()}>"
            rows += _reference_rows(args[1], f"{key}.{placeholder}.")
        else:
            rows.append((key, _values_column(hints[f.name]), _default_column(f)))
    return rows


def _key_placeholders(cls) -> list[tuple[str, str]]:
    """``(placeholder, accepted names)`` for each enum-keyed section."""
    notes: list[tuple[str, str]] = []
    hints = get_type_hints(cls)
    for f in dataclasses.fields(cls):
        kind, args = serde.classify(hints[f.name])
        if kind is serde.HintKind.DATACLASS:
            notes += _key_placeholders(hints[f.name])
        elif kind is serde.HintKind.DICT and args and _is_enum(args[0]):
            names = ", ".join(f"`{member.value}`" for member in args[0])
            notes.append((f"<{args[0].__name__.lower()}>", names))
    return notes


def docs_reference() -> str:
    """The generated half of the ``config.sh`` section in ``DOCS_PATH``.

    Beside ``schema_json`` for the same reason: the block a composer asks for
    and the one ``--check`` compares against render through one code path, and
    both derive from the dataclass rather than from a second listing of the keys
    that someone has to remember to update. The prose around it — what the
    reader is for, how the layers rank, the config unification migration — is
    hand-written and lives in ``lib/config.sh``'s header.

    It carries no "do not edit" banner of its own: the whole of ``DOCS_PATH`` is
    composed, and ``bin/local/compose-docs`` puts one at the top of the file.
    """
    lines = [
        "| Scope | File |",
        "|-------|------|",
        f"| Global | `{CONFIG_NAME}` under the [config root](#rootssh) |",
        f"| Project | `{PROJECT_CONFIG_NAME}` at a repo toplevel |",
        "",
        f"A new config file is born holding one line, the modeline that points an "
        f"editor's YAML language server at "
        f"[`{SCHEMA_PATH}`]({_DOCS_TO_ROOT}{SCHEMA_PATH}):",
        "",
        "```yaml",
        CONFIG_HEADER,
        "```",
        "",
        "Every key both files accept:",
        "",
        "| Key | Values | Default |",
        "|-----|--------|---------|",
    ]
    lines += [f"| `{key}` | {values} | {default} |" for key, values, default in
              _reference_rows(WorkbenchConfig)]
    placeholders = _key_placeholders(WorkbenchConfig)
    if placeholders:
        lines.append("")
        lines += [f"`{name}` is one of: {values}" for name, values in placeholders]
    return "\n".join(lines)


def global_config_path() -> Path:
    return workbench_paths.config_dir() / CONFIG_NAME


def project_config_path(project_root: Path | str) -> Path:
    return Path(project_root) / PROJECT_CONFIG_NAME


def read_yaml(path: Path) -> dict:
    """One YAML file as a plain dict, or ``{}`` when there is no file.

    PyYAML when it is installed, ``yq`` otherwise. PyYAML is not a declared
    dependency of this repo (``review_profiles.py`` treats it the same way),
    and ``yq`` already is one — ``lib/state.sh`` cannot work without it — so
    between them there is always a reader.
    """
    if not path.is_file():
        return {}
    try:
        raw = _parse_yaml(path)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # SubprocessError covers a yq that hangs past the timeout: every way
        # this module can fail to read a file has to arrive as ConfigError, or
        # load_config_or_default's fallback does not cover it.
        raise ConfigError(f"{path} is not readable YAML: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must hold a mapping, got {type(raw).__name__}")
    return raw


def _parse_yaml(path: Path):
    if yaml is not None:
        try:
            return yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ValueError(str(exc)) from exc
    if not shutil.which("yq"):
        raise ValueError("neither PyYAML nor yq is available to read YAML")
    result = subprocess.run(
        ["yq", "-o=json", ".", str(path)],
        capture_output=True, text=True, timeout=timeouts.LOCAL,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "yq failed")
    return json.loads(result.stdout or "null")


def yaml_dump(data: dict) -> str:
    """A plain mapping as YAML text, for the files this module writes."""
    if yaml is not None:
        return yaml.safe_dump(data, sort_keys=False)
    if not shutil.which("yq"):
        raise ConfigError("neither PyYAML nor yq is available to write YAML")
    try:
        result = subprocess.run(
            ["yq", "-P", "."], input=json.dumps(data),
            capture_output=True, text=True, timeout=timeouts.LOCAL,
        )
    except subprocess.SubprocessError as exc:
        raise ConfigError(f"could not render YAML: {exc}") from exc
    if result.returncode != 0:
        raise ConfigError(f"could not render YAML: {result.stderr.strip()}")
    return result.stdout


def _deep_merge(base: dict, over: dict) -> dict:
    """``over`` on top of ``base``, recursing into mappings.

    Recursive rather than a top-level update so a project file that sets one
    key under ``review`` keeps the rest of the global ``review`` section. A
    non-mapping value replaces whatever it lands on, including a mapping —
    there is no sensible merge of a scalar into a section.
    """
    merged = dict(base)
    for key, value in over.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True)
class ConfigScope:
    """One file the merge reads, and the name a reader knows it by."""

    name: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def config_scopes(project_root: Path | str | None = None) -> list[ConfigScope]:
    """Every file the config is merged from, lowest precedence first.

    Merge order rather than precedence order, because that is the order the
    loader applies them in and this is what it iterates. A reader wants the
    reverse, so ``config_status`` flips it once on the way out.

    The one owner of which files there are. ``load_config`` merges these and
    ``config_status`` reports on them, so a scope cannot be added to the merge
    without appearing in the report that explains where a value came from.
    """
    scopes = [ConfigScope(GLOBAL_SCOPE, global_config_path())]
    if project_root is not None:
        scopes.append(ConfigScope(PROJECT_SCOPE, project_config_path(project_root)))
    return scopes


def load_config(project_root: Path | str | None = None) -> WorkbenchConfig:
    """The merged, typed config for a scope.

    Raises ``ConfigError`` when a file exists but cannot be read as config —
    a hand-authored file gets a loud failure rather than the silent discard
    ``serde.load_file`` gives a regenerable cache.
    """
    paths = [scope.path for scope in config_scopes(project_root)]

    merged: dict = {}
    for path in paths:
        merged = _deep_merge(merged, read_yaml(path))

    try:
        return serde.from_dict(WorkbenchConfig, merged)
    except (TypeError, ValueError) as exc:
        named = " and ".join(str(p) for p in paths if p.is_file())
        raise ConfigError(f"{named or paths[0]}: {exc}") from exc


def load_config_or_default(
    project_root: Path | str | None = None,
) -> WorkbenchConfig:
    """``load_config`` for callers that must not fail on a bad file.

    The Claude hooks — statusline, session start, the /reuse tracker — run on
    every prompt. A typo in config.yml must not break the user's session, so
    they take built-in defaults and stay quiet; the tools that act on the
    config surface the error instead.
    """
    try:
        return load_config(project_root)
    except ConfigError:
        return WorkbenchConfig()


@dataclass(frozen=True)
class ResolvedKey:
    """One key of the config surface, and which file answered for it.

    ``scope`` is ``None`` when no file named the key and the value is the
    dataclass default. A caller renders that differently from an inherited
    value: one is a decision nobody has made, the other is a decision made
    somewhere else.
    """

    key: str
    value: str
    scope: ConfigScope | None = None

    @property
    def is_default(self) -> bool:
        return self.scope is None


@dataclass(frozen=True)
class StrayKey:
    """A key a config file holds that no version of the surface reads.

    The whole reason ``config status`` exists. ``serde`` drops what it does not
    recognise, so a key spelled slightly wrong is a value that is simply gone,
    and every reader downstream sees the default and says nothing.
    """

    key: str
    scope: ConfigScope


@dataclass(frozen=True)
class ConfigStatus:
    """What the config resolves to right now, and where each piece came from.

    ``scopes`` is highest precedence first — the order the answer is decided
    in, which is the order a reader reasons about, and the reverse of the merge
    order ``config_scopes`` returns.

    ``problems`` holds anything that stopped a file from being read or typed.
    It is separate from ``strays`` because the two cost different things: a
    stray key loses one value, an unreadable file loses the whole scope.
    """

    scopes: list[ConfigScope]
    keys: list[ResolvedKey]
    strays: list[StrayKey]
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def _flatten(data: dict, prefix: str = "") -> dict[str, object]:
    """Every leaf of a raw config mapping, keyed by its dotted path.

    An empty mapping is a leaf: it sets nothing, so recursing into it would
    contribute no key, and treating it as one records that the file mentioned
    the section at all.
    """
    flat: dict[str, object] = {}
    for key, value in data.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _entry_rows(
    cls, held: dict | None, provenance: dict[str, ConfigScope], prefix: str,
) -> list[ResolvedKey]:
    """Rows for the entries an enum-keyed section actually holds.

    An enum key contributes its value rather than its member name, because the
    value is what the config file spells.
    """
    rows: list[ResolvedKey] = []
    for entry, value in (held or {}).items():
        name = entry.value if isinstance(entry, Enum) else entry
        rows += _resolved_rows(cls, value, provenance, f"{prefix}{name}.")
    return rows


def _resolved_rows(
    cls, obj, provenance: dict[str, ConfigScope], prefix: str = "",
) -> list[ResolvedKey]:
    """One row per leaf key of a typed config object.

    Walks ``dataclasses.fields`` through ``serde.classify``, the same way
    ``_reference_rows`` builds the docs table and ``schema_gen`` builds the
    schema — so the keys reported here are the keys the workbench reads, with
    no second listing to keep in step.

    An enum-keyed section expands over the entries the config actually holds
    rather than over every member of the enum. A phase nobody has overridden
    has nothing to report, and listing all of them would bury the ones that do.
    """
    rows: list[ResolvedKey] = []
    hints = get_type_hints(cls)
    for f in dataclasses.fields(cls):
        kind, args = serde.classify(hints[f.name])
        key = f"{prefix}{f.name}"
        value = getattr(obj, f.name)
        if kind is serde.HintKind.DATACLASS:
            rows += _resolved_rows(hints[f.name], value, provenance, f"{key}.")
        elif kind is serde.HintKind.DICT and args and dataclasses.is_dataclass(args[1]):
            rows += _entry_rows(args[1], value, provenance, f"{key}.")
        else:
            rows.append(ResolvedKey(key, render_value(value), provenance.get(key)))
    return rows


def config_status(project_root: Path | str | None = None) -> ConfigStatus:
    """The resolved config for a scope, with the file each value came from.

    ``load_config`` answers what the value is and discards how it got there,
    which leaves a key written into the wrong file indistinguishable from a key
    nobody ever set. This answers both, and reports what it could not read
    rather than raising: a command whose whole job is diagnosing a config file
    has to survive the file being the problem.
    """
    import schema_gen

    scopes = config_scopes(project_root)
    problems: list[str] = []
    strays: list[StrayKey] = []
    provenance: dict[str, ConfigScope] = {}
    schema = schema_gen.dataclass_to_schema(WorkbenchConfig)

    merged: dict = {}
    loaded: list[tuple[ConfigScope, dict]] = []
    for scope in scopes:
        try:
            raw = read_yaml(scope.path)
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        loaded.append((scope, raw))
        merged = _deep_merge(merged, raw)
        flat = _flatten(raw)
        provenance.update(dict.fromkeys(flat, scope))
        strays += [StrayKey(key, scope) for key in flat
                   if not _schema_accepts(schema, key)]

    try:
        config = serde.from_dict(WorkbenchConfig, merged)
    except (TypeError, ValueError) as exc:
        problems += _typing_problems(loaded) or [f"{scopes[0].path}: {exc}"]
        return ConfigStatus(list(reversed(scopes)), [], strays, problems)

    keys = _resolved_rows(WorkbenchConfig, config, provenance)
    return ConfigStatus(list(reversed(scopes)), keys, strays, problems)


def _typing_problems(loaded: list[tuple[ConfigScope, dict]]) -> list[str]:
    """Which individual scopes hold a value the config cannot be built from.

    The merged failure names every file that exists, because that is all it
    knows. Typing each scope on its own instead names the file to open — which
    is the whole question a reader has when a value is rejected.

    Empty when no scope fails alone, which leaves the caller its joint message:
    a merge can only override, so this should not happen, and inventing a
    culprit would be worse than repeating what the loader would have said.
    """
    problems: list[str] = []
    for scope, raw in loaded:
        try:
            serde.from_dict(WorkbenchConfig, raw)
        except (TypeError, ValueError) as exc:
            problems.append(f"{scope.path}: {exc}")
    return problems


class KeyVerdict(StrEnum):
    """What ``check_key`` found for a dotted key.

    Two ways of being unrecognised, because they mean different things to
    whoever has to act on them. A key this checkout does not define is a typo
    or a key built at runtime. A key this checkout defines but the installed
    workbench does not is the two disagreeing about where a value lives, which
    is what a worktree standing off to the side of `main` produces.
    """

    KNOWN = "known"
    UNKNOWN_HERE = "unknown_here"
    UNKNOWN_INSTALLED = "unknown_installed"


@dataclass(frozen=True)
class KeyCheck:
    """Whether one dotted key names something the workbench actually reads.

    Read ``ok`` rather than comparing ``verdict``, so a call site states what
    it is asking. ``source`` names the schema that refused, and is empty when
    nothing did or when the refusal came from this checkout's own dataclasses.
    """

    verdict: KeyVerdict
    key: str
    source: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict is KeyVerdict.KNOWN

    @property
    def reason(self) -> str:
        """Why the key was refused, or ``""`` when it was not."""
        if self.verdict is KeyVerdict.UNKNOWN_HERE:
            return f"{self.key} is not a key WorkbenchConfig defines"
        if self.verdict is KeyVerdict.UNKNOWN_INSTALLED:
            return (
                f"{self.key} is a key this checkout defines and the installed "
                f"workbench does not ({self.source}) — the two disagree about "
                f"where the value lives, and the file is read by the installed one"
            )
        return ""


def _object_branch(schema: dict) -> dict:
    """The object half of a schema fragment.

    An optional field is written as a union with null, so the properties of the
    thing it holds are one level in.
    """
    branches = schema.get("oneOf")
    if not isinstance(branches, list):
        return schema
    for branch in branches:
        if isinstance(branch, dict) and branch.get("type") != "null":
            return branch
    return schema


def _named_property(schema: dict, name: str) -> dict | None:
    """The fragment one key segment lands on, or ``None`` when it lands nowhere.

    Permissive by design, and only in one direction: a fragment that lists its
    properties and does not list this one refuses, and everything else accepts.
    An open fragment — what ``schema_gen`` emits for a hint it cannot describe
    — says nothing about which keys exist, and a check that cannot see the keys
    must not be the thing that blocks a write.
    """
    schema = _object_branch(schema)
    properties = schema.get("properties")
    if isinstance(properties, dict) and name in properties:
        found = properties[name]
        return found if isinstance(found, dict) else {}
    names = schema.get("propertyNames")
    if isinstance(names, dict) and isinstance(names.get("enum"), list):
        if name not in names["enum"]:
            return None
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        return extra
    if isinstance(properties, dict):
        return None
    return {}


def _schema_accepts(schema: dict, key: str) -> bool:
    """Whether a JSON Schema describes a document that can hold this dotted key."""
    cursor: dict | None = schema
    for part in key.split("."):
        if cursor is None:
            return False
        cursor = _named_property(cursor, part)
    return cursor is not None


def installed_schema_path() -> Path | None:
    """``config.schema.json`` from the workbench installed on this machine.

    ``None`` when there is no installed workbench to ask: a CI checkout, a
    container, a clone with nothing on ``PATH``. The caller then has only its
    own checkout's answer, which is where this started.

    The launcher is resolved rather than read, so a ``~/.local/bin`` symlink
    into a bare repo's ``main`` worktree lands on that worktree whichever
    worktree the caller is running from.
    """
    launcher = shutil.which(INSTALLED_LAUNCHER)
    if not launcher:
        return None
    schema = Path(launcher).resolve().parents[1] / SCHEMA_PATH
    return schema if schema.is_file() else None


def check_key(key: str) -> KeyCheck:
    """Whether ``key`` is one the workbench reads, here and where it is installed.

    Two surfaces, because only one of them can be trusted to be current. This
    checkout answers first and catches a typo or a key assembled at runtime.
    The installed workbench answers second and is the only one that can catch
    the case this guard exists for: a worktree weeks behind ``main`` writing a
    key that was valid when the branch was cut and has since moved, into a file
    every repo on the machine shares.

    The same comparison refuses the opposite direction, and that one is a
    contributor rather than a mistake: a key added on a branch is a key the
    installed workbench has not learned yet, so writing it here is refused
    until the branch reaches ``main`` and the install follows. There is no flag
    for it, deliberately — an override would be reached for by exactly the
    stale writer this exists to stop. Edit the file by hand meanwhile; nothing
    checks a hand-edit, and a key only your branch reads is one only your
    branch has to load.
    """
    import schema_gen

    if not _schema_accepts(schema_gen.dataclass_to_schema(WorkbenchConfig), key):
        return KeyCheck(KeyVerdict.UNKNOWN_HERE, key)
    path = installed_schema_path()
    if path is None:
        return KeyCheck(KeyVerdict.KNOWN, key)
    try:
        installed = json.loads(path.read_text())
    except (OSError, ValueError):
        # A broken installed schema leaves the local surface, which is the only
        # check there was before there were two. Refusing here would turn one
        # unreadable file into a config nobody on the machine can write.
        return KeyCheck(KeyVerdict.KNOWN, key)
    if not isinstance(installed, dict) or _schema_accepts(installed, key):
        return KeyCheck(KeyVerdict.KNOWN, key)
    return KeyCheck(KeyVerdict.UNKNOWN_INSTALLED, key, str(path))


def set_value(key: str, value: str, path: Path | None = None) -> None:
    """Write one dotted key into a config file, creating it if needed.

    Through ``yq -i`` so the write preserves the comments and ordering of a
    file the user hand-authored. PyYAML is the fallback and does not preserve
    them, which is why it is second rather than first.

    Raises ``ConfigError`` when the write cannot happen — a caller running in a
    Claude hook has one exception type to catch, whichever writer ran — and the
    ``ConfigKeyError`` subclass when ``key`` is not one the workbench reads.

    ``check_key`` is what decides, and it asks the installed workbench as well
    as this checkout. Both files this writes are shared — the global one by
    every repo on the machine, a project one by everybody who clones the repo —
    and ``serde`` drops an unrecognised key on the way back in. Without the
    check the write reports success and the value is simply gone, which is a
    rule quietly not applying rather than anything anybody can see.
    """
    if path is None:
        path = global_config_path()
    check = check_key(key)
    if not check.ok:
        raise ConfigKeyError(f"not writing {path}: {check.reason}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(CONFIG_HEADER + "\n")
    if shutil.which("yq"):
        env = dict(os.environ, WB_CONFIG_VALUE=value)
        try:
            subprocess.run(
                ["yq", "-i", f".{key} = strenv(WB_CONFIG_VALUE)", str(path)],
                check=True, timeout=timeouts.LOCAL, env=env,
            )
        except subprocess.SubprocessError as exc:
            raise ConfigError(f"could not write {key} to {path}: {exc}") from exc
        return
    _set_value_with_pyyaml(path, key, value)


def set_project_value(key: str, value: str, project_root: Path | str) -> None:
    """Write one dotted key into a repo's ``.workbench.yml``.

    Shares ``set_value`` rather than reimplementing the write: the yq-first
    ordering exists so a hand-authored file keeps its comments, and a second
    writer would have to reproduce that ordering and would drift from it. The
    key check comes with it, which a repo file needs as much as the global one
    — this one is committed, so a dead key travels to everybody who clones.

    The file is committed in the consumer repo, so this dirties a working tree
    the user may not have meant to modify. That is the same trade
    ``adopt_project_review_yml`` already makes, and the caller treats a failed
    write as a non-event.
    """
    set_value(key, value, project_config_path(project_root))


def _set_value_with_pyyaml(path: Path, key: str, value: str) -> None:
    if yaml is None:
        raise ConfigError("neither yq nor PyYAML is available to write config")
    # PyYAML re-renders the document, so every comment in it is lost. The
    # modeline is the one comment this module owns, so it is the one it can put
    # back; a comment the user wrote is gone, which is why yq goes first.
    had_header = path.is_file() and path.read_text().startswith(CONFIG_HEADER)
    data = read_yaml(path)
    cursor = data
    parts = key.split(".")
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value
    text = yaml_dump(data)
    if had_header:
        text = f"{CONFIG_HEADER}\n{text}"
    path.write_text(text)
