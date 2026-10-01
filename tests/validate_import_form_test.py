"""Tests for bin/local/validate-import-form."""

from pathlib import Path

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "local" / "validate-import-form"

val = load_script("validate_import_form", SCRIPT)

# A miniature of the real layout: two packages that share a module basename,
# which is the collision the `from pkg import mod` form cannot survive.
LAYOUT = val.Layout(modules={
    "pr": frozenset({"state", "context"}),
    "gh": frozenset({"client"}),
    "git": frozenset({"client"}),
})


def reasons(source: str) -> list[str]:
    return [f.reason for f in val.check_source(source, LAYOUT)]


class TestTheImportForm:
    def test_the_qualified_form_is_what_passes(self):
        assert reasons("import pr.state\n\npr.state.load_state()\n") == []

    def test_the_bare_from_form_is_refused(self):
        found = reasons("from pr import state\n")
        assert len(found) == 1
        assert "import pr.state" in found[0]

    def test_an_aliased_from_import_is_refused(self):
        found = reasons("from pr import state as pr_state\n")
        assert len(found) == 1
        assert "from pr import state as pr_state" in found[0]

    def test_an_aliased_plain_import_is_refused(self):
        """The spelling the codemod itself left behind in one test file."""
        found = reasons("import pr.state as pr_state\n")
        assert len(found) == 1
        assert "an aliased module" in found[0]

    def test_a_bare_package_alias_is_refused_too(self):
        """`import pr as p` wears the aliasing just as much as `import pr.state
        as x` once a module is reached through it — `p.state` is the other
        spelling just as surely as `pr.state` is."""
        found = reasons("import pr as p\n")
        assert len(found) == 1
        assert "an aliased module" in found[0]

    def test_importing_a_symbol_is_not_a_module_import(self):
        """`PRState` is a class. Only module objects are governed here."""
        assert reasons("from pr.state import PRState\n") == []

    def test_a_package_that_is_not_ai_lib_is_ignored(self):
        assert reasons("from os import path\nimport json\n") == []

    def test_a_module_the_package_does_not_hold_is_ignored(self):
        """`pr.helpers` is not a module here, so this imports a symbol."""
        assert reasons("from pr import helpers\n") == []

    def test_every_offending_import_is_reported_not_just_the_first(self):
        found = reasons("from gh import client\nfrom git import client\n")
        assert len(found) == 2


class TestTheShadowCheck:
    def test_a_local_capturing_its_package_is_refused(self):
        source = (
            "import pr.state\n"
            "def f(pr):\n"
            "    return pr.state.load_state()\n"
        )
        found = reasons(source)
        assert len(found) == 1
        assert "shadows the package" in found[0]

    def test_an_assignment_shadows_as_surely_as_a_parameter(self):
        source = (
            "import pr.state\n"
            "def f(x):\n"
            "    pr = x\n"
            "    return pr.state.load_state()\n"
        )
        assert any("shadows the package" in r for r in reasons(source))

    def test_a_local_named_for_a_package_it_never_reaches_is_fine(self):
        """The rule is about capture, not about the word.

        A test that binds `pr` to a PRMetadata and never writes `pr.<module>`
        has taken nothing from anyone, and renaming it would cost a good name
        for no reason.
        """
        source = (
            "import pr.state\n"
            "def f(pr):\n"
            "    return pr.number\n"
        )
        assert reasons(source) == []

    def test_a_local_in_one_function_does_not_convict_another(self):
        source = (
            "import pr.state\n"
            "def f(pr):\n"
            "    return pr.number\n"
            "def g():\n"
            "    return pr.state.load_state()\n"
        )
        assert reasons(source) == []

    def test_a_comprehension_loop_variable_does_not_convict_its_own_function(self):
        """A comprehension has its own scope in Python 3: the `pr` bound by
        `for pr in prs` never leaks into `f`'s own scope, so `f`'s own
        `pr.state.load_state()` is unambiguous and must not be flagged."""
        source = (
            "import pr.state\n"
            "def f(prs):\n"
            "    names = [pr.number for pr in prs]\n"
            "    return pr.state.load_state()\n"
        )
        assert reasons(source) == []

    def test_a_nested_function_s_own_local_does_not_convict_the_enclosing_one(self):
        """`inner`'s own `pr = acquire()` is local to `inner`, not to `f` —
        it never shadows the package for `f`'s own `pr.state` call."""
        source = (
            "import pr.state\n"
            "def f():\n"
            "    def inner():\n"
            "        pr = acquire()\n"
            "        return pr\n"
            "    return pr.state.load_state()\n"
        )
        assert reasons(source) == []


class TestTheCheckerItself:
    def test_an_unparseable_file_is_not_reported_as_clean_by_crashing(self):
        """A syntax error belongs to whatever gate compiles the tree."""
        assert reasons("def (:\n") == []

    def test_the_real_tree_passes(self):
        """The gate lands green, which is the whole premise of #1137 phase 4.

        Scoped to `ai/lib` so the assertion stays about the subject rather
        than about how many files the repo happens to hold.
        """
        layout = val.Layout.read(REPO_ROOT)
        offenders = [
            path for path in (REPO_ROOT / "ai" / "lib").rglob("*.py")
            if "__pycache__" not in path.parts
            and val.check_source(path.read_text(), layout)
        ]
        assert offenders == []


class TestEmbeddedPythonInShell:
    """The gap that let `mod.agent.templates` survive three green runs.

    A `.bats` heredoc holds Python no AST sees. The retired names cannot be
    listed because they no longer exist, so the check derives the shape they
    all had: `<package>_<module>` naming a real pair, reached through a dot.
    """

    def test_a_reach_through_in_a_heredoc_is_caught(self):
        source = 'result=$(_py \'\nresult = mod.pr_state.load_state()\n\')\n'
        found = [f.reason for f in val.check_shell(source, LAYOUT)]
        assert len(found) == 1
        assert "pr_state was an alias for pr.state" in found[0]

    def test_the_qualified_form_in_a_heredoc_is_fine(self):
        source = 'result=$(_py \'\nimport pr.state\nresult = pr.state.load_state()\n\')\n'
        assert val.check_shell(source, LAYOUT) == []

    def test_a_pair_that_names_no_real_module_is_not_flagged(self):
        """`pr_helpers` is not a module here, so it is somebody's variable."""
        assert val.check_shell("echo $x.pr_helpers\n", LAYOUT) == []

    def test_a_bare_word_without_the_dot_is_somebody_elses_name(self):
        """`go.mod` and a local called `pr.state` are not reach-throughs."""
        assert val.check_shell('echo "module example.com/x" > go.mod\n', LAYOUT) == []
        assert val.check_shell("pr_state=1\n", LAYOUT) == []

    def test_every_line_is_reported_not_just_the_first(self):
        source = "a.pr_state\nb.gh_client\n"
        assert len(val.check_shell(source, LAYOUT)) == 2
