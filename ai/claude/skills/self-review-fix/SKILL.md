---
name: self-review-fix
description: "Run self-review and auto-fix findings. Wraps claude-review --self --fix. Can also fix from an existing review without re-running."
source: otto-workbench/ai/claude/skills/self-review-fix/SKILL.md
invocation: "/self-review-fix"
---

# Self-Review Fix

Reviews your current branch and automatically applies fixes for the findings.

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

```bash
repo_name=$(basename "$(gh repo view --json name -q .name 2>/dev/null)" 2>/dev/null) || repo_name=""
branch_name=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || branch_name=""
branch_sanitized=$(echo "$branch_name" | tr '/' '-')
review_dir="$HOME/.config/workbench/reviews/${repo_name}-self-${branch_sanitized}"
review_file="${review_dir}/review.md"
```

Check if `$review_file` exists. If it does, read the `<!-- head_sha: -->` comment
and compare against `git rev-parse HEAD`.

- **HEAD matches**: Read the review and count unchecked findings (`- [ ]`).
  If there are unchecked findings, run the fix pass only (Step 3).
  If all findings are checked, report "all findings already addressed."
- **HEAD doesn't match**: The review is stale. Run full review + fix (Step 2).
- **No review file**: Run full review + fix (Step 2).

### Step 2: Run full self-review with fix

```bash
claude-review --self --fix
```

This runs the review pipeline and then applies fixes automatically.

### Step 3: Fix from existing review (fix pass only)

If there's an existing current review with unchecked findings, run just the fix
pass without re-running the review:

```bash
claude-review --self --fix
```

The orchestrator detects the existing review and runs only the fix pass.

### Step 4: Summary

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
