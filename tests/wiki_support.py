"""Builders shared by the wiki suites: a knowledge base on disk, and the files in it.

Each suite covers one part of `cli.wiki`; every one of them lays out the same
directory shape, so the shape is written once here rather than once per suite.
"""

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402


def make_wiki(
    tmp_path: Path,
    schema: str = "# Schema\n\nTest knowledge base.\n",
    dirname: str = "wiki",
) -> Path:
    root = tmp_path / dirname
    for sub in ("articles", "raw", "drafts", "archive", "meta"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "SCHEMA.md").write_text(schema, encoding="utf-8")
    return root


def write_article(root: Path, slug: str, body: str = "", subdir: str = "articles", **frontmatter) -> Path:
    path = root / subdir / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    return path


def write_index(root: Path, *slugs: str) -> None:
    body = "# Index\n\n" + "".join(f"- [[{slug}]]\n" for slug in slugs)
    (root / "_index.md").write_text(body, encoding="utf-8")


def write_source(root: Path, name: str, content: str = "raw content") -> Path:
    path = root / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_log(root: Path, *lines: str) -> None:
    body = "# Activity Log\n\n" + "".join(f"{line}\n" for line in lines)
    (root / "_log.md").write_text(body, encoding="utf-8")


def write_manifest(root: Path, entries: dict[str, str]) -> None:
    lines = ["| source | hash |", "| --- | --- |"]
    lines += [f"| {path} | {digest} |" for path, digest in entries.items()]
    (root / "_sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def hash_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[: cli.wiki.HASH_PREFIX_LEN]


def findings_for(root: Path, check: str) -> list[dict]:
    return [f for f in cli.wiki.collect_lint(cli.wiki.Wiki(root)) if f["check"] == check]
