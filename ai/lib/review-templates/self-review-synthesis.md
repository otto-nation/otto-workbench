You are completing the final self-review of changes on branch ${branch_name} in ${repo}.

${pr_header}
${state_context}

${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups, as group JSON)
${merged_content}

## Reference data
${preflight_data}
${delta_section}
${issue_section}
${focus_section}

## Task
1. Write `summary` — one sentence on what the changes do and their overall quality. On a re-review, do not count the prior findings here ("13 of 19 fixed") — `prior_findings` is the record of what became of them, and a tally written twice is a tally that can disagree with itself
2. Put every finding from the merged findings into `findings`. Keep each finding's `sid`, and keep `declined` wherever it is set — it marks a finding already adjudicated, and dropping it puts the finding back into the next fix pass
3. Use any finding IDs — they will be mechanically renumbered after you write the file
4. Keep each must-fix and should-fix finding's `evidence` exactly as the merged findings carry it
5. Before including a Must-fix or Should-fix finding, verify its factual claims if they depend on config scope (check exclude rules, path filters), API contracts (trace the consumer chain to confirm required fields), or diff attribution (check `git log origin/main` to distinguish branch deletions from stale-base gaps). Drop findings that are factually incorrect — false positives erode trust more than missed findings
6. Check for cross-file concerns — do findings in one group imply issues in files from another group?
7. Leave `triage` as [] and `verdict` as ""
8. If the merged findings carry `prior_findings`, copy every entry into your output unchanged — they record which prior findings the group agents found fixed, and they are stripped before the review is published. Add entries only where a section below asks you to
9. Add any cross-cutting findings
10. Fill `focus` with one entry per area in the Focus section: what you checked and what you found, citing finding IDs. If the Focus section asks, add 1–3 areas of your own.
11. Save the COMPLETE review file as described under ## Output below

## Output
${output_block}

## Turn budget
You have ${max_turns} turns total. Your FIRST action must be writing the review file — all the content you need is already in this prompt. Do not read any source files before writing. Use remaining turns to verify Must-fix and Should-fix claims and update the file via Edit.

${execution_claim_guard}

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
${reply_threads}
