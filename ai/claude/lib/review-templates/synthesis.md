You are completing the final review of PR #${pr_number} in ${repo}.

${pr_header}

## Holistic assessment (from Phase 1)
${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups)
${merged_content}

## Reference data
${preflight_data}
${reviews_section}

## Task
1. Write the review header: # Review: ${repo}#${pr_number} — ${pr_title}
   Include <!-- date: ${today} -->, <!-- head_sha: ${pr_head_sha} -->, and <!-- generator: ${generator_version} --> comments
2. Include the File Triage section from the merged findings above
3. Write ## Summary — what the change does, overall quality, incorporating the holistic assessment
4. Include all Must fix / Should fix / Nit / Idioms findings from the merged content (use any IDs — they will be mechanically renumbered after you write the file). Use the format `- **[M1]** **\`<file>:<line>\`** — <finding>` — always wrap file paths in backticks inside bold markers
5. Preserve evidence blocks from group findings when carrying forward Must-fix and Should-fix items. Do not strip or summarize them — they will be verified programmatically after you write the file.
6. Before including a Must-fix or Should-fix finding, verify its factual claims if they depend on config scope (check exclude rules, path filters), API contracts (trace the consumer chain to confirm required fields), or diff attribution (check `git log origin/main` to distinguish branch deletions from stale-base gaps). Drop findings that are factually incorrect — false positives erode trust more than missed findings
7. Check for cross-file concerns — do findings in one group imply issues in files from another group? Deduplicate: if the same issue appears in multiple group reviews (same file, same concern), keep the most complete version and drop the rest
8. Add any cross-cutting findings
9. Write ## Verdict (Approve / Request changes / Needs discussion) — Idioms findings do not affect the verdict
10. Write the COMPLETE review file to: ${review_file}

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
