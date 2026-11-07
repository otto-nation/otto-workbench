"""The repo's open PRs as a stack graph: who is stacked on whom, and how healthy each link is.

Parent authority, in order: GitHub's open native stacks where the API exists (a
404 means the feature is off for this repo and is not an error; only open stacks
count); the PR's base branch when another open PR owns that branch;
`git.topology.stack_parent` for a PR based on the trunk whose history says
otherwise; else the trunk.

Health is read from git against `origin/<branch>` refs, never from GitHub:
`up-to-date` when the child contains its parent, `needs-rebase` when the
parent moved and `git merge-tree` merges it cleanly, `has-conflicts` when it
does not, `base-closed` when the PR's base is neither the trunk nor an open
PR's branch (its parent landed or was abandoned), `unknown` when git cannot say.
"""

# doc-group: pr-state

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import gh.client
import git.client
import git.topology


class Health(StrEnum):
    UP_TO_DATE = "up-to-date"
    NEEDS_REBASE = "needs-rebase"
    HAS_CONFLICTS = "has-conflicts"
    BASE_CLOSED = "base-closed"
    UNKNOWN = "unknown"


class ParentSource(StrEnum):
    NATIVE = "native"
    BASE = "base"
    TOPOLOGY = "topology"
    TRUNK = "trunk"


@dataclass(frozen=True)
class OpenPr:
    number: int
    title: str
    branch: str
    base_ref: str
    url: str
    is_draft: bool


@dataclass(frozen=True)
class StackNode:
    pr: int
    title: str
    branch: str
    base_ref: str
    url: str
    is_draft: bool
    parent: int | None
    parent_source: ParentSource
    children: list[int]
    health: Health
    stack_number: int | None


@dataclass(frozen=True)
class StackGraph:
    trunk: str
    nodes: list[StackNode]
    cycles: list[list[int]] = field(default_factory=list)


@dataclass(frozen=True)
class _Parent:
    pr: int | None
    source: ParentSource


def _parents(prs: list[OpenPr], trunk: str, native: dict[int, int] | None,
             topology_parent: Callable[[str], str]) -> dict[int, _Parent]:
    by_branch = {p.branch: p.number for p in prs}
    found = {}
    for p in prs:
        if native and native.get(p.number) in {q.number for q in prs}:
            found[p.number] = _Parent(native[p.number], ParentSource.NATIVE)
        elif p.base_ref in by_branch and by_branch[p.base_ref] != p.number:
            found[p.number] = _Parent(by_branch[p.base_ref], ParentSource.BASE)
        elif p.base_ref == trunk and (b := topology_parent(p.branch)) in by_branch \
                and by_branch[b] != p.number:
            found[p.number] = _Parent(by_branch[b], ParentSource.TOPOLOGY)
        else:
            found[p.number] = _Parent(None, ParentSource.TRUNK)
    return found


def _break_cycles(parents: dict[int, _Parent]) -> list[list[int]]:
    """Cut every parent loop at the PR the walk entered it by, and name the loop."""
    cycles = []
    for start in sorted(parents):
        seen, n = [], start
        while n is not None and n not in seen:
            seen.append(n)
            n = parents[n].pr
        if n is None:
            continue
        loop = seen[seen.index(n):]
        if any(sorted(loop) == sorted(c) for c in cycles):
            continue
        cycles.append(loop)
        parents[loop[-1]] = _Parent(None, ParentSource.TRUNK)
    return cycles


def build_graph(prs: list[OpenPr], trunk: str, *,
                native: dict[int, int] | None,
                native_stack: dict[int, int] | None,
                topology_parent: Callable[[str], str],
                health_of: Callable[[str, str], Health]) -> StackGraph:
    """The graph of *prs*, with every input that needs I/O passed in."""
    parents = _parents(prs, trunk, native, topology_parent)
    cycles = _break_cycles(parents)
    by_number = {p.number: p for p in prs}
    open_branches = {p.branch for p in prs}
    nodes = []
    for p in prs:
        parent = parents[p.number]
        if parent.pr is not None:
            status = health_of(p.branch, by_number[parent.pr].branch)
        elif p.base_ref != trunk and p.base_ref not in open_branches:
            status = Health.BASE_CLOSED
        else:
            status = health_of(p.branch, trunk)
        nodes.append(StackNode(
            pr=p.number, title=p.title, branch=p.branch, base_ref=p.base_ref, url=p.url,
            is_draft=p.is_draft, parent=parent.pr, parent_source=parent.source,
            children=sorted(c for c, q in parents.items() if q.pr == p.number),
            health=status,
            stack_number=(native_stack or {}).get(p.number),
        ))
    return StackGraph(trunk=trunk, nodes=sorted(nodes, key=lambda n: n.pr), cycles=cycles)


def ladder(graph: StackGraph, pr: int) -> list[StackNode]:
    """The chain through *pr*: its ancestors bottom-first, itself, then its first-child line."""
    by_pr = {n.pr: n for n in graph.nodes}
    chain, n = [], by_pr.get(pr)
    while n is not None:
        chain.insert(0, n)
        n = by_pr.get(n.parent) if n.parent is not None else None
    n = by_pr.get(pr)
    while n is not None and n.children:
        n = by_pr[n.children[0]]
        chain.append(n)
    return chain


def health(cwd: str, branch: str, parent_branch: str) -> Health:
    """How *branch* stands against *parent_branch*, both read as `origin/<name>`."""
    head, base = f"refs/remotes/origin/{branch}", f"refs/remotes/origin/{parent_branch}"
    behind = git.client.out("rev-list", "--count", f"{head}..{base}", cwd=cwd)
    if not behind.isdigit():
        return Health.UNKNOWN
    if behind == "0":
        return Health.UP_TO_DATE
    merged = git.client.run("merge-tree", "--write-tree", base, head, cwd=cwd)
    if merged.returncode == 0:
        return Health.NEEDS_REBASE
    if merged.returncode == 1:
        return Health.HAS_CONFLICTS
    # Exit >1 is git failing (bad ref, old git without --write-tree), not a
    # merge verdict, so the state is unknown rather than conflicted.
    return Health.UNKNOWN


def _open_prs(repo: str) -> list[OpenPr]:
    pages = gh.client.api_json(f"repos/{repo}/pulls?state=open&per_page=100",
                               paginate=True, slurp=True, default=[]) or []
    return [OpenPr(number=r["number"], title=r.get("title", ""), branch=r["head"]["ref"],
                   base_ref=r["base"]["ref"], url=r.get("html_url", ""),
                   is_draft=bool(r.get("draft")))
            for page in pages for r in page]


@dataclass(frozen=True)
class _NativeStacks:
    parent: dict[int, int]
    number: dict[int, int]


def _ingest_stack(stack: dict, parent: dict[int, int], number: dict[int, int]) -> None:
    if not stack.get("open"):
        return
    members = [m["number"] for m in stack.get("pull_requests", [])]
    for below, above in zip(members, members[1:]):
        parent[above] = below
    for member in members:
        number[member] = stack.get("number")


def _native(repo: str) -> _NativeStacks | None:
    stacks = gh.client.api_json(f"repos/{repo}/stacks?per_page=100",
                                paginate=True, slurp=True, default=None)
    if not stacks:
        return None
    parent, number = {}, {}
    for page in stacks:
        for stack in page:
            _ingest_stack(stack, parent, number)
    return _NativeStacks(parent, number)


def load_graph(repo: str, cwd: str) -> StackGraph:
    """The live graph for *repo*, read from GitHub and the checkout at *cwd*."""
    prs = _open_prs(repo)
    trunk = git.topology.default_branch(cwd)
    native = _native(repo)
    return build_graph(
        prs, trunk,
        native=native.parent if native else None,
        native_stack=native.number if native else None,
        topology_parent=lambda b: git.topology.stack_parent(
            cwd=cwd, default=trunk, ref=f"refs/remotes/origin/{b}"),
        health_of=lambda b, p: health(cwd, b, p),
    )
