---
name: pr-rebase
description: "AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead)."
source: otto-workbench/ai/claude/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [branch] [--no-fix]"
trigger: "Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase."
skip: "Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead)."
---

# PR Rebase

Rebases a feature branch onto origin/main. The `pr rebase` script handles
everything: fetch, rebase, AI-assisted conflict resolution (via `claude -p`),
and force-push.

Run with `/pr-rebase` or `/pr-rebase <branch>`.

---

## Arguments

- `branch` (optional): Target branch to rebase. Passed as `--branch` to
  `pr rebase`, which resolves the worktree automatically. When omitted, the
  current branch is used.
- `--no-fix` (optional): Report conflicts without resolving them. By default,
  conflicts are resolved with AI and force-pushed automatically.

---

## Steps

### 1. Run pr rebase

- **Default mode** (auto-fix):

```bash
pr rebase --fix --branch <branch>
```

- **`--no-fix` mode** (report only):

```bash
pr rebase --branch <branch>
```

When no branch argument is provided, omit `--branch` (uses CWD's branch).

JSON output is on stdout; status messages are on stderr.

### 2. Handle the result

**Exit 0 — success.** Parse the JSON output:

```json
{
  "status": "clean",
  "commits_replayed": 22,
  "conflicts_resolved": 3,
  "files_resolved": ["orc-lending/go.mod", "orc-lending/go.sum"],
  "force_pushed": true
}
```

Report commits replayed and any conflicts resolved. Done.

**Exit 3 — conflicts detected (`--no-fix` mode only).** Parse the JSON:

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
pr rebase --fix --branch <branch>
```

This resumes the in-progress rebase with AI conflict resolution and force-pushes.

**Exit 1 — error.** Report the error from stderr.

---

## Constraints

- Always call `pr rebase` (the dispatcher, two words), never `pr-rebase`
  (the backing script) — the dispatcher handles context resolution and routing
- Never run raw `git push --force-with-lease` — always use `pr rebase --push`
