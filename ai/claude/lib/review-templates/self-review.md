Self-review of changes on branch ${branch_name} in ${repo}.

${pr_header}
${preflight_data}
${delta_section}
${env_section}

## Output format

Write the review as an actionable checklist. Use this exact structure:

```
# Self-Review: ${repo} — ${branch_name}
<!-- date: YYYY-MM-DD -->
<!-- head_sha: FULL_SHA -->
<!-- generator: ${generator_version} -->

## Summary
One sentence: what the changes do and overall quality assessment.

## Must fix
- [ ] **[M1]** `path/file.ext:LINE` — Description of the issue

## Should fix
- [ ] **[S1]** `path/file.ext:LINE` — Description of the issue

## Nit
- [ ] **[N1]** `path/file.ext:LINE` — Description of the issue

## Idioms
- [ ] **[I1]** `path/file.ext:LINE` — Description of the issue
```

## Review angles

Apply these 7 lenses when scanning the diff:
1. **Line-by-line scan** — inverted conditions, off-by-one, null deref, missing await, falsy-zero, wrong-variable copy-paste, swallowed errors
2. **Removed behavior** — for each deleted/replaced line, check if the invariant it enforced is re-established elsewhere
3. **Cross-file tracer** — for each changed function, check callers and callees for broken contracts
4. **Reuse** — flag new code that reimplements an existing helper in the codebase
5. **Simplification** — redundant state, copy-paste with slight variation, deep nesting, dead code
6. **Efficiency** — redundant computation, sequential independent ops, hot-path waste
7. **Altitude** — special cases layered on shared infrastructure instead of generalizing

Rules:
- Every finding MUST start with `- [ ]` (unchecked checkbox)
- Every finding MUST include a file:line reference
- Use M/S/N/I severity prefixes with sequential numbering per category
- Must-fix and should-fix findings must include an evidence block — a blockquoted, fenced code snippet from the referenced file
- Do NOT include a File Triage section
- Do NOT include a Verdict section
- Omit empty severity sections entirely (no "None" or "N/A")

You MUST use the Write tool to write the review to: ${review_file}
Do NOT print the review to stdout — it must be saved as a file using the Write tool.

## Turn budget
You have ${max_turns} turns (each turn can include multiple parallel tool calls).${omitted_guidance} Write the review file FIRST based on the diff and file contents — do not investigate before writing. Use remaining turns to verify Must-fix and Should-fix claims against the source and update the file via Edit. Batch independent lookups (e.g. multiple grep/find/read calls) into a single turn.
${issue_section}
${prior_section}
