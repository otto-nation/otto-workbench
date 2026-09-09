"""End-to-end checks that run the `wiki` binary as a subprocess.

`wiki_test.py` imports the script and calls into it, which is faster and is where
behaviour is pinned. These run the installed entry point instead, and exist
because two defects on this CLI got past a green in-process suite:

  - the SCHEMA template shipped an instruction naming both placeholders, so
    substitution produced "Replace Payments and the team" in every new schema
  - `domain` then reported the HTML comment that replaced it, in every status

Both were visible the moment the command was run and invisible to a test that
imported past the entry point. What is asserted here is deliberately shallow:
the exit code, and the shape of what reaches the terminal.

A third defect — the package being unreachable from the binary, and so dropped
from the distribution tarball — is *not* covered here, and cannot be: the CLI
runs correctly from a source checkout either way.
`tests/test_tarball_completeness.py` is what catches that one.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import frontmatter_keys

WIKI_BIN = Path(__file__).resolve().parent.parent / "ai" / "bin" / "wiki"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the binary the way a shell would, with no inherited config."""
    return subprocess.run(
        [str(WIKI_BIN), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repo with no knowledge base yet."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


class TestLifecycle:
    def test_init_ingest_compile_index_lint(self, project):
        """The path a real knowledge base takes, from empty directory to clean lint."""
        assert run("init", "--domain", "Payments", "--audience", "the team", cwd=project).returncode == 0
        wiki_dir = project / "wiki"

        source = project / "notes.md"
        source.write_text("Token refresh rotates a credential.\n", encoding="utf-8")
        staged = run("ingest", "--stage", str(source), "--title", "Token Refresh", cwd=project)
        assert staged.returncode == 0, staged.stderr
        assert "staged" in staged.stdout

        # Stand in for the compile step, which is the skill's work.
        (wiki_dir / "articles" / "token-refresh.md").write_text(
            "---\ntitle: Token Refresh\ntags: [auth]\n---\n"
            + "word " * 200
            + "\n\n## Related\n\n- [[session-store]]\n",
            encoding="utf-8",
        )
        (wiki_dir / "articles" / "session-store.md").write_text(
            "---\ntitle: Session Store\ntags: [auth]\n---\n"
            + "word " * 200
            + "\n\nSee [[token-refresh]].\n",
            encoding="utf-8",
        )
        digest = json.loads(run("sources", "--json", cwd=project).stdout)["sources"][0]
        with (wiki_dir / "_sources.md").open("a", encoding="utf-8") as handle:
            handle.write(f"| {digest['path']} | {digest['hash']} | file | 2026-01-01 | x |\n")

        assert run("index", cwd=project).returncode == 0
        lint = run("lint", cwd=project)
        assert lint.returncode == 0, f"expected clean, got:\n{lint.stdout}"

    def test_status_reports_the_domain_not_template_furniture(self, project):
        """The template's own editing note must not surface as the subject."""
        assert run("init", "--domain", "Payments", cwd=project).returncode == 0
        # Matched on the label, not the substring: a pytest tmp path can itself
        # contain the word and would otherwise match the path line above it.
        line = [
            ln
            for ln in run("status", cwd=project).stdout.splitlines()
            if ln.strip().startswith("domain ")
        ][0]
        assert "Payments" in line
        assert "<!--" not in line and "Replace" not in line

    def test_a_new_schema_carries_no_placeholder_text(self, project):
        assert run("init", "--domain", "Payments", "--audience", "the team", cwd=project).returncode == 0
        schema = (project / "wiki" / "SCHEMA.md").read_text(encoding="utf-8")
        assert "{DOMAIN}" not in schema and "{AUDIENCE}" not in schema
        assert "Replace Payments" not in schema

    def test_a_fresh_base_lints_clean(self, project):
        run("init", cwd=project)
        result = run("lint", cwd=project)
        assert result.returncode == 0, result.stdout


class TestHashesAreComputed:
    def test_reported_hash_matches_the_file(self, project):
        """The reason this CLI exists: hashes come from bytes, not recollection."""
        assert run("init", cwd=project).returncode == 0
        source = project / "notes.md"
        source.write_text("known content\n", encoding="utf-8")
        run("ingest", "--stage", str(source), cwd=project)

        staged = next((project / "wiki" / "raw").iterdir())
        expected = hashlib.sha256(staged.read_bytes()).hexdigest()[:8]
        assert expected in run("sources", cwd=project).stdout

    def test_binary_source_survives_staging(self, project):
        assert run("init", cwd=project).returncode == 0
        source = project / "paper.pdf"
        source.write_bytes(b"%PDF-1.4\nbinary\x00bytes")
        run("ingest", "--stage", str(source), "--type", "pdf", cwd=project)
        staged = next((project / "wiki" / "raw").iterdir())
        assert staged.read_bytes() == b"%PDF-1.4\nbinary\x00bytes"


class TestContainment:
    def test_type_cannot_write_outside_the_base(self, project):
        """`--type ../../evil` wrote the source two directories up, once."""
        assert run("init", cwd=project).returncode == 0
        source = project / "s.md"
        source.write_text("body\n", encoding="utf-8")
        assert run("ingest", "--stage", str(source), "--type", "../../evil", cwd=project).returncode == 0

        raw = list((project / "wiki" / "raw").iterdir())
        assert len(raw) == 1
        assert raw[0].parent == project / "wiki" / "raw"
        assert not list(project.parent.glob("*evil*"))

    def test_type_cannot_add_a_frontmatter_key(self, project):
        assert run("init", cwd=project).returncode == 0
        source = project / "s.md"
        source.write_text("body\n", encoding="utf-8")
        run("ingest", "--stage", str(source), "--type", "file\ninjected: yes", cwd=project)

        staged = next((project / "wiki" / "raw").iterdir())
        assert frontmatter_keys(staged) == [
            "source_type",
            "title",
            "original_path",
            "ingest_date",
        ]


class TestExitCodes:
    def test_no_knowledge_base_exits_two(self, project):
        result = run("status", cwd=project)
        assert result.returncode == 2
        assert "no knowledge base found" in result.stderr

    def test_findings_exit_one(self, project):
        assert run("init", cwd=project).returncode == 0
        (project / "wiki" / "articles" / "a.md").write_text(
            "---\ntitle: A\n---\nsee [[ghost]]\n", encoding="utf-8"
        )
        result = run("lint", cwd=project)
        assert result.returncode == 1
        assert "broken-link" in result.stdout

    def test_init_refuses_an_existing_base(self, project):
        run("init", cwd=project)
        result = run("init", cwd=project)
        assert result.returncode == 1
        assert "already exists" in result.stderr

    def test_help_and_version_answer(self, project):
        assert run("--help", cwd=project).returncode == 0
        version = run("--version", cwd=project)
        assert version.returncode == 0
        assert "wiki" in version.stdout


class TestConfiguredDirectory:
    def test_wiki_dir_is_honoured_from_a_nested_directory(self, project):
        """The setting is read from the repo root, wherever the command runs."""
        (project / ".workbench.yml").write_text("wiki:\n  dir: knowledge\n", encoding="utf-8")
        assert run("init", cwd=project).returncode == 0
        assert (project / "knowledge" / "SCHEMA.md").is_file()

        nested = project / "src" / "deep"
        nested.mkdir(parents=True)
        result = run("path", cwd=nested)
        assert result.returncode == 0
        assert result.stdout.strip() == str((project / "knowledge").resolve())


class TestPackaging:
    def test_the_binary_runs_without_the_skill_directory(self, project, tmp_path):
        """`wiki init` falls back when the SCHEMA template is not installed.

        The template ships with the skill, and the CLI is installable without
        it. The layout built here is the tarball's: `ai/lib` and the workbench
        `lib` modules it imports, flattened into one `lib/` beside `bin/`. So
        this also fails if `ai/lib/wiki` ever stops being self-contained enough
        to ship — which is how the package came to be omitted from the tarball
        in the first place.
        """
        bin_dir = WIKI_BIN.parent
        repo_root = bin_dir.parent.parent
        standalone = tmp_path / "install"
        (standalone / "bin").mkdir(parents=True)
        shutil.copy2(WIKI_BIN, standalone / "bin" / "wiki")
        # `_version.py` sits beside the binary and is imported by it, so the
        # install is not a working one without it.
        shutil.copy2(bin_dir / "_version.py", standalone / "bin" / "_version.py")
        shutil.copytree(bin_dir.parent / "lib", standalone / "lib")
        for module in ("git_layout.py", "gitenv.py", "git_remote.py"):
            shutil.copy2(repo_root / "lib" / module, standalone / "lib" / module)

        result = subprocess.run(
            [str(standalone / "bin" / "wiki"), "init", "--domain", "Payments"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        schema = (project / "wiki" / "SCHEMA.md").read_text(encoding="utf-8")
        assert "Payments" in schema
