"""A command module standing in front of the submodules its flow is spread over.

A module under `ai/lib/cli/` re-imports the names its submodules define so a
test can reach one at `<command>.<name>` and patch it. A name imported between
submodules has one definition and several bindings: reading it can stop at the
first, since they are the same object, but writing it cannot — replacing a seam
means replacing it for every caller. `install` gives such a module the attribute
protocol that does both, and puts every binding back when the patch is undone.

Two submodules can also define a name each, meaning different things by it —
`gh.client` and `git.client` both have a `run`. A read resolves that to the
first of them, so a write follows the read: it reaches the bindings holding the
object the read returned, and leaves the other definition alone.

A caller passes every `ai/lib` module it imports, this one included — the set is
checked against the module's imports rather than curated, so a proxy listing
itself is the rule holding rather than a module patching its own attributes.
"""

# doc-group: platform

from __future__ import annotations

import sys
from types import ModuleType

_MISSING = object()


def install(module_name: str, submodules: tuple[ModuleType, ...]) -> None:
    """Forward `module_name`'s attribute access to the submodules behind it.

    A read falls through to the first submodule that defines the name; a write
    reaches every submodule bound to what that read returns; a delete restores
    what each of them held before the first write.

    The restore is what `mock.patch` needs. It decides a patch is "local" by
    looking the name up in the target's own `__dict__`, and a name only the
    proxy answers for is not there — so `__exit__` calls `delattr` instead of
    assigning the saved value back. Without a `__delattr__` that puts the
    submodules back, the mock outlives the `with` block and the next test in
    the process runs against it.
    """
    module = sys.modules[module_name]

    # Pre-patch values, keyed by (module name, attribute). A caller that
    # restores by assignment instead (monkeypatch.setattr) leaves its entry
    # behind, holding the value the attribute already has again — bounded by
    # the number of names ever patched.
    originals: dict[tuple[str, str], object] = {}

    def _current(name):
        """What a read of *name* resolves to, and every submodule bound to it."""
        found = _MISSING
        owners = []
        for mod in submodules:
            held = getattr(mod, name, _MISSING)
            if held is _MISSING:
                continue
            if found is _MISSING:
                found = held
            if held is found:
                owners.append(mod)
        return found, owners

    # One class per call rather than one for the module: it closes over this
    # install's submodules and pre-patch values, so two proxied scripts in a
    # process keep their patches apart.
    class _ProxyModule(type(module)):

        def __getattr__(self, name):
            found, _ = _current(name)
            if found is _MISSING:
                raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
            return found

        def __setattr__(self, name, value):
            found, owners = _current(name)
            for mod in owners:
                originals.setdefault((mod.__name__, name), found)
                setattr(mod, name, value)
            super().__setattr__(name, value)

        def __delattr__(self, name):
            for mod in submodules:
                original = originals.pop((mod.__name__, name), _MISSING)
                if original is not _MISSING:
                    setattr(mod, name, original)
            super().__delattr__(name)

    module.__class__ = _ProxyModule
