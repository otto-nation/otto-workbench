Fix review findings for branch ${branch_name} in ${repo}.

## Your role

${role_block}

## Findings to fix

${tracking_content}

## Task

${answer_format}

## Check the finding before you fix it

A finding is a reviewer's claim about the code, not a fact about it. Some of
them are wrong: the concern is handled upstream, the pattern flagged is this
repo's convention, the code is on main rather than on this branch, the
referenced symbol does not exist. A pass that treats every finding as true
manufactures dead code to match the false ones — a guard against something that
cannot happen, and a test beside it that passes with or without the guard.

So for each finding, before editing anything:

1. **Read the code it points at**, and enough around it to see the path in
   full — the caller, the guard upstream, the defer that catches the error.
2. **Try to make the case that the finding is wrong.** Is the concern already
   handled somewhere the reviewer did not look? Does the input it worries about
   reach that line? Does the API it names exist?
3. **Fix it only if that case fails.** If it holds, tick `declined` and say what
   disproves the finding — name the file and line that settles it.

Default to fixing when you cannot tell. Declining is for a premise you
disproved, not one you doubt: say what you checked in the box either way.

## What earns each box

- **fixed** — a clear, unambiguous change: a wrong value, a missing guard, an
  off-by-one, a missing import, dead code, duplicate logic, incorrect prose,
  wrong field names in docs, a wording fix
- **declined** — the premise does not hold, the code already does what it asks,
  or the change would be wrong
- **needs a person** — a design decision, an architectural change, external
  verification, or a change to files outside this branch

## The test the `fixed` box asks for

A finding names a behaviour that is wrong. The change makes it right, and the
test is what says so tomorrow — write it as part of the fix, not after it, and
name it in the box.

A test that earns the box fails without your change. Run it against the code as
it was, or reason concretely about why it would have failed, before you claim
it: a case added beside the fix that passes either way records nothing.

Prefer a case added to the suite that already covers the file over a new file.
Where a finding's own severity makes a test disproportionate — a comment, a
doc wording fix, a rename with no behaviour behind it — write that in the box
in those words. "No test: prose change" is an answer. An empty `<why>` is not.

## Rules

- Work in severity order: Must fix first, then Should fix, then Nit, then Idioms
- For each fix, make the minimal correct change — do not refactor surrounding code
- Never add a guard, a branch, or a check for a condition you could not show
  reaches the code. That is the false finding surviving as dead code, and the
  test written beside it passes either way
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
