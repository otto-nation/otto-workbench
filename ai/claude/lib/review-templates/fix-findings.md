Fix review findings for branch ${branch_name} in ${repo}.

## Review findings

${review_content}

## Task

For each unchecked finding (`- [ ]`) in the review above:

1. Read the referenced file at the specified line
2. Determine if the finding is auto-fixable:
   - **Fixable**: clear code change — wrong condition, missing guard, off-by-one, missing import, dead code, duplicate logic
   - **Not fixable**: requires design decision, architectural change, or user input
3. If fixable: apply the fix using the Edit tool on the source file
4. After fixing: update the finding checkbox from `- [ ]` to `- [x]` in the review file using Edit

## Rules

- Fix findings in severity order: Must fix first, then Should fix, then Nit, then Idioms
- For each fix, make the minimal correct change — do not refactor surrounding code
- If a finding is ambiguous or requires a design choice, skip it (leave unchecked)
- After all fixes, write a summary comment at the bottom of the review file:
  ```
  <!-- fix-pass: N fixed, M skipped -->
  ```

## Review file location
${review_file}

## Worktree
PR branch checked out at: ${wt_path}

## Turn budget
You have ${max_turns} turns. Process findings systematically — batch independent file reads into single turns.
