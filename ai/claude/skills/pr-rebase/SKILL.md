---
name: pr-rebase
description: "AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead)."
source: otto-workbench/ai/claude/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [--fix]"
trigger: "Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase."
skip: "Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead)."
---

# PR Rebase

Rebases the current feature branch onto origin/main with AI-assisted conflict
resolution. Resolves merge conflicts by reading both sides and producing a
merged resolution, then force-pushes the result.

Run with `/pr-rebase` or `/pr-rebase --fix`.

---

## Arguments

- `--fix` (optional): Fully autonomous mode — resolve all conflicts and
  force-push without waiting for confirmation. Without this flag, the skill
  prints a summary and waits for confirmation before force-pushing.

---

## Steps

### 1. Invoke pr rebase

Run the rebase script:

```bash
pr rebase [--fix] --repo-dir <worktree_root> 2>&1
```

Capture both stderr (human-readable status) and stdout (JSON report). Check
the exit code.

### 2. Handle exit code 0 — clean rebase

The rebase completed with no conflicts.

- **`--fix` mode**: Script already force-pushed. Report success and stop.
- **Default mode**: Parse the JSON from stdout. Report success and the number
  of commits replayed. Ask the user to confirm force-push, then run:

```bash
pr rebase --push --repo-dir <worktree_root>
```

Done.

### 3. Handle exit code 3 — conflicts

Parse the JSON from stdout. It contains:

```json
{
  "status": "conflicts",
  "files": ["src/auth.py", "tests/test_auth.py"],
  "rebase_head": "abc1234",
  "rebase_head_subject": "fix: auth token refresh",
  "remaining_commits": 3
}
```

Or `"status": "conflicts_resuming"` if resuming an interrupted rebase.

Report which commit is being applied and how many files have conflicts.
Proceed to step 4.

### 4. Resolve each conflicted file

For each file in the `files` array:

1. Read the file — it contains conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. Understand both sides:
   - In rebase context: the markers labeled `HEAD` are the rebased base
     (origin/main side), and the markers labeled with the commit SHA are the
     branch's changes
3. Resolve by merging the intent of both sides — not just picking one
4. Write the resolved file using Bash (not Edit or Write tools — active
   rebase makes the git index sensitive to tool-level file operations):

```bash
cat > path/to/file << 'RESOLVED_EOF'
<resolved file contents — no conflict markers>
RESOLVED_EOF
```

5. Stage the resolved file:

```bash
git add path/to/file
```

**Special cases:**
- **Binary files**: Do not attempt resolution. Report the file name to the user
  and skip it. The user must resolve binary conflicts manually.
- **Delete/modify conflicts** (file deleted on one side, modified on the other):
  Report both sides to the user. Ask which to keep — `git add <file>` to keep
  the modified version, or `git rm <file>` to accept the deletion.

### 5. Continue the rebase

After resolving all conflicted files for the current commit:

```bash
git -c core.editor=true rebase --continue
```

The `core.editor=true` accepts the existing commit message without opening an
editor.

**Check the result:**
- If exit 0 and no more rebase in progress: proceed to step 6
- If more conflicts (check `git diff --name-only --diff-filter=U`): loop back
  to step 4 with the new set of conflicted files
- If the continue fails for another reason: run `pr rebase --abort` and report
  the error

### 6. Print summary

Track the cumulative resolution work across all commits and print:

```
## Rebase Summary — N commits replayed

| Commit | Conflicts | Files Resolved |
|--------|-----------|----------------|
| abc123 fix: auth | 2 | src/auth.py, tests/test_auth.py |
| def456 feat: api | 1 | src/api.py |
| ghi789 chore: deps | 0 | (clean) |

Resolved X files across Y commits.
```

### 7. Force-push

- **Default mode**: Show the summary table. Ask the user to confirm. On
  confirmation, run:

```bash
pr rebase --push --repo-dir <worktree_root>
```

- **`--fix` mode**: Show the summary table. Force-push immediately:

```bash
pr rebase --push --repo-dir <worktree_root>
```

Report the result.

---

## Constraints

- Never run raw `git push --force-with-lease` — always use `pr rebase --push`
- During rebase conflict resolution, use only Bash for all file writes and git
  staging commands (`git add`, `git rebase --continue`)
- Do not use the Edit or Write tools during active rebase — they can corrupt
  git's index state
- If the rebase becomes unrecoverable (repeated failures on --continue), abort
  with `pr rebase --abort` and report what went wrong
