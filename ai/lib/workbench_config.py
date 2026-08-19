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
import workbench_paths
from review_common import Effort, Phase, Thinking

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


CONFIG_NAME = "config.yml"
PROJECT_CONFIG_NAME = ".workbench.yml"

# Where the generated schema lives, repo-relative, and the raw URL that serves
# it. One spelling of the path: bin/local/generate-config-schema writes there,
# the modeline below points there, and tests/test_workbench_config.py fails if
# the two stop agreeing — so moving the file is a one-line change here.
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

# Where the generated key reference is spliced into the prose that surrounds it.
DOCS_PATH = "docs/libraries.md"
DOCS_MARKER = "CONFIG-REFERENCE"

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
ISSUE_PROVIDER_KEY = "review.issue_tracker.provider"

_YQ_TIMEOUT = 10


class ConfigError(ValueError):
    """A config file exists but cannot be read as one.

    A ``ValueError`` subclass because that is what ``serde`` raises for the
    unusable value underneath, so a caller that already catches ``ValueError``
    keeps working.
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
    issue_tracker: IssueTrackerConfig = field(default_factory=IssueTrackerConfig)


@dataclass(frozen=True)
class ReuseConfig:
    """``level`` is the active pick; ``None`` means fall back to ``default``."""

    level: ReuseLevel | None = None
    default: ReuseLevel = ReuseLevel.FULL


@dataclass(frozen=True)
class WorkbenchConfig:
    reuse: ReuseConfig = field(default_factory=ReuseConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)


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


def _default_column(f: dataclasses.Field) -> str:
    """A field's default as the table writes it, or an em dash for no value.

    ``None`` and the empty string are both "nothing is set" to a reader — the
    key is absent from a config file that leaves it alone either way.
    """
    if f.default is dataclasses.MISSING or f.default is None or f.default == "":
        return "—"
    return f"`{f.default}`"


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

    Beside ``schema_json`` for the same reason: the write and the ``--check``
    comparison in ``bin/local/generate-config-schema`` render through one code
    path, and both derive from the dataclass rather than from a second listing
    of the keys that someone has to remember to update. The prose around the
    spliced block — what the reader is for, how the layers rank, the config
    unification migration — is hand-written and stays that way.
    """
    lines = [
        "<!-- AUTO-GENERATED — do not edit directly -->",
        f"<!-- Regenerate: {GENERATOR_PATH} -->",
        "",
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
        capture_output=True, text=True, timeout=_YQ_TIMEOUT,
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
            capture_output=True, text=True, timeout=_YQ_TIMEOUT,
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


def load_config(project_root: Path | str | None = None) -> WorkbenchConfig:
    """The merged, typed config for a scope.

    Raises ``ConfigError`` when a file exists but cannot be read as config —
    a hand-authored file gets a loud failure rather than the silent discard
    ``serde.load_file`` gives a regenerable cache.
    """
    paths = [global_config_path()]
    if project_root is not None:
        paths.append(project_config_path(project_root))

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


def set_value(key: str, value: str, path: Path | None = None) -> None:
    """Write one dotted key into a config file, creating it if needed.

    Through ``yq -i`` so the write preserves the comments and ordering of a
    file the user hand-authored. PyYAML is the fallback and does not preserve
    them, which is why it is second rather than first.

    Raises ``ConfigError`` when the write cannot happen — a caller running in a
    Claude hook has one exception type to catch, whichever writer ran.

    ``key`` is not checked against the dataclasses: a typo writes a stray field
    that ``serde`` then ignores on the way back in. Every call site today passes
    a literal; a call site that builds a key wants a check here first.
    """
    if path is None:
        path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(CONFIG_HEADER + "\n")
    if shutil.which("yq"):
        env = dict(os.environ, WB_CONFIG_VALUE=value)
        try:
            subprocess.run(
                ["yq", "-i", f".{key} = strenv(WB_CONFIG_VALUE)", str(path)],
                check=True, timeout=_YQ_TIMEOUT, env=env,
            )
        except subprocess.SubprocessError as exc:
            raise ConfigError(f"could not write {key} to {path}: {exc}") from exc
        return
    _set_value_with_pyyaml(path, key, value)


def set_project_value(key: str, value: str, project_root: Path | str) -> None:
    """Write one dotted key into a repo's ``.workbench.yml``.

    Shares ``set_value`` rather than reimplementing the write: the yq-first
    ordering exists so a hand-authored file keeps its comments, and a second
    writer would have to reproduce that ordering and would drift from it.

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
