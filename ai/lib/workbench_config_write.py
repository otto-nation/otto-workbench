"""How the workbench config is changed.

One dotted key at a time, into one of the three scopes ``config_scopes``
merges, and only after the key and the value have both been judged against the
surface that will read the file back. Every write here goes through
``set_value``, so the yq-first ordering that preserves a hand-authored file's
comments and the checks that keep a value nothing reads out of a shared file are
each written once.

Those checks are what make this its own module rather than three functions on
the config. A key is judged against two surfaces — this checkout's
``WorkbenchConfig`` and the schema of the workbench actually installed on the
machine — because the file outlives the checkout that wrote it. The value is
judged against the type the field declares, because everything arriving here is
a string off a command line and ``serde`` replaces a scalar of the wrong type
with the field's default rather than complaining. Reading needs neither check,
which is why ``workbench_config`` carries no part of them.
"""

# doc-group: platform

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import timeouts
from workbench_config import (
    CONFIG_HEADER,
    PROJECT_CONFIG_NAME,
    SCHEMA_PATH,
    ConfigError,
    ConfigKeyError,
    ConfigValueError,
    container_config_path,
    defines_key,
    global_config_path,
    project_config_path,
    read_yaml,
    schema_accepts,
    schema_at,
    schema_type,
    surface_schema,
    yaml_dump,
)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# The launcher that resolves to the workbench this machine has installed. It is
# a symlink into the checkout that installed it, so resolving it from any
# worktree lands on that one — which is what lets a write be judged by the key
# surface the machine actually reads rather than by the writing checkout's.
# `bin/otto-workbench` derives the same root from its own location; there is no
# equivalent here, so PATH is the link.
INSTALLED_LAUNCHER = "otto-workbench"


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
    checkout answers first, through ``workbench_config.defines_key``, and
    catches a typo or a key assembled at runtime. The installed workbench
    answers second and is the only one that can catch the case this guard exists
    for: a worktree weeks behind ``main`` writing a key that was valid when the
    branch was cut and has since moved, into a file every repo on the machine
    shares.

    The same comparison refuses the opposite direction, and that one is a
    contributor rather than a mistake: a key added on a branch is a key the
    installed workbench has not learned yet, so writing it here is refused
    until the branch reaches ``main`` and the install follows. There is no flag
    for it, deliberately — an override would be reached for by exactly the
    stale writer this exists to stop. Edit the file by hand meanwhile; nothing
    checks a hand-edit, and a key only your branch reads is one only your
    branch has to load.
    """
    if not defines_key(key):
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
    if not isinstance(installed, dict) or schema_accepts(installed, key):
        return KeyCheck(KeyVerdict.KNOWN, key)
    return KeyCheck(KeyVerdict.UNKNOWN_INSTALLED, key, str(path))


def coerce_value(key: str, value: str) -> bool | int | float | str:
    """``value`` as the scalar the field behind ``key`` actually holds.

    Everything arriving here is a string, because everything comes off a command
    line, and the file has to end up holding a real YAML scalar. ``serde``
    restores an int from ``"3"`` but refuses a bool from ``"true"`` on purpose —
    ``bool("false")`` is ``True``, so guessing there invents a value rather than
    recovering one — which makes a boolean written as a quoted string a key that
    reads back as the field's default with nothing said.

    Raises ``ConfigValueError`` when the string is not one the field could hold.
    A field whose type the schema does not name keeps its string, permissive in
    the same direction ``schema_type`` and the key walk are.
    """
    wanted = schema_type(schema_at(surface_schema(), key) or {})
    if wanted == "boolean":
        spelled = value.strip().lower()
        if spelled not in ("true", "false"):
            raise ConfigValueError(
                f"{key} is true or false, and {value!r} is neither")
        return spelled == "true"
    if wanted in ("integer", "number"):
        try:
            return int(value) if wanted == "integer" else float(value)
        except ValueError:
            raise ConfigValueError(
                f"{key} is a{'n' if wanted == 'integer' else ''} {wanted}, "
                f"and {value!r} is not one") from None
    return value


def _yq_assignment(key: str, value: bool | int | float | str) -> str:
    """The ``yq`` expression that writes *value* under *key* with its own type.

    A string goes through ``strenv`` so the shell never sees it and yq never
    reinterprets it. A bool or a number is spelled into the expression instead,
    because ``strenv`` would quote it back into a string — and it is safe to
    spell because ``coerce_value`` has already parsed it out of the input.
    """
    if isinstance(value, bool):
        return f".{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f".{key} = {value}"
    return f".{key} = strenv(WB_CONFIG_VALUE)"


def set_value(key: str, value: str, path: Path | None = None) -> None:
    """Write one dotted key into a config file, creating it if needed.

    Through ``yq -i`` so the write preserves the comments and ordering of a
    file the user hand-authored. PyYAML is the fallback and does not preserve
    them, which is why it is second rather than first.

    Raises ``ConfigError`` when the write cannot happen — a caller running in a
    Claude hook has one exception type to catch, whichever writer ran — with the
    ``ConfigKeyError`` subclass when ``key`` is not one the workbench reads and
    ``ConfigValueError`` when ``value`` is not one the field can hold.

    ``check_key`` decides the first, and asks the installed workbench as well as
    this checkout; ``coerce_value`` decides the second. Both files this writes
    are shared — the global one by every repo on the machine, a project one by
    everybody who clones the repo — and ``serde`` drops an unrecognised key, and
    replaces a scalar of the wrong type with the default, on the way back in.
    Without the checks the write reports success and the value is simply gone,
    which is a rule quietly not applying rather than anything anybody can see.
    """
    if path is None:
        path = global_config_path()
    check = check_key(key)
    if not check.ok:
        raise ConfigKeyError(f"not writing {path}: {check.reason}")
    typed = coerce_value(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(CONFIG_HEADER + "\n")
    if shutil.which("yq"):
        env = dict(os.environ, WB_CONFIG_VALUE=value)
        try:
            subprocess.run(
                ["yq", "-i", _yq_assignment(key, typed), str(path)],
                check=True, timeout=timeouts.LOCAL, env=env,
            )
        except subprocess.SubprocessError as exc:
            raise ConfigError(f"could not write {key} to {path}: {exc}") from exc
        return
    _set_value_with_pyyaml(path, key, typed)


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


def set_container_value(key: str, value: str, project_root: Path | str) -> None:
    """Write one dotted key into the file above a bare repo's worktrees.

    Raises ``ConfigError`` when the repo has no container, rather than falling
    back to the worktree. The two files differ in what survives: a worktree one
    is deleted by ``wt remove`` and unseen by every sibling checkout, so a
    caller that asked for the durable scope and silently got the disposable one
    has been told the opposite of what happened.

    Same ``set_value`` and therefore the same key check as the other two. This
    file is not committed, which makes the check matter more rather than less:
    nobody reviews it, so a key nothing reads is never noticed by anyone.
    """
    path = container_config_path(project_root)
    if path is None:
        raise ConfigError(
            f"{project_root} is not a bare-repo worktree, so it has no "
            f"container to write {PROJECT_CONFIG_NAME} into",
        )
    set_value(key, value, path)


def _set_value_with_pyyaml(
    path: Path, key: str, value: bool | int | float | str,
) -> None:
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
