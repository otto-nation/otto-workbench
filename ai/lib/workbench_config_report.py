"""How the workbench config is shown.

Three renderings of one surface: the JSON Schema an editor validates a config
file against, the key reference the docs print, and the resolved status
``otto-workbench config status`` reports. All three walk ``WorkbenchConfig``
through ``serde.classify`` rather than listing the keys a second time, so none
of them can disagree with the config ``workbench_config.load_config`` returns.

Reading only. Nothing here opens a file for writing or decides whether a key
may be written — that is ``workbench_config_write``. The split is what the
config *is* (``workbench_config``), how it is *shown* (here), and how it is
*changed* (there), and it runs one way: both of the others import the config,
neither imports this.
"""

# doc-group: platform

from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path
from typing import get_type_hints

import serde
from workbench_config import (
    CONFIG_HEADER,
    CONFIG_NAME,
    PROJECT_CONFIG_NAME,
    SCHEMA_PATH,
    ConfigError,
    ConfigScope,
    WorkbenchConfig,
    config_scopes,
    deep_merge,
    read_yaml,
    schema_accepts,
    surface_schema,
)

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


def schema_json() -> str:
    """``WorkbenchConfig`` as the JSON Schema text ``config.schema.json`` holds.

    Here rather than in the generator script so the write and the ``--check``
    comparison render through one code path, and so the schema's wrapper
    metadata sits beside the key reference generated from the same dataclass.

    Nothing sets ``additionalProperties: false``, so an editor validating
    against this will not flag a misspelled key. That is deliberate: the schema
    is committed at one version while the config on disk may have been written
    by a newer workbench, and a closed schema would turn every new key into an
    error in the editor of anyone who has not pulled yet. ``serde`` drops keys
    it does not know, so an unrecognised key is inert either way.
    """
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "otto-workbench configuration",
        "description": (
            f"Generated from WorkbenchConfig by {GENERATOR_PATH}. Do not edit by hand."
        ),
        **surface_schema(),
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
        f"| Project | `{PROJECT_CONFIG_NAME}` at a repo toplevel |",
        f"| Container | `{PROJECT_CONFIG_NAME}` beside a bare repo's worktrees |",
        f"| Global | `{CONFIG_NAME}` under the [config root](#rootssh) |",
        "",
        f"A new config file is born holding one line, the modeline that points an "
        f"editor's YAML language server at "
        f"[`{SCHEMA_PATH}`]({_DOCS_TO_ROOT}{SCHEMA_PATH}):",
        "",
        "```yaml",
        CONFIG_HEADER,
        "```",
        "",
        "Every key any of them accepts:",
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


@dataclasses.dataclass(frozen=True)
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


@dataclasses.dataclass(frozen=True)
class StrayKey:
    """A key a config file holds that no version of the surface reads.

    The whole reason ``config status`` exists. ``serde`` drops what it does not
    recognise, so a key spelled slightly wrong is a value that is simply gone,
    and every reader downstream sees the default and says nothing.
    """

    key: str
    scope: ConfigScope


@dataclasses.dataclass(frozen=True)
class DroppedValue:
    """A key the surface reads, holding a value the field cannot be built from.

    The other half of what ``StrayKey`` catches, one level down. ``serde``
    restores a scalar where it can and omits it where it cannot, so a boolean
    written as ``"true"`` resolves to the field's default with the file still
    saying otherwise — the right key, the wrong shape, and no complaint.

    ``held`` is what the file spells and ``read`` is what the loader gave back,
    both rendered the way ``config status`` prints a value, because a reader
    diagnosing this needs to see the two disagree.
    """

    key: str
    scope: ConfigScope
    held: str
    read: str


@dataclasses.dataclass(frozen=True)
class ConfigStatus:
    """What the config resolves to right now, and where each piece came from.

    ``scopes`` is highest precedence first — the order the answer is decided
    in, which is the order a reader reasons about, and the reverse of the merge
    order ``config_scopes`` returns.

    ``problems`` holds anything that stopped a file from being read or typed.
    It is separate from ``strays`` and ``dropped`` because the three cost
    different things: a stray key or a dropped value loses one value, an
    unreadable file loses the whole scope.
    """

    scopes: list[ConfigScope]
    keys: list[ResolvedKey]
    strays: list[StrayKey]
    problems: list[str]
    dropped: list[DroppedValue] = dataclasses.field(default_factory=list)

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
    scopes = config_scopes(project_root)
    problems: list[str] = []
    strays: list[StrayKey] = []
    provenance: dict[str, ConfigScope] = {}
    held: dict[str, object] = {}
    schema = surface_schema()

    merged: dict = {}
    loaded: list[tuple[ConfigScope, dict]] = []
    for scope in scopes:
        try:
            raw = read_yaml(scope.path)
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        loaded.append((scope, raw))
        merged = deep_merge(merged, raw)
        flat = _flatten(raw)
        provenance.update(dict.fromkeys(flat, scope))
        held.update(flat)
        strays += [StrayKey(key, scope) for key in flat
                   if not schema_accepts(schema, key)]

    try:
        config = serde.from_dict(WorkbenchConfig, merged)
    except (TypeError, ValueError) as exc:
        problems += _typing_problems(loaded) or [f"{scopes[0].path}: {exc}"]
        return ConfigStatus(list(reversed(scopes)), [], strays, problems)

    keys = _resolved_rows(WorkbenchConfig, config, provenance)
    dropped = _dropped_values(held, provenance, keys)
    return ConfigStatus(list(reversed(scopes)), keys, strays, problems, dropped)


def _dropped_values(
    held: dict[str, object],
    provenance: dict[str, ConfigScope],
    keys: list[ResolvedKey],
) -> list[DroppedValue]:
    """The keys whose winning file says one thing and the loaded config another.

    Derived from the loader rather than from a second type table, which is the
    only way the report can agree with it. ``serde`` restores a scalar wherever
    it can — ``"3"`` into an int field is not a dropped value, it is a recovered
    one — so the question is not whether the types match but whether the value
    survived, and the resolved row is what answers it.

    ``held`` holds the leaf of whichever scope won the key, the same one
    ``provenance`` names, so a value overridden by a higher scope is not
    reported against the file it was overridden in.
    """
    resolved = {row.key: row.value for row in keys}
    return [
        DroppedValue(key, provenance[key], render_value(value), resolved[key])
        for key, value in held.items()
        if key in resolved and render_value(value) != resolved[key]
    ]


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
