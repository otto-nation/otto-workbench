"""What the tool registries say about the scripts an MCP client may reach.

The ``*/registry.yml`` files already document every workbench script: a
description, and for the ones meant to be reached directly a ``when_to_use``
and a ``usage`` line. ``visibility`` is the field that says who a tool is for —
``full`` and ``brief`` entries are rendered into the rules Claude loads,
``hidden`` ones are implementation details of another tool and are rendered
nowhere.

The MCP server reads the same field, so a tool hidden from a reader is also
absent from ``tools/list``. Registering a script is the act that offers it. The
alternative — every marker-bearing script exposed — puts ``ci-check``,
``pr-rebase`` and ``pr-describe`` in front of a client as peers of ``pr``, the
CLI whose subcommands run them, and a client picking between them is choosing
between a tool and its own internals.

Only ``meta.validation: bindir`` registries are read. That value is the
declaration that ``meta.source`` is a directory of executables with one
``tools[]`` entry per file, which is exactly the mapping wanted here, and
``bin/local/validate-registries`` already enforces both directions of it — so
the paths built below cannot drift from the files on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from workbench_config import read_yaml

# The component registries, at the two depths lib/registries.sh globs. Brew
# stacks and `*.env.yml` files are not scanned: neither describes a directory
# of scripts, so neither can name one the server would probe.
REGISTRY_GLOBS = ("*/registry.yml", "*/*/registry.yml")

# meta.validation value declaring that meta.source is a directory of
# executables, one per tools[] entry.
BINDIR_VALIDATION = "bindir"


class Visibility(str, Enum):
    """Who a registered tool is documented for.

    The values are the strings in the registry files and in
    ``lib/registries.sh``; they are a wire format shared with the shell and do
    not change with the enum.
    """

    FULL = "full"
    BRIEF = "brief"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class RegistryEntry:
    """One ``tools[]`` entry, in the terms the MCP server needs."""

    name: str
    description: str
    visibility: Visibility
    when_to_use: str = ""
    usage: str = ""

    @property
    def offered(self) -> bool:
        """True when a client may see this tool."""
        return self.visibility is not Visibility.HIDDEN

    @property
    def tool_description(self) -> str:
        """The description a client reads, from everything the entry documents.

        A ``full`` entry carries the two fields that answer a caller's actual
        questions — when this tool is the right one and how its subcommands
        spell out — so they belong in the description rather than in a rule
        file the client may never have loaded. A ``brief`` entry has neither
        and reduces to its one line.
        """
        parts = [self.description]
        if self.when_to_use:
            parts.append(f"When to use: {self.when_to_use}")
        if self.usage:
            parts.append(f"Usage: {self.usage}")
        return "\n\n".join(parts)


def load_registry_entries(root: Path | str) -> dict[Path, RegistryEntry]:
    """Map every registered script under *root* to what its registry says.

    Keys are resolved absolute paths, so a caller holding a script path can ask
    about it directly. A script with no key here is registered nowhere, which
    the MCP server treats the same as hidden and
    ``bin/local/validate-tool-schema`` treats as a build failure.
    """
    base = Path(root)
    entries: dict[Path, RegistryEntry] = {}
    for path in _registry_files(base):
        entries.update(_bindir_entries(base, path))
    return entries


def _registry_files(base: Path) -> list[Path]:
    return sorted({f for pattern in REGISTRY_GLOBS for f in base.glob(pattern) if f.is_file()})


def _bindir_entries(base: Path, path: Path) -> dict[Path, RegistryEntry]:
    """The entries of one registry, keyed by the script each one names."""
    document = read_yaml(path)
    meta = document.get("meta") or {}
    if meta.get("validation") != BINDIR_VALIDATION or not meta.get("source"):
        return {}
    bindir = (base / meta["source"]).resolve()
    return {bindir / tool["name"]: _entry(tool)
            for tool in document.get("tools") or [] if tool.get("name")}


def _entry(tool: dict) -> RegistryEntry:
    return RegistryEntry(
        name=tool["name"],
        description=tool.get("description") or "",
        visibility=_visibility(tool.get("visibility")),
        when_to_use=tool.get("when_to_use") or "",
        usage=tool.get("usage") or "",
    )


def _visibility(value) -> Visibility:
    try:
        return Visibility(value)
    except ValueError:
        # Fail closed. bin/local/validate-registries rejects an unknown
        # visibility at build time, so a value reaching here comes from a
        # hand-edited registry — and a tool whose audience nobody stated is not
        # one to put in front of a client.
        return Visibility.HIDDEN
