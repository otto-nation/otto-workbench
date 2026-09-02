"""The workbench's typed configuration.

One file per scope, deep-merged and typed into ``WorkbenchConfig``. The
dataclasses here are the single definition: they type the runtime lookups, they
generate ``config.schema.json`` (``bin/local/generate-config-schema``), and
their ``Phase``-keyed maps make a phase a valid config key the moment it
becomes an enum member.

Three scopes, most specific first:

    project    ``.workbench.yml`` at the work-tree root — this checkout
    container  ``.workbench.yml`` beside a bare repo's worktrees — this repo
    global     ``config.yml`` under the config root — every repo

The container scope exists only in the bare-repo worktree layout, where every
checkout is a peer of the bare ``.git`` inside a container directory. It is the
scope for an answer that belongs to the repo but cannot be committed to it: a
worktree file has to be copied into each of the ~100 checkouts a monorepo
accumulates, is absent in whichever one ``wt switch -c`` cut this morning, and
is deleted with the worktree by ``wt remove``. A file at the container is
outside every checkout, so it needs no gitignore entry and survives all three.

Ordered by specificity, so the checkout in front of you outranks the repo and
the repo outranks the machine. A repo that is a plain clone has no container
and keeps exactly the two scopes it always had.

Those are layers 4 through 6 of the precedence chain, behind CLI flags and env
vars:

    CLI flag > WORKBENCH_AI_<PHASE>_* > WORKBENCH_AI_* > project > container > global

so nothing here overrides a value a caller passed or exported.

Bash reads through here too, rather than parsing the same files a second time:
``lib/config.sh``'s ``wb_config_get`` and the machine profile's registry table
both go out to ``otto-workbench config get``, which is ``lib/config_cli.py``
over ``config_status``. A partial reader in another language is what let the
machine profile call a repo's tracker ``unset`` while the SessionStart line in
the same session named it — so if a bash caller needs a config value, give it
that command rather than a third implementation of the scopes.

What the config *is* is here: the dataclasses, the files, the merge, and the
key surface a dotted key is judged against. How it is *shown* —
``config.schema.json``, the docs key reference, ``config status`` — is
``workbench_config_report``; how it is *changed* is ``workbench_config_write``.
Both of those import this one and neither is imported back, so the module every
Claude hook loads on every prompt carries nothing a reader does not need.
"""

# doc-group: platform

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import serde
import timeouts
import workbench_paths
from agent_types import Effort, Phase, Thinking

# `git_layout` is a workbench-wide module rather than an `ai/lib` one, because
# the permission mirror reads the same layout. In a checkout that is one
# directory up; in the otto-ai-tools tarball, which flattens both into one
# `lib/`, it is already beside this file and the path below does not exist.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
from git_layout import container_dir  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


CONFIG_NAME = "config.yml"
PROJECT_CONFIG_NAME = ".workbench.yml"

# What a reader calls each scope. Beside the filenames because they name the
# same files, and because ``config_scopes`` pairs them up. The container scope
# reuses ``PROJECT_CONFIG_NAME``: it is the same file in a directory one level
# out, so a copy moved up from a worktree keeps working.
GLOBAL_SCOPE = "global"
CONTAINER_SCOPE = "container"
PROJECT_SCOPE = "project"

# What a reader calls the answer when no file supplied one. Not a scope — it
# names no file and ``config_scopes`` never returns it — but every reader that
# reports where a value came from needs a word for the built-in default, and a
# caller parsing that report needs the same word the report prints.
DEFAULT_SCOPE = "default"

# Where the generated schema lives, repo-relative, and the raw URL that serves
# it. One spelling of the path: bin/local/generate-config-schema writes there,
# the modeline below points there, ``workbench_config_write`` reads it out of
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


class ConfigValueError(ConfigError):
    """A write named a real key and a value the field behind it cannot hold.

    The same cost as ``ConfigKeyError`` and for the same reason: ``serde``
    restores what it can and omits what it cannot, so a value of the wrong type
    is a key that reads back as its default with nothing said. Refusing at write
    time is the only place the caller still knows what it asked for.
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
class AgentConfig:
    """How this machine sizes an agent invocation.

    ``model``, ``thinking`` and ``provider`` are the file forms of
    WORKBENCH_AI_MODEL, _THINKING and _PROVIDER; ``phases`` is the file form
    of the per-phase keys ``Phase.model_env_key`` derives. A phase entry beats
    the section-level value, matching how a phase env key beats the global one.

    Top-level rather than under ``review``: a phase is any agent invocation the
    workbench sizes, and the review pipeline is one domain of them. Which model
    a fix pass runs is not a review setting just because reviews were the first
    caller to have one.
    """

    model: str | None = None
    thinking: Thinking | None = None
    provider: str | None = None
    phases: dict[Phase, PhaseOverride] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewConfig:
    """Review pipeline settings.

    ``effort`` is the only knob left here: it selects a depth preset that skips
    phases and moves thresholds, and no other domain has one. Everything a
    single invocation is sized by lives under ``agent``.
    """

    effort: Effort | None = None


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
    agent: AgentConfig = field(default_factory=AgentConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    issue_tracker: IssueTrackerConfig = field(default_factory=IssueTrackerConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)


def global_config_path() -> Path:
    return workbench_paths.config_dir() / CONFIG_NAME


def project_config_path(project_root: Path | str) -> Path:
    return Path(project_root) / PROJECT_CONFIG_NAME


def container_config_path(project_root: Path | str) -> Path | None:
    """The config file above a bare repo's worktrees, or ``None`` for a clone.

    ``git_layout.container_dir`` decides whether there is one, so this scope
    appears exactly where the permission mirror already writes and nowhere
    else. A plain clone, a linked worktree of one, and a directory in no repo
    at all each answer ``None``, which is what keeps a repo outside the layout
    on the two scopes it has always had.

    Shells out to git, so a caller in a loop should hold on to the answer.
    Nothing loops today: the scope list is built once per ``load_config``, and
    the hooks that run on every prompt load the global scope alone. A writer
    resolving the root twice — once to decide the scope, once inside
    ``set_container_value`` — pays two more ``rev-parse`` reads at a prompt a
    person is standing at, which is the price of the refusal living in one
    place instead of at each call site.
    """
    container = container_dir(str(project_root))
    return None if container is None else Path(container) / PROJECT_CONFIG_NAME


def read_yaml(path: Path) -> dict:
    """One YAML file as a plain dict, or ``{}`` when there is no file.

    PyYAML when it is installed, ``yq`` otherwise. PyYAML is not a declared
    dependency of this repo (``review_grouping.py`` treats it the same way),
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


def deep_merge(base: dict, over: dict) -> dict:
    """``over`` on top of ``base``, recursing into mappings.

    Recursive rather than a top-level update so a project file that sets one
    key under ``review`` keeps the rest of the global ``review`` section. A
    non-mapping value replaces whatever it lands on, including a mapping —
    there is no sensible merge of a scalar into a section.

    Published because ``config_status`` merges the same scopes a second time,
    to record which file answered for each key as it goes. Two merges with one
    rule between them, rather than a report that can disagree with the loader
    about what the config resolves to.
    """
    merged = dict(base)
    for key, value in over.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
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

    The container sits between the two older scopes: it speaks for one repo
    where the global file speaks for every repo, and the checkout in front of
    you speaks for itself. A repo outside the bare-repo layout has no container
    and contributes no third scope, rather than a scope naming a file that
    could never exist.
    """
    scopes = [ConfigScope(GLOBAL_SCOPE, global_config_path())]
    if project_root is None:
        return scopes
    container = container_config_path(project_root)
    if container is not None:
        scopes.append(ConfigScope(CONTAINER_SCOPE, container))
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
        merged = deep_merge(merged, read_yaml(path))

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


def surface_schema() -> dict:
    """``WorkbenchConfig`` as a JSON Schema fragment describing its keys.

    The one caller of ``schema_gen.dataclass_to_schema`` for this dataclass, so
    the schema the docs publish, the one ``config status`` measures strays
    against, and the one ``defines_key`` resolves a dotted key through are the
    same object rather than three renderings that could drift.

    ``schema_gen`` is imported inside the function rather than at module scope.
    Every Claude hook loads this module on every prompt to read one config
    value; the schema walk is only wanted by the three readers above, and none
    of them is on that path.
    """
    import schema_gen

    return schema_gen.dataclass_to_schema(WorkbenchConfig)


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


def schema_at(schema: dict, key: str) -> dict | None:
    """The fragment a dotted key lands on, or ``None`` when it lands nowhere.

    Takes the schema rather than reaching for ``surface_schema`` so the same
    walk answers for the *installed* workbench's committed schema, which is the
    second surface ``workbench_config_write.check_key`` judges a write against.
    """
    cursor: dict | None = schema
    for part in key.split("."):
        if cursor is None:
            return None
        cursor = _named_property(cursor, part)
    return cursor


def schema_accepts(schema: dict, key: str) -> bool:
    """Whether a JSON Schema describes a document that can hold this dotted key."""
    return schema_at(schema, key) is not None


def schema_type(fragment: dict) -> str | None:
    """The JSON Schema type a fragment names, or ``None`` when it names none.

    Reads through the null half of an optional field the same way the key walk
    does, so ``str | None`` answers ``"string"`` rather than a union nothing can
    act on.

    ``None`` means the fragment is open — what ``schema_gen`` emits for a hint
    it cannot describe — and is permissive for the same reason the key walk is:
    a check that cannot see the type must not be the thing that refuses a write.
    """
    found = _object_branch(fragment).get("type")
    return found if isinstance(found, str) else None


def defines_key(key: str) -> bool:
    """Whether ``key`` names something this checkout's ``WorkbenchConfig`` reads.

    The local half of ``workbench_config_write.check_key``, on its own for the
    readers. A write is judged by the installed workbench too, because the file
    outlives the checkout that wrote it and is read by whatever is on ``PATH``
    afterwards. A read has no such gap: it resolves the key here and now, so a
    key only this branch defines is one this branch may perfectly well ask for.
    """
    return schema_accepts(surface_schema(), key)
