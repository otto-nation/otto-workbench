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
${issue_section}
${prior_section}
