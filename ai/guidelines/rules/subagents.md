# Delegating to Subagents

How to brief a subagent so it finishes, and how to read what comes back. Routing
between models is the harness's job; this is about the prompt and the review.

## Implementation

- Keep one task to three to five concrete edits, each naming its file and the change. A prompt listing nine items sat silent past the stall timeout and was killed; the same work split in three finished every time
- Say what to change, not what to read. Telling a subagent to study a large reference file (a `.d.ts`, a generated schema) spends its budget before the first edit
- End the prompt with the exact verification command and ask for its output. A subagent given the check reports its own result, which makes review faster
- Retry a stalled or aborted call on the same model before changing models — the stall is usually the call, not the model
- A subagent drifts in small ways: an import moved, a colour or flag added, a branch skipped because it was "different". Read the whole diff. A passing check proves the code loads, not that it looks or behaves the same

## Audits

- Define the finding precisely ("an identical chain of 3+ builder calls", not "duplication"); a loose definition over-reports
- Require file:line evidence for every finding and cap the length — a finding without a line number is a guess
- Run the audit a second time after fixing what the first found. The repeat pass catches what the first pass's fixes left behind or exposed

## Reading the result

- The controller keeps the gates: validators, the selected tests, a revert check. A subagent's "passed" is a claim about its own run, in its own cwd (see `bash-tool.md` § Subagents Reset the Working Directory)
- Look at the artifact — the diff, the screenshot, the output file — not the summary of it
