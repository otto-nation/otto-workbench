Fix PR review comment suggestions for branch ${branch_name} in ${repo}.

## Comment threads to address

${threads_content}

## Task

For each unchecked thread (`- [ ]`) above:

1. Read the referenced file at the specified line
2. Determine if the suggestion is auto-fixable:
   - **Fixable**: clear code change — rename, use existing helper, add guard, fix import, add nil check, remove dead code
   - **Not fixable**: requires design decision, architectural change, or user input
3. If fixable: apply the fix using the Edit tool on the source file
4. After fixing: update the thread checkbox from `- [ ]` to `- [x]` in the tracking file using Edit

## Rules

- For each fix, make the minimal correct change — do not refactor surrounding code
- If a suggestion references a function, type, or API — verify it exists in the codebase before using it
- If a suggestion is ambiguous or requires a design choice, skip it (leave unchecked)
- Do not add comments explaining the change — the reviewer already knows what they asked for

## Tracking file location
${tracking_file}

## Worktree
PR branch checked out at: ${wt_path}

All file reads and git commands MUST use this path directly (e.g. `git -C "${wt_path}" diff`).
Never use command substitution `$(...)` to discover the worktree path — it triggers permission prompts.

## Turn budget
You have ${max_turns} turns. Process threads systematically — batch independent file reads into single turns.
