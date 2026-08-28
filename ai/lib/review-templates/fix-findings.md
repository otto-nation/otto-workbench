Fix review findings for branch ${branch_name} in ${repo}.

## Findings to fix

${tracking_content}

## Task

${answer_format}

## What earns each box

- **fixed** — a clear, unambiguous change: a wrong value, a missing guard, an
  off-by-one, a missing import, dead code, duplicate logic, incorrect prose,
  wrong field names in docs, a wording fix
- **declined** — the premise does not hold, the code already does what it asks,
  or the change would be wrong
- **needs a person** — a design decision, an architectural change, external
  verification, or a change to files outside this branch

## Rules

- Work in severity order: Must fix first, then Should fix, then Nit, then Idioms
- For each fix, make the minimal correct change — do not refactor surrounding code
- If a finding is ambiguous or requires a design choice, tick `needs a person` and say what the choice is — leaving it unticked reports it as unread
- If the code a finding points at carries a `// ceiling:` or `// ceiling-permanent:` comment naming that exact tradeoff, the tradeoff is a documented decision. Do not "fix" it — tick `declined` and say so

## Generated files

${generated_block}

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process findings systematically — batch independent file reads into single turns.
