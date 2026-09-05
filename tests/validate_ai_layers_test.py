"""Tests for bin/local/validate-ai-layers."""

import sys
from pathlib import Path

import pytest
from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-ai-layers"

val = load_script("validate_ai_layers", SCRIPT)


def _layer(package, number, allowed):
    return val.Layer(package=package, number=number, purpose="a purpose",
                     allowed=frozenset(allowed))


# A miniature stack with the two shapes the real one has: a foundation that may
# reach nothing, and two packages at the same layer that may not reach each other.
LAYERS = {
    "core": _layer("core", 1, ()),
    "git": _layer("git", 2, ("core",)),
    "gh": _layer("gh", 3, ("core", "git")),
    "agent": _layer("agent", 3, ("core", "git")),
    "pr": _layer("pr", 4, ("core", "git", "gh", "agent")),
}


def _check(source, relpath="pr/thing.py", layers=LAYERS):
    return val.check_source(source, relpath, layers)


# ── the declaration ──────────────────────────────────────────────────────


def test_a_declaration_is_parsed_into_its_parts():
    layer = val.parse_declaration("pr", '"""Layer 4 — PR state. May import: core, git."""\n')
    assert (layer.package, layer.number, layer.purpose) == ("pr", 4, "PR state")
    assert layer.allowed == frozenset({"core", "git"})


def test_the_foundation_declares_an_empty_set():
    layer = val.parse_declaration("core", '"""Layer 1 — bits. May import: nothing in ai/lib."""\n')
    assert layer.allowed == frozenset()


def test_a_module_with_no_docstring_declares_nothing():
    assert val.parse_declaration("pr", "x = 1\n") is None


def test_a_docstring_in_another_form_declares_nothing():
    """Accepting variants would mean reading a format nobody maintains."""
    assert val.parse_declaration("pr", '"""PR state and lifecycle."""\n') is None


def test_an_unparseable_init_declares_nothing():
    assert val.parse_declaration("pr", "def f(:\n") is None


def test_the_declaration_body_may_run_past_the_first_line():
    source = '"""Layer 2 — git plumbing. May import: core.\n\nMore prose here.\n"""\n'
    assert val.parse_declaration("git", source).number == 2


# ── what a declaration may permit ────────────────────────────────────────


def test_a_consistent_stack_has_no_declaration_problems():
    assert val.check_declarations(LAYERS) == {}


def test_permitting_a_package_that_does_not_exist_is_flagged():
    layers = dict(LAYERS, pr=_layer("pr", 4, ("core", "gitt")))
    assert [(v.target, v.reason) for v in val.check_declarations(layers)["pr"]] == [
        ("gitt", "no such package under ai/lib"),
    ]


def test_permitting_a_package_at_the_same_layer_is_flagged():
    """`gh` and `agent` are both layer 3 — the numbers are what forbids the edge,
    so a declaration that names it must not be taken at face value."""
    layers = dict(LAYERS, gh=_layer("gh", 3, ("core", "git", "agent")))
    assert [(v.target, v.reason) for v in val.check_declarations(layers)["gh"]] == [
        ("agent", "layer 3 is not below layer 3"),
    ]


def test_permitting_a_package_above_is_flagged():
    layers = dict(LAYERS, git=_layer("git", 2, ("core", "pr")))
    assert [v.target for v in val.check_declarations(layers)["git"]] == ["pr"]


# ── the imports ──────────────────────────────────────────────────────────


def test_a_permitted_import_is_clean():
    assert _check("from gh import client\n") == []


def test_a_permitted_dotted_import_is_clean():
    assert _check("import gh.client\n") == []


def test_an_import_of_the_package_itself_is_clean():
    """Intra-package edges are not the layering's business."""
    assert _check("from pr import domains\nimport pr.state\n") == []


def test_a_third_party_import_is_clean():
    assert _check("import json\nfrom pathlib import Path\n") == []


def test_an_upward_import_is_flagged():
    layers = dict(LAYERS, review=_layer("review", 6, ("core", "pr")))
    violations = _check("from review import pipeline\n", layers=layers)
    assert [(v.line, v.target) for v in violations] == [(1, "review")]
    assert "layer 6" in violations[0].reason


def test_a_peer_import_is_flagged():
    """`gh` may reach `git` and `core` and nothing else at its own layer."""
    violations = _check("from agent import invoke\n", relpath="gh/client.py")
    assert [(v.line, v.target) for v in violations] == [(1, "agent")]


def test_an_upward_dotted_import_is_flagged():
    layers = dict(LAYERS, review=_layer("review", 6, ("core", "pr")))
    violations = _check("import review.pipeline\n", layers=layers)
    assert [v.target for v in violations] == ["review"]


def test_an_import_inside_a_function_is_flagged():
    """Deferring the import defers the cost, not the dependency."""
    layers = dict(LAYERS, review=_layer("review", 6, ("core", "pr")))
    violations = _check("def f():\n    from review import pipeline\n", layers=layers)
    assert [(v.line, v.target) for v in violations] == [(2, "review")]


def test_every_offending_import_is_reported_once():
    layers = dict(LAYERS, review=_layer("review", 6, ("core", "pr")))
    violations = _check(
        "from review import pipeline\nfrom agent import invoke\nimport review.gc\n",
        layers=layers)
    assert [(v.line, v.target) for v in violations] == [(1, "review"), (3, "review")]


def test_a_relative_import_stays_inside_its_own_package():
    """`from ..gh import client` inside `pr.sub` resolves to `pr.gh`, not to the
    `gh` package — a relative import cannot reach a sibling by construction."""
    assert _check("from ..gh import client\n", relpath="pr/sub/mod.py") == []


def test_a_relative_import_naming_no_module_is_clean():
    assert _check("from . import domains\n", relpath="pr/thing.py") == []


def test_a_relative_import_that_escapes_the_tree_is_flagged():
    violations = _check("from ... import something\n", relpath="pr/thing.py")
    assert [(v.target, v.reason) for v in violations] == [
        ("..", "relative import escapes ai/lib"),
    ]


def test_a_module_outside_a_declared_package_is_flagged():
    violations = _check("import json\n", relpath="stray.py")
    assert [v.reason for v in violations] == [
        "not inside a package that declares a layer",
    ]


def test_syntax_error_is_tolerated_but_reported(capsys):
    assert _check("def f(:\n") == []
    assert "unparseable, not checked" in capsys.readouterr().err


# ── discovery and the repo ───────────────────────────────────────────────


def test_read_layers_finds_every_package():
    layers = val.read_layers(str(REPO_ROOT))
    assert set(layers) == {"agent", "config", "core", "eval", "fix",
                           "gh", "git", "pr", "retro", "review"}
    assert layers["core"].number == 1
    assert layers["core"].allowed == frozenset()


def test_every_package_declares_a_layer():
    layers = val.read_layers(str(REPO_ROOT))
    assert val.undeclared_packages(str(REPO_ROOT), layers) == []


def test_the_declarations_agree_with_each_other():
    assert val.check_declarations(val.read_layers(str(REPO_ROOT))) == {}


def test_discover_finds_modules_across_packages():
    files = val.discover_files(str(REPO_ROOT))
    assert str(REPO_ROOT / "ai" / "lib" / "core" / "workbench_paths.py") in files
    assert str(REPO_ROOT / "ai" / "lib" / "review" / "pipeline.py") in files


def test_repo_is_clean():
    """The layering the declarations describe is the layering the imports have."""
    layers = val.read_layers(str(REPO_ROOT))
    offenders = {
        path: val.check_file(path, layers, str(REPO_ROOT))
        for path in val.discover_files(str(REPO_ROOT))
    }
    assert {p: v for p, v in offenders.items() if v} == {}


def test_main_exits_1_on_a_violation(monkeypatch, capsys):
    bad = REPO_ROOT / "ai" / "lib" / "core" / "workbench_paths.py"
    monkeypatch.setattr(val, "check_file",
                        lambda *a: [val.Violation(line=3, target="review", reason="layer 6")])
    monkeypatch.setattr(sys, "argv", ["validate-ai-layers", "--quiet", str(bad)])
    with pytest.raises(SystemExit) as exc:
        val.main()
    assert exc.value.code == 1
    assert "imports outside its layer" in capsys.readouterr().err


def test_main_exits_0_on_the_repo(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["validate-ai-layers", "--quiet"])
    val.main()
    assert "every import stays within its declared layer" in capsys.readouterr().out
