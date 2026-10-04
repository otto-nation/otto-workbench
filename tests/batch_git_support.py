"""A bare `origin` and a clone of it, for the batch suites that read real refs."""

from dataclasses import dataclass
from pathlib import Path

from conftest import commit_all, git_in, git_out, init_repo, run_checked


@dataclass(frozen=True)
class GitPair:
    """`origin` is bare; `seed` pushes to it as "somebody else"; `work` is the batch's clone."""
    origin: Path
    seed: Path
    work: Path


def remote_and_clone(tmp_path: Path, branch: str = "feat") -> GitPair:
    """`main` with one commit and *branch* one commit ahead, both on origin; `work` on *branch*."""
    origin = tmp_path / "origin.git"
    run_checked(["git", "init", "--bare", "-q", "-b", "main", str(origin)])
    seed = init_repo(tmp_path / "seed")
    (seed / "base.txt").write_text("base\n")
    commit_all(seed, "base")
    git_in(seed, "remote", "add", "origin", str(origin))
    git_in(seed, "push", "-q", "origin", "main")
    git_in(seed, "checkout", "-q", "-b", branch)
    (seed / "feat.txt").write_text("feat\n")
    commit_all(seed, "feat")
    git_in(seed, "push", "-q", "origin", branch)
    work = tmp_path / "work"
    run_checked(["git", "clone", "-q", "-b", branch, str(origin), str(work)])
    for key, value in (("user.email", "t@t"), ("user.name", "T"), ("commit.gpgsign", "false")):
        git_in(work, "config", key, value)
    return GitPair(origin=origin, seed=seed, work=work)


def advance(repo: Path, branch: str, name: str) -> str:
    """Commit a new file on *branch* in *repo*, push it, and return the new tip."""
    git_in(repo, "checkout", "-q", branch)
    (repo / name).write_text(f"{name}\n")
    commit_all(repo, f"chore: {name}")
    git_in(repo, "push", "-q", "origin", branch)
    return git_out(repo, "rev-parse", "HEAD").strip()


def remote_tip(origin: Path, branch: str) -> str:
    return git_out(origin, "rev-parse", f"refs/heads/{branch}").strip()
