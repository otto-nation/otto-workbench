---
name: pr-comments
description: "Address incoming PR review comments: fetch, verify, fix, and reply. Works with human and bot reviewers."
source: otto-workbench/ai/claude/skills/pr-comments/SKILL.md
---

# PR Comments

Responds to review comments on a PR: fetches all threads, verifies each suggestion against the actual codebase, applies valid fixes, and replies inline.

Run with `/pr-comments` or `/pr-comments <pr_number>`.

---

## Steps

### 1. Identify the PR and fetch comments

```bash
# Get PR number from argument or current branch
PR_NUMBER=$(gh pr view --json number -q '.number')
```

If no PR exists for the current branch, ask the user which PR to address.

Determine the current GitHub user:

```bash
MY_LOGIN=$(gh api user --jq '.login')
```

Fetch all comment types — use `--paginate` to get every page:

```bash
# Inline review comments (code-level)
gh api repos/{owner}/{repo}/pulls/{number}/comments --paginate \
  --jq '.[] | "ID: \(.id)\nUser: \(.user.login)\nFile: \(.path)\nLine: \(.line // .original_line)\nReplyTo: \(.in_reply_to_id)\nBody: \(.body)\n---"'

# Top-level review bodies
gh api repos/{owner}/{repo}/pulls/{number}/reviews --paginate \
  --jq '.[] | select(.body != "") | "ReviewID: \(.id)\nUser: \(.user.login)\nState: \(.state)\nBody: \(.body)\n---"'

# Issue-level comments (general discussion)
gh api repos/{owner}/{repo}/issues/{number}/comments --paginate \
  --jq '.[] | "CommentID: \(.id)\nUser: \(.user.login)\nBody: \(.body)\n---"'
```

Group comments by thread using `in_reply_to_id`. Filter out:
- Comments from `$MY_LOGIN` (your own replies)
- Threads that already have a reply from `$MY_LOGIN` (already addressed)

Print a summary: N unaddressed threads, N comments, which reviewers.

If a specific reviewer was mentioned (e.g., "address Gemini's comments"), filter to that reviewer.

### 2. Classify each comment

For each unaddressed comment or thread, classify as:

| Classification | Description | Action |
|---|---|---|
| **Actionable suggestion** | Specific code change request | Verify and apply |
| **Question** | Asks for clarification or explanation | Answer |
| **Approval/acknowledgment** | No action needed | Skip |
| **Conflicting** | Contradicts another reviewer's suggestion | Flag |

Present the classification to the user for confirmation before proceeding. Example:

```
## Comment Classification

| # | Reviewer | File | Classification | Summary |
|---|----------|------|----------------|---------|
| 1 | @alice | handler.go:42 | Suggestion | Use RunTx instead of manual tx |
| 2 | @gemini | service.go:18 | Question | Why not use the shared helper? |
| 3 | @bob | handler.go:42 | Conflicting | Contradicts #1 — suggests BeginTx |

Proceed with this classification? (y/n)
```

### 3. Verify actionable suggestions

For each suggestion classified as actionable:

1. Read the file and line referenced in the comment
2. Determine if the suggestion is valid against the current code state
3. If the suggestion references a function, utility, or API — search the codebase to confirm it exists and has the signature the reviewer claims

For 3 or more suggestions, launch sub-agents in parallel for verification. Each agent should:

> A PR reviewer suggested changing `X` to `Y` in `path/to/file` at line N.
> Verify: does `Y` actually exist in the codebase? Check the relevant packages.
> Report: is the suggestion correct? What is the actual name/signature? Under 100 words.

Mark each suggestion as:
- **valid** — suggestion is correct, apply it
- **invalid** — suggestion is incorrect (with specific reason: "utility does not exist", "wrong signature", "would break callers at X:N")
- **needs-discussion** — ambiguous or requires design input

### 4. Apply valid fixes

For each `valid` suggestion:
1. Edit the file to address the comment
2. After all edits are applied, stage and commit:

```bash
git add <changed-files>
git commit -m "fix: address review feedback on PR #<number>"
git push
```

Group all fixes into a single commit. Do not create separate commits per comment.

### 5. Reply to each comment

Reply inline to every addressed comment. Commit and push before replying so replies reference the fix.

**Accepted:**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -f body="Fixed."
```

**Rejected (invalid suggestion):**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -f body="Not applying — <specific reason>. <what was checked>"
```

**Question answered:**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -f body="<answer>"
```

**Conflicting:**
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies \
  --method POST -f body="This conflicts with @<other-reviewer>'s suggestion. Leaving for discussion."
```

### 6. Emit feedback signals

After processing all comments, print a structured summary:

```markdown
## Review Feedback Summary

| Finding | Reviewer | Outcome | Reason |
|---------|----------|---------|--------|
| [M1] | @alice | accepted | Fixed in abc123 |
| [S1] | @gemini | rejected | Utility does not exist |
| — | @bob | needs-discussion | Conflicts with @alice |
```

This summary appears in the session transcript and is picked up by `/dream` for review quality tracking.

### 7. Report

Print:
- Number of fixes applied
- Number of replies posted
- Number of comments skipped or needing discussion
- Link to the PR

---

## Constraints

- NEVER apply fixes without user confirmation of the classification (Step 2)
- NEVER reply to comments without user confirmation
- Handle bot reviewers (Gemini, CodeRabbit, etc.) the same as humans — verify all suggestions against source code
- If conflicting suggestions exist, flag both and apply neither until resolved
- If the PR has been updated since comments were posted, warn the user before applying fixes
