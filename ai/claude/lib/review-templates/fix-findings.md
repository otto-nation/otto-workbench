Fix review findings for branch ${branch_name} in ${repo}.

## Review findings

${review_content}

## Task

For each unchecked finding (`- [ ]`) in the review above:

1. Read the referenced file at the specified line
2. Determine if the finding is auto-fixable:
   - **Fixable**: clear, unambiguous change — wrong value, missing guard, off-by-one, missing import, dead code, duplicate logic, incorrect prose, wrong field names in docs, missing comments, wording fixes
   - **Not fixable**: requires design decision, architectural change, user input, external verification, or changes to files outside this branch
3. If fixable: apply the fix using the Edit tool, then check the box: `- [x]`
4. If not fixable: leave unchecked and annotate with skip reason inline:
   `*(skipped — one-line reason)*`
   Example: `- [ ] **[S1]** ... *(skipped — requires product team sign-off on SLA figure)* — ...`

## Rules

- Fix findings in severity order: Must fix first, then Should fix, then Nit, then Idioms
- For each fix, make the minimal correct change — do not refactor surrounding code
- If a finding is ambiguous or requires a design choice, skip it with a reason
- When a generated file needs fixing, fix the source template AND the generated output

## Review file location
${review_file}

## Worktree
PR branch checked out at: ${wt_path}

All file reads and git commands MUST use this path directly (e.g. `git -C "${wt_path}" diff`).
Never use command substitution `$(...)` to discover the worktree path — it triggers permission prompts.

## Turn budget
You have ${max_turns} turns. Process findings systematically — batch independent file reads into single turns.
