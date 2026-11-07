"""Tests for pr.stack — the repo's PR stack graph and `pr stack`."""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr.stack  # noqa: E402
from pr.stack import Health, OpenPr, ParentSource  # noqa: E402
from conftest import commit_all, git_in, init_repo  # noqa: E402


def _pr(n, branch, base="main"):
    return OpenPr(number=n, title=f"pr {n}", branch=branch, base_ref=base,
                  url=f"https://github.com/o/r/pull/{n}", is_draft=False)


def _graph(prs, native=None, topology=None, health=Health.UP_TO_DATE):
    return pr.stack.build_graph(
        prs, "main", native=native, native_stack=None,
        topology_parent=lambda b: (topology or {}).get(b, ""),
        health_of=lambda b, p: health)


def _node(graph, n):
    return next(x for x in graph.nodes if x.pr == n)


def test_a_pr_based_on_another_prs_branch_is_its_child():
    g = _graph([_pr(1, "a"), _pr(2, "b", base="a")])
    assert (_node(g, 2).parent, _node(g, 2).parent_source) == (1, ParentSource.BASE)
    assert _node(g, 1).children == [2]
    assert (_node(g, 1).parent, _node(g, 1).parent_source) == (None, ParentSource.TRUNK)


def test_native_stacks_win_over_the_base_branch():
    g = _graph([_pr(1, "a"), _pr(2, "b"), _pr(3, "c", base="a")], native={3: 2})
    assert (_node(g, 3).parent, _node(g, 3).parent_source) == (2, ParentSource.NATIVE)


def test_topology_names_a_parent_when_the_base_is_the_trunk():
    g = _graph([_pr(1, "a"), _pr(2, "b")], topology={"b": "a"})
    assert (_node(g, 2).parent, _node(g, 2).parent_source) == (1, ParentSource.TOPOLOGY)


def test_a_base_that_is_neither_trunk_nor_an_open_pr_is_base_closed():
    g = _graph([_pr(2, "b", base="gone")])
    assert _node(g, 2).health is Health.BASE_CLOSED
    assert _node(g, 2).parent is None


def test_a_cycle_is_broken_and_reported():
    g = _graph([_pr(1, "a", base="b"), _pr(2, "b", base="a")])
    assert sorted(g.cycles[0]) == [1, 2]
    assert [n.parent for n in g.nodes].count(None) >= 1


def test_ladder_is_bottom_first_and_contains_the_pr():
    g = _graph([_pr(1, "a"), _pr(2, "b", base="a"), _pr(3, "c", base="b"), _pr(4, "d")])
    assert [n.pr for n in pr.stack.ladder(g, 2)] == [1, 2, 3]
    assert [n.pr for n in pr.stack.ladder(g, 4)] == [4]


# ── health, against real refs ───────────────────────────────────────────────


def _repo(tmp_path):
    repo = init_repo(tmp_path / "r")
    (repo / "f").write_text("base\n")
    commit_all(repo, "base")
    git_in(repo, "branch", "parent")
    git_in(repo, "checkout", "-q", "-b", "child")
    (repo / "g").write_text("child\n")
    commit_all(repo, "child work")
    return repo


def _publish(repo, *branches):
    """Stand in for a fetch: point origin/<b> at <b>."""
    for b in branches:
        git_in(repo, "update-ref", f"refs/remotes/origin/{b}", b)


def test_a_child_containing_its_parent_is_up_to_date(tmp_path):
    repo = _repo(tmp_path)
    _publish(repo, "parent", "child")
    assert pr.stack.health(str(repo), "child", "parent") is Health.UP_TO_DATE


def test_a_parent_that_moved_cleanly_needs_a_rebase(tmp_path):
    repo = _repo(tmp_path)
    git_in(repo, "checkout", "-q", "parent")
    (repo / "h").write_text("parent moved\n")
    commit_all(repo, "parent work")
    _publish(repo, "parent", "child")
    assert pr.stack.health(str(repo), "child", "parent") is Health.NEEDS_REBASE


def test_a_parent_that_conflicts_has_conflicts(tmp_path):
    repo = _repo(tmp_path)
    git_in(repo, "checkout", "-q", "parent")
    (repo / "g").write_text("parent's own g\n")
    commit_all(repo, "conflicting work")
    _publish(repo, "parent", "child")
    assert pr.stack.health(str(repo), "child", "parent") is Health.HAS_CONFLICTS


def test_unreadable_refs_are_unknown(tmp_path):
    repo = _repo(tmp_path)
    assert pr.stack.health(str(repo), "child", "parent") is Health.UNKNOWN


# ── load_graph's GitHub reads ───────────────────────────────────────────────


def test_load_graph_reads_open_prs_and_tolerates_no_stacks_api(monkeypatch, tmp_path):
    pages = [[{"number": 1, "title": "t", "html_url": "u", "draft": False,
               "head": {"ref": "a"}, "base": {"ref": "main"}}]]

    def fake(endpoint, **kw):
        if endpoint.endswith("/stacks"):
            return None
        return pages

    monkeypatch.setattr(pr.stack.gh.client, "api_json", fake)
    monkeypatch.setattr(pr.stack, "health", lambda cwd, b, p: Health.UP_TO_DATE)
    monkeypatch.setattr(pr.stack.git.topology, "stack_parent", lambda **kw: "")
    monkeypatch.setattr(pr.stack.git.topology, "default_branch", lambda cwd=None: "main")
    g = pr.stack.load_graph("o/r", str(tmp_path))
    assert [(n.pr, n.branch) for n in g.nodes] == [(1, "a")]
