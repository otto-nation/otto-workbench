You are completing the final review of PR #${pr_number} in ${repo}.

${pr_header}
${state_context}

${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups, as group JSON)
${merged_content}

## Reference data
${preflight_data}
${delta_section}
${reviews_section}

## Task
1. Take `triage` from the merged findings above
2. Write `summary` — what the change does and its overall quality, incorporating the phase 1 context. On a re-review, do not count the prior findings here ("13 of 19 fixed") — `prior_findings` is the record of what became of them, and a tally written twice is a tally that can disagree with itself
3. Put every finding from the merged findings into `findings` (any IDs — they are renumbered mechanically after you write the file). Keep each finding's `sid`, and keep `declined` wherever it is set — it marks a finding already adjudicated
4. Keep each must-fix and should-fix finding's `evidence` exactly as the merged findings carry it. Do not strip or summarize it — it is verified programmatically after you write the file
5. Group agents already verified findings against source code. Do NOT re-read files to re-verify individual findings — a programmatic verification pass runs after you write. Instead, check for cross-group inconsistencies: does one group's finding contradict another group's analysis of the same code? Drop findings only when you can identify the contradiction from the merged content itself
6. Check for cross-file concerns — do findings in one group imply issues in files from another group? Deduplicate: if the same issue appears in multiple group reviews (same file, same concern), keep the most complete version and drop the rest
7. If the merged findings carry `prior_findings`, copy every entry into your output unchanged — they record which prior findings the group agents found fixed, and they are stripped before the review is published. Add entries only where a section below asks you to
8. Add any cross-cutting findings
9. Set `verdict` to one of ${verdict_options}, then why — idiom findings do not affect it. Disapprove means the overall approach is wrong and the PR should not land in any form — explain what should be done instead
10. Save the COMPLETE review file as described under ## Output below

## Output
${output_block}

## Turn budget
You have ${max_turns} turns total. Your FIRST action must be writing the review file — all the content you need is already in this prompt. Do not read source files before writing. Use remaining turns only for cross-file consistency checks (e.g., confirming a finding about file A aligns with how file B uses it) and Edit updates.

${execution_claim_guard}

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
${reply_threads}
