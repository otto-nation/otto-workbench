Fix review findings for branch ${branch_name} in ${repo}.

## Findings to fix

${tracking_content}

## Task

Every finding above carries three boxes. Answer each finding by ticking exactly
one of them with the Edit tool, in the tracking file:

1. Read the referenced file at the specified line
2. Decide which answer the finding has earned:
   - `- [x] fixed` — you applied the change. A clear, unambiguous change belongs
     here: a wrong value, a missing guard, an off-by-one, a missing import, dead
     code, duplicate logic, incorrect prose, wrong field names in docs, a
     wording fix. Apply it with the Edit tool on the source file first
   - `- [x] declined — <why>` — you read the finding and it should not be acted
     on: the premise does not hold, the code already does what it asks, or the
     change would be wrong. Replace `<why>` with the reason, in one sentence
   - `- [x] needs a person — <why>` — the finding is real but the call is not
     yours: a design decision, an architectural change, external verification,
     or a change to files outside this branch. Replace `<why>` with what the
     decision turns on
3. Leave all three boxes unticked only for a finding you never got to. That
   reads as work still owed and the finding is handed to another pass, so use it
   for what you ran out of turns for — never as a way to pass over a finding you
   read and had an answer for

## Rules

- Work in severity order: Must fix first, then Should fix, then Nit, then Idioms
- For each fix, make the minimal correct change — do not refactor surrounding code
- If a finding is ambiguous or requires a design choice, tick `needs a person` and say what the choice is — leaving it unticked reports it as unread
- If the code a finding points at carries a `// ceiling:` or `// ceiling-permanent:` comment naming that exact tradeoff, the tradeoff is a documented decision. Do not "fix" it — tick `declined` and say so
- When a generated file needs fixing, fix the source template AND the generated output

## Tracking file location
${tracking_file}

## Worktree
${worktree_block}

## Turn budget
You have ${max_turns} turns. Process findings systematically — batch independent file reads into single turns.
