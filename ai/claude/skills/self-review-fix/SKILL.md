---
name: self-review-fix
description: "Run self-review and auto-fix findings. Wraps claude-review --self --fix. Can also fix from an existing review without re-running."
source: otto-workbench/ai/claude/skills/self-review-fix/SKILL.md
invocation: "/self-review-fix [branch_name]"
---

# Self-Review Fix

Reviews a branch and automatically applies fixes for the findings.

---

## Arguments

- `branch_name` (optional): Branch to review. Defaults to the current branch.
  Required when the CWD is a bare repo (no HEAD to detect).

---

## How It Works

1. Check if a self-review already exists for the current repo and branch in
   `~/.config/workbench/reviews/`
2. If a review exists and is current (HEAD matches `<!-- head_sha: -->`),
   offer to fix from the existing review or re-run
3. If no review exists or it's stale, run `claude-review --self --fix`
4. Present a summary of what was fixed and what was skipped

---

## Steps

### Step 1: Check for existing review

Determine the review file path. Run each lookup as a **separate** Bash call —
never chain variable assignments with `&&`.

1. Get the repo name:
   ```bash
   gh repo view --json name -q .name
   ```

2. Determine the branch name — use the skill argument if provided, otherwise:
   ```bash
   git rev-parse --abbrev-ref HEAD
   ```

3. Sanitize the branch name and check for the review file:
   ```bash
   echo "<branch_name>" | tr '/' '-'
   ```
   ```bash
   ls ~/.config/workbench/reviews/<repo>-self-<sanitized>/review.md
   ```

If the review file exists, read it with the Read tool. Extract the
`<!-- head_sha: -->` value and compare against `git rev-parse HEAD`.

- **HEAD matches**: Count unchecked findings (`- [ ]`).
  If unchecked findings exist, go to Step 2.
  If all findings are checked, report "all findings already addressed" and stop.
- **HEAD doesn't match**: The review is stale. Go to Step 2.
- **No review file**: Go to Step 2.

### Step 2: Run claude-review

```bash
claude-review --self --fix [<branch_name>]
```

Pass the branch name argument if one was provided. `claude-review` handles
bare repos, worktree resolution, and fresh-vs-existing review detection
internally.

### Step 3: Summary

Read the review file and present:
- Count of fixed findings (`- [x]`)
- Count of remaining unfixed findings (`- [ ]`)
- List any unfixed Must-fix or Should-fix findings that need manual attention

---

## Safety

- **Non-destructive.** All fixes are applied via Edit tool — individual changes
  are reviewable in the git diff.
- **Idempotent.** Running twice on the same review skips already-fixed findings.
- **Review preserved.** The review file is kept in `~/.config/workbench/reviews/`
  for retro analysis — it is not deleted after fixing.
