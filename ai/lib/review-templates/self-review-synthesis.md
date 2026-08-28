You are completing the final self-review of changes on branch ${branch_name} in ${repo}.

${pr_header}
${state_context}

## Holistic assessment (from Phase 1)
${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups)
${merged_content}

## Reference data
${preflight_data}
${delta_section}

## Task
1. Write the header: # Self-Review: ${repo} — ${branch_name}
   Include <!-- date: ${today} -->, <!-- head_sha: ${pr_head_sha} -->, and <!-- generator: ${generator_version} --> comments
2. Write ## Summary — one sentence on what the changes do and overall quality
3. Include all Must fix / Should fix / Nit / Idioms findings from the merged content
4. Convert each finding to checklist format: `- [ ] **[M1]** \`path:line\` — description` (use I prefix for Idioms). Keep any `*(declined — reason)*` annotation on the line — it marks a finding already adjudicated, and dropping it puts the finding back into the next fix pass. NEVER use ### headings for findings — downstream counters and fix tools parse the `- [ ] **[X1]**` list-item format only
5. Use any finding IDs — they will be mechanically renumbered after you write the file
6. Preserve evidence blocks from group findings when carrying forward Must-fix and Should-fix items.
7. Before including a Must-fix or Should-fix finding, verify its factual claims if they depend on config scope (check exclude rules, path filters), API contracts (trace the consumer chain to confirm required fields), or diff attribution (check `git log origin/main` to distinguish branch deletions from stale-base gaps). Drop findings that are factually incorrect — false positives erode trust more than missed findings
8. Check for cross-file concerns — do findings in one group imply issues in files from another group?
9. Add any cross-cutting findings
10. Omit empty severity sections entirely
11. Do NOT include a File Triage or Verdict section
12. If the merged findings contain a ## Prior findings section, copy every line of it into your output unchanged — it records which prior findings the group agents found fixed, and it is stripped before the review is published. Add lines to it only where a section below asks you to
13. Save the COMPLETE review file as described under ## Output below

## Output
${output_block}

## Turn budget
You have ${max_turns} turns total. Your FIRST action must be writing the review file — all the content you need is already in this prompt. Do not read any source files before writing. Use remaining turns to verify Must-fix and Should-fix claims and update the file via Edit.

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
${reply_threads}
