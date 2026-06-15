---
name: pr-review
description: "Manage GitHub PR review lifecycle: analyze unanswered threads, update review files, and post replies. Initial posting is handled by the review-post script."
source: otto-workbench/ai/claude/skills/pr-review/SKILL.md
invocation: "/pr-review <pr_number>"
---

# PR Review

Manages the thread lifecycle of GitHub PR reviews: analyze responses, update review files, and post replies.

**Initial posting** of review findings is handled by `review-post` (called via `claude-review post <N>` or `cmd_post()`). This skill is for thread management after a review has been posted.

Run with `/pr-review <pr_number>`.

---

## Steps

### 1. Check for existing review threads

```bash
repo=$(basename $(git rev-parse --show-toplevel))

# Check for an existing PENDING review by the current user
gh api repos/{owner}/{repo}/pulls/<pr_number>/reviews \
  --jq '[.[] | select(.user.login == "<current_user>" and .state == "PENDING")] | first'
```

Also check for submitted reviews with unanswered comment threads:

```bash
# Get all review comments and their reply threads
gh api repos/{owner}/{repo}/pulls/<pr_number>/comments \
  --jq '[.[] | select(.in_reply_to_id == null)] | map({id, path, body, user: .user.login, updated_at, replies: []})'
```

**If an existing review has unanswered responses:**
- Show the user a summary: how many threads, which files, who responded
- Ask: "There are N unanswered threads on this PR. Would you like to analyze them and update the review file?"
- If yes, proceed to Step 2
- If no, stop

### 2. Analyze unanswered threads

For each unanswered thread:
1. Read the original comment and all replies
2. Determine if the response resolves the finding, requests clarification, or disagrees
3. Update the review file in `~/.config/workbench/reviews/<repo>-<pr_number>.md`:
   - Mark resolved findings with `~~strikethrough~~` and note the resolution
   - Add new findings surfaced by the discussion
   - Append an `## Open Threads` section with threads needing a response
4. Present the updates to the user for confirmation before writing

### 3. Post replies

For threads that need a response, post replies. Never use `-f body="..."` — use `-F body=@-` with a quoted heredoc to avoid shell escaping failures:

```bash
gh api repos/{owner}/{repo}/pulls/<pr_number>/comments/<comment_id>/replies \
  --method POST -F body=@- <<'REPLY_BODY'
<reply text — backticks, quotes, markdown all safe here>
REPLY_BODY
```

### 4. Report

Print:
- Number of threads analyzed
- Number of findings resolved / still open
- Number of replies posted
- Link to the PR

---

## Constraints

- NEVER submit the review (`APPROVE`, `REQUEST_CHANGES`, `COMMENT`) — only create as `PENDING`
- NEVER modify the review file without user confirmation
- If the PR has been updated since the review file was written, warn and ask before proceeding
