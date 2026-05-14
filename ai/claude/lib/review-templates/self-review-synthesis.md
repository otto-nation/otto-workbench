You are completing the final self-review of changes on branch ${branch_name} in ${repo}.

${pr_header}

## Holistic assessment (from Phase 1)
${holistic_content}

## Detailed findings (from Phase 2, merged across ${group_count} file groups)
${merged_content}

## Task
1. Write the header: # Self-Review: ${repo} — ${branch_name}
   Include <!-- date: ${today} --> and <!-- head_sha: ${pr_head_sha} --> comments
2. Write ## Summary — one sentence on what the changes do and overall quality
3. Include all Must fix / Should fix / Nit findings from the merged content
4. Convert each finding to checklist format: `- [ ] **[M1]** \`path:line\` — description`
5. Renumber findings sequentially within each severity category
6. Check for cross-file concerns — do findings in one group imply issues in files from another group?
7. Add any cross-cutting findings with new IDs continuing the sequence
8. Omit empty severity sections entirely
9. Do NOT include a File Triage or Verdict section
10. Write the COMPLETE review file to: ${review_file}

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
${prior_section}
