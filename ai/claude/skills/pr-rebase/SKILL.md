---
name: pr-rebase
description: "AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead)."
source: otto-workbench/ai/claude/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [--fix]"
trigger: "Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase."
skip: "Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead)."
---

# PR Rebase

Rebases the current feature branch onto origin/main. The `pr rebase` script
handles everything: fetch, rebase, AI-assisted conflict resolution (via
`claude -p`), and force-push.

Run with `/pr-rebase` or `/pr-rebase --fix`.

---

## Arguments

- `--fix` (optional): Fully autonomous — resolve all conflicts with AI and
  force-push without user confirmation. Without this flag, conflicts are
  reported and the user decides whether to proceed.

---

## Steps

### 1. Run pr rebase

- **Default mode**:

```bash
pr rebase --repo-dir <worktree_root>
```

- **`--fix` mode**:

```bash
pr rebase --fix --repo-dir <worktree_root>
```

JSON output is on stdout; status messages are on stderr.

### 2. Handle the result

**Exit 0 — success.** Parse the JSON output. Default mode omits `force_pushed`:

```json
{
  "status": "clean",
  "commits_replayed": 22,
  "conflicts_resolved": 3,
  "files_resolved": ["orc-lending/go.mod", "orc-lending/go.sum"]
}
```

With `--fix`, includes `"force_pushed": true` (or `false` on push failure).

Report commits replayed and any conflicts resolved.

- **Default mode**: The script did not push. Ask the user to confirm, then:

```bash
pr rebase --push --repo-dir <worktree_root>
```

- **`--fix` mode**: The script already pushed (`force_pushed` in JSON). Done.

**Exit 3 — conflicts detected (default mode only).** Parse the JSON:

```json
{
  "status": "conflicts",
  "files": ["src/auth.py", "tests/test_auth.py"],
  "rebase_head": "abc1234",
  "rebase_head_subject": "fix: auth token refresh",
  "remaining_commits": 3
}
```

Report what was found. Ask the user if they want AI resolution. If yes:

```bash
pr rebase --fix --repo-dir <worktree_root>
```

This resumes the in-progress rebase with AI conflict resolution and force-pushes.

**Exit 1 — error.** Report the error from stderr.

---

## Constraints

- Always call `pr rebase` (the dispatcher, two words), never `pr-rebase`
  (the backing script) — the dispatcher handles context resolution and routing
- Never run raw `git push --force-with-lease` — always use `pr rebase --push`
