Review PR #${pr_number} in ${repo}.

${pr_header}
${preflight_data}
${reviews_section}
${env_section}

## Output
You MUST use the Write tool to write the review to: ${review_file}
Do NOT print the review to stdout — it must be saved as a file using the Write tool.
Include this metadata comment after the head_sha line: <!-- generator: ${generator_version} -->
Must-fix and should-fix findings must include an evidence block — a blockquoted, fenced code snippet from the referenced file proving the claim.

## Turn budget
You have a limited number of tool calls. Write the review file FIRST based on the pre-collected diff and file contents — do not investigate before writing. Use remaining turns to verify Must-fix and Should-fix claims against the source and update the file via Edit. Batch independent lookups (e.g. multiple grep/find/read calls) into a single turn.
${issue_section}
${prior_section}
