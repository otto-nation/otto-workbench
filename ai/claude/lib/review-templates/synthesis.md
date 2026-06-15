You are completing the final review of PR #${pr_number} in ${repo}.

${pr_header}

## Holistic assessment (from Phase 1)
${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups)
${merged_content}

## Reference data
${preflight_data}
${delta_section}
${reviews_section}

## Task
1. Write the review header: # Review: ${repo}#${pr_number} — ${pr_title}
   Include <!-- date: ${today} -->, <!-- head_sha: ${pr_head_sha} -->, and <!-- generator: ${generator_version} --> comments
2. Include the File Triage section from the merged findings above
3. Write ## Summary — what the change does, overall quality, incorporating the holistic assessment
4. Include all Must fix / Should fix / Nit / Idioms findings from the merged content (use any IDs — they will be mechanically renumbered after you write the file). Use the format `- **[M1]** **\`<file>:<line>\`** — <finding>` — always wrap file paths in backticks inside bold markers. NEVER use ### headings for findings — downstream counters and posting tools parse the `- **[X1]**` list-item format only
5. Preserve evidence blocks from group findings when carrying forward Must-fix and Should-fix items. Do not strip or summarize them — they will be verified programmatically after you write the file.
6. Group agents already verified findings against source code. Do NOT re-read files to re-verify individual findings — a programmatic verification pass runs after you write. Instead, check for cross-group inconsistencies: does one group's finding contradict another group's analysis of the same code? Drop findings only when you can identify the contradiction from the merged content itself
7. Check for cross-file concerns — do findings in one group imply issues in files from another group? Deduplicate: if the same issue appears in multiple group reviews (same file, same concern), keep the most complete version and drop the rest
8. Add any cross-cutting findings
9. Write ## Verdict (Approve / Request changes / Needs discussion) — Idioms findings do not affect the verdict
10. You MUST use the Write tool to write the COMPLETE review file to: ${review_file}
    Do NOT use Bash (cat, heredoc, python) to write the file — use the Write tool.

## Turn budget
You have ${max_turns} turns total. Your FIRST action must be the Write tool to create the review file — all the content you need is already in this prompt. Do not read source files before writing. Use remaining turns only for cross-file consistency checks (e.g., confirming a finding about file A aligns with how file B uses it) and Edit updates.

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
