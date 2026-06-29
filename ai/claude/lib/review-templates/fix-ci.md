Fix CI failures for branch ${branch_name} in ${repo}.

## Failures to fix

${failures_content}

## Task

For each unchecked failure (`- [ ]`) above:

1. Read the referenced file at the specified line
2. Determine if the failure is auto-fixable:
   - **Fixable**: lint error with clear fix, test assertion with obvious code bug, build config issue, missing import
   - **Not fixable**: flaky test, infrastructure failure, design decision, architectural change
3. If fixable: apply the fix using the Edit tool on the source file
4. After fixing: update the checkbox from `- [ ]` to `- [x]` in the tracking file using Edit

## Rules

- Make the minimal correct change — do not refactor surrounding code
- For lint errors, fix the specific issue flagged — do not "improve" surrounding code
- For test failures, determine whether the test or the code is wrong before fixing
- If a failure is ambiguous or requires a design choice, skip it (leave unchecked)
- Do not add comments explaining the change

## Tracking file location
${tracking_file}

## Worktree
Branch checked out at: ${wt_path}

All file reads and git commands MUST use this path directly (e.g. `git -C "${wt_path}" diff`).
Never use command substitution `$(...)` to discover the worktree path — it triggers permission prompts.

## Turn budget
You have ${max_turns} turns. Process failures systematically — batch independent file reads into single turns.
