Self-review of changes on branch ${branch_name} in ${repo}.

${pr_header}
${preflight_data}
${env_section}

## Output format

Write the review as an actionable checklist. Use this exact structure:

```
# Self-Review: ${repo} — ${branch_name}
<!-- date: YYYY-MM-DD -->
<!-- head_sha: FULL_SHA -->

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

Rules:
- Every finding MUST start with `- [ ]` (unchecked checkbox)
- Every finding MUST include a file:line reference
- Use M/S/N/I severity prefixes with sequential numbering per category
- Do NOT include a File Triage section
- Do NOT include a Verdict section
- Omit empty severity sections entirely (no "None" or "N/A")

You MUST use the Write tool to write the review to: ${review_file}
Do NOT print the review to stdout — it must be saved as a file using the Write tool.
${issue_section}
${prior_section}
