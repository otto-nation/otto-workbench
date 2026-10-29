Self-review of changes on branch ${branch_name} in ${repo}.

${pr_header}
${state_context}
${preflight_data}
${delta_section}
${env_section}

## Review format

Write the review as one JSON document, shaped as the Output section below shows. The harness renders it as an actionable checklist, so each finding is one thing to fix.
`summary` is one sentence: what the changes do and an overall quality assessment. On a re-review, do not count the prior findings there ("13 of 19 fixed") — `prior_findings` is the record of what became of them, and a tally written twice is a tally that can disagree with itself.

## Review angles

Apply these 7 lenses when scanning the diff:
1. **Line-by-line scan** — inverted conditions, off-by-one, null deref, missing await, falsy-zero, wrong-variable copy-paste, swallowed errors
2. **Removed behavior** — for each deleted/replaced line, check if the invariant it enforced is re-established elsewhere
3. **Cross-file tracer** — for each changed function, check callers and callees for broken contracts
4. **Reuse & convention fitness** — flag new code that reimplements an existing helper, class, or pattern. Also flag existing conventions that are stale, accidental, or scale-broken — recommend the better approach rather than enforcing conformity
5. **Simplification** — redundant state, copy-paste with slight variation, deep nesting, dead code
6. **Efficiency** — redundant computation, sequential independent ops, hot-path waste
7. **Altitude** — special cases layered on shared infrastructure instead of generalizing

Rules:
- Every finding names its file in `path` and its line in `line`; null only for a finding about the change as a whole
- IDs are M/S/N/I and a number, sequential per severity
- Must-fix and should-fix findings must carry `evidence` copied verbatim from the referenced file
- Leave `triage` as []
- Leave `verdict` as ""
- A tradeoff the code marks with a `ceiling:` or `ceiling-permanent:` comment is a documented decision, not a defect — do not raise it. Raise it only when the marker's own upgrade trigger has already fired, and say which trigger and what fired it
- On a re-review, a prior finding marked `*(declined — reason)*` was adjudicated — carry it forward with `sid` set to the value in its `<!-- sid: … -->` marker and `declined` set to that reason, rather than re-raising it as open
- On a re-review — a `## Prior review` section is present below — fill `prior_findings` as it asks

## Output
${output_block}

## Turn budget
You have ${max_turns} turns (each turn can include multiple parallel tool calls).${omitted_guidance} Write a complete review file FIRST based on the diff and file contents — do not investigate before that first write. Further complete rewrites are expected as findings accumulate; never leave the file as a non-document. Use remaining turns to verify Must-fix and Should-fix claims against the source and rewrite the file via Edit. Batch independent lookups (e.g. multiple grep/find/read calls) into a single turn.

A scratch file is not the review file. Writing a probe script, extracting a
dependency's source, or redirecting output somewhere to read it back leaves the
review file empty, and a run that ends there — out of turns, or because you
decided you were done — reports nothing at all. If a claim needs a shell to
settle and you have not written the file yet, write the file with the claim
marked unverified and settle it afterwards.

${execution_claim_guard}
${issue_section}
${focus_section}
${prior_section}
${reply_threads}
