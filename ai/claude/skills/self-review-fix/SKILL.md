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

1. Resolve the branch name (validate it exists, fuzzy-match if needed)
2. Check if a self-review already exists for the current repo and branch in
   `~/.config/workbench/reviews/`
3. If no review, or the review is stale, run `claude-review --self --fix`
4. Report what was fixed and what was skipped — never ask, never fix manually

---

## Steps

### Step 1: Resolve branch name

Determine and validate the branch name. Run each lookup as a **separate**
Bash call — never chain variable assignments with `&&`.

1. Get the branch name — use the skill argument if provided, otherwise:
   ```bash
   git rev-parse --abbrev-ref HEAD
   ```

2. **If a skill argument was provided**, validate the branch exists:
   ```bash
   git branch -a --list "<branch>" "remotes/origin/<branch>"
   ```
   If no exact match, fuzzy-search:
   ```bash
   git branch -a | grep -i "<branch>"
   ```
   - **One match**: use it (strip `remotes/origin/` prefix if present, trim
     whitespace)
   - **Multiple matches**: list them and ask the user to pick
   - **No matches**: error and stop

### Step 2: Check for existing review

1. Get the repo name:
   ```bash
   gh repo view --json name -q .name
   ```

2. Sanitize the branch name and check for the review file:
   ```bash
   echo "<branch_name>" | tr '/' '-'
   ```
   ```bash
   ls ~/.config/workbench/reviews/<repo>-self-<sanitized>/review.md
   ```

If the review file exists, read it with the Read tool. Extract the
`<!-- head_sha: -->` value and compare against `git rev-parse HEAD`.

- **HEAD matches**: Count unchecked findings (`- [ ]`).
  If unchecked findings exist, go to Step 3.
  If all findings are checked, report "all findings already addressed" and stop.
- **HEAD doesn't match**: The review is stale. Go to Step 3.
- **No review file**: Go to Step 3.

### Step 3: Run claude-review

```bash
claude-review --self --fix [<branch_name>]
```

Pass the resolved branch name. `claude-review` handles bare repos, worktree
resolution, and fresh-vs-existing review detection internally.

### Step 4: Report results

Read the review file and present:
- Count of fixed findings (`- [x]`)
- Count of remaining unfixed findings (`- [ ]`)
- If Must-fix or Should-fix findings remain unfixed, list them prominently

**Do not** ask "how would you like to proceed" or offer choices.
**Do not** attempt to fix remaining findings manually via Edit tool —
all fixing is done by `claude-review --self --fix`. The fix agent determines
what is auto-fixable; trust its judgment.

---

## Safety

- **Non-destructive.** All fixes are applied via Edit tool — individual changes
  are reviewable in the git diff.
- **Idempotent.** Running twice on the same review skips already-fixed findings.
- **Review preserved.** The review file is kept in `~/.config/workbench/reviews/`
  for retro analysis — it is not deleted after fixing.
