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

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

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
SCHEMA_URL = (
    "https://raw.githubusercontent.com/otto-nation/otto-workbench/main/"
    + SCHEMA_PATH
)
# The modeline a config file is born with, so an editor's YAML language server
# validates the file against that schema as the user hand-edits it.
# lib/config.sh spells the same string for the files bash creates;
# tests/config.bats cross-validates the pair.
CONFIG_HEADER = f"# yaml-language-server: $schema={SCHEMA_URL}"

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
    provider: IssueProvider = IssueProvider.LINEAR
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
            "Generated from WorkbenchConfig by bin/local/generate-config-schema. "
            "Do not edit by hand."
        ),
        **schema_gen.dataclass_to_schema(WorkbenchConfig),
    }
    return json.dumps(schema, indent=2)


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


def set_value(key: str, value: str) -> None:
    """Write one dotted key into the global config, creating it if needed.

    Through ``yq -i`` so the write preserves the comments and ordering of a
    file the user hand-authored. PyYAML is the fallback and does not preserve
    them, which is why it is second rather than first.

    Raises ``ConfigError`` when the write cannot happen — a caller running in a
    Claude hook has one exception type to catch, whichever writer ran.

    ``key`` is not checked against the dataclasses: a typo writes a stray field
    that ``serde`` then ignores on the way back in. Every call site today passes
    a literal; a call site that builds a key wants a check here first.
    """
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
