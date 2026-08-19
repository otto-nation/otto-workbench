Review PR #${pr_number} in ${repo}.

${pr_header}
${state_context}
${preflight_data}
${delta_section}
${reviews_section}
${env_section}

## Output
${output_block}

Include this metadata comment after the head_sha line: <!-- generator: ${generator_version} -->
Format each finding as a list item: `- **[M1]** **\`<file>:<line>\`** — <finding>`. NEVER use ### headings for findings — downstream counters and posting tools parse the `- **[X1]**` list-item format only.
Must-fix and should-fix findings must include an evidence block — a blockquoted, fenced code snippet from the referenced file proving the claim.
A tradeoff the code marks with a `ceiling:` or `ceiling-permanent:` comment is a documented decision, not a defect — do not raise it. Raise it only when the marker's own upgrade trigger has already fired, and say which trigger and what fired it.
A finding already annotated `*(declined — reason)*` was adjudicated — carry it forward with the annotation intact rather than re-raising it as open.
Include a `## Verdict` section: ${verdict_options}. Disapprove means the overall approach is wrong and the PR should not land in any form — explain what should be done instead.

## Turn budget
You have ${max_turns} turns (each turn can include multiple parallel tool calls).${omitted_guidance} Write the review file FIRST based on the diff and file contents — do not investigate before writing. Use remaining turns to verify Must-fix and Should-fix claims against the source and update the file via Edit. Batch independent lookups (e.g. multiple grep/find/read calls) into a single turn.
${issue_section}
${prior_section}
${reply_threads}
