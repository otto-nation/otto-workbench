### Task 1: Add RebaseSummary to pr_state

**Files:**
- Modify: `ai/claude/lib/pr_state.py`
- Modify: `tests/pr_state_test.py`

**Interfaces:**
- Consumes: existing `PRState`, `state_to_dict`, `state_from_dict`, `save_state`, `load_state` patterns
- Produces: `RebaseSummary` dataclass, `update_rebase(state, summary)` function, serialization via `_rebase_to_dict` / `_rebase_from_dict`, `RebaseSummary` field on `PRState`

- [ ] **Step 1: Write failing tests for RebaseSummary**

Add tests to `tests/pr_state_test.py`:

```python
def test_rebase_summary_defaults():
    ci = RebaseSummary()
    assert ci.target_base == ""
    assert ci.commits_replayed == 0
    assert ci.conflicts_resolved == 0
    assert ci.files_resolved == []
    assert ci.force_pushed is False
    assert ci.updated_at == ""


def test_pr_state_has_rebase_field():
    ident = PRIdentity(
        repo="r", branch="b", pr_number=None,
        head_sha="", worktree_root="",
    )
    state = PRState(identity=ident)
    assert state.rebase.target_base == ""
    assert state.rebase.force_pushed is False


def test_update_rebase_replaces():
    state = new_state("repo", "branch", pr_number=None, head_sha="", worktree_root="/wt")
    update_rebase(state, RebaseSummary(
        target_base="origin/main", commits_replayed=3,
        conflicts_resolved=2, files_resolved=["a.py", "b.py"],
        force_pushed=True, updated_at="t1",
    ))
    assert state.rebase.target_base == "origin/main"
    assert state.rebase.commits_replayed == 3
    assert state.rebase.conflicts_resolved == 2
    assert state.rebase.files_resolved == ["a.py", "b.py"]
    assert state.rebase.force_pushed is True


def test_state_roundtrip_with_rebase_data():
    state = new_state("owner/repo", "feat", pr_number=42, head_sha="def", worktree_root="/wt")
    update_rebase(state, RebaseSummary(
        target_base="origin/main", commits_replayed=5,
        conflicts_resolved=2, files_resolved=["x.py"],
        force_pushed=True, updated_at="2026-06-20T00:00:00+00:00",
    ))
    d = state_to_dict(state)
    restored = state_from_dict(d)
    assert restored.rebase.target_base == "origin/main"
    assert restored.rebase.commits_replayed == 5
    assert restored.rebase.conflicts_resolved == 2
    assert restored.rebase.files_resolved == ["x.py"]
    assert restored.rebase.force_pushed is True


def test_save_preserves_rebase_data():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = new_state("owner/repo", "feat", pr_number=5, head_sha="abc", worktree_root=tmp)
        update_rebase(state, RebaseSummary(
            target_base="origin/main", commits_replayed=3,
            conflicts_resolved=1, files_resolved=["f.py"],
            force_pushed=False, updated_at="2026-06-20T00:00:00+00:00",
        ))
        save_state(root, state)
        loaded = load_state(root)
        assert loaded is not None
        assert loaded.rebase.target_base == "origin/main"
        assert loaded.rebase.commits_replayed == 3
        assert loaded.rebase.files_resolved == ["f.py"]
```

Update the import line at the top of the test file to include `RebaseSummary` and `update_rebase`:

```python
from pr_state import (
    PRIdentity, CISummary, ReviewSummary, CommentsSummary, TriageSummary,
    RebaseSummary,
    PRState, load_state, save_state, new_state, update_identity, update_ci,
    update_review, update_comments, update_triage, update_rebase,
    state_to_dict, state_from_dict,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/pr_state_test.py -v`
Expected: FAIL — `ImportError: cannot import name 'RebaseSummary'`

- [ ] **Step 3: Implement RebaseSummary in pr_state.py**

Add the dataclass after `TriageSummary`:

```python
@dataclass
class RebaseSummary:
    """Snapshot written by ``pr rebase``."""
    target_base: str = ""
    commits_replayed: int = 0
    conflicts_resolved: int = 0
    files_resolved: list[str] = field(default_factory=list)
    force_pushed: bool = False
    updated_at: str = ""
```

Add `rebase` field to `PRState`:

```python
@dataclass
class PRState:
    """Unified PR state — envelope over domain summaries."""
    identity: PRIdentity
    ci: CISummary = field(default_factory=CISummary)
    review: ReviewSummary = field(default_factory=ReviewSummary)
    comments: CommentsSummary = field(default_factory=CommentsSummary)
    triage: TriageSummary = field(default_factory=TriageSummary)
    rebase: RebaseSummary = field(default_factory=RebaseSummary)
    created_at: str = ""
    updated_at: str = ""
```

Add serialization helpers after the triage helpers:

```python
def _rebase_to_dict(r: RebaseSummary) -> dict:
    return {
        "target_base": r.target_base,
        "commits_replayed": r.commits_replayed,
        "conflicts_resolved": r.conflicts_resolved,
        "files_resolved": r.files_resolved,
        "force_pushed": r.force_pushed,
        "updated_at": r.updated_at,
    }


def _rebase_from_dict(d: dict) -> RebaseSummary:
    return RebaseSummary(
        target_base=d.get("target_base", ""),
        commits_replayed=d.get("commits_replayed", 0),
        conflicts_resolved=d.get("conflicts_resolved", 0),
        files_resolved=d.get("files_resolved", []),
        force_pushed=d.get("force_pushed", False),
        updated_at=d.get("updated_at", ""),
    )
```

Add `rebase` to `state_to_dict`:

```python
def state_to_dict(state: PRState) -> dict:
    return {
        "identity": _identity_to_dict(state.identity),
        "ci": _ci_to_dict(state.ci),
        "review": _review_to_dict(state.review),
        "comments": _comments_to_dict(state.comments),
        "triage": _triage_to_dict(state.triage),
        "rebase": _rebase_to_dict(state.rebase),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }
```

Add `rebase` to `state_from_dict`:

```python
def state_from_dict(d: dict) -> PRState:
    return PRState(
        identity=_identity_from_dict(d["identity"]),
        ci=_ci_from_dict(d.get("ci", {})),
        review=_review_from_dict(d.get("review", {})),
        comments=_comments_from_dict(d.get("comments", {})),
        triage=_triage_from_dict(d.get("triage", {})),
        rebase=_rebase_from_dict(d.get("rebase", {})),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
    )
```

Add the updater after `update_triage`:

```python
def update_rebase(state: PRState, summary: RebaseSummary) -> None:
    """Replace rebase summary."""
    state.rebase = summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pr_state_test.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ai/claude/lib/pr_state.py tests/pr_state_test.py
git commit -m "feat(pr): add RebaseSummary to pr_state framework"
```

---

