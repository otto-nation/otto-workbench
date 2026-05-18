You are completing the final review of PR #${pr_number} in ${repo}.

${pr_header}

## Holistic assessment (from Phase 1)
${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups)
${merged_content}
${preflight_data}
${reviews_section}

## Task
1. Write the review header: # Review: ${repo}#${pr_number} — ${pr_title}
   Include <!-- date: ${today} -->, <!-- head_sha: ${pr_head_sha} -->, and <!-- generator: ${generator_version} --> comments
2. Include the File Triage section from the merged findings above
3. Write ## Summary — what the change does, overall quality, incorporating the holistic assessment
4. Include all Must fix / Should fix / Nit / Idioms findings from the merged content (use any IDs — they will be mechanically renumbered after you write the file)
5. Check for cross-file concerns — do findings in one group imply issues in files from another group?
6. Add any cross-cutting findings
7. Write ## Verdict (Approve / Request changes / Needs discussion) — Idioms findings do not affect the verdict
8. Write the COMPLETE review file to: ${review_file}

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
